# Launch the ASPICE / Knowledge-Extraction demo GUI from the installed venv.
#   .\scripts\run-demo.ps1                 # http://127.0.0.1:8765
#   .\scripts\run-demo.ps1 -Port 8770
param([string]$Venv = ".venv", [int]$Port = 8765)
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Py = Join-Path $Root "$Venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    throw "venv not found at $Py — run .\scripts\install.ps1 first."
}

# Local-only stack; embeddings prefer Ollama (nomic-embed-text) and fall back to
# a hashing embedder if Ollama isn't running, so the demo works fully offline.
$env:ASPICE_GUI_PORT = "$Port"
if (-not $env:GRAP_WORKSPACE) {
    $env:GRAP_WORKSPACE = Join-Path $Root "aspice-gui\grap-workspace"
}

Write-Host "ASPICE / KEE console -> http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "(LLM features use Ollama at GRAP_LLM_BASE_URL if available; offline otherwise)"
& $Py -m uvicorn backend.main:app --host 127.0.0.1 --port $Port `
    --app-dir (Join-Path $Root "aspice-gui")
