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

    async def upload_snapshot(self, db_bytes: bytes, meta: dict) -> None:
        (self._master / "latest_snapshot.db").write_bytes(db_bytes)
        (self._master / "snapshot_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def download_snapshot_db(self) -> bytes | None:
        path = self._master / "latest_snapshot.db"
        return path.read_bytes() if path.exists() else None

    async def download_snapshot_meta(self) -> dict | None:
        path = self._master / "snapshot_meta.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    async def list_pending(self) -> list[str]:
        return [f.stem for f in sorted(self._pending.glob("*.json"))]

    async def download_event(self, event_id: str) -> dict | None:
        path = self._pending / f"{event_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # DEPRECATED: no usado desde Fase 1, ver archive_processed
    async def mark_processed(self, event_id: str) -> None:
        src = self._pending / f"{event_id}.json"
        if src.exists():
            shutil.move(str(src), self._processed / f"{event_id}.json")

    async def archive_processed(self, event_id: str) -> None:
        src = self._pending / f"{event_id}.json"
        if not src.exists():
            return                                   # ya archivado → idempotente
        # copy(overwrite) → unlink: si crashea entre medio, la próxima corrida
        # re-copia (overwrite) y re-borra sin error.
        shutil.copy2(str(src), str(self._processed / f"{event_id}.json"))
        src.unlink()
