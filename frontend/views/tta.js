import { api } from "../api.js";
import { state } from "../state.js";
import { navigate } from "../router.js";
import { renderTable } from "../components/table.js";
import { renderPagination } from "../components/pagination.js";
import { openModal, createModalHeader, createModalFooter } from "../components/modal.js";
import { showToast } from "../toast.js";

const COLUMNS = [
    { header: "Cód. Margesi", key: "cod_margesi" },
    { header: "Sede", accessor: r => `${r.sede_codigo || ""} — ${r.nombre_agencia || ""}` },
    { header: "Macroregión", key: "macroregion" },
    { header: "Marca", key: "marca" },
    { header: "Modelo", key: "modelo" },
    { header: "Fases", key: "fases" },
    { header: "Estado", render: r => {
        const span = document.createElement("span");
        span.className = "badge--estado " + (r.estado || "");
        span.textContent = r.estado || "";
        return span;
    }},
];

function applyFilters() {
    const q = (state.tta.filters.busqueda || "").toLowerCase();
    state.tta.filtered = state.tta.list.filter(t => {
        if (!q) return true;
        const texto = `${t.cod_margesi || ""} ${t.nombre_agencia || ""} ${t.sede_codigo || ""} ${t.marca || ""}`.toLowerCase();
        return texto.includes(q);
    });
    state.tta.page = 1;
}

function getPageRows() {
    const start = (state.tta.page - 1) * state.tta.perPage;
    return state.tta.filtered.slice(start, start + state.tta.perPage);
}

export async function renderTTAList() {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando TTA…</div>`;
    document.getElementById("page-title").textContent = "TTA";

    try {
        const [ttaRes, sedesRes] = await Promise.all([
            api.get("/tta"),
            api.get("/sedes"),
        ]);
        state.tta.list = ttaRes.data || [];
        state.tta.sedes = (sedesRes.data || []).filter(s => s.activo === 1);
        applyFilters();

        main.innerHTML = "";
        const header = document.createElement("div");
        header.className = "page-header";
        header.innerHTML = `<h2>Tableros de Transferencia Automática</h2>`;
        const btnNuevo = document.createElement("button");
        btnNuevo.className = "btn btn--primary";
        btnNuevo.textContent = "+ Nuevo TTA";
        btnNuevo.addEventListener("click", () => openTTAModal());
        header.appendChild(btnNuevo);
        main.appendChild(header);

        const filters = document.createElement("div");
        filters.className = "filters";
        const inputBusq = document.createElement("input");
        inputBusq.type = "text";
        inputBusq.placeholder = "Buscar código, sede o marca…";
        inputBusq.value = state.tta.filters.busqueda;
        inputBusq.addEventListener("input", () => {
            state.tta.filters.busqueda = inputBusq.value;
            applyFilters();
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/tta/${row.id}`) });
            renderPagination(paginationContainer, { page: state.tta.page, perPage: state.tta.perPage, total: state.tta.filtered.length, onChange: onPageChange });
        });
        filters.appendChild(inputBusq);
        main.appendChild(filters);

        const wrapper = document.createElement("div");
        wrapper.className = "table-wrapper";
        const tableContainer = document.createElement("div");
        renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/tta/${row.id}`), emptyText: "No se encontraron TTA" });
        wrapper.appendChild(tableContainer);
        main.appendChild(wrapper);

        const paginationContainer = document.createElement("div");
        renderPagination(paginationContainer, { page: state.tta.page, perPage: state.tta.perPage, total: state.tta.filtered.length, onChange: onPageChange });
        main.appendChild(paginationContainer);

        function onPageChange(newPage) {
            state.tta.page = newPage;
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/tta/${row.id}`) });
            renderPagination(paginationContainer, { page: state.tta.page, perPage: state.tta.perPage, total: state.tta.filtered.length, onChange: onPageChange });
        }
    } catch (err) {
        console.error(err);
        showToast("Error al cargar TTA: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar TTA</div>`;
    }
}

export async function renderTTADetail(params) {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando detalle…</div>`;
    document.getElementById("page-title").textContent = "Detalle TTA";

    try {
        const res = await api.get(`/tta/${params.id}`);
        const item = res.data;
        state.tta.detail = item;

        main.innerHTML = "";
        const breadcrumb = document.createElement("div");
        breadcrumb.className = "breadcrumb";
        breadcrumb.innerHTML = `<a href="#/dashboard">Dashboard</a> <span>/</span> <a href="#/tta">TTA</a> <span>/</span> <span>${item.cod_margesi || item.id}</span>`;
        main.appendChild(breadcrumb);

        const actions = document.createElement("div");
        actions.className = "page-header";
        actions.innerHTML = `<h2>${item.cod_margesi || item.id}</h2>`;
        const actionGroup = document.createElement("div");
        actionGroup.style.display = "flex";
        actionGroup.style.gap = "8px";
        actionGroup.style.marginLeft = "auto";
        const btnVolver = document.createElement("button");
        btnVolver.className = "btn btn--ghost";
        btnVolver.textContent = "Volver";
        btnVolver.addEventListener("click", () => navigate("/tta"));
        actionGroup.appendChild(btnVolver);
        const btnEditar = document.createElement("button");
        btnEditar.className = "btn btn--primary";
        btnEditar.textContent = "Editar";
        btnEditar.addEventListener("click", () => openTTAModal(item));
        actionGroup.appendChild(btnEditar);
        const btnBaja = document.createElement("button");
        btnBaja.className = "btn btn--danger";
        btnBaja.textContent = "Dar de baja";
        btnBaja.addEventListener("click", async () => {
            if (!confirm("¿Confirmar baja lógica?")) return;
            try {
                await api.del(`/tta/${item.id}`);
                showToast("TTA dado de baja", "success");
                navigate("/tta");
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
            ["Código Margesi", item.cod_margesi],
            ["Sede", `${item.sede_codigo || ""} — ${item.nombre_agencia || ""}`],
            ["Macroregión", item.macroregion],
            ["Estado", item.estado],
            ["Fases", item.fases],
            ["Tipo mecanismo", item.tipo_mecanismo],
        ]));
        col1.appendChild(buildSection("Especificaciones", [
            ["Marca", item.marca],
            ["Modelo", item.modelo],
            ["Serie", item.serie],
        ]));
        grid.appendChild(col1);
        const col2 = document.createElement("div");
        col2.appendChild(buildSection("Observaciones", [
            ["Observaciones", item.observaciones],
        ]));
        col2.appendChild(buildSection("Sistema", [
            ["Activo", item.activo === 1 ? "Sí" : "No"],
            ["Creado", item.created_at],
            ["Actualizado", item.updated_at],
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

async function openTTAModal(item = null) {
    const isEdit = !!item;
    const title = isEdit ? "Editar TTA" : "Nuevo TTA";

    const body = document.createElement("div");
    body.className = "modal__body";
    body.style.maxHeight = "85vh";
    body.style.overflowY = "auto";
    const form = document.createElement("form");
    form.className = "form-grid";
    form.id = "tta-form";

    const fields = [
        { name: "id", label: "ID", type: "number", required: true, readonly: isEdit },
        { name: "sede_id", label: "Sede", type: "select", required: true, options: state.tta.sedes.map(s => ({ value: s.id, label: `${s.codigo} — ${s.nombre_agencia}` })) },
        { name: "cod_margesi", label: "Código Margesi", type: "text" },
        { name: "marca", label: "Marca", type: "text" },
        { name: "modelo", label: "Modelo", type: "text" },
        { name: "serie", label: "Serie", type: "text" },
        { name: "tipo_mecanismo", label: "Tipo mecanismo", type: "text" },
        { name: "fases", label: "Fases", type: "select", options: [{ value: "MONOFASICO", label: "MONOFASICO" }, { value: "TRIFASICO", label: "TRIFASICO" }] },
        { name: "estado", label: "Estado", type: "select", options: [{ value: "OPERATIVO", label: "OPERATIVO" }, { value: "INOPERATIVO", label: "INOPERATIVO" }] },
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
        if (isEdit && item[f.name] !== undefined && item[f.name] !== null) input.value = item[f.name];
        group.appendChild(input);
        form.appendChild(group);
    });

    const footer = createModalFooter([
        (() => { const b = document.createElement("button"); b.className = "btn btn--secondary"; b.type = "button"; b.textContent = "Cancelar"; b.addEventListener("click", () => modal.close()); return b; })(),
        (() => { const b = document.createElement("button"); b.className = "btn btn--primary"; b.type = "submit"; b.textContent = isEdit ? "Guardar cambios" : "Crear TTA"; return b; })(),
    ]);
    footer.style.gridColumn = "1 / -1";
    form.appendChild(footer);
    body.appendChild(form);

    const modalRoot = document.createElement("div");
    modalRoot.appendChild(createModalHeader(title, () => modal.close()));
    modalRoot.appendChild(body);
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
        if (!payload.sede_id) {
            showToast("La sede es obligatoria", "error");
            return;
        }
        try {
            if (isEdit) {
                await api.put(`/tta/${item.id}`, payload);
                showToast("TTA actualizado correctamente", "success");
                modal.close();
                renderTTADetail({ id: item.id });
            } else {
                await api.post("/tta", payload);
                showToast("TTA creado correctamente", "success");
                modal.close();
                renderTTAList();
            }
        } catch (err) {
            showToast("Error: " + (err.message || ""), "error");
        }
    });
}
