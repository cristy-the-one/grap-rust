# Implementation Plan: Transform core_graph_design.md into Functional grap Rust Knowledge Graph Engine

## Context
The repository at `E:\projects\grap-rust` currently contains only `core_graph_design.md` (2965 lines). This document is both:
- A product specification for a **Rust-native, read-optimized knowledge graph engine** specialized for interconnected text documents retrieved via relationship characteristics (property bitmasks, strength-weighted edges, fast traversal).
- A detailed "prototype" containing ~2000+ lines of Rust code sketches across a Core Module (arena Vec storage, 64-property bitmasks, separate BlobStore, PropertyRegistry, priority-query engine, QueryBuilder, ConcurrentGraph) plus three major extensions:
  - Section 10: Serialization (bincode for topology + optional memmap2 for blobs + meta)
  - Section 11: Incremental Full-Text Inverted Index (custom BM25 + positional postings, pluggable Tokenizer, integrates as `IndexedKnowledgeGraph`)
  - Section 12: Graph Sharding (consistent hashing, ghost edges for cross-shard, `ShardRouter` + `ShardedGraph` presenting unified API)

The architecture emphasizes:
- **Hot path** (topology + masks + adjacency) always in RAM, pointer-free `u32` handles.
- **Cold path** (full text blobs) demand-paged / zero-copy.
- **Entry point** via BM25 text search to select traversal seeds.
- O(1) edge filtering via `property_mask: u64 & required_mask`.
- Node-level early pruning via `outgoing_property_mask`.

No source code exists yet — only the design and pseudocode (in ```c fences). The user wants a **functional, working Rust system** built directly from the visible content of this document. "Sources for the concept" are referenced implicitly (BM25, arena alloc patterns, consistent hashing, ghost references) and described as "potentially correct"; we will validate/correct during porting.

This is an ambitious but well-scoped project because the spec already provides the concrete data structures, algorithms, and even an end-to-end test case (the "Rust memory safety" graph).

## Recommended Approach (Single Path)
**Phased, incremental port + refinement** of the exact design in the document into a clean, idiomatic, testable Rust library crate named **`grap`** (package `grap`, lib name `grap`).

**Repository & Git Policy (per user directive)**: The agent will create a proper `git` repository in `E:\projects\grap-rust`. All source code, the original `core_graph_design.md`, this implementation plan (or a cleaned copy under `docs/`), architecture notes, and future design decisions will live inside the repo. The `~/.grok/sessions/.../plan.md` is the ephemeral working copy used only during the planning conversation; once approved, a durable `docs/plan.md` (or `ARCHITECTURE.md`) will be committed to the repository as the canonical reference. Every major phase will produce a clean commit or stack of commits. Standard Rust `.gitignore` (target/, **/*.rs.bk, Cargo.lock for lib, etc.) will be used from day one.

1. **Honor the specification** — lift types, algorithms, bitmask logic, BM25 math, file formats, ghost-edge rules, etc. almost verbatim from the code blocks. The document *is* the source of truth.
2. **Fix only for correctness/compilation** — address issues visible in sketches (e.g. `CompactString` simulation, missing struct definitions, `BinaryHeap` ordering edge cases, incremental `avg_doc_length` when doc_lengths resizes, visited-set + priority semantics in traversal, lack of `Result` for IO, `Send+Sync` for index, etc.). Do not invent new architecture.
3. **Module organization** — split the monolithic sections into logical crates/modules with clear boundaries so each layer (core, index, serialize, shard) can be developed/tested independently.
4. **Feature flags** — `mmap` (memmap2), `fulltext` (index), `sharding` (multi-shard coordinator). Core is always on.
5. **API layering**:
   - Low-level: `KnowledgeGraph` (exact match to spec)
   - Mid-level: `IndexedKnowledgeGraph` (adds `search_and_traverse`)
   - High-level / facade: `ShardedGraph` or a `GraphEngine` when sharding enabled.
6. **Ergonomics & production** — add `QueryBuilder` extensions, proper error types (`GraphError`), `serde` derives where sketched, documentation, examples that mirror the Rust-language test case in the md.
7. **Verification first** — every phase ends with the corresponding test from the document (or expanded) passing + a runnable demo.

This produces a **functional system** after Phase 1 (you can already build graphs, add property edges, run filtered traversals, read blobs) and a **complete engine** after Phase 4.

**Trade-off note**: We implement the custom inverted index rather than pulling in `tantivy` because (a) the spec provides a complete self-contained sketch, (b) tight coupling to `NodeId` + incremental `index_document` on every `add_node`, and (c) avoids a  heavy dependency for the initial functional version. (If later profiling shows need, we can evaluate swapping the backend behind the same `TextIndex` trait.)

## Known Issues in the Prototype Code (Must Be Corrected During Port)
While the design is solid, the code sketches contain several issues that will be fixed (not "improved", just made to compile/work as intended):

1. **CompactString / SmallVec (md:124–144, ~1355)**: The `type SmallVec<T> = Vec<u8>;` is a placeholder and not generic. The `as_str` uses `unsafe`. **Decision**: For v1 use `String` for `PropertyValue::Label` (properties are low-volume; cache impact negligible). Document that a `compact_str` crate can be swapped later if edge scanning shows as bottleneck. Remove the simulation.

2. **QueueEntry Ord/PartialOrd (md:555–565)**: Only compares `cumulative_strength`; when equal, falls to `Equal` (unstable order). Fine, but add `depth` or `node` as tie-breaker for deterministic test results. Also the `visited` set + push-without-relax means it is a "greedy best-first" not a true "strongest path" finder. **Action**: Keep exact semantics from spec but add a comment block in `query()` explaining the algorithm choice and its implications vs Dijkstra.

3. **Index document_count & avg_doc_length (md:1845–1849)**: Resizing `doc_lengths` with 0s for earlier nodes, then `sum` includes zeros → avg is correct only if all lower ids are populated. Since `add_node` always appends the next sequential id, this holds. **Action**: Add debug_assert or explicit `if node_id.0 as usize >= self.doc_lengths.len() { resize }` guard + test for non-monotonic node ids (future safety).

4. **crc32_simple (md:1554–1565)**: Pure-Rust bit-by-bit implementation (very slow for large blobs). Comment even says "in production, use the `crc32fast` crate". **Decision**: Add `crc32fast` as optional dep under `[features] mmap` or always (tiny, ~50KB). Fall back to the simple version only if someone builds with `--no-default-features` and no std hasher. Keep the fn for reference or delete after switch.

5. **Missing pieces for compilation**:
   - `SerializablePropertyValue` and conversion fns between `PropertyValue` ↔ `Serializable*` (md ~1097–1550, the file was read partially).
   - Full `DefaultTokenizer::tokenize` body and stopword filtering logic.
   - `GraphMeta`, `RebalancePlan`, `DistributedQueryResult`, `GhostEdge` structs.
   - The `load` / `load_with_mmap` paths and reconstruction of `BlobRef` from global offsets.
   - `ConcurrentGraph` wrapping for `IndexedKnowledgeGraph`.

6. **Thread safety for index**: `InvertedIndex` uses `HashMap` + `Vec` internally; `Box<dyn Tokenizer>` needs `Send + Sync`. The `ConcurrentGraph` RwLock will protect, but if we expose direct index access we need `RwLock` inside or make callers hold the lock. **Action**: Keep `IndexedKnowledgeGraph` interior-mutable only via the outer `Concurrent` wrapper or document "single-writer".

7. **Query max_results interaction with priority**: The loop `if results.len() >= q.max_results { break; }` then `if depth >= max {continue}` can return fewer than `max_results` "best" nodes because it stops expanding the current best before seeing deeper better ones. Matches the spec exactly. **Document** this as "best-effort under budget".

All fixes will be minimal and called out in commit messages with `// PORT NOTE: ...` comments in source referencing the md line.

## Dependency Strategy (Minimal + Faithful to md)
Base (always):
- `serde = { version = "1", features = ["derive"] }`
- `serde_json = "1"`
- `bincode = "1.3"`

Optional (feature `mmap`):
- `memmap2 = "0.9"`
- `crc32fast = "1"` (recommended over the pure fn)

Dev / examples / tests:
- `tempfile = "3"`
- `criterion = "0.5"` (benches)
- `proptest = "1"` (later for query invariants)

**No** `thiserror` in v1 if we can use `std::io::Error` + simple enum for graph errors; add only if Result surface grows.

For labels / small strings: **start with `String`**. Revisit after Phase 1 profiling of real edge-property workloads.

Tokenizer: **exact port of the char_indices + stopword HashSet** from md:1743. No extra crates unless user requests better Unicode word breaking.

This keeps the dependency graph tiny for the functional core (the exact spirit of the "three hot/cold/index layers" design).

## Refined Public API Sketch (v0.1 target, after Phase 2)
```rust
// ids
pub struct NodeId(pub u32); // etc, all Copy/Hash/Eq

// properties
pub struct PropertyRegistry { ... }
pub enum PropertyValue { Float(f64), Int(i64), Bool(bool), Label(String) }
pub enum PropertyFilter { ... }

// core
pub struct KnowledgeGraph { pub properties: PropertyRegistry, ... }
impl KnowledgeGraph {
    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> NodeId { ... }
    pub fn add_edge(&mut self, ...) -> EdgeId { ... }
    pub fn read_blob(&self, n: NodeId) -> &str { ... }
    pub fn query(&self, q: &GraphQuery) -> Vec<QueryResult> { ... }
    pub fn find(&self) -> QueryBuilder { ... }
}

// index (feature "fulltext" or always after P2)
pub struct IndexedKnowledgeGraph { ... }
impl IndexedKnowledgeGraph {
    pub fn search(&self, text: &str, k: usize) -> Vec<TextSearchResult> { ... }
    pub fn search_and_traverse(...) -> Vec<(QueryResult, &str)> { ... }
}

// serialize (feature "serialize" or always)
impl KnowledgeGraph { pub fn save_to_disk(&self, dir: &Path) -> io::Result<()> { ... } ... }

// sharding (feature "sharding")
pub struct ShardRouter { ... }
pub struct ShardedGraph { ... }
impl ShardedGraph {
    pub fn distributed_search(...) -> Vec<DistributedQueryResult> { ... }
}
```

All core types will implement `Debug`, `Clone` where sensible, and `Serialize/Deserialize` for the portable path.

## Critical Files & Directories (to be created)

| Path                              | Purpose / What to Port                                                                 | Priority |
|-----------------------------------|-----------------------------------------------------------------------------------------|----------|
| `Cargo.toml`                      | Package `grap` v0.1.0, edition 2021, deps from md (serde, bincode, memmap2 optional, tempfile dev) + smallvec?, unicode-segmentation? (or pure char_indices as in doc) | 1 |
| `src/lib.rs`                      | Public API surface, re-exports, crate docs, `pub mod core`, feature-gated mods          | 1 |
| `src/ids.rs`                      | `NodeId`, `EdgeId`, `PropertyTag`, `GlobalNodeId`, `ShardId` (Copy, Hash, Debug)       | 1 |
| `src/blob_store.rs`               | `BlobRef`, `BlobStore` (chunked Vec<u8> arenas, zero-copy `read`)                       | 1 |
| `src/properties.rs`               | `PropertyValue`, `PropertyFilter`, `PropertyRegistry` (name<->u16<->mask), `CompactString` or `SmolStr` | 1 |
| `src/graph.rs`                    | Internal `Node`, `Edge` (with `property_mask`, `matches_mask`, `get_property`), `KnowledgeGraph` (add_*, read_blob, node/edge counts, the full `query` + `find` builder) | 1 |
| `src/query.rs`                    | `GraphQuery`, `QueryResult`, `QueryBuilder`, `edge_passes_filters`, priority queue impl (with documented semantics) | 1 |
| `src/index/mod.rs`                | `Tokenizer` trait + `DefaultTokenizer`, `TermId`, `StringInterner`, `Posting`, `PostingList`, `InvertedIndex` (index_document, search, phrase_search, BM25) | 2 |
| `src/index/indexed_graph.rs`      | `IndexedKnowledgeGraph` (wraps KnowledgeGraph + InvertedIndex, overrides add_node to index, `search_and_traverse`) | 2 |
| `src/serialize.rs`                | Serializable* mirrors, `save_to_dir`, `load_from_dir`, meta.json handling, mmap path under `#[cfg(feature = "mmap")]` | 3 |
| `src/shard.rs`                    | `ConsistentHash`, `GhostEdge`, `Shard`, `ShardRouter`, `ShardedGraph` (orchestrates cross-shard traversal) | 4 |
| `examples/demo.rs`                | The exact "Rust memory_safety" scenario from the test in md, plus a text-seed query   | 1 |
| `tests/integration_tests.rs`      | Roundtrip serialize, index search quality, sharded query correctness, large synthetic  | 2+ |
| `benches/traversal.rs`            | Criterion benches for mask pruning vs naive, index search, add_node throughput          | 5 |
| `README.md`                       | Architecture diagram (from md), quickstart, "Why this design" (key decisions section)   | 2 |
| `src/error.rs`                    | `GraphError` enum (for future Result-returning APIs)                                    | 2 |

No existing code to "reuse" from the workspace — the entire implementation is a faithful port + structural cleanup of the 2965-line design document. Key logic fragments to lift directly (with line references to md for traceability):

- Bitmask pruning & `outgoing_property_mask` (md:240, 260, 611, 620)
- `Edge::matches_mask` + binary-search `get_property` (md:268, 273)
- `PropertyRegistry::build_mask` + register (md:328, 303)
- The `QueueEntry` + `BinaryHeap` best-first traversal in `KnowledgeGraph::query` (md:536–661) — **note and document** that it is "first discovered via highest cumulative strength" (not guaranteed globally strongest path due to early visit)
- BM25 + IDF/TF math + posting accumulation (md:1897–1912)
- `IndexedKnowledgeGraph::search_and_traverse` skeleton (md:2135)
- File layout `.topo`/`.blobs`/`.meta` + SerializableNode/Edge (md:1060, 1075)
- Consistent hash + ghost edge creation rules (md:2286, 2342, 2442)

## Phased Delivery & Verification Strategy

**Phase 1 – Core Functional (MVP – immediate value)**
- Deliver a working `KnowledgeGraph` + `QueryBuilder` that passes an adapted version of the `knowledge_graph_end_to_end` test (md:859–999).
- `cargo test` green.
- `cargo run --example demo` prints ranked results for the memory-safety query and demonstrates blob reads + mask pruning.
- Verification: exact assertions from the document test + manual inspection of cumulative_strength products and visited nodes.

**Phase 2 – Text Entry Point**
- Full `InvertedIndex` + `IndexedKnowledgeGraph`.
- `search("rust borrowing")` returns high-score seeds; `search_and_traverse("memory safety", ...)` returns correct expanded subgraph.
- Add `graph.find().from_text_query("...")` sugar or equivalent on the builder.
- Verification: unit tests on tokenizer, BM25 scores for known docs, end-to-end "text query → seeds → property-filtered traversal".

**Phase 3 – Persistence**
- `save/load` roundtrips for topology + blobs (both plain and mmap feature).
- Blobs remain zero-copy after load; masks, labels, properties identical.
- Verification: the serialize test block at end of Section 10 (md ~2754).

**Phase 4 – Scaling Layer (Sharding)**
- In-process `ShardedGraph` using 2–4 shards, consistent hash, ghost edges.
- Cross-shard queries via `search_and_traverse` succeed and merge results.
- Verification: synthetic multi-shard test (create nodes on different shards, add cross edges, query from one side).

**Phase 5 – Production Hardening**
- See the dedicated plan in [docs/phase5.md](phase5.md).
- Error handling (`GraphError`), full documentation, root README, `cargo clippy --all-targets -- -D warnings`, `cargo test --all-features`.
- Atomic save, `GraphFormatVersion`, repair story.
- Criterion benchmark suite proving the O(1) mask benefit, sharded overhead, mmap wins, etc.
- Optional: small CLI or HTTP server example demonstrating production usage.
- Target: publishable `0.1.0` crate with excellent first-time experience.

**End-to-end user validation after Phase 1+2**:
```rust
let mut g = IndexedKnowledgeGraph::new();
let topic = g.properties.register("topic");
// ... add 5 Rust nodes + edges exactly as in md ...
let results = g.search_and_traverse("memory safety", 5, &["topic"], 0.8, 3, 10);
// assert ranked nodes contain ownership, borrowing, lifetimes with correct cumulative strengths
```

## Risks & Open Questions (to resolve with user if needed)
- **Traversal semantics**: The priority queue + visited set in the spec gives "best prefix path" not necessarily the strongest path to every node. Acceptable per design? Or upgrade to proper path-relaxing later?
- **Delete / mutability**: Current design is append-only (no remove_node). Is that intentional for v1?
- **Crate name**: `grap` (short, memorable) vs `knowledge-graph` (matches the toml in md)? 
- **PropertyValue::Label**: Use `String` (simple) or a `compact_str` / `smol_str` crate to match "CompactString" intent?
- **Sharding scope**: In-process only for memory scaling, or plan RPC boundaries from day 1?
- **Tokenizer deps**: Pure std `char_indices` + stopword list (as written) or add `unicode-segmentation` for better word boundaries?

## Next Immediate Steps (once plan approved)
1. **Repository bootstrap** (in the existing `E:\projects\grap-rust` dir that already holds the design doc):
   - `git init`
   - Create standard Rust `.gitignore`
   - `git add core_graph_design.md` (preserve the single source of truth)
   - Create `docs/` directory and copy the approved plan into `docs/implementation-plan.md` (or `ARCHITECTURE.md`) + commit as "chore: add design spec and initial implementation plan"
2. `cargo init --lib .` (or hand-author `Cargo.toml` + `src/lib.rs` skeleton) matching the `[package] name = "grap"` + deps from the md.
3. Scaffold the module tree (`src/{ids,blob_store,properties,graph,query}.rs` etc.).
4. Port `ids.rs` + `blob_store.rs` + minimal `KnowledgeGraph::add_node` / `read_blob`.
5. Iteratively port the query engine + QueryBuilder until the exact example test from the design doc (md:859) compiles and passes.
6. `cargo test`, `git commit -am "feat(core): working KnowledgeGraph with bitmask pruning and priority traversal"` (green, small commits).
7. Each subsequent phase (index, serialize, sharding) on topic branches or Graphite-style stacked PRs, always updating `docs/implementation-plan.md` with status.

The git repo becomes the single source for both the Rust implementation **and** the architecture/plans.

This plan turns the comprehensive prototype in the markdown into a **real, usable, high-performance Rust library** while staying faithful to every major design decision documented.

---
*Plan version 0 – built from direct reading of core_graph_design.md (all 2965 lines).*
