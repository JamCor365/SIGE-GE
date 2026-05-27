export function renderPagination(container, { page, perPage, total, onChange }) {
    container.innerHTML = "";
    const totalPages = Math.max(1, Math.ceil(total / perPage));
    if (totalPages <= 1) return;

    const wrap = document.createElement("div");
    wrap.className = "pagination";

    const prev = document.createElement("button");
    prev.textContent = "‹";
    prev.disabled = page <= 1;
    prev.addEventListener("click", () => onChange(page - 1));
    wrap.appendChild(prev);

    const maxButtons = 7;
    let start = Math.max(1, page - Math.floor(maxButtons / 2));
    let end = Math.min(totalPages, start + maxButtons - 1);
    if (end - start + 1 < maxButtons) {
        start = Math.max(1, end - maxButtons + 1);
    }

    for (let i = start; i <= end; i++) {
        const btn = document.createElement("button");
        btn.textContent = String(i);
        if (i === page) btn.classList.add("active");
        btn.addEventListener("click", () => onChange(i));
        wrap.appendChild(btn);
    }

    const next = document.createElement("button");
    next.textContent = "›";
    next.disabled = page >= totalPages;
    next.addEventListener("click", () => onChange(page + 1));
    wrap.appendChild(next);

    container.appendChild(wrap);
}
