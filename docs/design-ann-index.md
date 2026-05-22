# Design: Approximate Nearest Neighbor (ANN) Index for grap

**Status**: Implemented (Phases 0–2 complete, post-review refinements applied)  
**Target**: 0.3 late / 0.4  
**Owner**: Core Engine Team

---

## 1. Executive Summary

**Problem**  
The current vector support in `grap` (introduced in 0.3) stores embeddings and can fuse them with text and graph signals inside `search_and_traverse_hybrid`. However, vector scoring is exact and brute-force: cosine similarity is computed only for nodes reached via graph traversal from BM25 seeds. This works for small graphs and heavily constrained traversals, but does not scale to the hundreds of thousands or millions of nodes that real RAG + knowledge graph workloads require.

**Solution**  
Add a first-class Approximate Nearest Neighbor (ANN) index that enables *sub-linear* vector similarity search. The ANN index will be used primarily to generate high-quality vector-based seeds (or candidate sets) that feed into the existing hybrid graph traversal + fusion engine.

**Key Recommendation (Phase 1)**  
- Use **HNSW** (Hierarchical Navigable Small World) as the initial ANN algorithm (in-memory).
- Introduce a `VectorIndex` trait modeled directly on the successful `TextIndex` trait.
- Each `IndexedKnowledgeGraph` (and therefore each shard) owns its own ANN index.
- For v1, the ANN index is **rebuilt on load** from the canonical embeddings stored in the `BlobStore` using an explicit `rebuild_vector_index()` method. This dramatically simplifies persistence and mutability while avoiding the pitfall of silently losing the index on reload.
- Primary integration: when a query vector is supplied to `search_and_traverse_hybrid`, the ANN index proposes the top vector-similar nodes as additional (or primary) seeds for graph traversal.

This design preserves `grap`'s architectural strengths (hot topology + cold blobs, sharding via ghost edges, property bitmask pruning) while adding the missing fast vector retrieval primitive.

---

## 2. Current State (Post 0.3 Core)

### 2.1 Embedding Storage
- Embeddings are stored as `BlobRef` via `BlobStore::store_embedding(&[f32])`.
- The `Node` struct contains `embedding: Option<BlobRef>`.
- `KnowledgeGraph::read_embedding(node) -> Option<Vec<f32>>` (and the wrapper on `IndexedKnowledgeGraph`) returns a safe copy.
- Embeddings participate in full persistence (single + sharded `save_to_dir` / `load_from_disk` / `load_mmap`).

### 2.2 Hybrid Search Today
- `search_and_traverse_hybrid(text_query, vector: Option<&[f32]>, ..., HybridWeights)` always starts with BM25 seeds from the `TextIndex`.
- For every node reached by graph traversal, if both a query vector and a node embedding exist, we compute `cosine_similarity` on the fly.
- The three signals (`text_score`, `vector_score`, `graph_strength`) are fused using `HybridWeights`.
- There is **no** independent vector candidate generation path.

**Limitation**: The vector signal can only influence ranking of nodes already reached via text + graph. Pure "find nodes similar to this embedding, then expand via relationships" is not efficient.

### 2.3 Abstraction Precedent
The `TextIndex` trait (`src/index.rs`) has proven extremely successful:
- In-memory `InvertedIndex`
- On-disk `OnDiskTextIndex` (segmented, with WAL, merge, vacuum)
- Clean separation of concerns
- Persistence hooks and rebalance support

We will follow the exact same pattern for vectors.

---

## 3. Goals & Non-Goals

### Goals (Must Have for v1)
- Sub-linear vector similarity search (target: 10k–1M+ vectors with < 50–100 ms latency for top-100).
- Seamless integration into `search_and_traverse_hybrid` so the three-way fusion story remains intact.
- Works with the existing sharded architecture (`ShardedGraph` / `ShardRouter`).
- Compatible with current persistence model (at minimum: correct rebuild on load).
- Supports `remove_node` (tombstone-aware) without catastrophic recall loss.
- Clean Rust + Python API surface consistent with recent 0.3 work (`QueryResult`, `HybridWeights`).
- Pluggable backend via trait (so we can later add quantization or different algorithms).

### Nice-to-Have (Phase 2+)
- Standalone `vector_search(embedding, top_k)` primitive.
- Configurable HNSW parameters exposed to users.
- Quantization (scalar or product) for memory reduction.
- Incremental HNSW updates (instead of full rebuilds).

### Explicit Non-Goals (for first version)
- True distributed ANN across multiple machines (in-process sharding only).
- DiskANN / Vamana / memory-mapped ANN structures (in-memory HNSW first).
- Learned indexes or hardware-specific accelerators.
- Automatic embedding generation or model hosting.

---

## 4. Algorithm Choice

**Recommendation: HNSW (Hierarchical Navigable Small World) for Phase 1**

### Why HNSW?
- Excellent recall/latency trade-off for moderate-to-high dimensional embeddings (128–1024 dims typical for modern models).
- Well understood, many high-quality open-source implementations (including pure-Rust crates such as `hnsw_rs` or `sprs` + custom, or a careful hand-rolled version for full control).
- Naturally supports incremental insertion (important for future live updates).
- Hierarchical structure gives good "navigation" properties that pair well with graph traversal.

### Comparison

| Algorithm     | Recall@100 | Build Speed | Query Speed | Incremental | Memory | Persistence Difficulty | Recommendation |
|---------------|------------|-------------|-------------|-------------|--------|------------------------|----------------|
| Brute Force   | 100%       | N/A         | Poor        | Easy        | High   | Trivial                | Baseline only |
| IVF-PQ        | Good       | Fast        | Very Fast   | Medium      | Low    | Medium                 | Phase 2 candidate |
| HNSW          | Excellent  | Medium      | Fast        | Good        | Medium | Hard (v1: rebuild)     | **Phase 1 choice** |
| DiskANN       | Very Good  | Slow        | Good        | Poor        | Low    | Very Hard              | Future (if mmap needed) |

**Quantization Decision**  
Do **not** include quantization in the first HNSW implementation. Add it in a follow-up phase once we have real usage data and recall requirements. This keeps the initial surface small.

---

## 5. Core Architecture

### 5.1 New Abstraction: `VectorIndex` Trait

We will introduce (in `src/vector_index.rs` or inside `index.rs` initially):

```rust
pub trait VectorIndex: Send + Sync + std::any::Any {
    fn add_vector(&mut self, node: NodeId, embedding: &[f32]);
    fn remove_vector(&mut self, node: NodeId);           // logical or physical
    fn search(&self, query: &[f32], max_results: usize) -> Vec<(NodeId, f64)>; // (node, similarity)

    // Stats, persistence hooks, rebalance hooks (modeled on TextIndex)
    fn stats(&self) -> VectorIndexStats;
    fn save_to_dir(...) -> Result<...>;
    // ... (take_sealed_*, add_sealed_*, etc. as needed later)
}
```

An in-memory HNSW implementation will be the first concrete type (`HNSWVectorIndex`).

### 5.2 Placement in `IndexedKnowledgeGraph`

```rust
pub struct IndexedKnowledgeGraph {
    pub graph: KnowledgeGraph,
    pub text_index: Box<dyn TextIndex>,
    pub vector_index: Option<Box<dyn VectorIndex>>,   // NEW
    ...
}
```

- `None` when no vectors have been added yet (or feature is disabled).
- Created lazily on first `add_node(..., embedding=Some(...))` or explicitly via a constructor.

### 5.3 Population Strategy (v1 Recommendation)

**Eager population + periodic full rebuild on load**

- On `add_node(text, label, Some(embedding))`:
  1. Store the embedding blob (existing behavior).
  2. Call `vector_index.add_vector(node_id, embedding)`.

- On load (`load_from_disk`, `load_mmap`, sharded load):
  - After the graph and text index are ready, **iterate all nodes that have embeddings** and rebuild the ANN index from scratch.
  - This is the simplest correct strategy and matches how many production systems start.

**Future optimization** (Phase 3+): Persist the HNSW graph structure itself (or use a crate that supports serialization) so rebuild is only needed after significant mutations.

---

## 6. Hybrid Search Integration (Most Important Decision)

### Recommended Strategy

**Use the ANN index to generate high-quality vector seeds when a query vector is supplied.**

In `search_and_traverse_hybrid`:

```rust
let mut seed_ids = vec![];
let mut seed_scores = ...; // text scores

if let Some(text_results) = self.text_index.search(...) {
    // existing BM25 seeds
}

if let Some(qvec) = vector {
    if let Some(vidx) = &self.vector_index {
        let vector_results = vidx.search(qvec, max_vector_seeds);
        // merge / dedup with text seeds, or keep separate and let graph traversal handle overlap
        // record vector_scores for fusion
    }
}

let all_seeds = combine_and_dedup(text_seeds, vector_seeds);
let graph_results = self.graph.find().from_nodes(&all_seeds)...;
```

Then, during result fusion, nodes that came from the vector index will have a strong `vector_score`.

### Why This Approach?
- Preserves the existing excellent graph traversal + bitmask pruning engine.
- The "expand via relationships" part of hybrid RAG remains graph-driven (the unique strength of `grap`).
- Simple to implement and reason about.
- Naturally supports mixed queries ("find nodes similar to this embedding **and** related via 'topic=X' edges").

Alternative (more complex): Use ANN for fast candidate generation *during* traversal. This is powerful but significantly harder to implement correctly with pruning and is deferred.

**Fallback**: If no `vector_index` is present (or the vector is `None`), behavior is identical to today.

---

## 7. Persistence & Lifecycle

**v1 Recommendation: Rebuild-on-load**

- The ANN index is an **auxiliary acceleration structure**, not the source of truth.
- The source of truth for embeddings remains the `BlobStore` (which already has excellent persistence, chunking, and mmap support).
- On every load we simply re-insert all embeddings that exist in the graph.

**Benefits**
- Extremely simple and correct.
- No need to invent a serialization format for the HNSW graph in the first version.
- Automatically consistent after any manual file editing or partial corruption.

**Storage Layout (proposed)**
- No new mandatory files for v1.
- Future (optional) sidecar: `vector_index/ann-hnsw.bin` or `vector_index/manifest.json + segments/` when we add persistence.

When sharded, each shard directory gets its own (optional) vector index directory, exactly like the `text_index/` directory.

---

## 8. Mutability & Deletes

Current `remove_node` on `ShardedGraph` / `IndexedKnowledgeGraph` already marks tombstones in the text index.

**For the ANN index**:
- `remove_vector(node)` will be called (logical removal).
- In HNSW this typically means:
  - Mark the node as deleted in a bitmap / tombstone set.
  - During search, skip deleted nodes (or accept that they may occasionally appear and filter afterward).
- Periodic full rebuild (triggered by `vacuum()` or size threshold) will physically clean the index.

This mirrors exactly how the on-disk text index handles deletes today.

---

## 9. Sharding & Rebalancing

- Each shard maintains an independent `VectorIndex`.
- When `execute_rebalance` moves a node from shard A to shard B:
  1. Remove the vector from A's ANN index.
  2. Add the vector to B's ANN index (after the node data has been moved).
- Because we start with rebuild-on-load, a full rebalance can also trigger a full rebuild of the affected shards' ANN indexes as a safe fallback.

This keeps the mental model simple: "the ANN index is always a derivative of the nodes currently owned by this shard."

---

## 10. API Surface (Rust + Python)

### Rust (new public methods on `IndexedKnowledgeGraph`)

```rust
pub fn vector_search(&self, embedding: &[f32], top_k: usize) -> Vec<(NodeId, f64)>;

pub fn search_and_traverse_hybrid(
    &self,
    text_query: &str,
    vector: Option<&[f32]>,
    ...
    weights: HybridWeights,
) -> Vec<CombinedResult<'_>>;
```

Configuration (later):
```rust
pub fn set_vector_index_params(&mut self, params: HNSWParams); // ef_construction, M, etc.
```

### Python (`pygrap`)

```python
g.add_node(text, label=None, embedding=[0.1, 0.2, ...])

results = g.search_and_traverse_hybrid(
    "rust ownership",
    vector=[0.12, ...],
    text_weight=0.3,
    vector_weight=0.5,
    graph_weight=0.2,
)

# Also:
vec_results = g.vector_search(embedding, top_k=20)
```

The existing rich `QueryResult` / `ShardedQueryResult` objects already carry `vector_score`, so no breaking changes there.

---

## 11. Phased Implementation Roadmap (as executed)

> **Note**: The phases below reflect what was actually delivered. Several important corrections and hardenings were identified during code review and are documented in section 11.5.

**Phase 0 – Foundation (small)**
- Define `VectorIndex` trait + `VectorIndexStats`.
- Brute-force reference implementation (for tests and fallback).
- Basic tests that the trait works with `IndexedKnowledgeGraph`.

**Phase 1 – Core ANN**
- In-memory HNSW implementation behind the trait.
- `add_vector` / `search` working with good recall.
- Integration into `IndexedKnowledgeGraph` (lazy creation).

**Phase 2 – Hybrid Integration**
- Modify `search_and_traverse_hybrid` to use the ANN for vector seeds.
- Implement `vector_search` primitive.
- Update Python bindings.

**Phase 3 – Production Polish**
- Sharded rebalance integration.
- Tombstone / delete handling + periodic rebuild logic.
- Persistence (optional sidecar format) or documented rebuild strategy.
- Basic parameter exposure (`ef_search`, etc.).

**Phase 4 – Scale**
- Quantization (scalar first).
- Benchmarks vs brute force across embedding sizes and graph densities.
- Optional persistent HNSW format.

---

## 11.5 Implementation Experience & Review Corrections

After the initial implementation of Phases 0–2, a detailed code review surfaced several important issues and improvements. These have been incorporated into the codebase.

### Critical Fixes
- **Vector index persistence (C2)**: The vector index was being silently discarded on every load path (`load_from_disk`, `load_from_dir`, `load_mmap`). This was fixed by introducing a `create_default_vector_index()` factory and an explicit `rebuild_vector_index()` method, which is now called after restoring the graph in all load paths. A regression test `vector_index_survives_save_load` was added.
- **Stats underflow (C1)**: `HNSWVectorIndex::stats()` used subtraction that could underflow when the deleted set was larger than the live point count. Changed to `saturating_sub()`.

### Important Hardening & Cleanups
- Added dimension-mismatch guards in `add_vector`.
- Improved HNSW search efficiency (only over-fetch when the deleted set is non-empty).
- Added proper `Debug` implementation and documentation for the `'static` lifetime on the HNSW inner struct.
- Replaced ad-hoc `#[cfg]` branching in `add_node` with the factory function.
- Made `vector_index` field `pub(crate)` instead of `pub`.
- Cleaned up seed collection logic in `search_and_traverse_hybrid` using named constants and iterator chains.
- Fixed documentation around `HybridWeights` (they are **not** automatically normalized).

### Efficiency Improvement
- `BruteForceVectorIndex::search` was changed from a full sort (`O(N log N)`) to a partial sort using `select_nth_unstable_by` (`O(N + k log k)`).

### Minor Polish
- Added an object-safety note to the `VectorIndex::load_from_dir` trait method documentation.

These changes significantly improved robustness, especially around the critical "index survives restart" guarantee.

---

## 12. Risks, Trade-offs & Open Questions

| Risk / Question                        | Recommended Direction                          | Notes |
|----------------------------------------|------------------------------------------------|-------|
| HNSW crate vs hand-written             | Evaluate 1–2 solid crates first; fall back to careful implementation if licensing or maintenance is painful | Prefer not to take on a large dependency unless it is high quality |
| Recall surprises in hybrid setting     | Provide good defaults + expose `ef_search`; document that hybrid fusion can compensate for imperfect recall | |
| Memory usage with many high-dim vectors| HNSW is reasonable; add quantization in Phase 4 | |
| Rebuild cost on large shards           | Make rebuild observable + background-friendly | Tie into existing worker infrastructure |
| Thread safety during search + concurrent add | Use `RwLock` or interior mutability carefully | HNSW searches are read-only after construction |
| Exact vs approximate in the same query | Always allow fallback to exact for small result sets or debugging | |

---

## 13. References & Prior Art

- HNSW paper (Malkov & Yashunin)
- `hnsw_rs`, `annoy`, `faiss`, `nmslib`
- Existing `TextIndex` + `OnDiskTextIndex` implementation in this codebase (strong precedent)
- Hybrid search patterns in Weaviate, Pinecone + Neo4j, and LlamaIndex knowledge graph retrievers

---

## 14. Next Steps (Post Implementation)

With Phases 0–2 complete and review corrections applied, the recommended next areas of work are:

1. **Proper persistence for the HNSW index** (Phase 3) — Move beyond pure rebuild-on-load for large graphs.
2. **Improved delete / compaction story** for the vector index (periodic physical rebuilds, better tombstone handling).
3. **Quantization support** (scalar first) to reduce memory usage.
4. **Benchmarks** comparing brute-force vs HNSW in both pure vector search and hybrid retrieval scenarios.
5. **Python ergonomics** — Consider a small `VectorSearchResult` dataclass or richer return type.

This design document (including the post-implementation review notes in section 11.5) is intended to remain the reference for architectural decisions.

**End of Design Document**