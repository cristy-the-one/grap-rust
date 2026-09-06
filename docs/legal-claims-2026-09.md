# Qualified legal claims: implementation and real-source evaluation

**Historical baseline:** the literal-field extractor described below has been
superseded by [source statements and scoped definitions](legal-rules-2026-09.md).
The stored experiments and replay script retain this earlier contract for comparison.
Current ordinary rules use `statement`, with empty predicate/object fields; the
current API also exposes `resolve_legal_term(term, source_id)`.

The Romanian-law path now stores qualified legal proposals independently of ASPICE
assessment requirements. A right is not converted into a beneficiary's obligation;
an amendment is distinct from a citation. Claims can be inspected with their source
receipts and exported as qualified nodes into the existing Rust graph engine.

This is a working extraction/storage/retrieval foundation. It is **not yet a reliable
unattended legal ontology extractor**, and it does not determine current law. The
local-model experiments below contain omissions, misclassification and rejected
output, including replies that pass mechanical checks but remain semantically weak.

## Interface and usage

The application and the full MCP tier expose four operations:

| Operation | Result |
|---|---|
| `extract_legal_claims(path, max_files=3, max_chars=8000, refresh=False, model=None)` | Scan legal JSON files and attempt a bounded number of whole-document extractions. |
| `get_legal_claims(kind=None, source_id=None, revision_id=None, include_stale=False, limit=20, offset=0)` | Page latest stored proposals; an explicit revision ID returns the full historical receipt. |
| `get_legal_sources(source_id=None, limit=20, offset=0)` | Inspect observed files, errors and deferrals, or one source's extraction history. |
| `export_legal_claim_graph()` | Create a new Rust graph snapshot and a self-contained JSON evidence manifest. |

Input currently uses the supplied corpus's JSON shape: `id`, `titlu`,
`text_complet`, with optional issuer, URL, effective-date and in-force metadata.
This path does not yet ingest arbitrary PDF/Word folders.

From the repository root, with `PYTHONPATH=mcp-server` and the project's Python:

```python
from grap_mcp.application import GrapApplication

app = GrapApplication(r"C:\dev\grap-rust\.logs\my-legal-workspace", role="operator")

# A cheap coverage scan: zero document extraction attempts.
scan = app.extract_legal_claims(r"C:\dev\grap-rust\real-data-law-ro", max_files=0)

# Explicitly selected local model; no global model setting is changed.
batch = app.extract_legal_claims(
    r"C:\dev\grap-rust\real-data-law-ro",
    max_files=3, max_chars=8000, model="qwen3.5:9b-q4_K_M")

rights = app.get_legal_claims(kind="right")
if rights["claims"]:
    receipt = app.get_legal_claims(revision_id=rights["claims"][0]["revision_id"])

snapshot = app.export_legal_claim_graph()
app.load_knowledge_graph(snapshot["graph_path"])
```

The model named in this example is a tested option, not a claim of adequate legal
accuracy. The experiments used 4,096 output tokens, temperature zero and
`reasoning_effort=none`. Normal provider configuration still controls output limits,
timeouts and transport retries; `max_files` counts document attempts, not HTTP
retries. Set `GRAP_LLM_MAX_TOKENS=4096` and `GRAP_LLM_RETRIES=0` when reproducing those
bounds. `max_files=0` here means **scan only**, unlike older graph-building APIs
that use zero for an unlimited file count.

Structured decoding is scoped to this extraction call when the provider supports
it. The generic document-claims-v4 assessment extractor remains unchanged.

## What is preserved

Each proposal has a kind (`fact`, `definition`, `right`, `obligation`, `prohibition`,
`permission`, `amendment`), subject, predicate, object, a provision locator, scope,
conditions, exceptions, dates and evidence. Empty fields mean unspecified, not that
the rule is unconditional. All claims retain `review_status=proposed` and
`scope_resolution=unresolved`; no automatic legal-validity or global-identity
conclusion follows from storage.

The runtime requires literal evidence and literal populated semantic fields.
Evidence offsets refer to the retained original body, without rewriting Romanian
diacritics or silently normalizing wording. A unique provision locator can be
anchored separately from the sentence supporting the claim. It remains a source
locator: it is not automatically Article 1 of the amending act when the passage
contains quoted replacement text.

These checks prove occurrence, not entailment, completeness or correct role
assignment. A wrong subject chosen from the same passage can still pass. One
invalid item rejects the complete reply; the system never quietly prunes a failed
item and reports the source as completely processed. A valid explicit empty array
is stored as an empty result, which can still be a model omission.

`legal-claims.sqlite` is the authoritative store. Resolved file paths identify
sources; separate files with the same downloaded ID remain separate. Original
file hashes distinguish source revisions. `revision_id` identifies an extraction
receipt, including the model output and prompt version: two extractions of the
same source bytes may have different receipt IDs. Receipts retain the source body,
metadata, raw model reply and normalized claims. They are not consolidated legal
act versions, and source metadata such as `in_vigoare=true` is retained as supplied.

## Incremental and failure behavior

- Unchanged successful files cost no new model call. Unchanged failed files are
  also skipped, so early failures cannot monopolize every resumed batch. Retry one
  file, or a deliberately selected folder, with `refresh=True`.
- Empty bodies, placeholders, oversized sources and the remainder beyond the
  attempt budget are explicitly deferred. Whole documents are never truncated to
  fit. A provision-aware strategy is still required for larger texts.
- Successful replacement changes the source's active receipt atomically. Previous
  receipts remain inspectable. A successful empty extraction removes its old
  claims from the latest view while preserving their history.
- Failure, incomplete provider output, interruption or an observed source change
  during generation preserves the old receipt and excludes it from default reads.
  `include_stale=True` exposes retained claims with their status and reason.
- Attempt IDs prevent late results or errors from overwriting a newer extraction,
  even if both used identical source bytes. The source hash is checked again after
  generation. This is an observation-time check, not a filesystem lock or a promise
  that a source cannot change after the check.
- A folder rescan marks previously observed paths absent from that folder as
  missing. History is retained; scans do not infer absence outside their scope.
- Cheap observations are committed in batches of 256. Pending state is durable
  before each model call, and its result is committed immediately. There is no
  database transaction held during generation. Use one ingestion writer per
  workspace; this is not a distributed scheduling system.

## Rust retrieval and measured bookkeeping cost

An export builds one node per qualified claim and one node per source receipt,
connected by `claimed_in`. It does not create bare subject-to-object edges that
lose conditions, or use embedding similarity to merge legal identities. The native
index is built only on explicit export, not at each ingestion checkpoint. An export
is a dated snapshot; subsequent ingestion requires another export to update it.

Replayed saved Qwen v3 replies for **two complete original documents**, 17994 and
16955, through the application interface. This replay performs no new generation
and does not pass excerpt output off as a complete-document extraction. Reopening
the application preserved three claims (two amendments and one right). A scan of
all **2,092** original files retained those two processed sources and explicitly
deferred **2,090** others, with zero extraction attempts.

The initial coverage scan took **13.023 seconds** with one SQLite commit per
observation. Batching reduced a fresh-store scan with the same coverage to **0.589
seconds**; the final run including the race fixes took **0.676 seconds**. These are
local measurements, not throughput guarantees or improvements to LLM inference.

The exported Rust graph reopened with five nodes and three edges. Native full-text
search for `indemnizație` returned two hits. The JSON manifest retains the complete
qualified claims and original source bodies, so the snapshot can be inspected
without its original SQLite database. No Rust source change was needed: the engine
already supports this graph shape, text indexing, traversal and persistence.

## Real-model evaluation

Retained the twelve previously frozen passages and added semantic review criteria
in `experiments/real-law-legal-claim-checks.json` before generation. Every run
checks the original file hashes. An earlier fixture generator stripped outer
whitespace while keeping enclosing offsets; the harness now adjusts its receipt
offsets to the exact unchanged input text, and future fixture generation is fixed.

There were **72** bounded model requests: twelve each for the initial Gemma/v1 and
Qwen/v2 probes, twenty-four for the final literal-quote v3 comparison, and twenty-four
for a passage-ID experiment. Models were run sequentially against local Ollama.
Initially ComfyUI occupied about 8.8 GiB of GPU memory; after the final model unload,
the GPU reported zero usage. We did not control ComfyUI's lifecycle, so resource
occupancy changed between observations. These timings are not a strictly controlled
causal comparison of strategies. Model loading is included in the first request's
time; all results, including empty and rejected ones, are included in the medians.

| Strategy and model | Nonempty replies passing mechanical checks | Empty replies | Rejected replies | Median seconds |
|---|---:|---:|---:|---:|
| Literal v3, Gemma4 | 4 | 3 | 5 | 3.175 |
| Literal v3, Qwen3.5 9B Q4 | 4 | 1 | 7 | 6.959 |
| Passage IDs, Gemma4 | 1 | 9 | 2 | 0.202 |
| Passage IDs, Qwen3.5 9B Q4 | 4 | 1 | 7 | 3.460 |

Each arm has twelve cases, including one unavailable-body case for which empty is
correct. **These are not accuracy scores.** In particular, Gemma's apparent speed
with passage IDs mostly represents empty output. The initial probes are preserved
as negative evidence, not used for a same-prompt model ranking.

Source review of the final literal strategy:

- Qwen preserves the register's mandatory field list and both shareholder/payment
  requirements. It extracts the Norme 2/1999 amendment with both affected annex
  paragraphs, and the Law 2/1992 change separately from the Academy members' right.
  The right still lacks resolved replacement scope, and the historical approval
  chain is not fully extracted. Literal dates are not classified into act date,
  deadline and effective date.
- Gemma preserves the office's descriptive status, shareholder payment and the
  bank-capital deadline with its eligible-bank subject. It labels the second
  bank-application rule a fact. Its accepted abbreviation result is merely the
  introductory definition clause, not the two actual term expansions. It returns
  empty results for confidential-information and banking-amendment passages.
- Both models still introduce paraphrases into supposedly literal fields, splice
  noncontiguous quotations, miss qualifications or invent provision locators.
  Those literal-evidence violations are rejected rather than repaired by guessing.
  Entire-source rejection trades recall for a clearly incomplete state; it does
  not mean the other proposed claims in that reply were necessarily false.

The passage-ID experiment constrained evidence to code-owned passages and provision
labels to observed short locators. Qwen had a lower observed median time and recovered
**both C.N.V.M. and OSP definitions with their local scope**. However, it lost the
previously accepted nested amendment/right result and attached one payment duty
to the situation “La înfiinţarea unei bănci” as its subject. Gemma regressed to many
empty outputs. **The experiment was not promoted to runtime.** It remains a useful
direction for a better source-span representation, not a validated general remedy.

## Validation and next boundary

All **443 application, GUI and existing comparison-harness tests passed**, including
26 legal-claim tests, with one pre-existing Starlette/httpx deprecation warning.
The full run is recorded in `.logs/legal-claims-final-tests.log`.

The automated contracts cover literal evidence, separate provision anchors,
qualifier preservation, atomic refresh, incomplete output, interruption, source
changes during generation, superseded results/errors, same-hash races, migration,
budgeted resume, source identity, missing files, workspace ownership, MCP
registration and Rust snapshot readback. The real-data replay checks actual source
hashes, whole-document input, durable claims and native search after reopening.

Remaining work is substantive: provision-aware processing of long documents,
qualified review/adoption, source-scoped alias resolution, legal date roles and
act-version/amendment resolution, and calibrated semantic completeness checks.
The next extraction improvement should address source spans and role assignment
with held-out cases; another automatic model cascade would currently add cost
without a demonstrated accuracy gain. The existing full-text/citation corpus
remains useful alongside this deliberately bounded claim layer.

Reproduction from the repository root (`PYTHONPATH=mcp-server`):

```powershell
.venv/Scripts/python.exe docs/experiments/evaluate_legal_claims.py --model gemma4:latest --output <new-file.json>
.venv/Scripts/python.exe docs/experiments/evaluate_legal_claims.py --model qwen3.5:9b-q4_K_M --evidence-spans --output <new-file.json>
.venv/Scripts/python.exe docs/experiments/exercise_legal_claim_product.py --workspace <new-directory> --output <new-file.json>
```

The experiment commands refuse to overwrite prior outputs. Saved raw replies and
the three product-replay reports are under `docs/experiments/real-law-legal-claims-*`.
Task-loaded Ollama models were unloaded after the runs; ComfyUI was not stopped.
