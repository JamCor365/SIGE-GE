from abc import ABC, abstractmethod


class StorageBackend(ABC):
    @abstractmethod
    async def upload_event(self, event: dict) -> None: ...

    @abstractmethod
    async def download_snapshot(self) -> dict | None: ...

    @abstractmethod
    async def list_pending(self) -> list[str]: ...

    @abstractmethod
    async def mark_processed(self, event_id: str) -> None: ...
