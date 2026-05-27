import json
import logging

from aiohttp import web

log = logging.getLogger("sige.sync")


async def list_pending(request: web.Request) -> web.Response:
    db = request.app["db"]
    storage = request.app["storage"]

    if hasattr(storage, "ready") and not storage.ready:
        return web.json_response({
            "status": "initializing",
            "pending_storage": -1,
            "pending_db": 0,
            "events": [],
        })

    try:
        pending_storage = len(await storage.list_pending())
    except Exception as exc:
        log.warning("list_pending falló (storage no disponible): %s", exc)
        pending_storage = -1

    async with db.execute(
        "SELECT COUNT(*) FROM events_log WHERE synced = 0"
    ) as cur:
        row = await cur.fetchone()
        pending_db = row[0] if row else 0

    events = []
    async with db.execute(
        """
        SELECT event_id, entity, action, entity_id, created_at,
               created_by, machine, app_version, payload_json
        FROM events_log
        WHERE synced = 0
        ORDER BY created_at ASC
        """
    ) as cur:
        async for row in cur:
            events.append(
                {
                    "event_id": row["event_id"],
                    "entity": row["entity"],
                    "action": row["action"],
                    "entity_id": row["entity_id"],
                    "created_at": row["created_at"],
                    "created_by": row["created_by"],
                    "machine": row["machine"],
                    "app_version": row["app_version"],
                    "payload": json.loads(row["payload_json"]),
                }
            )

    return web.json_response(
        {
            "status": "ok",
            "pending_storage": pending_storage,
            "pending_db": pending_db,
            "events": events,
        }
    )
