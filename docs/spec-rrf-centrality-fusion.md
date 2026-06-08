# RRF Fusion + PageRank Centrality (Rust core) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace grap's *linear* score-addition fusion with **rank-based Reciprocal Rank Fusion (RRF)** and add a **PageRank centrality channel** — both in the Rust core — so hybrid retrieval stops depending on incomparable score scales and gains a structural-importance signal the engine currently lacks.

**Architecture:** A new `src/ranking.rs` core module provides a pure `rrf_fuse(channels, k)` and a `pagerank(...)` over the existing arena adjacency (respecting the 64-bit property mask). `search_and_traverse_hybrid` is refactored to emit its existing text/vector/graph streams as **ranked channels**, optionally add a centrality channel, and fuse via RRF when `FusionMode::Rrf` is selected (the new default). The linear path is preserved behind `FusionMode::Linear` for exact back-compat. `CombinedResult` keeps its per-signal scores and gains optional channel-rank diagnostics.

**Tech Stack:** Rust, existing `IndexedKnowledgeGraph`/`KnowledgeGraph`/`QueryBuilder`, `cargo test`. No new dependencies.

**Allocation rationale (core vs skin):** Ranking is the hottest path in the engine and runs on every query; RRF and PageRank are pure graph/array math over arena data — textbook "embed in the rusty core." The MCP only *passes through* a fusion mode + weights, so nothing of substance lands in the skin.

---

## File Structure

- **Create** `src/ranking.rs` — `RankedChannel`, `FusedNode`, `rrf_fuse`, `pagerank`; `mod ranking;` added to `src/lib.rs`.
- **Modify** `src/index.rs` — add `FusionMode`; extend `HybridWeights`/`FusionWeights` with `mode` + optional `centrality` weight; refactor `search_and_traverse_hybrid` (and `search_and_traverse`) to build channels and fuse; cache pagerank on `IndexedKnowledgeGraph`; add channel-rank fields to `CombinedResult`.
- **Modify** `src/graph.rs` — a `pagerank` over `KnowledgeGraph` adjacency with optional mask (or place it in `ranking.rs` taking `&KnowledgeGraph`).
- **Modify** `crates/pygrap/src/indexed_graph.rs` — surface `fusion_mode`, `rrf_k`, `centrality_weight` on the hybrid query.
- **Test** `tests/ranking_fusion.rs` (new) + unit tests in `src/ranking.rs` and `src/index.rs`.

---

## Task 1: Pure RRF fusion primitive

**Files:**
- Create: `src/ranking.rs`
- Modify: `src/lib.rs` (add `pub mod ranking;`)
- Test: `src/ranking.rs` unit tests

- [ ] **Step 1: Write the failing test**

Create `src/ranking.rs` with only the test first:

```rust
//! Rank-based fusion and graph-centrality scoring for query ranking.
use crate::ids::NodeId;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rrf_rewards_consensus_across_channels() {
        // node 1 is rank-1 in two channels; node 2 is rank-1 in only one.
        let ch_a = RankedChannel { name: "a", weight: 1.0, ranked: vec![NodeId(1), NodeId(2)] };
        let ch_b = RankedChannel { name: "b", weight: 1.0, ranked: vec![NodeId(1), NodeId(3)] };
        let fused = rrf_fuse(&[ch_a, ch_b], 60.0);
        assert_eq!(fused[0].node, NodeId(1)); // consensus wins
        // contribution = w/(k+rank): node1 = 1/61 + 1/61 ; node2 = 1/62
        assert!(fused[0].score > fused[1].score);
    }

    #[test]
    fn rrf_respects_channel_weight() {
        let strong = RankedChannel { name: "s", weight: 2.0, ranked: vec![NodeId(7)] };
        let weak = RankedChannel { name: "w", weight: 0.1, ranked: vec![NodeId(8)] };
        let fused = rrf_fuse(&[strong, weak], 60.0);
        assert_eq!(fused[0].node, NodeId(7));
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib ranking::tests`
Expected: FAIL — `RankedChannel`/`rrf_fuse` undefined.

- [ ] **Step 3: Write minimal implementation**

Add above the test module in `src/ranking.rs`:

```rust
use std::collections::HashMap;

/// One channel's ranked output: an ordered list of node ids, best first.
pub struct RankedChannel {
    pub name: &'static str,
    pub weight: f64,
    pub ranked: Vec<NodeId>,
}

/// A node's fused score with per-channel diagnostics.
#[derive(Debug, Clone)]
pub struct FusedNode {
    pub node: NodeId,
    pub score: f64,
    /// (channel_name, contribution) pairs, for explanations.
    pub contributions: Vec<(&'static str, f64)>,
    /// (channel_name, 1-based rank) pairs.
    pub ranks: Vec<(&'static str, u32)>,
}

/// Default RRF smoothing constant. Higher → less top-heavy.
pub const DEFAULT_RRF_K: f64 = 60.0;

/// Weighted Reciprocal Rank Fusion. Each channel contributes
/// `weight / (k + rank)` (1-based rank) to each node it ranks; scores are
/// summed across channels. Robust to incomparable per-channel score scales.
pub fn rrf_fuse(channels: &[RankedChannel], k: f64) -> Vec<FusedNode> {
    let mut accum: HashMap<NodeId, FusedNode> = HashMap::new();
    for ch in channels {
        for (rank0, &nid) in ch.ranked.iter().enumerate() {
            let rank1 = (rank0 + 1) as u32;
            let contribution = ch.weight / (k + rank1 as f64);
            let entry = accum.entry(nid).or_insert_with(|| FusedNode {
                node: nid,
                score: 0.0,
                contributions: Vec::new(),
                ranks: Vec::new(),
            });
            entry.score += contribution;
            entry.contributions.push((ch.name, contribution));
            entry.ranks.push((ch.name, rank1));
        }
    }
    let mut out: Vec<FusedNode> = accum.into_values().collect();
    out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    out
}
```

Add `pub mod ranking;` to `src/lib.rs` (near the other `mod` declarations).

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib ranking::tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ranking.rs src/lib.rs
git commit -m "feat(core): add rank-based RRF fusion primitive (src/ranking.rs)"
```

---

## Task 2: PageRank over arena adjacency (mask-aware)

**Files:**
- Modify: `src/ranking.rs` (add `pagerank`)
- Modify: `src/graph.rs` — add a `pub(crate)` accessor for outgoing edges + endpoints if not already reachable from `ranking.rs` (it can use existing `all_edges()` + `edge_endpoints()` + `matches_mask`).
- Test: `src/ranking.rs` unit tests

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn pagerank_ranks_hub_above_leaf() {
    use crate::KnowledgeGraph;
    let mut g = KnowledgeGraph::new();
    let hub = g.add_node("hub", None, None);
    let a = g.add_node("a", None, None);
    let b = g.add_node("b", None, None);
    // a -> hub, b -> hub  => hub has high in-rank
    g.add_edge(a, hub, 1.0, vec![]);
    g.add_edge(b, hub, 1.0, vec![]);
    let pr = pagerank(&g, None, 0.85, 30);
    assert!(pr[hub.0 as usize] > pr[a.0 as usize]);
    assert!(pr[hub.0 as usize] > pr[b.0 as usize]);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib ranking::tests::pagerank_ranks_hub_above_leaf`
Expected: FAIL — `pagerank` undefined.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ranking.rs`:

```rust
use crate::KnowledgeGraph;

/// Iterative PageRank over the live node set. When `mask` is `Some`, only
/// edges whose property bits satisfy the mask participate (so callers can
/// ask for "centrality among `cites` edges"). `damping` ~0.85, `iters` ~30.
/// Returns a dense vector indexed by NodeId.0 (tombstoned nodes get 0.0).
pub fn pagerank(g: &KnowledgeGraph, mask: Option<u64>, damping: f64, iters: u32) -> Vec<f64> {
    let n_total = g.node_count(); // id-space size incl. tombstones
    let live: Vec<bool> = (0..n_total)
        .map(|i| !g.is_node_deleted(NodeId(i as u32)))
        .collect();
    let live_count = live.iter().filter(|&&b| b).count().max(1) as f64;

    // Build outgoing adjacency honoring the optional mask.
    let mut out: Vec<Vec<u32>> = vec![Vec::new(); n_total];
    for edge in g.all_edges() {
        if let Some(m) = mask {
            if !edge.matches_mask(m) {
                continue;
            }
        }
        let (s, t) = (edge.source_index(), edge.target_index()); // see note below
        if live[s as usize] && live[t as usize] {
            out[s as usize].push(t);
        }
    }

    let base = (1.0 - damping) / live_count;
    let mut rank: Vec<f64> = live.iter().map(|&b| if b { 1.0 / live_count } else { 0.0 }).collect();

    for _ in 0..iters {
        let mut next = vec![0.0_f64; n_total];
        let mut dangling = 0.0;
        for i in 0..n_total {
            if !live[i] { continue; }
            if out[i].is_empty() {
                dangling += rank[i];
            } else {
                let share = rank[i] / out[i].len() as f64;
                for &t in &out[i] {
                    next[t as usize] += share;
                }
            }
        }
        let dangling_share = dangling / live_count;
        for i in 0..n_total {
            if live[i] {
                next[i] = base + damping * (next[i] + dangling_share);
            }
        }
        rank = next;
    }
    rank
}
```

**Implementation note for the engineer:** `Edge` already exposes endpoints via `KnowledgeGraph::edge_endpoints(EdgeId)` and a `matches_mask(u64)` method (`src/graph.rs:43`). If `Edge`'s source/target fields are private and there is no direct accessor, add two `pub(crate) fn source_index(&self)->u32` / `target_index(&self)->u32` on `Edge` (returning the `NodeId.0`), or iterate `0..g.edge_count()` calling `edge_endpoints`. Pick whichever matches the existing field visibility — do not widen visibility beyond `pub(crate)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib ranking::tests::pagerank_ranks_hub_above_leaf`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ranking.rs src/graph.rs
git commit -m "feat(core): mask-aware PageRank centrality over arena adjacency"
```

---

## Task 3: `FusionMode` + extend the weight structs

**Files:**
- Modify: `src/index.rs:213-261` (`FusionWeights`, `HybridWeights`, their `Default` impls)
- Test: `src/index.rs` unit test

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn hybrid_weights_default_is_rrf_with_centrality() {
    let w = HybridWeights::default();
    assert!(matches!(w.mode, crate::index::FusionMode::Rrf { .. }));
    assert!(w.centrality >= 0.0);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib index::tests::hybrid_weights_default_is_rrf_with_centrality`
Expected: FAIL — no `mode`/`centrality` field, no `FusionMode`.

- [ ] **Step 3: Write minimal implementation**

In `src/index.rs`, add the enum and extend the structs:

```rust
/// How channel scores are combined into a final ranking.
#[derive(Debug, Clone, Copy)]
pub enum FusionMode {
    /// Legacy: weighted sum of raw per-signal scores. Kept for back-compat.
    Linear,
    /// Reciprocal Rank Fusion with smoothing constant `k`. The default.
    Rrf { k: f64 },
}

impl Default for FusionMode {
    fn default() -> Self {
        FusionMode::Rrf { k: crate::ranking::DEFAULT_RRF_K }
    }
}
```

Extend `HybridWeights` (keep existing fields; add two):

```rust
pub struct HybridWeights {
    pub text: f64,
    pub vector: f64,
    pub graph: f64,
    /// Weight of the PageRank centrality channel (0.0 disables it).
    pub centrality: f64,
    pub traversal_weight_property: Option<String>,
    pub mode: FusionMode,
}
```

Update its `Default` (mirror jcodemunch's defaults: lexical/text strongest, structural ~0.4):

```rust
impl Default for HybridWeights {
    fn default() -> Self {
        HybridWeights {
            text: 1.0,
            vector: 0.6,
            graph: 0.5,
            centrality: 0.4,
            traversal_weight_property: None,
            mode: FusionMode::default(),
        }
    }
}
```

Add the same `mode` field (and keep `text`/`graph`) to `FusionWeights` with `Default` `mode: FusionMode::default()`. **Compile fix:** every construction site of `HybridWeights`/`FusionWeights` (pygrap, shard.rs, tests, `*_default` wrappers) must set the new fields — prefer `..Default::default()` where a struct literal is used so additions stay non-breaking.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib index::tests::hybrid_weights_default_is_rrf_with_centrality`
Run: `cargo build -p grap` (fix all construction sites).
Expected: test PASS, crate builds.

- [ ] **Step 5: Commit**

```bash
git add src/index.rs src/shard.rs
git commit -m "feat(core): add FusionMode (Rrf default) + centrality weight to weight structs"
```

---

## Task 4: Refactor `search_and_traverse_hybrid` to build channels + fuse

**Files:**
- Modify: `src/index.rs:1091-1201` (`search_and_traverse_hybrid`), `src/index.rs:294-307` (`CombinedResult`)
- Modify: `src/index.rs` `IndexedKnowledgeGraph` struct (~748-766) to add a lazy pagerank cache
- Test: `tests/ranking_fusion.rs` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/ranking_fusion.rs`:

```rust
use grap::index::FusionMode;
use grap::{HybridWeights, IndexedKnowledgeGraph};

#[test]
fn rrf_and_linear_can_disagree_but_both_return_results() {
    let mut g = IndexedKnowledgeGraph::new();
    let a = g.add_node("rust memory safety ownership", Some("A"), None);
    let b = g.add_node("garbage collection tradeoffs", Some("B"), None);
    let c = g.add_node("memory safety without gc", Some("C"), None);
    g.add_edge(a, c, 0.9, vec![]);
    g.add_edge(b, c, 0.2, vec![]);

    let mut w = HybridWeights::default();
    w.mode = FusionMode::Rrf { k: 60.0 };
    let rrf = g.search_and_traverse_hybrid("memory safety", None, 5, &[], 0.0, 2, 10, w);
    assert!(!rrf.is_empty());

    let mut wl = HybridWeights::default();
    wl.mode = FusionMode::Linear;
    let lin = g.search_and_traverse_hybrid("memory safety", None, 5, &[], 0.0, 2, 10, wl);
    assert!(!lin.is_empty());
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --test ranking_fusion`
Expected: FAIL to compile (no `FusionMode` import path) until Task 3 lands; then FAIL/behaviour until refactor. (If Task 3 already merged, this compiles and the assertions drive the refactor.)

- [ ] **Step 3: Write minimal implementation**

Add optional channel diagnostics to `CombinedResult` (keep existing fields):

```rust
pub struct CombinedResult<'a> {
    pub text_score: f64,
    pub vector_score: f64,
    pub graph_strength: f64,
    pub fused_score: f64,
    /// (channel, 1-based rank) when RRF was used; empty under Linear.
    pub channel_ranks: Vec<(&'static str, u32)>,
    pub graph_result: crate::query::QueryResult,
    pub text: &'a str,
}
```

Refactor the tail of `search_and_traverse_hybrid` (after `graph_results` is computed). Keep the per-node `text_score`/`vector_score`/`graph_strength` computation exactly as today, then branch on mode:

```rust
// ... unchanged: build `combined: Vec<CombinedResult>` with the three raw
// scores per node, but set fused_score = 0.0 and channel_ranks = vec![] for now.

match weights.mode {
    FusionMode::Linear => {
        for r in combined.iter_mut() {
            r.fused_score = r.text_score * weights.text
                + r.vector_score * weights.vector
                + r.graph_strength * weights.graph;
        }
    }
    FusionMode::Rrf { k } => {
        use crate::ranking::{rrf_fuse, RankedChannel};
        // Rank each channel by its raw score (desc) over the candidate set.
        let rank_by = |key: &dyn Fn(&CombinedResult) -> f64| -> Vec<crate::NodeId> {
            let mut idx: Vec<usize> = (0..combined.len()).collect();
            idx.sort_by(|&i, &j| key(&combined[j])
                .partial_cmp(&key(&combined[i])).unwrap_or(std::cmp::Ordering::Equal));
            idx.into_iter()
                .filter(|&i| key(&combined[i]) > 0.0)
                .map(|i| combined[i].graph_result.node_id)
                .collect()
        };
        let mut channels = vec![
            RankedChannel { name: "text", weight: weights.text, ranked: rank_by(&|r| r.text_score) },
            RankedChannel { name: "vector", weight: weights.vector, ranked: rank_by(&|r| r.vector_score) },
            RankedChannel { name: "graph", weight: weights.graph, ranked: rank_by(&|r| r.graph_strength) },
        ];
        // Centrality channel (optional). Reuses the lazy pagerank cache.
        if weights.centrality > 0.0 {
            let pr = self.pagerank_cached(None);
            let mut by_pr: Vec<&CombinedResult> = combined.iter().collect();
            by_pr.sort_by(|a, b| {
                let (ia, ib) = (a.graph_result.node_id.0 as usize, b.graph_result.node_id.0 as usize);
                pr.get(ib).unwrap_or(&0.0).partial_cmp(pr.get(ia).unwrap_or(&0.0))
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
            let ranked = by_pr.into_iter().map(|r| r.graph_result.node_id).collect();
            channels.push(RankedChannel { name: "centrality", weight: weights.centrality, ranked });
        }
        let fused = rrf_fuse(&channels, k);
        let pos: std::collections::HashMap<crate::NodeId, &crate::ranking::FusedNode> =
            fused.iter().map(|f| (f.node, f)).collect();
        for r in combined.iter_mut() {
            if let Some(f) = pos.get(&r.graph_result.node_id) {
                r.fused_score = f.score;
                r.channel_ranks = f.ranks.clone();
            }
        }
    }
}

combined.sort_by(|a, b| b.fused_score.partial_cmp(&a.fused_score).unwrap_or(std::cmp::Ordering::Equal));
combined.truncate(max_results);
combined
```

Add the lazy cache to `IndexedKnowledgeGraph`:

```rust
// field on the struct:
pagerank_cache: std::cell::RefCell<Option<Vec<f64>>>,

// method:
fn pagerank_cached(&self, mask: Option<u64>) -> Vec<f64> {
    // Mask-specific caching can come later; cache the unmasked global rank.
    if mask.is_none() {
        if let Some(pr) = self.pagerank_cache.borrow().as_ref() {
            return pr.clone();
        }
    }
    let pr = crate::ranking::pagerank(&self.graph, mask, 0.85, 30);
    if mask.is_none() {
        *self.pagerank_cache.borrow_mut() = Some(pr.clone());
    }
    pr
}
```

Invalidate the cache (`*self.pagerank_cache.borrow_mut() = None;`) inside `add_edge`, `add_node`, `remove_node`, `merge_nodes`, and `update_node` on `IndexedKnowledgeGraph`. Initialise the field to `RefCell::new(None)` in every constructor (`new`, `with_text_index`, `new_on_disk`, …).

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --test ranking_fusion`
Run: `cargo test -p grap` (full suite — the legacy `hybrid_traversal_weight_property_changes_ranking` test must still pass under the default RRF mode, or be split into Linear/Rrf variants).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/index.rs tests/ranking_fusion.rs
git commit -m "feat(core): fuse hybrid query via RRF + optional centrality channel"
```

---

## Task 5: Apply the same channels to `search_and_traverse` (text+graph)

**Files:**
- Modify: `src/index.rs:1010-1074` (`search_and_traverse`)
- Test: `src/index.rs` unit test

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn text_graph_query_uses_rrf_by_default() {
    let mut g = IndexedKnowledgeGraph::new();
    let a = g.add_node("alpha topic", None, None);
    let b = g.add_node("beta topic alpha", None, None);
    g.add_edge(a, b, 0.9, vec![]);
    let res = g.search_and_traverse_default("alpha", 5, &[], 0.0, 2, 10);
    assert!(!res.is_empty());
    assert!(res[0].channel_ranks.iter().any(|(n, _)| *n == "text"));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib index::tests::text_graph_query_uses_rrf_by_default`
Expected: FAIL — `channel_ranks` empty under the old linear path.

- [ ] **Step 3: Write minimal implementation**

Factor the channel-building + fuse tail from Task 4 into a private helper so both entry points share it:

```rust
fn fuse_combined(
    &self,
    mut combined: Vec<CombinedResult<'_>>,
    text_w: f64, vector_w: f64, graph_w: f64, centrality_w: f64,
    mode: FusionMode, max_results: usize,
) -> Vec<CombinedResult<'_>> { /* the match {Linear, Rrf} block from Task 4 */ }
```

Have `search_and_traverse` build `combined` with `vector_score = 0.0` and call `self.fuse_combined(combined, weights.text, 0.0, weights.graph, /*centrality*/ 0.0_or_field, weights.mode, max_results)`. (Add a `centrality` field to `FusionWeights` too if you want centrality in the text+graph path; otherwise pass 0.0.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib index::tests::text_graph_query_uses_rrf_by_default`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/index.rs
git commit -m "refactor(core): share RRF fuse path between hybrid and text+graph queries"
```

---

## Task 6: Surface fusion controls through pygrap

**Files:**
- Modify: `crates/pygrap/src/indexed_graph.rs:478` (`search_and_traverse_hybrid`)
- Test: `crates/pygrap/smoke_test.py`

- [ ] **Step 1: Write the failing test**

Extend `crates/pygrap/smoke_test.py` to call the hybrid query with `fusion_mode="rrf"` and assert results return and expose `channel_ranks`:

```python
hits = g.search_and_traverse_hybrid(
    "memory safety", vector=None, max_seeds=5, required_properties=[],
    min_strength=0.0, max_depth=2, max_results=10,
    text_weight=1.0, vector_weight=0.6, graph_weight=0.5,
    centrality_weight=0.4, fusion_mode="rrf", rrf_k=60.0,
)
assert hits, "expected results"
print("channel_ranks:", getattr(hits[0], "channel_ranks", None))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `maturin develop --manifest-path crates/pygrap/Cargo.toml --release && python crates/pygrap/smoke_test.py`
Expected: FAIL — unexpected kwargs `centrality_weight`/`fusion_mode`/`rrf_k`.

- [ ] **Step 3: Write minimal implementation**

Add the params to the PyO3 signature and translate `fusion_mode: &str` → `FusionMode`:

```rust
#[pyo3(signature = (query, vector=None, max_seeds=8, required_properties=vec![],
    min_strength=0.0, max_depth=3, max_results=20,
    text_weight=1.0, vector_weight=0.6, graph_weight=0.5,
    centrality_weight=0.4, traversal_weight_property=None,
    fusion_mode="rrf", rrf_k=60.0))]
#[allow(clippy::too_many_arguments)]
fn search_and_traverse_hybrid(/* ...existing args..., */
    centrality_weight: f64, fusion_mode: &str, rrf_k: f64,
) -> PyResult<Vec<QueryResult>> {
    let mode = match fusion_mode {
        "linear" => grap::index::FusionMode::Linear,
        _ => grap::index::FusionMode::Rrf { k: rrf_k },
    };
    let weights = grap::HybridWeights {
        text: text_weight, vector: vector_weight, graph: graph_weight,
        centrality: centrality_weight, traversal_weight_property, mode,
    };
    // ...call inner.search_and_traverse_hybrid(...) as before...
}
```

Expose `channel_ranks` on the Python `QueryResult` wrapper (a `Vec<(String,u32)>` getter).

- [ ] **Step 4: Run test to verify it passes**

Run: `maturin develop --manifest-path crates/pygrap/Cargo.toml --release && python crates/pygrap/smoke_test.py`
Expected: PASS, prints channel ranks.

- [ ] **Step 5: Commit**

```bash
git add crates/pygrap/src/indexed_graph.rs crates/pygrap/smoke_test.py
git commit -m "feat(pygrap): expose fusion_mode, rrf_k, centrality_weight + channel_ranks"
```

---

## Self-Review

- **Spec coverage:** RRF primitive (T1), mask-aware PageRank (T2), `FusionMode`+weights (T3), hybrid refactor + centrality channel + lazy cache (T4), shared into text+graph path (T5), pygrap surface (T6). Covered.
- **Type consistency:** `RankedChannel{name,weight,ranked}`, `FusedNode{node,score,contributions,ranks}`, `FusionMode::{Linear,Rrf{k}}`, `DEFAULT_RRF_K` referenced identically across tasks. `CombinedResult.channel_ranks: Vec<(&'static str,u32)>` matches `FusedNode.ranks`. pygrap `fusion_mode` strings `"rrf"|"linear"` map to the enum in T6.
- **Back-compat:** `FusionMode::Linear` reproduces the exact prior formula; new struct fields use `..Default::default()` at call sites; the legacy ranking test is preserved (Linear) or split. Default flips to RRF — call this out in the changelog/PR since top-N ordering can change.
- **Mission fit:** no new deps, all hot-path math in the core, MCP unaffected. PageRank is the same primitive reused by the P1 graph-analytics work — build it once here.
