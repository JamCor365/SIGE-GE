"""RamaF entidad 7 — adendas (modificaciones al contrato tras la firma).

Vocabulario y forma fijados contra la fuente real: Primera y Segunda Adenda del
Contrato 28278-2022-BN (sg-valtom/docs). Primera = REDUCCIÓN (deltas negativos,
principal y accesoria por separado); Segunda = MODIFICACION_CONVENCIONAL (incorpora
anexo SLA, sin tocar montos ni plazo).
"""
import json

from aiohttp import web

from backend.db import init_db
from backend.sync_engine import _apply_one


async def _mk_contrato(client, objeto="C"):
    return (await (await client.post("/api/contratos", json={"objeto": objeto})).json())["data"]["id"]


A = lambda cid: f"/api/contratos/{cid}/adendas"


# --- Identidad / CRUD ----------------------------------------------------------

async def test_create_genera_uuid(client):
    cid = await _mk_contrato(client)
    r = await client.post(A(cid), json={"numero": 1, "tipo": "REDUCCION"})
    assert r.status == 201
    data = (await r.json())["data"]
    assert len(data["id"]) == 32 and data["id"] != cid
    assert data["numero"] == 1 and data["tipo"] == "REDUCCION"
    assert data["activo"] == 1


async def test_contrato_inexistente_404(client):
    r = await client.post("/api/contratos/no-existe/adendas", json={"numero": 1})
    assert r.status == 404


async def test_numero_obligatorio(client):
    cid = await _mk_contrato(client)
    assert (await client.post(A(cid), json={"tipo": "REDUCCION"})).status == 400          # falta numero
    assert (await client.post(A(cid), json={"numero": None})).status == 400


async def test_numero_debe_ser_entero_positivo(client):
    cid = await _mk_contrato(client)
    assert (await client.post(A(cid), json={"numero": 0})).status == 400
    assert (await client.post(A(cid), json={"numero": -1})).status == 400
    assert (await client.post(A(cid), json={"numero": 1.5})).status == 400
    assert (await client.post(A(cid), json={"numero": True})).status == 400   # bool no es numero


async def test_check_tipo_invalido(client):
    cid = await _mk_contrato(client)
    assert (await client.post(A(cid), json={"numero": 1, "tipo": "MAGICA"})).status == 400


async def test_tipos_validos_ok(client):
    cid = await _mk_contrato(client)
    for i, t in enumerate(("AMPLIACION_PLAZO", "ADICIONAL", "REDUCCION", "MODIFICACION_CONVENCIONAL"), 1):
        r = await client.post(A(cid), json={"numero": i, "tipo": t})
        assert r.status == 201, t


async def test_deltas_con_signo(client):
    cid = await _mk_contrato(client)
    # negativos válidos (reducción)
    r = await client.post(A(cid), json={
        "numero": 1, "monto_delta_principal": -1929930, "monto_delta_accesorio": -701200,
        "plazo_delta_dias": -30})
    assert r.status == 201
    d = (await r.json())["data"]
    assert d["monto_delta_principal"] == -1929930 and d["monto_delta_accesorio"] == -701200
    # float rechazado
    assert (await client.post(A(cid), json={"numero": 2, "monto_delta_principal": 100.5})).status == 400


async def test_update_y_delete(client):
    cid = await _mk_contrato(client)
    aid = (await (await client.post(A(cid), json={"numero": 1, "tipo": "REDUCCION"})).json())["data"]["id"]
    r = await client.put(f"{A(cid)}/{aid}", json={"objeto": "Reduce Callao", "base_legal": "art.34.3 Ley"})
    assert r.status == 200
    d = (await r.json())["data"]
    assert d["objeto"] == "Reduce Callao" and d["base_legal"] == "art.34.3 Ley"

    r2 = await client.delete(f"{A(cid)}/{aid}")
    assert r2.status == 200 and (await r2.json())["data"]["activo"] == 0
    assert (await (await client.get(A(cid))).json())["data"] == []


async def test_adenda_de_otro_contrato_404(client):
    cid1 = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    aid = (await (await client.post(A(cid1), json={"numero": 1})).json())["data"]["id"]
    assert (await client.put(f"{A(cid2)}/{aid}", json={"objeto": "x"})).status == 404
    assert (await client.delete(f"{A(cid2)}/{aid}")).status == 404


# --- Dedup blando de (contrato_id, numero): NO único ---------------------------

async def test_numero_dedup_blando(client):
    cid = await _mk_contrato(client)
    r1 = await client.post(A(cid), json={"numero": 1})
    r2 = await client.post(A(cid), json={"numero": 1})
    assert r1.status == 201 and r2.status == 201          # coexisten (sin UNIQUE)
    lst = (await (await client.get(A(cid))).json())["data"]
    assert sum(1 for a in lst if a["numero"] == 1) == 2


# --- Sync ----------------------------------------------------------------------

async def test_round_trip_sync_adenda(client, tmp_path):
    cid = await _mk_contrato(client, "Valtom")
    r = await client.post(A(cid), json={
        "numero": 1, "tipo": "REDUCCION", "fecha": "2023-11-14",
        "base_legal": "num.34.3 art.34 TUO Ley Contrataciones",
        "objeto": "Reducción de prestaciones Callao y Centro Cívico",
        "monto_delta_principal": -1929930, "monto_delta_accesorio": -701200})
    aid = (await r.json())["data"]["id"]

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='adenda' AND entity_id=? AND action='create'",
        (aid,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])

    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    await db2.execute("INSERT INTO contratos(id,objeto,moneda,activo,created_at,updated_at) VALUES(?,'V','PEN',1,'t','t')", (cid,))
    await db2.commit()
    await _apply_one(db2, {"entity": "adenda", "action": "create", "entity_id": aid, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT * FROM adendas WHERE id=?", (aid,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()

    assert remote["id"] == aid and remote["contrato_id"] == cid
    assert remote["tipo"] == "REDUCCION"
    assert remote["monto_delta_principal"] == -1929930
    assert remote["monto_delta_accesorio"] == -701200


# --- Caso real: las 2 adendas del contrato Valtom ------------------------------

async def test_valtom_dos_adendas_reales(client):
    cid = await _mk_contrato(client, "Valtom")
    # Primera Adenda (14/11/2023): reducción de prestaciones, deltas negativos.
    a1 = await client.post(A(cid), json={
        "numero": 1, "tipo": "REDUCCION", "fecha": "2023-11-14",
        "base_legal": "num.34.3 art.34 TUO Ley Contrataciones + num.157.1 art.157 Reglamento",
        "objeto": "Reducción principal y accesoria (Callao Créditos, Centro Cívico)",
        "monto_delta_principal": -1929930, "monto_delta_accesorio": -701200})
    # Segunda Adenda (11/07/2024): incorpora Anexo II (SLA), sin tocar montos ni plazo.
    a2 = await client.post(A(cid), json={
        "numero": 2, "tipo": "MODIFICACION_CONVENCIONAL", "fecha": "2024-07-11",
        "objeto": "Incorpora Anexo II — Acuerdos de Niveles de Servicio (SLA)"})
    assert a1.status == 201 and a2.status == 201

    lst = (await (await client.get(A(cid))).json())["data"]
    assert [a["numero"] for a in lst] == [1, 2]              # ordenadas por numero
    assert lst[0]["tipo"] == "REDUCCION" and lst[0]["monto_delta_principal"] < 0
    assert lst[1]["tipo"] == "MODIFICACION_CONVENCIONAL"
    assert lst[1]["monto_delta_principal"] is None          # la Segunda no toca montos
