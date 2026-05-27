import tomllib
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.toml"


def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"settings.toml no encontrado en {_CONFIG_PATH}. "
            "Copia config/settings.example.toml a config/settings.toml."
        )
    with _CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)
