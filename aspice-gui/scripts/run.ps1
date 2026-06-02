# Run the ASPICE GUI.
# Uses the current environment's Python by default — the project pins
# fastapi>=0.128, which is compatible with the starlette 1.x that the MCP SDK
# (mcp>=1.27) pulls in, so no separate venv is required. If a local .venv
# happens to exist it is used automatically.
$GuiRoot = Split-Path $PSScriptRoot -Parent
$RepoRoot = (Resolve-Path (Join-Path $GuiRoot "..")).Path

$Venv = Join-Path $GuiRoot ".venv\Scripts\python.exe"
$Py = if (Test-Path $Venv) { $Venv } else { "python" }

$env:PYTHONPATH = Join-Path $RepoRoot "mcp-server"
if (-not $env:GRAP_WORKSPACE) {
    $env:GRAP_WORKSPACE = Join-Path $GuiRoot "grap-workspace"
}
$Port = if ($env:ASPICE_GUI_PORT) { $env:ASPICE_GUI_PORT } else { "8765" }

Set-Location $GuiRoot
Write-Host "ASPICE console -> http://127.0.0.1:$Port   (python: $Py)"
& $Py -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
