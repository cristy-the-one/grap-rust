# Preserve legal statements before extraction

KEE now offers opt-in `statements` chunking. It prefers recognized sentence,
paragraph and article boundaries instead of cutting at the last space before a
fixed limit. An individual unit may exceed the soft target, up to a hard limit of
three times that target. Longer units are split explicitly and counted.

Chunks remain exact, contiguous source slices with no overlap or rewritten text.
Only whitespace may fall between them. Common Romanian abbreviations, initials,
act numbers and article headings receive conservative handling. This is a bounded
structural heuristic, not a complete linguistic or legal parser; qualifications
in other articles or separate sentences still require contextual interpretation.

## Measured on the 2,092-document JSON corpus

Target 800 characters, new hard limit 2,400 characters. No LLM or embedding model
was loaded for this measurement.

| Measure | Existing fixed-size mode | Statement mode |
|---|---:|---:|
| Total chunks before filtering | 31,629 | 32,398 |
| Chunks after existing table filter | 29,572 | 31,141 |
| Cuts within recognized structural units | 26,389 | 833 |
| Characters skipped by table filter | 1,613,786 | 1,659,334 |

Internal cuts fell approximately 96.8%, while extraction chunks increased 5.3%.
These are structural/count measurements, not semantic-accuracy scores or runtime
estimates. The larger possible chunks change generation cost as well as context.
The unchanged table heuristic skips 45,548 more characters with these boundaries;
that coverage trade-off is one reason this mode is opt-in. Full source coverage
was checked **before** that filter, not claimed for the resulting extracted graph.

Concrete source `17101.json`, Article 59: the old `17101#34` chunk ended with
`Comisia centrala de arbitraj se numeşte şi îşi desfăşoară`. The new chunk preserves
the remainder `activitatea potrivit legii.` This removes the truncated input
that previously contributed a meaningless reflexive relation in the KEE probe.
No claim is made that every model will extract the corrected input accurately.

The [measurement artifact](experiments/statement-chunking-2026-09.json) records
source fingerprints, counts and coverage-check scope. Reproduce without models:

```powershell
.venv/Scripts/python.exe scripts/measure_chunking.py real-data-law-ro --out .logs/chunking.json
```

## Use and resumption

In **Persistent ontology**, select **Preserve statements (new workspace)** under
**Chunk boundaries**, and choose a fresh persistent graph directory. The UI reports
forced cuts and table skips at completion.

```python
app.extend_extraction_graph(path="fresh-graph", docs_path="corpus",
                            chunk_strategy="statements", chunk_chars=800)
```

`extract_ontology(..., chunk_strategy="statements")` also supports single-use
extraction. Existing callers default to legacy behavior. The persistent store
records its strategy in SQLite metadata; subsequent calls can omit the strategy
and inherit it. Switching a populated store to a different strategy is rejected
before extraction/retraction, even with refresh enabled. Use a fresh workdir for
comparisons. Older stores without metadata are interpreted as legacy.

Changing `chunk_chars` retains its existing re-chunk/re-extract behavior. Evidence
ingestion still uses its previous splitter; this change is confined to KEE input.
Matching source text remains identical, but evidence-node and extraction-chunk
boundaries can differ when this opt-in strategy is selected.
