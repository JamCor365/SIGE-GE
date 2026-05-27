from .base import StorageBackend


def get_backend(config: dict) -> StorageBackend:
    mode = config["storage"]["mode"]
    if mode == "local":
        from .local_folder import LocalFolderBackend
        return LocalFolderBackend(config["storage"]["local"]["base_path"])
    if mode == "sharepoint":
        from .sharepoint import SharePointBackend  # importa Playwright solo si se necesita
        return SharePointBackend(config["storage"]["sharepoint"])
    raise ValueError(f"Storage mode desconocido: {mode!r}")
