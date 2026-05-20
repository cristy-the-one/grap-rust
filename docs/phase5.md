# Phase 5 – Production Hardening & v0.1 Release Readiness

**Status**: In Progress (started after Phase 4 completion)  
**Target**: `grap` crate at version `0.1.0` — a clean, documented, tested, and benchmarked Rust library ready for real use and crates.io publication.

This phase shifts focus from "make it work" to "make it reliable, observable, and pleasant to use in production environments."

---

## 1. Goals

1. **Zero warning, zero surprise** codebase
   - `cargo clippy --all-targets -- -D warnings` passes cleanly on stable.
   - All public types, methods, and modules have high-quality rustdoc (with examples where useful).
   - `cargo test --all-features` is green and fast.

2. **Robust persistence layer**
   - Explicit `GraphFormatVersion` with clear compatibility rules.
   - Atomic save (temp file + rename) to survive crashes mid-write.
   - Optional repair / recovery path for partially written files.
   - Clear documentation of what is forward/backward compatible.

3. **Observability & introspection**
   - Rich statistics exposed for both single-graph and sharded deployments.
   - Extension points for metrics (Prometheus-style counters/histograms) and structured logging without forcing a specific backend.
   - Visibility into ghost-edge density, remote cache hit rate, rebalance cost, etc.

4. **Validated performance claims**
   - Criterion-based benchmark suite that proves the key architectural bets:
     - O(1) property bitmask pruning actually helps.
     - Sharding + ghost edges adds acceptable overhead.
     - mmap path is faster on cold cache for large blobs.
   - Published numbers (or at least reproducible commands) in the README.

5. **Excellent first-time experience**
   - Outstanding root `README.md` with quick start, architecture overview, feature matrix, and "when to use grap".
   - Polished examples that mirror real usage (hybrid search, sharded cluster, mmap persistence).
   - Clear MSRV policy (currently 1.81) and stability promise for 0.1.x.

---

## 2. Work Tracks

### Track A: Code Quality & API Polish

- Expand `GraphError` (in `src/lib.rs`) into a proper, non-exhaustive, `thiserror`-style or hand-written enum covering:
  - IO errors during save/load
  - Checksum / corruption
  - Version mismatch
  - Sharding configuration errors
  - Property registry conflicts
  - Make as many public methods return `Result<_, GraphError>` as is reasonable (especially `save_to_disk`, `load_*`, `add_node`/`add_edge` in sharded context if they can fail).
- Add `#![warn(missing_docs)]` (or deny on CI) and fill every gap.
- Remove all `#[allow(...)]` that are no longer needed; justify the ones that remain.
- Audit `pub` surface: hide implementation details (`Shard`, `ShardRouter` internals, `CachedRemoteNode`, `Serializable*` types) behind `pub(crate)` or `pub(super)` where possible.
- Consistent naming and constructor patterns across `KnowledgeGraph`, `IndexedKnowledgeGraph`, `ShardedGraph`.
- Add `#[must_use]` and `#[deprecated]` where appropriate.
- Ensure `Clone`, `Debug`, `Default`, `PartialEq` etc. are derived or implemented thoughtfully on all public types.

**Exit criteria**: `cargo clippy --all-targets -- -D warnings` + `cargo test --all-features` + `cargo doc --no-deps --all-features` succeed with zero warnings.

### Track B: Persistence Hardening & Versioning

- Introduce `GraphFormatVersion` (u16 or enum) stored in `GraphMeta`.
  - v1 = current layout (topology + blobs + meta with CRCs).
  - Document rules: "v1 readers will refuse v2+ files; v2 readers will accept v1 with a compatibility path."
- Make `save_to_disk` atomic:
  - Write to `graph.tmp.{pid}.XXXX` (or use `tempfile` + `persist`).
  - `fsync` data + directory.
  - Rename into place.
- Add a `repair` or `try_recover` function (best-effort) that can at least detect partial writes and report what can be salvaged.
- Store a small "features used" bitfield in meta (did we ever use fulltext? sharding? mmap?) so future loaders know what indices they must rebuild.
- Update `load_mmap` and `load_from_disk` to return rich `GraphError` on version or checksum failure instead of generic IO errors.

**Design note from core_graph_design.md**: The three-file layout + CRC32 was chosen precisely to enable fast detection of corruption after a crash. Phase 5 makes the "write side" match the "detection side" in robustness.

### Track C: Observability & Telemetry Hooks

- Enhance existing stats types:
  - `ShardedGraphStats` / `ClusterStats` already exist — make them more complete (remote cache size & hit rate, avg ghost fan-out, last rebalance timestamp if we track it).
  - Per-shard `IndexStats` already good; expose query cost counters (nodes visited, edges considered, masks pruned, etc.) from the last `search_and_traverse` call (or via a `QueryStats` sidecar struct).
- Add optional feature `metrics` (or just document the pattern) that lets users inject counters via a small trait:
  ```rust
  pub trait Telemetry: Send + Sync {
      fn record_traversal_nodes_visited(&self, n: u64);
      fn record_mask_prune(&self, pruned: u64);
      fn record_remote_fetch(&self, hit: bool);
      ...
  }
  ```
  Default implementation is a no-op.
- Structured logging via `tracing` (optional feature) or at least `log` with clear event names. Keep the crate `no_std` friendly if possible (or document the std requirement clearly).
- Add cheap "debug" counters behind a feature flag (`debug-counters`) that can be turned on in tests/CI without affecting release builds.

### Track D: Benchmark Suite & Performance Validation

Create `benches/` (using `criterion`) with at least the following scenarios, directly motivated by the architecture claims in `core_graph_design.md`:

1. **Bitmask Pruning Benefit** (the big one)
   - Build a graph with 10k–50k nodes, realistic property diversity (8–20 properties).
   - Run identical traversals with and without property filters.
   - Measure nodes visited and wall time. Goal: demonstrate the O(1) mask early-out effect.

2. **Sharded vs Monolithic**
   - Same dataset loaded as 1 shard vs 4 shards.
   - Distributed `search_and_traverse` (with real ghost following) vs single-shard version.
   - Measure latency and total work (nodes expanded, cross-shard fetches).

3. **Mmap Cold vs Hot Cache**
   - Large blobs (average 2–8 KiB).
   - First query after `load_mmap` (many page faults) vs subsequent queries.
   - Compare against plain `load_from_disk` (everything in RAM).

4. **Text + Graph Fusion Cost**
   - `search_and_traverse` with different `FusionWeights` (text-heavy, balanced, graph-heavy).
   - Show that BM25 seed selection + pruned traversal stays fast even when text index is large.

5. **Rebalance Cost**
   - Time to compute `plan_rebalance` + time to `execute_rebalance` on a 4-shard cluster when adding a 5th shard.
   - Measure ghost-edge churn.

**Output**: A `benches/README.md` or section in the root README that shows representative numbers (or commands to reproduce) and explains why the architecture wins.

We will add `criterion` under `[dev-dependencies]` and a `[[bench]]` section in `Cargo.toml`.

### Track E: Documentation & Packaging

- **Root `README.md`** (new):
  - One-paragraph "what is grap and why".
  - Quick start (add 5 nodes + edges, run hybrid query).
  - Feature matrix table (`fulltext`, `mmap`, `sharding`).
  - Architecture diagram (mermaid or ASCII art of the three layers + sharding).
  - Performance claims + benchmark commands.
  - MSRV, stability, and "contributing" section.
  - Link to `core_graph_design.md` for the curious and `docs/implementation-plan.md` + `docs/phase5.md`.

- Polish `docs/implementation-plan.md` to mark Phase 4 complete and Phase 5 active with link to this file.

- Update `Cargo.toml`:
  - `readme = "README.md"`
  - Good `description`, `keywords`, `categories`, `license`, `repository` (once published), `rust-version`.
  - `exclude` or `include` for docs and examples.

- Add a `CHANGELOG.md` (keep it simple for 0.1).

- High-quality rustdoc on the three main facades:
  - `KnowledgeGraph`
  - `IndexedKnowledgeGraph`
  - `ShardedGraph`

---

## 3. Concrete Milestone Checklist (for tracking)

- [ ] `GraphError` is the single error type used everywhere meaningful
- [ ] All public items documented; `#![warn(missing_docs)]` clean
- [ ] `cargo clippy --all-targets -- -D warnings` passes
- [ ] `cargo test --all-features` passes (including the big integration test in `shard.rs`)
- [ ] Atomic save implemented and tested (crash-during-write scenario via `tempfile` + rename)
- [ ] `GraphFormatVersion` introduced + loader rejects unknown future versions gracefully
- [ ] Criterion bench suite exists with the 5 scenarios above
- [ ] Representative benchmark numbers captured (even if just "on my machine")
- [ ] Root `README.md` written and reviewed
- [ ] `ShardedGraph` / router stats are rich enough for real operations
- [ ] Optional: tiny CLI binary example (`examples/grap-cli` or separate crate later)
- [ ] Optional: minimal HTTP server example using `axum` + `ShardedGraph` (shows horizontal scaling story)

**Definition of Done for Phase 5**:
The crate can be published as `0.1.0` with confidence. A new user can `cargo add grap`, follow the README, and get a fast, correct, observable knowledge graph that survives restarts and can be scaled across shards with understandable cost.

---

## 4. Risks & Open Decisions

- Do we take `thiserror` as a dependency for `GraphError`? (Small, popular, or hand-roll for zero deps.)
- Should `tracing` be optional or do we just use `log` + document how to hook metrics?
- Rebalancing today is "stop-the-world" for the in-process router. Do we need (or want) a live migration story for 0.1, or is "plan + execute while quiesced" acceptable?
- How much of the remote cache / ghost following logic should be tunable (TTL, max cache size, eviction policy)?
- Is the current "best-first with visited set, no relaxation" traversal semantics acceptable forever, or do we later add a true "strongest path" mode?

These will be resolved explicitly before the 0.1 release tag.

---

## 5. Relationship to Earlier Phases & Design Document

Phase 5 does **not** add major new algorithms. It hardens, documents, and measures everything built in Phases 1–4 (core + bitmasks, full-text index, persistence + mmap, sharding + ghost edges + rebalancing).

All decisions continue to be traceable to `core_graph_design.md` (especially the production concerns discussed around serialization, crash safety, monitoring, and the benchmarking scenarios implied by the O(1) mask claim and the sharding section).

After Phase 5 we consider the **initial vision complete**. Future work (0.2+) may include:
- On-disk index for the inverted index (currently rebuilt in RAM)
- True multi-process / RPC sharding (the current `ShardRouter` is in-process only)
- Delete / mutable operations with proper ghost cleanup
- pluggable storage backends

---

## 6. Next Immediate Actions (after this document is reviewed)

1. Finish any remaining clippy / warning fixes that were identified at the start of the phase (unused imports, borrow issues in `fetch_remote_node`, etc.).
2. Expand `GraphError` and convert key fallible operations.
3. Write the root `README.md`.
4. Add `criterion` and the first two benchmark scenarios (mask benefit + sharded overhead).
5. Implement atomic save + `GraphFormatVersion`.
6. Run the full benchmark suite and capture results.
7. Final documentation sweep + 0.1.0 release preparation.

---

*Phase 5 plan derived from the production, versioning, and benchmarking concerns expressed throughout `core_graph_design.md` (Sections 10, 12, 13–15 spirit) and the explicit Phase 5 bullet list in `docs/implementation-plan.md`.*

**Current version of this plan**: 1 (initial creation)
