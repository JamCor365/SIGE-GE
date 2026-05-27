export function openModal(contentNode) {
    const overlay = document.getElementById("modal-overlay");
    overlay.innerHTML = "";
    overlay.style.display = "block";

    const modal = document.createElement("div");
    modal.className = "modal";
    modal.appendChild(contentNode);
    overlay.appendChild(modal);

    function close() {
        overlay.style.display = "none";
        overlay.innerHTML = "";
        document.removeEventListener("keydown", onKey);
        overlay.removeEventListener("click", onClick);
    }

    function onKey(e) {
        if (e.key === "Escape") close();
    }
    function onClick(e) {
        if (e.target === overlay) close();
    }

    document.addEventListener("keydown", onKey);
    overlay.addEventListener("click", onClick);

    return { close, modal };
}

export function createModalHeader(title, onClose) {
    const header = document.createElement("div");
    header.className = "modal__header";
    const h3 = document.createElement("h3");
    h3.textContent = title;
    header.appendChild(h3);
    const btn = document.createElement("button");
    btn.className = "modal__close";
    btn.innerHTML = "&times;";
    btn.addEventListener("click", onClose);
    header.appendChild(btn);
    return header;
}

export function createModalFooter(actions) {
    const footer = document.createElement("div");
    footer.className = "modal__footer";
    actions.forEach(a => footer.appendChild(a));
    return footer;
}
