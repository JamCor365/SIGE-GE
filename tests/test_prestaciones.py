import json

from aiohttp import web

from backend.db import init_db
from backend.sync_engine import _apply_one


async def _mk_contrato(client, objeto="C"):
    return (await (await client.post("/api/contratos", json={"objeto": objeto})).json())["data"]["id"]


async def _mk_item(client, cid, numero_item):
    return (await (await client.post(f"/api/contratos/{cid}/items", json={"numero_item": numero_item})).json())["data"]["id"]


# --- Identidad / CRUD ----------------------------------------------------------

async def test_create_genera_uuid(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "PRINCIPAL"})
    assert r.status == 201
    data = (await r.json())["data"]
    assert len(data["id"]) == 32 and data["id"] != cid     # UUID propio, no determinista
    assert data["clase"] == "PRINCIPAL"
    assert data["activo"] == 1


async def test_clase_requerida(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={"monto": 100})
    assert r.status == 400


async def test_clase_check_rechaza_invalida(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "SECUNDARIA"})
    assert r.status == 400


async def test_contrato_inexistente_404(client):
    r = await client.post("/api/contratos/no-existe/prestaciones", json={"clase": "PRINCIPAL"})
    assert r.status == 404


async def test_monto_centimos_y_plazo(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/prestaciones",
                          json={"clase": "ACCESORIA", "monto": 109000000, "plazo_dias": 730})
    assert r.status == 201
    d = (await r.json())["data"]
    assert d["monto"] == 109000000 and d["plazo_dias"] == 730
    r2 = await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "ACCESORIA", "monto": 10.5})
    assert r2.status == 400


async def test_update_y_delete(client):
    cid = await _mk_contrato(client)
    pid = (await (await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "PRINCIPAL"})).json())["data"]["id"]
    r = await client.put(f"/api/contratos/{cid}/prestaciones/{pid}", json={"monto": 1019000000, "descripcion": "Adq+Inst"})
    assert r.status == 200 and (await r.json())["data"]["monto"] == 1019000000

    r2 = await client.delete(f"/api/contratos/{cid}/prestaciones/{pid}")
    assert r2.status == 200 and (await r2.json())["data"]["activo"] == 0
    lst = (await (await client.get(f"/api/contratos/{cid}/prestaciones")).json())["data"]
    assert lst == []


async def test_prestacion_de_otro_contrato_404(client):
    cid1 = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    pid = (await (await client.post(f"/api/contratos/{cid1}/prestaciones", json={"clase": "PRINCIPAL"})).json())["data"]["id"]
    # editar la prestación de cid1 a través de cid2 → 404 (no pertenece)
    r = await client.put(f"/api/contratos/{cid2}/prestaciones/{pid}", json={"monto": 1})
    assert r.status == 404


# --- tipos_objeto multivalor + round-trip de sync ------------------------------

async def test_tipos_objeto_multivalor_devuelve_array(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/prestaciones",
                          json={"clase": "PRINCIPAL", "tipos_objeto": ["INSTALACION", "ADQUISICION"]})
    assert r.status == 201
    assert (await r.json())["data"]["tipos_objeto"] == ["ADQUISICION", "INSTALACION"]   # canónico ordenado


async def test_tipos_objeto_rechaza_token_invalido(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/prestaciones",
                          json={"clase": "PRINCIPAL", "tipos_objeto": ["ADQUISICION", "NO_EXISTE"]})
    assert r.status == 400


async def test_round_trip_sync_prestacion(client, tmp_path):
    cid = await _mk_contrato(client, "Valtom")
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={
        "clase": "PRINCIPAL", "tipos_objeto": ["ADQUISICION", "INSTALACION"], "monto": 1019000000,
    })
    pid = (await r.json())["data"]["id"]
    canonical = json.dumps(["ADQUISICION", "INSTALACION"])

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='prestacion' AND entity_id=? AND action='create'",
        (pid,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])
    assert payload["tipos_objeto"] == canonical      # STRING canónico en el evento

    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    await db2.execute("INSERT INTO contratos(id,objeto,moneda,activo,created_at,updated_at) VALUES(?,'V','PEN',1,'t','t')", (cid,))
    await db2.commit()
    await _apply_one(db2, {"entity": "prestacion", "action": "create", "entity_id": pid, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT tipos_objeto, monto, clase FROM prestaciones WHERE id=?", (pid,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()
    assert remote["tipos_objeto"] == canonical        # idéntico en columna remota
    assert json.loads(remote["tipos_objeto"]) == ["ADQUISICION", "INSTALACION"]
    assert remote["monto"] == 1019000000 and remote["clase"] == "PRINCIPAL"


# --- item_id opcional (nivel contrato vs nivel ítem) ---------------------------

async def test_item_id_opcional_nivel_contrato(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "PRINCIPAL"})
    assert (await r.json())["data"]["item_id"] is None     # nivel contrato


async def test_item_id_valido_nivel_item(client):
    cid = await _mk_contrato(client)
    item_id = await _mk_item(client, cid, 1)
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "PRINCIPAL", "item_id": item_id})
    assert r.status == 201 and (await r.json())["data"]["item_id"] == item_id


async def test_item_id_inexistente_404(client):
    cid = await _mk_contrato(client)
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "PRINCIPAL", "item_id": "no-existe"})
    assert r.status == 404


async def test_item_id_de_otro_contrato_400(client):
    cid1 = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    item_de_cid2 = await _mk_item(client, cid2, 1)
    r = await client.post(f"/api/contratos/{cid1}/prestaciones", json={"clase": "PRINCIPAL", "item_id": item_de_cid2})
    assert r.status == 400


# --- Regla "una PRINCIPAL" BLANDA (avisa, NO bloquea la BD) ---------------------

async def test_segunda_principal_avisa_pero_no_bloquea(client):
    cid = await _mk_contrato(client)
    await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "PRINCIPAL"})
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": "PRINCIPAL"})
    assert r.status == 201                                  # se guarda igual
    body = await r.json()
    assert body["warnings"] and "PRINCIPAL" in body["warnings"][0]
    lst = (await (await client.get(f"/api/contratos/{cid}/prestaciones")).json())["data"]
    assert sum(1 for p in lst if p["clase"] == "PRINCIPAL") == 2   # ambas coexisten (sin UNIQUE)


# --- Casos reales: Valtom (1+1) y solo-mantenimiento (1 principal) -------------

async def test_valtom_principal_mas_accesoria(client):
    cid = await _mk_contrato(client, "Valtom")
    p1 = await client.post(f"/api/contratos/{cid}/prestaciones", json={
        "clase": "PRINCIPAL", "numero_prestacion": 1,
        "tipos_objeto": ["ADQUISICION", "INSTALACION"], "monto": 1019000000})
    p2 = await client.post(f"/api/contratos/{cid}/prestaciones", json={
        "clase": "ACCESORIA", "numero_prestacion": 2,
        "tipos_objeto": ["MANTENIMIENTO"], "monto": 109000000, "plazo_dias": 730})
    assert p1.status == 201 and p2.status == 201
    assert (await p2.json())["warnings"] == []              # accesoria no dispara la regla de principal

    lst = (await (await client.get(f"/api/contratos/{cid}/prestaciones")).json())["data"]
    assert len(lst) == 2
    # Derivación read-time simulada sobre la respuesta:
    principal = next(p for p in lst if p["clase"] == "PRINCIPAL")
    accesorias = [p for p in lst if p["clase"] == "ACCESORIA"]
    assert principal["monto"] == 1019000000
    assert sum(a["monto"] for a in accesorias) == 109000000
    tipos_union = sorted({t for p in lst for t in (p["tipos_objeto"] or [])})
    assert tipos_union == ["ADQUISICION", "INSTALACION", "MANTENIMIENTO"]


async def test_solo_mantenimiento_es_principal(client):
    """Un contrato solo-mantenimiento NO es caso especial: el mantenimiento es su PRINCIPAL."""
    cid = await _mk_contrato(client, "AT Energy - mantenimiento")
    r = await client.post(f"/api/contratos/{cid}/prestaciones", json={
        "clase": "PRINCIPAL", "tipos_objeto": ["MANTENIMIENTO"], "monto": 500000000})
    assert r.status == 201
    lst = (await (await client.get(f"/api/contratos/{cid}/prestaciones")).json())["data"]
    assert len(lst) == 1 and lst[0]["clase"] == "PRINCIPAL"
    assert lst[0]["tipos_objeto"] == ["MANTENIMIENTO"]
