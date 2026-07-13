"""Adendas del contrato: modificaciones tras la firma (Ley de Contrataciones).

Identidad UUID surrogate (como prestaciones/garantias). `numero` (1=Primera,
2=Segunda…) es el identificador humano pero NO es PK ni UNIQUE: el dedup por
(contrato_id, numero) es BLANDO (aviso, no rechazo) — dos máquinas offline no
deben descartar en silencio una de dos filas con UUID distinto. Nada le hace FK.

monto_delta_principal / monto_delta_accesorio: céntimos CON SIGNO (negativo si
reduce); NULL si la adenda no toca ese monto. plazo_delta_dias: variación de plazo.
tipo se valida por CHECK en la BD.
"""
import logging
import uuid

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.adendas")

TABLE = "adendas"

# contrato_id viene del path; id/created_at/updated_at los pone el backend.
INSERTABLE_FIELDS = {
    "numero",
    "fecha",
    "tipo",
    "base_legal",
    "objeto",
    "monto_delta_principal",
    "monto_delta_accesorio",
    "plazo_delta_dias",
    "observaciones",
    "activo",
}
UPDATABLE_FIELDS = INSERTABLE_FIELDS

# Enteros con signo (deltas): negativos válidos. numero/plazo también enteros.
_SIGNED_INT_FIELDS = ("monto_delta_principal", "monto_delta_accesorio", "plazo_delta_dias")


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
    """numero (>=1) y los deltas (con signo) deben ser enteros, no bool ni float."""
    if "numero" in payload and payload["numero"] is not None:
        v = payload["numero"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ValueError("numero debe ser un entero >= 1")
    for field in _SIGNED_INT_FIELDS:
        if field in payload and payload[field] is not None:
            v = payload[field]
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(f"{field} debe ser un entero (céntimos/días, con signo)")


async def _exists(db: aiosqlite.Connection, table: str, id_value) -> bool:
    async with db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_value,)) as cur:
        return await cur.fetchone() is not None


async def _get(db: aiosqlite.Connection, adenda_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (adenda_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def _emit(request: web.Request, action: str, adenda_id: str, payload: dict) -> None:
    db = request.app["db"]
    event_payload = dict(payload)
    event_payload["_entity"] = "adenda"
    user = get_user(request.app["config"])
    event = make_event(action, adenda_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para %s adenda %s: %s", action, adenda_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()


async def list_adendas(request: web.Request) -> web.Response:
    """GET /api/contratos/{id}/adendas — adendas activas del contrato, por número."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)
    async with db.execute(
        f"SELECT * FROM {TABLE} WHERE contrato_id = ? AND activo = 1 ORDER BY numero, created_at",
        (contrato_id,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return web.json_response({"status": "ok", "data": rows})


async def create_adenda(request: web.Request) -> web.Response:
    """POST /api/contratos/{id}/adendas."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    unknown = set(payload) - INSERTABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if payload.get("numero") is None:
        return _error("numero es obligatorio (1=Primera, 2=Segunda…)", 400)
    try:
        _validate_ints(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not await _exists(db, "contratos", contrato_id):
        return _error("contrato no encontrado", 404)

    adenda_id = uuid.uuid4().hex
    fields = ["id", "contrato_id", *payload.keys(), "created_at", "updated_at"]
    placeholders = (
        ["?"] * (2 + len(payload))
        + ["datetime('now','localtime')", "datetime('now','localtime')"]
    )
    values = [adenda_id, contrato_id, *payload.values()]
    sql = f"INSERT INTO {TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear adenda: {exc}", 400)

    adenda = await _get(db, adenda_id)
    await _emit(request, "create", adenda_id, adenda)
    return web.json_response({"status": "ok", "data": adenda}, status=201)


async def update_adenda(request: web.Request) -> web.Response:
    """PUT /api/contratos/{id}/adendas/{adenda_id}."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    adenda_id = request.match_info["adenda_id"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not payload:
        return _error("payload vacío", 400)
    unknown = set(payload) - UPDATABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if "numero" in payload and payload["numero"] is None:
        return _error("numero no puede ser null", 400)
    try:
        _validate_ints(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    current = await _get(db, adenda_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("adenda no encontrada", 404)

    assignments = ", ".join(f"{k} = ?" for k in payload)
    try:
        await db.execute(f"UPDATE {TABLE} SET {assignments} WHERE id = ?", [*payload.values(), adenda_id])
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar adenda: {exc}", 400)

    await _emit(request, "update", adenda_id, dict(payload))
    return web.json_response({"status": "ok", "data": await _get(db, adenda_id)})


async def delete_adenda(request: web.Request) -> web.Response:
    """DELETE /api/contratos/{id}/adendas/{adenda_id} — baja lógica."""
    db = request.app["db"]
    contrato_id = request.match_info["id"]
    adenda_id = request.match_info["adenda_id"]

    current = await _get(db, adenda_id)
    if current is None or current["contrato_id"] != contrato_id:
        return _error("adenda no encontrada", 404)

    await db.execute(f"UPDATE {TABLE} SET activo = 0 WHERE id = ?", (adenda_id,))
    await db.commit()
    await _emit(request, "delete", adenda_id, {"activo": 0})
    return web.json_response({"status": "ok", "data": await _get(db, adenda_id)})
