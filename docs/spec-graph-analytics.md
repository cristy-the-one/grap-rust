# Graph Analytics over the KG (Rust core + pygrap + MCP) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose grap's graph as something you can *analyse*, not just query — blast-radius (what depends on a node), dependency cycles, orphan/dead-knowledge, and a structural health report — implemented in the Rust core over the existing arena adjacency, surfaced through pygrap and the MCP.

**Architecture:** A new `src/analytics.rs` core module computes everything from primitives that already exist (`KnowledgeGraph::{all_edges, edge_endpoints, node_count, is_node_deleted}`, `Edge::matches_mask`, and `crate::ranking::pagerank`). No new graph state. `IndexedKnowledgeGraph` delegates; pygrap exposes Python-friendly wrappers; `grap_mcp` adds an analytics tool group. **All compute is in the core** (graph traversal is the hot, embeddable part); the MCP layer is a thin presentation skin.

**Tech Stack:** Rust (std only — no new deps), PyO3, Python (`grap_mcp`), `cargo test` + `pytest`.

**Scope discipline (what this is NOT):** No edge-semantic metrics (confidence/provenance/lifetime/visit_count) — those properties aren't in the core yet, so a "quality grade" built on them would be fiction. This ships purely **structural** analytics. Wiring cycle-detection into `promote_pg` is a noted follow-up, not part of this plan.

---

## File Structure
- **Create** `src/analytics.rs` — `blast_radius`, `find_cycles`, `find_orphans`, `health_report` + `HealthReport`; `pub mod analytics;` in `src/lib.rs`.
- **Modify** `src/index.rs` — delegating methods on `IndexedKnowledgeGraph`.
- **Modify** `crates/pygrap/src/indexed_graph.rs` — PyO3 wrappers returning lists/dicts.
- **Modify** `mcp-server/grap_mcp/server.py` — `@mcp.tool()` analytics group.
- **Tests** `tests/analytics.rs`, unit tests in `analytics.rs`, `mcp-server/tests/test_analytics.py`, `crates/pygrap/smoke_test.py`.

Reuse from prior work: `crate::ranking::pagerank(g, mask, damping, iters)`, `Edge::matches_mask(u64)`, `g.properties.build_mask(&[..]) -> u64`.

---

## Task 1: `blast_radius` — reverse masked BFS

**Files:** Create `src/analytics.rs`; add `pub mod analytics;` to `src/lib.rs`. Test: unit test in `analytics.rs`.

- [ ] **Step 1: Failing test** — create `src/analytics.rs`:

```rust
//! Structural graph analytics over the knowledge graph: impact, cycles,
//! orphans, and a structural health report. All compute reuses existing
//! adjacency primitives; no new graph state.
use crate::ids::NodeId;
use crate::KnowledgeGraph;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blast_radius_finds_transitive_dependents() {
        // a -> b -> c   (edges point "depends-on": a depends on b, b depends on c)
        // changing c impacts {b, a}; changing a impacts {} (nothing depends on a)
        let mut g = KnowledgeGraph::new();
        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        let c = g.add_node("c", None, None);
        g.add_edge(a, b, 1.0, vec![]);
        g.add_edge(b, c, 1.0, vec![]);
        let mut impacted = blast_radius(&g, c, None, 10);
        impacted.sort();
        assert_eq!(impacted, vec![a, b]);
        assert!(blast_radius(&g, a, None, 10).is_empty());
    }
}
```
Add `pub mod analytics;` to `src/lib.rs`.

- [ ] **Step 2: Run, verify FAIL** — `cargo test -p grap --lib analytics::tests::blast_radius_finds_transitive_dependents` → `blast_radius` undefined.

- [ ] **Step 3: Implement** (module level in `analytics.rs`):

```rust
use crate::ids::EdgeId;
use std::collections::VecDeque;

/// Build masked INCOMING adjacency: incoming[t] = nodes s with edge s->t
/// (optionally restricted to edges whose property bits satisfy `mask`).
/// Ghost edges to/from tombstoned nodes are excluded.
fn incoming_adjacency(g: &KnowledgeGraph, mask: Option<u64>) -> Vec<Vec<u32>> {
    let n = g.node_count();
    let live: Vec<bool> = (0..n).map(|i| !g.is_node_deleted(NodeId(i as u32))).collect();
    let mut inc: Vec<Vec<u32>> = vec![Vec::new(); n];
    for (i, edge) in g.all_edges().iter().enumerate() {
        if let Some(m) = mask {
            if !edge.matches_mask(m) {
                continue;
            }
        }
        let (s, t) = g.edge_endpoints(EdgeId(i as u32));
        let (s, t) = (s.0 as usize, t.0 as usize);
        if live[s] && live[t] {
            inc[t].push(s as u32);
        }
    }
    inc
}

/// Transitive dependents of `start`: every node that can reach `start` by
/// following edges in reverse (i.e. nodes that "depend on" start), up to
/// `max_depth` hops. `start` itself is NOT included. Honors `mask`.
pub fn blast_radius(g: &KnowledgeGraph, start: NodeId, mask: Option<u64>, max_depth: u32) -> Vec<NodeId> {
    let n = g.node_count();
    if (start.0 as usize) >= n || g.is_node_deleted(start) {
        return Vec::new();
    }
    let inc = incoming_adjacency(g, mask);
    let mut seen = vec![false; n];
    seen[start.0 as usize] = true;
    let mut out = Vec::new();
    let mut q: VecDeque<(u32, u32)> = VecDeque::new();
    q.push_back((start.0, 0));
    while let Some((node, depth)) = q.pop_front() {
        if depth >= max_depth {
            continue;
        }
        for &pred in &inc[node as usize] {
            if !seen[pred as usize] {
                seen[pred as usize] = true;
                out.push(NodeId(pred));
                q.push_back((pred, depth + 1));
            }
        }
    }
    out
}
```

- [ ] **Step 4: Run, verify PASS** — `cargo test -p grap --lib analytics::tests` then `cargo build -p grap` (clean; `HealthReport` not added yet so no missing-Debug issue).

- [ ] **Step 5: Commit**
```
git add src/analytics.rs src/lib.rs
git commit -m "feat(core): blast_radius (reverse masked BFS) in src/analytics.rs"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: `find_cycles` — Tarjan SCC over masked forward adjacency

**Files:** Modify `src/analytics.rs`. Test: unit test.

- [ ] **Step 1: Failing test**
```rust
#[test]
fn find_cycles_detects_a_loop() {
    let mut g = KnowledgeGraph::new();
    let a = g.add_node("a", None, None);
    let b = g.add_node("b", None, None);
    let c = g.add_node("c", None, None);
    let d = g.add_node("d", None, None);
    g.add_edge(a, b, 1.0, vec![]);
    g.add_edge(b, c, 1.0, vec![]);
    g.add_edge(c, a, 1.0, vec![]); // a->b->c->a is a cycle
    g.add_edge(c, d, 1.0, vec![]); // d is acyclic
    let cycles = find_cycles(&g, None);
    assert_eq!(cycles.len(), 1);
    let mut members = cycles[0].clone();
    members.sort();
    assert_eq!(members, vec![a, b, c]);
}
```

- [ ] **Step 2: Run, verify FAIL** — `find_cycles` undefined.

- [ ] **Step 3: Implement** (iterative Tarjan to avoid recursion blowup):
```rust
/// Build masked OUTGOING adjacency (live nodes only).
fn outgoing_adjacency(g: &KnowledgeGraph, mask: Option<u64>) -> Vec<Vec<u32>> {
    let n = g.node_count();
    let live: Vec<bool> = (0..n).map(|i| !g.is_node_deleted(NodeId(i as u32))).collect();
    let mut out: Vec<Vec<u32>> = vec![Vec::new(); n];
    for (i, edge) in g.all_edges().iter().enumerate() {
        if let Some(m) = mask {
            if !edge.matches_mask(m) {
                continue;
            }
        }
        let (s, t) = g.edge_endpoints(EdgeId(i as u32));
        let (s, t) = (s.0 as usize, t.0 as usize);
        if live[s] && live[t] {
            out[s].push(t as u32);
        }
    }
    out
}

/// Find dependency cycles via Tarjan's SCC algorithm over the (masked) forward
/// graph. Returns each strongly-connected component of size > 1, plus any
/// self-loop node as a singleton cycle. Order within a cycle is unspecified.
pub fn find_cycles(g: &KnowledgeGraph, mask: Option<u64>) -> Vec<Vec<NodeId>> {
    let n = g.node_count();
    let adj = outgoing_adjacency(g, mask);
    let self_loop: Vec<bool> = (0..n).map(|i| adj[i].contains(&(i as u32))).collect();

    let mut index = vec![u32::MAX; n];
    let mut lowlink = vec![0u32; n];
    let mut on_stack = vec![false; n];
    let mut stack: Vec<u32> = Vec::new();
    let mut idx_counter: u32 = 0;
    let mut sccs: Vec<Vec<NodeId>> = Vec::new();

    // Iterative Tarjan: frame = (node, next-neighbor-index).
    for root in 0..n {
        if index[root] != u32::MAX || g.is_node_deleted(NodeId(root as u32)) {
            continue;
        }
        let mut call_stack: Vec<(u32, usize)> = vec![(root as u32, 0)];
        index[root] = idx_counter;
        lowlink[root] = idx_counter;
        idx_counter += 1;
        stack.push(root as u32);
        on_stack[root] = true;

        while let Some(&(v, ni)) = call_stack.last() {
            let vu = v as usize;
            if ni < adj[vu].len() {
                call_stack.last_mut().unwrap().1 += 1;
                let w = adj[vu][ni];
                let wu = w as usize;
                if index[wu] == u32::MAX {
                    index[wu] = idx_counter;
                    lowlink[wu] = idx_counter;
                    idx_counter += 1;
                    stack.push(w);
                    on_stack[wu] = true;
                    call_stack.push((w, 0));
                } else if on_stack[wu] {
                    lowlink[vu] = lowlink[vu].min(index[wu]);
                }
            } else {
                // done with v: if root of an SCC, pop it.
                if lowlink[vu] == index[vu] {
                    let mut comp: Vec<NodeId> = Vec::new();
                    loop {
                        let w = stack.pop().unwrap();
                        on_stack[w as usize] = false;
                        comp.push(NodeId(w));
                        if w == v {
                            break;
                        }
                    }
                    if comp.len() > 1 || self_loop[vu] {
                        sccs.push(comp);
                    }
                }
                call_stack.pop();
                if let Some(&(parent, _)) = call_stack.last() {
                    lowlink[parent as usize] = lowlink[parent as usize].min(lowlink[vu]);
                }
            }
        }
    }
    sccs
}
```

- [ ] **Step 4: Run, verify PASS** — `cargo test -p grap --lib analytics::tests::find_cycles_detects_a_loop` + `cargo build -p grap`.

- [ ] **Step 5: Commit**
```
git add src/analytics.rs
git commit -m "feat(core): find_cycles via iterative Tarjan SCC (masked)"
```

---

## Task 3: `find_orphans` — unreachable from entry points / roots

**Files:** Modify `src/analytics.rs`. Test: unit test.

- [ ] **Step 1: Failing test**
```rust
#[test]
fn find_orphans_returns_unreachable_nodes() {
    let mut g = KnowledgeGraph::new();
    let root = g.add_node("root", None, None);
    let child = g.add_node("child", None, None);
    let island = g.add_node("island", None, None); // unreachable from root
    g.add_edge(root, child, 1.0, vec![]);
    // entry = [root]; reachable = {root, child}; orphan = {island}
    assert_eq!(find_orphans(&g, &[root]), vec![island]);
    // empty entry points => default roots = nodes with no incoming edges = {root, island};
    // island is then itself an entry, so orphans = {}.
    assert!(find_orphans(&g, &[]).is_empty());
}
```

- [ ] **Step 2: Run, verify FAIL** — `find_orphans` undefined.

- [ ] **Step 3: Implement**
```rust
/// Live nodes not forward-reachable from `entry_points`. When `entry_points`
/// is empty, the default entry set is every "root" (a live node with no
/// incoming edge). Follows all edges (unmasked). Useful for finding
/// dead/disconnected knowledge.
pub fn find_orphans(g: &KnowledgeGraph, entry_points: &[NodeId]) -> Vec<NodeId> {
    let n = g.node_count();
    let live: Vec<bool> = (0..n).map(|i| !g.is_node_deleted(NodeId(i as u32))).collect();
    let adj = outgoing_adjacency(g, None);

    let entries: Vec<u32> = if entry_points.is_empty() {
        // roots = live nodes with no incoming edge
        let inc = incoming_adjacency(g, None);
        (0..n)
            .filter(|&i| live[i] && inc[i].is_empty())
            .map(|i| i as u32)
            .collect()
    } else {
        entry_points
            .iter()
            .filter(|nid| (nid.0 as usize) < n && live[nid.0 as usize])
            .map(|nid| nid.0)
            .collect()
    };

    let mut reached = vec![false; n];
    let mut q: VecDeque<u32> = VecDeque::new();
    for e in entries {
        if !reached[e as usize] {
            reached[e as usize] = true;
            q.push_back(e);
        }
    }
    while let Some(v) = q.pop_front() {
        for &w in &adj[v as usize] {
            if !reached[w as usize] {
                reached[w as usize] = true;
                q.push_back(w);
            }
        }
    }
    (0..n)
        .filter(|&i| live[i] && !reached[i])
        .map(|i| NodeId(i as u32))
        .collect()
}
```

- [ ] **Step 4: Run, verify PASS** — `cargo test -p grap --lib analytics::tests::find_orphans_returns_unreachable_nodes` + `cargo build -p grap`.

- [ ] **Step 5: Commit**
```
git add src/analytics.rs
git commit -m "feat(core): find_orphans (reachability from entry points / roots)"
```

---

## Task 4: `health_report` — structural summary (no fictional grade)

**Files:** Modify `src/analytics.rs`. Test: unit test.

- [ ] **Step 1: Failing test**
```rust
#[test]
fn health_report_counts_structure() {
    let mut g = KnowledgeGraph::new();
    let a = g.add_node("a", None, None);
    let b = g.add_node("b", None, None);
    let _iso = g.add_node("iso", None, None); // isolated
    g.add_edge(a, b, 1.0, vec![]);
    let r = health_report(&g);
    assert_eq!(r.live_nodes, 3);
    assert_eq!(r.edges, 1);
    assert_eq!(r.isolated_nodes, 1);     // _iso
    assert_eq!(r.cycle_count, 0);
    assert_eq!(r.top_central.len().min(3), r.top_central.len()); // <=3 returned
}
```

- [ ] **Step 2: Run, verify FAIL** — `health_report`/`HealthReport` undefined.

- [ ] **Step 3: Implement**
```rust
/// A structural snapshot of the graph. Deliberately structural-only — no
/// confidence/provenance/quality scoring (those edge semantics don't exist
/// in the core). `top_central` is the top-N nodes by PageRank.
#[derive(Debug, Clone)]
pub struct HealthReport {
    pub live_nodes: usize,
    pub id_space: usize,
    pub edges: usize,
    pub isolated_nodes: usize,
    pub root_nodes: usize,
    pub leaf_nodes: usize,
    pub cycle_count: usize,
    pub orphan_count: usize,
    /// (node, pagerank) for the top-N most central nodes, descending.
    pub top_central: Vec<(NodeId, f64)>,
}

/// Compute a structural health report. `top_n` central nodes (default 5).
pub fn health_report(g: &KnowledgeGraph) -> HealthReport {
    health_report_top(g, 5)
}

pub fn health_report_top(g: &KnowledgeGraph, top_n: usize) -> HealthReport {
    let n = g.node_count();
    let live: Vec<bool> = (0..n).map(|i| !g.is_node_deleted(NodeId(i as u32))).collect();
    let live_nodes = live.iter().filter(|&&b| b).count();
    let out_adj = outgoing_adjacency(g, None);
    let in_adj = incoming_adjacency(g, None);

    let mut isolated = 0usize;
    let mut roots = 0usize;
    let mut leaves = 0usize;
    let mut edges = 0usize;
    for i in 0..n {
        if !live[i] {
            continue;
        }
        let (od, idg) = (out_adj[i].len(), in_adj[i].len());
        edges += od;
        if od == 0 && idg == 0 {
            isolated += 1;
        }
        if idg == 0 {
            roots += 1;
        }
        if od == 0 {
            leaves += 1;
        }
    }

    let cycle_count = find_cycles(g, None).len();
    let orphan_count = find_orphans(g, &[]).len();

    let pr = crate::ranking::pagerank(g, None, 0.85, 30);
    let mut ranked: Vec<(NodeId, f64)> = (0..n)
        .filter(|&i| live[i])
        .map(|i| (NodeId(i as u32), pr[i]))
        .collect();
    ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    ranked.truncate(top_n);

    HealthReport {
        live_nodes,
        id_space: n,
        edges,
        isolated_nodes: isolated,
        root_nodes: roots,
        leaf_nodes: leaves,
        cycle_count,
        orphan_count,
        top_central: ranked,
    }
}
```

- [ ] **Step 4: Run, verify PASS** — `cargo test -p grap --lib analytics::tests` (all) + `cargo build -p grap` (clean; `HealthReport` derives Debug).

- [ ] **Step 5: Commit**
```
git add src/analytics.rs
git commit -m "feat(core): structural health_report (counts, cycles, orphans, top-central)"
```

---

## Task 5: Surface on `IndexedKnowledgeGraph` + pygrap

**Files:** Modify `src/index.rs` (delegators), `crates/pygrap/src/indexed_graph.rs` (PyO3). Test: `src/index.rs` unit test + `crates/pygrap/smoke_test.py`.

- [ ] **Step 1: Failing test** (Rust delegation) — in `src/index.rs` tests:
```rust
#[test]
fn indexed_exposes_analytics() {
    let mut g = IndexedKnowledgeGraph::new();
    let a = g.add_node("a", None, None);
    let b = g.add_node("b", None, None);
    g.add_edge(a, b, 1.0, vec![]);
    assert_eq!(g.blast_radius(b, None, 5), vec![a]);
    assert_eq!(g.find_cycles(None).len(), 0);
    assert_eq!(g.health().live_nodes, 2);
}
```

- [ ] **Step 2: Run, verify FAIL** — methods missing.

- [ ] **Step 3: Implement** — `src/index.rs` (delegate to `self.graph`; place near `vector_search`):
```rust
/// Transitive dependents of a node (see `analytics::blast_radius`).
pub fn blast_radius(&self, node: NodeId, mask: Option<u64>, max_depth: u32) -> Vec<NodeId> {
    crate::analytics::blast_radius(&self.graph, node, mask, max_depth)
}
/// Dependency cycles (see `analytics::find_cycles`).
pub fn find_cycles(&self, mask: Option<u64>) -> Vec<Vec<NodeId>> {
    crate::analytics::find_cycles(&self.graph, mask)
}
/// Orphans / unreachable nodes (see `analytics::find_orphans`).
pub fn find_orphans(&self, entry_points: &[NodeId]) -> Vec<NodeId> {
    crate::analytics::find_orphans(&self.graph, entry_points)
}
/// Structural health report (see `analytics::health_report`).
pub fn health(&self) -> crate::analytics::HealthReport {
    crate::analytics::health_report(&self.graph)
}
```
   pygrap (`crates/pygrap/src/indexed_graph.rs`) — Python-friendly returns (node ids as `u32`, masks built from property-name lists, health as a dict). Match the crate's pyo3 conventions:
```rust
/// Transitive dependents of `node_id`. `relations` optionally restricts to
/// edges carrying those property names.
#[pyo3(signature = (node_id, relations=None, max_depth=10))]
fn blast_radius(&self, node_id: u32, relations: Option<Vec<String>>, max_depth: u32) -> Vec<u32> {
    let mask = relations.as_ref().map(|r| {
        let refs: Vec<&str> = r.iter().map(|s| s.as_str()).collect();
        self.inner.graph().properties_build_mask(&refs)   // see note
    });
    self.inner
        .blast_radius(grap::NodeId(node_id), mask, max_depth)
        .into_iter().map(|n| n.0).collect()
}

fn find_cycles(&self) -> Vec<Vec<u32>> {
    self.inner.find_cycles(None).into_iter()
        .map(|c| c.into_iter().map(|n| n.0).collect()).collect()
}

#[pyo3(signature = (entry_points=None))]
fn find_orphans(&self, entry_points: Option<Vec<u32>>) -> Vec<u32> {
    let eps: Vec<grap::NodeId> = entry_points.unwrap_or_default()
        .into_iter().map(grap::NodeId).collect();
    self.inner.find_orphans(&eps).into_iter().map(|n| n.0).collect()
}

fn health(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
    let h = self.inner.health();
    let d = pyo3::types::PyDict::new(py);   // match crate's PyDict ctor (new vs new_bound)
    d.set_item("live_nodes", h.live_nodes)?;
    d.set_item("id_space", h.id_space)?;
    d.set_item("edges", h.edges)?;
    d.set_item("isolated_nodes", h.isolated_nodes)?;
    d.set_item("root_nodes", h.root_nodes)?;
    d.set_item("leaf_nodes", h.leaf_nodes)?;
    d.set_item("cycle_count", h.cycle_count)?;
    d.set_item("orphan_count", h.orphan_count)?;
    let tc: Vec<(u32, f64)> = h.top_central.into_iter().map(|(n, s)| (n.0, s)).collect();
    d.set_item("top_central", tc)?;
    Ok(d.into())
}
```
   **Mask accessor note:** the core needs a way for pygrap to build a `u64` mask from property names without exposing internals. Add a small `pub fn build_property_mask(&self, names: &[&str]) -> u64` to `IndexedKnowledgeGraph` (delegating to `self.graph.properties.build_mask(names)`) and call THAT from pygrap (replace the `properties_build_mask` placeholder above). Verify `KnowledgeGraph` exposes `properties` (it does: `g.properties.build_mask`).

- [ ] **Step 4: Verify** — `cargo test -p grap --lib index::tests::indexed_exposes_analytics`; `cargo build -p pygrap`; `maturin develop --manifest-path crates/pygrap/Cargo.toml --release -i C:/Python313/python.exe` (or the crate's usual maturin invocation) then extend `smoke_test.py` to call `blast_radius`/`find_cycles`/`find_orphans`/`health` and assert shapes; `python crates/pygrap/smoke_test.py`. If maturin unavailable, `cargo build -p pygrap` is the gate; note it.

- [ ] **Step 5: Commit**
```
git add src/index.rs crates/pygrap/src/indexed_graph.rs crates/pygrap/smoke_test.py
git commit -m "feat(pygrap): expose graph analytics (blast_radius, cycles, orphans, health)"
```

---

## Task 6: MCP analytics tool group

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: `mcp-server/tests/test_analytics.py`.

- [ ] **Step 1: Failing test** — create `mcp-server/tests/test_analytics.py` mirroring `test_graph_neighbors_wire.py`'s `_FakeKG`/`monkeypatch` style. Inject a fake KG exposing `blast_radius`, `find_cycles`, `find_orphans`, `health`, and assert each tool returns the expected dict shape. Example:
```python
import grap_mcp.server as s

class _FakeKG:
    def blast_radius(self, node_id, relations=None, max_depth=10): return [1, 2]
    def find_cycles(self): return [[3, 4]]
    def find_orphans(self, entry_points=None): return [9]
    def health(self): return {"live_nodes": 3, "edges": 1, "cycle_count": 0}

def test_get_blast_radius(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _FakeKG())
    out = s.get_blast_radius(5)
    assert out["status"] == "ok"
    assert out["impacted"] == [1, 2]

def test_kg_health(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _FakeKG())
    out = s.kg_health()
    assert out["live_nodes"] == 3
```
(Match the real `_state`/`_require` conventions used by `graph_neighbors` — read it first. `graph_neighbors` requires the `operator` role via `_require("operator")`; analytics are read-only, so use `_require("viewer")` if that's the read tier, else mirror `graph_neighbors`.)

- [ ] **Step 2: Run, verify FAIL** — tools undefined.

- [ ] **Step 3: Implement** — add four `@mcp.tool()` functions to `server.py`, modeled on `graph_neighbors` (role check, `kg = _state.get("kg")`, error if none, try/except):
```python
@mcp.tool()
def get_blast_radius(node_id: int, relations: list = None, max_depth: int = 10) -> dict:
    """Transitive dependents of a node (what would be impacted if it changes)."""
    denied = _require("viewer")
    if denied: return denied
    kg = _state.get("kg")
    if not kg: return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        return {"status": "ok", "center": node_id,
                "impacted": kg.blast_radius(node_id, relations, max_depth)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def find_cycles() -> dict:
    """Dependency cycles in the current KG (strongly-connected components)."""
    denied = _require("viewer")
    if denied: return denied
    kg = _state.get("kg")
    if not kg: return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        cycles = kg.find_cycles()
        return {"status": "ok", "cycle_count": len(cycles), "cycles": cycles}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def find_orphans(entry_points: list = None) -> dict:
    """Live nodes unreachable from the given entry points (or from KG roots)."""
    denied = _require("viewer")
    if denied: return denied
    kg = _state.get("kg")
    if not kg: return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        orphans = kg.find_orphans(entry_points)
        return {"status": "ok", "orphan_count": len(orphans), "orphans": orphans}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def kg_health() -> dict:
    """Structural health snapshot of the current KG."""
    denied = _require("viewer")
    if denied: return denied
    kg = _state.get("kg")
    if not kg: return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        h = kg.health()
        h["status"] = "ok"
        return h
    except Exception as e:
        return {"status": "error", "message": str(e)}
```
   Confirm `_require` exists and the read tier name (`viewer`); if the role helper differs, mirror exactly what `graph_neighbors` does. If `list_capabilities` enumerates tools from a registry/manifest, add these four there too so `test_capabilities_cover_all_registered_tools` passes.

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_analytics.py -q`; then `python -m pytest -q` (full suite — `test_capabilities_cover_all_registered_tools` must still pass; if it fails, register the new tools in the capabilities list).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_analytics.py
git commit -m "feat(mcp): analytics tools (get_blast_radius, find_cycles, find_orphans, kg_health)"
```

---

## Self-Review
- **Coverage:** blast_radius (T1), cycles (T2), orphans (T3), health (T4), core→pygrap surface (T5), MCP tools (T6). All structural; no dependence on unbuilt edge semantics.
- **Type consistency:** `blast_radius(g, NodeId, Option<u64>, u32) -> Vec<NodeId>`, `find_cycles(g, Option<u64>) -> Vec<Vec<NodeId>>`, `find_orphans(g, &[NodeId]) -> Vec<NodeId>`, `health_report(g) -> HealthReport` consistent across core→index→pygrap. Shared `incoming_adjacency`/`outgoing_adjacency` helpers reused by T1–T4.
- **Reuse:** `ranking::pagerank` (T4 top_central), `Edge::matches_mask`, `properties.build_mask` — no duplication.
- **Follow-up (out of scope):** wiring `find_cycles`/reachability into the `promote_pg` validation gate; per-`kg_version` health diff.
