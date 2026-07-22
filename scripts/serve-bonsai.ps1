<#
.SYNOPSIS
  Start Ternary-Bonsai-27B on llama-server with the configuration validated for
  the KEE dark-factory (law-ro 200-doc run).

.DESCRIPTION
  Every flag here was chosen from a measured failure. Do not "simplify" without
  re-measuring:

  --device <NVIDIA>     llama.cpp binds Vulkan device 0 by default, which on this
                        laptop is the Intel iGPU, not the RTX 5080. The model
                        loads and answers, just slowly and on the wrong GPU. The
                        script auto-detects the NVIDIA device and pins it.

  -c 65536 with -np 4   llama-server splits total context across slots, so this
                        is 16384 tokens/slot. At -c 32768 (8192/slot) extraction
                        prompts over ~8k tokens were rejected with HTTP 400 —
                        26 chunks were lost that way in one run, and 4xx is
                        (correctly) never retried, so they were permanent losses.

  --cache-ram 0         Prompt cache defaults to 8 GiB and context checkpoints to
  --ctx-checkpoints 0   32 per slot. Every extraction prompt is unique (no shared
                        prefix), so the cache can never hit — it only costs
                        ~150 MiB copies per slot switch (2310 observed in one
                        run). Disabling both removes pure overhead.

  --jinja               Native OpenAI tool_calls, required by the augmented-audit
                        demo (demo/augmented-audit/agent_demo.py).

  --alias               KEE addresses the model by this name (GRAP_LLM_MODEL).

  Measured throughput on the RTX 5080 (16 GB) with real extraction prompts:
  ~5.8 tok/s per slot, ~23.5 tok/s aggregate at 4 slots (~17 s/chunk). More
  slots beat fewer: 2 slots gave only ~18 tok/s aggregate.

  VERIFYING GPU RESIDENCY: do NOT trust
      nvidia-smi --query-gpu=memory.used
  For a Vulkan (non-CUDA) process under WDDM it intermittently reports 0 MiB
  even while the model is resident — which looks exactly like a silent fallback
  to the iGPU. Use the process table instead:
      nvidia-smi            # llama-server.exe should be listed under Processes
  and sanity-check tokens/s; CPU-only would be several times slower.

.EXAMPLE
  .\scripts\serve-bonsai.ps1
  .\scripts\serve-bonsai.ps1 -Port 8081 -Slots 2 -Context 32768
#>
[CmdletBinding()]
param(
    [string]$BonsaiDir = "D:\projects\Bonsai-demo",
    [string]$Model,
    [int]$Port = 8080,
    [int]$Context = 65536,          # total; divided across -np slots
    [int]$Slots = 4,
    [string]$Alias = "ternary-bonsai-27b",
    [int]$ReadyTimeoutSec = 600,
    [switch]$Foreground,
    # KEE also needs an embedding model. Ollama unloads it after keep_alive
    # (default 5 min) of inactivity; during extraction the embedder is only
    # touched every few minutes, so it sits ~2 minutes from eviction. A reload
    # is cheap on its own, but if it happens while Bonsai's KV cache is at peak
    # Ollama may decide it no longer fits and silently place it on CPU, making
    # every embedding call an order of magnitude slower. Pinning it costs
    # ~664 MiB and removes that failure mode. Set -PinEmbedder:$false to skip.
    [string]$EmbedModel = "bge-m3",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [bool]$PinEmbedder = $true
)

$ErrorActionPreference = "Stop"

$bin = Join-Path $BonsaiDir "bin\vulkan\llama-server.exe"
if (-not (Test-Path $bin)) { throw "llama-server not found at $bin (set -BonsaiDir)" }

if (-not $Model) {
    $Model = Join-Path $BonsaiDir "models\ternary-gguf\27B\Ternary-Bonsai-27B-Q2_0.gguf"
}
if (-not (Test-Path $Model)) { throw "model not found: $Model" }

# --- pick the discrete GPU, never the iGPU -----------------------------------
$devices = & $bin --list-devices 2>&1 | Out-String
$device = $null
foreach ($line in ($devices -split "`n")) {
    if ($line -match '^\s*(Vulkan\d+):\s*(.+?)\s*\(') {
        $id, $name = $Matches[1], $Matches[2]
        if ($name -match 'NVIDIA|Radeon RX|Arc A') { $device = $id; break }
    }
}
if (-not $device) {
    Write-Warning "No discrete GPU found in --list-devices; letting llama.cpp choose (may pick an iGPU)."
} else {
    Write-Host "[serve-bonsai] GPU: $device" -ForegroundColor Green
}

$slotCtx = [math]::Floor($Context / [math]::Max($Slots, 1))
Write-Host "[serve-bonsai] ctx $Context across $Slots slot(s) = $slotCtx tokens/slot" -ForegroundColor Green
if ($slotCtx -lt 16384) {
    Write-Warning "slot context $slotCtx < 16384; large extraction prompts may be rejected with HTTP 400."
}

$argList = @("-m", $Model, "-ngl", "99", "-fa", "on",
             "-c", "$Context", "-np", "$Slots",
             "--cache-ram", "0", "--ctx-checkpoints", "0",
             "--host", "127.0.0.1", "--port", "$Port",
             "--jinja", "--alias", $Alias,
             "--temp", "0.7", "--top-p", "0.95", "--top-k", "20", "--min-p", "0")
if ($device) { $argList = @("--device", $device) + $argList }

$logDir = Join-Path $PSScriptRoot "..\.logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $logDir "bonsai-$stamp.out.log"
$err = Join-Path $logDir "bonsai-$stamp.err.log"

if ($Foreground) {
    & $bin @argList
    exit $LASTEXITCODE
}

# Stop any server already holding the port (ignore "not running").
try { Get-Process llama-server -ErrorAction Stop | Stop-Process -Force } catch { }
Start-Sleep -Seconds 3

Start-Process -FilePath $bin -ArgumentList $argList `
    -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden
Write-Host "[serve-bonsai] launched; logs: $err" -ForegroundColor Green

$deadline = (Get-Date).AddSeconds($ReadyTimeoutSec)
while ((Get-Date) -lt $deadline) {
    try {
        $m = Invoke-RestMethod "http://127.0.0.1:$Port/v1/models" -TimeoutSec 3
        Write-Host "[serve-bonsai] READY - model '$($m.data.id)' on port $Port" -ForegroundColor Green
        Write-Host ""
        Write-Host "Point KEE at it:" -ForegroundColor Cyan
        Write-Host "  `$env:GRAP_LLM_BASE_URL  = 'http://127.0.0.1:$Port/v1'"
        Write-Host "  `$env:GRAP_LLM_MODEL     = '$Alias'"
        Write-Host "  `$env:GRAP_LLM_EXTRA_BODY= '{\"chat_template_kwargs\": {\"enable_thinking\": false}}'"
        Write-Host "  `$env:GRAP_LLM_TIMEOUT   = '300'   # ~240s/chunk observed"
        Write-Host "  `$env:GRAP_LLM_RETRIES   = '2'     # bounds a stuck chunk"
        Write-Host "  `$env:GRAP_LLM_MAX_TOKENS= '2048'  # caps runaway repetition"

        if ($PinEmbedder) {
            # keep_alive=-1 pins the model for the life of the Ollama process,
            # and needs no service restart (OLLAMA_KEEP_ALIVE is only read at
            # startup). Set OLLAMA_KEEP_ALIVE=-1 in the service environment if
            # you want it to survive an Ollama restart too.
            try {
                $body = @{ model = $EmbedModel; input = "pin"; keep_alive = -1 } | ConvertTo-Json
                Invoke-RestMethod "$OllamaUrl/api/embed" -Method Post -Body $body `
                    -ContentType "application/json" -TimeoutSec 120 | Out-Null
                Write-Host ""
                Write-Host "[serve-bonsai] pinned '$EmbedModel' in VRAM (keep_alive=-1)" -ForegroundColor Green
            } catch {
                Write-Warning "could not pin '$EmbedModel' on $OllamaUrl : $($_.Exception.Message)"
            }
        }
        exit 0
    } catch { Start-Sleep -Seconds 4 }
}
Write-Error "server did not become ready within $ReadyTimeoutSec s; see $err"
exit 1
