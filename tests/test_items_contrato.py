import json

from aiohttp import web

from backend.db import init_db
from backend.sync_engine import _apply_one


async def _mk_contrato(client, objeto="C"):
    return (await (await client.post("/api/contratos", json={"objeto": objeto})).json())["data"]["id"]


async def _mk_proveedor(client, razon_social, ruc=None, tipo=None):
    body = {"razon_social": razon_social}
    if ruc:
        body["ruc"] = ruc
    if tipo:
        body["tipo"] = tipo
    return (await (await client.post("/api/proveedores", json=body)).json())["data"]["id"]


# --- Identidad / CRUD ----------------------------------------------------------

async def test_create_id_determinista(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1})
    assert r.status == 201
    data = (await r.json())["data"]
    assert data["id"] == f"{cid}_1"          # id determinista del par
    assert data["numero_item"] == 1
    assert data["activo"] == 1
    assert data["proveedor_id"] is None      # sin adjudicar aún


async def test_numero_item_requerido_entero(client):
    cid = await _mk_contrato(client)
    assert (await client.post(f"/api/contratos/{cid}/items", json={})).status == 400
    assert (await client.post(f"/api/contratos/{cid}/items", json={"numero_item": "1"})).status == 400


async def test_numero_item_inmutable_no_actualizable(client):
    cid = await _mk_contrato(client)
    await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1})
    r = await client.put(f"/api/contratos/{cid}/items/1", json={"numero_item": 2})
    assert r.status == 400                   # numero_item no está en UPDATABLE_FIELDS


async def test_contrato_inexistente_404(client):
    r = await client.post("/api/contratos/no-existe/items", json={"numero_item": 1})
    assert r.status == 404


async def test_proveedor_nullable_y_desierto(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1, "estado": "DESIERTO"})
    assert r.status == 201
    d = (await r.json())["data"]
    assert d["proveedor_id"] is None and d["estado"] == "DESIERTO"


async def test_proveedor_inexistente_404(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1, "proveedor_id": "no-existe"})
    assert r.status == 404


async def test_estado_check_rechaza_invalido(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1, "estado": "GANADO"})
    assert r.status == 400


async def test_monto_centimos_entero(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1, "monto": 1019000000})
    assert r.status == 201
    assert (await r.json())["data"]["monto"] == 1019000000
    r2 = await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 2, "monto": 10.5})
    assert r2.status == 400


async def test_update_y_delete(client):
    cid = await _mk_contrato(client)
    prov = await _mk_proveedor(client, "G&L S.A.C.", "20111111111")
    await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 4})
    r = await client.put(f"/api/contratos/{cid}/items/4", json={"proveedor_id": prov, "estado": "ADJUDICADO"})
    assert r.status == 200
    d = (await r.json())["data"]
    assert d["proveedor_id"] == prov and d["estado"] == "ADJUDICADO"

    r2 = await client.delete(f"/api/contratos/{cid}/items/4")
    assert r2.status == 200 and (await r2.json())["data"]["activo"] == 0
    lst = (await (await client.get(f"/api/contratos/{cid}/items")).json())["data"]
    assert lst == []                         # baja lógica → fuera de la lista activa


async def test_recrear_reactiva(client):
    """Recrear un ítem dado de baja lo reactiva (id determinista + INSERT OR IGNORE)."""
    cid = await _mk_contrato(client)
    await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1})
    await client.delete(f"/api/contratos/{cid}/items/1")
    r = await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1})
    assert r.status == 200
    assert (await r.json())["data"]["activo"] == 1
    lst = (await (await client.get(f"/api/contratos/{cid}/items")).json())["data"]
    assert len(lst) == 1


# --- Valtom (1 ítem) y Concurso 002 (multi-ítem), misma tabla sin ramas --------

async def test_valtom_un_item(client):
    cid = await _mk_contrato(client, "Valtom")
    valtom = await _mk_proveedor(client, "Consorcio Valtom", "20507745629", "CONSORCIO")
    r = await client.post(f"/api/contratos/{cid}/items",
                          json={"numero_item": 1, "proveedor_id": valtom, "estado": "ADJUDICADO", "monto": 1128000000})
    assert r.status == 201
    lst = (await (await client.get(f"/api/contratos/{cid}/items")).json())["data"]
    assert len(lst) == 1 and lst[0]["proveedor_id"] == valtom


async def test_concurso_multi_item_proveedores_distintos(client):
    cid = await _mk_contrato(client, "Concurso 002-2026-BN")
    airfratelli = await _mk_proveedor(client, "Consorcio Airfratelli", tipo="CONSORCIO")
    gyl = await _mk_proveedor(client, "G&L S.A.C.", "20111111111")
    marino = await _mk_proveedor(client, "Marino Diesel S.A.C.", "20222222222")
    await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 1, "proveedor_id": airfratelli})
    await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 2, "proveedor_id": airfratelli})
    await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 4, "proveedor_id": gyl})
    await client.post(f"/api/contratos/{cid}/items", json={"numero_item": 7, "proveedor_id": marino})

    lst = (await (await client.get(f"/api/contratos/{cid}/items")).json())["data"]
    assert [i["numero_item"] for i in lst] == [1, 2, 4, 7]          # ordenados
    assert len({i["proveedor_id"] for i in lst}) == 3              # tres proveedores distintos


# --- Sync ----------------------------------------------------------------------

async def test_round_trip_sync_item(client, tmp_path):
    """El ítem (id determinista) debe fluir idéntico a otra cache.db vía apply_remote."""
    cid = await _mk_contrato(client, "Valtom")
    prov = await _mk_proveedor(client, "Consorcio Valtom", "20507745629", "CONSORCIO")
    r = await client.post(f"/api/contratos/{cid}/items",
                          json={"numero_item": 1, "proveedor_id": prov, "monto": 1128000000, "estado": "ADJUDICADO"})
    item_id = (await r.json())["data"]["id"]
    assert item_id == f"{cid}_1"

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='item_contrato' AND entity_id=? AND action='create'",
        (item_id,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])
    assert payload["id"] == item_id and payload["numero_item"] == 1

    # Otra cache.db: sembrar padres (FK contrato + proveedor) y aplicar el evento.
    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    await db2.execute("INSERT INTO contratos(id,objeto,moneda,activo,created_at,updated_at) VALUES(?,'V','PEN',1,'t','t')", (cid,))
    await db2.execute("INSERT INTO proveedores(id,razon_social,activo,created_at,updated_at) VALUES(?,'Consorcio Valtom',1,'t','t')", (prov,))
    await db2.commit()

    await _apply_one(db2, {"entity": "item_contrato", "action": "create", "entity_id": item_id, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT * FROM items_contrato WHERE id=?", (item_id,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()

    assert remote["id"] == item_id
    assert remote["contrato_id"] == cid
    assert remote["numero_item"] == 1
    assert remote["proveedor_id"] == prov
    assert remote["monto"] == 1128000000
    assert remote["activo"] == 1
