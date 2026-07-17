#!/usr/bin/env python
"""Fase 2 — Aplica la propuesta Valtom LIMPIA (149 GE + 152 TTA) vía la API.

Levanta la app en proceso apuntando a la cache.db REAL con `_test_db_path`
(esto migra la cache.db y SALTA recover_state/snapshot/SharePoint), y aplica los
updates por PUT a través de las rutas reales → se emiten eventos como cualquier
CRUD. NO aplica los 3 de cruce ni los 4 creates (decisión patrimonial aparte).

Correr con la app NORMAL detenida. Hacer backup de data/cache.db antes (hecho).

    uv run python tools_scripts/valtom_apply.py [propuesta.json]
"""
import asyncio
import json
import sys
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from backend.server import create_app

PROP = sys.argv[1] if len(sys.argv) > 1 else "tools_scripts/valtom_propuesta.json"
REAL_DB = Path(sys.argv[2] if len(sys.argv) > 2 else "data/cache.db")
STORAGE = Path("data/_valtom_apply_storage")  # local; los eventos son subproducto


def payload_from(cambios: dict) -> dict:
    """{campo: {'de':x,'a':y}} -> {campo: y}."""
    return {k: v["a"] for k, v in cambios.items()}


async def main():
    prop = json.loads(Path(PROP).read_text(encoding="utf-8"))
    ge_updates = [u for u in prop["ge_updates"] if u.get("cambios")]
    tta_updates = [u for u in prop["tta_updates"] if u.get("cambios")]
    print(f"A aplicar: {len(ge_updates)} GE · {len(tta_updates)} TTA "
          f"(NO se tocan {len(prop['ge_updates_revisar'])} revisar / "
          f"{len(prop['ge_creates'])} creates)")

    STORAGE.mkdir(parents=True, exist_ok=True)
    app = create_app()
    app["_test_db_path"] = REAL_DB          # cache.db REAL → migra + salta SharePoint
    app["_test_storage_path"] = STORAGE

    ge_ok = ge_err = tta_ok = tta_err = 0
    errors = []
    async with TestClient(TestServer(app)) as c:
        for u in ge_updates:
            gid = u["sige_ge_id"]
            r = await c.put(f"/api/grupos/{gid}", json=payload_from(u["cambios"]))
            if r.status == 200:
                ge_ok += 1
            else:
                ge_err += 1
                errors.append((f"GE{gid}", r.status, (await r.json()).get("reason")))
        for u in tta_updates:
            tid = u["sige_tta_id"]
            r = await c.put(f"/api/tta/{tid}", json=payload_from(u["cambios"]))
            if r.status == 200:
                tta_ok += 1
            else:
                tta_err += 1
                errors.append((f"TTA{tid}", r.status, (await r.json()).get("reason")))

    print(f"\nGE:  {ge_ok} ok / {ge_err} error")
    print(f"TTA: {tta_ok} ok / {tta_err} error")
    if errors:
        print("\nERRORES:")
        for e in errors[:30]:
            print("  ", e)


if __name__ == "__main__":
    asyncio.run(main())
