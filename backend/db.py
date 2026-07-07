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


# Geolocalización de sedes (PASO 1 del mapa). CREATE TABLE del schema no altera
# tablas existentes → las cache.db previas se migran aquí con ALTER TABLE ADD
# COLUMN (idempotente). geo_fuente registra la procedencia de la coordenada para
# la autoría final: 'manual' (corregida a mano) manda y nunca se re-geocodifica.
_SEDES_NEW_COLUMNS = {
    "latitud": "REAL",
    "longitud": "REAL",
    "geo_fuente": (
        "TEXT CHECK (geo_fuente IN "
        "('distrito_centroide','nominatim','manual') OR geo_fuente IS NULL)"
    ),
}

# Vista canónica de sedes. Se mantiene idéntica a la de docs/schema.sql; se
# recrea en la migración para exponer las columnas geo en DBs heredadas.
_V_SEDES_COMPLETO = """
CREATE VIEW v_sedes_completo AS
SELECT s.id, s.codigo, s.nombre_agencia, s.categoria,
       s.direccion, s.departamento, s.provincia, s.distrito,
       s.latitud, s.longitud, s.geo_fuente,
       m.id   AS macroregion_id,
       m.nombre AS macroregion_nombre,
       s.activo, s.created_at, s.updated_at
FROM sedes s
JOIN macroregiones m ON m.id = s.macroregion_id
"""


async def _migrate_sedes(db: aiosqlite.Connection) -> None:
    """Añade lat/long/geo_fuente a una tabla `sedes` heredada y actualiza su vista.

    En una DB nueva (schema.sql ya trae las columnas y la vista finales) todo es
    no-op salvo el DROP/CREATE de la vista, que la deja idéntica.
    """
    async with db.execute("PRAGMA table_info(sedes)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    for name, decl in _SEDES_NEW_COLUMNS.items():
        if name not in cols:
            await db.execute(f"ALTER TABLE sedes ADD COLUMN {name} {decl}")
    # La vista se recrea siempre para reflejar las columnas geo (una vista vieja
    # sin lat/long dejaría al frontend sin coordenadas).
    await db.execute("DROP VIEW IF EXISTS v_sedes_completo")
    await db.execute(_V_SEDES_COMPLETO)


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

    # Geolocalización de sedes (columnas + vista); idempotente.
    await _migrate_sedes(db)

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

    # Proveedores: registro reutilizable de adjudicatarios (empresa o consorcio),
    # prerequisito de items_contrato (futuro: items_contrato.proveedor_id → id).
    #
    # PK = UUID surrogate (como contratos): entidad creada de forma distribuida; el
    # RUC NO sirve de PK porque es editable (typos), puede faltar al crear, y un
    # CONSORCIO no tiene RUC propio (factura con el de un miembro), así que el RUC
    # no es único entre filas.
    #
    # `ruc` indexado pero NO único a propósito. El motor aplica remotos con
    # INSERT OR IGNORE; un UNIQUE(ruc) descartaría en silencio una de dos filas
    # creadas offline con UUIDs distintos, y como items_contrato hará FK a este id,
    # quedarían FKs colgando. PRINCIPIO DEL PROYECTO: la única unicidad segura en
    # este sync es la PK `id`; toda clave natural única adicional debe ser blanda
    # (capa de app), salvo que nada le haga FK. Dedup de RUC = aviso en la UI.
    #
    # Consorcio: 1 fila tipo=CONSORCIO; miembros/porcentajes en `observaciones`
    # (excepción libre) hasta que exista la tabla hija proveedor_miembros.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS proveedores (
            id            TEXT    PRIMARY KEY,           -- uuid4().hex
            ruc           TEXT,                          -- 11 dígitos; nullable/editable; en consorcio = RUC del miembro facturador
            razon_social  TEXT    NOT NULL,
            tipo          TEXT    CHECK (tipo IN ('PERSONA_JURIDICA','PERSONA_NATURAL','CONSORCIO') OR tipo IS NULL),
            observaciones TEXT,
            activo        INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        )
        """
    )
    # Índice de búsqueda por RUC — NO único (ver comentario de la tabla).
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_proveedores_ruc ON proveedores(ruc)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_proveedores_updated
        AFTER UPDATE ON proveedores
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE proveedores SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )

    # Ítems del contrato: un contrato puede dividirse en ítems adjudicados a
    # proveedores distintos (Concurso 002). Adjudicatario único (Valtom) = 1 ítem.
    #
    # IDENTIDAD: la identidad de un ítem ES el par (contrato, numero_item), igual
    # que contrato_ge. Y los ítems SON target de FK (contrato_ge.item_id, y a
    # futuro servicios/penalidades). Por el principio de unicidad segura, un UUID
    # reintroduciría el descarte silencioso (UNIQUE) → FKs colgando. Solución:
    # `id` DETERMINISTA del par ("{contrato_id}_{numero_item}"), escalar, que el
    # motor lee sin tocarse y converge entre máquinas. numero_item es INMUTABLE
    # (define el id); corregirlo = delete + recreate. UNIQUE(par) = guard redundante.
    #
    # NO hay columna `ambito`: el alcance del ítem se DERIVA de contrato_ge con
    # item_id = este ítem → sede → macrorregión (mismo principio que en contratos).
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS items_contrato (
            id            TEXT    PRIMARY KEY,           -- determinista: contrato_id || '_' || numero_item
            contrato_id   TEXT    NOT NULL REFERENCES contratos(id)   ON UPDATE CASCADE ON DELETE RESTRICT,
            numero_item   INTEGER NOT NULL,              -- inmutable (define el id); nº de ítem de las bases
            proveedor_id  TEXT    REFERENCES proveedores(id) ON UPDATE CASCADE ON DELETE RESTRICT,  -- nullable: sin adjudicar / desierto
            descripcion   TEXT,
            monto         INTEGER,                       -- céntimos (S/ x 100)
            moneda        TEXT    NOT NULL DEFAULT 'PEN',
            estado        TEXT    CHECK (estado IN ('EN_EVALUACION','ADJUDICADO','DESIERTO') OR estado IS NULL),
            observaciones TEXT,
            activo        INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL,
            UNIQUE (contrato_id, numero_item)
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_contrato_contrato ON items_contrato(contrato_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_contrato_proveedor ON items_contrato(proveedor_id)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_items_contrato_updated
        AFTER UPDATE ON items_contrato
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE items_contrato SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )

    # Prestaciones: descomposición del objeto en PRINCIPAL (única) + ACCESORIAS
    # (0..N), cada una con su monto y sus tipos de objeto. PRINCIPAL/ACCESORIA es
    # jerarquía contractual, NO tipo de actividad: un contrato "solo mantenimiento"
    # tiene el mantenimiento como su PRINCIPAL.
    #
    # IDENTIDAD: UUID surrogate (NO determinista). numero_prestacion es invención
    # nuestra (la ley no numera prestaciones), así que NO converge entre máquinas:
    # un id determinista o un UNIQUE(contrato_id,numero_prestacion) haría que el
    # INSERT OR IGNORE del motor descarte en silencio una de dos filas distintas
    # con el mismo número → pérdida de datos, y como servicios hará FK a
    # prestaciones.id, dejaría FKs colgando. Con UUID ambas filas sobreviven; el
    # dedup lógico lo resuelve un humano (igual que proveedores). numero_prestacion
    # es solo un ordinal de display, NO identidad. "Una sola PRINCIPAL" = regla
    # BLANDA en la ruta, no UNIQUE de BD.
    #
    # item_id: referencia BLANDA a items_contrato.id (patrón contrato_ge).
    # NULL = descomposición a nivel contrato (Valtom); seteado = a nivel ítem.
    # tipos_objeto: array JSON multivalor (mismo manejo que contratos).
    # Derivación de monto_principal/accesorio/tipos_objeto del contrato = read-time
    # futura; el caché del contrato queda intacto.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS prestaciones (
            id                TEXT    PRIMARY KEY,           -- uuid4().hex
            contrato_id       TEXT    NOT NULL REFERENCES contratos(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            item_id           TEXT,                          -- nullable; ref BLANDA a items_contrato.id (NULL = nivel contrato)
            numero_prestacion INTEGER,                       -- ordinal de display; NO identidad (nullable)
            clase             TEXT    NOT NULL CHECK (clase IN ('PRINCIPAL','ACCESORIA')),
            tipos_objeto      TEXT,                          -- array JSON de tokens (validado en Python)
            descripcion       TEXT,
            monto             INTEGER,                       -- céntimos (S/ x 100)
            moneda            TEXT    NOT NULL DEFAULT 'PEN',
            plazo_dias        INTEGER,                       -- plazo de ejecución en días; nullable
            observaciones     TEXT,
            activo            INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at        TEXT    NOT NULL,
            updated_at        TEXT    NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_prestaciones_contrato ON prestaciones(contrato_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_prestaciones_item ON prestaciones(item_id)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_prestaciones_updated
        AFTER UPDATE ON prestaciones
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE prestaciones SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )

    # Garantías: cartas fianza / seguros que el contratista entrega al banco
    # (fiel cumplimiento, adelantos, monto diferencial).
    #
    # IDENTIDAD: UUID surrogate (como proveedores). numero_carta_fianza es externo
    # del emisor pero editable (typos, renovaciones) y puede faltar al registrar,
    # así que NO sirve de PK. Va INDEXADO pero NO único: un UNIQUE + INSERT OR
    # IGNORE descartaría en silencio una garantía registrada en otra máquina;
    # perder una garantía es peor que un duplicado deduplicable por un humano
    # (soft dedup, igual que proveedores.ruc).
    #
    # La distinción fiel cumplimiento principal vs accesoria NO va en `tipo`: se
    # deriva de prestacion_id → prestaciones.clase. prestacion_id e item_id son
    # refs BLANDAS (validadas en ruta), nullable (patrón contrato_ge).
    # estado NO incluye VENCIDA: se deriva de fecha_vencimiento < hoy (read-time,
    # no almacenado); idx_garantias_vencimiento soporta esa consulta futura.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS garantias (
            id                  TEXT    PRIMARY KEY,           -- uuid4().hex
            contrato_id         TEXT    NOT NULL REFERENCES contratos(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            prestacion_id       TEXT,                          -- nullable; ref BLANDA a prestaciones.id
            item_id             TEXT,                          -- nullable; ref BLANDA a items_contrato.id
            tipo                TEXT    CHECK (tipo IN ('FIEL_CUMPLIMIENTO','ADELANTO_DIRECTO','ADELANTO_MATERIALES','MONTO_DIFERENCIAL') OR tipo IS NULL),
            modalidad           TEXT    CHECK (modalidad IN ('CARTA_FIANZA','SEGURO_CAUCION','DEPOSITO') OR modalidad IS NULL),
            numero_carta_fianza TEXT,                          -- externo; nullable, editable, NO único
            monto               INTEGER,                       -- céntimos (S/ x 100)
            moneda              TEXT    NOT NULL DEFAULT 'PEN',
            entidad_emisora     TEXT,                          -- banco / aseguradora
            fecha_emision       TEXT,                          -- ISO (YYYY-MM-DD)
            fecha_vencimiento   TEXT,                          -- ISO
            estado              TEXT    CHECK (estado IN ('VIGENTE','EJECUTADA','DEVUELTA') OR estado IS NULL),
            observaciones       TEXT,
            activo              INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at          TEXT    NOT NULL,
            updated_at          TEXT    NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_garantias_contrato ON garantias(contrato_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_garantias_prestacion ON garantias(prestacion_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_garantias_vencimiento ON garantias(fecha_vencimiento)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_garantias_updated
        AFTER UPDATE ON garantias
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE garantias SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )
    await db.commit()

    app["db"] = db
    log.info("DB conectada: %s", db_path)


async def close_db(app: web.Application) -> None:
    await app["db"].close()
    log.info("DB cerrada")
