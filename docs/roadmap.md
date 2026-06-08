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

#### Fase 4 — `events_error/` ⬜

**Objetivo:** persistir en `events_error/` los eventos que fallaron al procesarse (hoy van solo al log de Python y se pierden al reiniciar).

**Última prioridad.** No hay casos de error en producción que justifiquen priorizar esto antes que las fases anteriores.

**Diseño preliminar:** al capturar excepción en `apply_remote_events`, subir el JSON original a `events_error/{event_id}.json` con un campo adicional `error` y `failed_at`. La carpeta ya existe en ambos backends.

**Archivos afectados:** `backend/sync_engine.py`, `backend/storage/base.py` (nuevo método `upload_error`), ambos backends.

---

### Decisiones de diseño ya tomadas

| Decisión | Razón |
|----------|-------|
| NO archivado por consenso entre máquinas | Una máquina apagada indefinidamente congela el archivado de todas — rompe el P2P offline-first |
| NO resolución de nombre completo vía Outlook/AD | Se pospone para un instalador futuro junto con la config del link de SharePoint |
