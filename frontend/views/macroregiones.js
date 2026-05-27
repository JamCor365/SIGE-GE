import { api } from "../api.js";
import { state } from "../state.js";
import { navigate } from "../router.js";
import { renderTable } from "../components/table.js";
import { renderPagination } from "../components/pagination.js";
import { openModal, createModalHeader, createModalFooter } from "../components/modal.js";
import { showToast } from "../toast.js";

const COLUMNS = [
    { header: "ID", key: "id" },
    { header: "Nombre", key: "nombre" },
    { header: "Estado", render: r => {
        const span = document.createElement("span");
        span.className = "badge--estado " + (r.activo === 1 ? "OPERATIVO" : "INOPERATIVO");
        span.textContent = r.activo === 1 ? "ACTIVO" : "BAJA";
        return span;
    }},
];

function applyFilters() {
    const q = (state.macroregiones.filters.busqueda || "").toLowerCase();
    state.macroregiones.filtered = state.macroregiones.list.filter(m => {
        if (!q) return true;
        const texto = `${m.id} ${m.nombre || ""}`.toLowerCase();
        return texto.includes(q);
    });
    state.macroregiones.page = 1;
}

function getPageRows() {
    const start = (state.macroregiones.page - 1) * state.macroregiones.perPage;
    return state.macroregiones.filtered.slice(start, start + state.macroregiones.perPage);
}

export async function renderMacroregionesList() {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando macroregiones…</div>`;
    document.getElementById("page-title").textContent = "Macroregiones";

    try {
        const res = await api.get("/macroregiones");
        state.macroregiones.list = res.data || [];
        applyFilters();

        main.innerHTML = "";
        const header = document.createElement("div");
        header.className = "page-header";
        header.innerHTML = `<h2>Macroregiones</h2>`;
        const btnNuevo = document.createElement("button");
        btnNuevo.className = "btn btn--primary";
        btnNuevo.textContent = "+ Nueva Macroregión";
        btnNuevo.addEventListener("click", () => openMacroModal());
        header.appendChild(btnNuevo);
        main.appendChild(header);

        const filters = document.createElement("div");
        filters.className = "filters";
        const inputBusq = document.createElement("input");
        inputBusq.type = "text";
        inputBusq.placeholder = "Buscar ID o nombre…";
        inputBusq.value = state.macroregiones.filters.busqueda;
        inputBusq.addEventListener("input", () => {
            state.macroregiones.filters.busqueda = inputBusq.value;
            applyFilters();
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/macroregiones/${row.id}`) });
            renderPagination(paginationContainer, { page: state.macroregiones.page, perPage: state.macroregiones.perPage, total: state.macroregiones.filtered.length, onChange: onPageChange });
        });
        filters.appendChild(inputBusq);
        main.appendChild(filters);

        const wrapper = document.createElement("div");
        wrapper.className = "table-wrapper";
        const tableContainer = document.createElement("div");
        renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/macroregiones/${row.id}`), emptyText: "No se encontraron macroregiones" });
        wrapper.appendChild(tableContainer);
        main.appendChild(wrapper);

        const paginationContainer = document.createElement("div");
        renderPagination(paginationContainer, { page: state.macroregiones.page, perPage: state.macroregiones.perPage, total: state.macroregiones.filtered.length, onChange: onPageChange });
        main.appendChild(paginationContainer);

        function onPageChange(newPage) {
            state.macroregiones.page = newPage;
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/macroregiones/${row.id}`) });
            renderPagination(paginationContainer, { page: state.macroregiones.page, perPage: state.macroregiones.perPage, total: state.macroregiones.filtered.length, onChange: onPageChange });
        }
    } catch (err) {
        console.error(err);
        showToast("Error al cargar macroregiones: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar macroregiones</div>`;
    }
}

export async function renderMacroregionDetail(params) {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando detalle…</div>`;
    document.getElementById("page-title").textContent = "Detalle Macroregión";

    try {
        const res = await api.get(`/macroregiones/${params.id}`);
        const item = res.data;
        state.macroregiones.detail = item;

        main.innerHTML = "";
        const breadcrumb = document.createElement("div");
        breadcrumb.className = "breadcrumb";
        breadcrumb.innerHTML = `<a href="#/dashboard">Dashboard</a> <span>/</span> <a href="#/macroregiones">Macroregiones</a> <span>/</span> <span>${item.nombre || item.id}</span>`;
        main.appendChild(breadcrumb);

        const actions = document.createElement("div");
        actions.className = "page-header";
        actions.innerHTML = `<h2>${item.nombre}</h2>`;
        const actionGroup = document.createElement("div");
        actionGroup.style.display = "flex";
        actionGroup.style.gap = "8px";
        actionGroup.style.marginLeft = "auto";
        const btnVolver = document.createElement("button");
        btnVolver.className = "btn btn--ghost";
        btnVolver.textContent = "Volver";
        btnVolver.addEventListener("click", () => navigate("/macroregiones"));
        actionGroup.appendChild(btnVolver);
        const btnEditar = document.createElement("button");
        btnEditar.className = "btn btn--primary";
        btnEditar.textContent = "Editar";
        btnEditar.addEventListener("click", () => openMacroModal(item));
        actionGroup.appendChild(btnEditar);
        const btnBaja = document.createElement("button");
        btnBaja.className = "btn btn--danger";
        btnBaja.textContent = "Dar de baja";
        btnBaja.addEventListener("click", async () => {
            if (!confirm("¿Confirmar baja lógica?")) return;
            try {
                await api.del(`/macroregiones/${item.id}`);
                showToast("Macroregión dada de baja", "success");
                navigate("/macroregiones");
            } catch (err) {
                showToast("Error: " + (err.message || ""), "error");
            }
        });
        actionGroup.appendChild(btnBaja);
        actions.appendChild(actionGroup);
        main.appendChild(actions);

        const grid = document.createElement("div");
        grid.className = "detail-grid";
        const col = document.createElement("div");
        col.appendChild(buildSection("Información", [
            ["ID", item.id],
            ["Nombre", item.nombre],
            ["Activo", item.activo === 1 ? "Sí" : "No"],
            ["Creado", item.created_at],
            ["Actualizado", item.updated_at],
        ]));
        grid.appendChild(col);
        main.appendChild(grid);
    } catch (err) {
        console.error(err);
        showToast("Error al cargar detalle: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar detalle</div>`;
    }
}

function buildSection(title, fields) {
    const section = document.createElement("div");
    section.className = "detail-section";
    const h3 = document.createElement("h3");
    h3.textContent = title;
    section.appendChild(h3);
    fields.forEach(([label, value]) => {
        const row = document.createElement("div");
        row.className = "detail-field";
        row.innerHTML = `<span class="detail-field__label">${label}</span><span class="detail-field__value">${value ?? "—"}</span>`;
        section.appendChild(row);
    });
    return section;
}

function openMacroModal(item = null) {
    const isEdit = !!item;
    const title = isEdit ? "Editar Macroregión" : "Nueva Macroregión";

    const body = document.createElement("div");
    body.className = "modal__body";
    const form = document.createElement("form");
    form.className = "form-grid";
    form.id = "macro-form";

    const fields = [
        { name: "id", label: "ID", type: "number", required: true, readonly: isEdit },
        { name: "nombre", label: "Nombre", type: "text", required: true },
    ];

    fields.forEach(f => {
        const group = document.createElement("div");
        group.className = "form-group";
        const label = document.createElement("label");
        label.textContent = f.label + (f.required ? " *" : "");
        group.appendChild(label);
        const input = document.createElement("input");
        input.type = f.type;
        input.name = f.name;
        if (f.readonly) input.readOnly = true;
        if (isEdit && item[f.name] !== undefined && item[f.name] !== null) input.value = item[f.name];
        group.appendChild(input);
        form.appendChild(group);
    });

    body.appendChild(form);

    const footer = createModalFooter([
        (() => { const b = document.createElement("button"); b.className = "btn btn--secondary"; b.type = "button"; b.textContent = "Cancelar"; b.addEventListener("click", () => modal.close()); return b; })(),
        (() => { const b = document.createElement("button"); b.className = "btn btn--primary"; b.type = "submit"; b.textContent = isEdit ? "Guardar cambios" : "Crear Macroregión"; return b; })(),
    ]);

    const modalRoot = document.createElement("div");
    modalRoot.appendChild(createModalHeader(title, () => modal.close()));
    modalRoot.appendChild(body);
    modalRoot.appendChild(footer);
    const modal = openModal(modalRoot);

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const payload = {};
        fields.forEach(f => {
            const v = fd.get(f.name);
            if (v !== "" && v !== null && v !== undefined) {
                payload[f.name] = f.type === "number" ? Number(v) : v;
            }
        });
        if (payload.id === undefined || payload.nombre === undefined) {
            showToast("ID y nombre son obligatorios", "error");
            return;
        }
        try {
            if (isEdit) {
                await api.put(`/macroregiones/${item.id}`, payload);
                showToast("Macroregión actualizada correctamente", "success");
                modal.close();
                renderMacroregionDetail({ id: item.id });
            } else {
                await api.post("/macroregiones", payload);
                showToast("Macroregión creada correctamente", "success");
                modal.close();
                renderMacroregionesList();
            }
        } catch (err) {
            showToast("Error: " + (err.message || ""), "error");
        }
    });
}
