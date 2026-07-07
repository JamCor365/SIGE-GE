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
    "latitud",
    "longitud",
    "geo_fuente",
    "macroregion_id",
    "observaciones",
    "activo",
    "created_at",
    "updated_at",
}
UPDATABLE_FIELDS = INSERTABLE_FIELDS - {"id", "created_at", "updated_at"}

_GEO_FUENTES = {"distrito_centroide", "nominatim", "manual"}
# Caja envolvente de Perú (WGS84) con margen. Sirve de guard contra lat/long
# invertidas o basura: una coordenada fuera de esta caja no es una sede peruana.
_LAT_MIN, _LAT_MAX = -18.5, 0.5
_LON_MIN, _LON_MAX = -81.5, -68.5


def _validate_geo(payload: dict) -> str | None:
    """Valida los campos geográficos si están presentes. Devuelve un mensaje de
    error o None. Acepta None (para limpiar la coordenada)."""
    fuente = payload.get("geo_fuente")
    if fuente is not None and fuente not in _GEO_FUENTES:
        return f"geo_fuente inválida: {fuente} (use {', '.join(sorted(_GEO_FUENTES))})"

    for campo, lo, hi in (("latitud", _LAT_MIN, _LAT_MAX), ("longitud", _LON_MIN, _LON_MAX)):
        val = payload.get(campo)
        if val is None:
            continue
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            return f"{campo} debe ser numérico"
        if not (lo <= val <= hi):
            return f"{campo} fuera del rango de Perú [{lo}, {hi}]: {val}"

    # Coordenada a medias: si el payload trae ambas, no vale una nula y otra no.
    if "latitud" in payload and "longitud" in payload:
        if (payload["latitud"] is None) != (payload["longitud"] is None):
            return "latitud y longitud deben ir juntas"
    return None


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
    geo_error = _validate_geo(payload)
    if geo_error:
        return error_response(geo_error, 400)

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
    user = get_user(request.app["config"])
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
    geo_error = _validate_geo(payload)
    if geo_error:
        return error_response(geo_error, 400)
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
    user = get_user(request.app["config"])
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
    user = get_user(request.app["config"])
    event = make_event("delete", item_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para delete sede %s: %s", item_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": item})
