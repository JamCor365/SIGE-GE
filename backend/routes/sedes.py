import logging

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event
from backend.routes.common import build_insert_sql, error_response, get_by_id, read_json

log = logging.getLogger("sige.sedes")

TABLE = "sedes"
VIEW = "v_sedes_completo"
INSERTABLE_FIELDS = {
    "id",
    "codigo",
    "nombre_agencia",
    "categoria",
    "direccion",
    "departamento",
    "provincia",
    "distrito",
    "macroregion_id",
    "observaciones",
    "activo",
    "created_at",
    "updated_at",
}
UPDATABLE_FIELDS = INSERTABLE_FIELDS - {"id", "created_at", "updated_at"}


async def list_sedes(request: web.Request) -> web.Response:
    db = request.app["db"]
    async with db.execute(f"SELECT * FROM {VIEW} ORDER BY id") as cur:
        rows = await cur.fetchall()
    return web.json_response({"status": "ok", "data": [dict(row) for row in rows]})


async def get_sede(request: web.Request) -> web.Response:
    try:
        item_id = int(request.match_info["id"])
    except ValueError:
        return error_response("id inválido", 400)

    item = await get_by_id(request.app["db"], VIEW, item_id)
    if item is None:
        return error_response("sede no encontrada", 404)
    return web.json_response({"status": "ok", "data": item})


async def create_sede(request: web.Request) -> web.Response:
    db = request.app["db"]
    try:
        payload = await read_json(request)
    except ValueError as exc:
        return error_response(str(exc), 400)

    unknown = set(payload) - INSERTABLE_FIELDS
    if unknown:
        return error_response(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    required = {"id", "codigo", "nombre_agencia", "macroregion_id"}
    missing = required - set(payload)
    if missing:
        return error_response(f"campos requeridos: {', '.join(sorted(missing))}", 400)

    sql, values = build_insert_sql(TABLE, payload)
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return error_response(f"no se pudo crear sede: {exc}", 400)

    item = await get_by_id(db, VIEW, int(payload["id"]))
    log.info("Sede creada: %s", payload["id"])

    event_payload = dict(item)
    event_payload["_entity"] = "sede"
    user = get_user(request)
    event = make_event("create", payload["id"], event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para create sede %s: %s", payload["id"], exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": item}, status=201)


async def update_sede(request: web.Request) -> web.Response:
    db = request.app["db"]
    try:
        item_id = int(request.match_info["id"])
        payload = await read_json(request)
    except ValueError as exc:
        return error_response(str(exc), 400)

    if not payload:
        return error_response("payload vacío", 400)
    unknown = set(payload) - UPDATABLE_FIELDS
    if unknown:
        return error_response(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if await get_by_id(db, VIEW, item_id) is None:
        return error_response("sede no encontrada", 404)

    assignments = ", ".join(f"{field} = ?" for field in payload)
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*payload.values(), item_id])
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return error_response(f"no se pudo actualizar sede: {exc}", 400)

    item = await get_by_id(db, VIEW, item_id)
    log.info("Sede actualizada: %s", item_id)

    event_payload = dict(payload)
    event_payload["_entity"] = "sede"
    user = get_user(request)
    event = make_event("update", item_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para update sede %s: %s", item_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": item})


async def delete_sede(request: web.Request) -> web.Response:
    db = request.app["db"]
    try:
        item_id = int(request.match_info["id"])
    except ValueError:
        return error_response("id inválido", 400)

    if await get_by_id(db, VIEW, item_id) is None:
        return error_response("sede no encontrada", 404)
    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (item_id,))
    await db.commit()
    item = await get_by_id(db, VIEW, item_id)
    log.info("Sede dada de baja: %s", item_id)

    event_payload = {"activo": 0, "_entity": "sede"}
    user = get_user(request)
    event = make_event("delete", item_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para delete sede %s: %s", item_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": item})
