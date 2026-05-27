const routes = {};

export function registerRoute(pattern, handler) {
    routes[pattern] = handler;
}

function matchRoute(hash) {
    const clean = hash.replace(/^#/, "").replace(/\/$/, "") || "/";
    for (const [pattern, handler] of Object.entries(routes)) {
        const regex = new RegExp(
            "^" + pattern.replace(/:([^/]+)/g, "([^/]+)") + "$"
        );
        const m = clean.match(regex);
        if (m) {
            const params = {};
            const keys = (pattern.match(/:([^/]+)/g) || []).map(k => k.slice(1));
            keys.forEach((k, i) => { params[k] = m[i + 1]; });
            return { handler, params };
        }
    }
    return null;
}

export function initRouter() {
    function onChange() {
        const hash = window.location.hash || "#/";
        const matched = matchRoute(hash);
        if (matched) {
            matched.handler(matched.params);
        } else {
            window.location.hash = "#/";
        }
    }
    window.addEventListener("hashchange", onChange);
    onChange();
}

export function navigate(path) {
    window.location.hash = `#${path}`;
}
