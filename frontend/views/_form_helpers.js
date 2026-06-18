// ID policy (all tables use natural keys, not AUTOINCREMENT):
//   - create mode (isEdit=false): id rendered as an editable required input.
//   - edit mode   (isEdit=true):  id rendered as a static text display — never an input,
//     so it cannot appear in FormData or reach the PUT payload.

const NEVER_IN_UPDATE = new Set(["id", "created_at", "updated_at"]);

/**
 * Appends field DOM elements to `container` based on a `fields` descriptor array.
 * `record` is the existing data object (for edit pre-fill) or null (for create).
 */
export function buildFormFields(container, fields, record, isEdit) {
    fields.forEach(f => {
        const group = document.createElement("div");
        group.className = "form-group" + (f.fullWidth ? " full-width" : "");

        const label = document.createElement("label");
        const showAsterisk = f.required && !(f.name === "id" && isEdit);
        label.textContent = f.label + (showAsterisk ? " *" : "");
        group.appendChild(label);

        let el;
        if (f.name === "id" && isEdit) {
            el = document.createElement("span");
            el.className = "form-value-static";
            el.textContent = record?.[f.name] ?? "—";
        } else if (f.type === "select") {
            el = document.createElement("select");
            el.name = f.name;
            el.innerHTML = `<option value="">— Seleccionar —</option>` +
                (f.options || []).map(o => `<option value="${o.value}">${o.label}</option>`).join("");
            if (record?.[f.name] !== undefined && record[f.name] !== null) el.value = record[f.name];
        } else if (f.type === "multiselect") {
            // Conjunto multivalor de tokens (checkbox group). El record trae un array.
            el = document.createElement("div");
            el.className = "form-multiselect";
            const selected = new Set(Array.isArray(record?.[f.name]) ? record[f.name] : []);
            (f.options || []).forEach(o => {
                const lbl = document.createElement("label");
                lbl.className = "form-check";
                const cb = document.createElement("input");
                cb.type = "checkbox";
                cb.name = f.name;
                cb.value = o.value;
                if (selected.has(o.value)) cb.checked = true;
                lbl.appendChild(cb);
                lbl.appendChild(document.createTextNode(" " + o.label));
                el.appendChild(lbl);
            });
        } else if (f.type === "textarea") {
            el = document.createElement("textarea");
            el.name = f.name;
            el.rows = 3;
            if (record?.[f.name] !== undefined && record[f.name] !== null) el.value = record[f.name];
        } else {
            el = document.createElement("input");
            el.type = f.type;
            el.name = f.name;
            if (f.step) el.step = f.step;
            if (record?.[f.name] !== undefined && record[f.name] !== null) el.value = record[f.name];
        }

        group.appendChild(el);
        container.appendChild(group);
    });
}

/**
 * Builds a payload object from the form's FormData.
 *
 * In edit mode, `id`, `created_at`, `updated_at` are always excluded — second defense layer
 * on top of `id` not being rendered as an input in edit mode.
 */
export function buildPayload(form, fields, isEdit) {
    const fd = new FormData(form);
    const payload = {};
    fields.forEach(f => {
        if (isEdit && NEVER_IN_UPDATE.has(f.name)) return;
        if (f.type === "multiselect") {
            // Siempre presente (incluso []) para permitir limpiar la selección al editar.
            payload[f.name] = fd.getAll(f.name);
            return;
        }
        const v = fd.get(f.name);
        if (v !== "" && v !== null && v !== undefined) {
            payload[f.name] = f.type === "number" ? Number(v) : v;
        }
    });
    return payload;
}
