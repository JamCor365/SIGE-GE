import aiosqlite
from aiohttp import web


def row_to_dict(row: aiosqlite.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def error_response(reason: str, status: int) -> web.Response:
    return web.json_response({"status": "error", "reason": reason}, status=status)


async def read_json(request: web.Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("JSON inválido") from exc
    if not isinstance(payload, dict):
        raise ValueError("El payload debe ser un objeto JSON")
    return payload


def build_insert_sql(table: str, payload: dict) -> tuple[str, list]:
    payload.setdefault("created_at", "datetime('now','localtime')")
    payload.setdefault("updated_at", "datetime('now','localtime')")

    fields = list(payload.keys())
    placeholders = []
    values = []
    for field in fields:
        if field in {"created_at", "updated_at"} and payload[field] == "datetime('now','localtime')":
            placeholders.append("datetime('now','localtime')")
        else:
            placeholders.append("?")
            values.append(payload[field])

    sql = f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
    return sql, values


async def get_by_id(db: aiosqlite.Connection, source: str, item_id: int) -> dict | None:
    async with db.execute(f"SELECT * FROM {source} WHERE id = ?", (item_id,)) as cur:
        return row_to_dict(await cur.fetchone())
