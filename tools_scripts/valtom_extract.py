#!/usr/bin/env python
"""Fase 1 — Extracción Valtom → propuesta de integración a SIGE-GE (SOLO LECTURA).

Lee sg-valtom/data/valtom.db y la cache.db de SIGE-GE, cruza por `margesi`, y
produce un JSON de PROPUESTA (updates / creates / ambiguos, y TTA) para revisar
ANTES de aplicar nada. NO escribe en ninguna base. La carga real (vía API +
eventos) es la Fase 2.

Uso:
    uv run python tools_scripts/valtom_extract.py [valtom.db] [cache.db] [salida.json]
"""
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

# La consola de Windows es cp1252 y no imprime '→'/acentos; la salida es informativa.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VALTOM_DB = sys.argv[1] if len(sys.argv) > 1 else \
    "C:/Users/Jamin/Documents/Projects/sg-valtom/data/valtom.db"
CACHE_DB = sys.argv[2] if len(sys.argv) > 2 else "data/cache.db"
OUT_JSON = sys.argv[3] if len(sys.argv) > 3 else \
    "tools_scripts/valtom_propuesta.json"

# margesi con cruce/duplicado conocido (docs/Elementos/PENDIENTES_VERIFICACION.md):
# no se actualizan en automático aunque crucen por margesi — van a revisión.
MARGESI_CRUCE_CONOCIDO = {
    "602096", "602182", "602138",  # CUSCO: COMBAPATA/SICUANI/HUAYOPATA cruzados
    "381756",                        # HUANCAYO: ACOBAMBA-TARMA/JUNIN duplicado
    "426735",                        # LIMA: COMAS 3 / LAS PALMAS compartido
    "366234",                        # TRUJILLO: USQUIL/PUERTO MALABRIGO dup en Excel
}


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def parse_kw(s):
    """'9 KW' -> 9.0 ; '' / None -> None."""
    if not s:
        return None
    m = re.search(r"[\d.]+", str(s))
    return float(m.group()) if m else None


def norm_voltaje(s):
    return s.replace(" ", "").upper() if s else None       # '220 VAC' -> '220VAC'


def norm_frecuencia(s):
    if not s:
        return None
    t = re.sub(r"(?i)hz", "", s).strip()
    return f"{t}Hz" if t else None                          # '60HZ' -> '60Hz'


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def key_nombre(s):
    # quita sufijos "(ex ...)" y colapsa espacios/acentos para comparar nombres
    s = re.sub(r"\(.*?\)", " ", s or "")
    return re.sub(r"\s+", " ", strip_accents(s.upper())).strip()


def nombre_similar(a, b):
    """True si dos nombres de agencia son 'el mismo sitio' pese a variantes de
    escritura (COMAS I/COMAS, VILCASHUAMAN/VILCAS HUAMAN, SAN JUAN MARCONA/
    SAN JUAN DE MARCONA). El margesi ya es la clave dura; esto solo evita marcar
    como 'revisar' una simple diferencia de nombre."""
    ka, kb = key_nombre(a), key_nombre(b)
    if not ka or not kb:
        return False
    if ka == kb or ka in kb or kb in ka:
        return True
    ca, cb = ka.replace(" ", ""), kb.replace(" ", "")
    if ca == cb or ca in cb or cb in ca:          # VILCASHUAMAN ~ VILCAS HUAMAN
        return True
    ta, tb = set(ka.split()) - {"DE"}, set(kb.split()) - {"DE"}
    if not ta or not tb:
        return False
    # todos los tokens del nombre más corto deben estar en el más largo
    # (COMAS I ⊇ COMAS ✓ ; SAN MARCOS vs SAN ISIDRO comparten solo 'SAN' ✗)
    menor = ta if len(ta) <= len(tb) else tb
    return (ta & tb) == menor


def map_ge(v):
    """Fila valtom grupo_electrogeno -> dict de columnas SIGE-GE (solo con valor)."""
    m = {
        "cod_margesi": v["margesi"],
        "serie_ensamblador": v["serie"],
        "marca_ensamblador": v["marca"],
        "modelo_ensamblador": v["modelo"],
        "cod_fabricante": v["cod_fabricante"],
        "potencia_kw": parse_kw(v["potencia_standby"]),
        "potencia_efectiva_kw": parse_kw(v["potencia_efectiva"]),
        "voltaje": norm_voltaje(v["voltaje"]),
        "frecuencia": norm_frecuencia(v["frecuencia"]),
        "fase_electrica": v["fases"],
        "marca_motor": v["motor_marca"],
        "modelo_motor": v["motor_modelo"],
        "marca_alternador": v["alternador_marca"],
        "modelo_alternador": v["alternador_modelo"],
        "etiqueta": v["etiqueta"],
        "fecha_garantia_ini": v["fecha_garantia_ini"],
        "fecha_garantia_fin": v["fecha_garantia_fin"],
    }
    return {k: val for k, val in m.items() if val not in (None, "")}


def main():
    vt = ro(VALTOM_DB)
    vt.row_factory = sqlite3.Row
    sg = ro(CACHE_DB)
    sg.row_factory = sqlite3.Row

    # ---- Valtom: GE por margesi + agencia; TTA por grupo_id ----
    vge = {}   # margesi -> row valtom GE
    vge_by_id = {}
    for r in vt.execute("""
        SELECT ge.*, a.nombre AS agencia_nombre, a.departamento AS agencia_depto
        FROM grupo_electrogeno ge LEFT JOIN agencia a ON a.id = ge.agencia_id
    """):
        mg = (r["margesi"] or "").strip()
        vge_by_id[r["id"]] = r
        if mg:
            vge.setdefault(mg, []).append(r)
    vtta = {r["grupo_id"]: r for r in vt.execute("SELECT * FROM tablero_tta")}

    # ---- SIGE-GE: GE por margesi (lista, para detectar dups); sedes por nombre ----
    sg_ge = {}
    for r in sg.execute("""
        SELECT g.*, s.nombre_agencia AS sede_nombre
        FROM grupos_electrogenos g JOIN sedes s ON s.id = g.sede_id
    """):
        mg = (r["cod_margesi"] or "").strip()
        if mg:
            sg_ge.setdefault(mg, []).append(r)
    sede_by_key = {}
    for s in sg.execute("SELECT id, nombre_agencia FROM sedes WHERE activo = 1"):
        sede_by_key.setdefault(key_nombre(s["nombre_agencia"]), []).append(dict(s))
    ge_por_sede = {}
    for r in sg.execute("SELECT id, sede_id, cod_margesi, marca_ensamblador, anio_fabricacion FROM grupos_electrogenos"):
        ge_por_sede.setdefault(r["sede_id"], []).append(dict(r))
    # SIGE-GE TTA por margesi. NO se lee ge_id: la cache.db real se migra al
    # arrancar la app; hoy ge_id es NULL en las 154 (sin vínculo). Solo se
    # necesita id/sede/margesi para cruzar.
    sg_tta = {}
    for r in sg.execute("SELECT id, sede_id, cod_margesi FROM tta"):
        mg = (r["cod_margesi"] or "").strip()
        if mg:
            sg_tta.setdefault(mg, []).append(dict(r))

    updates, creates, ambiguous, revisar_cruce = [], [], [], []

    for mg, rows in vge.items():
        v = rows[0]
        mapped = map_ge(v)
        matches = sg_ge.get(mg, [])

        if len(matches) > 1:
            ambiguous.append({
                "margesi": mg, "valtom_agencia": v["agencia_nombre"],
                "sige_ge_ids": [m["id"] for m in matches],
                "motivo": "margesi cruza con >1 GE en SIGE-GE",
            })
            continue

        if not matches:  # CREATE (los 4): decisión patrimonial, no auto
            cand = [s for slist in sede_by_key.values() for s in slist
                    if nombre_similar(v["agencia_nombre"], s["nombre_agencia"])]
            creates.append({
                "margesi": mg,
                "valtom_agencia": v["agencia_nombre"],
                "valtom_depto": v["agencia_depto"],
                "candidato_sede": cand[0] if len(cand) == 1 else None,
                "candidatos_sede_ambiguos": cand if len(cand) != 1 else None,
                "ge_existentes_en_sede": ge_por_sede.get(cand[0]["id"], []) if len(cand) == 1 else [],
                "mapped": mapped,
                "requiere_decision": "reemplazo de equipo o alta nueva (ver PENDIENTES)",
            })
            continue

        # UPDATE: diff campo a campo (solo donde valtom aporta valor y difiere)
        cur = matches[0]
        changes = {}
        for k, newv in mapped.items():
            if k == "cod_margesi":
                continue
            oldv = cur[k] if k in cur.keys() else None
            # observaciones: no sobreescribir; solo rellenar si está vacío
            if isinstance(oldv, float) and isinstance(newv, (int, float)):
                if abs(oldv - newv) < 1e-9:
                    continue
            if str(oldv or "") != str(newv):
                changes[k] = {"de": oldv, "a": newv}

        smatch = nombre_similar(v["agencia_nombre"], cur["sede_nombre"])
        entry = {
            "margesi": mg, "sige_ge_id": cur["id"], "sede_id": cur["sede_id"],
            "sede_nombre": cur["sede_nombre"], "valtom_agencia": v["agencia_nombre"],
            "cambios": changes,
        }
        # El margesi es la clave dura y cruza 1:1 → basta para actualizar specs.
        # Solo los margesi con cruce PATRIMONIAL conocido (PENDIENTES) se apartan;
        # una diferencia de NOMBRE de sede es solo informativa (nota_sede).
        if mg in MARGESI_CRUCE_CONOCIDO:
            entry["flag"] = "margesi con cruce conocido (PENDIENTES) -> NO auto, revisar"
            revisar_cruce.append(entry)
        else:
            if not smatch:
                entry["nota_sede"] = (
                    f"nombre difiere: valtom '{v['agencia_nombre']}' vs SIGE-GE "
                    f"'{cur['sede_nombre']}' (margesi 1:1, se actualiza igual)"
                )
            updates.append(entry)

    # ---- TTA: vincular ge_id + refrescar specs, cruzando por margesi ----
    tta_updates, tta_sin_match = [], []
    for gid, t in vtta.items():
        tmg = (t["margesi"] or "").strip()
        vge_row = vge_by_id.get(gid)
        ge_mg = (vge_row["margesi"] or "").strip() if vge_row else ""
        sige_ge_match = sg_ge.get(ge_mg, [])
        set_ge_id = sige_ge_match[0]["id"] if len(sige_ge_match) == 1 else None
        sige_tta = sg_tta.get(tmg, [])
        if len(sige_tta) == 1:
            cur = sige_tta[0]
            changes = {}
            proposed = {
                "ge_id": set_ge_id, "marca": t["marca"], "modelo": t["modelo"],
                "serie": t["serie"], "etiqueta": t["etiqueta"],
            }
            for k, newv in proposed.items():
                if newv in (None, ""):
                    continue
                oldv = cur.get(k)
                if str(oldv or "") != str(newv):
                    changes[k] = {"de": oldv, "a": newv}
            if changes:
                tta_updates.append({
                    "tta_margesi": tmg, "sige_tta_id": cur["id"],
                    "sede_id": cur["sede_id"], "cambios": changes,
                })
        else:
            tta_sin_match.append({
                "tta_margesi": tmg, "valtom_grupo_id": gid,
                "ge_margesi": ge_mg, "matches_en_sige": len(sige_tta),
            })

    out = {
        "_meta": {
            "valtom_db": VALTOM_DB, "cache_db": CACHE_DB,
            "valtom_ge": len(vge_by_id), "valtom_margesi_distintos": len(vge),
        },
        "resumen": {
            "ge_update_auto": len(updates),
            "ge_update_revisar": len(revisar_cruce),
            "ge_create": len(creates),
            "ge_ambiguo": len(ambiguous),
            "tta_update": len(tta_updates),
            "tta_sin_match_1a1": len(tta_sin_match),
        },
        "ge_updates": updates,
        "ge_updates_revisar": revisar_cruce,
        "ge_creates": creates,
        "ge_ambiguous": ambiguous,
        "tta_updates": tta_updates,
        "tta_sin_match": tta_sin_match,
    }
    Path(OUT_JSON).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print("== PROPUESTA VALTOM → SIGE-GE (solo lectura) ==")
    for k, v in out["resumen"].items():
        print(f"  {k:24} {v}")
    print(f"\nJSON escrito en: {OUT_JSON}")
    if creates:
        print("\n-- CREATES (decisión patrimonial) --")
        for c in creates:
            sede = c["candidato_sede"]
            print(f"  margesi {c['margesi']} · {c['valtom_agencia']} ({c['valtom_depto']}) "
                  f"-> sede candidata: {sede['nombre_agencia'] + ' #' + str(sede['id']) if sede else 'SIN MATCH / ambiguo'}; "
                  f"GE ya en esa sede: {[g['id'] for g in c['ge_existentes_en_sede']]}")
    if revisar_cruce:
        print("\n-- UPDATES A REVISAR (cruce/sede) --")
        for e in revisar_cruce:
            print(f"  margesi {e['margesi']} · GE{e['sige_ge_id']} · {e['flag']}")


if __name__ == "__main__":
    main()
