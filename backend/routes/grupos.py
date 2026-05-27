import logging

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.grupos")

GE_TABLE = "grupos_electrogenos"
GE_VIEW = "v_ge_completo"

INSERTABLE_FIELDS = {
    "id",
    "sede_id",
    "cod_margesi",
    "estado",
    "anio_fabricacion",
    "potencia_kw",
    "fase_electrica",
    "tipo_transferencia",
    "mecanismo_transferencia",
    "marca_ensamblador",
    "modelo_ensamblador",
    "serie_ensamblador",
    "marca_motor",
    "modelo_motor",
    "serie_motor",
    "marca_alternador",
    "modelo_alternador",
    "serie_alternador",
    "marca_modulocontrol",
    "modelo_modulocontrol",
    "serie_modulocontrol",
    "observaciones",
    "activo",
    "created_at",
    "updated_at",
}

UPDATABLE_FIELDS = INSERTABLE_FIELDS - {"id", "created_at", "updated_at"}


def _row_to_dict(row: aiosqlite.Row | None) -> dict | None:
    return dict(row) if row is not None else None


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


async def _get_from_view(db: aiosqlite.Connection, grupo_id: int) -> dict | None:
    async with db.execute(f"SELECT * FROM {GE_VIEW} WHERE id = ?", (grupo_id,)) as cur:
        return _row_to_dict(await cur.fetchone())


async def list_grupos(request: web.Request) -> web.Response:
    db = request.app["db"]
    async with db.execute(f"SELECT * FROM {GE_VIEW} ORDER BY id") as cur:
        rows = await cur.fetchall()
    return web.json_response({"status": "ok", "data": [dict(row) for row in rows]})


async def get_grupo(request: web.Request) -> web.Response:
    try:
        grupo_id = int(request.match_info["id"])
    except ValueError:
        return _error("id inválido", 400)

    grupo = await _get_from_view(request.app["db"], grupo_id)
    if grupo is None:
        return _error("grupo no encontrado", 404)
    return web.json_response({"status": "ok", "data": grupo})


async def create_grupo(request: web.Request) -> web.Response:
    db = request.app["db"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    unknown = set(payload) - INSERTABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if "id" not in payload or "sede_id" not in payload:
        return _error("campos requeridos: id, sede_id", 400)

    payload.setdefault("created_at", "datetime('now','localtime')")
    payload.setdefault("updated_at", "datetime('now','localtime')")

    fields = list(payload.keys())
    placeholders = []
    values = []
    for field in fields:
        if field in {"created_at", "updated_at"} and payload[field] == "datetime('now','localtime')":
            placeholders.append("datetime('now','localtime')")
        else:
            placeholders.append("?")
            values.append(payload[field])

    sql = f"INSERT INTO {GE_TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear grupo: {exc}", 400)

    grupo = await _get_from_view(db, int(payload["id"]))
    log.info("Grupo creado: %s", payload["id"])

    event_payload = dict(grupo)
    event_payload["_entity"] = "grupo_electrogeno"
    user = get_user(request)
    event = make_event("create", payload["id"], event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para create grupo %s: %s", payload["id"], exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": grupo}, status=201)


async def update_grupo(request: web.Request) -> web.Response:
    db = request.app["db"]
    try:
        grupo_id = int(request.match_info["id"])
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not payload:
        return _error("payload vacío", 400)
    unknown = set(payload) - UPDATABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)

    exists = await _get_from_view(db, grupo_id)
    if exists is None:
        return _error("grupo no encontrado", 404)

    assignments = ", ".join(f"{field} = ?" for field in payload)
    values = [*payload.values(), grupo_id]
    try:
        await db.execute(f"UPDATE {GE_TABLE} SET {assignments} WHERE id = ?", values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar grupo: {exc}", 400)

    grupo = await _get_from_view(db, grupo_id)
    log.info("Grupo actualizado: %s", grupo_id)

    event_payload = dict(payload)
    event_payload["_entity"] = "grupo_electrogeno"
    user = get_user(request)
    event = make_event("update", grupo_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para update grupo %s: %s", grupo_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": grupo})


async def delete_grupo(request: web.Request) -> web.Response:
    db = request.app["db"]
    try:
        grupo_id = int(request.match_info["id"])
    except ValueError:
        return _error("id inválido", 400)

    exists = await _get_from_view(db, grupo_id)
    if exists is None:
        return _error("grupo no encontrado", 404)

    await db.execute(f"UPDATE {GE_TABLE} SET activo = 0 WHERE id = ?", (grupo_id,))
    await db.commit()
    grupo = await _get_from_view(db, grupo_id)
    log.info("Grupo dado de baja: %s", grupo_id)

    event_payload = {"activo": 0, "_entity": "grupo_electrogeno"}
    user = get_user(request)
    event = make_event("delete", grupo_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para delete grupo %s: %s", grupo_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": grupo})
