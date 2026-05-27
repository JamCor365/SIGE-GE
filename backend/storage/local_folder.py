import json
import shutil
from pathlib import Path

from .base import StorageBackend


class LocalFolderBackend(StorageBackend):
    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)
        self._pending   = self._base / "events_pending"
        self._processed = self._base / "events_processed"
        self._error     = self._base / "events_error"
        self._master    = self._base / "master"
        for folder in (self._pending, self._processed, self._error, self._master):
            folder.mkdir(parents=True, exist_ok=True)

    async def upload_event(self, event: dict) -> None:
        event_id = event["event_id"]
        target = self._pending / f"{event_id}.json"
        target.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")

    async def download_snapshot(self) -> dict | None:
        snapshot = self._master / "latest_snapshot.json"
        if not snapshot.exists():
            return None
        return json.loads(snapshot.read_text(encoding="utf-8"))

    async def list_pending(self) -> list[str]:
        return [f.stem for f in sorted(self._pending.glob("*.json"))]

    async def mark_processed(self, event_id: str) -> None:
        src = self._pending / f"{event_id}.json"
        if src.exists():
            shutil.move(str(src), self._processed / f"{event_id}.json")
