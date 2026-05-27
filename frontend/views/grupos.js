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
    { header: "Estado", render: r => {
        const span = document.createElement("span");
        span.className = "badge--estado " + (r.estado || "");
        span.textContent = r.estado || "";
        return span;
    }},
    { header: "Marca Ensamblador", key: "marca_ensamblador" },
    { header: "Tipo Transferencia", key: "tipo_transferencia" },
    { header: "Potencia kW", key: "potencia_kw" },
];

function applyFilters() {
    const { macroregion, estado, busqueda } = state.grupos.filters;
    const q = (busqueda || "").toLowerCase();
    state.grupos.filtered = state.grupos.list.filter(g => {
        if (macroregion && g.macroregion !== macroregion) return false;
        if (estado && g.estado !== estado) return false;
        if (q) {
            const texto = `${g.cod_margesi || ""} ${g.nombre_agencia || ""} ${g.sede_codigo || ""}`.toLowerCase();
            if (!texto.includes(q)) return false;
        }
        return true;
    });
    state.grupos.page = 1;
}

function getPageRows() {
    const start = (state.grupos.page - 1) * state.grupos.perPage;
    return state.grupos.filtered.slice(start, start + state.grupos.perPage);
}

export async function renderGruposList() {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando grupos…</div>`;
    document.getElementById("page-title").textContent = "Grupos Electrógenos";

    try {
        const [gruposRes, macroRes] = await Promise.all([
            api.get("/grupos"),
            api.get("/macroregiones"),
        ]);
        state.grupos.list = gruposRes.data || [];
        state.grupos.macroregiones = (macroRes.data || []).filter(m => m.activo === 1);
        applyFilters();

        main.innerHTML = "";
        const header = document.createElement("div");
        header.className = "page-header";
        header.innerHTML = `<h2>Grupos Electrógenos</h2>`;
        const btnNuevo = document.createElement("button");
        btnNuevo.className = "btn btn--primary";
        btnNuevo.textContent = "+ Nuevo GE";
        btnNuevo.addEventListener("click", () => openGrupoModal());
        header.appendChild(btnNuevo);
        main.appendChild(header);

        const filters = document.createElement("div");
        filters.className = "filters";
        const selMacro = document.createElement("select");
        selMacro.innerHTML = `<option value="">Todas las macroregiones</option>` +
            state.grupos.macroregiones.map(m => `<option value="${m.nombre}">${m.nombre}</option>`).join("");
        selMacro.value = state.grupos.filters.macroregion;
        selMacro.addEventListener("change", () => {
            state.grupos.filters.macroregion = selMacro.value;
            applyFilters();
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/grupos/${row.id}`) });
            renderPagination(paginationContainer, { page: state.grupos.page, perPage: state.grupos.perPage, total: state.grupos.filtered.length, onChange: onPageChange });
        });
        filters.appendChild(selMacro);

        const selEstado = document.createElement("select");
        selEstado.innerHTML = `<option value="">Todos los estados</option><option value="OPERATIVO">OPERATIVO</option><option value="INOPERATIVO">INOPERATIVO</option>`;
        selEstado.value = state.grupos.filters.estado;
        selEstado.addEventListener("change", () => {
            state.grupos.filters.estado = selEstado.value;
            applyFilters();
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/grupos/${row.id}`) });
            renderPagination(paginationContainer, { page: state.grupos.page, perPage: state.grupos.perPage, total: state.grupos.filtered.length, onChange: onPageChange });
        });
        filters.appendChild(selEstado);

        const inputBusq = document.createElement("input");
        inputBusq.type = "text";
        inputBusq.placeholder = "Buscar código o sede…";
        inputBusq.value = state.grupos.filters.busqueda;
        inputBusq.addEventListener("input", () => {
            state.grupos.filters.busqueda = inputBusq.value;
            applyFilters();
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/grupos/${row.id}`) });
            renderPagination(paginationContainer, { page: state.grupos.page, perPage: state.grupos.perPage, total: state.grupos.filtered.length, onChange: onPageChange });
        });
        filters.appendChild(inputBusq);
        main.appendChild(filters);

        const wrapper = document.createElement("div");
        wrapper.className = "table-wrapper";
        const tableContainer = document.createElement("div");
        renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/grupos/${row.id}`), emptyText: "No se encontraron grupos" });
        wrapper.appendChild(tableContainer);
        main.appendChild(wrapper);

        const paginationContainer = document.createElement("div");
        renderPagination(paginationContainer, { page: state.grupos.page, perPage: state.grupos.perPage, total: state.grupos.filtered.length, onChange: onPageChange });
        main.appendChild(paginationContainer);

        function onPageChange(newPage) {
            state.grupos.page = newPage;
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/grupos/${row.id}`) });
            renderPagination(paginationContainer, { page: state.grupos.page, perPage: state.grupos.perPage, total: state.grupos.filtered.length, onChange: onPageChange });
        }
    } catch (err) {
        console.error(err);
        showToast("Error al cargar grupos: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar grupos</div>`;
    }
}

export async function renderGrupoDetail(params) {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando detalle…</div>`;
    document.getElementById("page-title").textContent = "Detalle GE";

    try {
        const [grupoRes, eventsRes] = await Promise.all([
            api.get(`/grupos/${params.id}`),
            api.get("/sync/pending"),
        ]);
        const grupo = grupoRes.data;
        state.grupos.detail = grupo;
        const historial = (eventsRes.events || []).filter(e => String(e.entity_id) === String(grupo.id) && e.entity === "grupo_electrogeno");

        main.innerHTML = "";
        const breadcrumb = document.createElement("div");
        breadcrumb.className = "breadcrumb";
        breadcrumb.innerHTML = `<a href="#/dashboard">Dashboard</a> <span>/</span> <a href="#/grupos">Grupos Electrógenos</a> <span>/</span> <span>${grupo.cod_margesi || grupo.id}</span>`;
        main.appendChild(breadcrumb);

        const actions = document.createElement("div");
        actions.className = "page-header";
        actions.innerHTML = `<h2>${grupo.cod_margesi || grupo.id}</h2>`;
        const actionGroup = document.createElement("div");
        actionGroup.style.display = "flex";
        actionGroup.style.gap = "8px";
        actionGroup.style.marginLeft = "auto";
        const btnVolver = document.createElement("button");
        btnVolver.className = "btn btn--ghost";
        btnVolver.textContent = "Volver";
        btnVolver.addEventListener("click", () => navigate("/grupos"));
        actionGroup.appendChild(btnVolver);
        const btnEditar = document.createElement("button");
        btnEditar.className = "btn btn--primary";
        btnEditar.textContent = "Editar";
        btnEditar.addEventListener("click", () => openGrupoModal(grupo));
        actionGroup.appendChild(btnEditar);
        const btnBaja = document.createElement("button");
        btnBaja.className = "btn btn--danger";
        btnBaja.textContent = "Dar de baja";
        btnBaja.addEventListener("click", async () => {
            if (!confirm("¿Confirmar baja lógica de este grupo electrógeno?")) return;
            try {
                await api.del(`/grupos/${grupo.id}`);
                showToast("Grupo electrógeno dado de baja", "success");
                navigate("/grupos");
            } catch (err) {
                showToast("Error: " + (err.message || ""), "error");
            }
        });
        actionGroup.appendChild(btnBaja);
        actions.appendChild(actionGroup);
        main.appendChild(actions);

        const grid = document.createElement("div");
        grid.className = "detail-grid";

        // Columna izquierda
        const col1 = document.createElement("div");
        col1.appendChild(buildSection("Información General", [
            ["Código Margesi", grupo.cod_margesi],
            ["Sede", `${grupo.sede_codigo || ""} — ${grupo.nombre_agencia || ""}`],
            ["Macroregión", grupo.macroregion],
            ["Estado", grupo.estado],
            ["Año fabricación", grupo.anio_fabricacion],
            ["Potencia kW", grupo.potencia_kw],
            ["Fase eléctrica", grupo.fase_electrica],
            ["Tipo transferencia", grupo.tipo_transferencia],
            ["Mecanismo transferencia", grupo.mecanismo_transferencia],
            ["Observaciones", grupo.observaciones],
        ]));
        col1.appendChild(buildSection("Ensamblador", [
            ["Marca", grupo.marca_ensamblador],
            ["Modelo", grupo.modelo_ensamblador],
            ["Serie", grupo.serie_ensamblador],
        ]));
        col1.appendChild(buildSection("Motor", [
            ["Marca", grupo.marca_motor],
            ["Modelo", grupo.modelo_motor],
            ["Serie", grupo.serie_motor],
        ]));
        grid.appendChild(col1);

        // Columna derecha
        const col2 = document.createElement("div");
        col2.appendChild(buildSection("Alternador", [
            ["Marca", grupo.marca_alternador],
            ["Modelo", grupo.modelo_alternador],
            ["Serie", grupo.serie_alternador],
        ]));
        col2.appendChild(buildSection("Módulo de control", [
            ["Marca", grupo.marca_modulocontrol],
            ["Modelo", grupo.modelo_modulocontrol],
            ["Serie", grupo.serie_modulocontrol],
        ]));

        // Documentos placeholder
        const docsSection = document.createElement("div");
        docsSection.className = "detail-section";
        docsSection.innerHTML = `<h3>Documentos</h3><div class="empty-state">Sin documentos adjuntos</div>`;
        col2.appendChild(docsSection);

        // Historial
        const histSection = document.createElement("div");
        histSection.className = "detail-section";
        histSection.innerHTML = `<h3>Historial de eventos</h3>`;
        const histTable = document.createElement("div");
        renderTable(histTable, [
            { header: "Event ID", key: "event_id" },
            { header: "Acción", key: "action" },
            { header: "Fecha", key: "created_at" },
            { header: "Usuario", key: "created_by" },
        ], historial, { emptyText: "Sin eventos pendientes" });
        histSection.appendChild(histTable);
        col2.appendChild(histSection);

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

async function openGrupoModal(grupo = null) {
    const isEdit = !!grupo;
    const title = isEdit ? "Editar Grupo Electrógeno" : "Nuevo Grupo Electrógeno";

    let sedes = [];
    try {
        const res = await api.get("/sedes");
        sedes = (res.data || []).filter(s => s.activo === 1);
    } catch (e) {
        showToast("Error al cargar sedes", "error");
    }

    const body = document.createElement("div");
    body.className = "modal__body";
    body.style.maxHeight = "85vh";
    body.style.overflowY = "auto";
    const form = document.createElement("form");
    form.className = "form-grid";
    form.id = "grupo-form";

    const fields = [
        { name: "id", label: "ID", type: "number", required: true, readonly: isEdit },
        { name: "sede_id", label: "Sede", type: "select", required: true, options: sedes.map(s => ({ value: s.id, label: `${s.codigo} — ${s.nombre_agencia}` })) },
        { name: "cod_margesi", label: "Código Margesi", type: "text" },
        { name: "estado", label: "Estado", type: "select", required: true, options: [{ value: "OPERATIVO", label: "OPERATIVO" }, { value: "INOPERATIVO", label: "INOPERATIVO" }] },
        { name: "anio_fabricacion", label: "Año fabricación", type: "number" },
        { name: "potencia_kw", label: "Potencia kW", type: "number", step: "0.1" },
        { name: "fase_electrica", label: "Fase eléctrica", type: "select", options: [{ value: "MONOFASICO", label: "MONOFASICO" }, { value: "TRIFASICO", label: "TRIFASICO" }] },
        { name: "tipo_transferencia", label: "Tipo transferencia", type: "select", options: [{ value: "AUTOMATICO", label: "AUTOMATICO" }, { value: "MANUAL", label: "MANUAL" }] },
        { name: "mecanismo_transferencia", label: "Mecanismo transferencia", type: "text" },
        { name: "marca_ensamblador", label: "Marca ensamblador", type: "text" },
        { name: "modelo_ensamblador", label: "Modelo ensamblador", type: "text" },
        { name: "serie_ensamblador", label: "Serie ensamblador", type: "text" },
        { name: "marca_motor", label: "Marca motor", type: "text" },
        { name: "modelo_motor", label: "Modelo motor", type: "text" },
        { name: "serie_motor", label: "Serie motor", type: "text" },
        { name: "marca_alternador", label: "Marca alternador", type: "text" },
        { name: "modelo_alternador", label: "Modelo alternador", type: "text" },
        { name: "serie_alternador", label: "Serie alternador", type: "text" },
        { name: "marca_modulocontrol", label: "Marca módulo control", type: "text" },
        { name: "modelo_modulocontrol", label: "Modelo módulo control", type: "text" },
        { name: "serie_modulocontrol", label: "Serie módulo control", type: "text" },
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
            if (f.step) input.step = f.step;
        }
        if (f.readonly) input.readOnly = true;
        if (isEdit && grupo[f.name] !== undefined && grupo[f.name] !== null) {
            input.value = grupo[f.name];
        }
        group.appendChild(input);
        form.appendChild(group);
    });

    const footer = createModalFooter([
        (() => {
            const btn = document.createElement("button");
            btn.className = "btn btn--secondary";
            btn.type = "button";
            btn.textContent = "Cancelar";
            btn.addEventListener("click", () => modal.close());
            return btn;
        })(),
        (() => {
            const btn = document.createElement("button");
            btn.className = "btn btn--primary";
            btn.type = "submit";
            btn.textContent = isEdit ? "Guardar cambios" : "Crear GE";
            return btn;
        })(),
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
                payload[f.name] = f.type === "number" ? (v === "" ? undefined : Number(v)) : v;
            }
        });

        if (!payload.sede_id) {
            showToast("La sede es obligatoria", "error");
            return;
        }
        if (!payload.estado) {
            showToast("El estado es obligatorio", "error");
            return;
        }

        try {
            if (isEdit) {
                await api.put(`/grupos/${grupo.id}`, payload);
                showToast("Grupo actualizado correctamente", "success");
                modal.close();
                renderGrupoDetail({ id: grupo.id });
            } else {
                await api.post("/grupos", payload);
                showToast("Grupo creado correctamente", "success");
                modal.close();
                renderGruposList();
            }
        } catch (err) {
            console.error(err);
            showToast("Error: " + (err.message || ""), "error");
        }
    });
}
