<#
.SYNOPSIS
    Run docs/runbook-first-real-run.md sections 1 to 5 and write the section 7 report.

.DESCRIPTION
    Checks the run's rules first, then builds the graph (stub and local), generates
    the specification, and judges one recording. Every step's full output goes to
    <run>\logs\<step>.log. <run>\report.md gets only the lines the runbook says can
    be shared: step lines, status lines, counts, exit codes and durations.

    Nothing is written inside a git repository: the run folder is refused if it is
    in one. The model must be on this machine: a GRAP_LLM_BASE_URL whose host is not
    localhost, 127.0.0.1 or ::1 is refused.

    -From restarts at a later step of an existing run, e.g. after fixing the model
    server: -From generate reuses graph-out and the workspace.

.EXAMPLE
    .\scripts\first-real-run.ps1 -RunId p17-1 -ReqIf D:\corpus\spec.reqif -Dbc D:\corpus\bus.dbc `
        -Release R1 -ScopeIds REQ-A,REQ-B -Model qwen3-8b -Recording D:\logs\drive.asc
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunId,
    [string]$RunsRoot = "D:\grap-runs",
    [string[]]$ReqIf = @(),
    [string[]]$Dbc = @(),
    [string[]]$Arxml = @(),
    [Parameter(Mandatory)][string]$Release,
    [Parameter(Mandatory)][string[]]$ScopeIds,
    [string]$Recording,
    [string]$Model,
    [string]$BaseUrl,
    [string]$LoomJudge,
    [string]$SignalLoom = "D:\projects\signal-loom",
    [string]$Python,
    [ValidateSet("local", "stub")][string]$Provider = "local",
    [string]$StubReply,
    [ValidateSet("check", "build", "generate", "judge")][string]$From = "check",
    [switch]$SkipStubBuild
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Run = Join-Path $RunsRoot $RunId
$Order = @{ check = 0; build = 1; generate = 2; judge = 3 }
$Start = $Order[$From]

$Phase = "check"

function Stop-Run([string]$why) {
    if ($script:Phase -eq "check" -and $Start -eq 0 -and (Test-Path $Run)) {
        # Nothing has run yet: leave no folder, so the same run id can be used again.
        Remove-Item -Recurse -Force $Run
    }
    Add-Report "**Stopped:** $why"
    Write-Host "STOPPED: $why" -ForegroundColor Red
    exit 1
}

function Add-Report([string]$text) {
    if (Test-Path $Run) { Add-Content -Path (Join-Path $Run "report.md") -Value $text -Encoding utf8 }
}

function Invoke-Step([string]$name, [string[]]$arguments, [string]$exe = $script:Py) {
    $ErrorActionPreference = "Continue"   # a native tool's stderr is output, not a terminating error
    $log = Join-Path $Run "logs\$name.log"
    Write-Host "--- $name" -ForegroundColor Cyan
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $lines = & $exe @arguments 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $watch.Stop()
    $lines | Set-Content -Path $log -Encoding utf8
    $lines | ForEach-Object { Write-Host $_ }
    $secs = [math]::Round($watch.Elapsed.TotalSeconds, 1)
    return [pscustomobject]@{ Code = $code; Lines = @($lines); Seconds = $secs; Log = $log }
}

function Get-Sha([string]$path) { (Get-FileHash -Algorithm SHA256 $path).Hash.ToLower() }

function Split-Channel([string]$spec) {
    # FILE@CHANNEL -> file, "@CHANNEL" (or "")
    $at = $spec.LastIndexOf("@")
    if ($at -gt 1 -and $spec.Substring($at + 1) -match '^\d+$') {
        return @($spec.Substring(0, $at), $spec.Substring($at))
    }
    return @($spec, "")
}

# ---------------------------------------------------------------- 0. checks
if ($Python) { $Py = $Python }
elseif (Test-Path (Join-Path $Repo ".venv\Scripts\python.exe")) { $Py = Join-Path $Repo ".venv\Scripts\python.exe" }
else { $Py = "python" }

if ($Model) { $env:GRAP_LLM_MODEL = $Model }
if ($BaseUrl) { $env:GRAP_LLM_BASE_URL = $BaseUrl }

if ($Start -eq 0) {
    if (Test-Path (Join-Path $Run "report.md")) {
        Write-Host "Run $RunId already exists at $Run. Use a new -RunId, or -From to resume." -ForegroundColor Red
        exit 1
    }
    New-Item -ItemType Directory -Force (Join-Path $Run "in"), (Join-Path $Run "logs") | Out-Null
} elseif (-not (Test-Path (Join-Path $Run "report.md"))) {
    Write-Host "No run $RunId at $Run to resume." -ForegroundColor Red
    exit 1
}

Add-Report ""
Add-Report "## $RunId — $(Get-Date -Format 'yyyy-MM-dd HH:mm') (from $From)"
Add-Report ""

# The run folder must not be inside any git repository.
& git -C $Run rev-parse --show-toplevel 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { Stop-Run "the run folder $Run is inside a git repository. Use a -RunsRoot outside every repository." }

$grapCommit = (& git -C $Repo rev-parse --short HEAD).Trim()
foreach ($needed in "build_automotive_graph.py", "generate_test_spec.py", "judge_recording.py") {
    if (-not (Test-Path (Join-Path $Repo "scripts\$needed"))) { Stop-Run "scripts\$needed is missing. Pull grap-rust master." }
}
& $Py -c "import pygrap" 2>$null
if ($LASTEXITCODE -ne 0) { Stop-Run "pygrap does not import with $Py. Use the grap-rust venv (-Python)." }
$pyVersion = (& $Py --version 2>&1 | Out-String).Trim()

if ($Start -eq 0) {
    if ($ReqIf.Count -eq 0) { Stop-Run "give at least one -ReqIf." }
    if ($Dbc.Count -eq 0) { Stop-Run "give at least one -Dbc." }
    foreach ($f in @($ReqIf + $Arxml + ($Dbc | ForEach-Object { (Split-Channel $_)[0] }))) {
        if (-not (Test-Path $f -PathType Leaf)) { Stop-Run "input not found: $f" }
    }
    if ($Recording -and -not (Test-Path $Recording -PathType Leaf)) { Stop-Run "recording not found: $Recording" }
}

$modelLine = "stub (no model)"
if ($Provider -eq "local" -and $Start -le 2) {
    $base = $env:GRAP_LLM_BASE_URL
    if (-not $base) { $base = "http://127.0.0.1:11434/v1" }
    $base = $base.TrimEnd("/")
    $hostName = ([uri]$base).Host
    if ($hostName -notin @("localhost", "127.0.0.1", "::1", "[::1]")) {
        Stop-Run "the model server $hostName is not on this machine. Requirement text must not leave it."
    }
    $modelId = $env:GRAP_LLM_MODEL
    if (-not $modelId) { $modelId = "llama3.1" }
    try { $listed = @((Invoke-RestMethod "$base/models" -TimeoutSec 10).data | ForEach-Object { $_.id }) }
    catch { Stop-Run "no model server answers at $base/models. Start it (scripts\serve-bonsai.ps1) and retry." }
    if ($modelId -notin $listed) { Stop-Run "model '$modelId' is not served at $base. It lists: $($listed -join ', '). Pass -Model." }
    $modelLine = "$modelId at $base"
}

if ($Start -le 3 -and ($Recording -or $Start -eq 3)) {
    if (-not $LoomJudge) { $LoomJudge = Join-Path $SignalLoom "target\release\loom-judge.exe" }
    if (-not (Test-Path $LoomJudge)) {
        $manifest = Join-Path $SignalLoom "Cargo.toml"
        if (-not (Test-Path (Join-Path $SignalLoom "crates\loom-judge"))) { Stop-Run "no crates\loom-judge in $SignalLoom. Pull signal-loom main." }
        Write-Host "--- building loom-judge (once)" -ForegroundColor Cyan
        $ErrorActionPreference = "Continue"
        & cargo build --release -p loom-judge --manifest-path $manifest
        $ErrorActionPreference = "Stop"
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $LoomJudge)) { Stop-Run "loom-judge did not build." }
    }
}

# Inputs are copied into the run folder, so the run is self-contained.
if ($Start -eq 0) {
    $inputs = @()
    foreach ($f in $ReqIf + $Arxml) { Copy-Item $f (Join-Path $Run "in") }
    foreach ($d in $Dbc) { Copy-Item (Split-Channel $d)[0] (Join-Path $Run "in") }
    if ($Recording) { Copy-Item $Recording (Join-Path $Run "in") }
    $settings = [ordered]@{
        reqif = @($ReqIf | ForEach-Object { Split-Path -Leaf $_ })
        arxml = @($Arxml | ForEach-Object { Split-Path -Leaf $_ })
        dbc = @($Dbc | ForEach-Object { $p = Split-Channel $_; (Split-Path -Leaf $p[0]) + $p[1] })
        recording = $(if ($Recording) { Split-Path -Leaf $Recording } else { $null })
        release = $Release
        scope = @($ScopeIds | ForEach-Object { $_ -split "," } | Where-Object { $_ })
    }
    $settings | ConvertTo-Json | Set-Content (Join-Path $Run "run.json") -Encoding utf8
}
$settings = Get-Content (Join-Path $Run "run.json") -Raw | ConvertFrom-Json
$in = Join-Path $Run "in"

$Phase = "run"
Add-Report "- grap-rust $grapCommit, $pyVersion, model: $modelLine"
if ($Start -eq 0) {
    Add-Report "- inputs: $(@($settings.reqif).Count) ReqIF, $(@($settings.dbc).Count) DBC, $(@($settings.arxml).Count) ARXML, recording: $(if ($settings.recording) { 'yes' } else { 'no' }); scope: $(@($settings.scope).Count) requirements"
    foreach ($name in @($settings.reqif) + @($settings.arxml) + @($settings.dbc | ForEach-Object { (Split-Channel $_)[0] }) + @($settings.recording)) {
        if ($name) { Add-Content (Join-Path $Run "inputs.sha256") "$(Get-Sha (Join-Path $in $name))  $name" -Encoding utf8 }
    }
}

# ---------------------------------------------------------------- 3. build
function Build-Args([string]$out, [string]$provider) {
    $a = @((Join-Path $Repo "scripts\build_automotive_graph.py"))
    foreach ($n in @($settings.reqif)) { if ($n) { $a += @("--reqif", (Join-Path $in $n)) } }
    foreach ($n in @($settings.arxml)) { if ($n) { $a += @("--arxml", (Join-Path $in $n)) } }
    foreach ($n in @($settings.dbc)) { if ($n) { $a += @("--dbc", (Join-Path $in (Split-Channel $n)[0])) } }
    $a += @("--release", $settings.release, "--out", (Join-Path $Run $out), "--provider", $provider)
    return $a
}

function Report-Build($result, [string]$label) {
    Add-Report ""
    Add-Report "### build ($label): exit $($result.Code), $($result.Seconds) s"
    Add-Report '```'
    $result.Lines | Where-Object { $_ -match '^step \d' } | ForEach-Object { Add-Report $_ }
    Add-Report '```'
}

if ($Start -le 1) {
    if ($Provider -eq "local" -and -not $SkipStubBuild) {
        $r = Invoke-Step "build-stub" (Build-Args "graph-stub" "stub")
        Report-Build $r "stub"
        if ($r.Code -ne 0) { Stop-Run "the stub build failed. See logs\build-stub.log." }
    }
    $r = Invoke-Step "build" (Build-Args "graph-out" $Provider)
    Report-Build $r $Provider
    if ($r.Code -ne 0) { Stop-Run "the $Provider build failed. See logs\build.log." }
}

# ---------------------------------------------------------------- 4. generate
$specPath = Join-Path $Run "spec-out\specification.json"
if ($Start -le 2) {
    if ($Provider -eq "stub" -and -not $StubReply) { Stop-Run "the stub provider needs -StubReply to generate. Resume with -From generate." }
    $a = @((Join-Path $Repo "scripts\generate_test_spec.py"),
        "--graph", (Join-Path $Run "graph-out"), "--workspace", (Join-Path $Run "ws"),
        "--run-id", $RunId, "--scope-ids", (@($settings.scope) -join ","),
        "--provider", $Provider, "--out", (Join-Path $Run "spec-out"))
    if ($Provider -eq "stub") { $a += @("--stub-reply", $StubReply) }
    $r = Invoke-Step "generate" $a
    Add-Report ""
    Add-Report "### generate: exit $($r.Code), $($r.Seconds) s"
    # The summary lines are lower-case key=value counts. A traceback line starts with a
    # capital or a space, and a line with a quote could carry text: neither is kept.
    $status = $r.Lines |
        Where-Object { ($_ -cmatch '^[a-z_][a-z_0-9-]*[ =]' -or $_ -match "^$([regex]::Escape($RunId)) ") -and $_ -notmatch "[`"']" } |
        ForEach-Object { $_ -replace [regex]::Escape($Run), '<run>' }
    Add-Report '```'
    $status | ForEach-Object { Add-Report $_ }
    Add-Report '```'
    if ($r.Code -ne 0) {
        if (-not $status) { Add-Report "The script crashed. The last line of logs\generate.log names the error: read it before sharing it." }
        Stop-Run "generation did not produce a specification."
    }
}

# ---------------------------------------------------------------- 5. judge
if ($Start -le 3) {
    if (-not $settings.recording) {
        Add-Report ""
        Add-Report "No recording given, so section 5 was not run."
    } else {
        $a = @((Join-Path $Repo "scripts\judge_recording.py"),
            "--spec", $specPath, "--spec-id", $RunId,
            "--log", (Join-Path $in $settings.recording))
        foreach ($n in @($settings.dbc)) { $p = Split-Channel $n; $a += @("--dbc", ((Join-Path $in $p[0]) + $p[1])) }
        $a += @("--loom-judge", $LoomJudge, "--run-id", $RunId,
            "--workspace", (Join-Path $Run "ws"), "--out", (Join-Path $Run "judge-out"))
        $r = Invoke-Step "judge" $a
        Add-Report ""
        Add-Report "### judge: exit $($r.Code), $($r.Seconds) s"
        Add-Report '```'
        $shown = $r.Lines | Where-Object { $_ -notmatch '^\s*(Traceback|File ")' }
        $shown | ForEach-Object { Add-Report ($_ -replace [regex]::Escape($Run), '<run>') }
        Add-Report '```'
        if ($r.Code -ne 0) { Stop-Run "judging exited $($r.Code). Exit 2 is loom-judge refusing; 1 is a start, crash or import failure." }
    }
}

Add-Report ""
Add-Report "Done."
Write-Host ""
Write-Host "Report: $(Join-Path $Run 'report.md')" -ForegroundColor Green
