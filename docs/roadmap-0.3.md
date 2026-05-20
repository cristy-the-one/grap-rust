# grap 0.3 Roadmap

**Status**: In Progress (Core complete, ANN next)  
**Target**: 0.3.x / early 0.4 (2026)

---

## 1. Vision

**0.2** made `grap` *production-scale and durable*.  
**0.3** will make it *obviously more powerful than either a pure vector database or a pure graph database*.

The unique promise of `grap` has always been the tight integration of three retrieval paradigms in one engine:

- **Symbolic / structural** — property-weighted graph traversal with aggressive bitmask pruning
- **Statistical text** — BM25 + phrase search with segmented on-disk indexes
- **Statistical vectors** — dense embeddings with hybrid fusion

0.3 delivered the foundational vector layer and first-class Python access so that users can actually experiment with this three-way hybrid retrieval. The next major step is making vector search *fast* at scale via an Approximate Nearest Neighbor (ANN) index.

---

## 2. High-Level Goals

| Goal                        | Description                                                                 | Success Criteria |
|----------------------------|-----------------------------------------------------------------------------|------------------|
| **Vector Storage Foundation** | Store embeddings using the same `BlobRef` / `BlobStore` discipline as text | Embeddings survive save/load/mmap/sharding roundtrips cleanly |
| **Hybrid Retrieval API**   | `search_and_traverse_hybrid(text, vector, HybridWeights)`                   | Users can meaningfully tune the three signals and get intuitive results |
| **First-class Python**     | `pygrap` exposes the full power of the engine (single + sharded)            | Real workloads can be written in Python without fighting the API |
| **Fast Vector Search**     | ANN index that integrates cleanly with the existing hybrid query engine     | Sub-linear vector search on graphs with 100k–1M+ nodes |
| **Hybrid Ergonomics**      | Pleasant Python experience for hybrid RAG-style workloads                   | Rich result objects, easy weight tuning, good defaults |

---

## 3. What 0.3 Delivered

### Completed in this phase

- **Python Bindings (`pygrap`)** — Full surface for both single-graph and sharded graphs:
  - `IndexedKnowledgeGraph` and `ShardedGraph` as first-class citizens
  - Context managers (`with` blocks) with automatic `save()`
  - `add_node(..., embedding=list[float])` on both classes
  - `search_and_traverse_hybrid(...)` with `HybridWeights`
  - Rich result objects (`QueryResult`, `ShardedQueryResult`) containing `text_score`, `vector_score`, `graph_strength`, `fused_score`, depth, and text
  - `stats()`, `remove_node()`, ghost-edge cleanup, rebalancing, background workers

- **Vector Storage Architecture**
  - Embeddings stored as `BlobRef` (consistent with text blobs)
  - `store_embedding()` / `read_embedding()` on `BlobStore`
  - `Node` carries `embedding: Option<BlobRef>`
  - Full persistence roundtrips (including sharded `node_map.json` + per-shard blobs)
  - Alignment-safe deserialization (`Vec<f32>`)

- **Hybrid Search Engine**
  - New `HybridWeights { text, vector, graph }` (distinct from legacy `FusionWeights`)
  - `search_and_traverse_hybrid()` on `IndexedKnowledgeGraph`
  - Hybrid path wired through `ShardRouter` (per-shard hybrid + global merge)
  - Cosine similarity fusion during traversal

- **Demonstration**
  - `examples/hybrid_search.rs` now shows real vector ingestion + tunable hybrid fusion
  - Clear separation of the three scoring signals in output

The vector layer was deliberately built on top of the existing cold-blob architecture rather than bolting embeddings onto hot graph structs. This preserves the "hot topology + cold content" design that has served the engine well.

---

## 4. Priority Roadmap

### P0 – Next Major Focus (User Priority)

#### 1. ANN Index (Approximate Nearest Neighbor)

This is the highest-leverage missing piece for making vector search practical at scale.

**Current State (post 0.3 core)**
- We have exact vector scoring inside `search_and_traverse_hybrid` (cosine computed on-the-fly for nodes that have embeddings).
- This is fine for small-to-medium graphs or when the graph traversal already heavily prunes the candidate set.
- Brute-force cosine becomes the bottleneck once you have tens or hundreds of thousands of nodes with embeddings.

**0.3 / 0.4 Goals**
- A production-quality in-memory ANN index (most likely **HNSW** or a high-quality IVF + PQ implementation).
- Seamless integration into the hybrid query path (`search_and_traverse_hybrid` should be able to use the ANN index for the vector component).
- Optional standalone `vector_search(embedding, top_k)` / `hybrid_vector_search(...)` APIs.
- The ANN index must respect the sharded architecture (each shard maintains its own ANN index).
- Persistence story for the ANN index (either rebuilt on load or persisted alongside the blob store).

**Key Design Questions to Answer**
- In-memory HNSW first, or go straight to a disk-friendly structure (DiskANN / Vamana, or memory-mapped HNSW)?
- How do we handle incremental inserts / deletes (tombstones) in the ANN index?
- How does the ANN index interact with the existing text index segments and the graph topology?
- Quantization strategy (scalar, product quantization, or none initially)?
- Should the ANN index live inside the `BlobStore`, as a separate set of files, or as an in-memory structure that is rebuilt on load?

**Deliverables**
- `AnnIndex` trait + at least one solid implementation (HNSW recommended for first cut)
- Integration point inside `search_and_traverse_hybrid` (vector candidate generation via ANN instead of brute force)
- Python exposure (`g.vector_search(...)` and hybrid variant)
- Benchmarks comparing brute-force vs ANN under different graph densities and embedding dimensions
- Basic persistence / reload behavior

**Risks**
- ANN index quality and recall tuning can be subtle.
- Incremental maintenance (especially with deletes) is non-trivial.
- Sharded + distributed ANN is hard — we should solve the single-shard case extremely well first.

---

### P1 – Strong Ergonomics & Polish

- Standalone `vector_search(embedding: &[f32], top_k: usize) -> Vec<...>` primitive (exact first, then ANN-backed)
- Richer Python result handling (dataclasses, better `__repr__`, property access)
- Improved property filtering inside hybrid queries (currently simplified in the Python layer)
- `HybridWeights` as a first-class Python object (nice constructor + validation)
- Better defaults and documentation for weight tuning in hybrid scenarios
- Optional "vector-only" fusion mode (text_weight=0, graph_weight=0)

---

### P2 – Production Readiness for Hybrid Workloads

- Quantization support in the ANN index (reduce memory + improve cache behavior)
- Persistent / mmap-friendly ANN index (so cold vector search doesn't require full RAM residency)
- Interaction between ANN index and rebalancing (moving nodes between shards must update ANN indexes)
- Metrics / observability for the vector component (recall, latency breakdown)
- Example showing real embedding model integration (e.g., via Python + sentence-transformers or Rust Candle)

---

## 5. Non-Goals for 0.3 / Early 0.4

- Full cross-shard best-first hybrid traversal using ANN (the current per-shard + merge strategy is acceptable for the first version)
- Learned indexes or exotic ANN variants (HNSW + good quantization is the pragmatic 80/20 choice)
- Automatic embedding generation inside the engine (we remain an *index*, not an embedding service)

---

## 6. Relationship to Later Versions

A high-quality ANN index in 0.3/0.4 positions `grap` uniquely:

- You get **graph + text + vector** in one system with consistent sharding, persistence, and traversal semantics.
- Many "RAG + knowledge graph" architectures today glue a vector DB to a separate graph DB. `grap` can eliminate that glue.

The ANN index is the last major missing retrieval primitive. Once it exists, the focus can shift toward:
- Distribution (true multi-process / multi-machine)
- Advanced fusion / reranking strategies
- Even tighter Python / ecosystem integration

---

*User note (May 2026): ANN index has been explicitly selected as the next major development focus after the Python + Vector foundation.*