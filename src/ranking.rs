//! Rank-based fusion and graph-centrality scoring for query ranking.
use crate::ids::{EdgeId, NodeId};
use crate::KnowledgeGraph;
use std::collections::HashMap;

/// One channel's ranked output: an ordered list of node ids, best first.
#[derive(Debug)]
pub struct RankedChannel {
    /// Human-readable name for this ranking channel (e.g. `"bm25"`, `"vector"`).
    pub name: &'static str,
    /// Relative weight for this channel's contributions. Higher → more influence.
    pub weight: f64,
    /// Node ids ordered best-first for this channel.
    pub ranked: Vec<NodeId>,
}

/// A node's fused score with per-channel diagnostics.
///
/// Per-channel contribution amounts are derivable from `ranks` and the
/// channel weights (contribution = weight / (k + rank)) if ever needed.
#[derive(Debug, Clone)]
pub struct FusedNode {
    /// The node being scored.
    pub node: NodeId,
    /// Aggregate RRF score (sum of weighted reciprocal ranks across channels).
    pub score: f64,
    /// `(channel_name, 1-based rank)` pairs.
    pub ranks: Vec<(&'static str, u32)>,
}

/// Default RRF smoothing constant. Higher → less top-heavy.
pub const DEFAULT_RRF_K: f64 = 60.0;

/// Weighted Reciprocal Rank Fusion.
///
/// Each channel contributes `weight / (k + rank)` (1-based rank) to each node
/// it ranks; scores are summed across channels. Robust to incomparable
/// per-channel score scales.
///
/// # Arguments
/// * `channels` – Slice of ranked channels. Each channel provides an ordered
///   list of `NodeId`s (best first) and a weight.
/// * `k` – Smoothing constant (use [`DEFAULT_RRF_K`] = 60.0 as a sane default).
///
/// # Returns
/// A `Vec<FusedNode>` sorted descending by fused score.
pub fn rrf_fuse(channels: &[RankedChannel], k: f64) -> Vec<FusedNode> {
    let mut accum: HashMap<NodeId, FusedNode> = HashMap::new();
    for ch in channels {
        for (rank0, &nid) in ch.ranked.iter().enumerate() {
            let rank1 = (rank0 + 1) as u32;
            let contribution = ch.weight / (k + rank1 as f64);
            let entry = accum.entry(nid).or_insert_with(|| FusedNode {
                node: nid,
                score: 0.0,
                ranks: Vec::new(),
            });
            entry.score += contribution;
            entry.ranks.push((ch.name, rank1));
        }
    }
    let mut out: Vec<FusedNode> = accum.into_values().collect();
    out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    out
}

/// Iterative PageRank over the live node set. When `mask` is `Some`, only
/// edges whose property bits satisfy the mask participate (so callers can
/// ask for "centrality among `cites` edges"). `damping` ~0.85, `iters` ~30.
/// Returns a dense vector indexed by `NodeId.0` (tombstoned nodes get 0.0).
pub fn pagerank(g: &KnowledgeGraph, mask: Option<u64>, damping: f64, iters: u32) -> Vec<f64> {
    let n_total = g.node_count(); // id-space size incl. tombstones
    let live: Vec<bool> = (0..n_total)
        .map(|i| !g.is_node_deleted(NodeId(i as u32)))
        .collect();
    let live_count = live.iter().filter(|&&b| b).count().max(1) as f64;

    // Build outgoing adjacency honoring the optional mask.
    let mut out: Vec<Vec<u32>> = vec![Vec::new(); n_total];
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rrf_rewards_consensus_across_channels() {
        let ch_a = RankedChannel { name: "a", weight: 1.0, ranked: vec![NodeId(1), NodeId(2)] };
        let ch_b = RankedChannel { name: "b", weight: 1.0, ranked: vec![NodeId(1), NodeId(3)] };
        let fused = rrf_fuse(&[ch_a, ch_b], 60.0);
        assert_eq!(fused[0].node, NodeId(1)); // consensus wins
        assert!(fused[0].score > fused[1].score);
    }

    #[test]
    fn rrf_respects_channel_weight() {
        let strong = RankedChannel { name: "s", weight: 2.0, ranked: vec![NodeId(7)] };
        let weak = RankedChannel { name: "w", weight: 0.1, ranked: vec![NodeId(8)] };
        let fused = rrf_fuse(&[strong, weak], 60.0);
        assert_eq!(fused[0].node, NodeId(7));
    }

    #[test]
    fn pagerank_ranks_hub_above_leaf() {
        use crate::KnowledgeGraph;
        let mut g = KnowledgeGraph::new();
        let hub = g.add_node("hub", None, None);
        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        g.add_edge(a, hub, 1.0, vec![]);
        g.add_edge(b, hub, 1.0, vec![]);
        let pr = pagerank(&g, None, 0.85, 30);
        assert!(pr[hub.0 as usize] > pr[a.0 as usize]);
        assert!(pr[hub.0 as usize] > pr[b.0 as usize]);
    }

    #[test]
    fn pagerank_mask_excludes_nonmatching_edges() {
        use crate::properties::PropertyValue;
        use crate::KnowledgeGraph;
        let mut g = KnowledgeGraph::new();
        let src = g.add_node("src", None, None);
        let cited = g.add_node("cited", None, None);
        let plain = g.add_node("plain", None, None);
        let cites = g.properties.register("cites");
        // Only this edge carries the "cites" property — it should be followed under the mask.
        g.add_edge(src, cited, 1.0, vec![(cites, PropertyValue::Label("c".into()))]);
        // This edge has no matching property — excluded under the mask.
        g.add_edge(src, plain, 1.0, vec![]);
        let mask = g.properties.build_mask(&["cites"]);
        let pr = pagerank(&g, Some(mask), 0.85, 30);
        // Only the "cites" edge is followed, so `cited` receives src's propagated
        // mass while `plain` (reachable only via the untagged edge) does not.
        assert!(
            pr[cited.0 as usize] > pr[plain.0 as usize],
            "cited ({}) should rank above plain ({})",
            pr[cited.0 as usize],
            pr[plain.0 as usize]
        );
    }
}
