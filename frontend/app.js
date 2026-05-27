import { initRouter, registerRoute, navigate } from "./router.js";
import { renderSidebar, updateSyncBadge } from "./components/sidebar.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderGruposList, renderGrupoDetail } from "./views/grupos.js";
import { renderSedesList, renderSedeDetail } from "./views/sedes.js";
import { renderMacroregionesList, renderMacroregionDetail } from "./views/macroregiones.js";
import { renderTTAList, renderTTADetail } from "./views/tta.js";
import { renderSync } from "./views/sync.js";
import { showToast } from "./toast.js";

const badge = document.getElementById("status-badge");

async function checkStatus() {
    try {
        const res = await fetch("/api/status");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        badge.textContent = `v${data.version} · ${data.storage_mode}`;
        badge.className = "badge badge--ok";
    } catch (err) {
        console.error("Error al consultar /api/status:", err);
        badge.textContent = "sin conexión";
        badge.className = "badge badge--error";
    }
}

function updateActiveSidebar() {
    const current = (window.location.hash || "#/").replace(/^#/, "").replace(/\/$/, "") || "/";
    document.querySelectorAll(".sidebar__link").forEach(a => {
        const href = a.getAttribute("href").replace(/^#/, "").replace(/\/$/, "") || "/";
        a.classList.toggle("active", current.startsWith(href) && href !== "/" || current === href);
    });
}

registerRoute("/", () => { renderDashboard(); updateActiveSidebar(); });
registerRoute("/dashboard", () => { renderDashboard(); updateActiveSidebar(); });
registerRoute("/grupos", () => { renderGruposList(); updateActiveSidebar(); });
registerRoute("/grupos/:id", (params) => { renderGrupoDetail(params); updateActiveSidebar(); });
registerRoute("/sedes", () => { renderSedesList(); updateActiveSidebar(); });
registerRoute("/sedes/:id", (params) => { renderSedeDetail(params); updateActiveSidebar(); });
registerRoute("/macroregiones", () => { renderMacroregionesList(); updateActiveSidebar(); });
registerRoute("/macroregiones/:id", (params) => { renderMacroregionDetail(params); updateActiveSidebar(); });
registerRoute("/tta", () => { renderTTAList(); updateActiveSidebar(); });
registerRoute("/tta/:id", (params) => { renderTTADetail(params); updateActiveSidebar(); });
registerRoute("/sync", () => { renderSync(); updateActiveSidebar(); });

renderSidebar();
initRouter();
checkStatus();
updateSyncBadge();

// Sidebar toggle
document.getElementById("sidebar-toggle").addEventListener("click", () => {
    document.getElementById("sidebar").classList.toggle("collapsed");
});

// Exponer navigate global para conveniencia interna
window._navigate = navigate;
