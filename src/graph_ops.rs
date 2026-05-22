//! Structural mutations on `KnowledgeGraph` — node merging and content update.
//!
//! These operations write to the graph topology and/or node content. They are
//! kept separate from `graph.rs` (which owns the arena layout) to maintain
//! a clear boundary between storage and mutation policy.

use crate::graph::KnowledgeGraph;
use crate::ids::NodeId;
use crate::GraphError;

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
    /// Replace a node's text content (and optionally its embedding) in-place.
    ///
    /// **Edges are never touched.** All outgoing/incoming adjacency lists remain
    /// intact so existing topology is fully preserved.
    ///
    /// Old blobs are orphaned in the append-only `BlobStore` and are not freed.
    /// A future `vacuum()` compaction can reclaim them.
    ///
    /// # Errors
    /// Returns `GraphError::Other` if `id` is out of bounds or has been deleted.
    pub(crate) fn update_node_content(
        &mut self,
        id: NodeId,
        new_text: &str,
        new_embedding: Option<&[f32]>,
    ) -> Result<(), GraphError> {
        let idx = id.0 as usize;
        if idx >= self.nodes.len() || self.deleted.get(idx).copied().unwrap_or(true) {
            return Err(GraphError::Other(format!(
                "update_node_content: node {:?} is out of bounds or deleted",
                id
            )));
        }
        let new_blob = self.blobs.store(new_text);
        self.nodes[idx].blob = new_blob;
        if let Some(emb) = new_embedding {
            self.nodes[idx].embedding = Some(self.blobs.store_embedding(emb));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::KnowledgeGraph;
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

    #[test]
    fn update_node_content_changes_blob() {
        let mut g = KnowledgeGraph::new();
        let id = g.add_node("original text", None, None);
        assert_eq!(g.read_blob(id), "original text");

        g.update_node_content(id, "updated text", None).unwrap();
        assert_eq!(g.read_blob(id), "updated text");
    }

    #[test]
    fn update_node_content_preserves_edges() {
        let mut g = KnowledgeGraph::new();
        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        g.add_edge(a, b, 0.9, vec![]);

        g.update_node_content(a, "a updated", None).unwrap();

        // Edge must survive
        assert_eq!(g.nodes[a.0 as usize].outgoing.len(), 1);
    }

    #[test]
    fn update_node_content_rejects_deleted_node() {
        let mut g = KnowledgeGraph::new();
        let id = g.add_node("x", None, None);
        g.remove_node(id);

        let result = g.update_node_content(id, "y", None);
        assert!(result.is_err());
    }

    #[test]
    fn update_node_content_updates_embedding() {
        let mut g = KnowledgeGraph::new();
        let id = g.add_node("node", None, Some(&[1.0f32, 0.0]));
        assert!(g.nodes[id.0 as usize].embedding.is_some());

        g.update_node_content(id, "node", Some(&[0.0f32, 1.0])).unwrap();

        let emb = g.read_embedding(id).unwrap();
        assert!((emb[1] - 1.0f32).abs() < 1e-6);
    }
}
