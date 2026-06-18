import json

from aiohttp import web

from backend.db import init_db
from backend.sync_engine import _apply_one


async def _mk_macro(client, mid, nombre):
    await client.post("/api/macroregiones", json={"id": mid, "nombre": nombre})


async def _mk_sede(client, sid, macro_id, agencia=None):
    await client.post("/api/sedes", json={
        "id": sid, "codigo": f"AG-{sid:04d}",
        "nombre_agencia": agencia or f"Agencia {sid}", "macroregion_id": macro_id,
    })


async def _mk_ge(client, gid, sede_id):
    await client.post("/api/grupos", json={"id": gid, "sede_id": sede_id})


async def _mk_contrato(client, objeto="Valtom"):
    r = await client.post("/api/contratos", json={"objeto": objeto})
    return (await r.json())["data"]["id"]


# --- Vincular / desvincular ----------------------------------------------------

async def test_vincular_ge(client):
    await _mk_macro(client, 1, "M1"); await _mk_sede(client, 1, 1); await _mk_ge(client, 10, 1)
    cid = await _mk_contrato(client)

    r = await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})
    assert r.status == 201
    data = (await r.json())["data"]
    assert data["id"] == f"{cid}_10"        # id determinista del par
    assert data["activo"] == 1

    lst = (await (await client.get(f"/api/contratos/{cid}/ge")).json())["data"]
    assert len(lst) == 1 and lst[0]["ge_id"] == 10


async def test_vincular_idempotente(client):
    await _mk_macro(client, 1, "M1"); await _mk_sede(client, 1, 1); await _mk_ge(client, 10, 1)
    cid = await _mk_contrato(client)

    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})
    r2 = await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})
    assert r2.status == 200   # ya activo → idempotente
    lst = (await (await client.get(f"/api/contratos/{cid}/ge")).json())["data"]
    assert len(lst) == 1      # sin duplicado (UNIQUE par)


async def test_desvincular(client):
    await _mk_macro(client, 1, "M1"); await _mk_sede(client, 1, 1); await _mk_ge(client, 10, 1)
    cid = await _mk_contrato(client)
    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})

    r = await client.delete(f"/api/contratos/{cid}/ge/10")
    assert r.status == 200
    assert (await r.json())["data"]["activo"] == 0
    lst = (await (await client.get(f"/api/contratos/{cid}/ge")).json())["data"]
    assert lst == []          # baja lógica → fuera de la lista activa


async def test_revincular_reactiva(client):
    """Re-vincular un par dado de baja debe reactivarlo (no duplicar, no quedar inactivo)."""
    await _mk_macro(client, 1, "M1"); await _mk_sede(client, 1, 1); await _mk_ge(client, 10, 1)
    cid = await _mk_contrato(client)
    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})
    await client.delete(f"/api/contratos/{cid}/ge/10")

    r = await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})
    assert r.status == 200
    assert (await r.json())["data"]["activo"] == 1
    lst = (await (await client.get(f"/api/contratos/{cid}/ge")).json())["data"]
    assert len(lst) == 1


async def test_vincular_ge_inexistente_404(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 999})
    assert r.status == 404


async def test_vincular_contrato_inexistente_404(client):
    await _mk_macro(client, 1, "M1"); await _mk_sede(client, 1, 1); await _mk_ge(client, 10, 1)
    r = await client.post("/api/contratos/no-existe/ge", json={"ge_id": 10})
    assert r.status == 404


async def test_vincular_requiere_ge_id_entero(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": "10"})
    assert r.status == 400


# --- Alcance derivado ----------------------------------------------------------

async def test_alcance_derivado(client):
    await _mk_macro(client, 1, "MACRO NORTE"); await _mk_macro(client, 2, "MACRO SUR")
    await _mk_sede(client, 1, 1, "Agencia Norte"); await _mk_sede(client, 2, 2, "Agencia Sur")
    await _mk_ge(client, 10, 1); await _mk_ge(client, 20, 2)
    cid = await _mk_contrato(client)
    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})
    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 20})

    data = (await (await client.get(f"/api/contratos/{cid}/alcance")).json())["data"]
    assert data["total_ge"] == 2
    assert data["total_macroregiones"] == 2
    assert data["total_agencias"] == 2
    assert [m["macroregion"] for m in data["macroregiones"]] == ["MACRO NORTE", "MACRO SUR"]


async def test_alcance_dedupe_misma_sede(client):
    """Dos GE de la misma sede → una agencia y una macrorregión, pero dos GE."""
    await _mk_macro(client, 1, "M1"); await _mk_sede(client, 1, 1)
    await _mk_ge(client, 10, 1); await _mk_ge(client, 11, 1)
    cid = await _mk_contrato(client)
    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})
    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 11})

    data = (await (await client.get(f"/api/contratos/{cid}/alcance")).json())["data"]
    assert data["total_ge"] == 2
    assert data["total_macroregiones"] == 1
    assert data["total_agencias"] == 1


async def test_alcance_excluye_desvinculado(client):
    await _mk_macro(client, 1, "M1"); await _mk_macro(client, 2, "M2")
    await _mk_sede(client, 1, 1); await _mk_sede(client, 2, 2)
    await _mk_ge(client, 10, 1); await _mk_ge(client, 20, 2)
    cid = await _mk_contrato(client)
    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 10})
    await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 20})
    await client.delete(f"/api/contratos/{cid}/ge/20")

    data = (await (await client.get(f"/api/contratos/{cid}/alcance")).json())["data"]
    assert data["total_ge"] == 1
    assert data["total_macroregiones"] == 1
    assert [m["macroregion"] for m in data["macroregiones"]] == ["M1"]


# --- Round-trip de sync con PK compuesta (identidad determinista) --------------

async def test_round_trip_sync_pk_compuesta(client, tmp_path):
    """El vínculo (PK compuesta materializada como id determinista) debe fluir
    idéntico a otra cache.db vía apply_remote, sin tocar el motor."""
    await _mk_macro(client, 2, "M2"); await _mk_sede(client, 3, 2); await _mk_ge(client, 7, 3)
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/ge", json={"ge_id": 7})
    link_id = (await r.json())["data"]["id"]
    assert link_id == f"{cid}_7"

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='contrato_ge' AND entity_id=? AND action='create'",
        (link_id,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])
    # entity_id transportado = id determinista; no hay campo escalar perdido.
    assert payload["id"] == link_id and payload["contrato_id"] == cid and payload["ge_id"] == 7

    # Otra cache.db: sembrar los padres (FK) y aplicar el evento puente.
    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    await db2.execute("INSERT INTO macroregiones(id,nombre,activo,created_at,updated_at) VALUES(2,'M2',1,'t','t')")
    await db2.execute("INSERT INTO sedes(id,codigo,nombre_agencia,macroregion_id,activo,created_at,updated_at) VALUES(3,'AG-0003','Ag3',2,1,'t','t')")
    await db2.execute("INSERT INTO grupos_electrogenos(id,sede_id,activo,created_at,updated_at) VALUES(7,3,1,'t','t')")
    await db2.execute("INSERT INTO contratos(id,objeto,moneda,activo,created_at,updated_at) VALUES(?,'V','PEN',1,'t','t')", (cid,))
    await db2.commit()

    await _apply_one(db2, {"entity": "contrato_ge", "action": "create", "entity_id": link_id, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT * FROM contrato_ge WHERE id=?", (link_id,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()

    assert remote["id"] == link_id
    assert remote["contrato_id"] == cid
    assert remote["ge_id"] == 7
    assert remote["activo"] == 1
