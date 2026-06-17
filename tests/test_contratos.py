from backend.sync_engine import _apply_one


# --- CRUD + reglas del esquema -------------------------------------------------

async def test_create_genera_uuid_y_numero_nullable(client):
    r = await client.post("/api/contratos", json={"objeto": "Mantenimiento GE 2026"})
    assert r.status == 201
    data = (await r.json())["data"]
    assert len(data["id"]) == 32          # uuid4().hex
    assert data["numero"] is None         # registro sin número aún
    assert data["objeto"] == "Mantenimiento GE 2026"
    assert data["activo"] == 1


async def test_create_requiere_objeto(client):
    r = await client.post("/api/contratos", json={"numero": "C-001"})
    assert r.status == 400


async def test_create_rechaza_id_del_cliente(client):
    # el id es UUID del backend; no está en INSERTABLE_FIELDS
    r = await client.post("/api/contratos", json={"objeto": "X", "id": "deadbeef"})
    assert r.status == 400


async def test_numero_unique(client):
    await client.post("/api/contratos", json={"objeto": "A", "numero": "C-001"})
    r = await client.post("/api/contratos", json={"objeto": "B", "numero": "C-001"})
    assert r.status == 400


async def test_varios_contratos_sin_numero(client):
    r1 = await client.post("/api/contratos", json={"objeto": "A"})
    r2 = await client.post("/api/contratos", json={"objeto": "B"})
    # varios NULL no violan UNIQUE en SQLite
    assert r1.status == 201 and r2.status == 201


async def test_estado_check_rechaza_valor_invalido(client):
    r = await client.post("/api/contratos", json={"objeto": "A", "estado": "POR_VENCER"})
    assert r.status == 400


async def test_tipo_objeto_acepta_adquisicion_instalacion(client):
    r = await client.post(
        "/api/contratos",
        json={"objeto": "Valtom", "tipo_objeto": "ADQUISICION_INSTALACION"},
    )
    assert r.status == 201


async def test_update_y_get(client):
    cid = (await (await client.post("/api/contratos", json={"objeto": "A"})).json())["data"]["id"]
    r = await client.put(f"/api/contratos/{cid}", json={"estado": "VIGENTE", "numero": "C-009"})
    assert r.status == 200
    d = (await (await client.get(f"/api/contratos/{cid}")).json())["data"]
    assert d["estado"] == "VIGENTE"
    assert d["numero"] == "C-009"


async def test_delete_baja_logica(client):
    cid = (await (await client.post("/api/contratos", json={"objeto": "A"})).json())["data"]["id"]
    r = await client.delete(f"/api/contratos/{cid}")
    assert r.status == 200
    assert (await r.json())["data"]["activo"] == 0


# --- Integración con el sync ---------------------------------------------------

async def test_create_emite_evento(client):
    await client.post("/api/contratos", json={"objeto": "A"})
    data = await (await client.get("/api/sync/pending")).json()
    assert data["pending_storage"] == 1


async def test_apply_remote_create_contrato(client):
    """_apply_one inserta un contrato remoto (entity_id = UUID) sin tocar el motor."""
    db = client.app["db"]
    event = {
        "entity": "contrato",
        "action": "create",
        "entity_id": "ffffffffffffffffffffffffffffffff",
        "payload": {
            "id": "ffffffffffffffffffffffffffffffff",
            "numero": "C-REMOTO",
            "objeto": "Creado en otra máquina",
            "activo": 1,
            "created_at": "2026-06-17 10:00:00",
            "updated_at": "2026-06-17 10:00:00",
        },
    }
    await _apply_one(db, event)
    await db.commit()
    d = (await (await client.get("/api/contratos/ffffffffffffffffffffffffffffffff")).json())["data"]
    assert d["objeto"] == "Creado en otra máquina"
    assert d["numero"] == "C-REMOTO"


async def test_apply_remote_update_y_delete_contrato(client):
    db = client.app["db"]
    cid = (await (await client.post("/api/contratos", json={"objeto": "A"})).json())["data"]["id"]

    await _apply_one(db, {
        "entity": "contrato", "action": "update", "entity_id": cid,
        "payload": {"estado": "CULMINADO"},
    })
    await db.commit()
    assert (await (await client.get(f"/api/contratos/{cid}")).json())["data"]["estado"] == "CULMINADO"

    await _apply_one(db, {
        "entity": "contrato", "action": "delete", "entity_id": cid, "payload": {},
    })
    await db.commit()
    assert (await (await client.get(f"/api/contratos/{cid}")).json())["data"]["activo"] == 0
