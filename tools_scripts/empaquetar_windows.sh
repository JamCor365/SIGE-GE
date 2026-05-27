#!/usr/bin/env bash
# tools_scripts/empaquetar_windows.sh
#
# Ejecutar en Ubuntu para descargar los wheels de Python necesarios
# para el despliegue offline en Windows (win_amd64, Python 3.12).
#
# Resultado: dist/wheels/ con todos los .whl del proyecto (sin dev deps).
#
# USO:
#   bash tools_scripts/empaquetar_windows.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
WHEELS_DIR="$ROOT/dist/wheels"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  SIGE-GE · Empaquetado para Windows"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# ── Verificar uv ──────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "ERROR: 'uv' no está en PATH."
    echo "Instálalo con:  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# ── Descargar wheels Windows ──────────────────────────────────────────────────
echo "→ Descargando wheels para win_amd64 / Python 3.12..."
mkdir -p "$WHEELS_DIR"

TEMP_REQ="$(mktemp /tmp/sige-req-XXXXX.txt)"

# Exportar solo dependencias de producción (sin dev: pytest, httpx, etc.)
uv export \
    --no-dev \
    --no-hashes \
    --format requirements-txt \
    --project "$ROOT" \
    > "$TEMP_REQ"

# uv no tiene "uv pip download"; usamos un venv temporal con pip seed
DL_ENV="$(mktemp -d /tmp/sige-dl-XXXXX)"
trap 'rm -f "$TEMP_REQ"; rm -rf "$DL_ENV"' EXIT

uv venv "$DL_ENV/venv" --seed -q
"$DL_ENV/venv/bin/pip" download \
    --platform win_amd64 \
    --python-version 3.12 \
    --only-binary :all: \
    -r "$TEMP_REQ" \
    -d "$WHEELS_DIR" \
    -q

WHEEL_COUNT=$(ls "$WHEELS_DIR"/*.whl 2>/dev/null | wc -l)
echo ""
echo "✓ $WHEEL_COUNT wheels descargados en dist/wheels/"
echo ""

# ── Instrucciones completas ───────────────────────────────────────────────────
cat <<'INSTRUCCIONES'
═══════════════════════════════════════════════════════════════
  PRÓXIMOS PASOS — leer antes de distribuir
═══════════════════════════════════════════════════════════════

PASO 1 — Preparar Python portable (una vez, con internet, en cualquier PC Windows)
─────────────────────────────────────────────────────────────
Copia la carpeta SIGE-GE al PC Windows y ejecuta en PowerShell:

    Set-ExecutionPolicy -Scope CurrentUser Bypass
    .\INICIAR.ps1

Esto descarga Python 3.12 a tools\python\ (~40 MB) y verifica las
dependencias desde dist\wheels\ (offline).  Al terminar, el navegador
abre http://localhost:8080.

PASO 2 — Crear el bundle offline definitivo (distribuir sin internet)
─────────────────────────────────────────────────────────────
Tras PASO 1 exitoso, detén el servidor (Ctrl+C) y ejecuta en PowerShell
desde la carpeta SIGE-GE:

    Compress-Archive `
      -Path .\* `
      -DestinationPath ..\SIGE-GE-bundle.zip `
      -CompressionLevel Optimal

El zip incluye tools\python\ + dist\wheels\ + .venv\
→ en cualquier otro PC Windows bastará extraer y ejecutar .\INICIAR.ps1

PASO 3 — Importar datos reales (banco)
─────────────────────────────────────────────────────────────
Con el servidor detenido, copia el Excel original y ejecuta:

    .\tools\uv.exe run python tools_scripts\import_excel.py `
      --excel ruta\al\SIGE_GE_Master_API.xlsx

Luego reinicia con .\INICIAR.ps1.

NOTA sobre SharePoint (Fase S1)
─────────────────────────────────────────────────────────────
Cuando SharePointBackend esté implementado, cambia en config\settings.toml:
    mode = "sharepoint"
y ejecuta una vez con internet:
    .\tools\uv.exe run playwright install msedge

Playwright usará el Edge ya instalado en Windows; no descarga navegadores.
═══════════════════════════════════════════════════════════════
INSTRUCCIONES
