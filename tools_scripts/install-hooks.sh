#!/bin/bash
# Instala los git hooks del proyecto en .git/hooks/.
# Ejecutar una vez por clon: bash tools_scripts/install-hooks.sh

HOOKS_DIR="$(git rev-parse --show-toplevel)/.git/hooks"
SCRIPTS_DIR="$(git rev-parse --show-toplevel)/tools_scripts"

install_hook() {
    local name="$1"
    local script="$2"
    local target="$HOOKS_DIR/$name"
    cp "$script" "$target"
    chmod +x "$target"
    echo "Hook instalado: $target"
}

install_hook "pre-push" "$SCRIPTS_DIR/hooks/pre-push"
echo "Listo."
