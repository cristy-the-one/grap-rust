# Simplification implementation

Implements the ordered decisions in [the architecture audit](architecture-value-audit-2026-09.md).

## Run contracts and unnecessary work

- `run_procedure` is a compatibility name for the same tracked gap check as
  `detect_gaps`. Both use frozen activation weights and PG content, persist
  explanations, and report that procedure operations were not executed.
- Run metadata identifies the actual ontology and explicitly records that no
  evidence snapshot was captured. `reconstruct_run.artifacts_available` checks
  referenced files; `replayable` is false. Legacy `complete` retains its artifact
  availability meaning.
- PG loading no longer inserts procedure steps into evidence retrieval.
- Reference patches are deterministic, preserve existing step semantics, and
  retain coverage validation, draft publication and promotion policy. The old
  `model` argument is accepted but ignored. Removed references require review
  under the existing nontrivial-diff policy.
- Default Rust hybrid ranking disables PageRank. Explicit centrality weights
  remain supported; no retrieval-quality improvement is claimed without an
  independent task evaluation.

Validation: existing Python suite 349 passed; two additional run/patch contract
regressions passed. Rust default-ranking regression passed.

## Application ownership

`GrapApplication(workspace, ...)` now owns the application operations, graph
state, workspace selection, provider factories and session history. MCP registers
bound operations; the GUI and batch runners instantiate the same application
without importing the MCP transport. Workspace selection creates a fresh instance
and does not change process environment variables or reset selected global fields.
Old MCP tool names remain callable for compatibility.

The service deliberately remains one cohesive module. This change fixes ownership
and removes transport coupling; it does not claim that moving code reduces its
intrinsic complexity. Independent application instances do not coordinate writes
to the same graph directory.

Validation: 352 existing/application tests passed, then the corrected two-instance
isolation regression passed separately; application import is checked to avoid
loading MCP. GUI integration suite: 18 passed.

## Extraction checkpoints and Rust views

SQLite `graph_store.db` is the authoritative extraction store. Schema 3 adds
the concept-id high-water mark and a model-identified embedding cache. Saving
compares existing rows and applies only additions, updates and removals in one
transaction. Snapshot construction and comparison still cost O(N); this is
incremental disk writing, not an O(delta) in-memory dirty tracker.

Checkpoints no longer rebuild or serialize the Rust graph, clear EntityIndex,
or call an embedder. Reopening rebuilds both retrieval views from the committed
records. Cached canonical-name vectors avoid embedding the entire graph again;
older stores or a changed embedding model require one embedding pass, persisted
at the next checkpoint. Broken JSON in the embedding cache does not hide records.

Concept ids now survive deletion holes and restart, and removed ids are not
reused. A small bidirectional mapping separates them from Rust node ids. EntityIndex
continues returning native retrieval ids, so GUI queries work after reconstruction.
Previously compacted historical ids cannot be recovered retroactively.

Retractions immediately remove nodes from Rust retrieval and aliases, and rebuild
only edge topology/component membership when necessary. The Rust `clear_edges`
primitive preserves node ids, text and vector indexes while invalidating edge ids
and cached PageRank. Retraction still costs O(V+E) for topology repair; ordinary
insertions and checkpoints do not perform it.

Application calls cannot mutate an extraction view through evidence-ingestion,
alias-editing or compaction tools and silently lose those edits on restart. Use
`extend_extraction_graph` for durable extraction changes or a separate evidence KG.
Selecting another KG clears the extraction-view state.

### Measurements

[Reproducible benchmark](experiments/checkpoint_benchmark.py), baseline `903beb1`,
same debug Rust binding, three timed repetitions per operation, local Windows
storage and offline 64-dimensional hashing embeddings. Timings exclude initial
graph construction and first checkpoint. The one-row case updates a chunk hash.

| Concepts | Unchanged checkpoint, before → after | One-row checkpoint, before → after | Reopen, before → after | Texts embedded on reopen, before → after |
|---:|---:|---:|---:|---:|
| 200 | 339.65 → 2.24 ms | 338.71 → 4.93 ms | 378.03 → 350.43 ms | 200 → 0 |
| 1,000 | 1,898.27 → 10.02 ms | 1,793.49 → 12.37 ms | 1,785.22 → 1,739.78 ms | 1,000 → 0 |

No claim of proportional improvement on all corpora or of faster local reopening:
index reconstruction remains the startup cost. Remote embedding latency was not
simulated. Regression checks independently assert zero SQL writes for an unchanged
checkpoint and exactly one write for a changed chunk hash.

### Migration and validation

Use `WorkingGraph.open(workdir, embedder)` or `open_extraction_graph(workdir)` to
open extraction records. `workdir/graph` and `entities.db` are disposable views,
not independent backups. To produce a standalone Rust snapshot explicitly:

```python
with WorkingGraph.open(workdir, embedder) as graph:
    graph.kg.save_to_dir(export_directory)  # choose a separate export directory
```

The Python suite now has 363 passing tests, including failed-transaction recovery,
uncommitted-view recovery, empty checkpoints, stable ids, embedding-model changes,
immediate edge retraction, application ownership and consistent tracked runs.
Rust checks: 146 passed with no default features (including doctests); 152 passed
with all features (`--tests --lib`). The rebuilt Python binding smoke test and all
18 GUI integration tests passed; frontend JavaScript passed `node --check`.
`cargo fmt --all -- --check` still reports existing formatting differences in
untouched code; this pass did not reformat unrelated files.

Existing evidence snapshots that already contain old PG projection nodes are not
automatically rewritten. Rebuild that evidence store from source documents to
remove legacy scaffolding; subsequent PG loads no longer add it.

## User-facing contracts and remaining experiments

The console presents one gap-check action plus the experiment variant. Audit UI
reports artifact availability, not replay completion. Unsupported scope strings
are rejected instead of silently ignored; PG references select the outcome scope.
Failed checks terminate their run records as failed.

Two-model extraction, explicit PageRank weighting, and policy-driven adoption
remain optional. Existing tests establish mechanics, not independent quality.
No multi-model or centrality quality benchmark was fabricated or replaced with
self-judgment. Those choices still need labeled task evaluations before becoming
defaults. Task-profile proliferation and further sharding infrastructure are not
needed to realize these measured improvements.
