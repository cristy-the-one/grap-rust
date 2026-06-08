//! Rank-based fusion and graph-centrality scoring for query ranking.
use crate::ids::NodeId;
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
#[derive(Debug, Clone)]
pub struct FusedNode {
    /// The node being scored.
    pub node: NodeId,
    /// Aggregate RRF score (sum of weighted reciprocal ranks across channels).
    pub score: f64,
    /// `(channel_name, contribution)` pairs, for explanations / debugging.
    pub contributions: Vec<(&'static str, f64)>,
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
}
