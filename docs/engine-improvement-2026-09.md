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
Final traversal probe: 20 samples, 300 ms warmup, 1 s measurement. These are
synthetic attribution probes; the disk-adapter probe uses the active buffer and
does not measure physical disk I/O.

| Workload | Before | After |
| --- | ---: | ---: |
| 32-node chain, 1K-node arena | 2.83 µs | 3.25 µs |
| Same chain, 100K-node arena | 4.89 µs | 2.68 µs |
| Same chain, 500K-node arena | 36.35 µs | 2.65 µs |
| One text hit, 1K documents | 6.86 µs | 0.35 µs |
| One text hit, 10K documents | 64.74 µs | 0.35 µs |
| One text hit, 50K documents | 404.60 µs | 0.35 µs |

The small-graph traversal regresses about 0.42 µs (15%) in this measurement;
accepting that cost buys correct competing-path behavior and removes whole-arena
initialization from bounded queries. Large-graph speedups do not establish that
every traversal workload improves. Dense converging graphs can queue additional
routes, and hop-limited correctness may expand a node more than once.

Replacement with common terms remains about 2.5 µs at 1K documents and 19 µs at
10K: sorted posting vectors still shift on removal/insertion. The reverse term
map adds per-document memory. The string interner retains historical vocabulary
after deletion. These are explicit remaining tradeoffs, not claims of solved
mutation scaling.

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
