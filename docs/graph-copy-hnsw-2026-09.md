# Rebalance payload sharing and HNSW reliability

## Rebalance: eliminate the second payload arena

Rebalance now imports references to immutable, reference-counted chunks. It
still constructs replacement topology and text/vector indexes before swapping,
so invalid plans and property-registry overflow leave the original usable.
Labels, hashes, embeddings, named edge properties and handle mappings survive.
The core blob arena is smaller in implementation: one list of owned or mapped
chunks replaces the separate writable/mapped storage branches.

Writes append to a uniquely owned chunk or allocate a new one. They never copy
an existing shared or mapped corpus. Shared allocations remain alive until the
last referencing graph drops them. Chunk sharing can retain dead bytes; existing
blob vacuum reclaims them. Save/reload reclaims unreferenced bytes, while
retaining payloads still referenced by tombstoned nodes. Per-shard `total_bytes` counts
referenced bytes, so summing it can double-count shared memory.

Saving streams only each graph's referenced payloads into chunks, with at most
4 MiB of payload buffering (oversized blobs stream directly). The existing
snapshot format is retained. Repeated 2→5→3→6 shard placement does not multiply
saved payload bytes. Empty, exactly 4 MiB, oversized and UTF-8 payloads with
empty embeddings round-trip through portable and mapped readers.

Measured with `cargo run --release --example rebalance_memory`: 256 documents,
256 KiB padding each, sparse tokens, 3→5 shards. Requested heap allocations are
instrumented; this is not RSS and does not represent topology-heavy workloads.

| Metric | Before | After |
|---|---:|---:|
| Heap before rebalance | 79,791,112 B | 79,791,488 B |
| Peak heap | 159,632,688 B | 79,943,104 B |
| Extra peak | 79,841,576 B | 151,616 B |
| Rebalance time, single run | 224.66 ms | 187.16 ms |

Topology and indexes still coexist during reconstruction; embedding indexes
still own their own vector copies. This removes the full payload copy, not all
temporary memory. The current sharded `load_mmap` facade still uses its portable
fallback; the core mapped arena itself supports sharing and appending.

## HNSW: correctness before approximate recall

The initial regression command was
`cargo test -p grap --features ann-hnsw --test hnsw_contract`.
It reproduced stale values after replacement and -1 cosine being reported as 0.
Upstream inserts another physical point for a repeated label; it does not replace
the old one. Upstream also exits the process for M greater than 256, so parameters
must be validated before calling it.

HNSW now inserts each base label once. Updates/deletions invalidate that label;
live changed vectors are scored in an exact overlay. Repeated edits to one node
do not add physical points. Compaction rebuilds after at least max(64, live/4)
distinct invalidations; `vacuum()` explicitly compacts. This bounds stale entries
but can introduce a rebuild pause and temporarily coexist with the old index.

ANN candidates are deduplicated, rescored against current raw vectors using
the exact backend's f64 cosine, and sorted by score then ID. Cosine retains
negative values. Finite vectors are normalized before entering upstream to
avoid overflow; zero vectors use exact scoring. Empty/nonfinite/mismatched
inserts are rejected by `try_add_vector`; the existing infallible trait method
logs rejection. Small indexes (up to 64), zero queries, requests for all vectors,
and insufficient ANN candidate counts use an exact fallback.

Loading validates counts, dimensions, duplicate IDs and parameters before
replacing the live index. Snapshots contain current raw vectors, not ANN topology;
reload rebuilds it. Stable insertion order does not eliminate upstream hierarchy
randomness. Valid candidate scores and mutation safety do not guarantee recall.

New HNSW indexes use M=32, ef_construction=100, ef_search=150, maximum 16
layers, and retain pruned neighbors to fill unused neighbor slots. Previously
saved parameters remain honored. Compaction preserves those parameters; it
does not silently upgrade an existing custom configuration.

The graph loader also validates cached vector values and live membership against
the graph's own embeddings. A syntactically valid older cache previously restored
obsolete values and deleted nodes. Mismatched caches now trigger a rebuild;
matching caches can still ingest newly added graph vectors. This check applies
to both backends and adds a linear embedding-validation pass during load.

Python exposes `vector_index_stats()` (backend, dimension, live count) and
`compact_vector_index()` (explicit synchronous maintenance). The latter can
pause queries; it is not silently added to background text maintenance.
The Python extension has an opt-in `ann-hnsw` Cargo feature; exact remains its
default. Build an isolated ANN wheel with:

```powershell
maturin build --release --manifest-path crates/pygrap/Cargo.toml --features ann-hnsw --interpreter .venv/Scripts/python.exe --out .logs/ann-wheel
```

## Measured retrieval and explicit limits

The read-only experiment uses 9,934 saved 1,024-dimensional project vectors:
65 records are held out as queries, leaving 9,869 indexed records. Holding out
records does not establish semantic uniqueness; duplicate content can remain.
The mutation stage deletes 581 and replaces 489 vectors, then the reload stage
rebuilds from the resulting saved index. Two independent constructions per
configuration exercise upstream random hierarchy choices.

The [selected configuration results](experiments/hnsw-m32-selected-2026-09.jsonl)
include the source SHA-256 and per-trial metrics. These measure agreement with
exact cosine retrieval, **not legal accuracy**, lawyer-query relevance, entity
merge correctness or corpus completeness.

| Stage | Mean recall@10, trial range | Worst query recall@10 | ANN median | Exact median |
|---|---:|---:|---:|---:|
| Fresh | 98.92–99.08% | 80% | 2.88–3.01 ms | 7.46–7.67 ms |
| Edited/deleted | 99.08–99.23% | 80% | 3.63–3.68 ms | 7.06–7.28 ms |
| Reloaded | 98.77–98.92% | 70–80% | 2.84–2.86 ms | 7.20–7.24 ms |

Build time was 14.59–14.91 s; save plus reload was 13.57–13.63 s. Timings are
observations on this machine, not guarantees. There are only 65 queries and
two trials, so the worst observed query is not a lower bound for future recall.

The initial mutation policy expanded search by every invalidated label, making
edited-index queries roughly 9.6–9.8 ms, slower than exact. Expansion now scales
by the invalidated fraction, with exact fallback if too few live candidates are
returned. This fixes the observed cost amplification without claiming complete
ANN retrieval. At M=16, retaining pruned neighbors helped a synthetic fixture
but some real-vector queries still recovered only 30%; expanding construction
candidates did not help and was discarded. M=32 improved the measured tails.
The intermediate experiment artifacts are retained beside the selected results.

For this product, use exact search for retrieval requiring complete results.
HNSW is an opt-in candidate accelerator, with current values and scores checked,
but it can omit relevant candidates. A graph loaded with an ANN build does not
thereby become suitable for automatic legal conclusions or exhaustive merging.

Reproduce the selected retrieval probe:

```powershell
$env:HNSW_M='32'
$env:HNSW_EFS='150'
$env:HNSW_TRIALS='2'
cargo run --release --features ann-hnsw --example hnsw_evaluate -- pilot-law-ro/kee-workdir/graph/vector_index/vectors.bin
```

Validation: 171 Rust tests without default features; 188 with all features;
strict core Clippy and workspace formatting; 591 Python/API/experiment tests
against each of the exact and isolated ANN wheels. The final synthetic recall
guard passed 25 independent hierarchy trials through fresh, edited and reloaded
states, without lowering its 90% aggregate threshold. Storage assertions use
direct or exhaustive access instead of assuming ANN top-k is complete.

The 96-document Romanian-law layout probe was rerun with the same source hashes:
all 70 comparisons still agree. The exact release wheel is installed for the GUI;
the selected ANN wheel was tested separately. The release CLI was rebuilt.
Existing PyO3 and Starlette deprecation warnings remain.

## Python as orchestration glue

The current native binding already puts graph operations, search, storage and
rebalance in Rust. Inspection found no `allow_threads` calls in the binding and
per-node calls in `grap_mcp/ingest.py`, `incremental.py` and KEE graph construction.
These are candidate boundaries to measure, not measured explanations of total
runtime. Releasing the GIL around pure Rust work may improve responsiveness;
batching may reduce conversion and call overhead. Neither establishes an
end-to-end speedup without profiling inference, I/O and engine time separately.

Prefer measured movement of cohesive hot loops into Rust over a wholesale
Python-to-Go/Rust orchestration rewrite. Keep domain policy and evidence
semantics covered by the same harness regressions across either boundary.
