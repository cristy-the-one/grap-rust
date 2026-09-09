//! Structural graph analytics over the knowledge graph: impact, cycles,
//! orphans, and a structural health report. All compute reuses existing
//! adjacency primitives; no new graph state.
use crate::ids::{EdgeId, NodeId};
use crate::KnowledgeGraph;
use std::collections::VecDeque;

/// Masked INCOMING adjacency: inc[t] = source nodes s with live edge s->t.
fn incoming_adjacency(g: &KnowledgeGraph, mask: Option<u64>) -> Vec<Vec<u32>> {
    let n = g.node_count();
    let live: Vec<bool> = (0..n)
        .map(|i| !g.is_node_deleted(NodeId(i as u32)))
        .collect();
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

/// Masked OUTGOING adjacency: out[s] = target nodes t with live edge s->t.
fn outgoing_adjacency(g: &KnowledgeGraph, mask: Option<u64>) -> Vec<Vec<u32>> {
    let n = g.node_count();
    let live: Vec<bool> = (0..n)
        .map(|i| !g.is_node_deleted(NodeId(i as u32)))
        .collect();
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

/// Transitive dependents of `start`: every node that can reach `start` by
/// following edges in reverse (nodes that "depend on" `start`), up to
/// `max_depth` hops. `start` is not included. Honors `mask`.
pub fn blast_radius(
    g: &KnowledgeGraph,
    start: NodeId,
    mask: Option<u64>,
    max_depth: u32,
) -> Vec<NodeId> {
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

/// Find dependency cycles via Tarjan's SCC over the (masked) forward graph.
/// Returns each SCC of size > 1, plus any self-loop node as a singleton.
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

/// Live nodes not forward-reachable from `entry_points`. When empty, the
/// default entry set is every root (live node with no incoming edge).
/// Follows all edges (unmasked).
///
/// Note: if `entry_points` is non-empty but every id is out-of-range or
/// tombstoned, the effective entry set is empty (no root fallback applies),
/// so *every* live node is reported as an orphan. Pass an empty slice to get
/// the root-based default instead.
pub fn find_orphans(g: &KnowledgeGraph, entry_points: &[NodeId]) -> Vec<NodeId> {
    let n = g.node_count();
    let live: Vec<bool> = (0..n)
        .map(|i| !g.is_node_deleted(NodeId(i as u32)))
        .collect();
    let adj = outgoing_adjacency(g, None);
    let entries: Vec<u32> = if entry_points.is_empty() {
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

/// A structural snapshot of the graph. Structural-only — no
/// confidence/provenance scoring. `top_central` = top-N nodes by PageRank.
#[derive(Debug, Clone)]
pub struct HealthReport {
    /// Number of live (non-tombstoned) nodes.
    pub live_nodes: usize,
    /// Node id-space size, including tombstones.
    pub id_space: usize,
    /// Total live edge count.
    pub edges: usize,
    /// Live nodes with no incident edges.
    pub isolated_nodes: usize,
    /// Live nodes with no incoming edge.
    pub root_nodes: usize,
    /// Live nodes with no outgoing edge.
    pub leaf_nodes: usize,
    /// Number of dependency cycles (SCCs of size>1 or self-loops).
    pub cycle_count: usize,
    /// Number of orphan (root-unreachable) nodes.
    pub orphan_count: usize,
    /// Top-N (node, pagerank) by centrality, descending.
    pub top_central: Vec<(NodeId, f64)>,
}

/// Compute a structural health report with the top-5 central nodes.
pub fn health_report(g: &KnowledgeGraph) -> HealthReport {
    health_report_top(g, 5)
}

/// `health_report` with a configurable number of central nodes.
pub fn health_report_top(g: &KnowledgeGraph, top_n: usize) -> HealthReport {
    let n = g.node_count();
    let live: Vec<bool> = (0..n)
        .map(|i| !g.is_node_deleted(NodeId(i as u32)))
        .collect();
    let live_nodes = live.iter().filter(|&&b| b).count();
    let out_adj = outgoing_adjacency(g, None);
    let in_adj = incoming_adjacency(g, None);
    let (mut isolated, mut roots, mut leaves, mut edges) = (0usize, 0usize, 0usize, 0usize);
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
    ranked.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.0 .0.cmp(&b.0 .0)) // explicit tie-break by ascending NodeId
    });
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blast_radius_finds_transitive_dependents() {
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

    #[test]
    fn find_cycles_detects_a_loop() {
        let mut g = KnowledgeGraph::new();
        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        let c = g.add_node("c", None, None);
        let d = g.add_node("d", None, None);
        g.add_edge(a, b, 1.0, vec![]);
        g.add_edge(b, c, 1.0, vec![]);
        g.add_edge(c, a, 1.0, vec![]);
        g.add_edge(c, d, 1.0, vec![]);
        let cycles = find_cycles(&g, None);
        assert_eq!(cycles.len(), 1);
        let mut members = cycles[0].clone();
        members.sort();
        assert_eq!(members, vec![a, b, c]);
    }

    #[test]
    fn find_orphans_returns_unreachable_nodes() {
        let mut g = KnowledgeGraph::new();
        let root = g.add_node("root", None, None);
        let child = g.add_node("child", None, None);
        let island = g.add_node("island", None, None);
        g.add_edge(root, child, 1.0, vec![]);
        assert_eq!(find_orphans(&g, &[root]), vec![island]);
        assert!(find_orphans(&g, &[]).is_empty());
    }

    #[test]
    fn health_report_counts_structure() {
        let mut g = KnowledgeGraph::new();
        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        let _iso = g.add_node("iso", None, None);
        g.add_edge(a, b, 1.0, vec![]);
        let r = health_report(&g);
        assert_eq!(r.live_nodes, 3);
        assert_eq!(r.edges, 1);
        assert_eq!(r.isolated_nodes, 1);
        assert_eq!(r.cycle_count, 0);
        assert!(r.top_central.len() <= 3);
    }
}
