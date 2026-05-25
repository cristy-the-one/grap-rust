# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `IndexedKnowledgeGraph::new_on_disk_with_merge_policy(segments_per_tier)` — convenient constructor for on-disk graphs with tiered merging enabled from the start.
- `grap-cli start-workers` subcommand — long-running process that keeps background merge/vacuum workers alive (very useful for demos and light production use).
- `--merge-policy <n>` flag to the interactive demo. When used, the demo automatically spawns a background worker process so real tiered merging can be observed during the run.
- `tests/persistence_roundtrip.rs` — regression test that verifies full node text + label round-trips after `save_to_dir`/`load_from_dir` (catches the previous blob offset corruption bug).

### Fixed / Hardened (Sharded + On-Disk)
- Globally-unique document IDs are now correctly maintained in the on-disk text index across flushes, merges, vacuum, and sharding.
- Sharded on-disk indexes now persist `node_map.json` so the mapping from global `NodeId` to (shard, local_doc) survives restarts.
- `ShardRouter::save_to_dir` is now atomic (uses per-file staging + rename with `manifest.json` last as the commit record).
- BM25 scoring, term frequency aggregation, and phrase search now work correctly when using sharded on-disk indexes.
- Deleted-node bitmaps are properly persisted and respected during search and vacuum in the on-disk backend.
- Fixed a critical blob offset serialization bug that caused nodes beyond the first chunk to return the wrong text after a persistence round-trip.
- Many safety improvements: overflow guards, bounds-checked `try_read_blob`/`try_node_label`, corrupt blob offset detection, proper error propagation instead of unwraps, etc.
- Large batch of minor correctness and robustness fixes (I1–I13, C1–C6, M1–M10) across the on-disk index, sharding layer, serialization, and query paths.
- All compiler warnings eliminated and test suite expanded.

### Changed
- The interactive demo is now idempotent: running it multiple times without `--reset` no longer duplicates documents.
- Windows persistence is significantly more reliable. Directory-level atomic rename was replaced by a per-file staging strategy (`.grap-new/`) that is far more tolerant of antivirus, file watchers, and concurrent access.
- `start_background_workers` + merge policy combination is now much easier to exercise and observe via the demo.

### Notes
- The combination of `fulltext + sharding + on-disk` is now substantially more mature and has survived extensive correctness hardening.
- Many previously partial or placeholder behaviors in the sharded on-disk path (global IDs, cross-shard search, persistence of mappings, atomic saves) are now properly implemented.

### Python Bindings (`pygrap`) & 0.3 Surface
- First-class, rich Python bindings for both `IndexedKnowledgeGraph` and `ShardedGraph` via PyO3 + maturin.
- `add_node(..., embedding=list[float])`, `add_edge(..., properties=dict)` with ergonomic Python dicts for properties.
- Full hybrid search: `search_and_traverse_hybrid(query, vector=..., HybridWeights, traversal_weight_property=...)`.
- Structure-aware graph traversal exposed to Python: `traverse_by_relation`, `predecessors_by_relation`, `neighbors_by_relation`.
- Rich result objects (`QueryResult`, `TraversalResult`, `MergeReport`, `ShardedQueryResult`) carrying text/vector/graph scores, depth, and full text.
- Context manager support (`with pygrap.IndexedKnowledgeGraph(...) as g:`) with automatic save.
- `vector_search(embedding, top_k)`, `merge_nodes`, `update_node`, `remove_node`, background workers, rebalancing, ghost-edge cleanup all exposed.
- Real-world demonstration: `real-data-law-ro/` — two-pass citation network over 2,092 Romanian legal acts using the new relation traversal APIs.
- Production-oriented drop-in templates added under `templates/pygrap-arbitrary-data*` (with Docker).

## [0.2.0] - 2026-05

### Added
- **`on-disk-index` feature** — Full segmented on-disk inverted index:
  - WAL for the active buffer
  - Tiered merge policy with background `MergeWorker`
  - Real incremental `vacuum()` / compaction (tombstone-aware, not just full merge)
  - `save_to_dir` / `load_from_dir` on `OnDiskIndex` and `OnDiskTextIndex`
- `IndexedKnowledgeGraph::new_on_disk()` + automatic backend detection on `load_from_dir` / `load_mmap`
- `ShardedGraph` / `ShardRouter` now support full persistence round-trips, including:
  - Per-shard on-disk text indexes
  - Persisted ghost edges (`ghost_edges.json`)
- `start_background_workers()` on `IndexedKnowledgeGraph` and `start_background_merge_workers()` on `ShardedGraph` for easy background compaction
- Richer `SegmentInfo` (live counts, approximate sizes, delete ratios) exposed via the `TextIndex` trait and `segment_distribution()`
- Real execution of `SegmentMigration` during rebalancing (bulk segment moves + compaction side-effects)
- New example: `cargo run --example on_disk_persistence --features "fulltext,on-disk-index,sharding"`
- New integration test covering sharded persistence + ghost edges + rebalance round-trips

### Changed
- `vacuum()` on on-disk indexes is now truly incremental instead of always doing a full compaction
- Background maintenance is now capable of driving real space reclamation

### Notes
- The combination of `fulltext + on-disk-index + sharding` is now the recommended path for large, persistent, horizontally scalable deployments.
- The documented 0.2 on-disk, deletes, sharded persistence, real compaction/vacuum, and background maintenance plan is complete. Nothing major remains from the original 0.2 roadmap.

## [0.1.0] - 2026-04

### Added
- **Atomic, crash-safe `save_to_disk`**: Graphs are now written to a temporary directory and atomically renamed into place. Readers will never see a partially-written graph.
- **Public `repair()` API**: `KnowledgeGraph::repair()` and `IndexedKnowledgeGraph::repair()` for best-effort recovery and diagnostics after interrupted saves.
- **Rich `RepairReport`**: Detailed diagnostic information including removed temp directories, file presence, version compatibility, and checksum verification results.
- **Observability (`Telemetry` trait)**: Pluggable metrics hook for tracking traversal cost, mask pruning, remote cache hits, and rebalance activity. Includes `NoopTelemetry`.
- **GraphFormatVersion**: On-disk format versioning with clear `UnsupportedVersion` errors.
- **Lightweight `load_meta()`**: Inspect persisted graphs without loading the full topology or blobs.
- **Expanded `GraphError`**: Rich error type covering I/O, versioning, corruption, sharding, and more.
- **Multi-platform CI**: Linux, macOS, and Windows testing, plus code coverage, scheduled benchmarks, and automated releases.
- **Developer Experience**: Dev Container support, `rust-toolchain.toml`, CONTRIBUTING.md, and PR template.

### Changed
- `save_to_disk` is now atomic (behavior change but same API).
- Many internal types (`Shard`, `GhostEdge`, etc.) are now `pub(crate)` for better encapsulation.
- `tempfile` moved from dev-dependency to regular dependency (required for atomic saves).

### Fixed
- Various documentation gaps across the public API.
- Improved robustness of distributed traversal and rebalancing.

### Notes
- MSRV is **1.81**.
- This is the initial public release of the `grap` crate after completing the architecture described in `core_graph_design.md`.

[0.1.0]: https://github.com/yourname/grap-rust/releases/tag/v0.1.0