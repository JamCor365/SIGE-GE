import logging
from pathlib import Path

import aiosqlite
from aiohttp import web

log = logging.getLogger("sige.db")
DB_PATH = Path("data/cache.db")


async def init_db(app: web.Application, db_path: Path | None = None) -> None:
    if db_path is None:
        db_path = DB_PATH
    if db_path == DB_PATH and not db_path.exists():
        raise FileNotFoundError(f"Base de datos no encontrada: {db_path}")

    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode = WAL")
    db.row_factory = aiosqlite.Row

    # Apply full schema when starting from an empty DB (tests or first install)
    async with db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='macroregiones'"
    ) as cur:
        row = await cur.fetchone()
        if row[0] == 0:
            schema = Path("docs/schema.sql").read_text(encoding="utf-8")
            await db.executescript(schema)

    # Re-set connection-scoped PRAGMAs (executescript issues an implicit COMMIT)
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA journal_mode = WAL")

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS events_log (
            event_id     TEXT PRIMARY KEY,
            entity       TEXT NOT NULL,
            action       TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
            entity_id    TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            created_by   TEXT NOT NULL,
            machine      TEXT NOT NULL,
            app_version  TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            synced       INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0, 1)),
            synced_at    TEXT,
            error_msg    TEXT
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_log_synced ON events_log(synced)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_log_created_at ON events_log(created_at)"
    )

    # Contratos: entidad creada de forma distribuida → PK es un UUID opaco
    # (generado en el backend al crear), no un id natural ni autoincrement.
    # Así dos máquinas offline nunca colisionan ids de fila al sincronizar.
    # numero es el identificador humano: UNIQUE pero NULLABLE (permite registrar
    # un contrato aún sin número; en SQLite varios NULL no violan UNIQUE) y editable.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS contratos (
            id            TEXT    PRIMARY KEY,           -- uuid4().hex
            numero        TEXT    UNIQUE,                -- nº de proceso/contrato (nullable, editable)
            objeto        TEXT    NOT NULL,
            proveedor     TEXT,
            ambito        TEXT,
            tipo_objeto   TEXT    CHECK (tipo_objeto IN ('MANTENIMIENTO','ADQUISICION','ADQUISICION_INSTALACION') OR tipo_objeto IS NULL),
            fecha_inicio  TEXT,
            fecha_fin     TEXT,
            estado        TEXT    CHECK (estado IN ('VIGENTE','CULMINADO','RESUELTO') OR estado IS NULL),
            observaciones TEXT,
            activo        INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_contratos_estado ON contratos(estado)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_contratos_updated
        AFTER UPDATE ON contratos
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE contratos SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )
    await db.commit()

    app["db"] = db
    log.info("DB conectada: %s", db_path)


async def close_db(app: web.Application) -> None:
    await app["db"].close()
    log.info("DB cerrada")
