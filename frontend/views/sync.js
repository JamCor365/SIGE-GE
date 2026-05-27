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

        const isInitializing = res.status === "initializing";
        const pendingStorage = res.pending_storage ?? -1;

        const card = document.createElement("div");
        card.className = "sync-card";
        card.innerHTML = `
            <div style="display:flex;gap:2rem;">
                <div>
                    <div style="font-size:0.85rem;color:var(--color-text-dark);opacity:0.8;margin-bottom:0.25rem;">Cambios locales sin subir</div>
                    <div class="sync-card__count">${state.sync.pendingCount}</div>
                </div>
                <div>
                    <div style="font-size:0.85rem;color:var(--color-text-dark);opacity:0.8;margin-bottom:0.25rem;">Eventos en SharePoint</div>
                    <div class="sync-card__count">${isInitializing ? "…" : pendingStorage < 0 ? "—" : pendingStorage}</div>
                </div>
            </div>
        `;

        const btnSync = document.createElement("button");
        btnSync.className = "btn btn--primary";
        btnSync.textContent = isInitializing ? "Conectando…" : "Sincronizar";
        btnSync.disabled = isInitializing;
        btnSync.addEventListener("click", () => _doSync(btnSync));
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
            { header: "Máquina", key: "machine" },
            { header: "Estado", render: () => {
                const span = document.createElement("span");
                span.className = "badge--estado INOPERATIVO";
                span.textContent = "PENDIENTE";
                return span;
            }},
        ], state.sync.events, { emptyText: "No hay eventos locales pendientes" });
        wrapper.appendChild(tableContainer);
        main.appendChild(wrapper);

    } catch (err) {
        console.error(err);
        showToast("Error al cargar sincronización: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar sincronización</div>`;
    }
}

async function _doSync(btn) {
    btn.disabled = true;
    btn.textContent = "Sincronizando…";
    try {
        const res = await api.post("/sync/apply");
        const { applied, skipped, errors } = res;
        if (errors.length > 0) {
            showToast(`Sync completado con errores: ${applied} aplicados, ${errors.length} errores`, "warning");
        } else if (applied === 0) {
            showToast("Todo al día — no hay cambios nuevos de otras máquinas", "success");
        } else {
            showToast(`${applied} cambio(s) aplicado(s) desde otras máquinas`, "success");
        }
        await renderSync();
    } catch (err) {
        showToast("Error al sincronizar: " + (err.message || ""), "error");
        btn.disabled = false;
        btn.textContent = "Sincronizar";
    }
}
