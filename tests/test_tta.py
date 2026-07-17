import pytest_asyncio


@pytest_asyncio.fixture
async def prereqs(client):
    """macroregion + sede + GE como prerequisitos FK. Retorna ids."""
    r = await client.post("/api/macroregiones", json={"id": 1, "nombre": "NORTE"})
    assert r.status == 201
    r = await client.post("/api/sedes", json={
        "id": 1,
        "codigo": "AG-0001",
        "nombre_agencia": "Sede Test",
        "macroregion_id": 1,
    })
    assert r.status == 201
    r = await client.post("/api/grupos", json={"id": 1, "sede_id": 1})
    assert r.status == 201
    return {"sede_id": 1, "ge_id": 1}


async def test_list_empty(client):
    r = await client.get("/api/tta")
    assert r.status == 200
    assert (await r.json())["data"] == []


async def test_create_vinculado_a_ge(client, prereqs):
    """El TTA se crea colgando de un GE (ge_id) y con etiqueta; ambos vuelven en GET."""
    r = await client.post("/api/tta", json={
        "id": 1,
        "sede_id": prereqs["sede_id"],
        "ge_id": prereqs["ge_id"],
        "etiqueta": "TTA-0001",
        "marca": "SPECTRUM",
    })
    assert r.status == 201
    d = (await r.json())["data"]
    assert d["ge_id"] == prereqs["ge_id"]
    assert d["etiqueta"] == "TTA-0001"
    assert d["marca"] == "SPECTRUM"


async def test_create_sin_ge_es_valido(client, prereqs):
    """ge_id es nullable: un TTA sin GE asignado sigue siendo válido (heredados)."""
    r = await client.post("/api/tta", json={"id": 1, "sede_id": prereqs["sede_id"]})
    assert r.status == 201
    assert (await r.json())["data"]["ge_id"] is None


async def test_create_ge_inexistente_falla(client, prereqs):
    """FK dura: ge_id apuntando a un GE inexistente se rechaza (IntegrityError -> 400)."""
    r = await client.post("/api/tta", json={
        "id": 1, "sede_id": prereqs["sede_id"], "ge_id": 9999,
    })
    assert r.status == 400
    assert (await r.json())["status"] == "error"


async def test_vincular_ge_en_update(client, prereqs):
    """Un TTA heredado (sin GE) se puede vincular a su GE vía PUT."""
    await client.post("/api/tta", json={"id": 1, "sede_id": prereqs["sede_id"]})
    r = await client.put("/api/tta/1", json={"ge_id": prereqs["ge_id"]})
    assert r.status == 200
    assert (await r.json())["data"]["ge_id"] == prereqs["ge_id"]
