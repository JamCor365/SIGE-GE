"""
Motor de sincronización: sube eventos locales pendientes y aplica eventos remotos.

Modelo:
- Cada PC rastrea qué event_ids ya conoce (en events_log).
- Ciclo completo al sincronizar:
    1. retry_pending_uploads: reintenta subir eventos locales con synced=0.
    2. apply_remote_events:   descarga events_pending/ y aplica los desconocidos.
- events_pending/ en SharePoint nunca se borra desde el cliente.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from backend.events import log_event

log = logging.getLogger("sige.sync_engine")

_ENTITY_TABLE = {
    "grupo_electrogeno": "grupos_electrogenos",
    "sede": "sedes",
    "macroregion": "macroregiones",
    "tta": "tta",
    "contrato": "contratos",
    "contrato_ge": "contrato_ge",
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


async def retry_pending_uploads(db: aiosqlite.Connection, storage) -> dict:
    """
    Reintenta subir a SharePoint los eventos que quedaron synced=0.
    - Éxito  → synced=1, synced_at actualizado, error_msg limpio.
    - Fallo  → error_msg actualizado, synced sigue en 0, continúa con el resto.
    upload_event usa overwrite=true, así que reintentar un evento ya subido es seguro.
    """
    uploaded = 0
    upload_failed = 0

    async with db.execute(
        """
        SELECT event_id, entity, action, entity_id, created_at,
               created_by, machine, app_version, payload_json
        FROM events_log
        WHERE synced = 0
        ORDER BY created_at ASC
        """
    ) as cur:
        rows = await cur.fetchall()

    for row in rows:
        event = {
            "event_id":    row["event_id"],
            "entity":      row["entity"],
            "action":      row["action"],
            "entity_id":   row["entity_id"],
            "created_at":  row["created_at"],
            "created_by":  row["created_by"],
            "machine":     row["machine"],
            "app_version": row["app_version"],
            "payload":     json.loads(row["payload_json"]),
        }
        try:
            await storage.upload_event(event)
            synced_at = datetime.now(timezone(offset=timedelta(hours=-5))).isoformat()
            await db.execute(
                "UPDATE events_log SET synced=1, synced_at=?, error_msg=NULL WHERE event_id=?",
                (synced_at, row["event_id"]),
            )
            await db.commit()
            uploaded += 1
            log.info("Evento resubido: %s", row["event_id"])
        except Exception as exc:
            await db.execute(
                "UPDATE events_log SET error_msg=? WHERE event_id=?",
                (str(exc), row["event_id"]),
            )
            await db.commit()
            upload_failed += 1
            log.warning("Reintento fallido para %s: %s", row["event_id"], exc)

    return {"uploaded": uploaded, "upload_failed": upload_failed}


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
