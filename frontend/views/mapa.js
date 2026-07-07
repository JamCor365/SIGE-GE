import { api } from "../api.js";
import { showToast } from "../toast.js";

// Paleta por macrorregión (id 0..6 en la data del Banco).
const MACRO_COLORS = {
    0: "#e6194B", 1: "#3cb44b", 2: "#4363d8", 3: "#f58231",
    4: "#911eb4", 5: "#42d4f4", 6: "#f032e6",
};
const FALLBACK_COLOR = "#808080";
const PERU_CENTER = [-9.19, -75.02];
const PERU_ZOOM = 5;

let map = null;          // instancia Leaflet (persistente entre renders)
const markers = new Map(); // sede_id -> L.marker
let editMode = false;
let placingSedeId = null;  // sede sin coord esperando click en el mapa

function macroColor(id) {
    return MACRO_COLORS[id] ?? FALLBACK_COLOR;
}

function makeIcon(color, fuente) {
    const manual = fuente === "manual" ? " sede-dot--manual" : "";
    return L.divIcon({
        className: "sede-marker",
        html: `<span class="sede-dot${manual}" style="background:${color}"></span>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
    });
}

function popupHtml(s) {
    const fuente = s.geo_fuente === "manual"
        ? '<b style="color:#166534">manual (confirmada)</b>'
        : (s.geo_fuente || "—");
    return `<div class="sede-popup">
        <b>${s.nombre_agencia || s.codigo}</b><br>
        <span class="muted">${s.codigo || ""} · ${s.macroregion_nombre || ""}</span><br>
        <span class="muted">Fuente:</span> ${fuente}<br>
        <span class="muted">${(s.latitud ?? "").toString().slice(0, 9)}, ${(s.longitud ?? "").toString().slice(0, 9)}</span>
    </div>`;
}

async function persistCoord(sede, lat, lng) {
    // Toda corrección manual manda: geo_fuente='manual' (blindado de re-geocode).
    await api.put(`/sedes/${sede.id}`, {
        latitud: Number(lat.toFixed(7)),
        longitud: Number(lng.toFixed(7)),
        geo_fuente: "manual",
    });
    sede.latitud = Number(lat.toFixed(7));
    sede.longitud = Number(lng.toFixed(7));
    sede.geo_fuente = "manual";
}

function addMarker(sede) {
    const color = macroColor(sede.macroregion_id);
    const marker = L.marker([sede.latitud, sede.longitud], {
        icon: makeIcon(color, sede.geo_fuente),
        draggable: editMode,
        title: sede.nombre_agencia || sede.codigo,
    });
    marker.bindPopup(popupHtml(sede));
    marker.on("dragend", async (e) => {
        const { lat, lng } = e.target.getLatLng();
        try {
            await persistCoord(sede, lat, lng);
            marker.setIcon(makeIcon(color, "manual"));
            marker.setPopupContent(popupHtml(sede));
            showToast(`${sede.nombre_agencia}: ubicación confirmada (manual)`, "success");
        } catch (err) {
            showToast("Error al guardar: " + (err.message || ""), "error");
            marker.setLatLng([sede.latitud, sede.longitud]); // revertir
        }
    });
    marker.addTo(map);
    markers.set(sede.id, marker);
}

export async function renderMapa() {
    const main = document.getElementById("main-content");
    document.getElementById("page-title").textContent = "Mapa de Sedes";

    if (typeof L === "undefined") {
        main.innerHTML = `<div class="empty-state">No se pudo cargar Leaflet (vendor/leaflet).</div>`;
        return;
    }

    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando mapa…</div>`;

    let sedes, macros;
    try {
        const [sedesRes, macroRes] = await Promise.all([
            api.get("/sedes"), api.get("/macroregiones"),
        ]);
        sedes = (sedesRes.data || []).filter(s => s.activo === 1);
        macros = (macroRes.data || []).filter(m => m.activo === 1);
    } catch (err) {
        showToast("Error al cargar sedes: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar el mapa</div>`;
        return;
    }

    const conCoord = sedes.filter(s => s.latitud != null && s.longitud != null);
    const sinCoord = sedes.filter(s => s.latitud == null || s.longitud == null);

    // Estructura: header + toolbar + (mapa | panel)
    main.innerHTML = "";
    const header = document.createElement("div");
    header.className = "page-header";
    header.innerHTML = `<h2>Mapa de Sedes</h2>
        <span class="map-counters">
            <span class="chip chip--ok">${conCoord.length} ubicadas</span>
            <span class="chip chip--warn">${sinCoord.length} sin ubicar</span>
        </span>`;
    main.appendChild(header);

    const toolbar = document.createElement("div");
    toolbar.className = "map-toolbar";
    const btnEdit = document.createElement("button");
    btnEdit.className = "btn btn--secondary";
    btnEdit.textContent = "✎ Editar ubicaciones";
    toolbar.appendChild(btnEdit);
    const editHint = document.createElement("span");
    editHint.className = "map-hint";
    toolbar.appendChild(editHint);
    main.appendChild(toolbar);

    const layout = document.createElement("div");
    layout.className = "map-layout";
    const mapEl = document.createElement("div");
    mapEl.id = "map";
    mapEl.className = "map-canvas";
    layout.appendChild(mapEl);

    const panel = document.createElement("aside");
    panel.className = "map-panel";
    layout.appendChild(panel);
    main.appendChild(layout);

    // Init Leaflet (nueva instancia cada render; el div es nuevo)
    map = L.map(mapEl, { center: PERU_CENTER, zoom: PERU_ZOOM });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap", maxZoom: 18,
    }).addTo(map);
    // Leaflet mide mal el contenedor si se creó oculto; recalcular al siguiente tick.
    setTimeout(() => map.invalidateSize(), 60);

    markers.clear();
    conCoord.forEach(addMarker);

    // --- Leyenda de macrorregiones ---
    const legend = document.createElement("div");
    legend.className = "map-legend";
    legend.innerHTML = "<h4>Macrorregiones</h4>";
    macros.forEach(m => {
        const row = document.createElement("div");
        row.className = "legend-row";
        row.innerHTML = `<span class="legend-dot" style="background:${macroColor(m.id)}"></span>${m.nombre}`;
        legend.appendChild(row);
    });
    const manualNote = document.createElement("div");
    manualNote.className = "legend-row legend-note";
    manualNote.innerHTML = `<span class="legend-dot legend-dot--manual"></span>Corregida a mano (manual)`;
    legend.appendChild(manualNote);
    panel.appendChild(legend);

    // --- Lista de sedes sin ubicar ---
    const sinBox = document.createElement("div");
    sinBox.className = "map-sincoord";
    renderSinCoord();
    panel.appendChild(sinBox);

    function renderSinCoord() {
        sinBox.innerHTML = `<h4>Sin ubicar (${sinCoord.length})</h4>`;
        if (!sinCoord.length) {
            sinBox.innerHTML += `<p class="muted">Todas las sedes están ubicadas 🎉</p>`;
            return;
        }
        const hint = document.createElement("p");
        hint.className = "muted map-sincoord__hint";
        hint.textContent = editMode
            ? "Elige una y haz clic en el mapa para ubicarla."
            : "Activa «Editar ubicaciones» para colocarlas.";
        sinBox.appendChild(hint);
        sinCoord.forEach(s => {
            const item = document.createElement("button");
            item.className = "sincoord-item" + (placingSedeId === s.id ? " active" : "");
            item.textContent = s.nombre_agencia || s.codigo;
            item.disabled = !editMode;
            item.addEventListener("click", () => {
                placingSedeId = placingSedeId === s.id ? null : s.id;
                editHint.textContent = placingSedeId
                    ? `Coloca «${s.nombre_agencia}»: haz clic en su ubicación en el mapa.`
                    : "";
                renderSinCoord();
            });
            sinBox.appendChild(item);
        });
    }

    // --- Click en el mapa para colocar una sede sin coord ---
    map.on("click", async (e) => {
        if (!editMode || placingSedeId == null) return;
        const sede = sinCoord.find(s => s.id === placingSedeId);
        if (!sede) return;
        try {
            await persistCoord(sede, e.latlng.lat, e.latlng.lng);
            addMarker(sede);
            markers.get(sede.id).openPopup();
            const idx = sinCoord.indexOf(sede);
            if (idx >= 0) sinCoord.splice(idx, 1);
            placingSedeId = null;
            editHint.textContent = "";
            showToast(`${sede.nombre_agencia}: ubicada (manual)`, "success");
            // actualizar contadores
            header.querySelector(".chip--ok").textContent = `${markers.size} ubicadas`;
            header.querySelector(".chip--warn").textContent = `${sinCoord.length} sin ubicar`;
            renderSinCoord();
        } catch (err) {
            showToast("Error al ubicar: " + (err.message || ""), "error");
        }
    });

    // --- Toggle de modo edición ---
    btnEdit.addEventListener("click", () => {
        editMode = !editMode;
        btnEdit.classList.toggle("btn--primary", editMode);
        btnEdit.classList.toggle("btn--secondary", !editMode);
        btnEdit.textContent = editMode ? "✓ Editando (clic para salir)" : "✎ Editar ubicaciones";
        editHint.textContent = editMode ? "Arrastra un punto para corregirlo." : "";
        mapEl.classList.toggle("map-canvas--editing", editMode);
        markers.forEach(mk => editMode ? mk.dragging.enable() : mk.dragging.disable());
        if (!editMode) { placingSedeId = null; }
        renderSinCoord();
    });
}
