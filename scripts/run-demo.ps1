# Launch the ASPICE / Knowledge-Extraction demo GUI from the installed venv.
#   .\scripts\run-demo.ps1                 # http://127.0.0.1:8765
#   .\scripts\run-demo.ps1 -Port 8770
param([string]$Venv = ".venv", [int]$Port = 8765)
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Make grap_mcp importable regardless of which Python we pick (bridge.py also does
# this at runtime; setting it here keeps the dep check consistent).
$env:PYTHONPATH = (Join-Path $Root "mcp-server") + ";" + $env:PYTHONPATH

# Pick a Python that actually has the demo deps: prefer the venv if it's complete,
# otherwise fall back to system Python (which may already have them). This avoids
# the "No module named uvicorn" trap when .venv exists but wasn't fully installed.
function Test-Deps($exe) {
    if (-not $exe) { return $false }
    & $exe -c "import uvicorn, fastapi" 2>$null
    return ($LASTEXITCODE -eq 0)
}
$VenvPy = Join-Path $Root "$Venv\Scripts\python.exe"
$Py = $null
if ((Test-Path $VenvPy) -and (Test-Deps $VenvPy)) { $Py = $VenvPy }
elseif (Test-Deps "python")                      { $Py = "python" }
if (-not $Py) {
    throw "No Python with the demo deps (uvicorn/fastapi). Run .\scripts\install.ps1 first."
}

# Local-only stack; embeddings prefer Ollama (nomic-embed-text) and fall back to
# a hashing embedder if Ollama isn't running, so the demo works fully offline.
$env:ASPICE_GUI_PORT = "$Port"
if (-not $env:GRAP_WORKSPACE) {
    $env:GRAP_WORKSPACE = Join-Path $Root "aspice-gui\grap-workspace"
}

Write-Host "ASPICE / KEE console -> http://127.0.0.1:$Port  (python: $Py)" -ForegroundColor Green
Write-Host "(LLM features use Ollama at GRAP_LLM_BASE_URL if available; offline otherwise)"
& $Py -m uvicorn backend.main:app --host 127.0.0.1 --port $Port `
    --app-dir (Join-Path $Root "aspice-gui")
