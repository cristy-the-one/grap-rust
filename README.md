# grap

**A Rust-native, read-optimized knowledge graph engine for interconnected text documents.**

grap is built for the specific workload of *storing rich text nodes* and *retrieving them by traversing property-weighted relationships* with excellent pruning and hybrid text+graph ranking.

It is a faithful implementation of the architecture described in [`core_graph_design.md`](./core_graph_design.md).

## Why grap?

Most graph libraries are either:

- General-purpose property graphs (too slow for heavy text traversal), or
- Vector / embedding stores (lose the explicit relationship semantics).

grap is different:

- **O(1) bitmask pruning** — every edge carries a 64-bit property mask. During traversal we reject entire subtrees in constant time if they cannot possibly satisfy the required properties.
- **Hot topology + cold blobs** — adjacency and masks live in RAM; full text lives in a separate `BlobStore` (chunked or memory-mapped).
- **BM25 seed selection** — text search finds high-quality starting points; the graph traversal then expands from them using strength and property filters.
- **Ghost edges for sharding** — consistent hashing + transparent cross-shard "ghost" references let a logical graph span many shards with automatic distributed traversal and rebalancing.

## Quick Start

Add to your `Cargo.toml`:

```toml
[dependencies]
grap = { version = "0.1", features = ["fulltext"] }
```

```rust
use grap::{FusionWeights, IndexedKnowledgeGraph, PropertyValue};

fn main() {
    let mut g = IndexedKnowledgeGraph::new();

    let topic = g.graph.properties.register("topic");
    let confidence = g.graph.properties.register("confidence");

    let rust = g.add_node("Rust guarantees memory safety without garbage collection.", Some("Rust"));
    let ownership = g.add_node("Every value has exactly one owner.", Some("Ownership"));

    g.add_edge(rust, ownership, 0.95, vec![
        (topic, PropertyValue::Label("memory_safety".into())),
        (confidence, PropertyValue::Float(0.98)),
    ]);

    // Hybrid search: text relevance + graph strength
    let results = g.search_and_traverse(
        "memory safety",
        5,                                    // max seeds from BM25
        &["topic"],                           // required properties (bitmask filtered)
        0.6,                                  // min strength
        3,                                    // max depth
        20,                                   // max results
        FusionWeights { text: 0.6, graph: 0.4 },
    );

    for r in results {
        println!("{} (fused={:.3})", r.text, r.fused_score);
    }
}
```

Run the built-in demo:

```bash
cargo run --example demo
cargo run --example hybrid_search
cargo run --example on_disk_persistence --features "fulltext,on-disk-index,sharding"
```

## Feature Flags

| Feature        | Description                                                                 | Brings in                  |
|----------------|-----------------------------------------------------------------------------|----------------------------|
| `fulltext`     | BM25 inverted index + phrase search + hybrid fusion                         | —                          |
| `on-disk-index`| Segmented on-disk text index with WAL, tiered merge, incremental vacuum   | —                          |
| `mmap`         | Real memory-mapped blob store (zero-copy reads)                             | `memmap2`, `crc32fast`     |
| `sharding`     | `ShardedGraph` + consistent hashing + ghost edges + rebalancing             | —                          |

All features are additive. Core graph + property bitmasks + traversal work with no features.

```toml
grap = { version = "0.1", features = ["fulltext", "on-disk-index", "sharding"] }
```

## The Three Main APIs

### 1. `KnowledgeGraph` (always available)

The low-level arena-based graph.

- `add_node(text, label) -> NodeId`
- `add_edge(from, to, strength, properties)`
- `QueryBuilder` + `search_and_traverse` with property masks and strength pruning
- `save_to_disk` / `load_from_disk`

### 2. `IndexedKnowledgeGraph` (requires `fulltext`)

Wraps `KnowledgeGraph` with an incremental BM25 index.

- `search("phrase")` → ranked text results
- `phrase_search("exact phrase", max_gap, k)`
- `search_and_traverse(...)` with `FusionWeights` blending text score and graph strength

When the `on-disk-index` feature is enabled you can also use a persistent backend:

```rust
let mut g = IndexedKnowledgeGraph::new_on_disk();
// ... add nodes ...
g.save_to_dir("my-index/")?;
let g2 = IndexedKnowledgeGraph::load_from_dir("my-index/")?;
```

The on-disk backend supports incremental vacuum, background merge workers, and per-segment statistics.

### 3. `ShardedGraph` (requires `sharding`)

High-level facade over a multi-shard cluster.

```rust
use grap::ShardedGraph;

let mut g = ShardedGraph::new(4);           // 4 shards, consistent hashing
let id = g.add_node("Some long document...", Some("Title"));
g.add_edge(...);                            // automatically creates ghost edges when needed

let results = g.search_and_traverse("...", 8, &["topic"], 0.5, 4, 50);
```

`ShardedGraph` also exposes introspection:

```rust
let stats = g.stats();
println!("{} nodes across {} shards, {} ghost edges",
         stats.total_nodes, stats.shard_count, stats.total_ghost_edges);
```

Rebalancing is explicit and cheap to plan:

```rust
let plan = g.plan_rebalance(8);   // "what if we went to 8 shards?"
g.execute_rebalance(plan);        // actual migration + ghost edge repair
```

`ShardedGraph` supports full persistence (including on-disk text indexes per shard and persisted ghost edges):

```rust
g.save_to_dir("my-cluster/")?;
let g2 = ShardedGraph::load_from_dir("my-cluster/")?;
```

## Persistence

grap offers two persistence styles:

**Classic (KnowledgeGraph level)**

```rust
g.save_to_disk("my-graph/")?;

#[cfg(feature = "mmap")]
let g = IndexedKnowledgeGraph::load_mmap("my-graph/")?;
```

**Modern directory-based (recommended for on-disk + sharded use)**

```rust
// Works for both IndexedKnowledgeGraph and ShardedGraph
g.save_to_dir("my-index/")?;
let g2 = IndexedKnowledgeGraph::load_from_dir("my-index/")?;
```

When using the `on-disk-index` feature, `save_to_dir` / `load_from_dir` will correctly persist and restore the segmented on-disk text indexes, including after rebalancing. Ghost edges are also persisted for `ShardedGraph`.

## Architecture at a Glance

```
User Query
     │
     ▼
┌────────────────────┐     ┌─────────────────────┐
│  InvertedIndex     │────▶│  KnowledgeGraph     │
│  (BM25 + phrase)   │ seeds │  (bitmask pruning,│
└────────────────────┘       │   priority queue)  │
                             └─────────────────────┘
                                      │
                                      ▼
                              ┌─────────────────────┐
                              │  BlobStore          │
                              │  (chunks or mmap)   │
                              └─────────────────────┘

Sharding layer (optional):
ShardedGraph → ShardRouter → N × (IndexedKnowledgeGraph + GhostEdge table)
```

The design is described in detail in [`core_graph_design.md`](./core_graph_design.md) (the original specification) and [`docs/implementation-plan.md`](./docs/implementation-plan.md).

## Status & Roadmap

- **Core engine + full-text + sharding** (0.1 features) are stable.
- **0.2 on-disk work** (behind the `on-disk-index` feature):
  - Segmented immutable on-disk text index with WAL and tiered merging
  - Incremental vacuum / compaction
  - First-class `save_to_dir` / `load_from_dir` for both single-graph and sharded use (including ghost edge persistence)
  - Background maintenance workers
  - Segment-aware rebalancing with real `SegmentMigration` execution

The `on-disk-index` + `sharding` combination now supports realistic large-scale persistent deployments.

See [`docs/roadmap-0.2.md`](./docs/roadmap-0.2.md) and [`docs/design-0.2-on-disk-index-and-deletes.md`](./docs/design-0.2-on-disk-index-and-deletes.md) for design details.

## Performance Claims (to be validated in Phase 5)

- O(1) property mask pruning dramatically reduces nodes visited on filtered traversals.
- Sharded distributed traversal adds modest overhead while enabling horizontal scale.
- mmap load is dramatically faster for cold-cache large-blob workloads than full deserialization.

A Criterion benchmark suite will be added shortly and numbers will be published.

## Minimum Supported Rust Version

MSRV = **1.81**.

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or http://www.apache.org/licenses/LICENSE-2.0)
- MIT license ([LICENSE-MIT](LICENSE-MIT) or http://opensource.org/licenses/MIT)

at your option.

## Contributing

Contributions are welcome once Phase 5 stabilizes the API. Until then, feel free to open issues with performance measurements or interesting workloads.

The single source of truth for the intended design remains `core_graph_design.md`.

---

*grap — because sometimes you need the graph, the text, and the ability to prune both at the same time.*
