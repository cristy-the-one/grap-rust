# Structure-Aware Traversal Layer — Design Spec

> **For agentic workers:** implement this via `superpowers:writing-plans` then
> `superpowers:executing-plans` or `superpowers:subagent-driven-development`.

**Goal:** Add four capabilities that turn grap from a semantic retrieval engine
into a graph that can answer topology questions using relation metadata already
stored on edges.

**Architecture:** One new focused module (`graph_ops.rs`) for write-path
structural mutations; traversal additions in existing `query.rs`; search-layer
additions in existing `index.rs`.

**Tech stack:** Pure Rust, no new dependencies. All additions follow existing
patterns (soft-delete, BlobStore append-only, `PropertyValue`/`PropertyTag`
system, `GraphError` for fallible ops).

---

## Context: Why These Four Features

Edge properties in grap can carry arbitrary typed key-value pairs (e.g.
`relation_type = "supports"`, `confidence = 0.92`). Before this work:

- You could *store* relation metadata on edges.
- You could not *query by* that metadata structurally.
- You could not update a node without losing its edges.
- You could not deduplicate entities without manually rewiring every edge.
- Traversal priority was always driven by `edge.strength`; a `confidence`
  property on edges was invisible to the BFS engine.

---

## Feature 1: `traverse_by_relation` + `neighbors_by_relation`

### API

On both `KnowledgeGraph` and `IndexedKnowledgeGraph` (the latter delegates to
`self.graph`).

```rust
/// Single-hop: direct neighbors where the connecting edge has
/// `property == value`. Returns (target_node, edge_strength) pairs.
/// Also: predecessors_by_relation for the incoming direction.
pub fn neighbors_by_relation(
    &self,
    node: NodeId,
    property: &str,
    value: PropertyValue,
) -> Vec<(NodeId, f64)>

pub fn predecessors_by_relation(
    &self,
    node: NodeId,
    property: &str,
    value: PropertyValue,
) -> Vec<(NodeId, f64)>

/// Multi-hop: BFS up to `depth` hops following only edges where
/// `property == value`. Returns full QueryResult (path, depth, strength).
pub fn traverse_by_relation(
    &self,
    node: NodeId,
    property: &str,
    value: PropertyValue,
    depth: u32,
    max_results: usize,
) -> Vec<QueryResult>
```

### Implementation

`neighbors_by_relation`: O(k) scan of `node.outgoing` edges. Resolve `property`
to a `PropertyTag` via the registry. Skip unregistered names (return empty vec).
Filter edges where `edge_property(tag) == value`. Collect `(edge.target,
edge.strength)`.

`predecessors_by_relation`: identical logic over `node.incoming`, collecting
`(edge.source, edge.strength)`.

`traverse_by_relation`: thin wrapper over the existing `QueryBuilder`:

```rust
self.find()
    .from_node(node)
    .where_property(property, value)
    .depth(depth)
    .limit(max_results)
    .execute()
```

No new traversal logic. Existing BFS + property filter handles multi-hop.

### Error handling

All three return empty `Vec` (never `Err`) for:
- Node out of bounds or deleted
- Property name not registered in the graph

---

## Feature 2: `merge_nodes(keep, absorb)`

### API

Low-level on `KnowledgeGraph` in `graph_ops.rs`:
```rust
pub fn merge_nodes(
    &mut self,
    keep: NodeId,
    absorb: NodeId,
) -> Result<MergeReport, GraphError>
```

High-level on `IndexedKnowledgeGraph` in `index.rs` (calls low-level then
updates text + vector indices).

### `MergeReport`

```rust
pub struct MergeReport {
    pub absorbed: NodeId,
    pub edges_redirected: usize,    // absorb's edges transplanted with no collision
    pub edges_merged: usize,        // absorb's edges averaged with existing keep edges
    pub self_loops_dropped: usize,  // keep→absorb or absorb→keep edges dropped
    pub embedding_updated: bool,    // true if keep gained absorb's embedding
}
```

### Low-level algorithm (`KnowledgeGraph::merge_nodes`)

**Validation:**
- Both nodes in bounds and not deleted → else `GraphError::Other`.
- `keep ≠ absorb` → else `GraphError::Other`.

**Outgoing edge transplant:**

Build `existing_out: HashMap<NodeId, EdgeId>` from keep's current outgoing
edges (snapshot before mutation).

For each outgoing edge `absorb → target`:

- `target == keep`: self-loop post-merge → drop. `self_loops_dropped++`.
- `existing_out` contains `target`:
  - Collision → merge:
    - `keep_edge.strength = (keep_edge.strength + absorb_edge.strength) / 2.0`
    - Properties: union of both; on key conflict, take value from the
      higher-strength edge (the dominant one pre-average).
  - `edges_merged++`.
- Otherwise: create new edge `keep → target` with absorb_edge's strength +
  properties. `edges_redirected++`.

**Incoming edge transplant:**

Build `existing_in: HashMap<NodeId, EdgeId>` from keep's current incoming edges.

For each incoming edge `source → absorb`:

- `source == keep`: self-loop → drop. `self_loops_dropped++`.
- `existing_in` contains `source`: merge (same rule as outgoing). `edges_merged++`.
- Otherwise: create new edge `source → keep`. `edges_redirected++`.

**Text and embedding (Option C):**

- keep's text and label are never changed.
- If `keep.embedding.is_none()` AND `absorb.embedding.is_some()`:
  copy absorb's embedding `BlobRef` to keep. `embedding_updated = true`.
- Else: `embedding_updated = false`.

**Finish:** soft-delete `absorb` (`self.deleted[absorb] = true`, clear its
outgoing/incoming edge lists).

### High-level (`IndexedKnowledgeGraph::merge_nodes`)

```
1. report = self.graph.merge_nodes(keep, absorb)?
2. self.text_index.remove_document(absorb)
3. if vector_index: vidx.remove_vector(absorb)
4. if report.embedding_updated:
       vidx.remove_vector(keep)
       vidx.add_vector(keep, new_embedding_from_blob)
5. return report
```

---

## Feature 3: `update_node`

### API

```rust
// IndexedKnowledgeGraph — public
pub fn update_node(
    &mut self,
    id: NodeId,
    new_text: &str,
    new_embedding: Option<&[f32]>,
) -> Result<(), GraphError>

// KnowledgeGraph — pub(crate), called by above
pub(crate) fn update_node_content(
    &mut self,
    id: NodeId,
    new_text: &str,
    new_embedding: Option<&[f32]>,
) -> Result<(), GraphError>
```

### Low-level (`KnowledgeGraph::update_node_content` in `graph_ops.rs`)

```
1. Validate id in bounds and not deleted
2. new_blob = self.blobs.store(new_text)     ← old blob orphaned, not freed
3. self.nodes[id].blob = new_blob
4. if let Some(emb) = new_embedding:
       self.nodes[id].embedding = Some(self.blobs.store_embedding(emb))
5. Ok(())
```

**Orphaned blobs:** the BlobStore is append-only. The old text blob becomes
unreachable but is not freed. This is the same trade-off accepted for deleted
nodes. A future `vacuum()` compaction can reclaim them; not in scope here.

**Edges are never touched.** All outgoing/incoming edge lists remain intact.

### High-level (`IndexedKnowledgeGraph::update_node`)

```
1. self.graph.update_node_content(id, new_text, new_embedding)?
2. self.text_index.remove_document(id)
3. self.text_index.index_document(id, new_text)
4. if let Some(emb) = new_embedding:
       if let Some(vidx) = &mut self.vector_index:
           vidx.remove_vector(id)
           vidx.add_vector(id, emb)
5. Ok(())
```

Partial updates: pass `None` for `new_embedding` to update text only. The
existing embedding blob and vector index entry are left unchanged.

---

## Feature 4: `HybridWeights::traversal_weight_property`

### API extension

```rust
pub struct HybridWeights {
    pub text: f64,
    pub vector: f64,
    pub graph: f64,
    /// If `Some(name)`, use this edge property's numeric value as the
    /// traversal priority weight instead of `edge.strength`.
    /// Falls back to `edge.strength` if the property is absent on a given edge.
    /// `None` (default) preserves existing behaviour exactly.
    pub traversal_weight_property: Option<String>,
}
```

`Default` keeps `traversal_weight_property: None`.

### New `QueryBuilder` method

```rust
pub fn weight_by_property(mut self, name: &str) -> Self
```

Resolves `name` to a `PropertyTag` via the registry, stores it in a new field
`traversal_weight_property: Option<PropertyTag>` on `GraphQuery`.

### Traversal loop change (`query.rs`)

One block replaces the current `edge.strength` read:

```rust
let w = match q.traversal_weight_property {
    Some(tag) => match self.edge_property(edge_id, tag) {
        Some(PropertyValue::Float(v)) => *v,
        Some(PropertyValue::Int(v))   => *v as f64,
        _                             => edge.strength,  // fallback
    },
    None => edge.strength,
};
// cumulative_strength: entry.cumulative_strength * w
```

**`min_strength` still filters on `edge.strength`**, not on the property value.
This prevents edges with a high confidence score but low strength from escaping
the floor unexpectedly. The property weight is a priority signal only.

### Wiring into `search_and_traverse_hybrid`

```rust
if let Some(ref prop) = weights.traversal_weight_property {
    builder = builder.weight_by_property(prop);
}
```

---

## File-Level Change Summary

| File | Change type | What changes |
|------|-------------|--------------|
| `src/graph_ops.rs` | **NEW** | `MergeReport` struct; `KnowledgeGraph::merge_nodes`, `KnowledgeGraph::update_node_content` |
| `src/query.rs` | extend | `GraphQuery::traversal_weight_property`; `QueryBuilder::weight_by_property`; `KnowledgeGraph::neighbors_by_relation`, `predecessors_by_relation`, `traverse_by_relation` |
| `src/index.rs` | extend | `HybridWeights::traversal_weight_property`; `IndexedKnowledgeGraph::update_node`, `merge_nodes`, `neighbors_by_relation`, `predecessors_by_relation`, `traverse_by_relation` |
| `src/lib.rs` | extend | `pub mod graph_ops;` declaration; re-export `MergeReport` |

---

## Testing Strategy

Each feature gets dedicated tests:

- **`neighbors_by_relation`**: 3-node graph, mixed edge properties, verify only
  matching edges returned in both directions.
- **`traverse_by_relation`**: chain A→B→C→D all with `relation_type="supports"`,
  one branch with different type; verify only the matching chain is returned.
- **`merge_nodes` — no collision**: two unconnected hubs, merge, verify edge
  counts and tombstone.
- **`merge_nodes` — collision**: keep and absorb both have edge to same target,
  verify averaged strength, merged properties, single edge after merge.
- **`merge_nodes` — self-loop**: keep→absorb edge exists; verify dropped.
- **`merge_nodes` — embedding propagation**: keep has none, absorb has one;
  verify keep gains it. Both have embeddings; verify keep's unchanged.
- **`update_node`**: create node, verify text search finds it, update text,
  verify old term no longer found, new term found. Edges intact before/after.
- **`weight_by_property`**: graph with two paths A→B of equal strength but
  different `confidence` properties; verify higher-confidence path wins.
- **Integration**: `search_and_traverse_hybrid` with
  `traversal_weight_property = Some("confidence")` returns differently ranked
  results than without.

---

## Out of Scope

- **`vacuum()` / blob compaction** — orphaned blobs from `update_node` are
  noted but not addressed. Separate future feature.
- **Python bindings (`pygrap`)** — the Rust API is the deliverable. Wiring into
  pygrap is a follow-up once the Rust layer is stable.
- **Bidirectional `traverse_by_relation`** — the current traversal engine is
  directed (outgoing only). Undirected traversal would require traversal engine
  changes beyond this scope.
- **`merge_nodes` on `ShardedGraph`** — cross-shard merges require routing
  logic that is out of scope. Within-shard merges already work since each shard
  is an `IndexedKnowledgeGraph`.
