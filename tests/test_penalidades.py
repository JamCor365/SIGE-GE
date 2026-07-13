"""RamaF entidad 8 — penalidades (descuentos por incumplimiento).

tipo MORA vs OTRAS (como sg-valtom rastrea en acta_conformidad: penalidad_mora /
otras_penalidades). dias_mora solo para MORA; refs blandas prestacion_id/item_id.
"""
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


P = lambda cid: f"/api/contratos/{cid}/penalidades"


# --- Identidad / CRUD ----------------------------------------------------------

async def test_create_genera_uuid(client):
    cid = await _mk_contrato(client)
    r = await client.post(P(cid), json={"tipo": "MORA", "dias_mora": 5, "monto": 50000})
    assert r.status == 201
    data = (await r.json())["data"]
    assert len(data["id"]) == 32 and data["id"] != cid
    assert data["tipo"] == "MORA" and data["dias_mora"] == 5 and data["activo"] == 1


async def test_contrato_inexistente_404(client):
    r = await client.post("/api/contratos/no-existe/penalidades", json={"tipo": "MORA"})
    assert r.status == 404


async def test_check_tipo_invalido(client):
    cid = await _mk_contrato(client)
    assert (await client.post(P(cid), json={"tipo": "CAPITAL"})).status == 400


async def test_tipos_validos(client):
    cid = await _mk_contrato(client)
    for t in ("MORA", "OTRAS"):
        assert (await client.post(P(cid), json={"tipo": t})).status == 201


async def test_check_estado_invalido_y_validos(client):
    cid = await _mk_contrato(client)
    assert (await client.post(P(cid), json={"estado": "PERDONADA"})).status == 400
    for e in ("EN_EVALUACION", "APLICADA", "EXONERADA"):
        assert (await client.post(P(cid), json={"estado": e})).status == 201


async def test_monto_y_dias_mora_enteros_no_negativos(client):
    cid = await _mk_contrato(client)
    assert (await client.post(P(cid), json={"monto": 50000})).status == 201
    assert (await client.post(P(cid), json={"monto": 500.5})).status == 400   # float
    assert (await client.post(P(cid), json={"monto": -1})).status == 400      # negativo
    assert (await client.post(P(cid), json={"dias_mora": -3})).status == 400


async def test_update_y_delete(client):
    cid = await _mk_contrato(client)
    pid = (await (await client.post(P(cid), json={"tipo": "MORA", "estado": "EN_EVALUACION"})).json())["data"]["id"]
    r = await client.put(f"{P(cid)}/{pid}", json={"estado": "APLICADA", "monto": 123456})
    assert r.status == 200
    d = (await r.json())["data"]
    assert d["estado"] == "APLICADA" and d["monto"] == 123456

    r2 = await client.delete(f"{P(cid)}/{pid}")
    assert r2.status == 200 and (await r2.json())["data"]["activo"] == 0
    assert (await (await client.get(P(cid))).json())["data"] == []


async def test_penalidad_de_otro_contrato_404(client):
    cid1 = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    pid = (await (await client.post(P(cid1), json={"tipo": "MORA"})).json())["data"]["id"]
    assert (await client.put(f"{P(cid2)}/{pid}", json={"estado": "APLICADA"})).status == 404


# --- Refs blandas prestacion_id / item_id --------------------------------------

async def test_prestacion_id_valido_e_inexistente_y_ajeno(client):
    cid = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    pid = await _mk_prestacion(client, cid, "PRINCIPAL")
    pid2 = await _mk_prestacion(client, cid2, "PRINCIPAL")
    assert (await client.post(P(cid), json={"prestacion_id": pid})).status == 201
    assert (await client.post(P(cid), json={"prestacion_id": "no-existe"})).status == 404
    assert (await client.post(P(cid), json={"prestacion_id": pid2})).status == 400   # de otro contrato


async def test_item_id_valido_y_ajeno(client):
    cid = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    item = await _mk_item(client, cid, 1)
    item2 = await _mk_item(client, cid2, 1)
    assert (await client.post(P(cid), json={"item_id": item})).status == 201
    assert (await client.post(P(cid), json={"item_id": item2})).status == 400
    assert (await client.post(P(cid), json={"item_id": "no-existe"})).status == 404


# --- Sync ----------------------------------------------------------------------

async def test_round_trip_sync_penalidad(client, tmp_path):
    cid = await _mk_contrato(client, "Valtom")
    r = await client.post(P(cid), json={
        "tipo": "MORA", "concepto": "Retraso en entrega de bienes", "monto": 128000000,
        "dias_mora": 12, "base_legal": "art.162 Reglamento", "fecha": "2022-06-01",
        "estado": "APLICADA"})
    pid = (await r.json())["data"]["id"]

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='penalidad' AND entity_id=? AND action='create'",
        (pid,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])

    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    await db2.execute("INSERT INTO contratos(id,objeto,moneda,activo,created_at,updated_at) VALUES(?,'V','PEN',1,'t','t')", (cid,))
    await db2.commit()
    await _apply_one(db2, {"entity": "penalidad", "action": "create", "entity_id": pid, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT * FROM penalidades WHERE id=?", (pid,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()

    assert remote["id"] == pid and remote["contrato_id"] == cid
    assert remote["tipo"] == "MORA" and remote["dias_mora"] == 12
    assert remote["monto"] == 128000000 and remote["estado"] == "APLICADA"


# --- Caso real: mora + otra penalidad sobre la prestación principal ------------

async def test_valtom_mora_y_otra(client):
    cid = await _mk_contrato(client, "Valtom")
    p_principal = await _mk_prestacion(client, cid, "PRINCIPAL")
    mora = await client.post(P(cid), json={
        "tipo": "MORA", "prestacion_id": p_principal, "dias_mora": 12,
        "concepto": "Retraso instalación", "monto": 128000000, "estado": "APLICADA"})
    otra = await client.post(P(cid), json={
        "tipo": "OTRAS", "prestacion_id": p_principal,
        "concepto": "Incumplimiento de nivel de servicio (SLA)", "monto": 5000000})
    assert mora.status == 201 and otra.status == 201

    lst = (await (await client.get(P(cid))).json())["data"]
    tipos = sorted(p["tipo"] for p in lst)
    assert tipos == ["MORA", "OTRAS"]
    mora_row = next(p for p in lst if p["tipo"] == "MORA")
    assert mora_row["dias_mora"] == 12 and mora_row["prestacion_id"] == p_principal
