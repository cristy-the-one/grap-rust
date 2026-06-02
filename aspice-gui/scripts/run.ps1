# Run ASPICE GUI with the project venv (not global Python).
$GuiRoot = Split-Path $PSScriptRoot -Parent
$RepoRoot = (Resolve-Path (Join-Path $GuiRoot "..")).Path
$Py = Join-Path $GuiRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Error "Missing .venv. From repo root run: .\aspice-gui\scripts\setup-venv.ps1"
}

$env:PYTHONPATH = Join-Path $RepoRoot "mcp-server"
if (-not $env:GRAP_WORKSPACE) {
    $env:GRAP_WORKSPACE = Join-Path $GuiRoot "grap-workspace"
}

Set-Location $GuiRoot
Write-Host "http://127.0.0.1:8765"
& $Py -m uvicorn backend.main:app --host 127.0.0.1 --port 8765