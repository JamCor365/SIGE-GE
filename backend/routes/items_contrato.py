"""Ítems de un contrato (adjudicados a proveedores distintos).

Identidad DETERMINISTA del par ("{contrato_id}_{numero_item}"), como contrato_ge:
los ítems son target de FK (contrato_ge.item_id, futuros servicios/penalidades),
así que un id determinista evita el descarte silencioso + FKs colgando del UUID.
numero_item es inmutable (define el id); corregirlo = delete + recreate.

Recrear un ítem dado de baja (activo=0) no puede ser un create (el motor aplica
create con INSERT OR IGNORE → no reactivaría). La ruta decide: no existe → create;
existe inactivo → update {activo:1, …}. Desvincular/eliminar → delete (activo=0).
"""
import logging

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.items_contrato")

TABLE = "items_contrato"

# Campos opcionales aceptados al crear (además de numero_item, que define el id).
INSERTABLE_OPTIONAL = {"proveedor_id", "descripcion", "monto", "moneda", "estado", "observaciones"}
# numero_item NO es actualizable (es inmutable: define el id). contrato_id tampoco.
UPDATABLE_FIELDS = {"proveedor_id", "descripcion", "monto", "moneda", "estado", "observaciones", "activo"}


def _item_id(contrato_id: str, numero_item: int) -> str:
    return f"{contrato_id}_{numero_item}"


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


def _validate_monto(payload: dict) -> None:
    if "monto" in payload and payload["monto"] is not None:
        v = payload["monto"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError("monto debe ser un entero de céntimos >= 0")


async def _exists(db: aiosqlite.Connection, table: str, id_value) -> bool:
    async with db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_value,)) as cur:
        return await cur.fetchone() is not None


async def _get(db: aiosqlite.Connection, item_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (item_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _emit(request: web.Request, action: str, item_id: str, payload: dict) -> None:
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "item_contrato"
    user = get_user(request.app["config"])
    event = make_event(action, item_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s item_contrato %s: %s", action, item_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def list_items(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/items — ítems activos del contrato."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    async with db.execute(
        f"SELECT * FROM {TABLE} WHERE contrato_id = ? AND activo = 1 ORDER BY numero_item",
        (contrato_id,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return web.json_response({"status": "ok", "data": rows})


async def create_item(request: web.Request) -> web.Response:
    """POST /api/contratos/{id}/items  body: {numero_item, proveedor_id?, …} — crear (o reactivar)."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    unknown = set(payload) - ({"numero_item"} | INSERTABLE_OPTIONAL)
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)

    numero_item = payload.get("numero_item")
    if not isinstance(numero_item, int) or isinstance(numero_item, bool):
        return _error("campo requerido: numero_item (entero)", 400)
    try:
        _validate_monto(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    proveedor_id = payload.get("proveedor_id")
    if proveedor_id is not None and not await _exists(db, "proveedores", proveedor_id):
        return _error("proveedor no encontrado", 404)

    item_id = _item_id(contrato_id, numero_item)
    existing = await _get(db, item_id)

    optional = {k: payload[k] for k in INSERTABLE_OPTIONAL if k in payload}

    if existing is None:
        row = {"id": item_id, "contrato_id": contrato_id, "numero_item": numero_item, **optional}
        cols = [*row.keys(), "created_at", "updated_at"]
        placeholders = ["?"] * len(row) + ["datetime('now','localtime')", "datetime('now','localtime')"]
        try:
            await db.execute(
                f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})",
                list(row.values()),
            )
            await db.commit()
        except aiosqlite.IntegrityError as exc:
            return _error(f"no se pudo crear ítem: {exc}", 400)
        item = await _get(db, item_id)
        await _emit(request, "create", item_id, item)   # payload = fila cruda completa
        return web.json_response({"status": "ok", "data": item}, status=201)

    if existing["activo"] == 0:
        upd = {**optional, "activo": 1}
        assignments = ", ".join(f"{k} = ?" for k in upd)
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*upd.values(), item_id])
        await db.commit()
        await _emit(request, "update", item_id, upd)
        return web.json_response({"status": "ok", "data": await _get(db, item_id)})

    # Ya existe y está activo → idempotente; las ediciones van por PUT.
    return web.json_response({"status": "ok", "data": existing})


async def update_item(request: web.Request) -> web.Response:
    """PUT /api/contratos/{id}/items/{numero_item} — editar campos mutables (no numero_item)."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        numero_item = int(request.match_info["numero_item"])
    except ValueError:
        return _error("numero_item inválido", 400)
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
        _validate_monto(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    item_id = _item_id(contrato_id, numero_item)
    if await _get(db, item_id) is None:
        return _error("ítem no encontrado", 404)
    proveedor_id = payload.get("proveedor_id")
    if "proveedor_id" in payload and proveedor_id is not None and not await _exists(db, "proveedores", proveedor_id):
        return _error("proveedor no encontrado", 404)

    assignments = ", ".join(f"{k} = ?" for k in payload)
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*payload.values(), item_id])
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar ítem: {exc}", 400)

    await _emit(request, "update", item_id, dict(payload))
    return web.json_response({"status": "ok", "data": await _get(db, item_id)})


async def delete_item(request: web.Request) -> web.Response:
    """DELETE /api/contratos/{id}/items/{numero_item} — baja lógica."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        numero_item = int(request.match_info["numero_item"])
    except ValueError:
        return _error("numero_item inválido", 400)

    item_id = _item_id(contrato_id, numero_item)
    if await _get(db, item_id) is None:
        return _error("ítem no encontrado", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (item_id,))
    await db.commit()
    await _emit(request, "delete", item_id, {"activo": 0})
    return web.json_response({"status": "ok", "data": await _get(db, item_id)})
