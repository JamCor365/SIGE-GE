import pytest_asyncio


@pytest_asyncio.fixture
async def prereqs(client):
    """Crea macroregion + sede como prerequisitos FK. Retorna sede_id."""
    r = await client.post("/api/macroregiones", json={"id": 1, "nombre": "NORTE"})
    assert r.status == 201
    r = await client.post("/api/sedes", json={
        "id": 1,
        "codigo": "AG-0001",
        "nombre_agencia": "Sede Test",
        "macroregion_id": 1,
    })
    assert r.status == 201
    return {"sede_id": 1}


async def test_list_empty(client):
    r = await client.get("/api/grupos")
    assert r.status == 200
    data = await r.json()
    assert data["status"] == "ok"
    assert data["data"] == []


async def test_create(client, prereqs):
    r = await client.post("/api/grupos", json={"id": 1, "sede_id": prereqs["sede_id"]})
    assert r.status == 201
    data = await r.json()
    assert data["status"] == "ok"
    assert data["data"]["id"] == 1
    assert data["data"]["sede_id"] == prereqs["sede_id"]


async def test_get(client, prereqs):
    await client.post("/api/grupos", json={"id": 1, "sede_id": prereqs["sede_id"]})
    r = await client.get("/api/grupos/1")
    assert r.status == 200
    assert (await r.json())["data"]["id"] == 1


async def test_get_not_found(client):
    r = await client.get("/api/grupos/9999")
    assert r.status == 404
    assert (await r.json())["status"] == "error"


async def test_create_missing_fields(client):
    r = await client.post("/api/grupos", json={"id": 1})
    assert r.status == 400
    assert (await r.json())["status"] == "error"


async def test_update(client, prereqs):
    await client.post("/api/grupos", json={
        "id": 1, "sede_id": prereqs["sede_id"], "estado": "OPERATIVO"
    })
    r = await client.put("/api/grupos/1", json={"estado": "INOPERATIVO"})
    assert r.status == 200
    assert (await r.json())["data"]["estado"] == "INOPERATIVO"


async def test_update_not_found(client):
    r = await client.put("/api/grupos/9999", json={"estado": "OPERATIVO"})
    assert r.status == 404


async def test_delete_soft(client, prereqs):
    await client.post("/api/grupos", json={"id": 1, "sede_id": prereqs["sede_id"]})
    r = await client.delete("/api/grupos/1")
    assert r.status == 200
    assert (await r.json())["data"]["activo"] == 0


async def test_delete_not_found(client):
    r = await client.delete("/api/grupos/9999")
    assert r.status == 404


async def test_eventos_registrados_en_storage(client, prereqs):
    """Create + update + delete deben subir 3 eventos al LocalFolderBackend."""
    sid = prereqs["sede_id"]
    await client.post("/api/grupos", json={"id": 1, "sede_id": sid})
    await client.put("/api/grupos/1", json={"estado": "OPERATIVO"})
    await client.delete("/api/grupos/1")

    r = await client.get("/api/sync/pending")
    assert r.status == 200
    data = await r.json()
    # prereqs generó 2 eventos + 3 del CRUD = al menos 3
    assert data["pending_storage"] >= 3


async def test_eventos_synced_ok(client, prereqs):
    """Con LocalFolderBackend funcionando, pending_db debe ser 0."""
    await client.post("/api/grupos", json={"id": 1, "sede_id": prereqs["sede_id"]})
    r = await client.get("/api/sync/pending")
    data = await r.json()
    assert data["pending_db"] == 0
