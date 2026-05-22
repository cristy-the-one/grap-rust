# pygrap Python Bindings Phase 1 — Design Spec

> **For agentic workers:** implement this via `superpowers:writing-plans` then
> `superpowers:subagent-driven-development`.

**Goal:** Wire the four new structure-aware traversal APIs into the pygrap Python
bindings, and update the Romanian legal acts demo (`real-data-law-ro`) to use
them — turning a flat collection of document nodes into a citation network with
relation-aware traversal queries.

**Architecture:** All changes are in `crates/pygrap/src/indexed_graph.rs` (Rust
PyO3 bindings) and `real-data-law-ro/{ingest,query}.py` (Python demo scripts).
`ShardedGraph` is explicitly out of scope for this phase.

**Tech stack:** PyO3 0.23, `pyo3::types::PyAny` for Python-native value
dispatch, `sentence-transformers` for embeddings in the demo.

---

## Context

After the structure-aware traversal layer (Task 1–10) the Rust API now has:
- `IndexedKnowledgeGraph::update_node`
- `IndexedKnowledgeGraph::merge_nodes` → `MergeReport`
- `IndexedKnowledgeGraph::neighbors_by_relation`
- `IndexedKnowledgeGraph::predecessors_by_relation`
- `IndexedKnowledgeGraph::traverse_by_relation`
- `HybridWeights::traversal_weight_property`

None of these are exposed in pygrap yet. The demo scripts (`ingest.py`,
`query.py`) are Phase 0 — no edges, no traversal, no relation metadata.

---

## File-Level Change Summary

| File | Change |
|------|--------|
| `crates/pygrap/src/indexed_graph.rs` | Add new Python classes + methods (see below) |
| `crates/pygrap/src/lib.rs` | Register `MergeReport`, `TraversalResult` |
| `real-data-law-ro/ingest.py` | Replace with two-pass citation-network ingestion |
| `real-data-law-ro/query.py` | Replace with three-mode interactive query interface |

---

## Part 1: pygrap Rust bindings

### 1.1 Helper: `py_to_property_value`

A private function (not `#[pyfunction]`) that converts a Python object to a
Rust `PropertyValue`:

```
str   → PropertyValue::Label(s.to_string())
bool  → PropertyValue::Bool(b)         # must be checked BEFORE int (bool is int in Python)
int   → PropertyValue::Int(i)
float → PropertyValue::Float(f)
other → PyTypeError
```

Signature:
```rust
fn py_to_property_value(val: &Bound<'_, PyAny>) -> PyResult<PropertyValue>
```

### 1.2 New Python class: `MergeReport`

```python
@dataclass  # conceptual
class MergeReport:
    absorbed: int              # NodeId of the absorbed node
    edges_redirected: int      # edges transplanted without collision
    edges_merged: int          # edges averaged with existing keep-edges
    self_loops_dropped: int    # keep↔absorb edges dropped
    embedding_updated: bool    # True if keep gained absorb's embedding
```

Rust: `#[pyclass(get_all)]` struct wrapping `grap::MergeReport`. `__repr__`
shows all fields.

### 1.3 New Python class: `TraversalResult`

Returned by `traverse_by_relation`. Simpler than `QueryResult` (no fusion
scores; traversal doesn't rank by text relevance).

```python
@dataclass  # conceptual
class TraversalResult:
    node_id: int    # local NodeId as u32
    depth: u32      # hops from source
    strength: float # cumulative path strength
```

Rust: `#[pyclass(get_all)]` struct. `__repr__` shows all fields.

### 1.4 New methods on `IndexedKnowledgeGraph`

#### `add_edge(source, target, strength, properties=None)`

```python
g.add_edge(a, b, 0.9, {"relation_type": "cites", "confidence": 0.85})
```

- `properties`: optional Python dict `{str: any}`. Each key is auto-registered
  via `self.inner.graph.properties.register(name)`. Values dispatched via
  `py_to_property_value`.
- Returns the edge ID as `u32`.
- Properties default to `{}` when omitted.

#### `get_node_text(id: int) -> str`

Wraps `self.inner.graph.read_blob(NodeId(id))`. Returns the full stored text
string. Panics if node is out of bounds (consistent with Rust behaviour).

#### `get_node_label(id: int) -> Optional[str]`

Wraps `self.inner.graph.node_label(NodeId(id))`. Returns `None` if no label.

#### `update_node(id, text, embedding=None)`

```python
g.update_node(node_id, "updated text content", embedding=[...])
```

Wraps `self.inner.update_node(NodeId(id), text, embedding)`. Raises
`GraphError` on invalid id. Preserves all edges.

#### `merge_nodes(keep, absorb) -> MergeReport`

```python
report = g.merge_nodes(keep_id, absorb_id)
print(report.edges_redirected, report.edges_merged)
```

Wraps `self.inner.merge_nodes(NodeId(keep), NodeId(absorb))`. Returns Python
`MergeReport`. Raises `GraphError` on validation failure.

#### `neighbors_by_relation(node, property_name, value) -> List[Tuple[int, float]]`

```python
cites = g.neighbors_by_relation(node_id, "relation_type", "cites")
# → [(target_id, strength), ...]
```

Wraps `self.inner.neighbors_by_relation(NodeId(node), property_name,
py_to_property_value(value))`. Returns list of `(u32, f64)` tuples.

#### `predecessors_by_relation(node, property_name, value) -> List[Tuple[int, float]]`

Same but incoming direction. Uses
`self.inner.predecessors_by_relation(...)`.

#### `traverse_by_relation(node, property_name, value, depth=3, max_results=50) -> List[TraversalResult]`

```python
chain = g.traverse_by_relation(node_id, "relation_type", "cites", depth=4)
for r in chain:
    print(r.node_id, r.depth, g.get_node_text(r.node_id)[:80])
```

Wraps `self.inner.traverse_by_relation(NodeId(node), property_name,
py_to_property_value(value), depth, max_results)`. Maps each Rust
`QueryResult` to a Python `TraversalResult`.

#### Extended: `search_and_traverse_hybrid`

Adds one new optional keyword argument:

```python
results = g.search_and_traverse_hybrid(
    query, vector=qvec,
    traversal_weight_property="confidence",  # NEW
    ...
)
```

When `traversal_weight_property` is not `None`, it is forwarded to
`HybridWeights::traversal_weight_property = Some(name.to_string())`. Edges
with a `confidence` property will be prioritised in the BFS expansion.

### 1.5 `lib.rs` registration

```rust
m.add_class::<indexed_graph::MergeReport>()?;
m.add_class::<indexed_graph::TraversalResult>()?;
```

---

## Part 2: real-data-law-ro demo

### 2.1 `ingest.py` — two-pass citation network

**Phase 1 replaces Phase 0.** Same embedding model, same on-disk graph.

**Pass 1 — node creation (same as Phase 0):**

For each JSON file:
- Build node text (metadata prefix + title + full text)
- Generate embedding with `sentence-transformers`
- Call `g.add_node(text, label=f"act-{id}", embedding=emb)`
- Record `act_key → node_id` in a dict, where `act_key = (tip_act_norm, nr, year)`

`tip_act_norm` normalises Romanian diacritics and casing:
`"ORDONANȚĂ"` / `"ORDONANŢA"` → `"ORDONANTA"` (so regex-extracted references
and database act types resolve to the same key).

**Pass 2 — edge extraction:**

Regex over each act's `text_complet`, looking for patterns:
```
(?:LEGEA|LEGE|ORDONANȚA|ORDONANŢA|HOTĂRÂREA|HOTARAREA|DECIZIA)
(?:\s+DE\s+URGENTA)?\s+
nr\.\s*(\d[\d\.]*)\s+din\s+(\d{1,2})\s+\w+\s+(\d{4})
```

For each match: compute `(tip_act_norm, nr, year)` key. If it exists in the
`act_key → node_id` map, call:
```python
g.add_edge(this_node, cited_node, 0.8,
           {"relation_type": "cites", "confidence": 0.8})
```

Duplicate edges (same source+target) are silently accepted by grap; we
de-duplicate in Python before calling `add_edge`.

**Persistence:** same as Phase 0 — periodic `save_to_dir` every 200 acts +
final save.

### 2.2 `query.py` — three-mode interactive interface

```
Commands:
  <text>               Hybrid search
  chain <id>           Citation chain from node (traverse_by_relation)
  cited_by <id>        Who cites this node (predecessors_by_relation)
  text <id>            Show full text of node
  quit
```

**Hybrid search** uses:
```python
g.search_and_traverse_hybrid(
    query, vector=qvec,
    text_weight=0.4, vector_weight=0.4, graph_weight=0.2,
    traversal_weight_property="confidence",
    min_strength=0.3, max_depth=3, max_results=10,
)
```

Result display shows node_id, scores, and first 220 chars of text. After
results are shown, the user is offered: "Enter a node_id to expand its
citation chain, or press Enter to continue."

**`chain <id>`** calls:
```python
results = g.traverse_by_relation(int(id), "relation_type", "cites",
                                  depth=4, max_results=20)
```
Displays each result as: `depth=N | node=ID | text[:120]`

**`cited_by <id>`** calls:
```python
preds = g.predecessors_by_relation(int(id), "relation_type", "cites")
```
Displays each predecessor with text snippet.

**`text <id>`** calls `g.get_node_text(int(id))` and prints the full text.

---

## Error Handling

- `add_edge` with an unmappable Python type raises `TypeError` via
  `py_to_property_value`.
- `merge_nodes` / `update_node` with invalid node IDs raise `pygrap.GraphError`
  (mapped from Rust `GraphError::Other`).
- Traversal methods with deleted/OOB nodes return empty list (consistent with
  Rust).
- Regex reference extraction in `ingest.py` is best-effort; unresolved
  references are counted and printed at the end.

---

## Testing Strategy

**Rust bindings (no Python unit tests for MVP — tested via the demo scripts):**

Manual smoke test after `maturin develop`:
```python
import pygrap as g
graph = pygrap.IndexedKnowledgeGraph("/tmp/test-graph")
a = graph.add_node("node a", embedding=[1.0, 0.0])
b = graph.add_node("node b", embedding=[0.0, 1.0])
graph.add_edge(a, b, 0.9, {"relation_type": "cites", "confidence": 0.85})
nbrs = graph.neighbors_by_relation(a, "relation_type", "cites")
assert len(nbrs) == 1 and nbrs[0][0] == b
chain = graph.traverse_by_relation(a, "relation_type", "cites", depth=2)
assert any(r.node_id == b for r in chain)
report = graph.merge_nodes(a, b)
assert report.edges_merged + report.edges_redirected + report.self_loops_dropped > 0
```

**Demo test (integration):**
```
python ingest.py       # completes without error, reports N edges created
python query.py        # interactive: run one search, one chain, one cited_by
```

---

## Out of Scope

- `ShardedGraph` — no new methods added (cross-shard merge not supported).
- `merge_nodes` on `ShardedGraph`.
- PyPI packaging — still internal/WIP.
- Full pytest suite for pygrap — out of scope for Phase 1.
- `traversal_weight_property` on `ShardedGraph.search_and_traverse_hybrid`.
