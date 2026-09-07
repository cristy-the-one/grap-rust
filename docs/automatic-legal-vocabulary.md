# Automatic vocabulary discovery

KEE now has a resumable **post-extraction** vocabulary discovery step. It uses
existing source receipts; it does not repeat corpus extraction, load embeddings,
or rebuild the Rust graph. One application operation serves MCP and the GUI;
the command-line runner uses the same implementation.

## Run it

In the console, select **Persistent ontology → Discover a legal vocabulary**.
Enter a KEE JSON/YAML export or a persistent extraction graph folder, select an
installed model, and click **Discover / Resume**. The call budget bounds each run.
Run again to continue; **0 calls** reads cached decisions without inference.
Download the evidence report to retain the complete result.

The saved real-document timing probe is a usable starting input:

```powershell
.venv/Scripts/python.exe scripts/distill_ontology.py `
  docs/experiments/full-kee-timing-2026-09.json `
  --out pilot-law-ro/vocabulary --model grap-legal-gemma4 --max-calls 30
```

For MCP/batch clients:

```python
app.distill_ontology(path="path/to/extraction-workdir", model="grap-legal-gemma4",
                     max_calls=8, min_documents=2)
```

This requires operator access. Application caches belong to the current workspace;
CLI caches belong to `--out`. Successful batches are keyed by content, prompt,
schema, and provider configuration. Failed transport calls remain retryable.
Malformed multi-item protocols (missing/duplicate IDs or wrong fields) split into
smaller batches automatically, within the same call budget. A valid protocol with
an invalid decision, such as an invented scope quote, marks that item uncertain
without regenerating its valid neighbors. The raw reply remains in the cache.
Completed singleton/batch decisions survive interruption.

## What it discovers

1. **Classes:** classifies each source mention as a specific entity, a general
   category, a fragment, or uncertain; proposes a short class label and definition.
2. **Predicates:** proposes consistent wording and checks source meaning,
   direction, modality and scope. A separate checker call can reject the mapping.
3. **Patterns:** groups accepted mappings by endpoint classes/kinds, predicate,
   and modality. Recurrence counts distinct document IDs, not repeated chunks.

Patterns are descriptive candidates. They do not assert that all bodies issue
acts, that an authorization has been exercised, or that a historical provision
remains in force. Conditions/dates remain attached to individual source relations;
they are not generalized into universal axioms. A sparse pattern is still shown
as sparse rather than silently discarded.

The input mentions come from receipts **before similarity merging**. Exact
orthographic matches can group within one document and class/kind. Identical
generic names in different documents remain separate. This avoids inheriting a
bad upstream merge, but does not solve cross-document legal identity or aliases.

## Checks and limitations

- Supplied full text must match its receipt fingerprint; quoted offsets must
  match the original text. Compact exports without full text are explicitly
  marked `receipt_only`, not independently source-checked.
- Missing quotes and ambiguous endpoints are excluded and accounted for. Older
  exports without receipts must be refreshed before discovery.
- Reflexive artifacts without reflexive evidence do not enter schema patterns.
- Explicit Romanian permission/obligation markers block an `asserted` mapping;
  dropped negation also blocks a mapping. These guards override model agreement.
- Recognized predicate families cannot change meaning during normalization:
  legal basis, decision, issuance, adoption, approval, amendment, repeal and
  publication remain distinct. Unknown predicates remain discoverable; sharing
  a family does not prove that two predicates are equivalent.
- A label cannot silently acquire a conflicting definition. Unresolved cases
  remain visible instead of changing earlier meanings.
- Model agreement is **not** semantic accuracy. The checker currently uses the
  same configured model in a separate call; its errors can correlate with the
  proposer. Class definitions and memberships remain model suggestions.
- Vocabulary context is capped at 64 entries; very large vocabularies can still
  contain synonyms. No similarity threshold automatically collapses their meanings.
- Distillation cannot recover facts that extraction omitted. Tables, completeness,
  historical validity and jurisdiction still require dedicated extraction work.

This workflow automates the bulk classification and normalization work. Human
review can concentrate on uncertain cases and sample-based evaluation; it is not
a required click-by-click stage of generation.

## Why after extraction?

The earlier [distillation experiments](experiments/distillation-journal.md) found
that giving extraction a small relation vocabulary encouraged distorted facts.
The discovered vocabulary therefore does not feed back as asserted patterns or
mandatory relation names. The evidence graph remains unchanged.

## Resumed real-document probe, 2026-09-07

The saved 24-chunk KEE sample yielded 84 source mentions. Resumption completed with
8 new model calls and 16 cache hits; a subsequent complete replay used zero new
calls and 23 cached batches. The report contains 15 candidate classes, 43 entities
grouped within documents, and 16 relations considered for normalization: six
passed the current model/evidence checks and ten remain uncertain. No pattern
had support from two documents. These counts are processing outcomes, not accuracy.

The observed `în baza` → `hotărăște` semantic drift is now blocked even if the
checker agrees. A minimized three-relation regression confirms that one fabricated
scope quote no longer doubles calls from three to six. Source quotes and the
reason for each abstention are visible in the GUI, and class lists expand to
their supporting mentions.

[Saved result](experiments/legal-vocabulary-resumed-2026-09.json) includes all
mentions, relations, decisions, checks and exclusions. The model classified both
a banking agency and `Banca Agricolă - S.A.` as public authorities. An explicit
commercial-form check now abstains on that bank's classification and excludes its
dependent schema pattern. Class membership still needs evaluation; the six
passing relation mappings remain candidates, not a validated legal schema.
The next accuracy experiment should measure entity typing and relation meaning
on held-out, related documents, including counterexamples to proposed classes.
