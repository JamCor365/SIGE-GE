# SIGE-GE — Inicio Windows
# Uso:  clic derecho → "Ejecutar con PowerShell"
#   o:  PowerShell -ExecutionPolicy Bypass -File INICIAR.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─── 1. Directorio de trabajo = carpeta del script ───────────────────────────
Set-Location $PSScriptRoot

# ─── 2. uv.exe ───────────────────────────────────────────────────────────────
$uv = Join-Path $PSScriptRoot "tools\uv.exe"
if (-not (Test-Path $uv)) {
    Write-Host ""
    Write-Host "ERROR: tools\uv.exe no encontrado." -ForegroundColor Red
    Write-Host "Descargalo desde: https://github.com/astral-sh/uv/releases"
    Write-Host "Archivo necesario: uv-x86_64-pc-windows-msvc.zip  (extraer uv.exe a tools\)"
    Write-Host ""
    Read-Host "Presiona Enter para salir"
    exit 1
}

# ─── 3. Python portátil (sin admin, dentro del proyecto) ─────────────────────
# uv descargará Python 3.12 aqui si no existe (~40 MB, una sola vez).
# Incluir tools\python\ en el bundle hace el despliegue completamente offline.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot "tools\python"

# ─── 4. settings.toml ────────────────────────────────────────────────────────
$settingsPath = Join-Path $PSScriptRoot "config\settings.toml"
if (-not (Test-Path $settingsPath)) {
    Copy-Item (Join-Path $PSScriptRoot "config\settings.example.toml") $settingsPath
    Write-Host "config\settings.toml creado desde plantilla. Revísalo antes de producción." -ForegroundColor Yellow
}

# ─── 5. Directorios de datos ─────────────────────────────────────────────────
@(
    "data\mock_sharepoint\events_pending",
    "data\mock_sharepoint\events_processed",
    "data\mock_sharepoint\events_error",
    "data\mock_sharepoint\master",
    "data\pending_local",
    "data\edge_profile"
) | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot $_) | Out-Null
}

# Crea cache.db vacío si no existe (init_db aplica el esquema completo al iniciar)
$dbPath = Join-Path $PSScriptRoot "data\cache.db"
if (-not (Test-Path $dbPath)) {
    New-Item -ItemType File -Force -Path $dbPath | Out-Null
    Write-Host "data\cache.db creado. El esquema SQLite se aplicará al iniciar." -ForegroundColor Yellow
}

# ─── 6. Instalar / verificar dependencias ────────────────────────────────────
$wheelsDir = Join-Path $PSScriptRoot "dist\wheels"
if (Test-Path $wheelsDir) {
    Write-Host "Dependencias: modo offline (dist\wheels)..." -ForegroundColor Cyan
    & $uv sync --find-links $wheelsDir --no-index
} else {
    Write-Host "Dependencias: descargando (requiere internet la primera vez)..." -ForegroundColor Cyan
    & $uv sync
}

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: fallo al instalar dependencias." -ForegroundColor Red
    Write-Host "Si no hay internet, ejecuta primero: bash tools_scripts/empaquetar_windows.sh"
    Write-Host ""
    Read-Host "Presiona Enter para salir"
    exit 1
}

# ─── 7. Abrir navegador tras 2 s (mientras el servidor arranca) ───────────────
$null = Start-Job -ScriptBlock { Start-Sleep 2; Start-Process "http://localhost:8080" }

# ─── 8. Iniciar servidor (foreground — Ctrl+C para detener) ──────────────────
Write-Host ""
Write-Host "  SIGE-GE iniciando en http://localhost:8080" -ForegroundColor Green
Write-Host "  Presiona Ctrl+C para detener el servidor." -ForegroundColor DarkGray
Write-Host ""

& $uv run python -m backend.server
