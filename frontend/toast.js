export function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => {
        el.classList.add("removing");
        el.addEventListener("animationend", () => el.remove());
    }, 4000);
}
