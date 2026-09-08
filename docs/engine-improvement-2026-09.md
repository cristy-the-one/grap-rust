# Engine improvement pass — September 2026

The Rust engine owns graph reachability, path scores, index membership, and
retrieval limits. Python, MCP, extraction workflows, and the UI should build on
those guarantees rather than compensate for inconsistencies in them.

## Findings from the current implementation

- In-memory BM25 reindexing accumulates old frequencies and positions. Its delete
  hook is a no-op, and document count measures the highest slot rather than live
  documents. This makes updates and deletes change retrieval incorrectly.
- Indexed graph deletion leaves its vector behind; adding a first embedding via
  an update does not initialize the vector index.
- Traversal marks a destination visited on enqueue. A weak direct edge can
  suppress a stronger indirect path. Hop limits also require retaining weaker,
  shorter routes when they leave room to reach further descendants.
- Traversal allocates a visited array proportional to the whole graph and clones
  paths for every queued edge. Small bounded queries should allocate according
  to their explored region, and materialize paths only for returned nodes.
- Invalid/deleted seeds, duplicate seeds, zero limits, and unknown required
  properties need explicit behavior through the public query interface.
- Existing benchmarks emphasize static retrieval. Mutation correctness and
  competing-path traversal need representative regression coverage.

Baseline: `cargo test --lib --features fulltext,sharding`: 123 tests pass.
Existing user files are preserved. Work proceeds on `codex/engine-correctness-efficiency`.

## Implementation milestones

1. Repair in-memory index replacement/deletion and vector mutation consistency;
   verify through public interfaces and commit.
2. Repair bounded strongest-path traversal, use sparse exploration state and
   shared predecessor records, document filter/limit semantics, and commit.
3. Extend the engine benchmark harness and Python regression coverage, measure
   representative workloads, record results and remaining limitations, and commit.

No new infrastructure or storage format is needed for these changes. Existing
mmap and segmented-index work is retained. This pass does not imply the entire
repository has been exhaustively audited.

## Implemented engine contracts

`IndexedKnowledgeGraph` keeps text and vector membership synchronized when its
mutation methods are used. Both built-in text indexes replace a node's prior
document; in-memory BM25 counts live documents, including empty documents, and
tracks each document's term membership to avoid a vocabulary scan on deletion.
Direct writes through the public `graph` field still bypass index maintenance;
indexed callers must use the indexed mutation methods.

Traversal returns distinct live nodes, including valid seeds, with the strongest
product of effective edge weights within the hop limit. Weights must be finite
and between zero and one; edges with invalid effective weights are skipped.
Numeric `weight_by_property` values override edge strength, while `min_strength`
continues to gate the stored strength of each edge separately.

A popped route can suppress another only when it used no more hops. This allows
a weaker shortcut to expand after a stronger long route has reached the same
node. Scores never increase along an extension, so the first popped route is the
node's strongest result. Cycles terminate because a revisited node can only
expand at a strictly smaller depth. Sparse state tracks explored nodes; shared
predecessor records replace path cloning on every queued edge. Paths are built
only for returned results.

Zero limits return nothing. Invalid/deleted seeds are ignored; duplicate seeds
produce one result. No specified seeds still means all live nodes. Unknown
required properties return nothing, including through Python; an unknown
optional weight property retains the existing fallback to edge strength.
Equal-priority traversal entries use depth and stable ids to break ties, and
in-memory text results break score ties by node id.

`OnDiskTextIndex` maintains both directions of its node/document mapping on
writes and rebuilds the reverse direction once on load. The persisted format is
unchanged. Replacement tombstones the previous document, and limits are applied
after translating valid document ids into node ids.

## Measurements

Windows x64, local release build, Criterion. Baseline for traversal and adapter
lookup was captured before those implementations changed, after the first
index-mutation fix. Initial probe: 10 samples, 100 ms warmup, 200 ms measurement.
Final focused probes: 20 samples, 300 ms warmup, 1 s measurement. These are
synthetic attribution probes; the disk-adapter probe uses the active buffer and
does not measure physical disk I/O.

| Workload | Before | After |
| --- | ---: | ---: |
| 32-node chain, 1K-node arena | 2.83 µs | 2.31 µs |
| Same chain, 100K-node arena | 4.89 µs | 2.50 µs |
| Same chain, 500K-node arena | 36.35 µs | 2.52 µs |
| One text hit, 1K documents | 6.86 µs | 0.40 µs |
| One text hit, 10K documents | 64.74 µs | 0.36 µs |
| One text hit, 50K documents | 404.60 µs | 0.40 µs |

The existing `large_n_traversal` benchmark exposed redundant queue entries after
the initial correctness repair. Remembering a queued candidate's depth and score
allows safe pruning of dominated routes without discarding stronger or shorter
alternatives. This brought that workload back from roughly 19–21 µs to 10–12 µs.
Against a detached checkout of original commit `1e10009`, its final measurements
are 9.63 → 11.61 µs (10K nodes), 9.85 → 9.89 µs (50K), and 11.27 → 11.93 µs
(200K). These probes used 20 samples, 100 ms warmup, 200 ms measurement.
Thus correctness still costs about 0–2 µs on this converging workload; the
large-arena chain speedup is not a universal traversal speedup. Hop-limited
correctness may expand a node more than once.

Replacement with common terms measured 2.5–3.5 µs at 1K documents and 19–21 µs
at 10K across runs. That implementation was unchanged between these timing runs;
no mutation throughput improvement is claimed. Sorted posting vectors still
shift on removal/insertion. The reverse term map adds per-document memory. The
string interner retains historical vocabulary after deletion. These are explicit
remaining tradeoffs, not claims of solved mutation scaling.

## Harness and verification

- `cargo test --no-default-features`: 144 tests including doctests pass.
- `cargo test --all-features --tests --lib`: 150 tests pass, including mmap and
  HNSW feature coverage.
- Rust regression tests exercise replacement versus a fresh index, phrase
  positions, sparse node ids, empty documents, repeated deletes, vector updates,
  persisted replacement/load/vacuum, deterministic text ties, and query limits.
- Traversal is compared to an independent exhaustive bounded-walk calculation
  over 30 small cyclic graphs at five depth limits. Dedicated cases exercise
  stronger indirect paths, weaker shortcuts, numeric weights, and invalid input.
- Python regression tests run the actual rebuilt binding through mutations,
  persistence, deletion, relation traversal, and required-property filters.
- All 349 MCP/Python tests pass against the rebuilt local binding, without LLM
  services. The installed binding was preserved; the test process loaded an
  isolated build from `.logs/engine-python`.
- Targeted rustfmt checks and `git diff --check` pass. Strict Clippy is not green:
  a detached checkout of baseline `1e10009` reproduces the lint backlog (37
  errors versus 36 now). These include existing broad query argument lists,
  persistence style lints, and unused HNSW persistence methods. This pass does
  not hide those failures with lint suppression.

The original design and earlier efficiency assessments are historical evidence,
not proof of current behavior. In particular, their static traversal results
missed per-query arena allocation, and in-memory text benchmarks did not measure
the on-disk adapter's reverse-map rebuild.

## Next engine questions

1. Measure persisted, multi-segment retrieval on a representative corpus. The
   segmented scorer currently calculates term document frequency per segment;
   ranking invariance across flush/merge layouts needs its own correctness pass.
2. Define one phrase/proximity contract across both text backends. Positions are
   byte offsets; the memory scorer allows partial ordered matches and the disk
   scorer uses an unordered window. A zero gap is not an exact-phrase switch.
3. Evaluate update-heavy posting storage before adding more ANN or orchestration
   layers. Use mutation correctness as a prerequisite to throughput comparisons.
4. Apply corresponding strongest-path/depth reasoning to sharded traversal,
   which uses its own queue and has not been rewritten in this pass.

## Retrieval stability follow-up (2026-09-08)

Equal scores must not make a legal concept appear or disappear from a small
result set merely because the same graph was reopened. The exact vector backend
previously used hash-map iteration to break ties, including during partial
top-k selection. Sorting only the selected results would not fix the arbitrary
cutoff. Selection and final ordering now both use descending similarity, then
ascending node id. The existing O(N + k log k) selection strategy is retained.

Hybrid channel ranking, optional centrality ranking, and final fused ordering
also break equal-score ties by ascending node id. This is a reproducibility
rule for the same graph ids, not a claim that lower ids are more legally relevant.
The experimental HNSW backend still chooses approximate candidates; no identical
candidate-set guarantee is made for it or for independently rebuilt graphs with
different ids.

Reciprocal rank fusion now ignores repeated node ids within each channel and
assigns consecutive ranks to distinct hits. Previously `[A, A, B]` gave A two
votes and pushed B to rank 3; it now behaves like `[A, B]`. A result appearing in
different channels still receives the intended consensus benefit. Deduplication
uses a per-channel set with linear expected work and memory in channel size.

Validation reproduced three Rust failures before the fix and two failures in
the previously installed Python binding. Regression coverage exercises insertion
orders, cutoff ties, save/reload, duplicate votes, fused ties, and both RRF and
linear retrieval through the real binding. The full Rust suites pass: 150 tests
with no default features and 159 with all features, including doctests. The
release Python wheel was rebuilt and installed in the project `.venv`; the GUI
was restarted against it. All 587 Python/API/experiment tests pass with that
binding (one existing Starlette deprecation warning). Targeted rustfmt and
`git diff --check` pass. This change adds no model calls or persistence format.
