"""RamaF entidad 10 — adjuntos (metadata; el archivo vive en SharePoint).

PK UUID; contrato_id FK dura; ref_entidad/ref_id puntero polimórfico BLANDO;
tipo con el vocab de sg-valtom (documento.tipo). El archivo NO va en la BD.
"""
import json

from aiohttp import web

from backend.db import init_db
from backend.sync_engine import _apply_one


async def _mk_contrato(client, objeto="C"):
    return (await (await client.post("/api/contratos", json={"objeto": objeto})).json())["data"]["id"]


async def _mk_adenda(client, cid, numero=1):
    return (await (await client.post(f"/api/contratos/{cid}/adendas", json={"numero": numero})).json())["data"]["id"]


D = lambda cid: f"/api/contratos/{cid}/adjuntos"


# --- Identidad / CRUD ----------------------------------------------------------

async def test_create_genera_uuid(client):
    cid = await _mk_contrato(client)
    r = await client.post(D(cid), json={"tipo": "CONTRATO", "nombre": "Contrato.pdf", "ruta": "SIGE_GE/contrato.pdf"})
    assert r.status == 201
    data = (await r.json())["data"]
    assert len(data["id"]) == 32
    assert data["tipo"] == "CONTRATO" and data["ruta"] == "SIGE_GE/contrato.pdf"
    assert data["activo"] == 1


async def test_contrato_inexistente_404(client):
    r = await client.post("/api/contratos/no-existe/adjuntos", json={"tipo": "CONTRATO"})
    assert r.status == 404


async def test_check_tipo_invalido(client):
    cid = await _mk_contrato(client)
    assert (await client.post(D(cid), json={"tipo": "MEME"})).status == 400


async def test_tipos_validos(client):
    cid = await _mk_contrato(client)
    for t in ("CONTRATO", "BASES", "ADENDA", "ACTA_CONFORMIDAD", "INFORME_TECNICO",
              "GUIA_REMISION", "PANEL_FOTOGRAFICO", "CONSTANCIA_OPERATIVIDAD", "CARTA_FIANZA", "OTRO"):
        assert (await client.post(D(cid), json={"tipo": t})).status == 201, t


async def test_paginas_entero_no_negativo(client):
    cid = await _mk_contrato(client)
    assert (await client.post(D(cid), json={"paginas": 4})).status == 201
    assert (await client.post(D(cid), json={"paginas": 4.5})).status == 400
    assert (await client.post(D(cid), json={"paginas": -1})).status == 400


async def test_update_y_delete(client):
    cid = await _mk_contrato(client)
    aid = (await (await client.post(D(cid), json={"tipo": "OTRO", "nombre": "x"})).json())["data"]["id"]
    r = await client.put(f"{D(cid)}/{aid}", json={"tipo": "ACTA_CONFORMIDAD", "sha256": "abc123"})
    assert r.status == 200
    d = (await r.json())["data"]
    assert d["tipo"] == "ACTA_CONFORMIDAD" and d["sha256"] == "abc123"

    r2 = await client.delete(f"{D(cid)}/{aid}")
    assert r2.status == 200 and (await r2.json())["data"]["activo"] == 0
    assert (await (await client.get(D(cid))).json())["data"] == []


async def test_adjunto_de_otro_contrato_404(client):
    cid1 = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    aid = (await (await client.post(D(cid1), json={"tipo": "OTRO"})).json())["data"]["id"]
    assert (await client.put(f"{D(cid2)}/{aid}", json={"nombre": "y"})).status == 404


# --- Puntero polimórfico blando + filtro por ref -------------------------------

async def test_ref_polimorfico_y_filtro(client):
    cid = await _mk_contrato(client)
    ad = await _mk_adenda(client, cid, 1)
    # adjunto ligado a la adenda (ref soft, sin FK)
    r = await client.post(D(cid), json={
        "tipo": "ADENDA", "nombre": "Primera Adenda.pdf", "ref_entidad": "adenda", "ref_id": ad})
    assert r.status == 201
    # adjunto suelto (sin ref)
    await client.post(D(cid), json={"tipo": "OTRO", "nombre": "nota.pdf"})

    # filtro por la sub-entidad
    filt = (await (await client.get(f"{D(cid)}?ref_entidad=adenda&ref_id={ad}")).json())["data"]
    assert len(filt) == 1 and filt[0]["ref_id"] == ad and filt[0]["ref_entidad"] == "adenda"
    # sin filtro: los 2
    todos = (await (await client.get(D(cid))).json())["data"]
    assert len(todos) == 2


async def test_ref_id_no_se_valida_duro(client):
    """ref_id es un puntero BLANDO: apuntar a un id inexistente NO es error (metadata)."""
    cid = await _mk_contrato(client)
    r = await client.post(D(cid), json={"tipo": "OTRO", "ref_entidad": "servicio", "ref_id": "no-existe-todavia"})
    assert r.status == 201


# --- Sync ----------------------------------------------------------------------

async def test_round_trip_sync_adjunto(client, tmp_path):
    cid = await _mk_contrato(client, "Valtom")
    r = await client.post(D(cid), json={
        "tipo": "ACTA_CONFORMIDAD", "nombre": "Acta Rimac.pdf", "ruta": "SIGE_GE/actas/rimac.pdf",
        "sha256": "deadbeef", "paginas": 3, "fecha": "2022-10-01",
        "ref_entidad": "contrato", "ref_id": cid})
    aid = (await r.json())["data"]["id"]

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='adjunto' AND entity_id=? AND action='create'",
        (aid,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])

    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    await db2.execute("INSERT INTO contratos(id,objeto,moneda,activo,created_at,updated_at) VALUES(?,'V','PEN',1,'t','t')", (cid,))
    await db2.commit()
    await _apply_one(db2, {"entity": "adjunto", "action": "create", "entity_id": aid, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT * FROM adjuntos WHERE id=?", (aid,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()

    assert remote["id"] == aid and remote["contrato_id"] == cid
    assert remote["tipo"] == "ACTA_CONFORMIDAD" and remote["sha256"] == "deadbeef"
    assert remote["paginas"] == 3 and remote["ruta"] == "SIGE_GE/actas/rimac.pdf"
