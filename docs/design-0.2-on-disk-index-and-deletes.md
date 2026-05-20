# Design: On-Disk Inverted Index + Delete/Tombstone Support (0.2)

**Status**: Draft (for discussion)  
**Target**: grap 0.2  
**Authors**: Grok + User  
**Date**: April 2026

---

## 1. Motivation

In 0.1, `grap` proved that the core architecture works:

- Property bitmask pruning for O(1) edge filtering
- BM25 + phrase search as high-quality seed selection
- Sharding with ghost edges for cross-shard traversal
- Atomic persistence and basic observability

However, two hard limitations prevent real-world use at scale:

1. **The inverted index is fully in-memory.**  
   On every `load_from_disk()` or `load_mmap()`, we re-index every document from the blob store. This is simple but becomes a fatal scalability wall beyond a few hundred thousand documents.

2. **The graph is append-only.**  
   There is no `remove_node()`. This breaks any workload that needs deletion, correction, or time-based expiration. It also makes long-term maintenance of sharded graphs painful.

These two features are the highest-leverage items for 0.2. They are also deeply intertwined: deletes must be correctly reflected in the on-disk index, and the index design must support efficient deletion and compaction.

---

## 2. Goals

### Must-Have for 0.2

- The inverted index must be **persistent and load incrementally**.
- The system must support **logical deletes** (tombstones) with correct query semantics.
- Deletes must work correctly with **sharding and ghost edges**.
- Both features must be **incremental** — adding a document or deleting a node should not require rewriting the entire index or graph.

### Nice-to-Have (stretch)

- Background segment merging with bounded impact on query latency.
- Cheap "vacuum"/compaction operation that actually reclaims space.
- Per-shard tombstone statistics exposed via `Telemetry`.

### Non-Goals for 0.2

- Replacing the custom index with Tantivy (we want tight integration with `GlobalNodeId` and bitmasks).
- Full live compaction while serving queries at high QPS (we can accept a short maintenance window or background merge).
- Vector / embedding search.

---

## 3. On-Disk Inverted Index Design

### 3.1 Current State (0.1)

- `InvertedIndex` lives entirely in RAM.
- `doc_lengths`, `term_postings`, `term_df`, etc. are all `HashMap`/`Vec` in memory.
- On load we call `index_document()` for every node.

### 3.2 Proposed Architecture

We will introduce a **segmented, persistent inverted index** while keeping the existing `InvertedIndex` trait / API as stable as possible.

#### Core Concepts

- **Segment**: A self-contained, immutable unit containing:
  - Term dictionary (sorted terms + metadata)
  - Posting lists (for each term)
  - Document length information for the segment
  - Deleted document bitmap (for tombstones — see Section 4)

- **Index**: A collection of segments + a small "active" in-memory buffer for very recent documents.

- **Term Dictionary**: Sorted list of terms with:
  - Document frequency (df)
  - Pointer to posting list location
  - (Optional) collection frequency for better BM25

- **Posting List Format** (per segment):
  - For each term: list of `(doc_id_within_segment, term_frequency, [positions])`
  - Positions are only stored if phrase search is used (or we can make it configurable).
  - Use delta + variable-byte or PForDelta encoding for good compression.

#### Query Execution

When running `search()` or `phrase_search()`:

1. Consult the term dictionary (can be memory-mapped or loaded into a small cache).
2. For each matching term, open the relevant posting list segments.
3. Merge results across segments (using a heap or similar).
4. Apply deleted document filter (bitset or roaring bitmap per segment).
5. Score using BM25 (need to maintain global `avg_doc_length` + per-segment stats).
6. Return top-k `NodeId`s (or `GlobalNodeId` in sharded case).

Phrase queries will require positional information. We can store positions only for terms that appear in documents that have been phrase-searched, or always store them (simpler but higher storage cost).

#### Indexing Path (Write Path)

- New documents are added to a small **in-memory "active" segment** (or a write-ahead log + small in-memory buffer).
- When the active segment reaches a threshold (size or document count), it is flushed to disk as an immutable segment.
- Background task (or explicit `merge()`) merges small segments into larger ones using a simple tiered or logarithmic policy.

This design gives us:
- Fast incremental indexing
- Bounded memory usage for the hot working set
- Good compression on cold data

#### Storage Format (on disk)

Proposed layout under each shard:

```
index/
├── segments/
│   ├── seg-00001/
│   │   ├── terms.bin          # sorted term dictionary + metadata
│   │   ├── postings.bin       # posting lists (compressed)
│   │   └── meta.json          # df, doc count, avg length, etc.
│   ├── seg-00002/
│   └── ...
├── active/                    # in-memory only or small WAL
└── index.json                 # list of segments + global stats
```

We can start with a simple binary format using `bincode` + manual compression, then optimize later.

#### Interaction with Sharding

Each shard will have its own independent on-disk index. `GlobalNodeId` will be used when returning results from `ShardedGraph`, but the index inside each shard only knows about local `NodeId`s. This keeps things clean.

Deleted documents (tombstones) will be tracked per segment using a deleted bitmap so that queries automatically filter them.

---

## 4. Delete / Tombstone Support Design

### 4.1 Current Problems

- `NodeId` is a dense index into `Vec<Node>`.
- Removing a node would invalidate all higher `NodeId`s.
- Ghost edges on other shards point to `GlobalNodeId` values that would become invalid.
- The inverted index has no concept of "this document no longer exists".

### 4.2 Proposed Model: Tombstones + Lazy Cleanup

We will use **logical deletes (tombstones)** rather than immediate physical removal.

#### Changes to the Graph Layer

- Add `tombstone: bool` (or a tombstone timestamp) to the `Node` struct.
- `remove_node(NodeId)` / `remove_node(GlobalNodeId)` will:
  - Mark the node as deleted.
  - Remove it from the local adjacency lists (or mark edges as deleted).
  - Record the deletion in the inverted index (see below).
  - For ghost edges: the *source* shard still has a ghost pointing to a now-deleted target. We will handle this lazily on traversal.

#### Changes to the Inverted Index

- Each segment will carry a **deleted document bitmap** (bitvec or roaring).
- When a document is deleted, we set the bit in the appropriate segment(s).
- During query, we filter out deleted documents after retrieving candidates (or during posting list intersection if possible).

This is cheap and standard.

#### Ghost Edge Handling on Delete

This is the trickiest part.

Options:

**A. Eager notification (complex)**
- When a node is deleted, somehow notify all shards that have ghost edges pointing to it.
- Hard to do efficiently without a reverse index of "who points to me".

**B. Lazy detection (preferred for 0.2)**
- When following a ghost edge during traversal, the target shard checks whether the destination node is a tombstone.
- If it is, skip it and optionally clean up the ghost edge on the source shard asynchronously.
- This is simple and correct. Cleanup can be best-effort.

We can also add a background "ghost edge sweeper" that periodically scans for dangling ghosts, but this can be deferred.

#### Compaction / Vacuum

We will add an explicit operation:

```rust
fn compact(&mut self) -> Result<CompactionReport, GraphError>
```

This operation will:
- Rewrite segments, dropping deleted documents entirely.
- Rebuild posting lists without tombstones.
- Rewrite local adjacency information.
- Update ghost edges on other shards where possible (or mark them for later cleanup).

Compaction can be expensive, so it should be an explicit maintenance operation (like `VACUUM` in databases).

---

## 5. Cross-Cutting Concerns

### 5.1 Consistency Between Graph and Index

When we delete a node:
- We must delete it from the local graph **and** from the inverted index atomically (or with clear recovery semantics).
- The safest approach is to record the deletion in a write-ahead log first, then apply it to both the graph and the index.

### 5.2 Interaction with Rebalancing

- A node can be deleted while a rebalance plan is being computed or executed.
- The rebalance logic must respect tombstones (i.e., never move a deleted node).
- After moving a node, we must update any ghost edges that pointed to it.

### 5.3 Query Semantics

- Deleted nodes must never appear in search results or traversal results.
- `search_and_traverse()` should behave as if the nodes never existed (from the caller's perspective).

### 5.4 API Surface

Proposed new public methods (approximate):

```rust
// On KnowledgeGraph / IndexedKnowledgeGraph
pub fn remove_node(&mut self, id: NodeId) -> Result<(), GraphError>;

// On ShardedGraph
pub fn remove_node(&mut self, id: GlobalNodeId) -> Result<(), GraphError>;

// Compaction
pub fn compact(&mut self) -> Result<CompactionReport, GraphError>;
```

We will also need to expose some tombstone statistics via `ShardedGraphStats` and `Telemetry`.

---

## 6. Migration & Compatibility

- 0.1 indexes will need to be rebuilt on first load after upgrading to 0.2 (or we provide a migration tool).
- We will bump the `GraphFormatVersion` in `GraphMeta`.
- Old readers will refuse to load new-format graphs (already implemented via `UnsupportedVersion`).

---

## 7. Risks & Open Questions

| Risk / Question | Notes |
|------------------|-------|
| Complexity of correct segment merging + deletes | Need careful testing, especially around concurrent deletes + indexing. |
| Phrase query performance on disk | Positional postings can become large. May need to make phrase support optional or lazy. |
| Ghost edge cleanup strategy | Lazy detection is simple but can leave dangling ghosts for a long time. |
| How aggressive should default merging be? | Too aggressive hurts write throughput; too lazy hurts query performance. |
| Should `remove_node` be synchronous? | For large graphs, deletion should probably be fast (just a tombstone) with compaction done later. |

---

## 8. Suggested Implementation Order

1. Design and implement the on-disk segment format + basic read path.
2. Add the write path (flushing segments).
3. Add delete/tombstone support in the graph layer.
4. Wire deletes into the new on-disk index (deleted bitmaps).
5. Implement compaction.
6. Update rebalancing to respect tombstones.
7. Add statistics and telemetry events.
8. Migration tooling + documentation.

---

## 9. References

- `core_graph_design.md` (Sections 11 and 12)
- `docs/roadmap-0.2.md`
- `docs/phase5.md` (observability and persistence lessons)

---

**Next Steps**

This document is intentionally detailed but still leaves room for discussion on file formats, merging strategy, and exact API shape.

Would you like me to:

- Split this into two separate design docs (one for the index, one for deletes)?
- Go deeper on the segment file format and posting list encoding?
- Start prototyping the data structures?

Let me know how you want to proceed.