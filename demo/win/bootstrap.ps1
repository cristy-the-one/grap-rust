#requires -Version 7.0
<#
    Self-contained ro-law ontology demo - one-time setup. Idempotent.

    Installs Ollama + pulls the local LLM/embedding models, creates a
    dedicated venv (.venv-demo), installs the bundled prebuilt pygrap wheel
    (or builds it when maturin/Rust are present and the wheel is absent),
    installs the GUI/server deps, and writes demo.env wiring the local stack.

    Requires a full grap-rust checkout (mcp-server/, aspice-gui/) - the demo
    folder cannot be copied out of the repository on its own.
#>
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Write-Host "== grap ro-law ontology demo: bootstrap ==" -ForegroundColor Cyan

# $ErrorActionPreference does not cover native exit codes, so every critical
# external command goes through Invoke-Step to fail fast instead of letting a
# broken install report "Bootstrap complete".
function Invoke-Step {
    param([string]$What, [scriptblock]$Action)
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

foreach ($required in "mcp-server\pyproject.toml", "aspice-gui\backend\main.py") {
    if (-not (Test-Path (Join-Path $RepoRoot $required))) {
        throw ("{0} not found under {1}. The demo needs the full grap-rust checkout; run this script from inside the repository, not from a copied demo folder." -f $required, $RepoRoot)
    }
}

try { & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | ForEach-Object { Write-Host "GPU: $_" } }
catch { Write-Warning "nvidia-smi not found - Ollama will fall back to CPU (slow)." }

# --- Ollama --------------------------------------------------------------
. (Join-Path $PSScriptRoot "common.ps1")

$ollama = Resolve-Ollama
if (-not $ollama) {
    Write-Host "Installing Ollama..." -ForegroundColor Yellow
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget not available. Install Ollama from https://ollama.com/download then re-run this script."
    }
    Invoke-Step "winget install Ollama" {
        & winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    }
    $ollama = Resolve-Ollama
    if (-not $ollama) {
        throw "Ollama installed but not found on PATH or in $env:LOCALAPPDATA\Programs\Ollama. Open a new terminal and re-run this script."
    }
}
Write-Host "Ollama: $ollama" -ForegroundColor Green

# `ollama pull` talks to the local server, so make sure one is running first.
if (-not (Test-OllamaUp)) { Start-OllamaServer $ollama }

Write-Host "Pulling models (first run downloads ~10GB)..." -ForegroundColor Yellow
Invoke-Step "ollama pull qwen2.5:14b-instruct-q4_K_M" { & $ollama pull qwen2.5:14b-instruct-q4_K_M }
Invoke-Step "ollama pull bge-m3" { & $ollama pull bge-m3 }

# --- Python venv ---------------------------------------------------------
function Resolve-Python313 {
    # Try the py launcher pinned to 3.13 first, then plain `python`. Probing
    # with -c also filters out the Microsoft Store stub, which is on PATH on
    # pristine systems but only prints an install hint and exits non-zero.
    $candidates = @(
        @{ Exe = "py";     Args = @("-3.13") },
        @{ Exe = "python"; Args = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $probe = & $cmd.Source @($c.Args) -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor} {sys.maxsize > 2**32}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $probe) { continue }
        $ver, $is64 = -split "$probe"
        if ($ver -eq "3.13" -and $is64 -eq "True") {
            return @{ Exe = $cmd.Source; Args = $c.Args }
        }
        Write-Host "Skipping $($cmd.Source): Python $ver, 64-bit=$is64 (need 3.13 x64 for the bundled pygrap wheel)."
    }
    return $null
}

$py = Resolve-Python313
if (-not $py) {
    throw "Python 3.13 x64 not found (checked 'py -3.13' and 'python'; the Microsoft Store stub does not count). Install it from https://www.python.org/downloads/ then re-run this script."
}

$venv = Join-Path $RepoRoot ".venv-demo"
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    Invoke-Step "venv creation" { & $py.Exe @($py.Args) -m venv $venv }
}
Invoke-Step "pip upgrade" { & $vpy -m pip install --upgrade pip }

# --- pygrap wheel (use bundled; build if absent and toolchain present) ---
$wheelDir = Join-Path $PSScriptRoot "wheels"
$wheel = Get-ChildItem $wheelDir -Filter "pygrap-*win_amd64.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $wheel) {
    Write-Host "No bundled pygrap wheel found; attempting to build one..." -ForegroundColor Yellow
    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        # maturin must live in the venv: the build runs via `$vpy -m maturin`.
        Invoke-Step "maturin install" { & $vpy -m pip install maturin }
        $manifest = Join-Path $RepoRoot "crates\pygrap\Cargo.toml"
        Write-Host "Building pygrap wheel (release) - this can take a few minutes..." -ForegroundColor Yellow
        Invoke-Step "pygrap wheel build" { & $vpy -m maturin build --release -m $manifest --out $wheelDir }
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
Invoke-Step "pygrap wheel install" { & $vpy -m pip install --force-reinstall $wheel.FullName }

# --- Server + GUI deps ---------------------------------------------------
# mcp-server is a real package (declares mcp + PyYAML; pygrap already satisfied
# by the wheel above). Install it editable so grap_mcp's deps are resolved,
# then add the FastAPI GUI deps used by aspice-gui/backend.
Invoke-Step "mcp-server install" { & $vpy -m pip install -e (Join-Path $RepoRoot "mcp-server") }
Invoke-Step "GUI deps install" { & $vpy -m pip install fastapi "uvicorn[standard]" pydantic }

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
