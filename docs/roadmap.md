# SIGE-GE — Roadmap de desarrollo

## Nomenclatura de ramas lógicas

Las "ramas" son unidades de trabajo, todas integradas en `master`.
No son ramas de git — son etapas del proyecto.

BASE + RamaA–RamaD: completadas e integradas.

---

## RamaE — Robustez de Sincronización

### Objetivo

Hacer el sistema de sync más robusto y auditable sin romper la arquitectura P2P offline-first ya probada.

---

### Base intocable (no modificar al avanzar en RamaE)

Los 24 tests deben pasar en cada fase.

- CRUD de los 4 módulos (grupos, sedes, macroregiones, tta) y sus routes.
- Frontend: forms corregidos (`_form_helpers.js`, id fuera del payload, submit dentro del form).
- Eventos JSON + `events_log` + `upload_event` a SharePoint (`events_pending/`).
- Sync actual: subida automática tras cada CRUD + reintento de `synced=0` al sincronizar + bajada (`apply_remote_events`).
- Header anti-caché de estáticos.

---

### Diagnóstico del estado actual (sesión 2026-06-05)

**Identidad de usuario/máquina:**
- `get_user()` lee header HTTP `X-SIGE-User`. El frontend nunca lo envía → `created_by` es siempre `"SIGE_LOCAL"`.
- `machine` captura el hostname real vía `platform.node()` → sí identifica la máquina de origen.

**Carpetas de SharePoint — uso real:**
- `events_pending/` → usada (`upload_event`, `list_pending`, `download_event`).
- `events_processed/` → creada, `mark_processed()` implementado en ambos backends, **nunca llamado** desde `sync_engine.py`.
- `events_error/` → creada, **sin ningún uso** en el código.
- `master/` → creada, `download_snapshot()` implementado, **nunca llamado** desde `sync_engine.py`.

**Bootstrap de máquina nueva:**
- `INICIAR.ps1` crea `data/cache.db` como archivo vacío.
- `init_db()` detecta que `macroregiones` no existe y ejecuta `docs/schema.sql` → tablas vacías, sin datos.
- El único mecanismo de recuperación de datos es el sync manual: `apply_remote_events` aplica **toda** la historia desde `events_pending/`. No hay snapshot.

**Ciclo de vida de un evento:**
- Los eventos en `events_pending/` **nunca se mueven ni se borran** (decisión de diseño explícita en `sync_engine.py`).
- Cada PC evita re-aplicar eventos via su `events_log` local (deduplicación por `event_id`).

---

### Fases (orden estricto — cada fase depende de la anterior)

#### Fase 1 — Identidad automática ⬜

**Objetivo:** `created_by` refleja el usuario/máquina real en lugar del string fijo `"SIGE_LOCAL"`.

**Diseño:**
- Prioridad de resolución: `settings.toml [app] user` (si no está vacío) → `getpass.getuser()` → `platform.node()`.
- `get_user()` en `events.py` deja de leer el header HTTP y pasa a leer config + OS.
- El valor se pasa a `make_event()` como hasta ahora.
- No hay cambios en frontend, esquema de DB ni formato de evento.
- Actualizar `config/settings.example.toml` con la nueva clave `user` (comentada, opcional).

**Archivos afectados:** `backend/events.py`, `backend/config.py` (o pasar config a get_user), `config/settings.example.toml`.

**Tests:** los existentes no deben romperse; añadir test para la lógica de prioridad de resolución.

---

#### Fase 2 — Snapshot en `master/` ✅

**Estado:** ✅ VERIFICADA EN BANCO (2026-06-08). `latest_snapshot.db` y `snapshot_meta.json` se generan correctamente; `last_event_id` coincide con el último evento real. Bug de race condition en `_ensure_context` resuelto (commit `ff50a1a`).

**Objetivo:** una máquina nueva arranca desde un snapshot del estado actual en lugar de replayar toda la historia de `events_pending/`.

**Requiere diseño aprobado antes de codear.** Preguntas a resolver:

1. **Formato del snapshot:** JSON con el volcado completo de las 4 tablas (más portable y auditable) vs. `.db` binario (más simple pero opaco). Recomendación pendiente de aprobación.
2. **Marcador de corte:** el snapshot debe incluir el `event_id` más reciente que incorpora, para que la máquina nueva pueda aplicar solo los eventos posteriores al snapshot.
3. **Quién genera el snapshot:** cualquier máquina al sincronizar (si el snapshot local está desactualizado en N eventos) vs. operación manual explícita.
4. **Cuándo:** al finalizar `apply_remote_events` si se aplicó al menos 1 evento nuevo, o bajo demanda.
5. **Cómo lo usa una máquina nueva:** `init_db()` detecta cache.db vacío → descarga snapshot → aplica eventos posteriores al `event_id` del snapshot desde `events_pending/`.

**Archivos afectados:** `backend/sync_engine.py`, `backend/db.py`, `backend/storage/base.py` (ya tiene `download_snapshot`), ambos backends, posiblemente `INICIAR.ps1` / `start.sh`.

---

#### Fase 3 — Archivado a `events_processed/` 🟡

**Estado:** 🟡 IMPLEMENTADA Y COMMITEADA (commit `3c369e7`), 41/41 tests pasando. Pendiente de verificación en banco:
- **Pieza 1 — archivado** (`event_id <= snap_last` AND ∈ `events_log` local): pendiente de verificar en banco — esperando el primer arranque del 2026-06-09 que dispare snapshot + archivado en el camino SharePoint real.
- **Pieza 2 — recuperación de máquina atrasada** (`recover_state`): pendiente — requiere una SEGUNDA máquina para probar el escenario P2P real; no verificable con una sola workstation.

**Objetivo:** evitar que `events_pending/` crezca sin límite. Mover eventos capturados por el snapshot a `events_processed/` para auditoría OCI.

**Solo seguro después de Fase 2.** Razón: `list_pending()` es hoy el único mecanismo de descubrimiento de eventos. Archivar antes de tener snapshot rompe la recuperación de máquinas nuevas.

**Diseño implementado (commit `3c369e7`):**

**Pieza 1 — archivado:**
- Elegibilidad (Decisión A): archivar los eventos de `events_pending/` cuyo `event_id <= snap_last` **AND** que estén en el `events_log` local. Como el snapshot ES la DB local al momento del backup, "∈ `events_log`" ≡ "contenido en el snapshot": excluye los pending de otras máquinas aún no aplicados y los `synced=0` inyectados por un re-bootstrap.
- Disparo: dentro de `_maybe_generate_snapshot`, **solo después** de confirmar `latest_snapshot.db` + `snapshot_meta.json` en `master/` (nunca antes).
- `mark_processed()` quedó **DEPRECADO** (sin uso desde Fase 1). El archivado usa el nuevo `archive_processed()`: copia a `events_processed/` con overwrite y luego borra de `events_pending/` — **idempotente** (si crashea entre copia y borrado, la próxima corrida re-copia y re-borra sin error).
- Los archivos en `events_processed/` quedan planos (sin subcarpetas por fecha); la fecha ya está en el nombre del archivo.

**Pieza 2 — recuperación de máquina atrasada:**
- `recover_state()` generaliza `bootstrap_if_new`: además del caso máquina nueva (`cache.db` de 0 bytes), cubre la máquina atrasada (`local_max_event_id < snapshot.last_event_id`).
- Secuencia crash-safe (orden obligatorio): 1) subir los `synced=0` locales a `events_pending/` **antes** de tocar `cache.db` (si algún upload falla, aborta sin tocar `cache.db` para no perder cambios); 2) bajar `latest_snapshot.db`; 3) reemplazo **atómico** de `cache.db` (temp + `os.replace`, sidecars `-wal`/`-shm` borrados); 4) aplicar la unión ordenada (Decisión B).
- Aplicación (Decisión B): `apply_post_snapshot_events` aplica la unión ordenada por `event_id` de (`events_pending/` con `id > last_event_id`) ∪ (los `synced=0` capturados), deduplicada por pertenencia a `events_log`. Esto re-aplica los `synced=0` aunque su `id <= snap_last`.

**Decisión tomada:** NO archivar por consenso entre máquinas. Razón: una máquina apagada indefinidamente bloquearía el archivado de todas. Rompe el modelo P2P offline-first.

**Archivos afectados:** `backend/snapshot.py`, `backend/server.py`, `backend/storage/base.py`, `backend/storage/local_folder.py`, `backend/storage/sharepoint.py`, `tests/test_snapshot.py`.

---

#### Fase 4 — `events_error/` y robustez de aplicación

Dos ítems de la misma familia: persistir lo que falla y no perder updates que llegan fuera de orden.

##### 4a — `events_error/` ⬜ (prioridad baja)

**Objetivo:** persistir en `events_error/` los eventos que fallaron al procesarse (hoy van solo al log de Python y se pierden al reiniciar).

**Prioridad baja.** No hay casos de error en producción que justifiquen priorizarlo antes que las fases anteriores.

**Diseño preliminar:** al capturar excepción en `apply_remote_events`, subir el JSON original a `events_error/{event_id}.json` con un campo adicional `error` y `failed_at`. La carpeta ya existe en ambos backends.

**Archivos afectados:** `backend/sync_engine.py`, `backend/storage/base.py` (nuevo método `upload_error`), ambos backends.

##### 4b — Hardening de orden: update/delete que llega antes que su create ✅ COMPLETADO

**Estado:** ✅ IMPLEMENTADO Y VERIFICADO (142 tests verdes; 5 nuevos en `tests/test_sync_ordering.py`). Pendiente de verificación en banco con segunda máquina (escenario P2P real de subida parcial). Enfoque elegido: **Opción 1 — deferral por re-descubrimiento** (sin migración).

**Fix implementado:** `_apply_one` ahora devuelve filas afectadas (`int | None`): `int` para update/delete, `None` para create (ancla) y para update sin columnas válidas (no-op, evita bucle de deferral). Si un update/delete afecta **0 filas**, `apply_remote_events` y `apply_post_snapshot_events` lo **DIFIEREN**: `rollback` + NO registrar en `events_log`, de modo que el evento permanece en `events_pending/` y el próximo sync lo re-descubre y re-aplica una vez presente el `create` (que por orden lexicográfico se procesa antes en el mismo ciclo). `apply_remote_events` devuelve un contador `deferred`; el frontend lo muestra ("N en espera de su creación").

**Residual conocido (acotar en 4a):** un huérfano *genuino permanente* —`create` de una máquina que murió sin subirlo nunca— se re-descarga y re-intenta (0 filas) en cada sync indefinidamente. Es barato (un UPDATE de 0 filas) y consistente con el modelo eventually-consistent que tolera máquinas muertas; el tope explícito (mandar a `events_error/` tras N intentos) es trabajo de 4a.

**Qué era (agujero real, confirmado en el código):** cuando `_apply_one` aplicaba un `update` o `delete` cuyo `WHERE id = X` no encontraba fila (porque el `create` aún no llegó), afectaba **0 filas sin error**, y `apply_remote_events` lo registraba igual en `events_log` con `synced=1`. El evento quedaba "consumido". Cuando el `create` llegaba después, se insertaba con `INSERT OR IGNORE` la versión **vieja** de la entidad y el `update` ya no se re-aplicaba → **el cambio se perdía en silencio**.

El orden lexicográfico de `event_id` (≡ cronológico) cubre el caso normal en que ambos eventos están presentes al sincronizar. El hueco se abre solo en **subida parcial**: el `create` falla al subir (queda `synced=0`, no está en SharePoint) mientras un `update` posterior sube OK, y otra máquina sincroniza en esa ventana.

**Por qué importaba AHORA y no "algún día":** `contrato` es la PRIMERA entidad que se **crea y edita activamente de forma distribuida** desde la UI. Los maestros (`grupo_electrogeno`, `sede`, etc.) se cargaron en bloque desde el Excel y casi no se tocan desde la UI — por eso nunca expusieron este hueco, aunque el motor lo tiene desde siempre. El riesgo se materializa con el uso intensivo multi-máquina de contratos.

**Archivos afectados (implementado):** `backend/sync_engine.py` (`_apply_one` reporta filas afectadas; `apply_remote_events` difiere en 0 filas y devuelve `deferred`), `backend/snapshot.py` (`apply_post_snapshot_events` comparte `_apply_one`), `frontend/views/sync.js` (muestra `deferred`), `tests/test_sync_ordering.py` (5 tests nuevos).

---

### Decisiones de diseño ya tomadas

| Decisión | Razón |
|----------|-------|
| NO archivado por consenso entre máquinas | Una máquina apagada indefinidamente congela el archivado de todas — rompe el P2P offline-first |
| NO resolución de nombre completo vía Outlook/AD | Se pospone para un instalador futuro junto con la config del link de SharePoint |

---

## RamaF — Contratos de mantenimiento/adquisición de GE

### Objetivo

Modelar los contratos que "cuelgan" alrededor de los GE. Un contrato es **dato, no estructura**: nace y termina, se modela con una tabla + campo `estado`, no con tabs dedicados. Los GE son permanentes; los contratos, temporales.

### Decisiones de diseño tomadas

| Decisión | Razón |
|----------|-------|
| PK = UUID (`id TEXT` = `uuid4().hex`, generado en backend) | Entidad creada de forma distribuida: dos máquinas offline no deben colisionar ids de fila al sincronizar. AUTOINCREMENT colisiona (silenciosamente, vía `INSERT OR IGNORE`); el id natural no aplica porque el `numero` es editable y a veces ausente. |
| `numero` UNIQUE pero NULLABLE | Es el identificador humano (nº de proceso), pero editable (se corrige entre convocatoria y adjudicación) y puede faltar (registro durante procura, antes de adjudicar). Varios NULL no violan UNIQUE en SQLite. |
| Adjuntos (actas, informes) NO en SQLite | Van en SharePoint; la tabla guardará solo el enlace/ruta (cuando se diseñe). No inflar la base ni romper el sync. |
| Vínculo GE↔contrato como tabla puente N:M `contrato_ge` ✅ IMPLEMENTADO | Un GE pasa por varios contratos en el tiempo y un contrato cubre muchos GE. PK = `id` TEXT **determinista** del par (`"{contrato_id}_{ge_id}"`), no UUID aleatorio: la colisión entre máquinas se vuelve idempotencia y un desvínculo converge. El motor de sync queda intacto (lee `id` escalar). |
| Alcance geográfico DERIVADO, no almacenado | Las macrorregiones/agencias que cubre un contrato se calculan vía `contrato_ge → ge → sede → macrorregión`. Por eso se eliminó el campo `ambito` en el rediseño de la cáscara. |

### Pasos

#### PASO 1 — Backend de contratos ✅ COMPLETADO

- Tabla `contratos` con PK UUID, `numero` UNIQUE nullable, `estado` CHECK (`VIGENTE`,`CULMINADO`,`RESUELTO`), `tipo_objeto` CHECK (`MANTENIMIENTO`,`ADQUISICION`,`ADQUISICION_INSTALACION`). Creada en `db.py` vía `CREATE TABLE IF NOT EXISTS` (igual que `events_log`) para que la `cache.db` ya poblada del banco la cree en el próximo arranque sin migración manual.
- `"contrato"` añadido a `_VALID_ENTITIES` (`events.py`) y a `_ENTITY_TABLE` (`sync_engine.py`). El motor de sync genérico maneja create/update/delete sin cambios.
- CRUD en `routes/contratos.py`: el UUID se genera en el backend al crear (no en frontend); el evento se emite con `entity_id = ese UUID`.
- 12 tests nuevos (UUID, `numero` nullable/unique, CHECKs, apply remoto create/update/delete). Total suite: 53 en verde.
- **Sin UI. Sin vínculo GE↔contrato.** Ver hardening 4b: el uso intensivo multi-máquina de contratos es lo que materializa el hueco de orden — resolverlo antes de producción intensiva.

#### PASO 2 — CRUD frontend de contratos ✅ COMPLETADO

Vista lista/detalle + formulario, con el tab "Contratos" como un item más en el sidebar **plano** actual. Cableado (ruta + sidebar + estado) en commit `ef74086`; el view + el rediseño de la cáscara, en `edfc471`. Validado cargando Valtom (`28278-2022-BN`) como primera fila real.

#### PASO 3 — Reagrupación de tabs en grupos/subgrupos ⬜ PENDIENTE

Cambio **cosmético** aparte, en su propio commit (no mezclar con funcional). Jerarquía aprobada: Maestros / Contratos / Operación. El grupo **Reportes** NO se crea hasta que exista RamaD (`/export`).

### Modelo general de contratos — entidades incrementales

> Eje **distinto** a los PASOS de despliegue de arriba (backend/frontend/tabs). Aquí se numera la construcción **incremental del modelo de datos** general del Estado peruano (Ley de Contrataciones), aprobado en diseño en papel. Diseño completo, implementación paso a paso.

La cáscara `contratos` original (PASO 1, enum `tipo_objeto` combinado, campo `ambito`) fue **rediseñada** a modelo general en `edfc471`: `+procedimiento_seleccion` (CHECK vocab), `+tipos_objeto` (array JSON multivalor validado en Python), `+entidad_contratante`, `+monto_principal/+monto_accesorio` (INTEGER **céntimos**), `+moneda`; `−tipo_objeto`, `−ambito`. Patrón **denormalización→derivado**: campos caché hoy (`proveedor`, `tipos_objeto`, montos) pasan a derivados cuando lleguen sus entidades hijas, sin invalidar lo cargado hoy.

| # | Entidad | Estado |
|---|---------|--------|
| 1 | `contratos` (cáscara general) | ✅ `edfc471` |
| 2 | `contrato_ge` (puente N:M + alcance derivado) | ✅ este commit |
| 3 | `proveedores` (registro reutilizable; UUID PK, RUC indexado NO único, consorcio en `observaciones`) | ✅ este commit |
| 4 | `items_contrato` (ítems adjudicados; id determinista `"{contrato_id}_{numero_item}"`; `proveedor_id → proveedores.id` nullable; ámbito derivado de `contrato_ge.item_id`) | ✅ este commit |
| 5 | `prestaciones` (principal/accesoria; UUID PK —numero sintético no converge—, `item_id` opcional, `tipos_objeto` multivalor; convierte `tipos_objeto`+montos del contrato en derivados read-time) | ✅ este commit |
| 6 | `garantias` (fiel cumplimiento, adelantos; UUID PK, `numero_carta_fianza` no único, `prestacion_id`/`item_id` soft; estado sin VENCIDA —derivable—; alertas de vencimiento read-time diseñadas) | ✅ este commit |
| 7 | `adendas` (UUID PK; tipo AMPLIACION_PLAZO/ADICIONAL/REDUCCION/MODIFICACION_CONVENCIONAL; monto_delta principal/accesorio con signo + plazo_delta_dias; dedup blando por (contrato_id,numero)) | ✅ este commit |
| 8 | `penalidades` (UUID PK; tipo MORA/OTRAS; monto céntimos + dias_mora; refs blandas prestacion_id/item_id; estado EN_EVALUACION/APLICADA/EXONERADA) | ✅ este commit |
| 9 | `servicios_mantenimiento` (cronograma por GE: UUID PK, ge_id FK dura, nro_servicio, fecha_programada vs fecha_ejecutada separadas, estado PROGRAMADO/EJECUTADO/CONFORME/OBSERVADO; alcance derivado del GE) | ✅ este commit |
| 10 | `adjuntos` (metadata; archivos fuera de SQLite: UUID PK, contrato_id FK, puntero polimórfico blando ref_entidad/ref_id, tipo vocab sg-valtom, ruta+sha256, filtro por ref en list) | ✅ este commit |

> **Principio de unicidad segura (regla del proyecto para entidades futuras).** En este modelo de sync la **única unicidad segura es la PK `id`**. El motor aplica creates remotos con `INSERT OR IGNORE`, así que un `UNIQUE` sobre una clave natural (RUC, código…) puede descartar en silencio una de dos filas creadas offline con `id` distintos; si esa fila es **target de una FK** (p.ej. `items_contrato.proveedor_id → proveedores.id`), deja FKs colgando → corrupción. Por eso toda clave natural única adicional debe ser **blanda** (dedup/aviso en la app), **salvo que nada le haga FK** (caso `contratos.numero`, que sí es `UNIQUE`). `proveedores.ruc` va indexado pero NO único. (También en AGENTS.md.)

---

## RamaG — Mapa geoespacial de sedes

### Objetivo

Dar ubicación (lat/long) a las sedes para trazar rutas y armar cronogramas de
viaje/mantenimiento sobre un mapa. Stack **100% gratuito, sin Google**: Leaflet +
tiles OpenStreetMap (mapa), Nominatim/OSM (geocoding), OpenRouteService (rutas).

### Decisiones de diseño tomadas

- **Coordenadas como columnas de `sedes`** (`latitud`, `longitud` REAL WGS84,
  nullable) + `geo_fuente` (`'distrito_centroide'|'nominatim'|'manual'`). Editables
  **vía API** → emiten eventos → sincronizan como cualquier campo (el motor filtra
  columnas con `PRAGMA table_info`, no se toca `events.py`/`sync_engine.py`).
- **Autoría final:** `geo_fuente='manual'` (coordenada corregida a mano) **manda** y
  nunca se re-geocodifica. Mismo principio que la reconciliación Excel-vs-BD.
- **Fuente = geocoding independiente** (Nominatim), no las coordenadas del Excel
  maestro. Las del Excel son ruidosas (ABANCAY a ~97 km del real) → se reservan solo
  para **auditoría**. Auditor fuerte = coincidencia de departamento (no la distancia).

### Pasos

#### PASO 1 — Modelo + migración ✅ COMPLETADO

`sedes` +`latitud`/`longitud`/`geo_fuente`; vista `v_sedes_completo` los expone.
`_migrate_sedes` idempotente (ALTER TABLE ADD COLUMN + recrea la vista). Validación en
`routes/sedes.py`: vocabulario de `geo_fuente`, caja de Perú (guard anti lat/long
invertida), lat+long juntas. 5 tests nuevos (137 verdes).

#### PASO 2 — Geocoding + carga ✅ COMPLETADO

470/494 sedes geocodificadas nivel localidad (`"{nombre_agencia}, {departamento}, Perú"`,
depto del Excel por margesi) y cargadas vía API como `geo_fuente='nominatim'`. 24 sin
resultado (nombres de institución/mall/oficina) → colocación manual en PASO 4.
Auditoría por departamento: 405 coinciden; los 11 "no coinciden" resultaron
geocode-correcto/Excel-mal. Riesgo residual: homónimos dentro del mismo depto
(p.ej. "28 DE JULIO") → los corrige el mapa.

#### PASO 3 — Refinamiento Nominatim ✅ (plegado en PASO 2)

#### PASO 4 — Mapa Leaflet + autoría final ✅ COMPLETADO

Vista `/mapa` (Leaflet vendorizado en `frontend/vendor/leaflet/`, tiles OSM online,
marcadores `divIcon` CSS sin PNG). Marcador por sede coloreado por macrorregión;
leyenda + panel de "Sin ubicar". **Modo edición** (toggle): arrastrar marcador →
`PUT geo_fuente='manual'` (blindado); seleccionar una sede sin coord + clic en el
mapa la ubica. Verificado con Playwright (470 marcadores, 20 tiles, 0 errores de
consola; toggle habilita drag y colocación).

#### PASO 5 — Rutas y cronogramas (OpenRouteService) ⬜ PENDIENTE

Matriz de tiempos/distancias entre sedes seleccionadas para planear la ruta de visita.
Alternativa offline futura: auto-hospedar OSRM con extracto OSM de Perú.
