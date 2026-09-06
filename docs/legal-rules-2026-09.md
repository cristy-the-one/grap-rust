# Romanian law: source statements, scoped definitions and searchable evidence

The default legal extractor is now `legal-rules-v6`. It keeps source wording and
context in code and asks the LLM to classify passages and select subject tokens.
Explicit definition lists and inline definitions are parsed directly. This replaces
the fragile request for a model to rewrite every rule into ten literal fields.
The earlier [literal-field evaluation](legal-claims-2026-09.md) remains available.

The gain is reliable preservation and much better extraction availability. It is
**not a demonstrated legal-accuracy rate**. Some subject ranges and labels remain
wrong, and some substantive passages are labeled `other`. The original text stays
in the receipt and is now searchable in the Rust graph independently of those labels.

## Implemented changes

- Complete source statements, local article offsets and surrounding provision text
  are retained verbatim. Replacement provisions retain neighboring amendment text.
  These are source locators, not resolved identities or consolidated act versions.
- The model returns a kind and an inclusive subject-token range for every supplied
  unit. Requests contain at most 12 units and 1,200 whitespace tokens. Malformed
  unit coverage still fails the document; an unusable subject becomes unresolved
  without discarding the original rule. Model `other` decisions remain in receipts.
- Explicit Romanian wording can correct obvious labels. Disagreements are recorded
  in `review_issues`. Negative future tense alone does not force a prohibition.
  Paragraph numbers, circumstances, verbs and some incomplete subject fragments
  are excluded from the subject field.
- Mixed and nested definition lists have disjoint extraction spans, preserve dotted
  abbreviations and include the last entry without requiring a final semicolon.
  `ART.7` headings and flattened numbered paragraphs are recognized.
- `resolve_legal_term(term, source_id)` looks up explicit definitions in one source.
  `cnvm` can match `C.N.V.M.`. Different meanings remain ambiguous, and stale sources
  do not supply active definitions. This does not merge entities across documents.
- Extractor upgrades automatically retry old failed results and replace successful
  results from an older extraction version when they are processed again. Unchanged
  results from the current version are reused; explicit refresh retries failures.
- Rust snapshots index complete accepted source bodies as well as proposed claims,
  including sources with zero extracted claims. Claims link to their source node.
  A single joined SQLite read replaces per-source receipt/claim lookups during export
  and keeps the exported source/claim set consistent. Indexing remains an explicit
  export step, with no new database, vector service or graph engine.

Ordinary rules use `statement`; their `predicate` and `object` are intentionally
empty. Explicit definitions populate those fields. Conditions, exceptions and dates
are literal cues, not a complete semantic decomposition. All proposals retain
`review_status=proposed` and `scope_resolution=unresolved`.

## Local-model evidence

Frozen source hashes and exact passages are checked before generation. Both models
used temperature zero, `reasoning_effort=none`, 4,096 output tokens and no retries.
The 12 original cases contain 11 substantive passages and one missing-body placeholder.

| Trial | Nonempty cases | Empty cases | Rejected cases | Proposals | Total seconds |
|---|---:|---:|---:|---:|---:|
| Gemma, old literal contract, new control run | 4 | 3 | 5 | 6 | 44.954 |
| Gemma, source statements v6 | 11 | 1 | 0 | 26 | 10.192 |
| Qwen 3.5 9B, source statements v6 | 11 | 1 | 0 | 27 | 18.038 |

These are observed one-pass wall times, not repeated throughput benchmarks; the
first case can include loading/warm-up. Excluding that first case gives 37.794 seconds
for the old Gemma contract and 9.173 seconds for the new one. The workload/output
contract changed deliberately: code supplies evidence instead of generating copies.

Artifacts: [old control](experiments/real-law-literal-gemma-control-2026-09.json),
[Gemma v6](experiments/real-law-rules-gemma-v6-2026-09.json),
[Qwen v6](experiments/real-law-rules-qwen-v6-2026-09.json).

On eight additional passages, Gemma returned nine proposals in 4.493 seconds and
Qwen ten in 6.548 seconds, with no rejected replies. These additional documents
helped develop the parser and are **not an independent held-out accuracy test**.
Artifacts: [cases](experiments/real-law-rules-additional-cases.json),
[Gemma](experiments/real-law-rules-gemma-additional-v6-2026-09.json),
[Qwen](experiments/real-law-rules-qwen-additional-v6-2026-09.json).

For this contract, `gemma4:latest` is the practical local choice in the usage example:
it is faster and Qwen does not consistently improve the reviewed labels/subjects.
Qwen selected the fuller “vărsămintele în contul capitalului social” in one passage,
but also selected a predicate as a subject and classified a document preamble as an
amendment. Gemma also produced truncated actors and misclassified duties as facts.
Neither comparison justifies automatic adoption of model claims. Existing provider
configuration and explicit model arguments remain authoritative; no global model
setting was changed.

## Complete-document run and Rust retrieval

The [live public-API trial](experiments/real-law-rules-live-final-2026-09.json)
processed complete files 16678, 16694, 17754, 17984, 18185, 16686, 17994 and 16955.
Placeholder 109203 was explicitly deferred. It used Gemma, 2,048 output tokens,
zero retries, and the normal 8,000-character document bound.

- Eight complete documents accepted in 25.956 seconds, using ten model calls.
- 80 proposals, including 11 explicit definitions; 14 unresolved subjects and
  22 proposals with recorded review issues. These flags do not capture every error.
- Every claim, context, subject and provision evidence span was checked against
  the retained full original body. Reopening reused the receipts without generation.
- The 2,092-file coverage scan took 0.570 seconds with zero new model calls:
  eight ready sources and 2,084 deferred. This is not full-corpus LLM extraction.
- Scoped `cnvm` lookup returned the explicit definition from source 18185.
- Export and reopen produced 88 native nodes and 80 claim-to-source edges.

After adding full source text to snapshot nodes, an additional
[source-search check](experiments/real-law-rules-source-search-2026-09.json) re-exported
those receipts without model calls. Rust search returned source 17754 for
`Documentaţia`, including the paragraph the model classified as `other`. A native
regression test also covers a source whose model output yields **zero claims**.

Manual review of the full documents caught the mixed-definition bug, an absorbed
paragraph boundary and a substantive `other` classification. It also confirms
remaining errors: truncated subjects such as “Obiectul”, descriptions that conceal
duties, and an offense statement classified as an obligation. Keeping the complete
statement prevents textual loss, but does not make its proposed interpretation right.

## Use and reproduce

The full MCP tier exposes extraction, claim reads, source/history reads, scoped term
lookup and snapshot export. The input remains legal JSON with `id`, `titlu` and
`text_complet`; arbitrary PDF/Word ingestion is not implemented by this path.

```python
from grap_mcp.application import GrapApplication

app = GrapApplication(r"C:\dev\grap-rust\.logs\my-law-workspace", role="operator")
result = app.extract_legal_claims(
    r"C:\dev\grap-rust\real-data-law-ro",
    max_files=3, max_chars=8000, model="gemma4:latest")
definitions = app.get_legal_claims(kind="definition")["claims"]
if definitions:
    definition = definitions[0]
    match = app.resolve_legal_term(definition["subject"], definition["source_id"])
snapshot = app.export_legal_claim_graph()
app.load_knowledge_graph(snapshot["graph_path"])
```

`max_files` limits whole-document attempts, not requests within each document or
transport retries. `max_files=0` scans only. Large documents and individual units
over the token bound remain explicitly deferred/failed rather than truncated.
The ledger is authoritative; export a new snapshot after further ingestion.
Historical receipts remain readable by revision ID. Snapshot manifests map source
node IDs to full receipts so retrieval can distinguish source evidence from claims.

From the repository root in PowerShell:

```powershell
$env:PYTHONPATH='C:\dev\grap-rust\mcp-server'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe docs/experiments/evaluate_legal_claims.py --rules --model gemma4:latest --output .logs/new-rule-trial.json
.venv/Scripts/python.exe docs/experiments/exercise_legal_rules_live.py --workspace .logs/new-law-workspace --output .logs/new-live-trial.json
```

Use fresh output/workspace names. Intermediate v1–v6 artifacts are retained as
development evidence. Earlier rule artifacts with `rule_strategy=true` incorrectly
retain the old top-level `strategy`/`schema` labels; their receipts identify the rule
extractor. The current evaluator fixes those metadata labels and records the extractor
file hash. The historical literal replay explicitly selects the legacy extractor.

## What will improve results next

The strongest next investment is a frozen, independently annotated provision-level
set covering actors, duties/rights, exceptions, scope and negative cases. Use it to
measure omissions and incorrect role assignments separately from source preservation,
and to choose targeted second-model review rather than running two models everywhere.

Larger acts need resumable provision-level extraction with a document completion
receipt; raising a character cap alone does not solve cross-provision scope. After
that, explicit amendment-target and effective-date resolution can connect historical
versions without treating a citation as a modification. Those steps matter more to
an encompassing law knowledge system than a larger graph or automatic semantic merges.

Validation for this implementation includes 465 Python/application/GUI/experiment
tests, including native Rust snapshot/search tests. Rust source was not changed in
this step. The existing Starlette/httpx deprecation warning remains unrelated.
