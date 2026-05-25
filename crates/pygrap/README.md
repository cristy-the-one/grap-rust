# pygrap – Python Bindings for grap

`pygrap` provides first-class Python bindings for the `grap` Rust knowledge graph engine using PyO3.

It is currently an **internal / development tool** and is not published on PyPI. You build and install it from source using maturin.

## Installation (from local source)

### Prerequisites
- Rust (stable, MSRV 1.81+)
- Python 3.9+
- maturin: `pip install maturin`

### Recommended: From repository root

```bash
# Activate a virtualenv (recommended)
python -m venv .venv
source .venv/bin/activate   # or .venv\\Scripts\\Activate.ps1 on Windows

pip install maturin

# Build and install the extension in development (editable) mode
maturin develop --manifest-path crates/pygrap/Cargo.toml --release
```

### Alternative (cd into the crate)

```bash
cd crates/pygrap
maturin develop --release
```

Re-run the command after changing Rust code. Only changed files are rebuilt.

> **Note**: The `--features "fulltext,sharding"` flags shown in older instructions are no longer needed — they are already enabled in the crate's `Cargo.toml`.

After installation:

```python
import pygrap
print(pygrap.__version__)

g = pygrap.IndexedKnowledgeGraph("my-graph")
```

## Main Classes

### `IndexedKnowledgeGraph`
The primary high-level API for a single (possibly on-disk) graph.

**Constructors**
- `IndexedKnowledgeGraph(path, merge_policy=None)`
- `IndexedKnowledgeGraph.with_merge_policy(path, segments_per_tier)`

**Core methods**
- `add_node(text, label=None, embedding=None) -> int`
- `add_edge(source, target, strength, properties: dict | None = None)`
- `get_node_text(id) -> str`
- `get_node_label(id) -> str | None`
- `update_node(id, text=None, label=None, embedding=None)`
- `merge_nodes(keep, absorb) -> MergeReport`
- `remove_node(id) -> bool`
- `search_and_traverse(...)`
- `search_and_traverse_hybrid(query, vector=None, ..., traversal_weight_property=None)`
- `vector_search(embedding, top_k) -> list[(node_id, score)]`
- `traverse_by_relation(node, property_name, value, ...) -> list[TraversalResult]`
- `predecessors_by_relation(node, property_name, value) -> list[(node_id, strength)]`
- `neighbors_by_relation(...)`
- `start_background_workers(interval_ms)`
- `save()`, `save_to_dir(path)`
- Context manager support (`with ... as g:` auto-saves on exit)

### `ShardedGraph`
Multi-shard facade (requires the sharding feature).

Supports the same high-level operations plus:
- `plan_rebalance(target_shards)`
- `execute_rebalance_to(target_shards)`
- `cleanup_ghost_edges()`
- Global node IDs as `(shard, local)` tuples

### Result Classes
- `QueryResult` (node, text_score, vector_score, graph_strength, fused_score, depth, text)
- `TraversalResult` (node_id, depth, strength)
- `MergeReport`
- `ShardedQueryResult`

## Real-World Example

The best demonstration is the **Romanian Legal Acts citation network**:

```bash
cd real-data-law-ro
python ingest.py          # Two-pass: nodes + citation edges
python query.py           # Hybrid search + chain / cited_by traversal
```

See [real-data-law-ro/README.md](../../real-data-law-ro/README.md) for full details.

It exercises:
- Embeddings + hybrid search with `traversal_weight_property`
- `add_edge(..., {"relation_type": "cites", ...})`
- `traverse_by_relation` / `predecessors_by_relation` for citation chains

## Drop-in Templates

Production-oriented starting points for your own data:

- `templates/pygrap-arbitrary-data/` — Basic recursive ingestion + hybrid search + Docker
- `templates/pygrap-arbitrary-data-extended/` — YAML frontmatter, edge properties, `--require` filtering

## Status & Notes

- This crate lives at `crates/pygrap` and is part of the main workspace.
- We use `maturin develop` for local development (no published wheels yet).
- The Python surface is intentionally kept close to the Rust APIs while offering ergonomic dict-based property handling and rich result objects.
- For the absolute latest API surface, read the `#[pymethods]` blocks in `src/indexed_graph.rs` and `src/sharded_graph.rs`.

Contributions and feedback on the Python ergonomics are very welcome.
