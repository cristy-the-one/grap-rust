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

Validation: **378 Python tests and 18 GUI tests pass**, including failed-refresh recovery,
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

Two-model extraction also retains each complete source proposal as an immutable
artifact. The agreement artifact and response link these in `source_proposals`,
including each arm's quality and failed chunks. Intersecting outcomes no longer
destroys the only record of relations and supporting quotes. Agreement measures
consistency between model responses; it does not establish factual correctness.

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
| Llama 3.1 8B Q4 / new (v4) | 5/6 | 5/5 | 0 | 23/24 | 1.46 |
| Qwen 2.5 14B Q4 / old | 5/6 | 1/5 | 2 | 0/13 | 0.86 |
| Qwen 2.5 14B Q4 / new (v4) | 5/6 | 5/5 | 1 | 16/18 | 1.99 |

Counts include deterministic recovery of omitted, literally quoted endpoints.
Forbidden counts cover parsed proposals only; zero is not a precision score.
The Llama new-prompt empty-page response was prose; Qwen returned `{}` without
the required arrays. Both correctly failed the extraction boundary. The earlier
v2 prompt produced a valid empty Qwen response; the longer requirement prompt
regressed that case. Qwen still extracted the fabricated Moon entity from the
malicious instruction, with an exact quote. Llama supplied an invented Moon quote
that the normalizer rejected. Quotes establish location, not entailment or truth;
an outcome quote also does not independently validate every characteristic.
The result supports retaining LLM extraction and improving its boundary, not
automatic trust or a claim that hallucination has been solved.

Final v4 replies and scores: [extraction-v4-2026-09.json](experiments/extraction-v4-2026-09.json).
Initial v2 run: [extraction-evaluation-2026-09.json](experiments/extraction-evaluation-2026-09.json).
Cases: [extraction-cases.json](experiments/extraction-cases.json).
Runner: [evaluate_extraction.py](experiments/evaluate_extraction.py).

From the repository root, reproduce scoring without a model:

```powershell
$env:PYTHONPATH = "$PWD/mcp-server"
.venv/Scripts/python.exe docs/experiments/evaluate_extraction.py `
  --rescore docs/experiments/extraction-v4-2026-09.json `
  --output .logs/extraction-rescored.json
```

For fresh model replies, omit `--rescore` and use `--models` with the installed
model names from the result file. `--base-url` defaults to the local Ollama `/v1`
endpoint. No paid service or additional model download was used in this pass.

## Requirements are not descriptions

The old model-facing `characteristics` field fed directly into the ontology's
`required_characteristics`. A description such as "Plan records coverage" could
therefore become a mandatory assessment criterion. The current prompt (v4) names
the field `requirements` and gives a contrast with "Plan shall record coverage".
One adapter in `normalize_fragment` maps it to existing graph storage; explicit
requirements, including an empty list, take precedence over the legacy field.
Existing callers using `characteristics` remain supported.

No new graph type or assessment flag is necessary: the Rust-backed pipeline can
already keep named entities with no criteria, and the reasoner skips those
entities during gap checking. A regression spans extraction, YAML loading and
gap detection to verify this. The validator's contrary "always a gap" warning
was wrong and is corrected.

Four additional English/Romanian cases compare required and descriptive sentences:

| Model | Actual requirements retained (v2 / v3 / v4) | Descriptive concepts incorrectly given requirements (v2 / v3 / v4) |
| --- | --- | --- |
| Llama 3.1 8B Q4 | 2/2 / 2/2 / 2/2 | 2 / 1 / 2 |
| Qwen 2.5 14B Q4 | 2/2 / 2/2 / 2/2 | 2 / 2 / 1 |

v3 only strengthened the wording; v4 renamed the field and added examples. The
results do **not** establish reliable requirement classification. The explicit
field clarifies the interface with a small compatibility adapter, but a longer
prompt is not a semantic validator. Further work should evaluate domain-labeled
requirements and model quality, not add a series of language-specific keyword
filters that happen to pass these four cases. Automatic adoption remains an
optional policy; its existing integrity/churn checks do not validate entailment.

[Cases](experiments/extraction-requirement-cases.json),
[v3 replies](experiments/extraction-requirements-2026-09.json),
[v3 prompt](experiments/extraction-requirements-v3-prompt.txt), and
[v4 replies](experiments/extraction-requirements-v4-2026-09.json) retain the failed
approach as well as the final one. Use `--cases` with this case file and
`--baseline ba46e2f` for a v2-to-current comparison.

Incomplete extraction is now a deterministic reason to block **automatic**
adoption across single-use, incremental batch and two-model paths. Its candidate
stays available for review, while the active ontology remains unchanged. This
checks completeness, not semantic truth, and avoids a partial model response
silently replacing a complete earlier ontology.

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
