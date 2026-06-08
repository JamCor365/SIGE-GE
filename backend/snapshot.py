"""
Lógica de snapshot: bootstrap de máquina nueva y generación en background.

Bootstrap (sincrónico, solo máquinas nuevas):
  bootstrap_if_new(storage, db_path) → dict | None
    Detecta cache.db de 0 bytes, descarga latest_snapshot.db + meta desde master/.
    Retorna el meta dict (para filtrar eventos post-snapshot) o None.

Aplicación de eventos post-snapshot (sincrónica, justo después de init_db):
  apply_post_snapshot_events(db, storage, meta)
    Aplica los eventos de events_pending/ con event_id > meta["last_event_id"].

Generación en background (una vez por sesión, no bloquea el startup):
  _maybe_generate_snapshot(app)
    Evalúa las tres condiciones localmente y sube DB+meta si todas se cumplen.
"""
import asyncio
import getpass
import json
import logging
import platform
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.db import DB_PATH
from backend.events import log_event
from backend.sync_engine import _apply_one

log = logging.getLogger("sige.snapshot")

TZ = timezone(offset=timedelta(hours=-5))


# ── Utilidad de backup ────────────────────────────────────────────────────────

def _backup_and_read_last_event_id(db_path: Path) -> tuple[str | None, bytes]:
    """
    Hace backup WAL-safe de db_path con sqlite3.Connection.backup() hacia un
    archivo temporal. Lee MAX(event_id) FROM events_log DESDE EL BACKUP (no de
    la conexión en vivo) → meta y .db son coherentes por construcción.
    Retorna (last_event_id, db_bytes). last_event_id es None si events_log vacío.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(tmp_path))
        src.backup(dst)          # checkpoint del WAL incluido; bloquea escrituras brevemente
        src.close()
        row = dst.execute("SELECT MAX(event_id) FROM events_log").fetchone()
        last_event_id = row[0] if row else None
        dst.close()
        return last_event_id, tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Bootstrap de máquina nueva ────────────────────────────────────────────────

async def bootstrap_if_new(storage, db_path: Path = DB_PATH) -> dict | None:
    """
    Si db_path es un archivo de 0 bytes (nueva instalación vía INICIAR.ps1),
    intenta restaurar desde el snapshot en master/.
    Retorna el meta dict si restauró, None en cualquier otro caso.
    El meta retornado se usa en on_startup para llamar apply_post_snapshot_events.
    """
    if not (db_path.exists() and db_path.stat().st_size == 0):
        return None

    print("Restaurando base inicial desde snapshot...", flush=True)

    try:
        meta = await storage.download_snapshot_meta()
    except Exception as exc:
        print(f"  No se pudo contactar storage: {exc}. Continuando sin snapshot.", flush=True)
        return None

    if meta is None:
        print("  Sin snapshot disponible. El primer sync cargará todos los eventos.", flush=True)
        return None

    last_id_preview = str(meta.get("last_event_id", "?"))[:19]
    print(f"  Descargando snapshot (hasta {last_id_preview})...", flush=True)

    try:
        db_bytes = await storage.download_snapshot_db()
    except Exception as exc:
        print(f"  Error al descargar snapshot: {exc}. Continuando sin datos.", flush=True)
        return None

    if db_bytes is None:
        print("  snapshot_meta.json existe pero latest_snapshot.db no disponible.", flush=True)
        return None

    db_path.write_bytes(db_bytes)
    return meta


async def apply_post_snapshot_events(db, storage, meta: dict) -> None:
    """
    Aplica los eventos de events_pending/ con event_id > meta["last_event_id"].
    Llamar justo después de init_db() cuando bootstrap_if_new retornó un meta.
    """
    last_event_id = meta.get("last_event_id")
    try:
        pending = await storage.list_pending()
    except Exception as exc:
        log.warning("Bootstrap: no se pudo listar events_pending/ — %s", exc)
        return

    to_apply = sorted(
        eid for eid in pending
        if last_event_id is None or eid > last_event_id
    )
    if not to_apply:
        return

    print(f"  Aplicando {len(to_apply)} eventos posteriores al snapshot...", flush=True)
    applied = 0
    for event_id in to_apply:
        try:
            event = await storage.download_event(event_id)
            if event is None:
                continue
            await _apply_one(db, event)
            await log_event(db, event, synced=1)
            await db.commit()
            applied += 1
        except Exception as exc:
            log.warning("Bootstrap: error al aplicar %s — %s", event_id, exc)

    log.info("Bootstrap: %d evento(s) aplicado(s) post-snapshot", applied)


# ── Generación en background ──────────────────────────────────────────────────

async def _maybe_generate_snapshot(app) -> None:
    """
    Genera y sube snapshot a master/ si las tres condiciones se cumplen.
    Llamar con asyncio.ensure_future() — no bloquea el startup.
    El flag app["_snapshot_done"] garantiza una sola ejecución por sesión.
    """
    if app.get("_snapshot_done"):
        return
    app["_snapshot_done"] = True   # guard antes del primer await

    db      = app["db"]
    storage = app["storage"]
    config  = app["config"]
    db_path = app.get("_db_path", DB_PATH)

    # Condición 1: sin eventos locales pendientes de subir a SharePoint
    async with db.execute(
        "SELECT 1 FROM events_log WHERE synced=0 LIMIT 1"
    ) as cur:
        if await cur.fetchone():
            log.debug("Snapshot: omitido — hay eventos con synced=0")
            return

    # Condición 2: último snapshot no es de hoy (o no existe)
    try:
        meta = await storage.download_snapshot_meta()
    except Exception as exc:
        log.warning(
            "Snapshot: download_snapshot_meta() falló al leer master/snapshot_meta.json"
            " — %s: %s", type(exc).__name__, exc,
        )
        return

    today = datetime.now(TZ).date()
    if meta:
        try:
            snap_date = datetime.fromisoformat(meta["generated_at"]).date()
            if snap_date == today:
                log.debug("Snapshot: omitido — ya generado hoy")
                return
        except (KeyError, ValueError):
            pass  # meta malformado → ignorar y regenerar

    # Condición 3: hay eventos en events_pending/ posteriores al último snapshot
    last_snap = meta.get("last_event_id") if meta else None
    try:
        pending = await storage.list_pending()
    except Exception as exc:
        log.warning("Snapshot: no se pudo listar pending — %s", exc)
        return

    newer = [eid for eid in pending if last_snap is None or eid > last_snap]
    if not newer:
        log.debug("Snapshot: omitido — sin eventos nuevos desde el último snapshot")
        return

    # Las tres condiciones se cumplen → generar
    log.info("Snapshot: generando backup...")
    try:
        loop = asyncio.get_event_loop()
        last_event_id, db_bytes = await loop.run_in_executor(
            None, _backup_and_read_last_event_id, db_path
        )
    except Exception as exc:
        log.warning("Snapshot: error en backup local — %s", exc)
        return

    if last_event_id is None:
        log.debug("Snapshot: omitido — events_log vacío (DB sin datos)")
        return

    new_meta = {
        "last_event_id": last_event_id,
        "generated_at":  datetime.now(TZ).isoformat(),
        "generated_by":  (config.get("app", {}).get("user") or "").strip() or getpass.getuser(),
        "machine":        platform.node() or "LOCAL",
        "app_version":   config["app"]["version"],
    }
    try:
        await storage.upload_snapshot(db_bytes, new_meta)
        log.info(
            "Snapshot generado hasta %s (%.1f KB)",
            last_event_id, len(db_bytes) / 1024,
        )
    except Exception as exc:
        log.warning("Snapshot: error al subir — %s", exc)
