import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from backend.server import create_app


@pytest_asyncio.fixture
async def client(tmp_path):
    app = create_app()
    app["_test_db_path"] = tmp_path / "test.db"
    app["_test_storage_path"] = tmp_path / "storage"
    async with TestClient(TestServer(app)) as c:
        yield c
