"""Puente N:M contrato ↔ grupo electrógeno + alcance geográfico derivado.

Identidad de fila DETERMINISTA del par ("{contrato_id}_{ge_id}") para que el
motor de sync (que indexa por columna `id` escalar) funcione sin tocarse y para
que el mismo par converja entre máquinas. Ver db.py.

Vincular un par que ya existe pero está inactivo NO puede ser un create (el
motor aplica create con INSERT OR IGNORE → ignoraría la fila existente y no
reactivaría). Por eso la ruta decide: no existe → create; existe inactivo →
update {activo:1}. Desvincular → delete (activo=0).
"""
import logging

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.contrato_ge")

TABLE = "contrato_ge"


def _link_id(contrato_id: str, ge_id: int) -> str:
    return f"{contrato_id}_{ge_id}"


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


async def _exists(db: aiosqlite.Connection, table: str, id_value) -> bool:
    async with db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_value,)) as cur:
        return await cur.fetchone() is not None


async def _get_link(db: aiosqlite.Connection, link_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (link_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _emit(request: web.Request, action: str, link_id: str, payload: dict) -> None:
    """Sube el evento y lo registra, con el mismo manejo de fallo que contratos."""
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "contrato_ge"
    user = get_user(request.app["config"])
    event = make_event(action, link_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s contrato_ge %s: %s", action, link_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def link_ge(request: web.Request) -> web.Response:
    """POST /api/contratos/{id}/ge  body: {ge_id, item_id?}  — vincular (o reactivar)."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    ge_id = payload.get("ge_id")
    if not isinstance(ge_id, int) or isinstance(ge_id, bool):
        return _error("campo requerido: ge_id (entero)", 400)
    item_id = payload.get("item_id")  # por ahora siempre NULL (no existe items_contrato)

    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    if not await _exists(db, "grupos_electrogenos", ge_id):
        return _error("grupo electrógeno no encontrado", 404)

    link_id = _link_id(contrato_id, ge_id)
    existing = await _get_link(db, link_id)

    if existing is None:
        await db.execute(
            f"""INSERT INTO {TABLE} (id, contrato_id, ge_id, item_id, activo, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, datetime('now','localtime'), datetime('now','localtime'))""",
            (link_id, contrato_id, ge_id, item_id),
        )
        await db.commit()
        link = await _get_link(db, link_id)
        # Payload = fila cruda completa, como en contratos create.
        await _emit(request, "create", link_id, link)
        status = 201
    elif existing["activo"] == 0:
        await db.execute(
            f"UPDATE {TABLE} SET activo = 1, item_id = ? WHERE id = ?", (item_id, link_id)
        )
        await db.commit()
        await _emit(request, "update", link_id, {"activo": 1, "item_id": item_id})
        link = await _get_link(db, link_id)
        status = 200
    else:
        # Ya vinculado y activo → idempotente, sin evento.
        link = existing
        status = 200

    return web.json_response({"status": "ok", "data": link}, status=status)


async def unlink_ge(request: web.Request) -> web.Response:
    """DELETE /api/contratos/{id}/ge/{ge_id}  — desvincular (baja lógica)."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        ge_id = int(request.match_info["ge_id"])
    except ValueError:
        return _error("ge_id inválido", 400)

    link_id = _link_id(contrato_id, ge_id)
    existing = await _get_link(db, link_id)
    if existing is None:
        return _error("vínculo no encontrado", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (link_id,))
    await db.commit()
    await _emit(request, "delete", link_id, {"activo": 0})

    link = await _get_link(db, link_id)
    return web.json_response({"status": "ok", "data": link})


async def list_contrato_ge(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/ge — GE vinculados (activos) al contrato."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)

    async with db.execute(
        """
        SELECT cg.id AS link_id, cg.item_id,
               ge.id AS ge_id, ge.sede_id, ge.estado, ge.cod_margesi
        FROM contrato_ge cg
        JOIN grupos_electrogenos ge ON ge.id = cg.ge_id
        WHERE cg.contrato_id = ? AND cg.activo = 1
        ORDER BY ge.id
        """,
        (contrato_id,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return web.json_response({"status": "ok", "data": rows})


async def contrato_alcance(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/alcance — alcance geográfico DERIVADO de los GE
    vinculados (reemplaza el antiguo campo `ambito`): macrorregiones y agencias
    distintas, vía ge → sede → macrorregión."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)

    async with db.execute(
        """
        SELECT DISTINCT m.id AS macroregion_id, m.nombre AS macroregion
        FROM contrato_ge cg
        JOIN grupos_electrogenos ge ON ge.id = cg.ge_id
        JOIN sedes s             ON s.id = ge.sede_id
        JOIN macroregiones m     ON m.id = s.macroregion_id
        WHERE cg.contrato_id = ? AND cg.activo = 1
        ORDER BY m.nombre
        """,
        (contrato_id,),
    ) as cur:
        macroregiones = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        """
        SELECT DISTINCT s.id AS sede_id, s.codigo, s.nombre_agencia, s.macroregion_id
        FROM contrato_ge cg
        JOIN grupos_electrogenos ge ON ge.id = cg.ge_id
        JOIN sedes s ON s.id = ge.sede_id
        WHERE cg.contrato_id = ? AND cg.activo = 1
        ORDER BY s.nombre_agencia
        """,
        (contrato_id,),
    ) as cur:
        agencias = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        "SELECT COUNT(*) FROM contrato_ge WHERE contrato_id = ? AND activo = 1", (contrato_id,)
    ) as cur:
        total_ge = (await cur.fetchone())[0]

    return web.json_response({
        "status": "ok",
        "data": {
            "contrato_id": contrato_id,
            "total_ge": total_ge,
            "total_macroregiones": len(macroregiones),
            "total_agencias": len(agencias),
            "macroregiones": macroregiones,
            "agencias": agencias,
        },
    })
