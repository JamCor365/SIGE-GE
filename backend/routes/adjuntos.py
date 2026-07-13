"""Adjuntos: metadata de archivos (el archivo vive en SharePoint, no en SQLite).

PK UUID surrogate. contrato_id es FK dura. ref_entidad/ref_id son un puntero
POLIMÓRFICO BLANDO al objeto documentado (adenda, servicio, penalidad…) — no es FK
(no se puede FK polimórficamente). tipo por CHECK en BD. `ruta` es el enlace en
SharePoint; `sha256` la integridad (no único).
"""
import logging
import uuid

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.adjuntos")

TABLE = "adjuntos"

# contrato_id viene del path; id/created_at/updated_at los pone el backend.
INSERTABLE_FIELDS = {
    "ref_entidad",
    "ref_id",
    "tipo",
    "nombre",
    "ruta",
    "sha256",
    "paginas",
    "fecha",
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


def _validate_paginas(payload: dict) -> None:
    if "paginas" in payload and payload["paginas"] is not None:
        v = payload["paginas"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError("paginas debe ser un entero >= 0")


async def _exists(db: aiosqlite.Connection, table: str, id_value) -> bool:
    async with db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_value,)) as cur:
        return await cur.fetchone() is not None


async def _get(db: aiosqlite.Connection, adjunto_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (adjunto_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _emit(request: web.Request, action: str, adjunto_id: str, payload: dict) -> None:
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "adjunto"
    user = get_user(request.app["config"])
    event = make_event(action, adjunto_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s adjunto %s: %s", action, adjunto_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def list_adjuntos(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/adjuntos — adjuntos activos del contrato.

    Filtro opcional por ?ref_entidad=&ref_id= para traer los de una sub-entidad.
    """
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)

    sql = f"SELECT * FROM {TABLE} WHERE contrato_id = ? AND activo = 1"
    params: list = [contrato_id]
    ref_entidad = request.query.get("ref_entidad")
    ref_id = request.query.get("ref_id")
    if ref_entidad is not None:
        sql += " AND ref_entidad = ?"
        params.append(ref_entidad)
    if ref_id is not None:
        sql += " AND ref_id = ?"
        params.append(ref_id)
    sql += " ORDER BY fecha, created_at"

    async with db.execute(sql, params) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return web.json_response({"status": "ok", "data": rows})


async def create_adjunto(request: web.Request) -> web.Response:
    """POST /api/contratos/{id}/adjuntos."""
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
        _validate_paginas(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)

    adjunto_id = uuid.uuid4().hex
    fields = ["id", "contrato_id", *payload.keys(), "created_at", "updated_at"]
    placeholders = (
        ["?"] * (2 + len(payload))
        + ["datetime('now','localtime')", "datetime('now','localtime')"]
    )
    values = [adjunto_id, contrato_id, *payload.values()]
    sql = f"INSERT INTO {TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear adjunto: {exc}", 400)

    adjunto = await _get(db, adjunto_id)
    await _emit(request, "create", adjunto_id, adjunto)
    return web.json_response({"status": "ok", "data": adjunto}, status=201)


async def update_adjunto(request: web.Request) -> web.Response:
    """PUT /api/contratos/{id}/adjuntos/{adjunto_id}."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    adjunto_id = request.match_info["adjunto_id"]
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
        _validate_paginas(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    current = await _get(db, adjunto_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("adjunto no encontrado", 404)

    assignments = ", ".join(f"{k} = ?" for k in payload)
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*payload.values(), adjunto_id])
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar adjunto: {exc}", 400)

    await _emit(request, "update", adjunto_id, dict(payload))
    return web.json_response({"status": "ok", "data": await _get(db, adjunto_id)})


async def delete_adjunto(request: web.Request) -> web.Response:
    """DELETE /api/contratos/{id}/adjuntos/{adjunto_id} — baja lógica."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    adjunto_id = request.match_info["adjunto_id"]

    current = await _get(db, adjunto_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("adjunto no encontrado", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (adjunto_id,))
    await db.commit()
    await _emit(request, "delete", adjunto_id, {"activo": 0})
    return web.json_response({"status": "ok", "data": await _get(db, adjunto_id)})
