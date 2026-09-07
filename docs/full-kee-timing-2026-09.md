# Full KEE remains available: measured scope, runtime and limits

The graph-augmented KEE path still runs: source chunks → LLM concepts/relations →
multilingual embeddings and entity reconciliation → Rust graph projection →
candidate ontology export. The application exposes `extract_ontology`,
`extend_extraction_graph`, `open_extraction_graph` and `export_ontology`.
The existing console's **Persistent ontology** area exposes this workflow.

This path is distinct from `legal-rules-v8` and the new legal review workbench.
The latter assesses complete-source legal claims and omissions. It does not yet
grade the generic KEE's concept identities, inferred merges or relation vocabulary.

## Corpus dry run

The current folder has 2,092 JSON files. Generic KEE also accepts Markdown/text,
so pointing it at the folder includes `README.md` and `requirements.txt`. Their
24 small chunks were excluded from this probe using the JSON source IDs.

| Chunk size, characters | Extractable JSON-source chunks | Tabular chunks skipped by reader |
|---:|---:|---:|
| 800 (current default) | 29,572 | 2,057 |
| 1,600 | 16,359 | 1,044 |
| 2,400 | 12,027 | 694 |

Tabular skipping is existing behavior, not evidence that tables lack legal
meaning. A truly encompassing knowledge system needs to account for these too.
Larger chunks change context and extraction behavior; call-count reduction alone
does not establish equal accuracy or proportional speedup.

## Live full-path probe, 2026-09-07

[Benchmark driver](experiments/benchmark_full_kee.py) sampled 24 default-size chunks
without replacement using seed `20260907`. It ran graph-augmented KEE with:

- Installed `grap-legal-gemma4`, temperature zero, structured generation, reasoning
  disabled, 2,048 output tokens, no transport retries, 90-second timeout.
- Installed `bge-m3` through local Ollama for multilingual embeddings.
- Serial calls, default 0.86 merge threshold and 0.2 maximum relook ratio.

[Saved evidence](experiments/full-kee-timing-2026-09.json) includes the selected
source chunks, raw requests/replies, candidate ontology and pipeline quality counts.

Observed wall time was **98.469 seconds**. The 24 LLM calls accounted for 79.116
seconds, with median 2.499 seconds; the first call took 13.993 seconds. The probe
included cold loading and graph/embedding work. No relooks were triggered.

The candidate contained 74 concepts and 21 relations; eight relations remained
parked. Three source chunks were filtered rather than producing retained fragments.
No provider or JSON-schema failures occurred. These are execution/grounding counts,
not semantic accuracy scores.

Linear extrapolation from the entire probe is **33.7 hours** for 29,572 chunks.
For planning, allow roughly **24–48 hours** for a first serial pass at these
settings. This is a rough planning range, not a confidence interval or completion
guarantee. Cold loading overstates steady-state cost, while a much larger graph,
relooks, failed chunks and GPU contention can increase it. The other chunk sizes
have not been timed end-to-end. A representative larger pilot should precede an
unattended corpus run. Incremental subsequent runs can reuse unchanged extractions.

## Is ontology extraction meaningful?

Yes, as discovery of candidate entities and relationships with traceable evidence.
The probe surfaced institutions, legal acts and relations involving bank assets,
public bodies and international agreements. It also produced generic concepts
such as `Sala`, `lei` and `SOCIETATEA COMERCIALA`, long clause-like concept names,
and inconsistent relation vocabulary. No legal correctness is claimed for these.

An extracted inventory is not yet a coherent legal schema with validated classes,
typed relations, jurisdiction, temporal scope and identity rules. Useful next work
is to review the discovered concepts/relations and merges, group relation variants,
and distinguish act identity, quoted changes and actual applicability. Source
quotes must remain available throughout that work. Automatic promotion would hide
exactly the errors this review is meant to reveal.

## Reproduce

```powershell
$env:PYTHONPATH='C:\dev\grap-rust\mcp-server'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe docs/experiments/benchmark_full_kee.py --chunks 24 --output .logs/new-full-kee-probe.json
```

For a full KEE console session, configure an installed model and multilingual
embedder explicitly before starting the server. The generic defaults may refer to
models absent from this machine (`llama3.1` and `nomic-embed-text`). For this setup:

```powershell
$env:GRAP_LLM_MODEL='grap-legal-gemma4'
$env:GRAP_EMBED_BACKEND='ollama'
$env:GRAP_EMBED_MODEL='bge-m3'
$env:GRAP_KEE_STRUCTURED_OUTPUT='1'
$env:GRAP_LLM_EXTRA_BODY='{"reasoning_effort":"none"}'
.\aspice-gui\scripts\run-legal-review.ps1
```

Use a dedicated JSON-only corpus scope and a fresh durable KEE workdir for an
initial pilot. No full-corpus inference job was started in this activity.
