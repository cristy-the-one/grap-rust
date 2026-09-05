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
