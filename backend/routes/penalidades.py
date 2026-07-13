"""Penalidades del contrato: descuentos al contratista por incumplimiento.

Identidad UUID surrogate (como garantias): sin clave natural que converja (una
misma causa puede penalizarse varias veces). tipo MORA vs OTRAS (CHECK en BD).
`dias_mora` aplica solo a MORA. prestacion_id/item_id son refs BLANDAS validadas
en ruta (existen + pertenecen al contrato). monto en céntimos (>= 0).
"""
import logging
import uuid

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.penalidades")

TABLE = "penalidades"

# contrato_id viene del path; id/created_at/updated_at los pone el backend.
INSERTABLE_FIELDS = {
    "prestacion_id",
    "item_id",
    "tipo",
    "concepto",
    "monto",
    "moneda",
    "dias_mora",
    "base_legal",
    "fecha",
    "estado",
    "observaciones",
    "activo",
}
UPDATABLE_FIELDS = INSERTABLE_FIELDS


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


def _validate_montos(payload: dict) -> None:
    """monto (céntimos) y dias_mora deben ser enteros >= 0, no bool ni float."""
    for field in ("monto", "dias_mora"):
        if field in payload and payload[field] is not None:
            v = payload[field]
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{field} debe ser un entero >= 0")


async def _exists(db: aiosqlite.Connection, table: str, id_value) -> bool:
    async with db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_value,)) as cur:
        return await cur.fetchone() is not None


async def _belongs(db: aiosqlite.Connection, table: str, id_value: str, contrato_id: str) -> bool | None:
    """True/False si la fila pertenece al contrato; None si no existe."""
    async with db.execute(f"SELECT contrato_id FROM {table} WHERE id = ?", (id_value,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return row["contrato_id"] == contrato_id


async def _get(db: aiosqlite.Connection, penalidad_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (penalidad_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _check_refs(db, payload, contrato_id) -> web.Response | None:
    """Valida prestacion_id/item_id si vienen: existen y pertenecen al contrato."""
    for field, table in (("prestacion_id", "prestaciones"), ("item_id", "items_contrato")):
        value = payload.get(field)
        if field in payload and value is not None:
            belongs = await _belongs(db, table, value, contrato_id)
            if belongs is None:
                return _error(f"{field.replace('_id', '')} no encontrado", 404)
            if not belongs:
                return _error(f"el {field.replace('_id', '')} no pertenece a este contrato", 400)
    return None


async def _emit(request: web.Request, action: str, penalidad_id: str, payload: dict) -> None:
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "penalidad"
    user = get_user(request.app["config"])
    event = make_event(action, penalidad_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s penalidad %s: %s", action, penalidad_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def list_penalidades(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/penalidades — penalidades activas del contrato."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    async with db.execute(
        f"SELECT * FROM {TABLE} WHERE contrato_id = ? AND activo = 1 ORDER BY fecha, created_at",
        (contrato_id,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return web.json_response({"status": "ok", "data": rows})


async def create_penalidad(request: web.Request) -> web.Response:
    """POST /api/contratos/{id}/penalidades."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    unknown = set(payload) - INSERTABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    try:
        _validate_montos(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    ref_err = await _check_refs(db, payload, contrato_id)
    if ref_err is not None:
        return ref_err

    penalidad_id = uuid.uuid4().hex
    fields = ["id", "contrato_id", *payload.keys(), "created_at", "updated_at"]
    placeholders = (
        ["?"] * (2 + len(payload))
        + ["datetime('now','localtime')", "datetime('now','localtime')"]
    )
    values = [penalidad_id, contrato_id, *payload.values()]
    sql = f"INSERT INTO {TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear penalidad: {exc}", 400)

    penalidad = await _get(db, penalidad_id)
    await _emit(request, "create", penalidad_id, penalidad)
    return web.json_response({"status": "ok", "data": penalidad}, status=201)


async def update_penalidad(request: web.Request) -> web.Response:
    """PUT /api/contratos/{id}/penalidades/{penalidad_id}."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    penalidad_id = request.match_info["penalidad_id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not payload:
        return _error("payload vacío", 400)
    unknown = set(payload) - UPDATABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    try:
        _validate_montos(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    current = await _get(db, penalidad_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("penalidad no encontrada", 404)
    ref_err = await _check_refs(db, payload, contrato_id)
    if ref_err is not None:
        return ref_err

    assignments = ", ".join(f"{k} = ?" for k in payload)
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*payload.values(), penalidad_id])
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar penalidad: {exc}", 400)

    await _emit(request, "update", penalidad_id, dict(payload))
    return web.json_response({"status": "ok", "data": await _get(db, penalidad_id)})


async def delete_penalidad(request: web.Request) -> web.Response:
    """DELETE /api/contratos/{id}/penalidades/{penalidad_id} — baja lógica."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    penalidad_id = request.match_info["penalidad_id"]

    current = await _get(db, penalidad_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("penalidad no encontrada", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (penalidad_id,))
    await db.commit()
    await _emit(request, "delete", penalidad_id, {"activo": 0})
    return web.json_response({"status": "ok", "data": await _get(db, penalidad_id)})
