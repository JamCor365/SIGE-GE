import json
import logging
import uuid

import aiosqlite
from aiohttp import web

from backend.events import get_user, log_event, make_event

log = logging.getLogger("sige.contratos")

CONTRATOS_TABLE = "contratos"

# Campos que el cliente puede enviar al crear. El id NO está aquí: es un UUID
# generado en el backend (nunca se acepta del cliente). created_at/updated_at
# los pone el servidor.
INSERTABLE_FIELDS = {
    "numero",
    "objeto",
    "entidad_contratante",
    "procedimiento_seleccion",
    "tipos_objeto",
    "proveedor",
    "moneda",
    "monto_principal",
    "monto_accesorio",
    "fecha_inicio",
    "fecha_fin",
    "estado",
    "observaciones",
    "activo",
}

# id/created_at/updated_at no se actualizan vía API.
UPDATABLE_FIELDS = INSERTABLE_FIELDS

# Vocabulario cerrado de tipos de objeto (tokens atómicos). Un contrato es
# multivalor: puede ser varios a la vez. Se valida aquí (no con CHECK SQL,
# que no comprueba pertenencia de un set multivalor).
_TIPOS_OBJETO = {
    "ADQUISICION",
    "INSTALACION",
    "MANTENIMIENTO",
    "SERVICIO",
    "OBRA",
    "CONSULTORIA",
}

# Montos almacenados en céntimos (enteros). Ver db.py.
_MONTO_FIELDS = ("monto_principal", "monto_accesorio")


def _normalize_payload(payload: dict) -> None:
    """Valida y canonicaliza campos de cáscara EN SITIO, antes de tocar la DB.

    Tras esta función, payload["tipos_objeto"] es el STRING JSON canónico que se
    guarda en la columna Y viaja en el evento de sync — idéntico en ambos lados,
    de modo que apply_remote reconstruye el mismo array en otra máquina.
    """
    if "tipos_objeto" in payload:
        value = payload["tipos_objeto"]
        if value is None or value == []:
            payload["tipos_objeto"] = None
        else:
            if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
                raise ValueError("tipos_objeto debe ser una lista de tokens")
            unknown = [t for t in value if t not in _TIPOS_OBJETO]
            if unknown:
                raise ValueError(f"tipos_objeto inválidos: {', '.join(unknown)}")
            # Dedupe + orden estable → forma canónica determinista.
            canonical = sorted(set(value))
            payload["tipos_objeto"] = json.dumps(canonical)

    for field in _MONTO_FIELDS:
        if field in payload and payload[field] is not None:
            value = payload[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} debe ser un entero de céntimos >= 0")


def _to_api(contrato: dict | None) -> dict | None:
    """Vista de API: tipos_objeto se devuelve como ARRAY (la columna guarda STRING).

    Solo para respuestas al cliente — nunca para el payload del evento, que debe
    conservar el string canónico para que el sync round-tripee idéntico.
    """
    if contrato is None:
        return None
    out = dict(contrato)
    raw = out.get("tipos_objeto")
    out["tipos_objeto"] = json.loads(raw) if raw else None
    return out


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


async def _get(db: aiosqlite.Connection, contrato_id: str) -> dict | None:
    async with db.execute(f"SELECT * FROM {CONTRATOS_TABLE} WHERE id = ?", (contrato_id,)) as cur:
        return _row_to_dict(await cur.fetchone())


async def list_contratos(request: web.Request) -> web.Response:
    db = request.app["db"]
    async with db.execute(f"SELECT * FROM {CONTRATOS_TABLE} ORDER BY created_at DESC") as cur:
        rows = await cur.fetchall()
    return web.json_response({"status": "ok", "data": [_to_api(dict(row)) for row in rows]})


async def get_contrato(request: web.Request) -> web.Response:
    contrato = await _get(request.app["db"], request.match_info["id"])
    if contrato is None:
        return _error("contrato no encontrado", 404)
    return web.json_response({"status": "ok", "data": _to_api(contrato)})


async def create_contrato(request: web.Request) -> web.Response:
    db = request.app["db"]
    try:
        payload = await _read_json(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    unknown = set(payload) - INSERTABLE_FIELDS
    if unknown:
        return _error(f"campos no permitidos: {', '.join(sorted(unknown))}", 400)
    if not payload.get("objeto"):
        return _error("campo requerido: objeto", 400)
    try:
        _normalize_payload(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    # Identidad de fila: UUID opaco generado aquí, no en el frontend.
    contrato_id = uuid.uuid4().hex

    fields = ["id", *payload.keys(), "created_at", "updated_at"]
    placeholders = (
        ["?"] * (1 + len(payload))
        + ["datetime('now','localtime')", "datetime('now','localtime')"]
    )
    values = [contrato_id, *payload.values()]

    sql = f"INSERT INTO {CONTRATOS_TABLE} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    try:
        await db.execute(sql, values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo crear contrato: {exc}", 400)

    contrato = await _get(db, contrato_id)
    log.info("Contrato creado: %s", contrato_id)

    # event_payload sale de la fila cruda (_get): tipos_objeto es el STRING JSON
    # canónico, no el array. Así el evento viaja idéntico a lo guardado en columna.
    event_payload = dict(contrato)
    event_payload["_entity"] = "contrato"
    user = get_user(request.app["config"])
    event = make_event("create", contrato_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para create contrato %s: %s", contrato_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": _to_api(contrato)}, status=201)


async def update_contrato(request: web.Request) -> web.Response:
    db = request.app["db"]
    contrato_id = request.match_info["id"]
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
        _normalize_payload(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if await _get(db, contrato_id) is None:
        return _error("contrato no encontrado", 404)

    assignments = ", ".join(f"{field} = ?" for field in payload)
    values = [*payload.values(), contrato_id]
    try:
        await db.execute(f"UPDATE {CONTRATOS_TABLE} SET {assignments} WHERE id = ?", values)
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        return _error(f"no se pudo actualizar contrato: {exc}", 400)

    contrato = await _get(db, contrato_id)
    log.info("Contrato actualizado: %s", contrato_id)

    event_payload = dict(payload)
    event_payload["_entity"] = "contrato"
    user = get_user(request.app["config"])
    event = make_event("update", contrato_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para update contrato %s: %s", contrato_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": _to_api(contrato)})


async def delete_contrato(request: web.Request) -> web.Response:
    db = request.app["db"]
    contrato_id = request.match_info["id"]

    if await _get(db, contrato_id) is None:
        return _error("contrato no encontrado", 404)

    await db.execute(f"UPDATE {CONTRATOS_TABLE} SET activo = 0 WHERE id = ?", (contrato_id,))
    await db.commit()
    contrato = await _get(db, contrato_id)
    log.info("Contrato dado de baja: %s", contrato_id)

    event_payload = {"activo": 0, "_entity": "contrato"}
    user = get_user(request.app["config"])
    event = make_event("delete", contrato_id, event_payload, user, request.app["config"]["app"]["version"])
    try:
        await request.app["storage"].upload_event(event)
        await log_event(db, event, synced=1)
    except Exception as exc:
        log.warning("upload_event falló para delete contrato %s: %s", contrato_id, exc)
        await log_event(db, event, synced=0, error_msg=str(exc))
    await db.commit()

    return web.json_response({"status": "ok", "data": _to_api(contrato)})
