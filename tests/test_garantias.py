import json

from aiohttp import web

from backend.db import init_db
from backend.sync_engine import _apply_one


async def _mk_contrato(client, objeto="C"):
    return (await (await client.post("/api/contratos", json={"objeto": objeto})).json())["data"]["id"]


async def _mk_prestacion(client, cid, clase):
    return (await (await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": clase})).json())["data"]["id"]


async def _mk_item(client, cid, numero_item):
    return (await (await client.post(f"/api/contratos/{cid}/items", json={"numero_item": numero_item})).json())["data"]["id"]


G = lambda cid: f"/api/contratos/{cid}/garantias"


# --- Identidad / CRUD ----------------------------------------------------------

async def test_create_genera_uuid(client):
    cid = await _mk_contrato(client)
    r = await client.post(G(cid), json={"tipo": "FIEL_CUMPLIMIENTO", "modalidad": "CARTA_FIANZA"})
    assert r.status == 201
    data = (await r.json())["data"]
    assert len(data["id"]) == 32 and data["id"] != cid
    assert data["tipo"] == "FIEL_CUMPLIMIENTO"
    assert data["activo"] == 1


async def test_contrato_inexistente_404(client):
    r = await client.post("/api/contratos/no-existe/garantias", json={"tipo": "FIEL_CUMPLIMIENTO"})
    assert r.status == 404


async def test_check_tipo_invalido(client):
    cid = await _mk_contrato(client)
    r = await client.post(G(cid), json={"tipo": "MAGICA"})
    assert r.status == 400


async def test_check_modalidad_invalida(client):
    cid = await _mk_contrato(client)
    r = await client.post(G(cid), json={"modalidad": "TRUEQUE"})
    assert r.status == 400


async def test_check_estado_invalido(client):
    cid = await _mk_contrato(client)
    r = await client.post(G(cid), json={"estado": "VENCIDA"})   # VENCIDA NO es estado almacenable (se deriva)
    assert r.status == 400


async def test_estado_vigente_ok(client):
    cid = await _mk_contrato(client)
    for e in ("VIGENTE", "EJECUTADA", "DEVUELTA"):
        r = await client.post(G(cid), json={"estado": e})
        assert r.status == 201


async def test_monto_centimos_entero(client):
    cid = await _mk_contrato(client)
    assert (await client.post(G(cid), json={"monto": 101900000})).status == 201
    assert (await client.post(G(cid), json={"monto": 1019.5})).status == 400


async def test_numero_nullable(client):
    cid = await _mk_contrato(client)
    r = await client.post(G(cid), json={"tipo": "FIEL_CUMPLIMIENTO"})
    assert r.status == 201 and (await r.json())["data"]["numero_carta_fianza"] is None


async def test_update_y_delete(client):
    cid = await _mk_contrato(client)
    gid = (await (await client.post(G(cid), json={"tipo": "FIEL_CUMPLIMIENTO"})).json())["data"]["id"]
    r = await client.put(f"{G(cid)}/{gid}", json={"estado": "DEVUELTA", "numero_carta_fianza": "010633267-000"})
    assert r.status == 200
    d = (await r.json())["data"]
    assert d["estado"] == "DEVUELTA" and d["numero_carta_fianza"] == "010633267-000"

    r2 = await client.delete(f"{G(cid)}/{gid}")
    assert r2.status == 200 and (await r2.json())["data"]["activo"] == 0
    assert (await (await client.get(G(cid))).json())["data"] == []


async def test_garantia_de_otro_contrato_404(client):
    cid1 = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    gid = (await (await client.post(G(cid1), json={"tipo": "FIEL_CUMPLIMIENTO"})).json())["data"]["id"]
    r = await client.put(f"{G(cid2)}/{gid}", json={"estado": "DEVUELTA"})
    assert r.status == 404


# --- Soft dedup de numero_carta_fianza (NO único) ------------------------------

async def test_numero_carta_fianza_dedup_blando(client):
    cid = await _mk_contrato(client)
    r1 = await client.post(G(cid), json={"numero_carta_fianza": "010633267-000"})
    r2 = await client.post(G(cid), json={"numero_carta_fianza": "010633267-000"})
    assert r1.status == 201 and r2.status == 201          # coexisten (sin UNIQUE)
    lst = (await (await client.get(G(cid))).json())["data"]
    assert sum(1 for g in lst if g["numero_carta_fianza"] == "010633267-000") == 2


# --- Soft refs prestacion_id / item_id -----------------------------------------

async def test_prestacion_id_valido(client):
    cid = await _mk_contrato(client)
    pid = await _mk_prestacion(client, cid, "PRINCIPAL")
    r = await client.post(G(cid), json={"tipo": "FIEL_CUMPLIMIENTO", "prestacion_id": pid})
    assert r.status == 201 and (await r.json())["data"]["prestacion_id"] == pid


async def test_prestacion_id_inexistente_404(client):
    cid = await _mk_contrato(client)
    r = await client.post(G(cid), json={"prestacion_id": "no-existe"})
    assert r.status == 404


async def test_prestacion_id_de_otro_contrato_400(client):
    cid1 = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    pid2 = await _mk_prestacion(client, cid2, "PRINCIPAL")
    r = await client.post(G(cid1), json={"prestacion_id": pid2})
    assert r.status == 400


async def test_item_id_valido_y_ajeno(client):
    cid = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    item = await _mk_item(client, cid, 1)
    item2 = await _mk_item(client, cid2, 1)
    assert (await client.post(G(cid), json={"item_id": item})).status == 201
    assert (await client.post(G(cid), json={"item_id": item2})).status == 400      # de otro contrato
    assert (await client.post(G(cid), json={"item_id": "no-existe"})).status == 404


# --- Sync ----------------------------------------------------------------------

async def test_round_trip_sync_garantia(client, tmp_path):
    cid = await _mk_contrato(client, "Valtom")
    pid = await _mk_prestacion(client, cid, "PRINCIPAL")
    r = await client.post(G(cid), json={
        "tipo": "FIEL_CUMPLIMIENTO", "modalidad": "CARTA_FIANZA", "prestacion_id": pid,
        "numero_carta_fianza": "010633267-000", "monto": 101900000,
        "entidad_emisora": "Scotiabank", "fecha_emision": "2021-12-20", "fecha_vencimiento": "2022-10-16",
        "estado": "VIGENTE",
    })
    gid = (await r.json())["data"]["id"]

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='garantia' AND entity_id=? AND action='create'",
        (gid,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])

    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    await db2.execute("INSERT INTO contratos(id,objeto,moneda,activo,created_at,updated_at) VALUES(?,'V','PEN',1,'t','t')", (cid,))
    await db2.commit()
    await _apply_one(db2, {"entity": "garantia", "action": "create", "entity_id": gid, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT * FROM garantias WHERE id=?", (gid,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()

    assert remote["id"] == gid
    assert remote["contrato_id"] == cid
    assert remote["prestacion_id"] == pid
    assert remote["numero_carta_fianza"] == "010633267-000"
    assert remote["monto"] == 101900000
    assert remote["entidad_emisora"] == "Scotiabank"


# --- Caso real: 2 garantías de Valtom (principal vs accesoria vía prestacion_id) -

async def test_valtom_dos_garantias_fiel_cumplimiento(client):
    cid = await _mk_contrato(client, "Valtom")
    p_principal = await _mk_prestacion(client, cid, "PRINCIPAL")
    p_accesoria = await _mk_prestacion(client, cid, "ACCESORIA")

    g1 = await client.post(G(cid), json={
        "tipo": "FIEL_CUMPLIMIENTO", "modalidad": "CARTA_FIANZA", "prestacion_id": p_principal,
        "numero_carta_fianza": "010633267-000", "monto": 101900000, "entidad_emisora": "Scotiabank"})
    g2 = await client.post(G(cid), json={
        "tipo": "FIEL_CUMPLIMIENTO", "modalidad": "CARTA_FIANZA", "prestacion_id": p_accesoria,
        "numero_carta_fianza": "010633264-000", "monto": 10900000, "entidad_emisora": "Scotiabank"})
    assert g1.status == 201 and g2.status == 201

    lst = (await (await client.get(G(cid))).json())["data"]
    assert len(lst) == 2
    # La distinción principal/accesoria sale de prestacion_id (no de un tipo redundante).
    presta = {g["prestacion_id"] for g in lst}
    assert presta == {p_principal, p_accesoria}
    assert all(g["tipo"] == "FIEL_CUMPLIMIENTO" for g in lst)
