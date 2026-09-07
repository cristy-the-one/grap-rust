# Resumable large-act extraction

**Extraction update:** [legal-rules-v7](legal-semantics-2026-09.md) removes numbered
tokens from model input and uses uniquely grounded subject phrases. The resumption
contract below is unchanged; its 12,000-byte request bound now applies to serialized
source statements. Older v6 work is not mixed with v7 checkpoints.

`extract_legal_claims` now accepts `max_batches`. With that option, every validated
labeling batch is committed to the existing SQLite ledger. A later call in a new
process reuses completed batches. The authoritative document receipt and its claims
are published atomically only after every planned batch is complete.

This extends the [source-statement extractor](legal-rules-2026-09.md). Statements
retain full-document offsets, local provision locators and surrounding source
context. Batches can contain several statements/provisions; they are not arbitrary
character slices. An individual oversized statement is rejected with its location,
rather than silently cut into independent legal assertions.

## Interface and state

```python
from grap_mcp.application import GrapApplication

app = GrapApplication(r"C:\dev\grap-rust\.logs\my-law-workspace", role="operator")
result = app.extract_legal_claims(
    r"C:\dev\grap-rust\real-data-law-ro\16918.json",
    max_chars=100000, max_batches=3, model="grap-legal-gemma4")
# Repeat the same call in a later process to continue.
sources = app.get_legal_sources()["sources"]
if sources:
    history = app.get_legal_sources(source_id=sources[0]["source_id"])
    print(history["work"])  # completed_batches, total_batches, source hash, work ID
```

- `max_files` remains a document-attempt bound. `max_batches` bounds new labeling
  calls across the entire invocation, not separately for each file. Transport retries
  are still controlled by provider configuration. Zero `max_batches` scans only.
- `max_chars` remains an explicit whole-source safety bound; its default is still
  8,000. Raise it to process larger acts. Each batch also has bounds of 12 units,
  1,200 whitespace tokens and 12,000 UTF-8 bytes of numbered source text.
- `partial` means successful work has been saved but the document is incomplete.
  `pending` can indicate an active or interrupted attempt. A model/validation error
  is `failed`; successful earlier checkpoints survive that failure.
- Incomplete sources are excluded from current claim reads, scoped definitions and
  new Rust snapshots. An earlier accepted revision stays available explicitly in
  history and through stale reads. A completion receipt records the source hash,
  work ID and complete batch count; all offsets still address the full original body.
- Normal folder scans skip unchanged failures so they cannot monopolize later runs.
  Target a failed file explicitly to retry its missing batch with saved work intact.
- `refresh=True` starts a new extraction run. Use it on the first call only, then
  resume with `refresh=False`. Ready sources otherwise keep the existing reuse
  behavior, even if a different model argument is supplied.

Partial work is reused only when source bytes, extractor version, generation
configuration and the planned unit ranges match. A model/configuration change starts
fresh work. Provider configuration is fingerprinted; credentials are not stored in
the work metadata. Model tags are configuration identities, not immutable weight
digests: refresh explicitly if weights behind a tag change.

Two additive tables hold work manifests and validated batch receipts, with one work
pointer on each source. Attempt ownership guards both checkpoints and publication.
No SQLite transaction spans a model call. Changed source bytes or a newer competing
attempt prevent late output from being committed. There is no scheduler, queue
service or second graph store. Historical work/checkpoints are retained; automatic
pruning is not implemented.

## Context-window failure found and fixed

The first 41,244-character trial saved six batches, then failed on batch seven.
Retrying generated only the failed batch and left the six saved batches intact.
The failure was a serving context limit, not a need for a longer JSON answer:
the diagnostic response used 3,887 prompt tokens plus 209 output tokens, exactly
4,096 total. Raising `max_tokens` to 4,096 did not increase that context capacity.
The incomplete answer was rejected and no document claims were published.

Ollama documents that its OpenAI-compatible interface cannot directly set context
size and recommends a derived model with `PARAMETER num_ctx`.
[Official documentation](https://docs.ollama.com/api/openai-compatibility)

The repository now contains [legal-gemma4.Modelfile](../scripts/legal-gemma4.Modelfile):

```powershell
ollama create grap-legal-gemma4 -f scripts/legal-gemma4.Modelfile
```

This profile reuses installed `gemma4:latest` weights and sets a 16,384-token context.
It leaves the original model unchanged and downloaded no additional weights in this
run. The server confirmed the profile's active context size; reported GPU allocation
was 3,384,698,469 bytes on this machine. The profile controls context capacity;
application generation still uses temperature zero in these experiments.

The request planner now also bounds rendered byte size: word counts alone miss long
unbroken text and the extra size of token numbering. This is a transport bound, not
a model-specific tokenizer. Compatible backends still need a context window large
enough for the source, instructions, formatting and reply. All explicit incomplete
responses remain failures rather than accepted partial output.

Diagnostic evidence: [context check](experiments/real-law-resume-context-check.json),
[first failure](experiments/real-law-resume-16918-pass3.json),
[retry preserving six batches](experiments/real-law-resume-16918-pass4.json).

## Live validation across separate processes

Source hashes were frozen in [the case manifest](experiments/real-law-resume-cases.json).
Each pass ran as a separate Python process against the same SQLite workspace.
The following final trials used `grap-legal-gemma4`, temperature zero, 2,048 output
tokens and zero HTTP retries. No source text was truncated.

| Source | Characters | Newly generated batches by pass | Final proposals | Total extraction seconds |
|---|---:|---|---:|---:|
| 18839 — Law 130/1999 | 15,805 | 2 + 3 | 46 | 16.564 |
| 16918 — Decision 58/1999 and its norms | 41,244 | 3 + 3 + 2 | 44 | 36.428 |

The larger act reused three saved batches in pass two and six in pass three.
Changing from the original model to the GRAP profile correctly created a fresh
work ID. An additional completed-source call took 0.002 seconds with zero model
calls. The smaller act also exercised a partial refresh of a previously accepted
document: its old receipt remained in history until the new run completed.

All claim, context, subject and provision evidence offsets were verified against
the complete source body. Current claim count and exported source count stayed zero
on every incomplete pass. Completed Rust exports reopened with 47 nodes/46 edges
and 45 nodes/44 edges respectively, including their complete source nodes.
These are preservation, resumption and integration checks, **not legal accuracy
scores**; model classifications and subjects remain unreviewed proposals.

Final artifacts:

- Source 18839: [pass 1](experiments/real-law-resume-18839-16k-pass1.json),
  [pass 2](experiments/real-law-resume-18839-16k-pass2.json).
- Source 16918: [pass 1](experiments/real-law-resume-16918-16k-pass1.json),
  [pass 2](experiments/real-law-resume-16918-16k-pass2.json),
  [pass 3](experiments/real-law-resume-16918-16k-pass3.json),
  [unchanged call](experiments/real-law-resume-16918-16k-unchanged.json).

Reproduce one pass from the repository root; use a new report path on each pass:

```powershell
$env:PYTHONPATH='C:\dev\grap-rust\mcp-server'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe docs/experiments/exercise_legal_resume_live.py --source real-data-law-ro/16918.json --workspace .logs/new-resume-trial --model grap-legal-gemma4 --max-batches 3 --output .logs/new-resume-pass1.json
```

The full Python/application/GUI/experiment suite passes **481 tests**, including 16
new resumption tests. Coverage includes process reopening, interruption, bad model
output, stale source/configuration, partial refresh, folder fairness, global budgets,
competing workers, request-size preflight and exact source offsets. Existing native
Rust export and search tests also pass. Rust source was not changed in this step.

The next semantic priority remains an independently annotated evaluation set for
actors, rule kinds, conditions and scope. This work enables larger documents to be
processed reliably; it does not resolve cross-provision references, amendment targets
or legal validity, and it does not claim full-corpus LLM coverage.
