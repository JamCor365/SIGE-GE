"""
Tests RamaE Fase 4b — Hardening de orden: update/delete que llega antes que su create.

El motor aplica los eventos de events_pending/ en orden lexicográfico (≡ cronológico),
así que dentro de un mismo sync el create se procesa antes que su update. El hueco se
abre ENTRE syncs: el create queda synced=0 en otra máquina (aún no en events_pending/)
mientras un update posterior sí subió y una tercera máquina sincroniza en esa ventana.

Sin el fix, el update afectaba 0 filas pero igual se registraba en events_log con
synced=1 → quedaba "consumido"; cuando el create llegaba, INSERT OR IGNORE metía la
versión vieja y el update no se re-aplicaba → cambio perdido en silencio.

Fix (Opción 1): _apply_one devuelve filas afectadas; si un update/delete afecta 0
filas, NO se registra → permanece en events_pending/, se re-descubre y re-aplica en
el próximo sync una vez presente el create.
"""
import sqlite3
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from backend.snapshot import apply_post_snapshot_events
from backend.storage.local_folder import LocalFolderBackend
from backend.sync_engine import apply_remote_events


def _create_db(db_path: Path) -> None:
    schema = (Path(__file__).parent.parent / "docs" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events_log (
            event_id TEXT PRIMARY KEY, entity TEXT NOT NULL, action TEXT NOT NULL,
            entity_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            machine TEXT NOT NULL, app_version TEXT NOT NULL, payload_json TEXT NOT NULL,
            synced INTEGER NOT NULL DEFAULT 0, synced_at TEXT, error_msg TEXT
        )
        """
    )
    conn.commit()
    conn.close()


@pytest_asyncio.fixture
async def env(tmp_path):
    db_path = tmp_path / "cache.db"
    _create_db(db_path)
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = aiosqlite.Row
    storage = LocalFolderBackend(str(tmp_path / "storage"))
    yield db, storage
    await db.close()


def _event(event_id: str, action: str, entity_id, payload: dict) -> dict:
    return {
        "event_id": event_id,
        "entity": "macroregion",
        "action": action,
        "entity_id": str(entity_id),
        "created_at": "2026-01-01T10:00:00-05:00",
        "created_by": "jamin",
        "machine": "PC1",
        "app_version": "1.0.0",
        "payload": payload,
    }


def _create_payload(entity_id, nombre) -> dict:
    return {
        "id": entity_id, "nombre": nombre, "activo": 1,
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
    }


async def _count(db, sql, params=()) -> int:
    async with db.execute(sql, params) as cur:
        return (await cur.fetchone())[0]


# ── El bug central: update antes que su create no se pierde ───────────────────

@pytest.mark.asyncio
async def test_update_before_create_is_deferred_then_applied(env):
    """Un update cuyo create aún no llegó se difiere y se re-aplica al llegar el create."""
    db, storage = env
    create_id = "20260101_100000_jamin_create_macroregion_5_aaa111"
    update_id = "20260101_110000_jamin_update_macroregion_5_bbb222"  # posterior

    # Solo el update está en pending (el create sigue synced=0 en otra máquina).
    await storage.upload_event(_event(update_id, "update", 5, {"nombre": "ACTUALIZADO"}))

    r1 = await apply_remote_events(db, storage)
    assert r1["deferred"] == 1
    assert r1["applied"] == 0
    assert await _count(db, "SELECT COUNT(*) FROM events_log") == 0          # no consumido
    assert await _count(db, "SELECT COUNT(*) FROM macroregiones WHERE id=5") == 0

    # Ahora llega el create.
    await storage.upload_event(_event(create_id, "create", 5, _create_payload(5, "ORIGINAL")))

    r2 = await apply_remote_events(db, storage)
    assert r2["applied"] == 2   # create + update re-descubierto
    assert r2["deferred"] == 0

    async with db.execute("SELECT nombre FROM macroregiones WHERE id=5") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["nombre"] == "ACTUALIZADO"   # el cambio NO se perdió


@pytest.mark.asyncio
async def test_delete_before_create_is_deferred_then_applied(env):
    """Un delete cuyo create aún no llegó se difiere y se aplica tras el create."""
    db, storage = env
    create_id = "20260101_100000_jamin_create_macroregion_7_aaa"
    delete_id = "20260101_120000_jamin_delete_macroregion_7_bbb"

    await storage.upload_event(_event(delete_id, "delete", 7, {}))
    r1 = await apply_remote_events(db, storage)
    assert r1["deferred"] == 1 and r1["applied"] == 0
    assert await _count(db, "SELECT COUNT(*) FROM events_log") == 0

    await storage.upload_event(_event(create_id, "create", 7, _create_payload(7, "X")))
    r2 = await apply_remote_events(db, storage)
    assert r2["applied"] == 2

    async with db.execute("SELECT activo FROM macroregiones WHERE id=7") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["activo"] == 0   # el delete se aplicó tras crear la fila


# ── Regresión: el caso normal sigue funcionando ───────────────────────────────

@pytest.mark.asyncio
async def test_create_then_update_same_sync_applies_in_order(env):
    """create + update presentes en el mismo sync se aplican en orden (sin deferral)."""
    db, storage = env
    await storage.upload_event(
        _event("20260101_100000_jamin_create_macroregion_9_aaa", "create", 9, _create_payload(9, "ORIG"))
    )
    await storage.upload_event(
        _event("20260101_110000_jamin_update_macroregion_9_bbb", "update", 9, {"nombre": "NUEVO"})
    )
    r = await apply_remote_events(db, storage)
    assert r["applied"] == 2
    assert r["deferred"] == 0

    async with db.execute("SELECT nombre FROM macroregiones WHERE id=9") as cur:
        assert (await cur.fetchone())["nombre"] == "NUEVO"


@pytest.mark.asyncio
async def test_update_with_no_valid_columns_is_not_deferred(env):
    """Un update cuyo payload no tiene columnas válidas es no-op: se registra, no se difiere.

    Bloquea el bucle infinito: sin la distinción None-vs-0, un update sin columnas
    devolvería 0 filas y se diferiría para siempre.
    """
    db, storage = env
    upd_id = "20260101_100000_jamin_update_macroregion_3_aaa"
    await storage.upload_event(_event(upd_id, "update", 3, {"columna_inexistente": 1}))

    r = await apply_remote_events(db, storage)
    assert r["deferred"] == 0
    assert r["applied"] == 1   # nada que aplicar, pero se consume (no re-descubrir)
    assert await _count(db, "SELECT COUNT(*) FROM events_log WHERE event_id=?", (upd_id,)) == 1


# ── El fix aplica también en la aplicación post-snapshot (mismo _apply_one) ────

@pytest.mark.asyncio
async def test_post_snapshot_defers_update_without_create(env):
    """apply_post_snapshot_events también difiere un update sin su create."""
    db, storage = env
    update_id = "20260101_110000_jamin_update_macroregion_8_bbb"
    await storage.upload_event(_event(update_id, "update", 8, {"nombre": "Z"}))

    meta = {"last_event_id": "20260101_090000_jamin_create_macroregion_1_base"}
    await apply_post_snapshot_events(db, storage, meta)

    assert await _count(db, "SELECT COUNT(*) FROM events_log WHERE event_id=?", (update_id,)) == 0
    assert await _count(db, "SELECT COUNT(*) FROM macroregiones WHERE id=8") == 0
