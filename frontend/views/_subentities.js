// Panel reutilizable de sub-entidades de un contrato (adendas, penalidades,
// servicios, adjuntos, …). Renderiza una sección con lista + botón "Añadir" y
// gestiona el CRUD vía modal, reusando table/modal/_form_helpers.
//
// config: {
//   key, title, singular,
//   path:      (cid)      => "/contratos/{cid}/adendas",       // list + create
//   itemPath:  (cid, row) => "/contratos/{cid}/adendas/{id}",  // put + delete
//   columns:   [ {header, key|accessor|render} ],              // tabla
//   fields:    [ ...descriptores de _form_helpers ],           // formulario
//   montoFields?: ["monto"],       // soles en el form ↔ céntimos en la API
//   intFields?:   ["ge_id"],       // selects que deben viajar como enteros
//   loadOptions?: async (cid) => ({ ge_id: [{value,label}], … }),  // pobla selects FK
//   enrich?:      async (cid, rows) => rows,   // decora filas antes de la tabla
//   noEdit?:      true,            // la entidad no expone PUT (p.ej. contrato_ge)
//   gender?:      "m" | "f",       // concordancia de los toasts; "f" por defecto
//   addLabel?, deleteLabel?, deleteConfirm?,   // textos; hay defaults razonables
// }
//
// itemPath recibe la FILA, no el id: la ruta de cada entidad no siempre se
// construye con `id` — items_contrato direcciona por `numero_item` y contrato_ge
// por `ge_id` (cuyo listado ni siquiera devuelve una columna `id`).
import { api } from "../api.js";
import { renderTable } from "../components/table.js";
import { openModal, createModalHeader, createModalFooter } from "../components/modal.js";
import { showToast } from "../toast.js";
import { buildFormFields, buildPayload } from "./_form_helpers.js";

export function formatMonto(centimos, moneda = "PEN") {
    if (centimos === null || centimos === undefined) return "—";
    const soles = (centimos / 100).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `${moneda === "PEN" ? "S/" : moneda} ${soles}`;
}

/** Crea y devuelve la sección DOM del panel; se autogestiona (carga y refresca). */
export function renderSubentityPanel(contratoId, config) {
    const section = document.createElement("div");
    section.className = "detail-section subentity-panel";

    const head = document.createElement("div");
    head.className = "subentity-panel__head";
    const h3 = document.createElement("h3");
    h3.textContent = config.title;
    head.appendChild(h3);
    const btnAdd = document.createElement("button");
    btnAdd.className = "btn btn--primary btn--sm";
    btnAdd.textContent = config.addLabel || `+ ${config.singular}`;
    btnAdd.addEventListener("click", () => openSubentityModal(contratoId, config, null, load));
    head.appendChild(btnAdd);
    section.appendChild(head);

    const tableContainer = document.createElement("div");
    tableContainer.className = "table-wrapper";
    section.appendChild(tableContainer);

    // Columna de acciones (editar / baja) añadida a las columnas de config.
    const columns = [
        ...config.columns,
        {
            header: "", render: row => {
                const wrap = document.createElement("div");
                wrap.style.display = "flex";
                wrap.style.gap = "6px";
                if (!config.noEdit) {
                    const edit = document.createElement("button");
                    edit.className = "btn btn--ghost btn--sm";
                    edit.textContent = "Editar";
                    edit.addEventListener("click", (e) => { e.stopPropagation(); openSubentityModal(contratoId, config, row, load); });
                    wrap.appendChild(edit);
                }
                const del = document.createElement("button");
                del.className = "btn btn--danger btn--sm";
                del.textContent = config.deleteLabel || "Baja";
                del.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    const ask = config.deleteConfirm || `¿Dar de baja este registro de ${config.singular.toLowerCase()}?`;
                    if (!confirm(ask)) return;
                    try {
                        await api.del(config.itemPath(contratoId, row));
                        showToast("Registro dado de baja", "success");
                        load();
                    } catch (err) {
                        showToast("Error: " + (err.message || ""), "error");
                    }
                });
                wrap.appendChild(del);
                return wrap;
            },
        },
    ];

    async function load() {
        try {
            const res = await api.get(config.path(contratoId));
            // Los listados devuelven las FK crudas (un UUID no le dice nada a
            // nadie); enrich las resuelve a etiquetas legibles antes de pintar.
            const rows = config.enrich ? await config.enrich(contratoId, res.data || []) : (res.data || []);
            renderTable(tableContainer, columns, rows, { emptyText: `Sin ${config.title.toLowerCase()}` });
        } catch (err) {
            console.error(err);
            renderTable(tableContainer, columns, [], { emptyText: `Error al cargar ${config.title.toLowerCase()}` });
        }
    }
    load();
    return section;
}

async function openSubentityModal(contratoId, config, item, onSaved) {
    const isEdit = !!item;
    const fields = config.fields.map(f => ({ ...f }));

    // Poblar selects FK (p.ej. ge_id desde /grupos) antes de construir el form.
    if (config.loadOptions) {
        try {
            const opts = await config.loadOptions(contratoId);
            fields.forEach(f => { if (opts[f.name]) f.options = opts[f.name]; });
        } catch (err) {
            showToast("No se pudieron cargar opciones: " + (err.message || ""), "error");
        }
    }

    const body = document.createElement("div");
    body.className = "modal__body";
    body.style.maxHeight = "85vh";
    body.style.overflowY = "auto";
    const form = document.createElement("form");
    form.className = "form-grid";

    // Prefill: montos céntimos→soles para mostrar.
    let record = item;
    if (item && config.montoFields) {
        record = { ...item };
        config.montoFields.forEach(k => {
            if (record[k] !== null && record[k] !== undefined) record[k] = record[k] / 100;
        });
    }
    buildFormFields(form, fields, record, isEdit);

    const footer = createModalFooter([
        (() => { const b = document.createElement("button"); b.className = "btn btn--secondary"; b.type = "button"; b.textContent = "Cancelar"; b.addEventListener("click", () => modal.close()); return b; })(),
        (() => { const b = document.createElement("button"); b.className = "btn btn--primary"; b.type = "submit"; b.textContent = isEdit ? "Guardar" : "Crear"; return b; })(),
    ]);
    footer.style.gridColumn = "1 / -1";
    form.appendChild(footer);
    body.appendChild(form);

    const modalRoot = document.createElement("div");
    modalRoot.appendChild(createModalHeader(`${isEdit ? "Editar" : "Registrar"} ${config.singular}`, () => modal.close()));
    modalRoot.appendChild(body);
    const modal = openModal(modalRoot);

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = buildPayload(form, fields, isEdit);
        // Soles→céntimos exactos (Math.round elimina el residuo float de *100).
        (config.montoFields || []).forEach(k => {
            if (payload[k] !== undefined) payload[k] = Math.round(payload[k] * 100);
        });
        // Selects que deben viajar como enteros (buildPayload deja los select como string).
        (config.intFields || []).forEach(k => {
            if (payload[k] !== undefined && payload[k] !== "") payload[k] = Number(payload[k]);
        });
        try {
            const done = config.gender === "m" ? ["creado", "actualizado"] : ["creada", "actualizada"];
            const res = isEdit
                ? await api.put(config.itemPath(contratoId, item), payload)
                : await api.post(config.path(contratoId), payload);
            showToast(`${config.singular} ${isEdit ? done[1] : done[0]}`, "success");
            // Reglas BLANDAS del backend (p.ej. 2ª prestación PRINCIPAL en el mismo
            // ámbito): la fila se guardó igual, pero el aviso solo existe aquí.
            (res.warnings || []).forEach(w => showToast(w, "warning"));
            modal.close();
            onSaved();
        } catch (err) {
            console.error(err);
            showToast("Error: " + (err.message || ""), "error");
        }
    });
}
