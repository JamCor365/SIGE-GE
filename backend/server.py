import asyncio
import logging
from pathlib import Path

from aiohttp import web

FRONTEND = Path("frontend")

from backend.config import load_config
from backend.db import close_db, init_db
from backend.routes import grupos, macroregiones, sedes, sync, tta
from backend.snapshot import _maybe_generate_snapshot, apply_post_snapshot_events, recover_state
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

    is_test = app.get("_test_db_path") is not None

    # Recuperación de estado (sincrónica, antes de init_db): máquina nueva
    # (cache.db de 0 bytes) o atrasada (local_max < snapshot.last_event_id).
    recovery = None
    if not is_test:
        recovery = await recover_state(app["storage"], config=cfg)

    await init_db(app, app.get("_test_db_path"))

    # Aplicar la unión (pending > last_event_id) ∪ local_events tras la recuperación.
    if recovery is not None:
        await apply_post_snapshot_events(
            app["db"], app["storage"], recovery.meta, recovery.local_events
        )
        print("Recuperación completada. Base de datos lista.", flush=True)

    # Warmup + generación de snapshot en background (una sola tarea encadenada:
    # el snapshot DEBE correr después del warmup para reusar el contexto de
    # SharePoint ya construido, no relanzar launch_persistent_context).
    if not is_test:
        app["_snapshot_done"] = False
    asyncio.ensure_future(_bg_startup(app, is_test))
    log.info("SIGE-GE iniciado — storage: %s", cfg["storage"]["mode"])


async def _bg_startup(app: web.Application, is_test: bool) -> None:
    """Warmup primero (si el backend lo soporta), luego snapshot. En modo
    test/local el backend no expone warmup → se salta y solo corre snapshot."""
    if hasattr(app["storage"], "warmup"):
        await app["storage"].warmup()
    if not is_test:
        await _maybe_generate_snapshot(app)


async def on_cleanup(app: web.Application) -> None:
    storage = app.get("storage")
    if storage is not None and hasattr(storage, "close"):
        await storage.close()
    await close_db(app)
    log.info("SIGE-GE detenido")


async def serve_index(request: web.Request) -> web.Response:
    return web.FileResponse(FRONTEND / "index.html")


@web.middleware
async def no_cache_middleware(request: web.Request, handler) -> web.Response:
    response = await handler(request)
    if not request.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


def create_app() -> web.Application:
    app = web.Application(middlewares=[no_cache_middleware])
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
    app.router.add_post("/api/sync/apply", sync.apply_pending)

    app.router.add_get("/", serve_index)
    app.router.add_static("/", FRONTEND)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    web.run_app(create_app(), host="localhost", port=8080)
