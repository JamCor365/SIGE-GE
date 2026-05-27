import { api } from "../api.js";
import { state } from "../state.js";
import { navigate } from "../router.js";
import { renderTable } from "../components/table.js";
import { renderPagination } from "../components/pagination.js";
import { openModal, createModalHeader, createModalFooter } from "../components/modal.js";
import { showToast } from "../toast.js";

const COLUMNS = [
    { header: "Código", key: "codigo" },
    { header: "Nombre Agencia", key: "nombre_agencia" },
    { header: "Categoría", key: "categoria" },
    { header: "Macroregión", key: "macroregion_nombre" },
    { header: "Estado", render: r => {
        const span = document.createElement("span");
        span.className = "badge--estado " + (r.activo === 1 ? "OPERATIVO" : "INOPERATIVO");
        span.textContent = r.activo === 1 ? "ACTIVO" : "BAJA";
        return span;
    }},
];

function applyFilters() {
    const q = (state.sedes.filters.busqueda || "").toLowerCase();
    state.sedes.filtered = state.sedes.list.filter(s => {
        if (!q) return true;
        const texto = `${s.codigo || ""} ${s.nombre_agencia || ""}`.toLowerCase();
        return texto.includes(q);
    });
    state.sedes.page = 1;
}

function getPageRows() {
    const start = (state.sedes.page - 1) * state.sedes.perPage;
    return state.sedes.filtered.slice(start, start + state.sedes.perPage);
}

export async function renderSedesList() {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando sedes…</div>`;
    document.getElementById("page-title").textContent = "Sedes";

    try {
        const [sedesRes, macroRes] = await Promise.all([
            api.get("/sedes"),
            api.get("/macroregiones"),
        ]);
        state.sedes.list = sedesRes.data || [];
        state.sedes.macroregiones = (macroRes.data || []).filter(m => m.activo === 1);
        applyFilters();

        main.innerHTML = "";
        const header = document.createElement("div");
        header.className = "page-header";
        header.innerHTML = `<h2>Sedes</h2>`;
        const btnNuevo = document.createElement("button");
        btnNuevo.className = "btn btn--primary";
        btnNuevo.textContent = "+ Nueva Sede";
        btnNuevo.addEventListener("click", () => openSedeModal());
        header.appendChild(btnNuevo);
        main.appendChild(header);

        const filters = document.createElement("div");
        filters.className = "filters";
        const inputBusq = document.createElement("input");
        inputBusq.type = "text";
        inputBusq.placeholder = "Buscar código o nombre…";
        inputBusq.value = state.sedes.filters.busqueda;
        inputBusq.addEventListener("input", () => {
            state.sedes.filters.busqueda = inputBusq.value;
            applyFilters();
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/sedes/${row.id}`), emptyText: "No se encontraron sedes" });
            renderPagination(paginationContainer, { page: state.sedes.page, perPage: state.sedes.perPage, total: state.sedes.filtered.length, onChange: onPageChange });
        });
        filters.appendChild(inputBusq);
        main.appendChild(filters);

        const wrapper = document.createElement("div");
        wrapper.className = "table-wrapper";
        const tableContainer = document.createElement("div");
        renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/sedes/${row.id}`), emptyText: "No se encontraron sedes" });
        wrapper.appendChild(tableContainer);
        main.appendChild(wrapper);

        const paginationContainer = document.createElement("div");
        renderPagination(paginationContainer, { page: state.sedes.page, perPage: state.sedes.perPage, total: state.sedes.filtered.length, onChange: onPageChange });
        main.appendChild(paginationContainer);

        function onPageChange(newPage) {
            state.sedes.page = newPage;
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/sedes/${row.id}`), emptyText: "No se encontraron sedes" });
            renderPagination(paginationContainer, { page: state.sedes.page, perPage: state.sedes.perPage, total: state.sedes.filtered.length, onChange: onPageChange });
        }
    } catch (err) {
        console.error(err);
        showToast("Error al cargar sedes: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar sedes</div>`;
    }
}

export async function renderSedeDetail(params) {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando detalle…</div>`;
    document.getElementById("page-title").textContent = "Detalle Sede";

    try {
        const res = await api.get(`/sedes/${params.id}`);
        const sede = res.data;
        state.sedes.detail = sede;

        main.innerHTML = "";
        const breadcrumb = document.createElement("div");
        breadcrumb.className = "breadcrumb";
        breadcrumb.innerHTML = `<a href="#/dashboard">Dashboard</a> <span>/</span> <a href="#/sedes">Sedes</a> <span>/</span> <span>${sede.codigo || sede.id}</span>`;
        main.appendChild(breadcrumb);

        const actions = document.createElement("div");
        actions.className = "page-header";
        actions.innerHTML = `<h2>${sede.nombre_agencia || sede.codigo}</h2>`;
        const actionGroup = document.createElement("div");
        actionGroup.style.display = "flex";
        actionGroup.style.gap = "8px";
        actionGroup.style.marginLeft = "auto";
        const btnVolver = document.createElement("button");
        btnVolver.className = "btn btn--ghost";
        btnVolver.textContent = "Volver";
        btnVolver.addEventListener("click", () => navigate("/sedes"));
        actionGroup.appendChild(btnVolver);
        const btnEditar = document.createElement("button");
        btnEditar.className = "btn btn--primary";
        btnEditar.textContent = "Editar";
        btnEditar.addEventListener("click", () => openSedeModal(sede));
        actionGroup.appendChild(btnEditar);
        const btnBaja = document.createElement("button");
        btnBaja.className = "btn btn--danger";
        btnBaja.textContent = "Dar de baja";
        btnBaja.addEventListener("click", async () => {
            if (!confirm("¿Confirmar baja lógica de esta sede?")) return;
            try {
                await api.del(`/sedes/${sede.id}`);
                showToast("Sede dada de baja", "success");
                navigate("/sedes");
            } catch (err) {
                showToast("Error: " + (err.message || ""), "error");
            }
        });
        actionGroup.appendChild(btnBaja);
        actions.appendChild(actionGroup);
        main.appendChild(actions);

        const grid = document.createElement("div");
        grid.className = "detail-grid";
        const col1 = document.createElement("div");
        col1.appendChild(buildSection("Información General", [
            ["Código", sede.codigo],
            ["Nombre Agencia", sede.nombre_agencia],
            ["Categoría", sede.categoria],
            ["Macroregión", sede.macroregion_nombre],
            ["Departamento", sede.departamento],
            ["Provincia", sede.provincia],
            ["Distrito", sede.distrito],
        ]));
        col1.appendChild(buildSection("Ubicación", [
            ["Dirección", sede.direccion],
        ]));
        grid.appendChild(col1);
        const col2 = document.createElement("div");
        col2.appendChild(buildSection("Observaciones", [
            ["Observaciones", sede.observaciones],
        ]));
        col2.appendChild(buildSection("Sistema", [
            ["Activo", sede.activo === 1 ? "Sí" : "No"],
            ["Creado", sede.created_at],
            ["Actualizado", sede.updated_at],
        ]));
        grid.appendChild(col2);
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

async function openSedeModal(sede = null) {
    const isEdit = !!sede;
    const title = isEdit ? "Editar Sede" : "Nueva Sede";

    const body = document.createElement("div");
    body.className = "modal__body";
    const form = document.createElement("form");
    form.className = "form-grid";
    form.id = "sede-form";

    const fields = [
        { name: "id", label: "ID", type: "number", required: true, readonly: isEdit },
        { name: "codigo", label: "Código", type: "text", required: true },
        { name: "nombre_agencia", label: "Nombre Agencia", type: "text", required: true },
        { name: "macroregion_id", label: "Macroregión", type: "select", required: true, options: state.sedes.macroregiones.map(m => ({ value: m.id, label: m.nombre })) },
        { name: "categoria", label: "Categoría", type: "select", options: [{ value: "Agencia 1", label: "Agencia 1" }, { value: "Agencia 2", label: "Agencia 2" }, { value: "Agencia 3", label: "Agencia 3" }, { value: "Almacen", label: "Almacen" }] },
        { name: "departamento", label: "Departamento", type: "text" },
        { name: "provincia", label: "Provincia", type: "text" },
        { name: "distrito", label: "Distrito", type: "text" },
        { name: "direccion", label: "Dirección", type: "text", fullWidth: true },
        { name: "observaciones", label: "Observaciones", type: "textarea", fullWidth: true },
    ];

    fields.forEach(f => {
        const group = document.createElement("div");
        group.className = "form-group" + (f.fullWidth ? " full-width" : "");
        const label = document.createElement("label");
        label.textContent = f.label + (f.required ? " *" : "");
        group.appendChild(label);
        let input;
        if (f.type === "select") {
            input = document.createElement("select");
            input.name = f.name;
            input.innerHTML = `<option value="">— Seleccionar —</option>` +
                (f.options || []).map(o => `<option value="${o.value}">${o.label}</option>`).join("");
        } else if (f.type === "textarea") {
            input = document.createElement("textarea");
            input.name = f.name;
            input.rows = 3;
        } else {
            input = document.createElement("input");
            input.type = f.type;
            input.name = f.name;
        }
        if (f.readonly) input.readOnly = true;
        if (isEdit && sede[f.name] !== undefined && sede[f.name] !== null) {
            input.value = sede[f.name];
        }
        group.appendChild(input);
        form.appendChild(group);
    });

    body.appendChild(form);

    const footer = createModalFooter([
        (() => { const b = document.createElement("button"); b.className = "btn btn--secondary"; b.type = "button"; b.textContent = "Cancelar"; b.addEventListener("click", () => modal.close()); return b; })(),
        (() => { const b = document.createElement("button"); b.className = "btn btn--primary"; b.type = "submit"; b.textContent = isEdit ? "Guardar cambios" : "Crear Sede"; return b; })(),
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
        const required = ["id", "codigo", "nombre_agencia", "macroregion_id"];
        const missing = required.filter(k => payload[k] === undefined || payload[k] === "");
        if (missing.length) {
            showToast("Campos obligatorios: " + missing.join(", "), "error");
            return;
        }
        try {
            if (isEdit) {
                await api.put(`/sedes/${sede.id}`, payload);
                showToast("Sede actualizada correctamente", "success");
                modal.close();
                renderSedeDetail({ id: sede.id });
            } else {
                await api.post("/sedes", payload);
                showToast("Sede creada correctamente", "success");
                modal.close();
                renderSedesList();
            }
        } catch (err) {
            showToast("Error: " + (err.message || ""), "error");
        }
    });
}
