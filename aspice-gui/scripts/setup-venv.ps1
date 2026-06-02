# Create an isolated venv for aspice-gui (avoids global fastapi/starlette conflicts).
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$GuiRoot = Join-Path $RepoRoot "aspice-gui"
$Venv = Join-Path $GuiRoot ".venv"
$McpServer = Join-Path $RepoRoot "mcp-server"
$Pygrap = Join-Path $RepoRoot "crates\pygrap"
$Req = Join-Path $GuiRoot "requirements.txt"

if (-not (Test-Path $McpServer)) {
    throw "mcp-server not found at $McpServer"
}

Write-Host "Creating venv at $Venv"
if (-not (Test-Path $Venv)) {
    python -m venv $Venv
}
$Py = Join-Path $Venv "Scripts\python.exe"

& $Py -m pip install -U pip wheel
# GUI deps (fastapi>=0.128 works with starlette 1.x from MCP)
& $Py -m pip install -r $Req

if (Test-Path (Join-Path $Pygrap "pyproject.toml")) {
    Write-Host "Installing pygrap from $Pygrap ..."
    & $Py -m pip install -e $Pygrap
    if ($LASTEXITCODE -ne 0) {
        throw @"
pygrap install failed. From repo root run once:
  cd crates\pygrap
  maturin develop
Then re-run: .\aspice-gui\scripts\setup-venv.ps1
"@
    }
}

Write-Host "Installing grap-mcp-server from $McpServer ..."
& $Py -m pip install -e $McpServer
if ($LASTEXITCODE -ne 0) { throw "mcp-server install failed" }

Write-Host ""
& $Py -m pip check
Write-Host ""
Write-Host "Done. Activate and run:"
Write-Host "  $($Venv)\Scripts\Activate.ps1"
Write-Host "  cd $GuiRoot"
Write-Host "  python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765"
Write-Host "  # http://127.0.0.1:8765"