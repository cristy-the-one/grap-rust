#requires -Version 7.0
<#
    Shared Ollama helpers for bootstrap.ps1 / run-demo.ps1 (dot-sourced).
#>

function Resolve-Ollama {
    # PATH first; fall back to the default per-user install location, which a
    # fresh winget install will not have put on this session's PATH yet.
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $default) { return $default }
    return $null
}

function Test-OllamaUp {
    # -NoProxy: a system/corporate proxy must not intercept the localhost probe.
    try { Invoke-RestMethod "http://localhost:11434/api/version" -TimeoutSec 2 -NoProxy | Out-Null; return $true }
    catch { return $false }
}

function Start-OllamaServer([string]$OllamaExe) {
    if ($env:OLLAMA_HOST) {
        Write-Warning "OLLAMA_HOST is set to '$env:OLLAMA_HOST'; the demo expects the default http://localhost:11434. Unset it (`$env:OLLAMA_HOST = `$null) if Ollama stays unreachable."
    }
    Write-Host "Starting Ollama server (log: $env:TEMP\ollama-serve*.log)..." -ForegroundColor Yellow
    $outLog = Join-Path $env:TEMP "ollama-serve.log"
    $errLog = Join-Path $env:TEMP "ollama-serve.err.log"
    $proc = Start-Process $OllamaExe -ArgumentList "serve" -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    foreach ($i in 1..30) {
        if ($proc.HasExited) { break }
        Start-Sleep -Seconds 1
        if (Test-OllamaUp) { Write-Host " up." -ForegroundColor Green; return }
        Write-Host -NoNewline "."
    }
    Write-Host ""
    $tail = (Get-Content $errLog, $outLog -ErrorAction SilentlyContinue | Select-Object -Last 15) -join "`n"
    $state = if ($proc.HasExited) { "the serve process exited (code $($proc.ExitCode))" } else { "the serve process is still running but not answering" }
    throw ("Ollama server did not become reachable on http://localhost:11434 - $state.`n" +
           "--- last lines of ollama serve output ---`n$tail`n" +
           "Try running  `"$OllamaExe`" serve  in a visible console to see the full error.")
}
