const API_BASE = "/api";

async function _fetch(method, path, body) {
    const opts = {
        method,
        headers: { "Content-Type": "application/json" },
    };
    if (body !== undefined) {
        opts.body = JSON.stringify(body);
    }
    const res = await fetch(`${API_BASE}${path}`, opts);
    const data = await res.json().catch(() => ({ status: "error", reason: "Respuesta no válida" }));
    if (!res.ok || data.status === "error") {
        const err = new Error(data.reason || `Error HTTP ${res.status}`);
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return data;
}

export const api = {
    get:  (path) => _fetch("GET", path),
    post: (path, body) => _fetch("POST", path, body),
    put:  (path, body) => _fetch("PUT", path, body),
    del:  (path) => _fetch("DELETE", path),
};
