# FRONTEND_AUDIT — SIGE-GE

> Nota: las capturas en frontend_audit_shots/ son locales, regenerables con Playwright y no están versionadas.

> Auditoría del estado **actual** del frontend (HTML/CSS/JS vanilla).
> Documenta lo que HAY hoy. No contiene recomendaciones ni propuestas de rediseño.
> Capturas tomadas con la app corriendo localmente (`storage_mode: local`, datos reales:
> 466 GE activos, 494 sedes, 8 macroregiones, 154 TTA) a **1280px** (desktop) y **390px** (móvil).

---

## 1. Estructura de archivos del frontend

Todo el frontend vive en `frontend/`. No hay paso de build: los archivos se sirven tal cual.

```
frontend/
├── index.html              # Entry point — único HTML del proyecto
├── styles.css              # TODO el CSS de la app (un solo archivo)
├── app.js                  # Bootstrap: registra rutas, monta sidebar, arranca router
├── router.js               # Router por hash (#/...) con params :id
├── state.js                # Estado en memoria (objeto plano exportado)
├── api.js                  # Wrapper fetch sobre /api (get/post/put/del)
├── toast.js                # Notificaciones toast
├── components/
│   ├── sidebar.js          # Navegación lateral + badge de sync
│   ├── table.js            # renderTable() genérico
│   ├── pagination.js       # renderPagination() genérico
│   └── modal.js            # openModal() + header/footer helpers
└── views/
    ├── dashboard.js        # Vista Dashboard (tarjetas + tabla resumen)
    ├── grupos.js           # Lista + detalle + modal de Grupos Electrógenos
    ├── sedes.js            # Lista + detalle + modal de Sedes
    ├── macroregiones.js    # Lista + detalle + modal de Macroregiones
    ├── tta.js              # Lista + detalle + modal de TTA
    ├── sync.js             # Vista de sincronización
    └── _form_helpers.js    # buildFormFields() / buildPayload() compartidos por los modales
```

**Entry point:** `frontend/index.html`. Carga un único script de módulo: `<script type="module" src="app.js">`. A partir de ahí todo es JS de módulos ES (`import`/`export`), sin bundler.

**Cómo se sirve la app:** la sirve el backend (aiohttp, Python) — no hay servidor de frontend separado.
- `backend/server.py` define `FRONTEND = Path("frontend")`.
- La raíz `/` responde con `web.FileResponse(FRONTEND / "index.html")`.
- Los assets estáticos se montan con `app.router.add_static("/", FRONTEND)`.
- Corre en `host="localhost", port=8080` (configurable en `config/settings.toml`).
- Se arranca con `./start.sh` → `uv run python -m backend.server`.

No existe `package.json`, `node_modules`, ni configuración de build/lint para el frontend. Es JS de navegador puro.

---

## 2. Stack y dependencias de UI

**El frontend es 100% vanilla.** No hay frameworks ni librerías de UI de ningún tipo.

| Categoría | Estado |
|---|---|
| Framework JS (React/Vue/etc.) | **Ninguno.** Módulos ES nativos + DOM API. |
| Framework CSS (Bootstrap/Tailwind/etc.) | **Ninguno.** CSS escrito a mano en `styles.css`. |
| Librerías por CDN | **Ninguna.** No hay ni un solo `<script src>` o `<link>` externo. |
| Fuentes externas (Google Fonts, etc.) | **Ninguna.** Usa la pila de fuentes del sistema (`'Segoe UI', Arial, sans-serif`). |
| Iconos | **SVG inline** escritos a mano (estilo trazo tipo Feather). No hay librería de iconos ni icon-font. Los íconos del sidebar son strings de `<svg>` dentro de `components/sidebar.js`; el logo y el chevron de colapsar están inline en `index.html`. |
| Bundler / transpilador | **Ninguno.** Sin Webpack/Vite/Babel. El navegador carga los módulos directamente. |
| Estado | Objeto plano en memoria (`state.js`). Sin librería de estado. |
| Routing | Router propio por hash (`router.js`), ~40 líneas. |

`index.html` solo enlaza recursos locales: `<link rel="stylesheet" href="styles.css">` y `app.js`.

---

## 3. Design tokens actuales

Sí existe un sistema mínimo de tokens, definido como **variables CSS en `:root`** (`styles.css:1-17`). Es el único lugar donde se centralizan colores/medidas; el resto del CSS las consume vía `var(...)`, aunque hay también valores hardcodeados (ver notas).

### Variables CSS (`:root`)

| Variable | Valor | Uso principal |
|---|---|---|
| `--color-base` | `#1A1A2E` | Fondo del sidebar, títulos `h2/h3`, valores de tarjetas/tablas |
| `--color-surface` | `#2D2D44` | Fondo de la topbar, botón secundario |
| `--color-accent` | `#C0392B` (rojo) | Color de marca: link activo, botón primario, logo, badges, foco de inputs |
| `--color-bg` | `#F4F4F6` | Fondo general de la página, fondo de `th`, inputs estáticos |
| `--color-text-dark` | `#4A4A6A` | Texto de cuerpo por defecto |
| `--color-text-light` | `#FFFFFF` | Texto sobre superficies oscuras |
| `--color-success` | `#27AE60` (verde) | Estado OPERATIVO, tarjeta de operativos, toast success |
| `--color-warning` | `#E67E22` (naranja) | Tarjeta de alerta, toast warning, borde de `card--alert` |
| `--color-danger` | `#E74C3C` (rojo) | Estado INOPERATIVO, botón "Dar de baja", toast error, badge error |
| `--color-neutral` | `#7F8C8D` (gris) | Borde de tarjeta neutral, texto de input estático |
| `--font-main` | `'Segoe UI', Arial, sans-serif` | Toda la tipografía |
| `--radius` | `6px` | Radio de borde global |
| `--shadow` | `0 2px 8px rgba(0,0,0,0.10)` | Sombra estándar de tarjetas/tablas/topbar |
| `--sidebar-width` | `260px` | Ancho del sidebar expandido |
| `--sidebar-collapsed` | `64px` | Ancho del sidebar colapsado |

> Nota: `--color-base` aparece como `#1A1A2E` (azul muy oscuro). Es el color institucional del sidebar y de los títulos. El acento de marca es el rojo `#C0392B` (Banco de la Nación).

### Paleta de colores (incluyendo valores no tokenizados)

| Color | Dónde se usa |
|---|---|
| `#1A1A2E` | Sidebar, títulos, valores numéricos |
| `#2D2D44` | Topbar, botón secundario |
| `#C0392B` | Acento de marca (activo, primario, badges) |
| `#27AE60` / `rgba(39,174,96,0.12)` | OPERATIVO (texto y fondo suave) |
| `#E74C3C` / `rgba(231,76,60,0.12)` | INOPERATIVO / BAJA, danger |
| `#E67E22` | Warning / alertas |
| `#7F8C8D` | Neutral |
| `#F4F4F6` | Fondo de página y de `th` |
| `#FFFFFF` (`#fff`) | Fondo de tarjetas, tablas, modales, toasts |
| `#ccc` | Bordes de inputs y botón ghost (hardcodeado) |
| `#ddd` | Bordes de botones de paginación (hardcodeado) |
| `#eee` / `#f0f0f0` | Líneas divisorias de tablas y secciones de detalle (hardcodeado) |
| `#f9f9fb` | Hover de fila de tabla (hardcodeado) |
| `rgba(255,255,255,0.75 / 0.5 / 0.08 / 0.06)` | Texto y bordes dentro del sidebar oscuro |
| `rgba(0,0,0,0.45)` | Overlay del modal |

### Tipografías

| Aspecto | Valor |
|---|---|
| Familia | `'Segoe UI', Arial, sans-serif` (`--font-main`). No hay fuentes web cargadas. |
| Título de marca (`.sidebar__title`) | `1.1rem` / peso `700` / `letter-spacing: 0.04em` |
| Título de topbar (`.topbar__title`) | `1.1rem` / peso `600` |
| Encabezado de página (`.page-header h2`) | `1.35rem` / peso `700` / color base |
| Valor de tarjeta (`.card__value`) | `1.6rem` / peso `700` |
| `h3` de secciones de detalle/modal | `1rem`–`1.1rem` |
| Texto de tabla (`td`) | `0.88rem` |
| Encabezado de tabla (`th`) | `0.8rem` / peso `600` / `uppercase` / `letter-spacing: 0.03em` |
| Links del sidebar | `0.9rem` |
| Botones | `0.9rem` / peso `600` |
| Badges | `0.75rem` / peso `700` / `letter-spacing: 0.04em` |
| Labels de formulario | `0.8rem` / peso `600` |

No hay escala tipográfica formal; los tamaños se fijan ad-hoc en `rem` por componente.

### Espaciados, radios y sombras

| Token / patrón | Valor |
|---|---|
| Radio de borde | `--radius: 6px` (uniforme); `999px` para badges tipo píldora del sidebar |
| Sombra estándar | `--shadow: 0 2px 8px rgba(0,0,0,0.10)` (tarjetas, tablas, topbar) |
| Sombra de modal | `0 8px 32px rgba(0,0,0,0.25)` |
| Sombra de toast | `0 4px 16px rgba(0,0,0,0.15)` |
| Espaciados | **No tokenizados.** Se usan valores `rem`/`px` directos por componente (p. ej. `padding: 1.25rem`, `gap: 0.75rem`, `1rem`, `1.5rem`). No existe escala de spacing (`--space-*`). |
| Gap de grid de tarjetas | `1rem`, columnas `minmax(220px, 1fr)` auto-fill |
| `box-sizing` | `border-box` global; reset `margin/padding: 0` en `*` |

---

## 4. Inventario de vistas / pantallas

### Cómo se definen y renderizan las vistas

Es una **SPA con router por hash propio**, sin framework:

- `router.js` mantiene un mapa `pattern → handler`. `matchRoute()` convierte patrones tipo `/grupos/:id` en regex y extrae `params`. Escucha `window.addEventListener("hashchange", ...)`.
- `app.js` registra todas las rutas con `registerRoute(...)` y arranca con `initRouter()`.
- Cada vista es una **función `render*()` async** que hace `document.getElementById("main-content").innerHTML = ""` y construye el DOM con `document.createElement` + template strings (`innerHTML`). No hay `<template>`, ni Web Components, ni virtual DOM: cada navegación **reconstruye `#main-content` desde cero**.
- El contenedor que cambia es siempre `<main id="main-content">`. El sidebar y la topbar son persistentes.

### Rutas registradas (`app.js:35-45`)

| Hash | Handler | Vista |
|---|---|---|
| `#/` y `#/dashboard` | `renderDashboard` | Dashboard |
| `#/grupos` | `renderGruposList` | Lista de Grupos Electrógenos |
| `#/grupos/:id` | `renderGrupoDetail` | Detalle de GE |
| `#/sedes` | `renderSedesList` | Lista de Sedes |
| `#/sedes/:id` | `renderSedeDetail` | Detalle de Sede |
| `#/macroregiones` | `renderMacroregionesList` | Lista de Macroregiones |
| `#/macroregiones/:id` | `renderMacroregionDetail` | Detalle de Macroregión |
| `#/tta` | `renderTTAList` | Lista de TTA |
| `#/tta/:id` | `renderTTADetail` | Detalle de TTA |
| `#/sync` | `renderSync` | Sincronización |

Si el hash no matchea ninguna ruta, el router redirige a `#/`.

### Detalle por vista

**Dashboard** (`views/dashboard.js`) · `frontend_audit_shots/dashboard-desktop.png` · `dashboard-mobile.png`
Propósito: resumen ejecutivo. Hace 3 fetch en paralelo (`/grupos`, `/sedes`, `/sync/pending`) y agrega en cliente.
Elementos: 4 tarjetas KPI (Total GE activos, GE operativos, GE inoperativos, Total sedes activas), una 5ª tarjeta de alerta naranja **solo si hay eventos pendientes**, y una tabla "Grupos Electrógenos por Macroregión" (Macroregión / Total / Operativos / Inoperativos).

**Lista de Grupos Electrógenos** (`views/grupos.js`) · `grupos-list-desktop.png` · `grupos-list-mobile.png`
Propósito: tabla maestra de GE con filtros y alta. Carga `/grupos` y `/macroregiones`.
Elementos: header con botón "+ Nuevo GE"; barra de filtros (select de macroregión, select de estado, input de búsqueda); tabla (Cód. Margesi, Sede, Macroregión, Estado [badge], Marca Ensamblador, Tipo Transferencia, Potencia kW); paginación. Filtrado y paginación son **en cliente** (50 por página). Click en fila → detalle.

**Detalle de GE** (`views/grupos.js`) · `grupo-detail-desktop.png` · `grupo-detail-mobile.png`
Propósito: ficha completa del equipo. Carga `/grupos/:id` y `/sync/pending` (para historial).
Elementos: breadcrumb, título con código, botones Volver/Editar/Dar de baja; grid de 2 columnas con secciones (Información General, Ensamblador, Motor | Alternador, Módulo de control, Documentos [placeholder "Sin documentos adjuntos"], Historial de eventos). Es la vista más rica.

**Lista de Sedes** (`views/sedes.js`) · `sedes-list-desktop.png` · `sedes-list-mobile.png`
Igual patrón que grupos pero sin selects (solo búsqueda). Columnas: Código, Nombre Agencia, Categoría, Macroregión, Estado (ACTIVO/BAJA según `activo`).

**Detalle de Sede** (`views/sedes.js`)
Grid de 2 columnas: Información General, Ubicación | Observaciones, Sistema (Activo/Creado/Actualizado). Sin historial.

**Lista de Macroregiones** (`views/macroregiones.js`) · `macroregiones-list-desktop.png` · `macroregiones-list-mobile.png`
Tabla simple: ID, Nombre, Estado. Botón "+ Nueva Macroregión", búsqueda.

**Detalle de Macroregión** (`views/macroregiones.js`)
Una sola sección "Información" (ID, Nombre, Activo, Creado, Actualizado). Grid de 1 columna en la práctica.

**Lista de TTA** (`views/tta.js`) · `tta-list-desktop.png` · `tta-list-mobile.png`
Tableros de Transferencia Automática. Columnas: Cód. Margesi, Sede, Macroregión, Marca, Modelo, Fases, Estado. Botón "+ Nuevo TTA", búsqueda.

**Detalle de TTA** (`views/tta.js`)
Grid de 2 columnas: Información General, Especificaciones | Observaciones, Sistema.

**Sincronización** (`views/sync.js`) · `sync-desktop.png` · `sync-mobile.png`
Propósito: estado de sincronización con el almacenamiento (SharePoint/local). Carga `/sync/pending`.
Elementos: una `sync-card` con dos contadores ("Cambios locales sin subir", "Eventos en SharePoint") y un botón "Sincronizar" (deshabilitado y con texto "Conectando…" si el backend está `initializing`); debajo, tabla de eventos pendientes (Event ID, Entidad, Acción, Fecha, Máquina, Estado=badge "PENDIENTE"). Al sincronizar (`POST /sync/apply`) muestra un toast con el resultado.

**Modal de alta/edición** (compartido) · `grupo-modal-desktop.png`
No es una ruta: es un overlay. Lo abren los botones "+ Nuevo…" y "Editar". Formulario en grid de 2 columnas generado por `_form_helpers.js`. Ver sección 5.

---

## 5. Componentes / patrones reutilizables

Hay un conjunto pequeño de componentes-función reutilizables (en `components/`) más patrones repetidos copiados entre vistas (cada vista tiene su propio `buildSection()` casi idéntico, y su propio bloque de botones de detalle).

### Layout shell (sidebar + topbar) — `index.html`
Estructura fija que envuelve todas las vistas: `#app` (flex) → `aside.sidebar` + `.main-wrapper` (`header.topbar` + `main#main-content`).

```html
<div id="app">
  <aside id="sidebar" class="sidebar">
    <div class="sidebar__brand">
      <svg class="sidebar__logo" ...><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
      <span class="sidebar__title">SIGE-GE</span>
    </div>
    <nav class="sidebar__nav" id="sidebar-nav"></nav>
    <button id="sidebar-toggle" class="sidebar__toggle" aria-label="Colapsar sidebar">…</button>
  </aside>
  <div class="main-wrapper">
    <header class="topbar">
      <span id="page-title" class="topbar__title">Dashboard</span>
      <span id="status-badge" class="badge badge--loading">conectando…</span>
    </header>
    <main id="main-content"></main>
  </div>
</div>
```

### Navegación (sidebar) — `components/sidebar.js`
Construida en JS desde un array `ITEMS` (path, label, icon SVG, badge opcional). El ítem de Sincronización tiene un badge rojo con el nº de eventos pendientes (`updateSyncBadge()` consulta `/sync/pending`). Colapsable a 64px con el botón inferior (toggle de la clase `.collapsed`). Markup generado por ítem:

```html
<a href="#/grupos" class="sidebar__link active">
  <svg ...></svg>
  <span class="sidebar__link-text">Grupos Electrógenos</span>
  <!-- solo en Sincronización: -->
  <span class="sidebar__badge" id="sidebar-sync-badge">3</span>
</a>
```

### Tabla — `components/table.js`
`renderTable(container, columns, rows, options)`. Cada columna soporta `key`, `accessor(row)` o `render(row)→Node`. `options.onRowClick` añade clase `.clickable`; `options.emptyText` muestra un `.empty-state`. Markup resultante:

```html
<table>
  <thead><tr><th>CÓD. MARGESI</th><th>SEDE</th> … </tr></thead>
  <tbody>
    <tr class="clickable">
      <td>577665</td>
      <td>AG-0001 — Sede Lima Norte</td>
      <td>MR LIMA</td>
      <td><span class="badge--estado OPERATIVO">OPERATIVO</span></td>
      …
    </tr>
  </tbody>
</table>
```

### Paginación — `components/pagination.js`
`renderPagination(container, {page, perPage, total, onChange})`. Botones ‹ › + hasta 7 números, ventana deslizante, oculta si hay ≤1 página.

```html
<div class="pagination">
  <button>‹</button>
  <button class="active">4</button>
  <button>5</button>
  <button>›</button>
</div>
```

### Formularios — `views/_form_helpers.js`
Los modales de alta/edición no escriben los campos a mano: `buildFormFields(container, fields, record, isEdit)` los genera desde un descriptor (`name`, `label`, `type`, `required`, `options`, `fullWidth`, `step`). `buildPayload()` arma el body para la API. Regla de negocio visible en UI: el campo `id` se muestra como **input editable al crear** y como **texto estático al editar** (`.form-value-static`). Markup por campo:

```html
<div class="form-group">
  <label>Sede *</label>
  <select name="sede_id">
    <option value="">— Seleccionar —</option>
    <option value="1">AG-0001 — Sede Lima Norte</option>
  </select>
</div>
<div class="form-group full-width">
  <label>Observaciones</label>
  <textarea name="observaciones" rows="3"></textarea>
</div>
```

### Modal — `components/modal.js`
`openModal(contentNode)` muestra el overlay `#modal-overlay`, inyecta `.modal` centrada, y devuelve `{close, modal}`. Cierra con Escape o click en el overlay. Helpers `createModalHeader(title, onClose)` y `createModalFooter(actions)`.

```html
<div class="modal">
  <div class="modal__header">
    <h3>Nuevo Grupo Electrógeno</h3>
    <button class="modal__close">×</button>
  </div>
  <div class="modal__body">
    <form class="form-grid" id="grupo-form"> … <div class="modal__footer">…</div></form>
  </div>
</div>
```

### Botones — `styles.css`
Clase base `.btn` + modificadores. Variantes presentes:

```html
<button class="btn btn--primary">+ Nuevo GE</button>     <!-- rojo acento -->
<button class="btn btn--secondary">Cancelar</button>     <!-- surface oscuro -->
<button class="btn btn--danger">Dar de baja</button>     <!-- rojo danger -->
<button class="btn btn--ghost">Volver</button>           <!-- transparente, borde gris -->
```

### Badges / estados
Dos familias:
- **Topbar** (`.badge` + `--loading/--ok/--error/--warning`): estado de conexión (`v1.0.0 · local` en verde cuando OK).
- **Estado de fila** (`.badge--estado` + clase del valor): `.OPERATIVO` (verde suave) / `.INOPERATIVO` (rojo suave). Sedes/Macroregiones reutilizan estas clases para ACTIVO/BAJA; Sync las reutiliza para "PENDIENTE".

```html
<span class="badge badge--ok">v1.0.0 · local</span>
<span class="badge--estado OPERATIVO">OPERATIVO</span>
<span class="badge--estado INOPERATIVO">INOPERATIVO</span>
```

### Toast — `toast.js`
`showToast(message, type)`. Crea un `.toast` (success/error/warning/info) en `#toast-container` (arriba-derecha, fixed), con animación de entrada/salida (`@keyframes toastIn/toastOut`) y auto-remove a los 4s.

### Tarjetas (KPI) — Dashboard
```html
<div class="card card--accent-success">
  <div class="card__label">GE operativos</div>
  <div class="card__value">455</div>
</div>
```

### Detalle (secciones campo/valor)
Patrón repetido en cada detalle (`buildSection` local a cada vista):
```html
<div class="detail-section">
  <h3>Información General</h3>
  <div class="detail-field">
    <span class="detail-field__label">Estado</span>
    <span class="detail-field__value">OPERATIVO</span>
  </div>
</div>
```

### Breadcrumb
```html
<div class="breadcrumb">
  <a href="#/dashboard">Dashboard</a> <span>/</span>
  <a href="#/grupos">Grupos Electrógenos</a> <span>/</span> <span>577665</span>
</div>
```

---

## 6. Layout y responsividad

### Estructura de layout
- **Shell:** `#app` es `display:flex` a pantalla completa (`min-height:100vh`). Sidebar de ancho fijo (`flex-shrink:0`) + `.main-wrapper` flexible (`flex:1`, columna). Layout clásico de **Flexbox**, sin floats.
- **Topbar:** flex en fila, `space-between` (título a la izquierda, badge de estado a la derecha).
- **Grids:** se usa CSS Grid en tres lugares:
  - `.cards-grid`: `repeat(auto-fill, minmax(220px, 1fr))` — las tarjetas KPI **sí** se reflujan según el ancho.
  - `.detail-grid`: `1fr 1fr` (2 columnas) en los detalles.
  - `.form-grid`: `minmax(0,1fr) minmax(0,1fr)` en los formularios de modal.
- **Tablas:** `width:100%`, `border-collapse`. Sin scroll horizontal propio (`.table-wrapper` usa `overflow:hidden`).

### Media queries existentes
Hay **exactamente 3** media queries en todo el CSS, todas para colapsar grids a 1 columna:

| Breakpoint | Qué hace |
|---|---|
| `@media (max-width: 900px)` | `.detail-grid` → 1 columna |
| `@media (max-width: 600px)` | `.form-grid` → 1 columna |
| `@media (max-width: 600px)` | (mismo) formularios a una columna |

### Estado real de responsividad
**La app NO es responsive en el sentido completo.** No está pensada para móvil. Evidencia concreta (capturas a 390px):

- El **sidebar mantiene su ancho fijo de 260px** en cualquier viewport. No hay media query que lo colapse, oculte o convierta en menú hamburguesa en móvil. A 390px, el sidebar se come ~⅔ de la pantalla.
- Las **tablas no tienen scroll horizontal** ni layout alternativo: en móvil el contenido queda **cortado** (en `grupos-list-mobile.png` solo se ve la 1ª columna; el resto queda fuera de pantalla, oculto por `overflow:hidden` del wrapper).
- La topbar y el contenido se comprimen; los títulos se parten en varias líneas.

Lo único que sí se adapta: el grid de tarjetas KPI (por `auto-fill`) y los grids de detalle/formulario (por las 2 media queries), pero esas mejoras quedan eclipsadas por el sidebar fijo y las tablas desbordadas.

> En resumen: **diseñado para escritorio (~1280px)**. A 390px funciona pero se ve roto. Comparar `*-desktop.png` vs `*-mobile.png` en `frontend_audit_shots/`.

---

## 7. Walkthrough en lenguaje natural

**Al abrir la app** (`http://localhost:8080`, redirige a `#/dashboard`):

Ves una interfaz de dos zonas. A la izquierda, una **barra lateral azul muy oscura** con el logo (un rayo rojo) y el nombre "SIGE-GE" arriba, y debajo seis enlaces con íconos: Dashboard, Grupos Electrógenos, Sedes, Macroregiones, TTA y Sincronización. El enlace activo se resalta en **rojo**. Abajo del todo hay una flecha "‹" para colapsar la barra a solo íconos. Arriba, una **barra superior** azul-grisácea muestra el título de la página actual y, a la derecha, una etiqueta verde `v1.0.0 · local` indicando que hay conexión con el backend (si falla, se pone roja "sin conexión").

El **Dashboard** te recibe con cuatro tarjetas blancas grandes con un borde superior de color: "Total GE activos" (466), "GE operativos" (455, verde), "GE inoperativos" (11, rojo) y "Total sedes activas" (494). Si hubiera cambios sin sincronizar, aparecería una quinta tarjeta de alerta naranja. Debajo, una tabla "Grupos Electrógenos por Macroregión" lista cada macroregión (MR LIMA, NORTE ACTUALIZADO, MR II - TRUJILLO, etc.) con su total de equipos y cuántos están operativos/inoperativos.

Si haces click en **"Grupos Electrógenos"**, llegas a la pantalla más usada: un título, un botón rojo **"+ Nuevo GE"** arriba a la derecha, y una **barra de filtros** (desplegable de macroregión, desplegable de estado OPERATIVO/INOPERATIVO, y un buscador por código o sede). Debajo, una tabla blanca lista todos los grupos: código Margesi, sede, macroregión, un **badge verde "OPERATIVO" o rojo "INOPERATIVO"**, marca, tipo de transferencia y potencia. Al pie, paginación numerada (50 por página). Filtrar o buscar refresca la tabla al instante sin recargar.

Al **hacer click en una fila**, entras al **detalle del equipo**. Arriba hay migas de pan (Dashboard / Grupos Electrógenos / 577665) y tres botones: "Volver" (gris), "Editar" (rojo) y "Dar de baja" (rojo intenso). El cuerpo es una rejilla de dos columnas con fichas: Información General, Ensamblador, Motor en la izquierda; Alternador, Módulo de control, un recuadro "Documentos" que hoy dice "Sin documentos adjuntos", y un "Historial de eventos" en la derecha. Cada ficha es una lista de pares etiqueta-valor (Estado: OPERATIVO, Potencia kW: 55, Marca motor: CATERPILLAR…). Si pulsas **"Dar de baja"**, sale un `confirm()` del navegador y, si aceptas, una notificación verde aparece arriba a la derecha y vuelves a la lista.

Si pulsas **"+ Nuevo GE"** o **"Editar"**, la pantalla se oscurece y aparece un **modal centrado** con un formulario de dos columnas: selección de sede, código, estado, año, potencia, fases, y todos los campos de ensamblador/motor/alternador/módulo, más un campo de observaciones que ocupa el ancho completo. Abajo, "Cancelar" y "Crear GE / Guardar cambios". Al crear se pide el ID como campo editable; al editar, el ID aparece como texto fijo no modificable. Se cierra con la X, con Escape o haciendo click fuera.

Las pantallas de **Sedes**, **Macroregiones** y **TTA** se ven y se usan igual que Grupos (lista filtrable + detalle + modal), cambiando solo las columnas y campos. Sedes y Macroregiones muestran el estado como **ACTIVO/BAJA**; TTA, como OPERATIVO/INOPERATIVO. Macroregión es la más simple (solo ID y nombre).

Por último, **"Sincronización"** muestra una tarjeta con dos cifras grandes — "Cambios locales sin subir" y "Eventos en SharePoint" — y un botón rojo **"Sincronizar"** (que dice "Conectando…" y aparece deshabilitado mientras el backend arranca). Debajo, una tabla de eventos pendientes; cuando no hay nada, dice "No hay eventos locales pendientes". Al sincronizar, sale una notificación con el resultado (cuántos cambios se subieron/aplicaron). El ícono de Sincronización en el sidebar lleva un **badge rojo** con el número de cambios pendientes cuando los hay.

**En móvil (390px)** la experiencia se degrada: la barra lateral sigue ocupando 260px fijos (no se colapsa ni se vuelve hamburguesa), por lo que come buena parte del ancho, y las tablas se cortan porque no tienen scroll horizontal. La app es claramente de **escritorio**.

---

## Anexo: capturas

Todas en `./frontend_audit_shots/` (PNG, `fullPage`). Generadas con Playwright + Chromium sobre la app real en `localhost:8080`.

| Vista | Desktop 1280px | Móvil 390px |
|---|---|---|
| Dashboard | `dashboard-desktop.png` | `dashboard-mobile.png` |
| Grupos (lista) | `grupos-list-desktop.png` | `grupos-list-mobile.png` |
| Grupo (detalle) | `grupo-detail-desktop.png` | `grupo-detail-mobile.png` |
| Grupo (modal alta) | `grupo-modal-desktop.png` | — |
| Sedes (lista) | `sedes-list-desktop.png` | `sedes-list-mobile.png` |
| Macroregiones (lista) | `macroregiones-list-desktop.png` | `macroregiones-list-mobile.png` |
| TTA (lista) | `tta-list-desktop.png` | `tta-list-mobile.png` |
| Sincronización | `sync-desktop.png` | `sync-mobile.png` |
