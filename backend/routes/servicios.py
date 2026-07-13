"""Servicios de mantenimiento: cronograma de ejecución de la prestación accesoria.

Uno por (GE × nro_servicio) — el mantenimiento es del equipo. PK UUID surrogate.
ge_id es obligatorio y debe existir (FK dura a grupos_electrogenos). prestacion_id
es ref BLANDA opcional (validada en ruta). fecha_programada (cronograma) y
fecha_ejecutada (ejecución real) van separadas; nunca se pisan. estado por CHECK.
El alcance geográfico se DERIVA del GE, no se almacena.
"""
import logging
import uuid

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.servicios")

TABLE = "servicios_mantenimiento"

# contrato_id viene del path; id/created_at/updated_at los pone el backend.
INSERTABLE_FIELDS = {
    "ge_id",
    "prestacion_id",
    "nro_servicio",
    "fecha_programada",
    "fecha_ejecutada",
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


def _validate_ints(payload: dict) -> None:
    """ge_id (entero) y nro_servicio (entero >= 1), no bool ni float."""
    if "ge_id" in payload and payload["ge_id"] is not None:
        v = payload["ge_id"]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("ge_id debe ser un entero")
    if "nro_servicio" in payload and payload["nro_servicio"] is not None:
        v = payload["nro_servicio"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ValueError("nro_servicio debe ser un entero >= 1")


async def _exists(db: aiosqlite.Connection, table: str, id_value) -> bool:
    async with db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_value,)) as cur:
        return await cur.fetchone() is not None


async def _belongs(db: aiosqlite.Connection, table: str, id_value: str, contrato_id: str) -> bool | None:
    async with db.execute(f"SELECT contrato_id FROM {table} WHERE id = ?", (id_value,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return row["contrato_id"] == contrato_id


async def _get(db: aiosqlite.Connection, servicio_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (servicio_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _check_refs(db, payload, contrato_id) -> web.Response | None:
    """ge_id (si viene) debe existir; prestacion_id (si viene) debe pertenecer al contrato."""
    if payload.get("ge_id") is not None:
        if not await _exists(db, "grupos_electrogenos", payload["ge_id"]):
            return _error("ge no encontrado", 404)
    value = payload.get("prestacion_id")
    if "prestacion_id" in payload and value is not None:
        belongs = await _belongs(db, "prestaciones", value, contrato_id)
        if belongs is None:
            return _error("prestacion no encontrada", 404)
        if not belongs:
            return _error("la prestacion no pertenece a este contrato", 400)
    return None


async def _emit(request: web.Request, action: str, servicio_id: str, payload: dict) -> None:
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "servicio"
    user = get_user(request.app["config"])
    event = make_event(action, servicio_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s servicio %s: %s", action, servicio_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def list_servicios(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/servicios — servicios activos del contrato."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    async with db.execute(
        f"SELECT * FROM {TABLE} WHERE contrato_id = ? AND activo = 1 ORDER BY ge_id, nro_servicio",
        (contrato_id,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return web.json_response({"status": "ok", "data": rows})


async def create_servicio(request: web.Request) -> web.Response:
    """POST /api/contratos/{id}/servicios."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    unknown = set(payload) - INSERTABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if payload.get("ge_id") is None:
        return _error("ge_id es obligatorio", 400)
    if payload.get("nro_servicio") is None:
        return _error("nro_servicio es obligatorio (1..N)", 400)
    try:
        _validate_ints(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    ref_err = await _check_refs(db, payload, contrato_id)
    if ref_err is not None:
        return ref_err

    servicio_id = uuid.uuid4().hex
    fields = ["id", "contrato_id", *payload.keys(), "created_at", "updated_at"]
    placeholders = (
        ["?"] * (2 + len(payload))
        + ["datetime('now','localtime')", "datetime('now','localtime')"]
    )
    values = [servicio_id, contrato_id, *payload.values()]
    sql = f"INSERT INTO {TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear servicio: {exc}", 400)

    servicio = await _get(db, servicio_id)
    await _emit(request, "create", servicio_id, servicio)
    return web.json_response({"status": "ok", "data": servicio}, status=201)


async def update_servicio(request: web.Request) -> web.Response:
    """PUT /api/contratos/{id}/servicios/{servicio_id}."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    servicio_id = request.match_info["servicio_id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not payload:
        return _error("payload vacío", 400)
    unknown = set(payload) - UPDATABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if "ge_id" in payload and payload["ge_id"] is None:
        return _error("ge_id no puede ser null", 400)
    if "nro_servicio" in payload and payload["nro_servicio"] is None:
        return _error("nro_servicio no puede ser null", 400)
    try:
        _validate_ints(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    current = await _get(db, servicio_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("servicio no encontrado", 404)
    ref_err = await _check_refs(db, payload, contrato_id)
    if ref_err is not None:
        return ref_err

    assignments = ", ".join(f"{k} = ?" for k in payload)
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*payload.values(), servicio_id])
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar servicio: {exc}", 400)

    await _emit(request, "update", servicio_id, dict(payload))
    return web.json_response({"status": "ok", "data": await _get(db, servicio_id)})


async def delete_servicio(request: web.Request) -> web.Response:
    """DELETE /api/contratos/{id}/servicios/{servicio_id} — baja lógica."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    servicio_id = request.match_info["servicio_id"]

    current = await _get(db, servicio_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("servicio no encontrado", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (servicio_id,))
    await db.commit()
    await _emit(request, "delete", servicio_id, {"activo": 0})
    return web.json_response({"status": "ok", "data": await _get(db, servicio_id)})
