"""Registro reutilizable de proveedores (adjudicatarios: empresa o consorcio).

Espejo de routes/contratos.py: PK = UUID generado en el backend (nunca del
cliente), eventos de sync por create/update/delete. El RUC se valida de forma
SUAVE (avisa vía `warnings`, no bloquea) — se permite registrar incompleto,
igual que `numero` nullable en contratos.
"""
import logging
import uuid

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.proveedores")

TABLE = "proveedores"

# id/created_at/updated_at no se aceptan del cliente ni se actualizan vía API.
INSERTABLE_FIELDS = {
    "ruc",
    "razon_social",
    "tipo",
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


def _ruc_warning(ruc) -> str | None:
    """Validación SUAVE: avisa si el RUC no son 11 dígitos, pero NO impide guardar."""
    if ruc is None or ruc == "":
        return None
    s = str(ruc)
    if not (s.isdigit() and len(s) == 11):
        return f"RUC '{s}' no tiene 11 dígitos numéricos (se guardó igual)"
    return None


async def _get(db: aiosqlite.Connection, proveedor_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (proveedor_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _emit(request: web.Request, action: str, proveedor_id: str, payload: dict) -> None:
    """Sube el evento y lo registra, con el mismo manejo de fallo que contratos."""
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "proveedor"
    user = get_user(request.app["config"])
    event = make_event(action, proveedor_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s proveedor %s: %s", action, proveedor_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def list_proveedores(request: web.Request) -> web.Response:
    db = request.app["db"]
    async with db.execute(f"SELECT * FROM {TABLE} ORDER BY razon_social") as cur:
        rows = await cur.fetchall()
    return web.json_response({"status": "ok", "data": [dict(r) for r in rows]})


async def get_proveedor(request: web.Request) -> web.Response:
    proveedor = await _get(request.app["db"], request.match_info["id"])
    if proveedor is None:
        return _error("proveedor no encontrado", 404)
    return web.json_response({"status": "ok", "data": proveedor})


async def create_proveedor(request: web.Request) -> web.Response:
    db = request.app["db"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    unknown = set(payload) - INSERTABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if not payload.get("razon_social"):
        return _error("campo requerido: razon_social", 400)

    warnings = [w for w in (_ruc_warning(payload.get("ruc")),) if w]

    proveedor_id = uuid.uuid4().hex
    fields = ["id", *payload.keys(), "created_at", "updated_at"]
    placeholders = (
        ["?"] * (1 + len(payload))
        + ["datetime('now','localtime')", "datetime('now','localtime')"]
    )
    values = [proveedor_id, *payload.values()]
    sql = f"INSERT INTO {TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear proveedor: {exc}", 400)

    proveedor = await _get(db, proveedor_id)
    log.info("Proveedor creado: %s", proveedor_id)
    await _emit(request, "create", proveedor_id, proveedor)

    return web.json_response({"status": "ok", "data": proveedor, "warnings": warnings}, status=201)


async def update_proveedor(request: web.Request) -> web.Response:
    db = request.app["db"]
    proveedor_id = request.match_info["id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not payload:
        return _error("payload vacío", 400)
    unknown = set(payload) - UPDATABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if await _get(db, proveedor_id) is None:
        return _error("proveedor no encontrado", 404)

    warnings = [w for w in (_ruc_warning(payload.get("ruc")),) if w] if "ruc" in payload else []

    assignments = ", ".join(f"{field} = ?" for field in payload)
    values = [*payload.values(), proveedor_id]
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar proveedor: {exc}", 400)

    proveedor = await _get(db, proveedor_id)
    log.info("Proveedor actualizado: %s", proveedor_id)
    await _emit(request, "update", proveedor_id, dict(payload))

    return web.json_response({"status": "ok", "data": proveedor, "warnings": warnings})


async def delete_proveedor(request: web.Request) -> web.Response:
    db = request.app["db"]
    proveedor_id = request.match_info["id"]
    if await _get(db, proveedor_id) is None:
        return _error("proveedor no encontrado", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (proveedor_id,))
    await db.commit()
    proveedor = await _get(db, proveedor_id)
    log.info("Proveedor dado de baja: %s", proveedor_id)
    await _emit(request, "delete", proveedor_id, {"activo": 0})

    return web.json_response({"status": "ok", "data": proveedor})
