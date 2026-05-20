# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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