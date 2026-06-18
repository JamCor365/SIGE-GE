"""Garantías del contrato: cartas fianza / seguros (fiel cumplimiento, adelantos…).

Identidad UUID surrogate (como proveedores): numero_carta_fianza es externo pero
editable/ausente y NO converge → no es PK ni UNIQUE (soft dedup). La distinción
fiel cumplimiento principal/accesoria se deriva de prestacion_id → prestaciones.clase.
prestacion_id e item_id son refs BLANDAS validadas en ruta (existen + pertenecen
al contrato). estado NO incluye VENCIDA: se deriva de fecha_vencimiento (read-time).
"""
import logging
import uuid

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.garantias")

TABLE = "garantias"

# contrato_id viene del path; id/created_at/updated_at los pone el backend.
INSERTABLE_FIELDS = {
    "prestacion_id",
    "item_id",
    "tipo",
    "modalidad",
    "numero_carta_fianza",
    "monto",
    "moneda",
    "entidad_emisora",
    "fecha_emision",
    "fecha_vencimiento",
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


def _validate_monto(payload: dict) -> None:
    if "monto" in payload and payload["monto"] is not None:
        v = payload["monto"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError("monto debe ser un entero de céntimos >= 0")


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


async def _get(db: aiosqlite.Connection, garantia_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (garantia_id,)) as cur:
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


async def _emit(request: web.Request, action: str, garantia_id: str, payload: dict) -> None:
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "garantia"
    user = get_user(request.app["config"])
    event = make_event(action, garantia_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s garantia %s: %s", action, garantia_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def list_garantias(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/garantias — garantías activas del contrato."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    async with db.execute(
        f"SELECT * FROM {TABLE} WHERE contrato_id = ? AND activo = 1 ORDER BY fecha_vencimiento, created_at",
        (contrato_id,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return web.json_response({"status": "ok", "data": rows})


async def create_garantia(request: web.Request) -> web.Response:
    """POST /api/contratos/{id}/garantias."""
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
        _validate_monto(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    ref_err = await _check_refs(db, payload, contrato_id)
    if ref_err is not None:
        return ref_err

    garantia_id = uuid.uuid4().hex
    fields = ["id", "contrato_id", *payload.keys(), "created_at", "updated_at"]
    placeholders = (
        ["?"] * (2 + len(payload))
        + ["datetime('now','localtime')", "datetime('now','localtime')"]
    )
    values = [garantia_id, contrato_id, *payload.values()]
    sql = f"INSERT INTO {TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear garantía: {exc}", 400)

    garantia = await _get(db, garantia_id)
    await _emit(request, "create", garantia_id, garantia)
    return web.json_response({"status": "ok", "data": garantia}, status=201)


async def update_garantia(request: web.Request) -> web.Response:
    """PUT /api/contratos/{id}/garantias/{garantia_id}."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    garantia_id = request.match_info["garantia_id"]
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

    current = await _get(db, garantia_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("garantía no encontrada", 404)
    ref_err = await _check_refs(db, payload, contrato_id)
    if ref_err is not None:
        return ref_err

    assignments = ", ".join(f"{k} = ?" for k in payload)
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*payload.values(), garantia_id])
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar garantía: {exc}", 400)

    await _emit(request, "update", garantia_id, dict(payload))
    return web.json_response({"status": "ok", "data": await _get(db, garantia_id)})


async def delete_garantia(request: web.Request) -> web.Response:
    """DELETE /api/contratos/{id}/garantias/{garantia_id} — baja lógica."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    garantia_id = request.match_info["garantia_id"]

    current = await _get(db, garantia_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("garantía no encontrada", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (garantia_id,))
    await db.commit()
    await _emit(request, "delete", garantia_id, {"activo": 0})
    return web.json_response({"status": "ok", "data": await _get(db, garantia_id)})
