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
