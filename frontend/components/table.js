export function renderTable(container, columns, rows, options = {}) {
    container.innerHTML = "";
    if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = options.emptyText || "No hay datos";
        container.appendChild(empty);
        return;
    }

    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const trh = document.createElement("tr");
    columns.forEach(col => {
        const th = document.createElement("th");
        th.textContent = col.header;
        trh.appendChild(th);
    });
    thead.appendChild(trh);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    rows.forEach(row => {
        const tr = document.createElement("tr");
        if (options.onRowClick) {
            tr.classList.add("clickable");
            tr.addEventListener("click", () => options.onRowClick(row));
        }
        columns.forEach(col => {
            const td = document.createElement("td");
            const value = col.accessor ? col.accessor(row) : row[col.key];
            if (col.render) {
                td.appendChild(col.render(row));
            } else {
                td.textContent = value ?? "";
            }
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
}
