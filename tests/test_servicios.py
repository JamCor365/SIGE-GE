"""RamaF entidad 9 — servicios_mantenimiento (cronograma por GE).

Un servicio = (GE × nro_servicio). ge_id FK dura (obligatorio y debe existir);
prestacion_id ref blanda; fecha_programada vs fecha_ejecutada separadas.
"""
import json

from aiohttp import web

from backend.db import init_db
from backend.sync_engine import _apply_one


async def _mk_contrato(client, objeto="C"):
    return (await (await client.post("/api/contratos", json={"objeto": objeto})).json())["data"]["id"]


async def _mk_ge(client, ge_id=1, sede_id=1, macro_id=1):
    """Crea macroregion→sede→GE y devuelve ge_id (chain FK)."""
    await client.post("/api/macroregiones", json={"id": macro_id, "nombre": f"MR{macro_id}"})
    await client.post("/api/sedes", json={
        "id": sede_id, "codigo": f"AG-{sede_id:04d}", "nombre_agencia": f"Sede {sede_id}",
        "macroregion_id": macro_id})
    r = await client.post("/api/grupos", json={"id": ge_id, "sede_id": sede_id})
    assert r.status == 201
    return ge_id


async def _mk_prestacion(client, cid, clase):
    return (await (await client.post(f"/api/contratos/{cid}/prestaciones", json={"clase": clase})).json())["data"]["id"]


S = lambda cid: f"/api/contratos/{cid}/servicios"


# --- Identidad / CRUD ----------------------------------------------------------

async def test_create_genera_uuid(client):
    cid = await _mk_contrato(client)
    ge = await _mk_ge(client)
    r = await client.post(S(cid), json={"ge_id": ge, "nro_servicio": 1, "estado": "PROGRAMADO"})
    assert r.status == 201
    data = (await r.json())["data"]
    assert len(data["id"]) == 32
    assert data["ge_id"] == ge and data["nro_servicio"] == 1 and data["activo"] == 1


async def test_contrato_inexistente_404(client):
    ge = await _mk_ge(client)
    r = await client.post("/api/contratos/no-existe/servicios", json={"ge_id": ge, "nro_servicio": 1})
    assert r.status == 404


async def test_ge_id_obligatorio_y_debe_existir(client):
    cid = await _mk_contrato(client)
    assert (await client.post(S(cid), json={"nro_servicio": 1})).status == 400          # falta ge_id
    assert (await client.post(S(cid), json={"ge_id": 999, "nro_servicio": 1})).status == 404  # GE inexistente


async def test_nro_servicio_obligatorio_y_positivo(client):
    cid = await _mk_contrato(client)
    ge = await _mk_ge(client)
    assert (await client.post(S(cid), json={"ge_id": ge})).status == 400                 # falta nro_servicio
    assert (await client.post(S(cid), json={"ge_id": ge, "nro_servicio": 0})).status == 400
    assert (await client.post(S(cid), json={"ge_id": ge, "nro_servicio": 1.5})).status == 400


async def test_check_estado_invalido_y_validos(client):
    cid = await _mk_contrato(client)
    ge = await _mk_ge(client)
    assert (await client.post(S(cid), json={"ge_id": ge, "nro_servicio": 1, "estado": "LISTO"})).status == 400
    for i, e in enumerate(("PROGRAMADO", "EJECUTADO", "CONFORME", "OBSERVADO"), 1):
        assert (await client.post(S(cid), json={"ge_id": ge, "nro_servicio": i, "estado": e})).status == 201


async def test_fechas_separadas_y_update(client):
    cid = await _mk_contrato(client)
    ge = await _mk_ge(client)
    sid = (await (await client.post(S(cid), json={
        "ge_id": ge, "nro_servicio": 1, "fecha_programada": "2024-06-01", "estado": "PROGRAMADO"})).json())["data"]["id"]
    # registrar ejecución real sin pisar la programada
    r = await client.put(f"{S(cid)}/{sid}", json={"fecha_ejecutada": "2024-06-05", "estado": "EJECUTADO"})
    assert r.status == 200
    d = (await r.json())["data"]
    assert d["fecha_programada"] == "2024-06-01" and d["fecha_ejecutada"] == "2024-06-05"
    assert d["estado"] == "EJECUTADO"

    r2 = await client.delete(f"{S(cid)}/{sid}")
    assert r2.status == 200 and (await r2.json())["data"]["activo"] == 0
    assert (await (await client.get(S(cid))).json())["data"] == []


async def test_servicio_de_otro_contrato_404(client):
    cid1 = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    ge = await _mk_ge(client)
    sid = (await (await client.post(S(cid1), json={"ge_id": ge, "nro_servicio": 1})).json())["data"]["id"]
    assert (await client.put(f"{S(cid2)}/{sid}", json={"estado": "EJECUTADO"})).status == 404


# --- Ref blanda prestacion_id --------------------------------------------------

async def test_prestacion_id_valido_e_inexistente_y_ajeno(client):
    cid = await _mk_contrato(client, "A")
    cid2 = await _mk_contrato(client, "B")
    ge = await _mk_ge(client)
    pacc = await _mk_prestacion(client, cid, "ACCESORIA")
    pacc2 = await _mk_prestacion(client, cid2, "ACCESORIA")
    assert (await client.post(S(cid), json={"ge_id": ge, "nro_servicio": 1, "prestacion_id": pacc})).status == 201
    assert (await client.post(S(cid), json={"ge_id": ge, "nro_servicio": 2, "prestacion_id": "no-existe"})).status == 404
    assert (await client.post(S(cid), json={"ge_id": ge, "nro_servicio": 3, "prestacion_id": pacc2})).status == 400


# --- Sync ----------------------------------------------------------------------

async def test_round_trip_sync_servicio(client, tmp_path):
    cid = await _mk_contrato(client, "Valtom")
    ge = await _mk_ge(client)
    r = await client.post(S(cid), json={
        "ge_id": ge, "nro_servicio": 2, "fecha_programada": "2024-06-01",
        "fecha_ejecutada": "2024-06-05", "estado": "CONFORME"})
    sid = (await r.json())["data"]["id"]

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='servicio' AND entity_id=? AND action='create'",
        (sid,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])

    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    # prereqs FK en la DB remota: macroregion→sede→GE + contrato
    await db2.execute("INSERT INTO macroregiones(id,nombre,activo,created_at,updated_at) VALUES(1,'MR1',1,'t','t')")
    await db2.execute("INSERT INTO sedes(id,codigo,nombre_agencia,macroregion_id,activo,created_at,updated_at) VALUES(1,'AG-0001','S1',1,1,'t','t')")
    await db2.execute("INSERT INTO grupos_electrogenos(id,sede_id,activo,created_at,updated_at) VALUES(?,1,1,'t','t')", (ge,))
    await db2.execute("INSERT INTO contratos(id,objeto,moneda,activo,created_at,updated_at) VALUES(?,'V','PEN',1,'t','t')", (cid,))
    await db2.commit()
    await _apply_one(db2, {"entity": "servicio", "action": "create", "entity_id": sid, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT * FROM servicios_mantenimiento WHERE id=?", (sid,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()

    assert remote["id"] == sid and remote["contrato_id"] == cid
    assert remote["ge_id"] == ge and remote["nro_servicio"] == 2
    assert remote["fecha_programada"] == "2024-06-01" and remote["fecha_ejecutada"] == "2024-06-05"
    assert remote["estado"] == "CONFORME"


# --- Caso real: cronograma MPV1..MPV4 de un GE ---------------------------------

async def test_cronograma_mpv1_a_4(client):
    cid = await _mk_contrato(client, "Valtom")
    ge = await _mk_ge(client)
    for n in range(1, 5):
        r = await client.post(S(cid), json={"ge_id": ge, "nro_servicio": n, "estado": "PROGRAMADO"})
        assert r.status == 201
    lst = (await (await client.get(S(cid))).json())["data"]
    assert [s["nro_servicio"] for s in lst] == [1, 2, 3, 4]   # ordenado por ge_id, nro_servicio
    assert all(s["ge_id"] == ge for s in lst)
