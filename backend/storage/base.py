from abc import ABC, abstractmethod


class StorageBackend(ABC):
    @abstractmethod
    async def upload_event(self, event: dict) -> None: ...

    @abstractmethod
    async def download_event(self, event_id: str) -> dict | None: ...

    @abstractmethod
    async def list_pending(self) -> list[str]: ...

    # DEPRECATED: no usado desde Fase 1, ver archive_processed
    @abstractmethod
    async def mark_processed(self, event_id: str) -> None: ...

    @abstractmethod
    async def archive_processed(self, event_id: str) -> None:
        """Archiva events_pending/{id}.json → events_processed/{id}.json.

        Copia con overwrite y luego borra de pending. Idempotente: si el archivo
        ya no está en pending (corrida previa lo movió), es no-op sin error.
        """
        ...

    @abstractmethod
    async def upload_snapshot(self, db_bytes: bytes, meta: dict) -> None: ...

    @abstractmethod
    async def download_snapshot_db(self) -> bytes | None: ...

    @abstractmethod
    async def download_snapshot_meta(self) -> dict | None: ...
