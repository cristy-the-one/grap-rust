#requires -Version 7.0
<#
    Launch the ro-law ontology demo: load demo.env, start Ollama, serve the
    aspice-gui FastAPI app, and open the browser. Run bootstrap.ps1 first.
#>
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
$venv = Join-Path $RepoRoot ".venv-demo"
$vpy  = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) { throw "Run  pwsh -File .\demo\win\bootstrap.ps1  first." }

# Load demo.env into this process's environment.
$envFile = Join-Path $PSScriptRoot "demo.env"
if (-not (Test-Path $envFile)) { throw "demo.env not found. Run bootstrap.ps1 first." }
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
    }
}

# Start Ollama if it is not already serving. Probe the API rather than the
# process list (the desktop app's process is not named `ollama`).
. (Join-Path $PSScriptRoot "common.ps1")
if (-not (Test-OllamaUp)) {
    $ollama = Resolve-Ollama
    if (-not $ollama) { throw "Ollama not found. Run  pwsh -File .\demo\win\bootstrap.ps1  first." }
    Start-OllamaServer $ollama
}

# Port: prefer ASPICE_GUI_PORT from demo.env, else 8080. uvicorn.run() in
# backend.main also honors ASPICE_GUI_PORT, but we pass it explicitly so the
# browser URL and the bound port always agree.
$port = $env:ASPICE_GUI_PORT
if ([string]::IsNullOrWhiteSpace($port)) { $port = "8080" }

# aspice-gui/backend imports package-relative (`from . import bridge`) and
# bridge.py inserts mcp-server onto sys.path itself, so we only need aspice-gui
# on the app path. The static frontend is mounted relative to __file__, so the
# working directory does not matter. This matches backend.main:run().
Write-Host "Starting GUI at http://localhost:$port ..." -ForegroundColor Cyan
Start-Process "http://localhost:$port"
& $vpy -m uvicorn backend.main:app --app-dir (Join-Path $RepoRoot "aspice-gui") --host 127.0.0.1 --port $port
