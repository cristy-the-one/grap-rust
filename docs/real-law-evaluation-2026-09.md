# Romanian legal knowledge: real-corpus evaluation

## Decision

Process the whole folder for source coverage, full-text retrieval and citation
discovery. Keep semantic LLM extraction scoped and reviewable until it preserves
legal subjects, conditions and amendment scope reliably. A larger graph is not
evidence of a more accurate legal knowledge system.

The clarified product goal is a self-discovered, comprehensive knowledge system
over Romanian legal documents. The vocabulary is recorded in [CONTEXT.md](../CONTEXT.md).
The existing assessment-oriented `outcomes` / `requirements` representation is
useful as an extraction prototype, but it is not a sufficient model of laws.

## Whole-corpus audit and working graph

All **2,092 JSON files** in `real-data-law-ro` were parsed. No JSON parse errors
were found. They contain **22,496,879 body characters** in 25,290,853 file bytes.
Median body size is 2,620 characters; the largest is 492,976. Two bodies are
processing placeholders. A short body is not necessarily missing content.

With the existing reader and tabular filter, full extraction would request:

| Chunk cap | LLM chunks | Tabular chunks skipped |
|---|---:|---:|
| 800 characters | 29,596 | 2,057 |
| 1,600 characters | 16,383 | 1,044 |
| 2,400 characters | 12,051 | 694 |

These counts include separately chunked headings and exclude relooks/retries.
They justify auditing all documents while restricting expensive semantic tests.
Tabular exclusions are not proof that a document's legal content was covered.

Built a persisted **Rust-backed graph of 2,092 full act texts and 978 citation
edges**, without LLM calls. The measured final build took 2.395 seconds on this
machine; reopening the native graph and searching a body-only term also worked.
The application previously indexed only truncated titles for this graph.

Local output: `.logs/real-law-product-workspace/legal-kg/`. Its
`legal-sources.json` records source paths, file hashes, URLs and metadata;
`citation-report.json` records matched quotes, body offsets and resolution status.
The graph remains an evaluation output, separate from other workspaces.

### Corrected citation identity

- The old parser used the effective-date year as the act year. **218 documents**
  have a different heading year. For example, source `16678` is Decision 997 of
  December 1998, with a January 1999 effective-date field.
- Common slash references such as `Legii bancare nr. 58/1998` were missed.
  Parsing now covers these and date-form references, including inflected kinds
  and intervening issuer/descriptive words.
- Type/number/year can collide across issuers or source versions. The old map
  silently picked its last entry, even linking a document's own heading to an
  unrelated act. Collisions now stay unresolved. An act's own heading is not an
  external reference.
- Heading numbers take precedence over lossy metadata: `1.1/X` must not become
  act `11`. A missing identity year stays unknown rather than borrowing an
  effective year.

The old graph had 379 citation pairs. The final graph has 978; this is **not** a
claim that all new edges are proven correct. Twenty newly resolved references
were inspected against target headings and matched. All recorded quote offsets
were checked against source bodies. Removed old edges include demonstrably wrong
issuer collisions. The stored review sample makes that check reproducible.

Final coverage records **12,276 missing-reference occurrences**, **68 ambiguous
reference occurrences**, and **87 acts lacking a numbered/year identity**. The
87 still exist as source-ID graph nodes. Missing references can mean absent
documents or identities the parser cannot resolve; they are not silently dropped
from the audit. Number/year matching remains incomplete for issuer-specific,
unnumbered and unusual acts. It never implies amendment or repeal.

`in_vigoare` is null in 1,301 source files, true in 677 and false in 114. These
downloaded values are retained as source metadata, not treated as a current-law
determination or reconstructed version history.

## LLM experiment and source review

Before generation, froze **12 source excerpts from nine documents**, with file
hashes, field names, offsets and selected expected checks. They cover duties,
prohibitions, exceptions, deadlines, percentage bases, abbreviations and nested
amendments. These are agent-reviewed engineering fixtures, not an independently
annotated legal acceptance corpus. No population-wide accuracy percentage is
claimed. The small set was deliberately chosen to expose failure modes.

Ran Gemma 4 and Qwen 3.5 9B through structured generation, then compared an
experimental Romanian legal prompt using the same models and excerpts:

| Model / prompt | Usable replies | Median seconds |
|---|---:|---:|
| Gemma 4 / existing v4 | 10/12 | 3.35 |
| Qwen 3.5 9B / existing v4 | 11/12 | 5.46 |
| Gemma 4 / experimental Romanian prompt | 11/12 | 1.31 |
| Qwen 3.5 9B / experimental Romanian prompt | 12/12 | 3.51 |

“Usable” only means accepted by parsing/schema/source-quote checks. It does not
mean accurate or complete. In particular, exact-name/verb scores in the raw
comparison files miss legitimate inflections or paraphrases. Treat them as
diagnostics, not semantic accuracy scores.

Source review found:

| Source / case | Finding |
|---|---|
| 16678, office subordination | The original Gemma reply retained the relationship using a longer Romanian predicate. The experimental prompt omitted it. An exact predicate score alone incorrectly reports the original as a miss. |
| 16694, bank-register fields | Both original models failed to retain the listed mandatory fields as requirements. The experimental prompt still failed to capture the complete list. |
| 17754, shareholder payment | Original Gemma attached the content to `Articolul 2`, which the structural filter rejected. The Romanian prompt recovered the shareholder obligation, but did not recover every duty in that article. |
| 17754, capital deadline | Gemma retained a deadline-bearing requirement under a more specific bank name than the exact-name fixture. Qwen's original reply lost the deadline and attached requirements to the wrong object. |
| 17984, prohibitions | Models attached duties to article headings or shortened conditional prohibitions. Experimental Gemma produced malformed output despite schema mode. These replies cannot establish a reliable duty model. |
| 17984, exceptional acceptance | Experimental Gemma retained the exceptional-case and approval constraints. Qwen still split the rule and emitted an acceptance relation whose conditions were not fully represented. |
| 18185, abbreviations | The original replies omitted explicit expansion relationships. The Romanian prompt recovered both definitions, but Qwen also incorrectly made the definitions mandatory requirements. |
| 18185, confidentiality | Secrecy, access restrictions and approval were only partially represented. Some statements became malformed or weakly supported relations. |
| 16686, broadcast rules | Percentages, exclusions and their denominators were lost or split. The Romanian prompt recovered more text, but still did not preserve the complete structured legal scope. |
| 17994 / 16955, act references | The original prompt retained more act identities. The Romanian prompt often focused on a duty and omitted the amendment chain. A baseline reply also assigned an approval to the wrong step in that chain. |
| 16955, beneficiary rights | The experimental prompt put receiving an indemnity into the beneficiary's mandatory requirements, exposing the inadequacy of the assessment schema for legal rights. |
| 109203, missing body | Original Gemma invented a concept named “Act în curs de procesare”; the other tested arms returned empty results. |

**The Romanian prompt was not promoted to runtime configuration.** Its speed and
parsing improvements came with losses that matter to the clarified product goal.
It is preserved as `experiments/real-law-experimental-prompt.txt`, with raw
outputs, for comparison. The generic runtime prompt remains unchanged.

## Entity merging

Froze 20 source-derived identity pairs: eight aliases and twelve distinct entities.
Some pairs use explicitly documented nominative/genitive variants. Each pair was
inserted into the actual `WorkingGraph` / Rust resolver with a relation between
its endpoints. Distinct identities must retain two nodes and that edge.
Embeddings were fetched once per model, then cached and replayed across thresholds.

At the existing **0.86** threshold:

| Embedder | False merges before | False merges after | Missed aliases after |
|---|---:|---:|---:|
| Hashing baseline | 0/12 | 0/12 | 6/8 |
| BGE-M3 | 0/12 | 0/12 | 3/8 |
| Qwen3 embedding 0.6B | 5/12 | 0/12 | 3/8 |

Qwen's raw cosine similarities were genuinely high: Law 101/1998 versus Law
58/1998 scored 0.955; ministry versus minister scored 0.968. Rust retrieval agreed
with the saved vectors. This was an identity-policy bug, not bad Rust ranking.

Added a shared conservative merge veto for conflicting explicit numbers and
recognized entity kinds. It applies to live outcome resolution, relation endpoint
resolution and batch reconciliation. Checking all registered aliases prevents a
generic name from bridging incompatible numbered acts. Regression tests force
identical vectors for distinct identities and require the nodes/edge to survive.

The three missed semantic aliases are explicitly defined abbreviations. Lowering
the threshold to 0.80 introduces false merges even with the guard. Prefer BGE-M3
at 0.86 as the next semantic baseline, but do not infer global identity from
similarity. Abbreviations such as OSP need source-scoped definitions. The guard
does not prove two same-number/same-kind entities are identical and does not
repair already merged stores; compare/re-extract into a separate store.

## Chunk-size experiment

Tested complete documents 16678, 17994 and 16955 at 800 and 1,600 characters,
using Gemma and the unchanged v4 prompt. Identical chunks shared one saved model
reply across arms, yielding 18 distinct requests. These totals exclude model
loading; they are a small controlled comparison, not a throughput forecast.

| Cap | Logical requests | Usable replies | Generation seconds |
|---|---:|---:|---:|
| 800 | 13 | 13/13 | 40.60 |
| 1,600 | 8 | 7/8 | 40.11 |

The 1,600-character body of the amendment-chain document reached the output
limit and was rejected. Larger chunks reduced requests without materially
reducing generation time here. Keep 800 as the present default. This is not an
endorsement of arbitrary character splitting: the next chunking work should
preserve provision boundaries and document identity, and must account for quoted
replacement articles rather than treating every repeated article number as a
new top-level provision. Changing chunk IDs must be an explicit migration.

## Product direction

The useful next step is a legal claim representation, not another general agent
or automatic model cascade:

1. **Source and act catalog:** keep source ID/hash, issuer, exact heading number,
   act date and supplied effective/in-force metadata separate. Add version history
   and duplicate-source handling before treating multiple folders as one evolving
   authoritative collection. This pass delivers the full-text/citation foundation,
   not that complete lifecycle.
2. **Scoped provisions and references:** identify an article within its act and
   version; retain quote offsets and uncertain references. Distinguish actual
   amendment operations from citations and quoted replacement text.
3. **Typed, source-backed claims:** represent fact, definition, right, obligation,
   prohibition, permission and legal change separately, with subject, scope,
   conditions, exceptions and dates. Preserve original wording. An ASPICE
   “missing characteristic” is not a legal-validity conclusion.
4. **Aliases with scope:** consume explicit definitions as evidence, while keeping
   ambiguous abbreviations local. Test the final graph, not just pairwise vectors.
5. **Coverage-aware retrieval:** answers should expose their sources, applicable
   version/date and unresolved scope. A fresh download or a positive source flag
   alone must not establish what is currently in force.

These are remaining product requirements, not features claimed to be implemented
by this evaluation. The current source/citation graph can already support useful
discovery while LLM claims remain reviewable proposals.

## Validation and reproduction

All **417 application, GUI and comparison-harness tests passed**, with the existing
Starlette/httpx deprecation warning. The run is recorded in
`.logs/real-law-final-tests.log`. Source hashes, raw outputs, model settings,
timings, vector-pair scores and reviewed citation samples are committed under
`docs/experiments/real-law-*`.

From the repository root, set `PYTHONPATH=mcp-server` and use:

- `docs/experiments/prepare_real_law_eval.py` for the whole-corpus audit and frozen
  fixtures (regenerate only deliberately; they predate model evaluation).
- `docs/experiments/evaluate_real_law_merging.py --output <new-path>` for the
  identity comparison. Its local vector cache avoids repeated embedding calls.
- `docs/experiments/audit_real_law_product.py` to rebuild the isolated evaluation
  graph and verify full-text retrieval after reopening it.
- `docs/experiments/compare_local_models.py --models-config ... --cases ...
  --output <new-path>` for generation; `--prompt-file` selects the experimental
  prompt without changing runtime defaults. Existing outputs require `--resume`.

The Rust engine source is unchanged; the experiments exercise its graph, vector
resolution, text index and persistence through the installed binding. Original
corpus files are unchanged. Test models are unloaded when evaluation finishes.
