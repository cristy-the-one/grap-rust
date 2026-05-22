//! Structural mutations on `KnowledgeGraph` — node merging and content update.
//!
//! These operations write to the graph topology and/or node content. They are
//! kept separate from `graph.rs` (which owns the arena layout) to maintain
//! a clear boundary between storage and mutation policy.

use crate::graph::KnowledgeGraph;
use crate::ids::NodeId;

/// Summary of what changed after a [`KnowledgeGraph::merge_nodes`] call.
#[derive(Debug, Clone, PartialEq)]
pub struct MergeReport {
    /// The node that was absorbed (now soft-deleted).
    pub absorbed: NodeId,
    /// Edges from `absorbed` transplanted to `keep` with no duplicate.
    pub edges_redirected: usize,
    /// Edges that collided with an existing `keep` edge and were averaged.
    pub edges_merged: usize,
    /// Self-loop edges (keep↔absorb) that were dropped.
    pub self_loops_dropped: usize,
    /// `true` if `keep` gained `absorbed`'s embedding because it had none.
    pub embedding_updated: bool,
}

impl KnowledgeGraph {
    // merge_nodes and update_node_content will be added in later tasks.
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::NodeId;

    #[test]
    fn merge_report_default_fields() {
        let r = MergeReport {
            absorbed: NodeId(0),
            edges_redirected: 0,
            edges_merged: 0,
            self_loops_dropped: 0,
            embedding_updated: false,
        };
        assert_eq!(r.edges_redirected, 0);
        assert_eq!(r.edges_merged, 0);
        assert_eq!(r.self_loops_dropped, 0);
        assert!(!r.embedding_updated);
    }
}
