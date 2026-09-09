param(
    [int]$Port = 8766,
    [string]$Workspace,
    [switch]$LocalLegalProfile
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
if ($LocalLegalProfile) {
    $env:GRAP_LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
    $env:GRAP_LLM_MODEL = 'grap-legal-gemma4'
    $env:GRAP_LLM_EXTRA_BODY = '{"reasoning_effort":"none"}'
    $env:GRAP_EMBED_BASE_URL = 'http://127.0.0.1:11434/v1'
    $env:GRAP_EMBED_BACKEND = 'ollama'
    $env:GRAP_EMBED_MODEL = 'bge-m3'
    $env:GRAP_KEE_STRUCTURED_OUTPUT = '1'
    Write-Host 'Local legal profile: Gemma + BGE-M3; structured source-grounded extraction'
}
Write-Host "Legal source review -> http://127.0.0.1:$Port/legal-review"
Write-Host "Workspace: $env:GRAP_WORKSPACE"
& $Python -m uvicorn backend.main:app --host 127.0.0.1 --port $Port --app-dir $GuiRoot
exit $LASTEXITCODE
