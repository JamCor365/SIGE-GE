import logging
from pathlib import Path

import aiosqlite
from aiohttp import web

log = logging.getLogger("sige.db")
DB_PATH = Path("data/cache.db")


# Columnas de cáscara añadidas en el rediseño general (post commit 7630ea1).
# CREATE TABLE IF NOT EXISTS no altera tablas ya existentes, así que las cache.db
# anteriores se migran aquí con ALTER TABLE ADD COLUMN (idempotente).
_CONTRATOS_NEW_COLUMNS = {
    "entidad_contratante": "TEXT",
    "procedimiento_seleccion": (
        "TEXT CHECK (procedimiento_seleccion IN ("
        "'LICITACION_PUBLICA','CONCURSO_PUBLICO','ADJUDICACION_SIMPLIFICADA',"
        "'SUBASTA_INVERSA_ELECTRONICA','SELECCION_CONSULTORES_INDIVIDUALES',"
        "'COMPARACION_PRECIOS','CONTRATACION_DIRECTA','CATALOGO_ELECTRONICO_AM'"
        ") OR procedimiento_seleccion IS NULL)"
    ),
    "tipos_objeto": "TEXT",
    "moneda": "TEXT NOT NULL DEFAULT 'PEN'",
    "monto_principal": "INTEGER",
    "monto_accesorio": "INTEGER",
}


async def _contratos_columns(db: aiosqlite.Connection) -> set[str]:
    async with db.execute("PRAGMA table_info(contratos)") as cur:
        return {row[1] for row in await cur.fetchall()}


async def _migrate_contratos(db: aiosqlite.Connection) -> None:
    """Lleva una tabla `contratos` heredada al esquema de cáscara general.

    En una DB nueva (CREATE TABLE ya trae el esquema final) todo es no-op.
    En una DB previa: añade columnas nuevas, backfillea tipo_objeto→tipos_objeto
    y elimina tipo_objeto y ambito (el alcance se deriva de contrato_ge).
    DROP COLUMN exige SQLite >= 3.35 (verificado: entorno usa 3.45).
    """
    cols = await _contratos_columns(db)
    for name, decl in _CONTRATOS_NEW_COLUMNS.items():
        if name not in cols:
            await db.execute(f"ALTER TABLE contratos ADD COLUMN {name} {decl}")

    cols = await _contratos_columns(db)
    if "tipo_objeto" in cols and "tipos_objeto" in cols:
        # El enum combinado se desdobla en el array multivalor canónico
        # (mismo formato que json.dumps produce en el backend).
        await db.execute(
            """
            UPDATE contratos SET tipos_objeto = CASE tipo_objeto
                WHEN 'ADQUISICION_INSTALACION' THEN '["ADQUISICION", "INSTALACION"]'
                WHEN 'ADQUISICION'             THEN '["ADQUISICION"]'
                WHEN 'MANTENIMIENTO'           THEN '["MANTENIMIENTO"]'
                ELSE tipos_objeto END
            WHERE tipos_objeto IS NULL AND tipo_objeto IS NOT NULL
            """
        )
        await db.execute("ALTER TABLE contratos DROP COLUMN tipo_objeto")
    if "ambito" in cols:
        await db.execute("ALTER TABLE contratos DROP COLUMN ambito")


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
    #
    # Cáscara general (estructura común que la Ley de Contrataciones del Estado
    # impone a todo contrato). Campos "caché/denormalizados" hoy autoritativos que
    # pasan a DERIVADOS cuando lleguen sus entidades hijas (patrón denorm→derivado):
    #   - proveedor        → derivado de items_contrato (fase futura)
    #   - tipos_objeto     → derivado de prestaciones.tipo_objeto (fase futura)
    #   - monto_principal/_accesorio → derivados de prestaciones (fase futura)
    # El alcance geográfico NO se modela aquí: se DERIVA de contrato_ge (GE→sede→macro).
    #
    # MONTOS EN CÉNTIMOS: monto_principal y monto_accesorio son enteros de céntimos
    # (S/ 11'280,000.00 → 1128000000). Entero, no REAL: el cálculo de penalidades y
    # los totales deben cuadrar al céntimo con el banco, sin error de punto flotante.
    # Se divide entre 100 solo al mostrar.
    #
    # tipos_objeto: array JSON de tokens atómicos de vocabulario cerrado
    # (p.ej. ["ADQUISICION","INSTALACION","MANTENIMIENTO"]). Multivalor — un contrato
    # puede ser varios tipos a la vez. Sin CHECK SQL (no valida pertenencia de un set
    # multivalor); la validación del vocabulario va en backend/routes/contratos.py.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS contratos (
            id                      TEXT    PRIMARY KEY,           -- uuid4().hex
            numero                  TEXT    UNIQUE,                -- nº de proceso/contrato (nullable, editable)
            objeto                  TEXT    NOT NULL,
            entidad_contratante     TEXT,                          -- entidad del Estado que contrata
            procedimiento_seleccion TEXT    CHECK (procedimiento_seleccion IN ('LICITACION_PUBLICA','CONCURSO_PUBLICO','ADJUDICACION_SIMPLIFICADA','SUBASTA_INVERSA_ELECTRONICA','SELECCION_CONSULTORES_INDIVIDUALES','COMPARACION_PRECIOS','CONTRATACION_DIRECTA','CATALOGO_ELECTRONICO_AM') OR procedimiento_seleccion IS NULL),
            tipos_objeto            TEXT,                          -- array JSON de tokens (validado en Python)
            proveedor               TEXT,                          -- caché adjudicatario único/principal; futuro derivado de items_contrato
            moneda                  TEXT    NOT NULL DEFAULT 'PEN',
            monto_principal         INTEGER,                       -- céntimos (S/ x 100)
            monto_accesorio         INTEGER,                       -- céntimos (S/ x 100)
            fecha_inicio            TEXT,
            fecha_fin               TEXT,
            estado                  TEXT    CHECK (estado IN ('VIGENTE','CULMINADO','RESUELTO') OR estado IS NULL),
            observaciones           TEXT,
            activo                  INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at              TEXT    NOT NULL,
            updated_at              TEXT    NOT NULL
        )
        """
    )
    await _migrate_contratos(db)
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

    # Puente N:M contrato ↔ grupo electrógeno. El alcance geográfico de un
    # contrato (macrorregiones/agencias) se DERIVA de estos vínculos vía
    # ge → sede → macrorregión; no se almacena como campo.
    #
    # IDENTIDAD DE FILA: el motor de sync (_apply_one) usa una columna `id`
    # escalar para update/delete (WHERE id = ?). Una PK compuesta (contrato_id,
    # ge_id) no tendría columna `id` y rompería el motor. Solución: `id` es un
    # texto DETERMINISTA del par ("{contrato_id}_{ge_id}"), de un solo campo, que
    # el motor ya sabe leer SIN modificarlo. Determinista (no UUID aleatorio) para
    # que dos máquinas offline que vinculen el mismo par generen el MISMO id y un
    # desvínculo converja en todas. El par real se garantiza con UNIQUE.
    # Borrado lógico (activo) → el delete del motor hace SET activo = 0 WHERE id.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS contrato_ge (
            id           TEXT    PRIMARY KEY,            -- determinista: contrato_id || '_' || ge_id
            contrato_id  TEXT    NOT NULL REFERENCES contratos(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            ge_id        INTEGER NOT NULL REFERENCES grupos_electrogenos(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            item_id      TEXT,                           -- futuro items_contrato; por ahora siempre NULL
            activo       INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL,
            UNIQUE (contrato_id, ge_id)
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_contrato_ge_contrato ON contrato_ge(contrato_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_contrato_ge_ge ON contrato_ge(ge_id)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_contrato_ge_updated
        AFTER UPDATE ON contrato_ge
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE contrato_ge SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )
    await db.commit()

    app["db"] = db
    log.info("DB conectada: %s", db_path)


async def close_db(app: web.Application) -> None:
    await app["db"].close()
    log.info("DB cerrada")
