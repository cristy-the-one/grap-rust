#requires -Version 7.0
<#
    Self-contained ro-law ontology demo - one-time setup. Idempotent.

    Installs Ollama + pulls the local LLM/embedding models, creates a
    dedicated venv (.venv-demo), installs the bundled prebuilt pygrap wheel
    (or builds it when maturin/Rust are present and the wheel is absent),
    installs the GUI/server deps, and writes demo.env wiring the local stack.
#>
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Write-Host "== grap ro-law ontology demo: bootstrap ==" -ForegroundColor Cyan

try { & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | ForEach-Object { Write-Host "GPU: $_" } }
catch { Write-Warning "nvidia-smi not found - Ollama will fall back to CPU (slow)." }

# --- Ollama --------------------------------------------------------------
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Ollama..." -ForegroundColor Yellow
    try { winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements }
    catch { throw "winget install failed. Install Ollama from https://ollama.com/download then re-run." }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw "Ollama installed but not on PATH yet. Open a new terminal and re-run this script."
    }
} else { Write-Host "Ollama present." }

Write-Host "Pulling models (first run downloads ~10GB)..." -ForegroundColor Yellow
ollama pull qwen2.5:14b-instruct-q4_K_M
ollama pull bge-m3

# --- Python venv ---------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.13 x64 not found on PATH." }
$ver = (& python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($ver -ne "3.13") { throw "Python $ver found; this demo's bundled pygrap wheel needs Python 3.13 x64." }
$is64 = (& python -c "import sys;print(sys.maxsize > 2**32)").Trim()
if ($is64 -ne "True") { throw "32-bit Python found; this demo needs Python 3.13 x64." }

$venv = Join-Path $RepoRoot ".venv-demo"
if (-not (Test-Path $venv)) { & python -m venv $venv }
$vpy = Join-Path $venv "Scripts\python.exe"
& $vpy -m pip install --upgrade pip

# --- pygrap wheel (use bundled; build if absent and toolchain present) ---
$wheelDir = Join-Path $PSScriptRoot "wheels"
$wheel = Get-ChildItem $wheelDir -Filter "pygrap-*win_amd64.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $wheel) {
    Write-Host "No bundled pygrap wheel found; attempting to build one..." -ForegroundColor Yellow
    $haveMaturin = ($null -ne (Get-Command maturin -ErrorAction SilentlyContinue)) -or `
                   (& $vpy -c "import maturin" 2>$null; $LASTEXITCODE -eq 0)
    $haveCargo = $null -ne (Get-Command cargo -ErrorAction SilentlyContinue)
    if ($haveCargo) {
        if (-not (Get-Command maturin -ErrorAction SilentlyContinue)) { & $vpy -m pip install maturin }
        $manifest = Join-Path $RepoRoot "crates\pygrap\Cargo.toml"
        Write-Host "Building pygrap wheel (release) - this can take a few minutes..." -ForegroundColor Yellow
        & $vpy -m maturin build --release -m $manifest --out $wheelDir
        $wheel = Get-ChildItem $wheelDir -Filter "pygrap-*win_amd64.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if (-not $wheel) {
        Write-Host ""
        Write-Host "Could not obtain a pygrap wheel automatically." -ForegroundColor Red
        Write-Host "Build it on a Python 3.13 x64 box with Rust + maturin installed:" -ForegroundColor Yellow
        Write-Host "  python -m pip install maturin" -ForegroundColor Yellow
        Write-Host "  maturin build --release -m crates\pygrap\Cargo.toml --out demo\win\wheels" -ForegroundColor Yellow
        Write-Host "Then re-run this script." -ForegroundColor Yellow
        throw "pygrap wheel missing and no Rust toolchain available to build it."
    }
}
Write-Host "Installing pygrap wheel: $($wheel.Name)" -ForegroundColor Green
& $vpy -m pip install --force-reinstall $wheel.FullName

# --- Server + GUI deps ---------------------------------------------------
# mcp-server is a real package (declares mcp + PyYAML; pygrap already satisfied
# by the wheel above). Install it editable so grap_mcp's deps are resolved,
# then add the FastAPI GUI deps used by aspice-gui/backend.
& $vpy -m pip install -e (Join-Path $RepoRoot "mcp-server")
& $vpy -m pip install fastapi "uvicorn[standard]" pydantic

# --- demo.env ------------------------------------------------------------
$envPath = Join-Path $PSScriptRoot "demo.env"
@"
GRAP_LLM_BASE_URL=http://localhost:11434/v1
GRAP_LLM_MODEL=qwen2.5:14b-instruct-q4_K_M
GRAP_EMBED_BACKEND=ollama
GRAP_EMBED_MODEL=bge-m3
GRAP_WORKSPACE=$($RepoRoot -replace '\\','/')/demo/workspace
ASPICE_GUI_PORT=8080
"@ | Set-Content -Path $envPath -Encoding utf8
Write-Host "Wrote $envPath" -ForegroundColor Green
Write-Host "Bootstrap complete. Run:  pwsh -File .\demo\win\run-demo.ps1" -ForegroundColor Cyan
