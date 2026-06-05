from abc import ABC, abstractmethod


class StorageBackend(ABC):
    @abstractmethod
    async def upload_event(self, event: dict) -> None: ...

    @abstractmethod
    async def download_event(self, event_id: str) -> dict | None: ...

    @abstractmethod
    async def list_pending(self) -> list[str]: ...

    @abstractmethod
    async def mark_processed(self, event_id: str) -> None: ...

    @abstractmethod
    async def upload_snapshot(self, db_bytes: bytes, meta: dict) -> None: ...

    @abstractmethod
    async def download_snapshot_db(self) -> bytes | None: ...

    @abstractmethod
    async def download_snapshot_meta(self) -> dict | None: ...
