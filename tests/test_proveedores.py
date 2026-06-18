import json

from aiohttp import web

from backend.db import init_db
from backend.sync_engine import _apply_one


# --- CRUD + reglas del esquema -------------------------------------------------

async def test_create_genera_uuid(client):
    r = await client.post("/api/proveedores", json={"razon_social": "ACME S.A.C."})
    assert r.status == 201
    data = (await r.json())["data"]
    assert len(data["id"]) == 32          # uuid4().hex
    assert data["razon_social"] == "ACME S.A.C."
    assert data["activo"] == 1


async def test_create_requiere_razon_social(client):
    r = await client.post("/api/proveedores", json={"ruc": "20123456789"})
    assert r.status == 400


async def test_create_rechaza_id_del_cliente(client):
    r = await client.post("/api/proveedores", json={"razon_social": "X", "id": "deadbeef"})
    assert r.status == 400


async def test_tipo_check_rechaza_invalido(client):
    r = await client.post("/api/proveedores", json={"razon_social": "X", "tipo": "ALIEN"})
    assert r.status == 400


async def test_tipo_acepta_vocabulario(client):
    for t in ("PERSONA_JURIDICA", "PERSONA_NATURAL", "CONSORCIO"):
        r = await client.post("/api/proveedores", json={"razon_social": f"X {t}", "tipo": t})
        assert r.status == 201


async def test_ruc_nullable(client):
    r = await client.post("/api/proveedores", json={"razon_social": "Sin RUC aún"})
    assert r.status == 201
    assert (await r.json())["data"]["ruc"] is None


async def test_update_y_get(client):
    pid = (await (await client.post("/api/proveedores", json={"razon_social": "A"})).json())["data"]["id"]
    r = await client.put(f"/api/proveedores/{pid}", json={"ruc": "20100070970", "tipo": "PERSONA_JURIDICA"})
    assert r.status == 200
    d = (await (await client.get(f"/api/proveedores/{pid}")).json())["data"]
    assert d["ruc"] == "20100070970"
    assert d["tipo"] == "PERSONA_JURIDICA"


async def test_delete_baja_logica(client):
    pid = (await (await client.post("/api/proveedores", json={"razon_social": "A"})).json())["data"]["id"]
    r = await client.delete(f"/api/proveedores/{pid}")
    assert r.status == 200
    assert (await r.json())["data"]["activo"] == 0


# --- RUC: validación SUAVE (avisa, NO bloquea) ---------------------------------

async def test_ruc_invalido_no_bloquea_pero_avisa(client):
    r = await client.post("/api/proveedores", json={"razon_social": "X", "ruc": "123"})
    assert r.status == 201                       # se guarda igual
    body = await r.json()
    assert body["data"]["ruc"] == "123"
    assert body["warnings"] and "11 dígitos" in body["warnings"][0]


async def test_ruc_valido_sin_warning(client):
    r = await client.post("/api/proveedores", json={"razon_social": "X", "ruc": "20100070970"})
    assert r.status == 201
    assert (await r.json())["warnings"] == []


# --- Dedup de RUC BLANDO: dos proveedores con el mismo RUC coexisten -----------

async def test_dedup_ruc_no_bloqueante(client):
    """No hay UNIQUE(ruc): dos filas con el mismo RUC se permiten (dedup es de UI).
    Esto evita el descarte silencioso en sync que colgaría FKs de items_contrato."""
    r1 = await client.post("/api/proveedores", json={"razon_social": "Empresa A", "ruc": "20100070970"})
    r2 = await client.post("/api/proveedores", json={"razon_social": "Empresa A (dup)", "ruc": "20100070970"})
    assert r1.status == 201 and r2.status == 201
    lst = (await (await client.get("/api/proveedores")).json())["data"]
    con_ese_ruc = [p for p in lst if p["ruc"] == "20100070970"]
    assert len(con_ese_ruc) == 2                 # ambas coexisten


# --- Consorcio: 1 fila + miembros/% en observaciones ---------------------------

async def test_consorcio_en_observaciones(client):
    r = await client.post("/api/proveedores", json={
        "razon_social": "Consorcio Airfratelli",
        "tipo": "CONSORCIO",
        "ruc": "20601234567",  # RUC del miembro facturador
        "observaciones": "Miembros: MC Fratelli 40%, Arredondo Ingenieros 60%",
    })
    assert r.status == 201
    d = (await r.json())["data"]
    assert d["tipo"] == "CONSORCIO"
    assert "MC Fratelli 40%" in d["observaciones"]


# --- Sync ----------------------------------------------------------------------

async def test_create_emite_evento(client):
    await client.post("/api/proveedores", json={"razon_social": "A"})
    data = await (await client.get("/api/sync/pending")).json()
    assert data["pending_storage"] == 1


async def test_round_trip_sync_proveedor(client, tmp_path):
    """El proveedor (UUID) debe fluir idéntico a otra cache.db vía apply_remote."""
    r = await client.post("/api/proveedores", json={
        "razon_social": "Valtom (Consorcio)", "tipo": "CONSORCIO", "ruc": "20512345678",
        "observaciones": "Valtom Ingenieros + Verificación y Control + OLC Ingenieros",
    })
    pid = (await r.json())["data"]["id"]

    db = client.app["db"]
    async with db.execute(
        "SELECT payload_json FROM events_log WHERE entity='proveedor' AND entity_id=? AND action='create'",
        (pid,),
    ) as cur:
        payload = json.loads((await cur.fetchone())["payload_json"])

    app2 = web.Application()
    await init_db(app2, tmp_path / "remote.db")
    db2 = app2["db"]
    await _apply_one(db2, {"entity": "proveedor", "action": "create", "entity_id": pid, "payload": payload})
    await db2.commit()
    async with db2.execute("SELECT * FROM proveedores WHERE id=?", (pid,)) as cur:
        remote = dict(await cur.fetchone())
    await db2.close()

    assert remote["id"] == pid
    assert remote["razon_social"] == "Valtom (Consorcio)"
    assert remote["tipo"] == "CONSORCIO"
    assert remote["ruc"] == "20512345678"
    assert "OLC Ingenieros" in remote["observaciones"]
