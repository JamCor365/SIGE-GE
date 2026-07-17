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


# Enriquecimiento del inventario GE/TTA con la data verificada de Valtom (specs
# completas, garantía y atributos de monitoreo de red), y vínculo TTA↔GE.
# CREATE TABLE del schema no altera tablas existentes → las cache.db previas se
# migran aquí con ALTER TABLE ADD COLUMN (idempotente). Nullable: los GE/TTA ya
# cargados conservan su estado sin estos campos hasta poblarlos.
_GRUPOS_NEW_COLUMNS = {
    "etiqueta": "TEXT",
    "potencia_efectiva_kw": "REAL",
    "voltaje": "TEXT",
    "frecuencia": "TEXT",
    "cod_fabricante": "TEXT",
    "fecha_garantia_ini": "TEXT",
    "fecha_garantia_fin": "TEXT",
    "red_ip": "TEXT",
    "red_mascara": "TEXT",
    "red_gateway": "TEXT",
}

# ge_id: el TTA cuelga del GE que sirve (modelo Valtom). FK dura pero NULLABLE:
# ALTER TABLE ADD COLUMN con REFERENCES exige default NULL, y los TTA heredados
# aún no tienen GE asignado. ON DELETE RESTRICT: no borrar un GE con TTA vivo.
_TTA_NEW_COLUMNS = {
    "ge_id": "INTEGER REFERENCES grupos_electrogenos(id) ON UPDATE CASCADE ON DELETE RESTRICT",
    "etiqueta": "TEXT",
}

# Vistas canónicas GE/TTA — idénticas a docs/schema.sql; se recrean en la
# migración para exponer las columnas nuevas en DBs heredadas.
_V_GE_COMPLETO = """
CREATE VIEW v_ge_completo AS
SELECT g.id, g.cod_margesi, g.etiqueta, g.estado, g.anio_fabricacion,
       g.potencia_kw, g.potencia_efectiva_kw, g.voltaje, g.frecuencia,
       g.fase_electrica, g.tipo_transferencia, g.mecanismo_transferencia,
       g.marca_ensamblador, g.modelo_ensamblador, g.serie_ensamblador, g.cod_fabricante,
       g.marca_motor, g.modelo_motor, g.serie_motor,
       g.marca_alternador, g.modelo_alternador, g.serie_alternador,
       g.marca_modulocontrol, g.modelo_modulocontrol, g.serie_modulocontrol,
       g.fecha_garantia_ini, g.fecha_garantia_fin,
       g.red_ip, g.red_mascara, g.red_gateway,
       s.id AS sede_id, s.codigo AS sede_codigo, s.nombre_agencia,
       m.nombre AS macroregion,
       g.activo, g.created_at, g.updated_at
FROM grupos_electrogenos g
JOIN sedes s         ON s.id = g.sede_id
JOIN macroregiones m ON m.id = s.macroregion_id
"""

_V_TTA_COMPLETO = """
CREATE VIEW v_tta_completo AS
SELECT t.id, t.ge_id, t.cod_margesi, t.etiqueta, t.marca, t.modelo, t.serie,
       t.tipo_mecanismo, t.fases, t.estado,
       s.id AS sede_id, s.codigo AS sede_codigo, s.nombre_agencia,
       m.nombre AS macroregion,
       t.activo, t.created_at, t.updated_at
FROM tta t
JOIN sedes s         ON s.id = t.sede_id
JOIN macroregiones m ON m.id = s.macroregion_id
"""


async def _migrate_grupos(db: aiosqlite.Connection) -> None:
    """Añade specs/garantía/red de Valtom a `grupos_electrogenos` y recrea su vista.

    En una DB nueva (schema.sql ya trae las columnas y la vista finales) todo es
    no-op salvo el DROP/CREATE de la vista, que la deja idéntica.
    """
    async with db.execute("PRAGMA table_info(grupos_electrogenos)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    for name, decl in _GRUPOS_NEW_COLUMNS.items():
        if name not in cols:
            await db.execute(f"ALTER TABLE grupos_electrogenos ADD COLUMN {name} {decl}")
    await db.execute("DROP VIEW IF EXISTS v_ge_completo")
    await db.execute(_V_GE_COMPLETO)


async def _migrate_tta(db: aiosqlite.Connection) -> None:
    """Añade el vínculo ge_id y `etiqueta` a `tta` y recrea su vista (idempotente)."""
    async with db.execute("PRAGMA table_info(tta)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    for name, decl in _TTA_NEW_COLUMNS.items():
        if name not in cols:
            await db.execute(f"ALTER TABLE tta ADD COLUMN {name} {decl}")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tta_ge ON tta(ge_id)")
    await db.execute("DROP VIEW IF EXISTS v_tta_completo")
    await db.execute(_V_TTA_COMPLETO)


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

    # Enriquecimiento GE/TTA con la data verificada de Valtom (specs, garantía,
    # monitoreo de red) + vínculo TTA↔GE; idempotente.
    await _migrate_grupos(db)
    await _migrate_tta(db)

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

    # Adendas: modificaciones al contrato tras su firma (Ley de Contrataciones del
    # Estado). PK = UUID surrogate (como prestaciones/garantias): entidad creada de
    # forma distribuida; `numero` (1=Primera, 2=Segunda…) es el identificador humano
    # pero NO es PK ni UNIQUE — el dedup por (contrato_id, numero) es BLANDO en la
    # ruta (dos máquinas offline no deben descartar en silencio una de dos filas con
    # UUID distinto; principio de unicidad segura del proyecto). Nada le hace FK.
    #
    # MONTO DELTA SEPARADO principal/accesorio (INTEGER céntimos CON SIGNO, negativo
    # si reduce): una misma adenda puede tocar ambas prestaciones con montos
    # distintos (confirmado en la Primera Adenda real del 28278-2022-BN, que reduce
    # principal y accesoria por separado). Espeja el split de `contratos` y permite
    # derivar el monto vigente por tipo de prestación read-time (base + Σ deltas).
    # plazo_delta_dias: variación de plazo (ampliaciones); NULL si no toca plazo.
    #
    # tipo (CHECK vocab): REDUCCION y MODIFICACION_CONVENCIONAL son los 2 tipos
    # reales del contrato Valtom (Primera=reducción, Segunda=incorpora anexo SLA);
    # AMPLIACION_PLAZO y ADICIONAL completan los estándar de la Ley.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS adendas (
            id                    TEXT    PRIMARY KEY,           -- uuid4().hex
            contrato_id           TEXT    NOT NULL REFERENCES contratos(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            numero                INTEGER NOT NULL,              -- 1=Primera, 2=Segunda… (dedup blando por (contrato_id,numero))
            fecha                 TEXT,                          -- suscripción de la adenda (ISO)
            tipo                  TEXT    CHECK (tipo IN ('AMPLIACION_PLAZO','ADICIONAL','REDUCCION','MODIFICACION_CONVENCIONAL') OR tipo IS NULL),
            base_legal            TEXT,                          -- artículo/norma que la sustenta
            objeto                TEXT,                          -- qué modifica (resumen)
            monto_delta_principal INTEGER,                       -- céntimos con signo; NULL si no toca principal
            monto_delta_accesorio INTEGER,                       -- céntimos con signo; NULL si no toca accesoria
            plazo_delta_dias      INTEGER,                       -- variación de plazo en días; NULL si no toca plazo
            observaciones         TEXT,
            activo                INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at            TEXT    NOT NULL,
            updated_at            TEXT    NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_adendas_contrato ON adendas(contrato_id)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_adendas_updated
        AFTER UPDATE ON adendas
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE adendas SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )

    # Penalidades: descuentos al contratista por incumplimiento (Ley de
    # Contrataciones). PK = UUID surrogate (como garantias): entidad creada de
    # forma distribuida, sin clave natural que converja (una misma causa puede
    # penalizarse varias veces). Nada le hace FK.
    #
    # tipo MORA vs OTRAS: en sg-valtom (acta_conformidad) las penalidades se
    # rastrean justamente con ese split (penalidad_mora / otras_penalidades).
    #   - MORA: por retraso; `dias_mora` guarda los días de atraso.
    #   - OTRAS: infracciones tipificadas en las bases; `concepto` describe la causa.
    # monto en céntimos (>= 0). prestacion_id/item_id son refs BLANDAS validadas en
    # ruta (existen + pertenecen al contrato), como en garantias. estado del ciclo
    # de la penalidad: EN_EVALUACION → APLICADA / EXONERADA.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS penalidades (
            id            TEXT    PRIMARY KEY,           -- uuid4().hex
            contrato_id   TEXT    NOT NULL REFERENCES contratos(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            prestacion_id TEXT,                          -- ref blanda (validada en ruta)
            item_id       TEXT,                          -- ref blanda (validada en ruta)
            tipo          TEXT    CHECK (tipo IN ('MORA','OTRAS') OR tipo IS NULL),
            concepto      TEXT,                          -- causa/descripción de la penalidad
            monto         INTEGER,                       -- céntimos (S/ x 100), >= 0
            moneda        TEXT    NOT NULL DEFAULT 'PEN',
            dias_mora     INTEGER,                       -- días de retraso (solo MORA); NULL en OTRAS
            base_legal    TEXT,                          -- cláusula/norma que la sustenta
            fecha         TEXT,                          -- aplicación/detección (ISO)
            estado        TEXT    CHECK (estado IN ('EN_EVALUACION','APLICADA','EXONERADA') OR estado IS NULL),
            observaciones TEXT,
            activo        INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_penalidades_contrato ON penalidades(contrato_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_penalidades_prestacion ON penalidades(prestacion_id)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_penalidades_updated
        AFTER UPDATE ON penalidades
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE penalidades SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )

    # Servicios de mantenimiento: cronograma de ejecución de la prestación
    # accesoria, UNO POR (GE × nro_servicio) — el mantenimiento es del equipo
    # (decisión de granularidad: permite rastrear qué GE tuvo su MPV-N y cuál no).
    # El alcance geográfico (sede/macro) NO se almacena: se DERIVA del GE.
    #
    # PK = UUID surrogate (entidad creada de forma distribuida). ge_id es FK dura a
    # grupos_electrogenos (como contrato_ge). prestacion_id es ref BLANDA opcional a
    # la prestación accesoria (validada en ruta). FECHAS SEPARADAS por origen, como
    # en sg-valtom: fecha_programada (cronograma) vs fecha_ejecutada (ejecución
    # real) — nunca se pisan. estado del ciclo del servicio.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS servicios_mantenimiento (
            id                TEXT    PRIMARY KEY,           -- uuid4().hex
            contrato_id       TEXT    NOT NULL REFERENCES contratos(id)          ON UPDATE CASCADE ON DELETE RESTRICT,
            ge_id             INTEGER NOT NULL REFERENCES grupos_electrogenos(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            prestacion_id     TEXT,                          -- ref blanda a la prestación accesoria (validada en ruta)
            nro_servicio      INTEGER NOT NULL,              -- 1..N (secuencia del cronograma, p.ej. MPV1..MPV4)
            fecha_programada  TEXT,                          -- cronograma (ISO)
            fecha_ejecutada   TEXT,                          -- ejecución real (ISO); separada de la programada
            estado            TEXT    CHECK (estado IN ('PROGRAMADO','EJECUTADO','CONFORME','OBSERVADO') OR estado IS NULL),
            observaciones     TEXT,
            activo            INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at        TEXT    NOT NULL,
            updated_at        TEXT    NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_servicios_contrato ON servicios_mantenimiento(contrato_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_servicios_ge ON servicios_mantenimiento(ge_id)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_servicios_updated
        AFTER UPDATE ON servicios_mantenimiento
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE servicios_mantenimiento SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )

    # Adjuntos: METADATA de archivos (actas, informes, guías, fotos…). El ARCHIVO
    # vive en SharePoint, NO en SQLite: aquí solo su ruta/enlace + integridad, para
    # no inflar la base ni romper el sync. PK = UUID surrogate.
    #
    # Puntero POLIMÓRFICO BLANDO al objeto que documenta: ref_entidad (token, p.ej.
    # 'adenda'/'servicio'/'penalidad') + ref_id (id de esa fila). NO es FK — no se
    # puede FK polimórficamente; es metadata, se valida en la capa de app si hace
    # falta. contrato_id sí es FK dura (todo adjunto cuelga de un contrato).
    #
    # tipo (CHECK vocab): tomado de sg-valtom (documento.tipo), el mismo universo de
    # documentos del legajo del Estado.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS adjuntos (
            id            TEXT    PRIMARY KEY,           -- uuid4().hex
            contrato_id   TEXT    NOT NULL REFERENCES contratos(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            ref_entidad   TEXT,                          -- token de la entidad relacionada (soft, polimórfico)
            ref_id        TEXT,                          -- id de esa entidad (soft, NO FK)
            tipo          TEXT    CHECK (tipo IN ('CONTRATO','BASES','ADENDA','ACTA_CONFORMIDAD','INFORME_TECNICO','GUIA_REMISION','PANEL_FOTOGRAFICO','CONSTANCIA_OPERATIVIDAD','CARTA_FIANZA','OTRO') OR tipo IS NULL),
            nombre        TEXT,                          -- nombre/título del archivo
            ruta          TEXT,                          -- ruta/enlace en SharePoint (el archivo vive FUERA de SQLite)
            sha256        TEXT,                          -- integridad (NO único: un archivo puede repetirse)
            paginas       INTEGER,
            fecha         TEXT,                          -- fecha del documento (ISO)
            observaciones TEXT,
            activo        INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_adjuntos_contrato ON adjuntos(contrato_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_adjuntos_ref ON adjuntos(ref_entidad, ref_id)"
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_adjuntos_updated
        AFTER UPDATE ON adjuntos
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE adjuntos SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
        END
        """
    )
    await db.commit()

    app["db"] = db
    log.info("DB conectada: %s", db_path)


async def close_db(app: web.Application) -> None:
    await app["db"].close()
    log.info("DB cerrada")
