"""
Tests para RamaE Fase 2 — Snapshot en master/.

(a) Coherencia meta/DB: last_event_id leído del backup coincide con el contenido del .db.
(b) Bootstrap: máquina nueva restaura desde snapshot y aplica eventos posteriores.
(c) Condiciones de generación: _maybe_generate_snapshot aborta correctamente en cada caso.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from backend.snapshot import (
    _archive_processed_events,
    _backup_and_read_last_event_id,
    _maybe_generate_snapshot,
    apply_post_snapshot_events,
    bootstrap_if_new,
    recover_state,
)
from backend.storage.local_folder import LocalFolderBackend

TZ = timezone(offset=timedelta(hours=-5))

# ── Fixture compartida ────────────────────────────────────────────────────────

def _create_db(db_path: Path) -> None:
    """Crea un SQLite con schema completo + events_log via stdlib."""
    schema = (Path(__file__).parent.parent / "docs" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events_log (
            event_id     TEXT PRIMARY KEY,
            entity       TEXT NOT NULL,
            action       TEXT NOT NULL,
            entity_id    TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            created_by   TEXT NOT NULL,
            machine      TEXT NOT NULL,
            app_version  TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            synced       INTEGER NOT NULL DEFAULT 0,
            synced_at    TEXT,
            error_msg    TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_log_synced ON events_log(synced)"
    )
    conn.commit()
    conn.close()


async def _open_db(db_path: Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA journal_mode = WAL")
    db.row_factory = aiosqlite.Row
    return db


def _insert_event(conn: sqlite3.Connection, event_id: str, synced: int = 1) -> None:
    conn.execute(
        """INSERT INTO events_log
           (event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (event_id, "macroregion", "create", "1",
         "2026-06-01T10:00:00-05:00", "jamin", "PC1", "1.0.0", "{}", synced),
    )


@pytest_asyncio.fixture
async def snap_env(tmp_path):
    """DB aiosqlite con schema + LocalFolderBackend aislados por test."""
    db_path = tmp_path / "snap.db"
    _create_db(db_path)
    db = await _open_db(db_path)
    storage = LocalFolderBackend(str(tmp_path / "storage"))
    yield db, storage, db_path, tmp_path
    await db.close()


# ── (a) Coherencia meta/DB ────────────────────────────────────────────────────

def test_backup_last_event_id_coherence(tmp_path):
    """last_event_id retornado por _backup_and_read_last_event_id coincide
    con MAX(event_id) del .db subido — coherencia garantizada por construcción."""
    db_path = tmp_path / "coherence.db"
    _create_db(db_path)
    conn = sqlite3.connect(str(db_path))
    _insert_event(conn, "20260605_100000_jamin_create_macroregion_1_aaa111")
    _insert_event(conn, "20260605_110000_jamin_update_macroregion_1_bbb222")
    conn.commit()
    conn.close()

    last_event_id, db_bytes = _backup_and_read_last_event_id(db_path)

    # last_event_id es el máximo lexicográfico
    assert last_event_id == "20260605_110000_jamin_update_macroregion_1_bbb222"

    # El mismo valor existe en los bytes descargados
    verify_path = tmp_path / "verify.db"
    verify_path.write_bytes(db_bytes)
    vconn = sqlite3.connect(str(verify_path))
    row = vconn.execute("SELECT MAX(event_id) FROM events_log").fetchone()
    vconn.close()
    assert row[0] == last_event_id


def test_backup_returns_none_when_events_log_empty(tmp_path):
    """Si events_log está vacío, last_event_id es None (no hay datos reales)."""
    db_path = tmp_path / "empty.db"
    _create_db(db_path)

    last_event_id, db_bytes = _backup_and_read_last_event_id(db_path)

    assert last_event_id is None
    assert len(db_bytes) > 0   # el .db existe aunque esté vacío de eventos


# ── (b) Bootstrap ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bootstrap_restores_snapshot(snap_env):
    """Máquina nueva (cache.db de 0 bytes) restaura snapshot correctamente."""
    db, storage, db_path, tmp_path = snap_env

    # Preparar snapshot: insertar macroregión + evento en la DB fuente
    await db.execute(
        "INSERT INTO macroregiones (id, nombre, activo, created_at, updated_at)"
        " VALUES (?,?,?,?,?)",
        (1, "NORTE", 1, "2026-06-01", "2026-06-01"),
    )
    await db.execute(
        """INSERT INTO events_log
           (event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("20260601_100000_jamin_create_macroregion_1_snap001",
         "macroregion", "create", "1", "2026-06-01T10:00:00-05:00",
         "jamin", "PC1", "1.0.0", '{"id":1,"nombre":"NORTE"}', 1),
    )
    await db.commit()

    last_event_id, db_bytes = _backup_and_read_last_event_id(db_path)
    meta = {
        "last_event_id": last_event_id,
        "generated_at":  "2026-06-01T10:00:00-05:00",
        "generated_by":  "jamin",
        "machine":        "PC1",
        "app_version":   "1.0.0",
    }
    await storage.upload_snapshot(db_bytes, meta)

    # Máquina nueva: cache.db de 0 bytes
    new_db_path = tmp_path / "new_cache.db"
    new_db_path.write_bytes(b"")

    returned_meta = await bootstrap_if_new(storage, db_path=new_db_path)

    assert returned_meta is not None
    assert returned_meta["last_event_id"] == last_event_id
    assert new_db_path.stat().st_size > 0

    # La DB restaurada contiene la macroregión del snapshot
    new_db = await _open_db(new_db_path)
    async with new_db.execute("SELECT nombre FROM macroregiones WHERE id=1") as cur:
        row = await cur.fetchone()
    await new_db.close()
    assert row is not None
    assert row["nombre"] == "NORTE"


@pytest.mark.asyncio
async def test_bootstrap_applies_post_snapshot_events(snap_env):
    """Después del bootstrap se aplican los eventos de pending/ posteriores al snapshot."""
    db, storage, db_path, tmp_path = snap_env

    # Snapshot base: solo macroregión 1
    await db.execute(
        "INSERT INTO macroregiones (id, nombre, activo, created_at, updated_at)"
        " VALUES (?,?,?,?,?)",
        (1, "NORTE", 1, "2026-06-01", "2026-06-01"),
    )
    snap_event_id = "20260601_100000_jamin_create_macroregion_1_snap001"
    await db.execute(
        """INSERT INTO events_log
           (event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (snap_event_id, "macroregion", "create", "1",
         "2026-06-01T10:00:00-05:00", "jamin", "PC1", "1.0.0",
         '{"id":1,"nombre":"NORTE"}', 1),
    )
    await db.commit()

    last_event_id, db_bytes = _backup_and_read_last_event_id(db_path)
    meta = {
        "last_event_id": last_event_id,
        "generated_at":  "2026-06-01T10:00:00-05:00",
        "generated_by":  "jamin",
        "machine":        "PC1",
        "app_version":   "1.0.0",
    }
    await storage.upload_snapshot(db_bytes, meta)

    # Evento posterior al snapshot: macroregión 2 (otro PC lo generó después)
    post_event = {
        "event_id":   "20260602_100000_jamin_create_macroregion_2_post001",
        "entity":     "macroregion",
        "action":     "create",
        "entity_id":  "2",
        "created_at": "2026-06-02T10:00:00-05:00",
        "created_by": "jamin",
        "machine":    "PC2",
        "app_version": "1.0.0",
        "payload":    {"id": 2, "nombre": "SUR", "activo": 1,
                       "created_at": "2026-06-02", "updated_at": "2026-06-02"},
    }
    await storage.upload_event(post_event)

    # Bootstrap en máquina nueva
    new_db_path = tmp_path / "new_cache.db"
    new_db_path.write_bytes(b"")
    returned_meta = await bootstrap_if_new(storage, db_path=new_db_path)
    assert returned_meta is not None

    new_db = await _open_db(new_db_path)
    await apply_post_snapshot_events(new_db, storage, returned_meta)

    async with new_db.execute(
        "SELECT nombre FROM macroregiones ORDER BY id"
    ) as cur:
        rows = await cur.fetchall()
    await new_db.close()

    nombres = [r["nombre"] for r in rows]
    assert "NORTE" in nombres   # del snapshot
    assert "SUR" in nombres     # del evento post-snapshot


@pytest.mark.asyncio
async def test_bootstrap_skips_when_no_snapshot(snap_env):
    """Si no hay snapshot en master/, bootstrap_if_new retorna None y no modifica el DB."""
    _, storage, _, tmp_path = snap_env

    new_db_path = tmp_path / "empty_cache.db"
    new_db_path.write_bytes(b"")

    result = await bootstrap_if_new(storage, db_path=new_db_path)

    assert result is None
    assert new_db_path.stat().st_size == 0   # no se tocó el archivo


# ── (c) Condiciones de generación ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generation_aborts_if_synced_pending(snap_env):
    """Condición 1: si hay eventos con synced=0, no se genera snapshot."""
    db, storage, db_path, _ = snap_env

    await db.execute(
        """INSERT INTO events_log
           (event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("20260605_100000_jamin_create_macroregion_1_pend",
         "macroregion", "create", "1", "2026-06-05T10:00:00-05:00",
         "jamin", "PC1", "1.0.0", "{}", 0),  # synced=0
    )
    await db.commit()

    app = {
        "_snapshot_done": False,
        "db": db, "storage": storage,
        "config": {"app": {"version": "1.0.0", "user": ""}},
        "_db_path": db_path,
    }
    await _maybe_generate_snapshot(app)

    assert await storage.download_snapshot_meta() is None


@pytest.mark.asyncio
async def test_generation_aborts_if_snapshot_from_today(snap_env):
    """Condición 2: si el snapshot ya es de hoy, no se regenera."""
    db, storage, db_path, _ = snap_env

    today_iso = datetime.now(TZ).isoformat()
    old_meta = {
        "last_event_id": "20260604_000000_x_create_macroregion_1_old",
        "generated_at":  today_iso,
        "generated_by":  "jamin", "machine": "PC1", "app_version": "1.0.0",
    }
    await storage.upload_snapshot(b"placeholder", old_meta)

    # Hay un evento synced=1 (pasa condición 1) y uno en pending (pasaría condición 3)
    await db.execute(
        """INSERT INTO events_log
           (event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("20260605_100000_jamin_create_macroregion_1_ok",
         "macroregion", "create", "1", "2026-06-05T10:00:00-05:00",
         "jamin", "PC1", "1.0.0", "{}", 1),
    )
    await db.commit()
    await storage.upload_event({
        "event_id": "20260605_110000_jamin_update_macroregion_1_new",
        "entity": "macroregion", "action": "update", "entity_id": "1",
        "created_at": "2026-06-05T11:00:00-05:00", "created_by": "jamin",
        "machine": "PC1", "app_version": "1.0.0", "payload": {"nombre": "X"},
    })

    app = {
        "_snapshot_done": False,
        "db": db, "storage": storage,
        "config": {"app": {"version": "1.0.0", "user": ""}},
        "_db_path": db_path,
    }
    await _maybe_generate_snapshot(app)

    # Meta no debe haber cambiado (sigue siendo el "placeholder" de hoy)
    meta = await storage.download_snapshot_meta()
    assert meta["last_event_id"] == old_meta["last_event_id"]


@pytest.mark.asyncio
async def test_generation_aborts_if_no_newer_events(snap_env):
    """Condición 3: si no hay eventos en pending/ más nuevos que el snapshot, no genera."""
    db, storage, db_path, _ = snap_env

    event_id = "20260601_100000_jamin_create_macroregion_1_covered"
    await storage.upload_event({
        "event_id": event_id,
        "entity": "macroregion", "action": "create", "entity_id": "1",
        "created_at": "2026-06-01T10:00:00-05:00", "created_by": "jamin",
        "machine": "PC1", "app_version": "1.0.0", "payload": {"id": 1},
    })
    await storage.upload_snapshot(b"placeholder", {
        "last_event_id": event_id,        # snapshot YA cubre ese evento
        "generated_at":  "2026-06-01T10:00:00-05:00",  # no es hoy → pasa condición 2
        "generated_by":  "jamin", "machine": "PC1", "app_version": "1.0.0",
    })
    await db.execute(
        """INSERT INTO events_log
           (event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (event_id, "macroregion", "create", "1", "2026-06-01T10:00:00-05:00",
         "jamin", "PC1", "1.0.0", "{}", 1),
    )
    await db.commit()

    app = {
        "_snapshot_done": False,
        "db": db, "storage": storage,
        "config": {"app": {"version": "1.0.0", "user": ""}},
        "_db_path": db_path,
    }
    await _maybe_generate_snapshot(app)

    # El meta no debe haber cambiado (generated_at sigue siendo de 2026-06-01)
    meta = await storage.download_snapshot_meta()
    assert meta["generated_at"] == "2026-06-01T10:00:00-05:00"


@pytest.mark.asyncio
async def test_generation_runs_when_all_conditions_met(snap_env):
    """Cuando las tres condiciones se cumplen, se genera snapshot con meta coherente."""
    db, storage, db_path, _ = snap_env

    # Estado: macroregión + evento synced=1 en la DB
    await db.execute(
        "INSERT INTO macroregiones (id, nombre, activo, created_at, updated_at)"
        " VALUES (?,?,?,?,?)",
        (1, "NORTE", 1, "2026-06-01", "2026-06-01"),
    )
    event_id = "20260601_100000_jamin_create_macroregion_1_gen001"
    await db.execute(
        """INSERT INTO events_log
           (event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (event_id, "macroregion", "create", "1", "2026-06-01T10:00:00-05:00",
         "jamin", "PC1", "1.0.0", '{"id":1}', 1),
    )
    await db.commit()

    # Evento en pending (más nuevo que el último snapshot que no existe)
    await storage.upload_event({
        "event_id": event_id,
        "entity": "macroregion", "action": "create", "entity_id": "1",
        "created_at": "2026-06-01T10:00:00-05:00", "created_by": "jamin",
        "machine": "PC1", "app_version": "1.0.0", "payload": {"id": 1},
    })

    app = {
        "_snapshot_done": False,
        "db": db, "storage": storage,
        "config": {"app": {"version": "1.0.0", "user": "test_user"}},
        "_db_path": db_path,
    }
    await _maybe_generate_snapshot(app)

    # Se generó el snapshot
    meta = await storage.download_snapshot_meta()
    assert meta is not None
    assert meta["last_event_id"] == event_id
    assert meta["generated_by"] == "test_user"
    assert meta["app_version"] == "1.0.0"

    # El .db subido contiene el mismo last_event_id en events_log
    db_bytes = await storage.download_snapshot_db()
    assert db_bytes is not None
    verify_path = db_path.parent / "verify_gen.db"
    verify_path.write_bytes(db_bytes)
    conn = sqlite3.connect(str(verify_path))
    row = conn.execute("SELECT MAX(event_id) FROM events_log").fetchone()
    conn.close()
    assert row[0] == meta["last_event_id"]


# ── Helpers Fase 3 ────────────────────────────────────────────────────────────

def _insert_event_full(
    conn: sqlite3.Connection, event_id: str, *,
    entity: str = "macroregion", action: str = "create", entity_id: str = "1",
    payload_json: str = "{}", synced: int = 1,
) -> None:
    conn.execute(
        """INSERT INTO events_log
           (event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (event_id, entity, action, entity_id, "2026-06-01T10:00:00-05:00",
         "jamin", "PC1", "1.0.0", payload_json, synced),
    )


def _pending_ids(storage: LocalFolderBackend) -> set[str]:
    return {p.stem for p in (storage._base / "events_pending").glob("*.json")}


def _processed_ids(storage: LocalFolderBackend) -> set[str]:
    return {p.stem for p in (storage._base / "events_processed").glob("*.json")}


# ── PIEZA 1: Archivado a events_processed/ ────────────────────────────────────

E_A = "20260601_100000_jamin_create_macroregion_1_aaa"   # <= snap_last, en log
E_B = "20260602_100000_jamin_create_macroregion_2_bbb"   # == snap_last, en log
E_OTHER = "20260601_120000_jamin_create_macroregion_3_ccc"  # <= snap_last, NO en log
E_NEWER = "20260603_100000_jamin_create_macroregion_4_ddd"  # > snap_last


@pytest.mark.asyncio
async def test_archive_only_eligible_and_in_log(snap_env):
    """Archiva id <= snap_last AND ∈ events_log; deja > snap_last y los ajenos."""
    db, storage, db_path, _ = snap_env
    # events_log local (conexión aiosqlite viva, la que lee _archive_processed_events)
    for eid, ent in ((E_A, "1"), (E_B, "2")):
        await db.execute(
            """INSERT INTO events_log (event_id, entity, action, entity_id,
               created_at, created_by, machine, app_version, payload_json, synced)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (eid, "macroregion", "create", ent, "2026-06-01T10:00:00-05:00",
             "jamin", "PC1", "1.0.0", "{}", 1),
        )
    await db.commit()

    # pending tiene los 4
    for eid in (E_A, E_B, E_OTHER, E_NEWER):
        await storage.upload_event({"event_id": eid, "entity": "macroregion",
            "action": "create", "entity_id": "1", "created_at": "x",
            "created_by": "j", "machine": "m", "app_version": "1.0.0",
            "payload": {"id": 1}})

    await _archive_processed_events(db, storage, snap_last=E_B)

    assert _pending_ids(storage) == {E_OTHER, E_NEWER}   # ajeno y posterior quedan
    assert _processed_ids(storage) == {E_A, E_B}         # elegibles archivados


@pytest.mark.asyncio
async def test_archive_idempotent_and_crash_safe(snap_env):
    """archive_processed: re-corrida = no-op; crash copy-sin-unlink se completa."""
    _, storage, _, _ = snap_env
    ev = {"event_id": E_A, "entity": "macroregion", "action": "create",
          "entity_id": "1", "created_at": "x", "created_by": "j",
          "machine": "m", "app_version": "1.0.0", "payload": {"id": 1}}
    await storage.upload_event(ev)

    await storage.archive_processed(E_A)
    assert _pending_ids(storage) == set()
    assert _processed_ids(storage) == {E_A}

    # Re-corrida: pending ya no lo tiene → no-op sin error
    await storage.archive_processed(E_A)
    assert _processed_ids(storage) == {E_A}

    # Simular crash entre copy y unlink: archivo presente en AMBOS lados
    (storage._base / "events_pending" / f"{E_A}.json").write_text("stale", encoding="utf-8")
    await storage.archive_processed(E_A)                 # re-copia (overwrite) + re-borra
    assert _pending_ids(storage) == set()
    assert _processed_ids(storage) == {E_A}


@pytest.mark.asyncio
async def test_archive_not_called_when_generation_aborts(snap_env):
    """Si la generación aborta (synced=0), no se archiva nada (processed vacío)."""
    db, storage, db_path, _ = snap_env
    await db.execute(                                    # synced=0 → aborta condición 1
        """INSERT INTO events_log (event_id, entity, action, entity_id, created_at,
           created_by, machine, app_version, payload_json, synced)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (E_A, "macroregion", "create", "1", "2026-06-01T10:00:00-05:00",
         "jamin", "PC1", "1.0.0", "{}", 0),
    )
    await db.commit()
    await storage.upload_event({"event_id": E_A, "entity": "macroregion",
        "action": "create", "entity_id": "1", "created_at": "x", "created_by": "j",
        "machine": "m", "app_version": "1.0.0", "payload": {"id": 1}})

    app = {"_snapshot_done": False, "db": db, "storage": storage,
           "config": {"app": {"version": "1.0.0", "user": ""}}, "_db_path": db_path}
    await _maybe_generate_snapshot(app)

    assert await storage.download_snapshot_meta() is None   # no generó
    assert _processed_ids(storage) == set()                 # no archivó
    assert _pending_ids(storage) == {E_A}                   # pending intacto


# ── PIEZA 2: Recuperación de máquina atrasada ─────────────────────────────────

async def _build_snapshot(storage, tmp_path, snap_last: str) -> tuple[str, bytes]:
    """Crea una db fuente con macroregión 1 + evento snap_last y sube el snapshot."""
    src_path = tmp_path / "source.db"
    _create_db(src_path)
    conn = sqlite3.connect(src_path)
    conn.execute("INSERT INTO macroregiones (id, nombre, activo, created_at, updated_at)"
                 " VALUES (1,'NORTE',1,'2026-06-01','2026-06-01')")
    _insert_event_full(conn, snap_last, payload_json='{"id":1,"nombre":"NORTE"}')
    conn.commit()
    conn.close()
    last_event_id, db_bytes = _backup_and_read_last_event_id(src_path)
    meta = {"last_event_id": last_event_id, "generated_at": "2026-06-05T10:00:00-05:00",
            "generated_by": "jamin", "machine": "PC1", "app_version": "1.0.0"}
    await storage.upload_snapshot(db_bytes, meta)
    return last_event_id, db_bytes


@pytest.mark.asyncio
async def test_recover_behind_replaces_db_atomically(snap_env):
    """Máquina atrasada: cache reemplazado por el snapshot; -wal/-shm borrados."""
    _, storage, _, tmp_path = snap_env
    snap_last = "20260605_100000_jamin_create_macroregion_1_snap"
    _, snap_bytes = await _build_snapshot(storage, tmp_path, snap_last)

    # cache local atrasado: solo un evento viejo (local_max < snap_last)
    cache = tmp_path / "cache.db"
    _create_db(cache)
    conn = sqlite3.connect(cache)
    _insert_event_full(conn, "20260601_090000_jamin_create_macroregion_1_old")
    conn.commit()
    conn.close()
    # sidecars WAL que deben quedar borrados
    (tmp_path / "cache.db-wal").write_bytes(b"stale-wal")
    (tmp_path / "cache.db-shm").write_bytes(b"stale-shm")

    result = await recover_state(storage, db_path=cache)

    assert result is not None
    assert result.meta["last_event_id"] == snap_last
    assert cache.read_bytes() == snap_bytes               # reemplazo por el snapshot
    assert not (tmp_path / "cache.db-wal").exists()       # sidecars viejos borrados
    assert not (tmp_path / "cache.db-shm").exists()
    assert not (tmp_path / "cache.db.tmp").exists()       # temp limpiado por os.replace


@pytest.mark.asyncio
async def test_recover_behind_preserves_local_unsynced(snap_env):
    """Regresión mismatch #1: synced=0 con id <= snap_last sobrevive al re-bootstrap."""
    _, storage, _, tmp_path = snap_env
    snap_last = "20260605_100000_jamin_create_macroregion_1_snap"
    await _build_snapshot(storage, tmp_path, snap_last)

    # cache atrasado con un synced=0 local (id <= snap_last) que crea macroregión 2
    e_local = "20260603_120000_jamin_create_macroregion_2_local"
    cache = tmp_path / "cache.db"
    _create_db(cache)
    conn = sqlite3.connect(cache)
    _insert_event_full(conn, "20260601_090000_jamin_create_macroregion_1_old")
    _insert_event_full(
        conn, e_local, entity_id="2", synced=0,
        payload_json='{"id":2,"nombre":"SUR","activo":1,'
                     '"created_at":"2026-06-03","updated_at":"2026-06-03"}',
    )
    conn.commit()
    conn.close()

    result = await recover_state(storage, db_path=cache)
    assert result is not None
    assert [e["event_id"] for e in result.local_events] == [e_local]
    assert e_local in _pending_ids(storage)               # subido ANTES de reemplazar

    # aplicar tras init_db simulado
    new_db = await _open_db(cache)
    await apply_post_snapshot_events(new_db, storage, result.meta, result.local_events)
    async with new_db.execute("SELECT nombre FROM macroregiones ORDER BY id") as cur:
        nombres = [r["nombre"] for r in await cur.fetchall()]
    async with new_db.execute(
        "SELECT 1 FROM events_log WHERE event_id=?", (e_local,)
    ) as cur:
        in_log = await cur.fetchone()
    await new_db.close()

    assert nombres == ["NORTE", "SUR"]                    # snapshot + evento local
    assert in_log is not None                             # registrado en events_log


@pytest.mark.asyncio
async def test_recover_not_behind_returns_none(snap_env):
    """Si local_max >= snap_last, no recupera y no toca el cache."""
    _, storage, _, tmp_path = snap_env
    snap_last = "20260605_100000_jamin_create_macroregion_1_snap"
    await _build_snapshot(storage, tmp_path, snap_last)

    cache = tmp_path / "cache.db"
    _create_db(cache)
    conn = sqlite3.connect(cache)
    _insert_event_full(conn, "20260606_100000_jamin_create_macroregion_9_ahead")  # > snap_last
    conn.commit()
    conn.close()
    before = cache.read_bytes()

    result = await recover_state(storage, db_path=cache)

    assert result is None
    assert cache.read_bytes() == before                   # cache intacto


@pytest.mark.asyncio
async def test_recover_new_machine_zero_byte(snap_env):
    """cache.db de 0 bytes → delega en bootstrap y devuelve RecoveryResult."""
    _, storage, _, tmp_path = snap_env
    snap_last = "20260605_100000_jamin_create_macroregion_1_snap"
    await _build_snapshot(storage, tmp_path, snap_last)

    cache = tmp_path / "new_cache.db"
    cache.write_bytes(b"")
    result = await recover_state(storage, db_path=cache)

    assert result is not None
    assert result.meta["last_event_id"] == snap_last
    assert result.local_events == []
    assert cache.stat().st_size > 0


@pytest.mark.asyncio
async def test_recover_aborts_if_unsynced_upload_fails(snap_env):
    """Si falla subir un synced=0, aborta sin tocar el cache (no perder cambios)."""
    _, storage, _, tmp_path = snap_env
    snap_last = "20260605_100000_jamin_create_macroregion_1_snap"
    await _build_snapshot(storage, tmp_path, snap_last)

    class FailingUpload(LocalFolderBackend):
        async def upload_event(self, event):
            raise RuntimeError("storage caído")

    failing = FailingUpload(str(tmp_path / "storage"))    # mismo dir que snap_env

    cache = tmp_path / "cache.db"
    _create_db(cache)
    conn = sqlite3.connect(cache)
    _insert_event_full(conn, "20260601_090000_jamin_create_macroregion_1_old")
    _insert_event_full(conn, "20260603_120000_jamin_create_macroregion_2_local", synced=0)
    conn.commit()
    conn.close()
    before = cache.read_bytes()

    result = await recover_state(failing, db_path=cache)

    assert result is None
    assert cache.read_bytes() == before                   # cache intacto
