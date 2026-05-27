import json
import logging
import platform
import secrets
from datetime import datetime, timedelta, timezone

from aiohttp import web

log = logging.getLogger("sige.events")

_VALID_ACTIONS = {"create", "update", "delete"}
_VALID_ENTITIES = {"grupo_electrogeno", "sede", "macroregion", "tta"}


def make_event(
    action: str,
    entity_id: str | int,
    payload: dict,
    user: str,
    app_version: str,
) -> dict:
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action debe ser uno de {_VALID_ACTIONS}")
    if not payload:
        raise ValueError("payload no puede estar vacío")
    if "_entity" not in payload:
        raise ValueError("payload debe contener _entity")

    entity = payload.pop("_entity")
    if entity not in _VALID_ENTITIES:
        raise ValueError(f"_entity debe ser uno de {_VALID_ENTITIES}")

    now = datetime.now(timezone(offset=timedelta(hours=-5)))
    created_at = now.isoformat()

    hex6 = secrets.token_hex(3)
    event_id = (
        f"{now.strftime('%Y%m%d_%H%M%S')}_{user}_{action}_{entity}_{entity_id}_{hex6}"
    )

    machine = platform.node() or "LOCAL"

    return {
        "event_id": event_id,
        "entity": entity,
        "action": action,
        "entity_id": str(entity_id),
        "created_at": created_at,
        "created_by": user,
        "machine": machine,
        "app_version": app_version,
        "payload": dict(payload),
    }


def get_user(request: web.Request) -> str:
    user = request.headers.get("X-SIGE-User", "").strip()
    return user if user else "SIGE_LOCAL"


async def log_event(
    db,
    event: dict,
    synced: int,
    error_msg: str | None = None,
) -> None:
    if synced not in (0, 1):
        raise ValueError("synced debe ser 0 o 1")

    payload_json = json.dumps(event["payload"], ensure_ascii=False)
    synced_at = datetime.now(timezone(offset=timedelta(hours=-5))).isoformat() if synced == 1 else None

    await db.execute(
        """
        INSERT INTO events_log (
            event_id, entity, action, entity_id, created_at,
            created_by, machine, app_version, payload_json, synced, synced_at, error_msg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            event["entity"],
            event["action"],
            event["entity_id"],
            event["created_at"],
            event["created_by"],
            event["machine"],
            event["app_version"],
            payload_json,
            synced,
            synced_at,
            error_msg,
        ),
    )
