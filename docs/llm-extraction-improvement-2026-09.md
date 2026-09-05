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

Validation so far: **372 Python tests pass**, including failed-refresh recovery,
empty-result retraction, persisted receipts, malformed-output quarantine, second
look replacement, and literal endpoint recovery.
