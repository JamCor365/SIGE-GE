import pytest_asyncio

SEDE = {"id": 1, "codigo": "AG-0001", "nombre_agencia": "Sede Lima", "macroregion_id": 1}


@pytest_asyncio.fixture
async def macro(client):
    r = await client.post("/api/macroregiones", json={"id": 1, "nombre": "NORTE"})
    assert r.status == 201
    return 1


async def test_list_empty(client):
    r = await client.get("/api/sedes")
    assert r.status == 200
    assert (await r.json())["data"] == []


async def test_create(client, macro):
    r = await client.post("/api/sedes", json=SEDE)
    assert r.status == 201
    data = await r.json()
    assert data["status"] == "ok"
    assert data["data"]["id"] == 1
    assert data["data"]["macroregion_id"] == macro
    assert data["data"]["macroregion_nombre"] == "NORTE"


async def test_get(client, macro):
    await client.post("/api/sedes", json=SEDE)
    r = await client.get("/api/sedes/1")
    assert r.status == 200
    assert (await r.json())["data"]["codigo"] == "AG-0001"


async def test_get_not_found(client):
    r = await client.get("/api/sedes/9999")
    assert r.status == 404
    assert (await r.json())["status"] == "error"


async def test_create_missing_fields(client):
    r = await client.post("/api/sedes", json={"id": 1, "codigo": "X"})
    assert r.status == 400


async def test_update(client, macro):
    await client.post("/api/sedes", json=SEDE)
    r = await client.put("/api/sedes/1", json={"nombre_agencia": "Sede Lima Norte"})
    assert r.status == 200
    assert (await r.json())["data"]["nombre_agencia"] == "Sede Lima Norte"


async def test_update_not_found(client):
    r = await client.put("/api/sedes/9999", json={"nombre_agencia": "X"})
    assert r.status == 404


async def test_delete_soft(client, macro):
    await client.post("/api/sedes", json=SEDE)
    r = await client.delete("/api/sedes/1")
    assert r.status == 200
    assert (await r.json())["data"]["activo"] == 0


async def test_fk_macroregion_invalida(client):
    r = await client.post("/api/sedes", json={
        "id": 1, "codigo": "AG-0001", "nombre_agencia": "X", "macroregion_id": 999
    })
    assert r.status == 400
    assert (await r.json())["status"] == "error"


# --- Geolocalización (PASO 1 del mapa) ---

async def test_create_con_coordenadas(client, macro):
    payload = {**SEDE, "latitud": -12.0464, "longitud": -77.0428,
               "geo_fuente": "distrito_centroide"}
    r = await client.post("/api/sedes", json=payload)
    assert r.status == 201
    data = (await r.json())["data"]
    assert data["latitud"] == -12.0464
    assert data["longitud"] == -77.0428
    assert data["geo_fuente"] == "distrito_centroide"


async def test_update_coordenadas_manual(client, macro):
    await client.post("/api/sedes", json=SEDE)
    r = await client.put("/api/sedes/1", json={
        "latitud": -12.05, "longitud": -77.04, "geo_fuente": "manual"})
    assert r.status == 200
    data = (await r.json())["data"]
    assert data["geo_fuente"] == "manual"


async def test_coordenada_fuera_de_peru(client, macro):
    # Lima con lat/long invertidas → latitud ~-77 cae fuera de la caja.
    r = await client.post("/api/sedes", json={
        **SEDE, "latitud": -77.04, "longitud": -12.05})
    assert r.status == 400
    assert "rango de Perú" in (await r.json())["reason"]


async def test_geo_fuente_invalida(client, macro):
    r = await client.post("/api/sedes", json={**SEDE, "geo_fuente": "google"})
    assert r.status == 400
    assert (await r.json())["status"] == "error"


async def test_coordenada_a_medias(client, macro):
    await client.post("/api/sedes", json=SEDE)
    r = await client.put("/api/sedes/1", json={"latitud": -12.05, "longitud": None})
    assert r.status == 400
    assert "juntas" in (await r.json())["reason"]
