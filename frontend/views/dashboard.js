import { api } from "../api.js";
import { state } from "../state.js";
import { renderTable } from "../components/table.js";
import { updateSyncBadge } from "../components/sidebar.js";
import { showToast } from "../toast.js";

export async function renderDashboard() {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando dashboard…</div>`;
    document.getElementById("page-title").textContent = "Dashboard";

    try {
        const [gruposRes, sedesRes, syncRes] = await Promise.all([
            api.get("/grupos"),
            api.get("/sedes"),
            api.get("/sync/pending"),
        ]);

        const grupos = gruposRes.data || [];
        const sedes = sedesRes.data || [];
        const syncData = syncRes;

        const activos = grupos.filter(g => g.activo === 1);
        const operativos = activos.filter(g => g.estado === "OPERATIVO");
        const inoperativos = activos.filter(g => g.estado === "INOPERATIVO");
        const sedesActivas = sedes.filter(s => s.activo === 1);

        state.dashboard.stats = {
            totalGE: activos.length,
            operativos: operativos.length,
            inoperativos: inoperativos.length,
            totalSedes: sedesActivas.length,
        };
        state.sync.events = syncData.events || [];
        state.sync.pendingCount = state.sync.events.length;

        // Agrupar GE por macroregión
        const porMacro = {};
        activos.forEach(g => {
            const nombre = g.macroregion || "Sin macroregión";
            porMacro[nombre] = (porMacro[nombre] || 0) + 1;
        });
        state.dashboard.gruposPorMacro = Object.entries(porMacro).map(([nombre, total]) => ({
            macroregion: nombre,
            total,
            operativos: activos.filter(g => g.macroregion === nombre && g.estado === "OPERATIVO").length,
            inoperativos: activos.filter(g => g.macroregion === nombre && g.estado === "INOPERATIVO").length,
        }));

        updateSyncBadge();

        main.innerHTML = "";
        const cards = document.createElement("div");
        cards.className = "cards-grid";
        cards.innerHTML = `
            <div class="card card--accent-base">
                <div class="card__label">Total GE activos</div>
                <div class="card__value">${state.dashboard.stats.totalGE}</div>
            </div>
            <div class="card card--accent-success">
                <div class="card__label">GE operativos</div>
                <div class="card__value">${state.dashboard.stats.operativos}</div>
            </div>
            <div class="card card--accent-danger">
                <div class="card__label">GE inoperativos</div>
                <div class="card__value">${state.dashboard.stats.inoperativos}</div>
            </div>
            <div class="card card--accent-neutral">
                <div class="card__label">Total sedes activas</div>
                <div class="card__value">${state.dashboard.stats.totalSedes}</div>
            </div>
        `;
        if (state.sync.pendingCount > 0) {
            const alert = document.createElement("div");
            alert.className = "card card--alert";
            alert.innerHTML = `
                <div class="card__label">⚠ Eventos pendientes de sincronización</div>
                <div class="card__value">${state.sync.pendingCount}</div>
            `;
            cards.appendChild(alert);
        }
        main.appendChild(cards);

        const section = document.createElement("div");
        section.className = "table-wrapper";
        const title = document.createElement("h3");
        title.textContent = "Grupos Electrógenos por Macroregión";
        title.style.padding = "0.9rem 1rem";
        title.style.fontSize = "1rem";
        title.style.color = "var(--color-base)";
        section.appendChild(title);
        const tableContainer = document.createElement("div");
        renderTable(tableContainer, [
            { header: "Macroregión", key: "macroregion" },
            { header: "Total", key: "total" },
            { header: "Operativos", key: "operativos" },
            { header: "Inoperativos", key: "inoperativos" },
        ], state.dashboard.gruposPorMacro, { emptyText: "No hay grupos electrógenos" });
        section.appendChild(tableContainer);
        main.appendChild(section);
    } catch (err) {
        console.error(err);
        showToast("Error al cargar dashboard: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar dashboard</div>`;
    }
}
