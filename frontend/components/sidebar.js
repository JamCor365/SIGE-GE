import { navigate } from "../router.js";
import { api } from "../api.js";

const ITEMS = [
    { path: "/dashboard", label: "Dashboard", icon: dashboardIcon() },
    { path: "/grupos", label: "Grupos Electrógenos", icon: geIcon() },
    { path: "/sedes", label: "Sedes", icon: sedeIcon() },
    { path: "/macroregiones", label: "Macroregiones", icon: macroIcon() },
    { path: "/tta", label: "TTA", icon: ttaIcon() },
    { path: "/sync", label: "Sincronización", icon: syncIcon(), badge: true },
];

function dashboardIcon() {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>`;
}
function geIcon() {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>`;
}
function sedeIcon() {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18M5 21V7l8-4 8 4v14M8 21v-4a2 2 0 0 1 4 0v4"></path></svg>`;
}
function macroIcon() {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>`;
}
function ttaIcon() {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"></rect><path d="M4 12h16M12 4v16"></path></svg>`;
}
function syncIcon() {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.3"></path></svg>`;
}

export function renderSidebar() {
    const nav = document.getElementById("sidebar-nav");
    nav.innerHTML = "";
    const current = (window.location.hash || "#/").replace(/^#/, "").replace(/\/$/, "") || "/";

    ITEMS.forEach(item => {
        const a = document.createElement("a");
        a.href = `#${item.path}`;
        a.className = "sidebar__link" + (current.startsWith(item.path) ? " active" : "");
        a.innerHTML = item.icon;
        const span = document.createElement("span");
        span.className = "sidebar__link-text";
        span.textContent = item.label;
        a.appendChild(span);
        if (item.badge) {
            const badge = document.createElement("span");
            badge.className = "sidebar__badge";
            badge.id = "sidebar-sync-badge";
            badge.style.display = "none";
            badge.textContent = "0";
            a.appendChild(badge);
        }
        a.addEventListener("click", (e) => {
            e.preventDefault();
            navigate(item.path);
        });
        nav.appendChild(a);
    });
}

export async function updateSyncBadge() {
    try {
        const data = await api.get("/sync/pending");
        const total = (data.events || []).length;
        const badge = document.getElementById("sidebar-sync-badge");
        if (badge) {
            badge.textContent = String(total);
            badge.style.display = total > 0 ? "inline-block" : "none";
        }
    } catch {
        // ignorar
    }
}
