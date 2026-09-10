# Gemma configuration and warm-up evaluation

Measured September 9–10, 2026, on the local RTX 5080 Laptop GPU with Ollama
0.33.3, `grap-legal-gemma4` (Q4_K_M), and BGE-M3.

## Decision

Keep the current production sampling and output order. Add explicit load-only
warm-up and record the client generation settings in extraction receipts.
No tested runtime setting eliminated repairs while preserving the measured
quality and improving speed. Fewer repairs alone is not a quality metric.

## Controlled screen

Four difficult Romanian-law cases used identical frozen graph-context prompts,
source statements and schemas. Each arm used the native Ollama API, the real
parser and the normal bounded repair path. Temperature zero, 16K context and
thinking disabled were the baseline. Explicit top-p was 0.95 and seed was 42.

| Setting | Total seconds including repairs | Repair calls | Correct targets after repairs | Unresolved rules |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 27.89 | 3 | 4/4 | 1 |
| Temperature 0.2 | 28.07 | 3 | 4/4 | 2 |
| Temperature 1.0 | 23.58 | 2 | 3/4 | 1 |
| Context 8K | 28.97 | 3 | 4/4 | 1 |
| Batch 128 | 29.60 | 3 | 4/4 | 1 |
| Thinking enabled | 87.79 | 1 | 4/4 | 1 |

Thinking improved first-pass target correctness from 3/4 to 4/4 but cost 3.15
times as much wall time. It is a candidate for a separately evaluated escalation
path, not a faster default. Its reported evaluation token counts exceeded the
requested 2,048 token budget on some calls: that request limit must not be
assumed to bound all thinking work on this runtime.

## Warm-up and prefix priming

A separate one-case run explicitly charged preparation time:

| Condition | Preparation seconds | Extraction seconds | Combined seconds |
| --- | ---: | ---: | ---: |
| Cold | 0 | 12.54 | 12.54 |
| Empty request preload | 5.42 | 7.01 | 12.42 |
| Matching system-prefix warm-up | 5.68 | 6.90 | 12.59 |

All three produced a correct target without repairs. The broader four-case
warm-up comparison retained three repairs and one unresolved rule in every
condition. Only the first request in each block is a cold/preloaded comparison;
the remaining requests share residency.

Preloading can hide loading behind document preparation or a user's next action.
It does not eliminate the work. Prefix preparation yielded no meaningful extra
benefit here. These are small measurements, not confidence intervals.

Warm-up does not train weights or transfer examples into independent prompts.
Prefix reuse only helps matching input tokens. See the
[primary-source runtime research](gemma-runtime-research-2026-09.md).

## Schema order: promising timing, insufficient quality evidence

Emitting legal units before entities reduced one repair in the four-case screen.
Across all 12 targets, however, both orders required three repair calls and left
one unresolved rule. Baseline took 56.48 seconds and units-first took 51.32.
Entity mentions changed from 50 to 44, including missing concepts and changed
granularity. For example, the units-first output omitted agricultural equipment
concepts in semantic-13 and tariff concepts in semantic-15. Some other changes
were potentially useful naming improvements. Counts alone cannot distinguish
these effects, so the candidate was not adopted. A reverse-order replication is
included in the evidence bundle. With seed 43 and reversed arm order,
units-first took 50.14 seconds (three repairs, 44 mentions) and baseline took
49.94 seconds (four repairs, 42 mentions). Both retained 12/12 targets and one
unresolved rule. The apparent speed advantage did not replicate. The changed
baseline output also demonstrates why a fixed greedy configuration must not
be mistaken for guaranteed repeatability; this experiment does not isolate
which cache, execution-path or seed difference caused it.

## Product changes

The legal-review launcher accepts `-LocalLegalProfile -WarmModels`. This issues
empty native load requests for Gemma and BGE-M3, then checks that both are
resident simultaneously. The default requested residency is ten minutes;
later requests, expiry or memory pressure can change it. Warm-up is optional
and does not write graph data or generate synthetic examples.

From the repository root it can also be run separately:

```powershell
$env:PYTHONPATH = 'C:\dev\grap-rust\mcp-server'
.venv/Scripts/python.exe -m grap_mcp.kee.warmup
```

Extraction receipts now record a whitelist of actual client generation fields
after request overrides, without prompts or credentials. This is not a claim
to know server defaults. The existing compatible API defaults override some
Modelfile settings: Grap sends temperature zero, and Ollama's compatible route
uses top-p 1.0 when omitted. Native options such as context and batch size must
not be assumed to work when hidden inside an arbitrary compatible `options`
object. The controlled trials used native options explicitly; production
transport and defaults remain unchanged.

## Evidence and limits

`experiments/gemma-runtime-evidence.json` contains per-case scores, raw outputs,
phase timings and final fragments from the screen, thinking trial, warm-up
trials and both full schema-order comparisons. Frozen prompts are preserved in
`experiments/gemma-runtime-prompts.json`. The experiment runner is
`experiments/evaluate_gemma_runtime.py`.

For example, reproduce the full order comparison without regenerating graph
context (choose a new output path on every run):

```powershell
$env:PYTHONPATH = 'C:\dev\grap-rust\mcp-server'
.venv/Scripts/python.exe docs/experiments/evaluate_gemma_runtime.py --prompts docs/experiments/gemma-runtime-prompts.json --all-cases --configs greedy,units_first --output .logs/gemma-runtime-repeat.json
```

The live warm-up smoke check returned `ready: true` with both model names
resident. Gemma was already loaded in this smoke check; its timing is not a
cold-load measurement.

Validation: 554 MCP-server, 26 GUI and 32 experiment tests passed (612 total). The PowerShell
launcher parsed without errors. The GUI process was not restarted as part of
this evaluation; the new warm-up option applies to a subsequent launch.

These are development fixtures, not a held-out corpus accuracy estimate.
Target scoring checks the selected rule's kind, subject and required qualifying
text; it does not establish complete ontology recall. Frozen context isolates
decoder behavior but does not measure each configuration's downstream effects
on a growing graph. Native and compatible APIs are not assumed equivalent.
Temperature zero does not guarantee identical results across cache and batch
execution paths. No global server flags, KV-cache quantization, weight changes
or fine-tuning were tested. Models are unloaded after evaluation.
