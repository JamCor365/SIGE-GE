#!/usr/bin/env bash
set -e

# Copia settings si no existe
if [ ! -f config/settings.toml ]; then
    cp config/settings.example.toml config/settings.toml
    echo "config/settings.toml creado desde example."
fi

uv sync
uv run python -m backend.server
