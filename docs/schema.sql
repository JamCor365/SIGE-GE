-- =====================================================================
--  SIGE-GE  ·  cache.db  ·  Esquema SQLite
--  Migración desde SIGE_GE_Master_API.xlsx (hojas MACROREGIONES, SEDES,
--  GE, TTA)
--
--  Convenciones:
--   · snake_case para todos los identificadores
--   · IDs naturales del Excel preservadas como PRIMARY KEY
--   · Columnas de sistema en cada tabla:
--       activo      INTEGER NOT NULL DEFAULT 1   (1=activo, 0=baja lógica)
--       created_at  TEXT    NOT NULL             (ISO-8601 YYYY-MM-DD[ HH:MM:SS])
--       updated_at  TEXT    NOT NULL             (ISO-8601 YYYY-MM-DD[ HH:MM:SS])
--   · Llaves foráneas con ON UPDATE CASCADE / ON DELETE RESTRICT
--   · CHECKs sobre dominios cerrados (estado, fase, etc.)
-- =====================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------------
-- 1. macroregiones  (catálogo maestro)
-- ---------------------------------------------------------------------
CREATE TABLE macroregiones (
    id          INTEGER PRIMARY KEY,            -- 0..6 (no AUTOINCREMENT: el id 0 es válido)
    nombre      TEXT    NOT NULL UNIQUE,
    activo      INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

-- ---------------------------------------------------------------------
-- 2. sedes  (agencias / almacenes)
-- ---------------------------------------------------------------------
CREATE TABLE sedes (
    id              INTEGER PRIMARY KEY,        -- ID_Sede natural (1..495)
    codigo          TEXT    NOT NULL UNIQUE,    -- Codigo_Sede (ej. AG-0001, AL-2638)
    nombre_agencia  TEXT    NOT NULL,
    categoria       TEXT    CHECK (categoria IN ('Agencia 1','Agencia 2','Agencia 3','Almacen') OR categoria IS NULL),
    direccion       TEXT,
    departamento    TEXT,
    provincia       TEXT,
    distrito        TEXT,
    macroregion_id  INTEGER NOT NULL,
    observaciones   TEXT,
    activo          INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    FOREIGN KEY (macroregion_id) REFERENCES macroregiones(id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE INDEX idx_sedes_macroregion ON sedes(macroregion_id);
CREATE INDEX idx_sedes_categoria   ON sedes(categoria);
CREATE INDEX idx_sedes_codigo      ON sedes(codigo);

-- ---------------------------------------------------------------------
-- 3. grupos_electrogenos  (inventario de GE)
-- ---------------------------------------------------------------------
CREATE TABLE grupos_electrogenos (
    id                         INTEGER PRIMARY KEY,    -- ID_GE natural
    sede_id                    INTEGER NOT NULL,
    cod_margesi                TEXT,                   -- código patrimonial Banco de la Nación
    estado                     TEXT    CHECK (estado IN ('OPERATIVO','INOPERATIVO') OR estado IS NULL),
    anio_fabricacion           INTEGER,
    potencia_kw                REAL,
    fase_electrica             TEXT    CHECK (fase_electrica IN ('MONOFASICO','TRIFASICO') OR fase_electrica IS NULL),
    tipo_transferencia         TEXT    CHECK (tipo_transferencia IN ('AUTOMATICO','MANUAL') OR tipo_transferencia IS NULL),
    mecanismo_transferencia    TEXT,
    marca_ensamblador          TEXT,
    modelo_ensamblador         TEXT,
    serie_ensamblador          TEXT,
    marca_motor                TEXT,
    modelo_motor               TEXT,
    serie_motor                TEXT,
    marca_alternador           TEXT,
    modelo_alternador          TEXT,
    serie_alternador           TEXT,
    marca_modulocontrol        TEXT,
    modelo_modulocontrol       TEXT,
    serie_modulocontrol        TEXT,
    observaciones              TEXT,
    activo                     INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    created_at                 TEXT    NOT NULL,       -- mapea Fecha_Registro
    updated_at                 TEXT    NOT NULL,       -- mapea Fecha_Actualizacion
    FOREIGN KEY (sede_id) REFERENCES sedes(id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE INDEX idx_ge_sede     ON grupos_electrogenos(sede_id);
CREATE INDEX idx_ge_estado   ON grupos_electrogenos(estado);
CREATE INDEX idx_ge_margesi  ON grupos_electrogenos(cod_margesi);

-- ---------------------------------------------------------------------
-- 4. tta  (tableros de transferencia automática)
-- ---------------------------------------------------------------------
CREATE TABLE tta (
    id              INTEGER PRIMARY KEY,        -- ID_TTA natural
    sede_id         INTEGER NOT NULL,
    cod_margesi     TEXT,
    marca           TEXT,
    modelo          TEXT,
    serie           TEXT,
    tipo_mecanismo  TEXT,
    fases           TEXT    CHECK (fases IN ('MONOFASICO','TRIFASICO') OR fases IS NULL),
    estado          TEXT    CHECK (estado IN ('OPERATIVO','INOPERATIVO') OR estado IS NULL),
    observaciones   TEXT,
    activo          INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    created_at      TEXT    NOT NULL,           -- mapea Fecha_Registro
    updated_at      TEXT    NOT NULL,           -- mapea Fecha_Actualizacion
    FOREIGN KEY (sede_id) REFERENCES sedes(id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE INDEX idx_tta_sede    ON tta(sede_id);
CREATE INDEX idx_tta_margesi ON tta(cod_margesi);

-- ---------------------------------------------------------------------
-- Triggers de updated_at  (refresco automático en UPDATE)
-- ---------------------------------------------------------------------
CREATE TRIGGER trg_macroregiones_updated
AFTER UPDATE ON macroregiones
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE macroregiones SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_sedes_updated
AFTER UPDATE ON sedes
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE sedes SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_ge_updated
AFTER UPDATE ON grupos_electrogenos
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE grupos_electrogenos SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_tta_updated
AFTER UPDATE ON tta
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE tta SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

-- ---------------------------------------------------------------------
-- Vistas de conveniencia
-- ---------------------------------------------------------------------
CREATE VIEW v_sedes_completo AS
SELECT s.id, s.codigo, s.nombre_agencia, s.categoria,
       s.direccion, s.departamento, s.provincia, s.distrito,
       m.id   AS macroregion_id,
       m.nombre AS macroregion_nombre,
       s.activo, s.created_at, s.updated_at
FROM sedes s
JOIN macroregiones m ON m.id = s.macroregion_id;

CREATE VIEW v_ge_completo AS
SELECT g.id, g.cod_margesi, g.estado, g.anio_fabricacion, g.potencia_kw,
       g.fase_electrica, g.tipo_transferencia, g.mecanismo_transferencia,
       g.marca_ensamblador, g.modelo_ensamblador, g.serie_ensamblador,
       g.marca_motor, g.modelo_motor, g.serie_motor,
       g.marca_alternador, g.modelo_alternador, g.serie_alternador,
       g.marca_modulocontrol, g.modelo_modulocontrol, g.serie_modulocontrol,
       s.id AS sede_id, s.codigo AS sede_codigo, s.nombre_agencia,
       m.nombre AS macroregion,
       g.activo, g.created_at, g.updated_at
FROM grupos_electrogenos g
JOIN sedes s         ON s.id = g.sede_id
JOIN macroregiones m ON m.id = s.macroregion_id;

CREATE VIEW v_tta_completo AS
SELECT t.id, t.cod_margesi, t.marca, t.modelo, t.serie,
       t.tipo_mecanismo, t.fases, t.estado,
       s.id AS sede_id, s.codigo AS sede_codigo, s.nombre_agencia,
       m.nombre AS macroregion,
       t.activo, t.created_at, t.updated_at
FROM tta t
JOIN sedes s         ON s.id = t.sede_id
JOIN macroregiones m ON m.id = s.macroregion_id;
