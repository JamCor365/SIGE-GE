"""
Lógica de snapshot: recuperación de estado, archivado y generación en background.

Recuperación de estado (sincrónica, antes de init_db):
  recover_state(storage, db_path, config) → RecoveryResult | None
    Caso máquina nueva (cache.db de 0 bytes): restaura desde master/.
    Caso máquina atrasada (local_max_event_id < snapshot.last_event_id):
    sube los synced=0 locales, baja el snapshot y reemplaza cache.db de forma
    atómica. Retorna meta + los eventos locales capturados, o None.

Aplicación post-recuperación (sincrónica, justo después de init_db):
  apply_post_snapshot_events(db, storage, meta, local_events=None)
    Aplica la unión ordenada de (events_pending/ con id > last_event_id) ∪
    (local_events capturados), deduplicada por pertenencia a events_log.

Archivado (en la máquina que generó el snapshot, tras subirlo):
  _archive_processed_events(db, storage, snap_last)
    Mueve events_pending/ → events_processed/ los eventos con
    id <= snap_last AND presentes en events_log local (== contenidos en el
    snapshot recién subido). Copia (overwrite) y luego borra: idempotente.

Generación en background (una vez por sesión, no bloquea el startup):
  _maybe_generate_snapshot(app)
    Evalúa las tres condiciones localmente, sube DB+meta y archiva si todas
    se cumplen.

INVARIANTE LEXICOGRÁFICO (del que depende toda comparación de event_id):
  event_id = "{YYYYMMDD}_{HHMMSS}_{user}_{action}_{entity}_{entity_id}_{hex6}"
  El prefijo de fecha/hora es de ancho fijo y zero-padded (strftime), por lo
  que el orden lexicográfico de strings event_id COINCIDE con el cronológico.
  Las comparaciones `eid <= snap_last`, `local_max < snap_last`, `eid >
  last_event_id` y el `sorted()` de la aplicación se basan en esto. Si el
  formato del prefijo cambia, todas estas comparaciones se rompen.
"""
import asyncio
import getpass
import json
import logging
import os
import platform
import sqlite3
import tempfile
from dataclasses import dataclass, field
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


# ── Recuperación de máquina atrasada (generaliza bootstrap_if_new) ────────────

@dataclass
class RecoveryResult:
    """Resultado de recover_state, consumido por on_startup tras init_db."""
    meta: dict
    local_events: list[dict] = field(default_factory=list)


def _read_local_max_event_id(db_path: Path) -> str | None:
    """MAX(event_id) de events_log con sqlite3 crudo. None si vacío o sin tabla."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT MAX(event_id) FROM events_log").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None       # events_log no existe todavía
    finally:
        conn.close()


def _read_unsynced_events(db_path: Path) -> list[dict]:
    """Eventos synced=0 de events_log con sqlite3 crudo, con shape de evento."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT event_id, entity, action, entity_id, created_at,
                      created_by, machine, app_version, payload_json
               FROM events_log WHERE synced = 0 ORDER BY event_id ASC"""
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [
        {
            "event_id":    r["event_id"],
            "entity":      r["entity"],
            "action":      r["action"],
            "entity_id":   r["entity_id"],
            "created_at":  r["created_at"],
            "created_by":  r["created_by"],
            "machine":     r["machine"],
            "app_version": r["app_version"],
            "payload":     json.loads(r["payload_json"]),
        }
        for r in rows
    ]


def _atomic_replace_db(db_path: Path, db_bytes: bytes) -> None:
    """Reemplaza db_path por db_bytes de forma atómica (temp + os.replace, mismo
    FS). Borra los sidecars -wal/-shm de la db vieja: el caller garantiza que
    SQLite NO tiene la db abierta en este momento, así que es seguro.
    """
    tmp_path = db_path.with_name(db_path.name + ".tmp")
    tmp_path.write_bytes(db_bytes)
    for sidecar in (db_path.with_name(db_path.name + "-wal"),
                    db_path.with_name(db_path.name + "-shm")):
        sidecar.unlink(missing_ok=True)
    os.replace(tmp_path, db_path)        # atómico dentro del mismo filesystem


async def recover_state(storage, db_path: Path = DB_PATH, config: dict | None = None) -> RecoveryResult | None:
    """
    Generaliza bootstrap_if_new para cubrir también máquinas atrasadas.

    - Máquina nueva (cache.db de 0 bytes): delega en bootstrap_if_new.
    - Máquina atrasada (local_max_event_id < snapshot.last_event_id): re-bootstrap
      crash-safe en este orden OBLIGATORIO:
        1. Subir los synced=0 locales a events_pending/ ANTES de tocar cache.db.
           Si algún upload falla → aborta sin tocar cache.db (no perder cambios).
        2. Bajar latest_snapshot.db.
        3. Reemplazar cache.db de forma atómica (temp + rename, sidecars borrados).
        4. Retornar meta + local_events para que el caller los aplique (la unión
           ordenada cubre los synced=0 aunque su id sea <= last_event_id).

    Retorna RecoveryResult si restauró/recuperó, None en cualquier otro caso.
    """
    # Caso máquina nueva: cache.db de 0 bytes → camino bootstrap existente.
    if db_path.exists() and db_path.stat().st_size == 0:
        meta = await bootstrap_if_new(storage, db_path)
        return RecoveryResult(meta=meta) if meta is not None else None

    if not db_path.exists():
        return None

    # Caso posible máquina atrasada: comparar local_max con snapshot.last_event_id.
    local_max = _read_local_max_event_id(db_path)
    try:
        meta = await storage.download_snapshot_meta()
    except Exception as exc:
        log.warning("Recuperación: no se pudo leer meta del snapshot — %s: %s",
                    type(exc).__name__, exc)
        return None
    if meta is None:
        return None
    snap_last = meta.get("last_event_id")
    if snap_last is None:
        return None

    # local_max >= snap_last → al día o adelantada (orden lexicográfico ≡ cronológico).
    if local_max is not None and local_max >= snap_last:
        return None

    log.info("Recuperación: máquina atrasada (local=%s < snapshot=%s) — re-bootstrap",
             local_max, snap_last)

    # 1. Subir synced=0 ANTES de tocar cache.db. Si alguno falla, abortar.
    local_events = _read_unsynced_events(db_path)
    for ev in local_events:
        try:
            await storage.upload_event(ev)
        except Exception as exc:
            log.warning(
                "Recuperación abortada: no se pudo subir el evento local %s "
                "(%s: %s). cache.db intacto, se reintenta al próximo arranque.",
                ev["event_id"], type(exc).__name__, exc,
            )
            return None

    # 2. Bajar el snapshot.
    try:
        db_bytes = await storage.download_snapshot_db()
    except Exception as exc:
        log.warning("Recuperación: error al descargar snapshot — %s: %s",
                    type(exc).__name__, exc)
        return None
    if db_bytes is None:
        log.warning("Recuperación: snapshot_meta.json existe pero latest_snapshot.db no.")
        return None

    # 3. Reemplazo atómico (cache.db NO está abierto por SQLite aquí).
    _atomic_replace_db(db_path, db_bytes)

    # 4. El caller aplica meta + local_events tras init_db.
    return RecoveryResult(meta=meta, local_events=local_events)


# ── Aplicación post-recuperación ──────────────────────────────────────────────

async def apply_post_snapshot_events(
    db, storage, meta: dict, local_events: list[dict] | None = None
) -> None:
    """
    Aplica la unión ordenada por event_id de:
      - events_pending/ con event_id > meta["last_event_id"] (eventos de otras
        máquinas posteriores al snapshot), y
      - local_events capturados en el re-bootstrap (pueden tener id <=
        last_event_id; por eso NO se filtran por el corte).
    Deduplicada por pertenencia a events_log (cada evento se aplica una sola vez).
    Llamar justo después de init_db() cuando recover_state retornó un resultado.
    """
    last_event_id = meta.get("last_event_id")
    try:
        pending = await storage.list_pending()
    except Exception as exc:
        log.warning("Recuperación: no se pudo listar events_pending/ — %s", exc)
        pending = []

    # event_id → dict en memoria (local) o None (descargar de storage).
    sources: dict[str, dict | None] = {}
    for eid in pending:
        if last_event_id is None or eid > last_event_id:
            sources.setdefault(eid, None)
    for ev in (local_events or []):
        sources[ev["event_id"]] = ev      # el dict local evita una descarga

    if not sources:
        return

    print(f"  Aplicando {len(sources)} eventos tras la recuperación...", flush=True)
    applied = 0
    for event_id in sorted(sources):       # orden lexicográfico ≡ cronológico
        async with db.execute(
            "SELECT 1 FROM events_log WHERE event_id = ?", (event_id,)
        ) as cur:
            if await cur.fetchone():
                continue                   # dedup por pertenencia a events_log
        try:
            event = sources[event_id]
            if event is None:
                event = await storage.download_event(event_id)
            if event is None:
                continue
            await _apply_one(db, event)
            await log_event(db, event, synced=1)
            await db.commit()
            applied += 1
        except Exception as exc:
            log.warning("Recuperación: error al aplicar %s — %s", event_id, exc)

    log.info("Recuperación: %d evento(s) aplicado(s)", applied)


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
        return

    # Archivado: solo DESPUÉS de confirmar snapshot+meta en master/ (nunca antes).
    await _archive_processed_events(db, storage, last_event_id)


async def _archive_processed_events(db, storage, snap_last: str) -> None:
    """
    Mueve de events_pending/ a events_processed/ los eventos elegibles.

    Elegible = (event_id <= snap_last) AND (event_id ∈ events_log local).
    Como el snapshot ES la db local al momento del backup, "∈ events_log" ≡
    "contenido en el snapshot": esto excluye pending de otras máquinas aún no
    aplicados y los synced=0 inyectados por un re-bootstrap, que NO están en el
    snapshot y se perderían si se archivaran antes de que las máquinas lentas
    los apliquen.
    """
    try:
        pending = await storage.list_pending()
    except Exception as exc:
        log.warning("Archivado: no se pudo listar pending — %s", exc)
        return

    candidates = [eid for eid in pending if eid <= snap_last]
    if not candidates:
        return

    # Set de event_ids contenidos en el snapshot (== events_log local).
    async with db.execute("SELECT event_id FROM events_log") as cur:
        in_snapshot = {row[0] for row in await cur.fetchall()}

    archived = 0
    for event_id in candidates:
        if event_id not in in_snapshot:
            continue
        try:
            await storage.archive_processed(event_id)
            archived += 1
        except Exception as exc:
            log.warning("Archivado: error al archivar %s — %s", event_id, exc)

    if archived:
        log.info("Archivado: %d evento(s) movido(s) a events_processed/", archived)
