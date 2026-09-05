# LLM ontology extraction improvement

LLMs remain responsible for interpreting documents and proposing concepts,
characteristics and relations. The removed model call only performed mechanical
PG reference maintenance. Single-model extraction, graph context, bounded second
looks and optional two-model comparison are still present.

This pass concentrates on the extraction itself:

1. Give each chunk one consistent interpretation: valid empty results, malformed
   output and provider failures are different outcomes. Preserve earlier successful
   work until a refresh succeeds; let a second look correct its first proposal.
2. Retain source support with extraction proposals. Ask for quoted evidence,
   check quote locations mechanically, and preserve source text and model identity
   in the authoritative store. A matching quote is traceability, not proof that
   the model's interpretation is correct.
3. Exercise the implemented path with small labeled process and legal examples
   against an available local model, alongside failure/restart regressions.
   Report actual results and limits rather than using output quantity as quality.

These examples are development checks, not independent production acceptance
data. Automatic adoption remains policy-controlled.

## Implemented extraction boundary

`extract_chunk` now owns parsing, shape validation, quote validation and provider
metadata for both extraction paths. The prompt preserves named entities and
subject/object direction rather than forcing every entity into a process activity.
Exact quoted relation endpoints omitted from the concept list can be recovered
without another model call. Missing quotes remain visible as unquoted claims for
older providers; a supplied quote that is absent from the source rejects the claim.

Successful extraction replaces only that chunk's contributions. Failed refreshes
retain the previous graph and receipt, report stale/pending chunks, and leave the
successful content hash unchanged. Explicit empty arrays are successful results.
Malformed responses are bounded by quarantine; transport failures remain retryable.
`refresh=True` deliberately retries unchanged input after a model/prompt change.

SQLite schema 4 adds per-source receipts containing original chunk text, its hash,
model/prompt identity, accepted claims, quote offsets and stable concept IDs.
`get_extraction_evidence(path, source)` reads one receipt and its latest failure.
Older graphs migrate without model calls; refresh them explicitly to add receipts.
The existing Rust graph and entity index remain the resolver and retrieval engine.

Validation: **375 Python tests pass**, including failed-refresh recovery,
empty-result retraction, persisted receipts, malformed-output quarantine, second
look replacement, and literal endpoint recovery.

## Proposals survive export

Candidate YAML now retains `relations` and compact `extraction_evidence`, including
quotes, offsets, chunk hashes and model/prompt identity. This covers both the
default graph loop and the legacy stateless path. Full chunk text stays in SQLite
for durable graphs; exports carry quoted excerpts instead of duplicating whole
documents. Graph-loop relation endpoints also carry the exported outcome IDs.
Duplicate source labels fail before extraction so files cannot silently overwrite
each other's claims. Single-use extraction reports failed chunks and quality
counts, and its fragment count no longer accidentally reports second looks.

These relations remain proposals in the candidate artifact. Loading its ASPICE
outcome model does not automatically turn them into evidence or logical axioms.
The existing explicit relation projection tools remain available. In particular,
the `process_outcomes`/`required_characteristics` compatibility model is still an
ASPICE-oriented view, not a general class/instance ontology or OWL reasoner.

## Local model experiment

Six authored development cases cover a process requirement, negation, English and
Romanian legal relations, an empty page and a malicious instruction embedded in
source text. Five positive triples and selected forbidden claims are labeled.
These were used during development, so they are not a held-out acceptance set.

Both arms use the current extraction boundary and Rust-backed `WorkingGraph`;
only the system prompt changes (baseline commit `bd27c20`). The resolver uses a
deterministic hashing embedder with approximate merging disabled (`sim=1.1`).
This isolates naming/direction/endpoint retention; it does not measure semantic
entity resolution, long-document accuracy or graph-context quality. No second
looks are used. Ollama served both models locally, temperature 0, maximum 1,200
output tokens, no retries, after a warm-up call per model. Times are single runs,
with the old arm run first, and should not be treated as a performance benchmark.

| Model / prompt | Usable replies | Selected triples retained | Forbidden assertions detected | Quoted claims | Median seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Llama 3.1 8B Q4 / old | 4/6 | 0/5 | 0 | 0/20 | 0.91 |
| Llama 3.1 8B Q4 / new | 5/6 | 5/5 | 0 | 20/22 | 1.38 |
| Qwen 2.5 14B Q4 / old | 5/6 | 1/5 | 2 | 0/13 | 0.86 |
| Qwen 2.5 14B Q4 / new | 6/6 | 5/5 | 1 | 23/23 | 2.33 |

Counts include deterministic recovery of omitted, literally quoted endpoints.
Forbidden counts cover parsed proposals only; zero is not a precision score.
The Llama new-prompt empty-page response was prose and correctly failed parsing.
Qwen still extracted the fabricated Moon claim from the malicious instruction,
with an exact quote. Llama also emitted a Romanian negative relation type despite
the instruction to omit it. Quotes establish location, not entailment or truth;
an outcome quote also does not independently validate every characteristic.
The result supports retaining LLM extraction and improving its boundary, not
automatic trust or a claim that hallucination has been solved.

Raw replies and scores: [extraction-evaluation-2026-09.json](experiments/extraction-evaluation-2026-09.json).
Cases: [extraction-cases.json](experiments/extraction-cases.json).
Runner: [evaluate_extraction.py](experiments/evaluate_extraction.py).

From the repository root, reproduce scoring without a model:

```powershell
$env:PYTHONPATH = "$PWD/mcp-server"
.venv/Scripts/python.exe docs/experiments/evaluate_extraction.py `
  --rescore docs/experiments/extraction-evaluation-2026-09.json `
  --output .logs/extraction-rescored.json
```

For fresh model replies, omit `--rescore` and use `--models` with the installed
model names from the result file. `--base-url` defaults to the local Ollama `/v1`
endpoint. No paid service or additional model download was used in this pass.

## Using the improved path

`extract_ontology(path, model=...)` writes a proposed artifact with relation and
quote metadata. Inspect it through `get_proposed_ontology(version_hash)`.
For a durable corpus, `extend_extraction_graph(path, docs_path, refresh=True)`
deliberately re-extracts visible chunks with the configured model/prompt; normal
runs skip successfully processed unchanged chunks. Batch configurations accept
`extract.refresh`. Inspect one durable source with
`get_extraction_evidence(path, source="plan#0")`. Its successful receipt and latest
failed attempt are separate. Provider configuration still uses `GRAP_LLM_BASE_URL`
and `GRAP_LLM_MODEL`; `GRAP_LLM_STUB=1` selects the offline stub.

Existing graphs acquire receipts when refreshed; they cannot retroactively prove
old claims. Schema migration itself makes no model calls. Refreshing a corpus is
an explicit cost, not an automatic consequence of upgrading. SQLite saves still
compare O(N) records and write only changes; receipts add storage and comparison
work proportional to source text and claims. Larger corpus measurements remain
necessary before claiming an end-to-end throughput improvement.
