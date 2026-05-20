# grap 0.2 Roadmap

**Status**: Draft (post 0.1.0)  
**Target Release**: 0.2.x (2026 H2 / early 2027)

---

## 1. Vision

**0.1** made `grap` *correct and observable*.  
**0.2** will make it *usable at real scale and with real mutability*.

The core promise of the original design — fast property-bitmask traversal + BM25 seed selection + sharded ghost edges — remains the same. The 0.2 work focuses on removing the two largest practical limitations that appeared during 0.1 development:

- The inverted index must live on disk (not be rebuilt in RAM).
- The graph must support deletes (and therefore proper tombstoning and rebalancing).

Everything else in 0.2 is either enabling these two goals or making the system more pleasant to run in production for longer periods.

---

## 2. High-Level Goals

| Goal | Description | Success Criteria |
|------|-------------|------------------|
| **Persistent Text Index** | The `InvertedIndex` (postings + term dictionary) must be durable and load incrementally. | Can load a 5M+ document index in seconds, not minutes. Memory usage is bounded by working set, not total corpus size. |
| **Mutable Graphs** | Support `remove_node` (and eventually `update_node`) without corrupting the graph or ghost edges. | Delete 10% of nodes from a 500k-node sharded graph and still get correct query results. |
| **Better Rebalancing** | Rebalancing should be able to run with the system live (or with very short quiesce windows). | Rebalance a 4-shard cluster while serving queries. |
| **Storage Flexibility** | Make it easier to plug in different storage backends. | `BlobStore` and topology storage behind traits. |
| **Production Maturity** | Fewer sharp edges for long-running deployments. | Good defaults, better error messages, clearer operational docs. |

---

## 3. Priority Roadmap

### P0 – Must Have for 0.2

#### 1. On-Disk Inverted Index (Biggest Single Project)

**Current State (0.1)**  
The `InvertedIndex` is fully in-memory. On every `load_from_disk` / `load_mmap` we re-index every document from the blob store. This works for small-to-medium graphs but becomes a hard scalability wall.

**0.2 Goals**
- Persistent, segmented posting lists (similar in spirit to early Lucene / mini-Tantivy).
- Term dictionary + postings stored on disk.
- Lazy loading of posting lists (only load what is needed for the current query).
- Incremental indexing (new documents go into a new segment).
- Background segment merging (simple tiered or logarithmic strategy).
- Support for the existing `Tokenizer` trait (so users can keep custom tokenization).

**Key Design Decisions to Make**
- Segment file format (binary, length-prefixed, or using a small existing crate like `rkyv`/`bincode` + compression).
- How to handle phrase queries efficiently on disk (positional postings are expensive).
- Whether to keep a small in-memory "hot" cache of frequent terms.
- Write path durability (WAL vs. fsync on every document vs. periodic).

**Deliverables**
- New `OnDiskInvertedIndex` (or `PersistentInvertedIndex`) behind the existing `InvertedIndex` abstraction if possible.
- Migration path from 0.1 in-memory indexes.
- Benchmarks showing load time and memory usage at 1M / 5M / 10M documents.

**Risks**
- Complexity of correct segment merging + query correctness.
- Performance regression on small corpora if not careful with layering.

---

#### 2. Delete / Tombstone Support + Ghost Edge Cleanup

**Current State (0.1)**  
The system is append-only. `NodeId`s are dense. Removing a node would break adjacency lists and all ghost edges pointing to it.

**0.2 Goals**
- `remove_node(GlobalNodeId)` (and per-shard `remove_node(NodeId)`).
- Soft deletes via tombstones (preferred over immediate compaction for performance).
- When traversing, skip tombstones.
- When following ghost edges, detect and skip tombstones on the remote side.
- Explicit or background compaction that rewrites segments and updates ghost edges.
- Correct behavior during rebalancing (a node can be deleted while a rebalance is in flight).

**Key Challenges**
- Ghost edges are stored on the *source* shard. When the *target* is deleted, the source needs to be notified (or we accept lazy cleanup on next traversal).
- Maintaining correct `outgoing_property_mask` after deletes.
- Interaction with the on-disk index (deleted documents must not appear in search results).

**Deliverables**
- `remove_node` API on `KnowledgeGraph`, `IndexedKnowledgeGraph`, and `ShardedGraph`.
- Tombstone-aware traversal in both local and distributed paths.
- Compaction API (`compact()` / `vacuum()`).
- Updated rebalancing logic that respects tombstones.

---

### P1 – Strong Nice-to-Haves

#### 3. Live / Low-Impact Rebalancing

Current rebalancing (`plan_rebalance` + `execute_rebalance`) is stop-the-world for the router.

**Desired Improvements**
- Ability to run rebalancing while queries are served (with bounded impact).
- Promotion of ghost edges to local edges when two nodes end up on the same shard after a move.
- Better progress reporting and cancellation.

This work is heavily coupled with the delete/tombstone work.

#### 4. Pluggable Storage Backend

Extract the storage concerns behind traits so the core engine is not tied to the current `Vec` + `BlobStore` + filesystem layout.

Proposed traits (rough):

```rust
pub trait GraphStore: Send + Sync {
    // node/edge CRUD + iteration
}

pub trait BlobStore: Send + Sync {
    fn store(&mut self, data: &[u8]) -> BlobRef;
    fn read(&self, r: &BlobRef) -> &[u8];
    // etc.
}
```

This would allow future backends such as:
- Pure in-memory (current default)
- Memory-mapped with custom paging
- RocksDB / LMDB backed
- S3 + local cache (longer term)

---

### P2 – Foundational for Future Scaling

#### 5. RPC / Multi-Process Sharding Foundation

The current `ShardRouter` is strictly in-process. Real horizontal scaling will eventually require shards on different machines.

**0.2 Scope (Foundation Only)**
- Define clear boundaries between `Shard` and the router.
- Introduce a `ShardTransport` trait (or similar) so the same router logic can eventually talk over gRPC / Cap’n Proto / etc.
- No actual network implementation in 0.2 — just the abstraction and a local (in-process) implementation that proves the interface.

This is preparation work so 0.3+ can add real distribution without rewriting the query engine.

---

## 4. Non-Goals for 0.2

- Full SQL-like query language
- Vector embeddings / hybrid search with ANN indexes
- Multi-tenancy / ACLs
- True distributed consensus (Raft, etc.) for the router
- Replacing the custom inverted index with Tantivy (we will keep our own for tight integration with `GlobalNodeId` and bitmasks)

---

## 5. API Stability Expectations

- `0.2` will contain **some breaking changes** from `0.1`, primarily around:
  - Storage traits (if we introduce them)
  - `remove_node` API shape
  - Possibly the exact shape of `Telemetry` as we gain more experience
- We will try to keep the high-level facades (`KnowledgeGraph`, `IndexedKnowledgeGraph`, `ShardedGraph`) as stable as possible.

---

## 6. Rough Timeline (Very Approximate)

| Milestone | Target | Deliverable |
|-----------|--------|-------------|
| M1 | ~2–3 months | On-disk index design + first working segmented index (read-only) |
| M2 | ~4–5 months | Delete/tombstone support + basic compaction |
| M3 | ~6 months | Live rebalancing + improved stats |
| M4 | ~7–8 months | Pluggable storage traits + polish |
| **0.2.0** | ~8–9 months | Release |

---

## 7. Open Questions

- Exact segment file format for the on-disk index (binary vs. using an existing crate)?
- How aggressive should compaction be by default?
- Should `remove_node` be synchronous or always go through a background queue?
- Do we want a "read-only" mode for the on-disk index (cheaper, no merging)?
- How do we expose per-shard `Telemetry` vs. cluster-wide?

---

## 8. Relationship to the Original Design Document

Most of the above was already anticipated in `core_graph_design.md` (Sections 11 and 12 especially). The 0.1 release proved the core traversal + sharding model works. 0.2 is about making the "full vision" from the design document practical for production use.

---

**Next Step**

The two biggest bets for 0.2 are clearly:

1. **On-Disk Inverted Index**
2. **Delete / Tombstone Support**

Would you like me to start with a **design document** for one of them (probably the on-disk index first, as it has the highest architectural impact), or would you prefer to discuss trade-offs first (e.g., segment format choices, how aggressive we want to be on the feature flag for telemetry, etc.)?

Just tell me where you want to go next.