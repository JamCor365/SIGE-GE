"""
Motor de sincronización entrante: aplica eventos remotos de SharePoint al cache.db local.

Modelo:
- Cada PC rastrea qué event_ids ya conoce (en events_log).
- Al sincronizar, descarga todos los eventos de events_pending/ en SharePoint.
- Aplica solo los que no están en events_log local (nuevos de otras máquinas).
- Los eventos propios ya están en events_log (se registraron al crearse) → se saltan.
- events_pending/ en SharePoint nunca se borra desde el cliente.
"""
import logging

import aiosqlite

from backend.events import log_event

log = logging.getLogger("sige.sync_engine")

_ENTITY_TABLE = {
    "grupo_electrogeno": "grupos_electrogenos",
    "sede": "sedes",
    "macroregion": "macroregiones",
    "tta": "tta",
}


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return {row[1] for row in rows}


async def _apply_one(db: aiosqlite.Connection, event: dict) -> None:
    entity = event["entity"]
    action = event["action"]
    entity_id = event["entity_id"]

    table = _ENTITY_TABLE.get(entity)
    if not table:
        raise ValueError(f"Entidad desconocida: {entity!r}")

    valid_cols = await _table_columns(db, table)
    payload = {
        k: v for k, v in event["payload"].items()
        if k in valid_cols and not k.startswith("_")
    }

    if action == "create":
        if not payload:
            raise ValueError("payload vacío tras filtrar columnas de tabla")
        cols = list(payload.keys())
        placeholders = ["?" for _ in cols]
        await db.execute(
            f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})",
            list(payload.values()),
        )

    elif action == "update":
        if not payload:
            return
        assignments = ", ".join(f"{k} = ?" for k in payload)
        await db.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",
            [*payload.values(), entity_id],
        )

    elif action == "delete":
        await db.execute(
            f"UPDATE {table} SET activo = 0 WHERE id = ?", (entity_id,)
        )

    else:
        raise ValueError(f"Acción desconocida: {action!r}")


async def apply_remote_events(db: aiosqlite.Connection, storage) -> dict:
    """
    Descarga todos los eventos de events_pending/ y aplica los desconocidos localmente.
    Devuelve {"applied": N, "skipped": N, "errors": [...]}.
    """
    event_ids = await storage.list_pending()
    applied, skipped = 0, 0
    errors: list[dict] = []

    for event_id in event_ids:
        async with db.execute(
            "SELECT 1 FROM events_log WHERE event_id = ?", (event_id,)
        ) as cur:
            if await cur.fetchone():
                skipped += 1
                continue

        try:
            event = await storage.download_event(event_id)
            if event is None:
                errors.append({"event_id": event_id, "error": "no encontrado en storage"})
                continue

            await _apply_one(db, event)
            await log_event(db, event, synced=1)
            await db.commit()
            applied += 1
            log.info("Evento aplicado: %s (%s %s)", event_id, event["action"], event["entity"])

        except Exception as exc:
            log.warning("Error al aplicar evento %s: %s", event_id, exc)
            errors.append({"event_id": event_id, "error": str(exc)})

    return {"applied": applied, "skipped": skipped, "errors": errors}
