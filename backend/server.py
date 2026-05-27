import asyncio
import logging
from pathlib import Path

from aiohttp import web

FRONTEND = Path("frontend")

from backend.config import load_config
from backend.db import close_db, init_db
from backend.routes import grupos, macroregiones, sedes, sync, tta
from backend.storage import get_backend

log = logging.getLogger("sige.server")


async def handle_status(request: web.Request) -> web.Response:
    cfg = request.app["config"]
    return web.json_response({
        "status": "ok",
        "version": cfg["app"]["version"],
        "storage_mode": cfg["storage"]["mode"],
    })


async def on_startup(app: web.Application) -> None:
    cfg = load_config()
    app["config"] = cfg
    test_storage = app.get("_test_storage_path")
    if test_storage is not None:
        from backend.storage.local_folder import LocalFolderBackend
        app["storage"] = LocalFolderBackend(test_storage)
    else:
        app["storage"] = get_backend(cfg)
    await init_db(app, app.get("_test_db_path"))
    if hasattr(app["storage"], "warmup"):
        asyncio.ensure_future(app["storage"].warmup())
    log.info("SIGE-GE iniciado — storage: %s", cfg["storage"]["mode"])


async def on_cleanup(app: web.Application) -> None:
    storage = app.get("storage")
    if storage is not None and hasattr(storage, "close"):
        await storage.close()
    await close_db(app)
    log.info("SIGE-GE detenido")


async def serve_index(request: web.Request) -> web.Response:
    return web.FileResponse(FRONTEND / "index.html")


def create_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/grupos", grupos.list_grupos)
    app.router.add_get("/api/grupos/{id}", grupos.get_grupo)
    app.router.add_post("/api/grupos", grupos.create_grupo)
    app.router.add_put("/api/grupos/{id}", grupos.update_grupo)
    app.router.add_delete("/api/grupos/{id}", grupos.delete_grupo)
    app.router.add_get("/api/macroregiones", macroregiones.list_macroregiones)
    app.router.add_get("/api/macroregiones/{id}", macroregiones.get_macroregion)
    app.router.add_post("/api/macroregiones", macroregiones.create_macroregion)
    app.router.add_put("/api/macroregiones/{id}", macroregiones.update_macroregion)
    app.router.add_delete("/api/macroregiones/{id}", macroregiones.delete_macroregion)
    app.router.add_get("/api/sedes", sedes.list_sedes)
    app.router.add_get("/api/sedes/{id}", sedes.get_sede)
    app.router.add_post("/api/sedes", sedes.create_sede)
    app.router.add_put("/api/sedes/{id}", sedes.update_sede)
    app.router.add_delete("/api/sedes/{id}", sedes.delete_sede)
    app.router.add_get("/api/tta", tta.list_tta)
    app.router.add_get("/api/tta/{id}", tta.get_tta)
    app.router.add_post("/api/tta", tta.create_tta)
    app.router.add_put("/api/tta/{id}", tta.update_tta)
    app.router.add_delete("/api/tta/{id}", tta.delete_tta)

    app.router.add_get("/api/sync/pending", sync.list_pending)

    app.router.add_get("/", serve_index)
    app.router.add_static("/", FRONTEND)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    web.run_app(create_app(), host="localhost", port=8080)
