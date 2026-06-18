"""Prestaciones de un contrato: PRINCIPAL (única) + ACCESORIAS (0..N).

Identidad UUID surrogate (como contratos/proveedores): numero_prestacion es
invención nuestra y NO converge entre máquinas, así que no sirve de id
determinista ni de UNIQUE (descartaría filas distintas en silencio vía
INSERT OR IGNORE y dejaría FKs de servicios colgando). Es solo un ordinal.

`item_id` es una referencia BLANDA a items_contrato.id (patrón contrato_ge):
NULL = descomposición a nivel contrato; seteado = a nivel ítem.
`tipos_objeto` se maneja igual que en contratos (string JSON canónico en columna
y evento, array en la respuesta API) para que el round-trip de sync sea idéntico.
"""
import json
import logging
import uuid

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event
from backend.routes.contratos import _TIPOS_OBJETO  # mismo vocabulario cerrado

log = logging.getLogger("sige.prestaciones")

TABLE = "prestaciones"

# contrato_id viene del path; id/created_at/updated_at los pone el backend.
INSERTABLE_FIELDS = {
    "item_id",
    "numero_prestacion",
    "clase",
    "tipos_objeto",
    "descripcion",
    "monto",
    "moneda",
    "plazo_dias",
    "observaciones",
    "activo",
}
# clase/numero/item editables; id y contrato_id no se actualizan vía API.
UPDATABLE_FIELDS = INSERTABLE_FIELDS

_CLASES = {"PRINCIPAL", "ACCESORIA"}


def _error(reason: str, status: int) -> web.Response:
    return web.json_response({"status": "error", "reason": reason}, status=status)


async def _read_json(request: web.Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("JSON inválido") from exc
    if not isinstance(payload, dict):
        raise ValueError("El payload debe ser un objeto JSON")
    return payload


def _normalize(payload: dict) -> None:
    """Valida y canonicaliza EN SITIO. tipos_objeto → STRING JSON canónico (igual
    que contratos) para que columna y evento sean idénticos en el sync."""
    if "tipos_objeto" in payload:
        value = payload["tipos_objeto"]
        if value is None or value == []:
            payload["tipos_objeto"] = None
        else:
            if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
                raise ValueError("tipos_objeto debe ser una lista de tokens")
            unknown = [t for t in value if t not in _TIPOS_OBJETO]
            if unknown:
                raise ValueError(f"tipos_objeto inválidos: {', '.join(unknown)}")
            payload["tipos_objeto"] = json.dumps(sorted(set(value)))

    for field in ("monto", "plazo_dias"):
        if field in payload and payload[field] is not None:
            v = payload[field]
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{field} debe ser un entero >= 0")


def _to_api(row: dict | None) -> dict | None:
    """tipos_objeto se devuelve como ARRAY (la columna guarda STRING). Solo para
    respuestas al cliente — el evento conserva el string canónico."""
    if row is None:
        return None
    out = dict(row)
    raw = out.get("tipos_objeto")
    out["tipos_objeto"] = json.loads(raw) if raw else None
    return out


async def _exists(db: aiosqlite.Connection, table: str, id_value) -> bool:
    async with db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_value,)) as cur:
        return await cur.fetchone() is not None


async def _get(db: aiosqlite.Connection, prestacion_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (prestacion_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _item_belongs(db: aiosqlite.Connection, item_id: str, contrato_id: str) -> bool | None:
    """True/False si el ítem pertenece al contrato; None si no existe."""
    async with db.execute("SELECT contrato_id FROM items_contrato WHERE id = ?", (item_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return row["contrato_id"] == contrato_id


async def _principal_warning(db, contrato_id, item_id, clase, exclude_id=None) -> str | None:
    """Regla BLANDA: avisa si ya hay una PRINCIPAL activa en el mismo ámbito
    (mismo contrato + mismo item_id). No bloquea."""
    if clase != "PRINCIPAL":
        return None
    scope = "item_id IS ?" if item_id is None else "item_id = ?"
    sql = f"SELECT id FROM {TABLE} WHERE contrato_id = ? AND {scope} AND clase = 'PRINCIPAL' AND activo = 1"
    params = [contrato_id, item_id]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    async with db.execute(sql, params) as cur:
        if await cur.fetchone():
            ambito = "el contrato" if item_id is None else f"el ítem {item_id}"
            return f"ya existe una prestación PRINCIPAL activa en {ambito} (se guardó igual)"
    return None


async def _emit(request: web.Request, action: str, prestacion_id: str, payload: dict) -> None:
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "prestacion"
    user = get_user(request.app["config"])
    event = make_event(action, prestacion_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s prestacion %s: %s", action, prestacion_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def list_prestaciones(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/prestaciones — prestaciones activas del contrato."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    async with db.execute(
        f"SELECT * FROM {TABLE} WHERE contrato_id = ? AND activo = 1 ORDER BY numero_prestacion, created_at",
        (contrato_id,),
    ) as cur:
        rows = [_to_api(dict(r)) for r in await cur.fetchall()]
    return web.json_response({"status": "ok", "data": rows})


async def create_prestacion(request: web.Request) -> web.Response:
    """POST /api/contratos/{id}/prestaciones  body: {clase, tipos_objeto?, monto?, …}."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    unknown = set(payload) - INSERTABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if payload.get("clase") not in _CLASES:
        return _error("campo requerido: clase (PRINCIPAL|ACCESORIA)", 400)
    try:
        _normalize(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)

    item_id = payload.get("item_id")
    if item_id is not None:
        belongs = await _item_belongs(db, item_id, contrato_id)
        if belongs is None:
            return _error("ítem no encontrado", 404)
        if not belongs:
            return _error("el ítem no pertenece a este contrato", 400)

    warnings = [w for w in (await _principal_warning(db, contrato_id, item_id, payload["clase"]),) if w]

    prestacion_id = uuid.uuid4().hex
    fields = ["id", "contrato_id", *payload.keys(), "created_at", "updated_at"]
    placeholders = (
        ["?"] * (2 + len(payload))
        + ["datetime('now','localtime')", "datetime('now','localtime')"]
    )
    values = [prestacion_id, contrato_id, *payload.values()]
    sql = f"INSERT INTO {TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear prestación: {exc}", 400)

    prestacion = await _get(db, prestacion_id)        # crudo (tipos_objeto string) → evento
    await _emit(request, "create", prestacion_id, prestacion)
    return web.json_response({"status": "ok", "data": _to_api(prestacion), "warnings": warnings}, status=201)


async def update_prestacion(request: web.Request) -> web.Response:
    """PUT /api/contratos/{id}/prestaciones/{prestacion_id}."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    prestacion_id = request.match_info["prestacion_id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not payload:
        return _error("payload vacío", 400)
    unknown = set(payload) - UPDATABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if "clase" in payload and payload["clase"] not in _CLASES:
        return _error("clase inválida (PRINCIPAL|ACCESORIA)", 400)
    try:
        _normalize(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    current = await _get(db, prestacion_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("prestación no encontrada", 404)

    item_id = payload.get("item_id")
    if "item_id" in payload and item_id is not None:
        belongs = await _item_belongs(db, item_id, contrato_id)
        if belongs is None:
            return _error("ítem no encontrado", 404)
        if not belongs:
            return _error("el ítem no pertenece a este contrato", 400)

    clase = payload.get("clase", current["clase"])
    scope_item = item_id if "item_id" in payload else current["item_id"]
    warnings = [w for w in (await _principal_warning(db, contrato_id, scope_item, clase, exclude_id=prestacion_id),) if w]

    assignments = ", ".join(f"{k} = ?" for k in payload)
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*payload.values(), prestacion_id])
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar prestación: {exc}", 400)

    await _emit(request, "update", prestacion_id, dict(payload))
    return web.json_response({"status": "ok", "data": _to_api(await _get(db, prestacion_id)), "warnings": warnings})


async def delete_prestacion(request: web.Request) -> web.Response:
    """DELETE /api/contratos/{id}/prestaciones/{prestacion_id} — baja lógica."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    prestacion_id = request.match_info["prestacion_id"]

    current = await _get(db, prestacion_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("prestación no encontrada", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (prestacion_id,))
    await db.commit()
    await _emit(request, "delete", prestacion_id, {"activo": 0})
    return web.json_response({"status": "ok", "data": _to_api(await _get(db, prestacion_id))})
