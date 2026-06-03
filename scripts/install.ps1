# One-shot installer for a fresh Windows system.
# Sets up a self-contained venv with: pygrap (grap-rust-core bindings),
# the grap MCP server, and the ASPICE/KEE demo GUI.
#
#   .\scripts\install.ps1            # creates .\.venv and installs everything
#   .\scripts\install.ps1 -Venv .v  # custom venv dir
#
# Prerequisites: Python 3.11+ (x64) on PATH. Rust is ONLY needed if the bundled
# pygrap wheel doesn't match your Python (then it builds from source).
param([string]$Venv = ".venv")
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPath = Join-Path $Root $Venv
$Py = Join-Path $VenvPath "Scripts\python.exe"

Write-Host "== grap demo installer ==" -ForegroundColor Cyan
Write-Host "repo:  $Root"
Write-Host "venv:  $VenvPath"

# 1. virtual environment
if (-not (Test-Path $Py)) {
    Write-Host "`n[1/4] Creating virtual environment..."
    python -m venv $VenvPath
}
& $Py -m pip install -q -U pip wheel

# 2. pygrap: prefer the bundled wheel; fall back to building from source (Rust)
Write-Host "`n[2/4] Installing pygrap (grap-rust-core bindings)..."
$wheel = Get-ChildItem (Join-Path $Root "packaging\wheels\pygrap-*.whl") -ErrorAction SilentlyContinue |
    Select-Object -First 1
$pygrapOk = $false
if ($wheel) {
    Write-Host "  trying bundled wheel: $($wheel.Name)"
    & $Py -m pip install --force-reinstall --no-deps $wheel.FullName 2>$null
    $pygrapOk = ($LASTEXITCODE -eq 0)
}
if (-not $pygrapOk) {
    Write-Host "  bundled wheel not compatible with this Python; building from source (needs Rust + maturin)..." -ForegroundColor Yellow
    & $Py -m pip install -q maturin
    & $Py -m maturin build --release -m (Join-Path $Root "crates\pygrap\Cargo.toml") -o (Join-Path $Root "packaging\wheels")
    if ($LASTEXITCODE -ne 0) { throw "pygrap build failed. Install the Rust toolchain (https://rustup.rs) and retry." }
    $wheel = Get-ChildItem (Join-Path $Root "packaging\wheels\pygrap-*.whl") |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    & $Py -m pip install --force-reinstall --no-deps $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "pygrap install failed" }
}

# 3. MCP server (pulls mcp + PyYAML) and the GUI deps (fastapi>=0.128, uvicorn)
Write-Host "`n[3/4] Installing the MCP server and demo dependencies..."
& $Py -m pip install -q (Join-Path $Root "mcp-server")
if ($LASTEXITCODE -ne 0) { throw "mcp-server install failed" }
& $Py -m pip install -q -r (Join-Path $Root "aspice-gui\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "GUI dependency install failed" }

# 4. verify the stack imports
Write-Host "`n[4/4] Verifying..."
& $Py -c "import pygrap, fastapi, uvicorn, yaml, mcp; import grap_mcp.server; print('imports OK; pygrap at', pygrap.__file__)"
if ($LASTEXITCODE -ne 0) { throw "verification failed" }

Write-Host "`nDone." -ForegroundColor Green
Write-Host "Run the demo:  .\scripts\run-demo.ps1"
Write-Host "Then open:     http://127.0.0.1:8765"
