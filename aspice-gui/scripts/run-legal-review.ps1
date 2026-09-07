param(
    [int]$Port = 8766,
    [string]$Workspace
)
$ErrorActionPreference = 'Stop'
$GuiRoot = Split-Path $PSScriptRoot -Parent
$RepoRoot = (Resolve-Path (Join-Path $GuiRoot '..')).Path
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }
if (-not $Workspace) { $Workspace = Join-Path $RepoRoot 'pilot-law-ro\review' }
$env:GRAP_WORKSPACE = [System.IO.Path]::GetFullPath($Workspace)
$env:GRAP_ROLE = 'operator'
$env:PYTHONPATH = Join-Path $RepoRoot 'mcp-server'
Write-Host "Legal source review -> http://127.0.0.1:$Port/legal-review"
Write-Host "Workspace: $env:GRAP_WORKSPACE"
& $Python -m uvicorn backend.main:app --host 127.0.0.1 --port $Port --app-dir $GuiRoot
exit $LASTEXITCODE
