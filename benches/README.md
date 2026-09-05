# grap Benchmarks

This directory contains Criterion benchmarks that validate the core architectural decisions described in `core_graph_design.md`.

## Running the Benchmarks

```bash
# Basic set (fulltext + sharding)
cargo bench --features "fulltext,sharding"

# Full set including mmap cold/hot cache measurements
cargo bench --features "fulltext,sharding,mmap"
```

Results are written to `target/criterion/`.

## Engine regression workloads

`engine_workloads` needs no feature flags or Python services:

```bash
cargo bench --bench engine_workloads -- --save-baseline before
# After an engine change:
cargo bench --bench engine_workloads -- --baseline before
```

- `bounded_traversal`: the same reachable 32-node chain in arenas of 1K, 100K,
  and 500K nodes. This exposes whole-arena allocation during small queries.
- `selective_disk_search`: exactly one match in 1K, 10K, and 50K documents through
  `OnDiskTextIndex`. The index is in its active buffer: this isolates adapter
  overhead, not physical disk latency or a cold persisted-index open.
- `replace_document`: repeatedly replace three terms, two shared with every
  document. This exposes posting-list movement costs under mutation. It must
  be paired with `tests/index_mutation.rs`; a faster index retaining obsolete
  terms is not a valid improvement.

The first two probes held returned work constant while the old implementation's
latency grew with corpus size. See the [September engine report](../docs/engine-improvement-2026-09.md)
for before/after numbers, regression coverage, and tradeoffs. Timing results are
local measurements, not CI thresholds or general throughput guarantees.

## The Five Scenarios

### 1. Bitmask Pruning Benefit (`bitmask_pruning`)

**Goal**: Demonstrate that the 64-bit property mask pruning provides significant early-out savings during traversal.

**Methodology**:
- Synthetic graph with ~2,000 nodes and realistic property distribution (topic, confidence, category, relation).
- Same hybrid query run twice:
  - Without any property filter (baseline)
  - With a required `"topic"` property (strong mask pruning)
- Measures wall time and result cardinality.

**Expected Outcome**: The filtered version should be dramatically faster because large subtrees are rejected in O(1) time via the bitmask.

### 2. Sharded vs Monolithic (`sharded_vs_single`)

**Goal**: Measure the overhead of distributed traversal across shards using ghost edges.

**Methodology**:
- Same logical dataset (~1,200 nodes) represented as a single `IndexedKnowledgeGraph` vs a 4-shard `ShardedGraph`.
- Identical `search_and_traverse` calls.
- Measures latency and result quality.

**Expected Outcome**: Modest overhead (typically < 2×) for the sharded version, which is acceptable given the horizontal scaling benefit.

### 3. Rebalance Cost (`rebalance_cost`)

**Goal**: Quantify the cost of growing a sharded cluster.

**Methodology**:
- 4-shard cluster with ~4,000 nodes.
- Measure both `plan_rebalance(6)` and the full `execute_rebalance` when moving from 4 → 6 shards.
- Includes ghost edge creation/repair work.

**Expected Outcome**: Planning is cheap. Execution is the dominant cost but remains practical for moderate cluster sizes.

### 4. Text + Graph Fusion Cost (`text_graph_fusion`)

**Goal**: Show that hybrid search remains efficient across different `FusionWeights`.

**Methodology**:
- Same query executed with text-heavy (0.9/0.1), balanced (0.5/0.5), and graph-heavy (0.1/0.9) weightings.
- ~3,000 node graph.

**Expected Outcome**: Even when the text component dominates, the pruned graph traversal stays fast because BM25 seed selection is effective.

### 5. Mmap Cold vs Hot Cache (`mmap_cold_vs_hot`) — requires `mmap` feature

**Goal**: Validate the value of memory-mapped blob storage for large corpora.

**Methodology**:
- Graph with ~1,500 nodes, each carrying ~4–6 KiB of text.
- Compare:
  1. First traversal after `load_mmap` (cold pages)
  2. Subsequent traversals on the same `load_mmap` instance (hot)
  3. `load_from_disk` (full deserialization into RAM)

**Expected Outcome**:
- Cold mmap pays a one-time page-fault cost.
- Hot mmap should be competitive with or better than full in-memory load for large working sets.
- `load_from_disk` has high upfront cost but no later faults.

## Interpreting Results

- Focus on **relative** differences rather than absolute nanoseconds.
- Run on a quiet machine. Criterion does statistical analysis, but heavy background load still affects results.
- The most important signal is usually the **ratio** between "with pruning" vs "without pruning" and "sharded" vs "single".

## Updating These Benchmarks

When modifying core traversal, indexing, or sharding logic, please run the full suite and update this document with new representative numbers if the relative behavior changes meaningfully.
