# Portable, offline, headless tour of grap's capabilities (no GUI, no server, no network).
# A self-contained console walkthrough of THIS version: RRF retrieval, graph analytics,
# the SQLite entity index, content-hash integrity, session/context bundle, telemetry,
# the #MUNCH wire encoding, and tool tiering -- run against a tiny throwaway KG.
#
#   .\scripts\portable-demo.ps1                 # default: full tool tier
#   .\scripts\portable-demo.ps1 -Tier core      # shrink the tool surface (~9 tools)
#   .\scripts\portable-demo.ps1 -Venv .venv     # pick a specific venv
#
# Needs only `pygrap` to be built/installed (see .\scripts\install.ps1). Everything else
# the demo uses is Python stdlib + the in-repo grap_mcp package.
param([string]$Venv = ".venv", [string]$Tier = "")
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Make the in-repo grap_mcp package importable (the demo also does this at runtime).
$env:PYTHONPATH = (Join-Path $Root "mcp-server") + ";" + $env:PYTHONPATH

# Pick a Python that has the demo's non-stdlib deps: the compiled `pygrap` extension
# AND the `mcp` SDK that grap_mcp.server imports. Prefer the venv if complete, else
# fall back to system Python (avoids the trap where .venv has pygrap but not mcp).
function Test-DemoDeps($exe) {
    if (-not $exe) { return $false }
    & $exe -c "import pygrap, mcp" 2>$null
    return ($LASTEXITCODE -eq 0)
}
$VenvPy = Join-Path $Root "$Venv\Scripts\python.exe"
$Py = $null
if ((Test-Path $VenvPy) -and (Test-DemoDeps $VenvPy)) { $Py = $VenvPy }
elseif (Test-DemoDeps "python")                       { $Py = "python" }
if (-not $Py) {
    throw "No Python with the demo deps (pygrap + the 'mcp' SDK). Run .\scripts\install.ps1 first " +
          "(builds pygrap and installs the server deps)."
}

# Tool tier is read at import time; pass it through so the demo reports it.
if ($Tier) { $env:GRAP_TOOL_TIER = $Tier }
# Opt-in telemetry so the demo's get_telemetry step has data (local-only; never phones home).
if (-not $env:GRAP_TELEMETRY) { $env:GRAP_TELEMETRY = "1" }

$tierLabel = if ($Tier) { $Tier } else { "full (default)" }
Write-Host "grap portable demo  (python: $Py; tool tier: $tierLabel)" -ForegroundColor Green
& $Py (Join-Path $Root "demo\python\portable_demo.py")
