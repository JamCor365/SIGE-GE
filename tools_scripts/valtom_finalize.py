#!/usr/bin/env python
"""Cierre de la integración Valtom — casos confirmados (vía API, in-proceso).

Aplica lo confirmado tras la investigación (correos-db + relación inicial):
  A) 3 cruces Cusco (update de specs): GE332/335/336 — la BD ya los tenía bien.
  B) 3 creates limpios (GE + TTA): San Marcos Áncash, Abancay, Urubamba.
  C) Marca INOPERATIVO las 2 unidades 2008 reemplazadas (Abancay GE316, Urubamba GE311).

NO toca Chilca (602048): es un mislabel — SIGE-GE GE64 lleva el margesi del TTA
(602204); requiere corrección manual, no un create. Ver reporte.

Backup de data/cache.db hecho antes. Correr con la app normal detenida.
    uv run python tools_scripts/valtom_finalize.py
"""
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from backend.server import create_app

CACHE = Path("data/cache.db")
STORAGE = Path("data/_valtom_apply_storage")
PROP = "tools_scripts/valtom_propuesta.json"
VALTOM = "C:/Users/Jamin/Documents/Projects/sg-valtom/data/valtom.db"

# margesi -> (sede_id, nuevo_ge_id, nuevo_tta_id, nota). Resolución de sede:
#   602135 San Marcos ÁNCASH -> #447 (la #224 ya tiene 602107=San Marcos Cajamarca)
#   602184 Abancay -> #114 (reemplaza GE316 2008)
#   602180 Urubamba -> #109 (reemplaza GE311 2008)
CREATES = {
    "602135": (447, 100001, 155, "San Marcos Áncash"),
    "602184": (114, 100002, 156, "Abancay (reemplaza GE316)"),
    "602180": (109, 100003, 157, "Urubamba (reemplaza GE311)"),
}
# GE viejo -> (margesi nuevo, ge_id nuevo) para la nota de reemplazo
REEMPLAZOS = {316: ("602184", 100002), 311: ("602180", 100003)}


def parse_kw(s):
    if not s:
        return None
    m = re.search(r"[\d.]+", str(s))
    return float(m.group()) if m else None


def norm_v(s):
    return s.replace(" ", "").upper() if s else None


def norm_f(s):
    if not s:
        return None
    t = re.sub(r"(?i)hz", "", s).strip()
    return f"{t}Hz" if t else None


def clean(d):
    return {k: v for k, v in d.items() if v not in (None, "")}


def ge_payload(v, ge_id, sede_id):
    return clean({
        "id": ge_id, "sede_id": sede_id, "estado": "OPERATIVO",
        "cod_margesi": v["margesi"], "etiqueta": v["etiqueta"],
        "serie_ensamblador": v["serie"], "marca_ensamblador": v["marca"],
        "modelo_ensamblador": v["modelo"], "cod_fabricante": v["cod_fabricante"],
        "potencia_kw": parse_kw(v["potencia_standby"]),
        "potencia_efectiva_kw": parse_kw(v["potencia_efectiva"]),
        "voltaje": norm_v(v["voltaje"]), "frecuencia": norm_f(v["frecuencia"]),
        "fase_electrica": v["fases"],
        "marca_motor": v["motor_marca"], "modelo_motor": v["motor_modelo"],
        "marca_alternador": v["alternador_marca"], "modelo_alternador": v["alternador_modelo"],
        "fecha_garantia_ini": v["fecha_garantia_ini"], "fecha_garantia_fin": v["fecha_garantia_fin"],
    })


def tta_payload(t, tta_id, sede_id, ge_id):
    return clean({
        "id": tta_id, "sede_id": sede_id, "ge_id": ge_id, "estado": "OPERATIVO",
        "cod_margesi": t["margesi"], "etiqueta": t["etiqueta"],
        "marca": t["marca"], "modelo": t["modelo"], "serie": t["serie"],
    })


async def main():
    vt = sqlite3.connect(f"file:{VALTOM}?mode=ro", uri=True)
    vt.row_factory = sqlite3.Row
    prop = json.loads(Path(PROP).read_text(encoding="utf-8"))
    cruces = [u for u in prop["ge_updates_revisar"] if "cruce" in (u.get("flag") or "") and u.get("cambios")]

    STORAGE.mkdir(parents=True, exist_ok=True)
    app = create_app()
    app["_test_db_path"] = CACHE
    app["_test_storage_path"] = STORAGE
    res = {"cruces": [], "creates": [], "ttas": [], "viejos": []}

    async with TestClient(TestServer(app)) as c:
        # A) 3 cruces (update specs)
        for u in cruces:
            r = await c.put(f"/api/grupos/{u['sige_ge_id']}", json={k: v["a"] for k, v in u["cambios"].items()})
            res["cruces"].append((u["sige_ge_id"], r.status))
        # B) 3 creates GE + TTA
        for mg, (sede_id, ge_id, tta_id, nota) in CREATES.items():
            v = vt.execute("SELECT * FROM grupo_electrogeno WHERE margesi=?", (mg,)).fetchone()
            t = vt.execute("SELECT * FROM tablero_tta WHERE grupo_id=?", (v["id"],)).fetchone()
            r = await c.post("/api/grupos", json=ge_payload(v, ge_id, sede_id))
            res["creates"].append((mg, nota, ge_id, r.status))
            if r.status == 201 and t:
                rt = await c.post("/api/tta", json=tta_payload(t, tta_id, sede_id, ge_id))
                res["ttas"].append((mg, tta_id, rt.status))
        # C) marcar viejos INOPERATIVO
        for old_id, (mg_new, ge_new) in REEMPLAZOS.items():
            r = await c.put(f"/api/grupos/{old_id}", json={
                "estado": "INOPERATIVO",
                "observaciones": f"Unidad 2008 retirada 2025; reemplazada por GE {ge_new} (margesi {mg_new}, Valtom).",
            })
            res["viejos"].append((old_id, r.status))

    print("A) cruces (update):", res["cruces"])
    print("B) creates GE:     ", res["creates"])
    print("   creates TTA:    ", res["ttas"])
    print("C) viejos INOPER.: ", res["viejos"])


if __name__ == "__main__":
    asyncio.run(main())
