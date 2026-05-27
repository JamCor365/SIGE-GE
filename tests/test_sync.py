async def test_shape_inicial(client):
    r = await client.get("/api/sync/pending")
    assert r.status == 200
    data = await r.json()
    assert data["status"] == "ok"
    assert isinstance(data["pending_storage"], int)
    assert isinstance(data["pending_db"], int)
    assert isinstance(data["events"], list)
    assert data["pending_storage"] == 0
    assert data["pending_db"] == 0
    assert data["events"] == []


async def test_pending_storage_sube_tras_create(client):
    await client.post("/api/macroregiones", json={"id": 1, "nombre": "NORTE"})
    r = await client.get("/api/sync/pending")
    data = await r.json()
    assert data["pending_storage"] == 1


async def test_pending_db_cero_cuando_upload_ok(client):
    """Con LocalFolderBackend operativo, todos los eventos quedan synced=1."""
    await client.post("/api/macroregiones", json={"id": 1, "nombre": "NORTE"})
    await client.put("/api/macroregiones/1", json={"nombre": "NORTE ACTUALIZADO"})
    r = await client.get("/api/sync/pending")
    data = await r.json()
    assert data["pending_db"] == 0
    assert data["events"] == []


async def test_events_lista_refleja_synced_cero(client):
    """events[] solo contiene entradas con synced=0; con storage OK siempre vacío."""
    await client.post("/api/macroregiones", json={"id": 1, "nombre": "NORTE"})
    r = await client.get("/api/sync/pending")
    assert (await r.json())["events"] == []
