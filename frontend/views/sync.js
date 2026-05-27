import { api } from "../api.js";
import { state } from "../state.js";
import { renderTable } from "../components/table.js";
import { showToast } from "../toast.js";
import { updateSyncBadge } from "../components/sidebar.js";

export async function renderSync() {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando sincronización…</div>`;
    document.getElementById("page-title").textContent = "Sincronización";

    try {
        const res = await api.get("/sync/pending");
        state.sync.events = res.events || [];
        state.sync.pendingCount = state.sync.events.length;
        updateSyncBadge();

        main.innerHTML = "";
        const header = document.createElement("div");
        header.className = "page-header";
        header.innerHTML = `<h2>Estado de sincronización</h2>`;
        main.appendChild(header);

        const card = document.createElement("div");
        card.className = "sync-card";
        card.innerHTML = `
            <div>
                <div style="font-size:0.85rem;color:var(--color-text-dark);opacity:0.8;margin-bottom:0.25rem;">Eventos pendientes</div>
                <div class="sync-card__count">${state.sync.pendingCount}</div>
            </div>
        `;
        const btnSync = document.createElement("button");
        btnSync.className = "btn btn--primary";
        btnSync.textContent = "Sincronizar";
        btnSync.addEventListener("click", () => {
            showToast("Sincronización en desarrollo", "warning");
        });
        card.appendChild(btnSync);
        main.appendChild(card);

        const wrapper = document.createElement("div");
        wrapper.className = "table-wrapper";
        const tableContainer = document.createElement("div");
        renderTable(tableContainer, [
            { header: "Event ID", key: "event_id" },
            { header: "Entidad", key: "entity" },
            { header: "Acción", key: "action" },
            { header: "Fecha", key: "created_at" },
            { header: "Estado sync", render: () => {
                const span = document.createElement("span");
                span.className = "badge--estado INOPERATIVO";
                span.textContent = "PENDIENTE";
                return span;
            }},
        ], state.sync.events, { emptyText: "No hay eventos pendientes" });
        wrapper.appendChild(tableContainer);
        main.appendChild(wrapper);
    } catch (err) {
        console.error(err);
        showToast("Error al cargar sincronización: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar sincronización</div>`;
    }
}
