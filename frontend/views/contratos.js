import { api } from "../api.js";
import { state } from "../state.js";
import { navigate } from "../router.js";
import { renderTable } from "../components/table.js";
import { renderPagination } from "../components/pagination.js";
import { openModal, createModalHeader, createModalFooter } from "../components/modal.js";
import { showToast } from "../toast.js";
import { buildFormFields, buildPayload } from "./_form_helpers.js";

// Dominios cerrados — deben coincidir con los CHECK del backend (db.py / contratos.py).
const ESTADOS = ["VIGENTE", "CULMINADO", "RESUELTO"];
// Tokens atómicos multivalor (un contrato puede ser varios a la vez).
const TIPOS_OBJETO = ["ADQUISICION", "INSTALACION", "MANTENIMIENTO", "SERVICIO", "OBRA", "CONSULTORIA"];
const PROCEDIMIENTOS = [
    "LICITACION_PUBLICA", "CONCURSO_PUBLICO", "ADJUDICACION_SIMPLIFICADA",
    "SUBASTA_INVERSA_ELECTRONICA", "SELECCION_CONSULTORES_INDIVIDUALES",
    "COMPARACION_PRECIOS", "CONTRATACION_DIRECTA", "CATALOGO_ELECTRONICO_AM",
];

// Montos llegan de la API en céntimos (enteros). Soles = céntimos / 100.
function formatMonto(centimos, moneda = "PEN") {
    if (centimos === null || centimos === undefined) return "—";
    const soles = (centimos / 100).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `${moneda === "PEN" ? "S/" : moneda} ${soles}`;
}
const tiposLabel = r => (Array.isArray(r.tipos_objeto) && r.tipos_objeto.length ? r.tipos_objeto.join(" + ") : "—");

const COLUMNS = [
    { header: "Número", accessor: r => r.numero || "—" },
    { header: "Objeto", key: "objeto" },
    { header: "Proveedor", accessor: r => r.proveedor || "—" },
    { header: "Tipo", accessor: tiposLabel },
    { header: "Monto principal", accessor: r => formatMonto(r.monto_principal, r.moneda) },
    { header: "Inicio", accessor: r => r.fecha_inicio || "—" },
    { header: "Fin", accessor: r => r.fecha_fin || "—" },
    { header: "Estado", render: r => {
        const span = document.createElement("span");
        span.className = "badge--estado " + (r.estado || "");
        span.textContent = r.estado || "—";
        return span;
    }},
];

function applyFilters() {
    const { estado, busqueda } = state.contratos.filters;
    const q = (busqueda || "").toLowerCase();
    state.contratos.filtered = state.contratos.list.filter(c => {
        if (estado && c.estado !== estado) return false;
        if (q) {
            const texto = `${c.numero || ""} ${c.objeto || ""} ${c.proveedor || ""}`.toLowerCase();
            if (!texto.includes(q)) return false;
        }
        return true;
    });
    state.contratos.page = 1;
}

function getPageRows() {
    const start = (state.contratos.page - 1) * state.contratos.perPage;
    return state.contratos.filtered.slice(start, start + state.contratos.perPage);
}

export async function renderContratosList() {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando contratos…</div>`;
    document.getElementById("page-title").textContent = "Contratos";

    try {
        const res = await api.get("/contratos");
        // Solo contratos vigentes (activo=1); la baja lógica los oculta de la lista.
        state.contratos.list = (res.data || []).filter(c => c.activo === 1);
        applyFilters();

        main.innerHTML = "";
        const header = document.createElement("div");
        header.className = "page-header";
        header.innerHTML = `<h2>Contratos</h2>`;
        const btnNuevo = document.createElement("button");
        btnNuevo.className = "btn btn--primary";
        btnNuevo.textContent = "+ Nuevo Contrato";
        btnNuevo.addEventListener("click", () => openContratoModal());
        header.appendChild(btnNuevo);
        main.appendChild(header);

        const filters = document.createElement("div");
        filters.className = "filters";

        const selEstado = document.createElement("select");
        selEstado.innerHTML = `<option value="">Todos los estados</option>` +
            ESTADOS.map(e => `<option value="${e}">${e}</option>`).join("");
        selEstado.value = state.contratos.filters.estado;
        selEstado.addEventListener("change", () => {
            state.contratos.filters.estado = selEstado.value;
            applyFilters();
            refresh();
        });
        filters.appendChild(selEstado);

        const inputBusq = document.createElement("input");
        inputBusq.type = "text";
        inputBusq.placeholder = "Buscar número, objeto o proveedor…";
        inputBusq.value = state.contratos.filters.busqueda;
        inputBusq.addEventListener("input", () => {
            state.contratos.filters.busqueda = inputBusq.value;
            applyFilters();
            refresh();
        });
        filters.appendChild(inputBusq);
        main.appendChild(filters);

        const wrapper = document.createElement("div");
        wrapper.className = "table-wrapper";
        const tableContainer = document.createElement("div");
        renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/contratos/${row.id}`), emptyText: "No se encontraron contratos" });
        wrapper.appendChild(tableContainer);
        main.appendChild(wrapper);

        const paginationContainer = document.createElement("div");
        renderPagination(paginationContainer, { page: state.contratos.page, perPage: state.contratos.perPage, total: state.contratos.filtered.length, onChange: onPageChange });
        main.appendChild(paginationContainer);

        function refresh() {
            renderTable(tableContainer, COLUMNS, getPageRows(), { onRowClick: row => navigate(`/contratos/${row.id}`), emptyText: "No se encontraron contratos" });
            renderPagination(paginationContainer, { page: state.contratos.page, perPage: state.contratos.perPage, total: state.contratos.filtered.length, onChange: onPageChange });
        }
        function onPageChange(newPage) {
            state.contratos.page = newPage;
            refresh();
        }
    } catch (err) {
        console.error(err);
        showToast("Error al cargar contratos: " + (err.message || ""), "error");
        main.innerHTML = `<div class="empty-state">Error al cargar contratos</div>`;
    }
}

export async function renderContratoDetail(params) {
    const main = document.getElementById("main-content");
    main.innerHTML = `<div class="loading" style="padding:2rem;text-align:center;">Cargando detalle…</div>`;
    document.getElementById("page-title").textContent = "Detalle Contrato";

    try {
        const res = await api.get(`/contratos/${params.id}`);
        const item = res.data;
        state.contratos.detail = item;
        const titulo = item.numero || item.objeto || item.id;

        main.innerHTML = "";
        const breadcrumb = document.createElement("div");
        breadcrumb.className = "breadcrumb";
        breadcrumb.innerHTML = `<a href="#/dashboard">Dashboard</a> <span>/</span> <a href="#/contratos">Contratos</a> <span>/</span> <span>${titulo}</span>`;
        main.appendChild(breadcrumb);

        const actions = document.createElement("div");
        actions.className = "page-header";
        actions.innerHTML = `<h2>${titulo}</h2>`;
        const actionGroup = document.createElement("div");
        actionGroup.style.display = "flex";
        actionGroup.style.gap = "8px";
        actionGroup.style.marginLeft = "auto";
        const btnVolver = document.createElement("button");
        btnVolver.className = "btn btn--ghost";
        btnVolver.textContent = "Volver";
        btnVolver.addEventListener("click", () => navigate("/contratos"));
        actionGroup.appendChild(btnVolver);
        const btnEditar = document.createElement("button");
        btnEditar.className = "btn btn--primary";
        btnEditar.textContent = "Editar";
        btnEditar.addEventListener("click", () => openContratoModal(item));
        actionGroup.appendChild(btnEditar);
        const btnBaja = document.createElement("button");
        btnBaja.className = "btn btn--danger";
        btnBaja.textContent = "Dar de baja";
        btnBaja.addEventListener("click", async () => {
            if (!confirm("¿Confirmar baja lógica de este contrato?")) return;
            try {
                await api.del(`/contratos/${item.id}`);
                showToast("Contrato dado de baja", "success");
                navigate("/contratos");
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
        col.appendChild(buildSection("Información del contrato", [
            ["Número", item.numero],
            ["Objeto", item.objeto],
            ["Entidad contratante", item.entidad_contratante],
            ["Procedimiento de selección", item.procedimiento_seleccion],
            ["Tipo de objeto", tiposLabel(item)],
            ["Proveedor", item.proveedor],
            ["Monto principal", formatMonto(item.monto_principal, item.moneda)],
            ["Monto accesorio", formatMonto(item.monto_accesorio, item.moneda)],
            ["Moneda", item.moneda],
            ["Fecha inicio", item.fecha_inicio],
            ["Fecha fin", item.fecha_fin],
            ["Estado", item.estado],
            ["Observaciones", item.observaciones],
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

function openContratoModal(item = null) {
    const isEdit = !!item;
    const title = isEdit ? "Editar Contrato" : "Nuevo Contrato";

    const body = document.createElement("div");
    body.className = "modal__body";
    body.style.maxHeight = "85vh";
    body.style.overflowY = "auto";
    const form = document.createElement("form");
    form.className = "form-grid";
    form.id = "contrato-form";

    // Sin campo id: el UUID lo genera el backend al crear.
    // objeto es el único requerido (único NOT NULL). El resto opcional —
    // numero puede ir vacío (contrato aún sin número adjudicado).
    const fields = [
        { name: "numero", label: "Número", type: "text" },
        { name: "objeto", label: "Objeto", type: "text", required: true, fullWidth: true },
        { name: "entidad_contratante", label: "Entidad contratante", type: "text" },
        { name: "procedimiento_seleccion", label: "Procedimiento de selección", type: "select", options: PROCEDIMIENTOS.map(p => ({ value: p, label: p })) },
        { name: "tipos_objeto", label: "Tipo de objeto", type: "multiselect", options: TIPOS_OBJETO.map(t => ({ value: t, label: t })), fullWidth: true },
        { name: "proveedor", label: "Proveedor (adjudicatario)", type: "text" },
        { name: "monto_principal", label: "Monto principal (S/)", type: "number", step: "0.01" },
        { name: "monto_accesorio", label: "Monto accesorio (S/)", type: "number", step: "0.01" },
        { name: "moneda", label: "Moneda", type: "select", options: [{ value: "PEN", label: "PEN (S/)" }, { value: "USD", label: "USD ($)" }] },
        { name: "estado", label: "Estado", type: "select", options: ESTADOS.map(e => ({ value: e, label: e })) },
        { name: "fecha_inicio", label: "Fecha inicio", type: "date" },
        { name: "fecha_fin", label: "Fecha fin", type: "date" },
        { name: "observaciones", label: "Observaciones", type: "textarea", fullWidth: true },
    ];

    // Record para el form: montos céntimos→soles para mostrar; tipos_objeto ya es array (API).
    const formRecord = item ? {
        ...item,
        monto_principal: item.monto_principal != null ? item.monto_principal / 100 : "",
        monto_accesorio: item.monto_accesorio != null ? item.monto_accesorio / 100 : "",
    } : null;
    buildFormFields(form, fields, formRecord, isEdit);

    const footer = createModalFooter([
        (() => { const b = document.createElement("button"); b.className = "btn btn--secondary"; b.type = "button"; b.textContent = "Cancelar"; b.addEventListener("click", () => modal.close()); return b; })(),
        (() => { const b = document.createElement("button"); b.className = "btn btn--primary"; b.type = "submit"; b.textContent = isEdit ? "Guardar cambios" : "Crear Contrato"; return b; })(),
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
        const payload = buildPayload(form, fields, isEdit);
        // Soles→céntimos exactos. Math.round elimina el residuo float de *100.
        ["monto_principal", "monto_accesorio"].forEach(k => {
            if (payload[k] !== undefined) payload[k] = Math.round(payload[k] * 100);
        });
        if (payload.objeto === undefined) {
            showToast("El objeto es obligatorio", "error");
            return;
        }
        try {
            if (isEdit) {
                await api.put(`/contratos/${item.id}`, payload);
                showToast("Contrato actualizado correctamente", "success");
                modal.close();
                renderContratoDetail({ id: item.id });
            } else {
                await api.post("/contratos", payload);
                showToast("Contrato creado correctamente", "success");
                modal.close();
                renderContratosList();
            }
        } catch (err) {
            console.error(err);
            showToast("Error: " + (err.message || ""), "error");
        }
    });
}
