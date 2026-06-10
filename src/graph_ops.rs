//! Structural mutations on `KnowledgeGraph` — node merging and content update.
//!
//! These operations write to the graph topology and/or node content. They are
//! kept separate from `graph.rs` (which owns the arena layout) to maintain
//! a clear boundary between storage and mutation policy.

use crate::graph::KnowledgeGraph;
use crate::ids::NodeId;
use crate::GraphError;
use std::collections::{HashMap, HashSet};

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

/// Outcome of a [`KnowledgeGraph::vacuum_blobs`] compaction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BlobVacuumReport {
    /// Blob-store bytes before compaction (incl. dead/orphaned bytes).
    pub bytes_before: usize,
    /// Blob-store bytes after compaction (live blobs only).
    pub bytes_after: usize,
    /// Bytes reclaimed (`bytes_before - bytes_after`).
    pub bytes_reclaimed: usize,
}

impl KnowledgeGraph {
    /// Merge `absorb` into `keep`, redirecting all edges and soft-deleting `absorb`.
    ///
    /// **Edge collision rule:** When both nodes have an outgoing (or incoming) edge
    /// to/from the same third node, their strengths are combined with a max-biased
    /// weighted average: `(a² + b²) / (a + b)`.  This is always ≥ arithmetic mean
    /// and ≤ max — the weaker edge contributes proportionally less.
    ///
    /// **Property collision rule (on merged edges):** properties from the
    /// higher-strength edge win; the weaker edge's properties fill in any gaps.
    ///
    /// **Text / label:** `keep`'s text and label are never changed.
    ///
    /// **Embedding:** if `keep` has no embedding and `absorb` does, the embedding
    /// `BlobRef` is copied to `keep`.
    ///
    /// # Errors
    /// Returns `GraphError::Other` if either node is out of bounds/deleted, or if
    /// `keep == absorb`.
    pub fn merge_nodes(&mut self, keep: NodeId, absorb: NodeId) -> Result<MergeReport, GraphError> {
        // --- Validation ---
        if keep == absorb {
            return Err(GraphError::Other(
                "merge_nodes: keep and absorb must differ".into(),
            ));
        }
        let keep_idx = keep.0 as usize;
        let absorb_idx = absorb.0 as usize;
        for (id, idx, label) in [(keep, keep_idx, "keep"), (absorb, absorb_idx, "absorb")] {
            if idx >= self.nodes.len() || self.deleted.get(idx).copied().unwrap_or(true) {
                return Err(GraphError::Other(format!(
                    "merge_nodes: {} node {:?} is out of bounds or deleted",
                    label, id
                )));
            }
        }

        let mut report = MergeReport {
            absorbed: absorb,
            edges_redirected: 0,
            edges_merged: 0,
            self_loops_dropped: 0,
            embedding_updated: false,
        };

        // --- Snapshot keep's current edges (before mutation) ---
        let existing_out: HashMap<NodeId, usize> = self.nodes[keep_idx]
            .outgoing
            .iter()
            .map(|&eid| (self.edges[eid.0 as usize].target, eid.0 as usize))
            .collect();
        let existing_in: HashMap<NodeId, usize> = self.nodes[keep_idx]
            .incoming
            .iter()
            .map(|&eid| (self.edges[eid.0 as usize].source, eid.0 as usize))
            .collect();

        // --- Collect absorb edge data (clone to avoid borrow issues) ---
        #[derive(Clone)]
        struct EdgeData {
            other: NodeId,
            strength: f64,
            props: Vec<(crate::ids::PropertyTag, crate::properties::PropertyValue)>,
        }

        let absorb_out: Vec<EdgeData> = self.nodes[absorb_idx]
            .outgoing
            .iter()
            .map(|&eid| {
                let e = &self.edges[eid.0 as usize];
                EdgeData {
                    other: e.target,
                    strength: e.strength,
                    props: e.properties.clone(),
                }
            })
            .collect();

        let absorb_in: Vec<EdgeData> = self.nodes[absorb_idx]
            .incoming
            .iter()
            .map(|&eid| {
                let e = &self.edges[eid.0 as usize];
                EdgeData {
                    other: e.source,
                    strength: e.strength,
                    props: e.properties.clone(),
                }
            })
            .collect();

        // --- Helper: max-biased strength merge ---
        fn blend(a: f64, b: f64) -> f64 {
            let denom = a + b;
            if denom == 0.0 {
                0.0
            } else {
                (a * a + b * b) / denom
            }
        }

        // --- Helper: merge properties (dominant = higher-strength source) ---
        fn merge_props(
            dominant: &[(crate::ids::PropertyTag, crate::properties::PropertyValue)],
            recessive: &[(crate::ids::PropertyTag, crate::properties::PropertyValue)],
        ) -> Vec<(crate::ids::PropertyTag, crate::properties::PropertyValue)> {
            let mut result = dominant.to_vec();
            let present: HashSet<u16> = result.iter().map(|(t, _)| t.0).collect();
            for (tag, val) in recessive {
                if !present.contains(&tag.0) {
                    result.push((*tag, val.clone()));
                }
            }
            result.sort_by_key(|(t, _)| t.0);
            result
        }

        // --- Process absorb's outgoing edges ---
        for ed in &absorb_out {
            if ed.other == keep {
                report.self_loops_dropped += 1;
                continue;
            }
            if let Some(&keep_edge_idx) = existing_out.get(&ed.other) {
                // Collision: blend strengths, merge properties
                let keep_str = self.edges[keep_edge_idx].strength;
                let (dominant, recessive) = if keep_str >= ed.strength {
                    (
                        self.edges[keep_edge_idx].properties.clone(),
                        ed.props.clone(),
                    )
                } else {
                    (
                        ed.props.clone(),
                        self.edges[keep_edge_idx].properties.clone(),
                    )
                };
                self.edges[keep_edge_idx].strength = blend(keep_str, ed.strength);
                self.edges[keep_edge_idx].properties = merge_props(&dominant, &recessive);
                // Rebuild mask
                let mut mask = 0u64;
                for (tag, _) in &self.edges[keep_edge_idx].properties {
                    mask |= 1u64 << tag.0;
                }
                self.edges[keep_edge_idx].property_mask = mask;
                self.nodes[keep_idx].outgoing_property_mask |= mask;
                report.edges_merged += 1;
            } else {
                self.add_edge(keep, ed.other, ed.strength, ed.props.clone());
                report.edges_redirected += 1;
            }
        }

        // --- Process absorb's incoming edges ---
        for ed in &absorb_in {
            if ed.other == keep {
                report.self_loops_dropped += 1;
                continue;
            }
            if let Some(&keep_edge_idx) = existing_in.get(&ed.other) {
                // Collision
                let keep_str = self.edges[keep_edge_idx].strength;
                let (dominant, recessive) = if keep_str >= ed.strength {
                    (
                        self.edges[keep_edge_idx].properties.clone(),
                        ed.props.clone(),
                    )
                } else {
                    (
                        ed.props.clone(),
                        self.edges[keep_edge_idx].properties.clone(),
                    )
                };
                self.edges[keep_edge_idx].strength = blend(keep_str, ed.strength);
                self.edges[keep_edge_idx].properties = merge_props(&dominant, &recessive);
                let mut mask = 0u64;
                for (tag, _) in &self.edges[keep_edge_idx].properties {
                    mask |= 1u64 << tag.0;
                }
                self.edges[keep_edge_idx].property_mask = mask;
                report.edges_merged += 1;
            } else {
                self.add_edge(ed.other, keep, ed.strength, ed.props.clone());
                report.edges_redirected += 1;
            }
        }

        // --- Embedding transfer ---
        if self.nodes[keep_idx].embedding.is_none() {
            if let Some(blob_ref) = self.nodes[absorb_idx].embedding.clone() {
                self.nodes[keep_idx].embedding = Some(blob_ref);
                report.embedding_updated = true;
            }
        }

        // --- Soft-delete absorb (cleans up its adjacency lists) ---
        self.remove_node(absorb);

        Ok(report)
    }

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
        self.nodes[idx].content_hash = crate::lifecycle::hash::sha256_digest(new_text.as_bytes());
        if let Some(emb) = new_embedding {
            self.nodes[idx].embedding = Some(self.blobs.store_embedding(emb));
        }
        Ok(())
    }

    /// Bytes currently held by the blob store (live + dead). See [`vacuum_blobs`](Self::vacuum_blobs).
    pub fn blob_bytes(&self) -> usize {
        self.blobs.total_bytes()
    }

    /// Compact the blob store: rebuild it from only the live nodes' current
    /// blob + embedding bytes and remap each [`BlobRef`](crate::blob_store::BlobRef).
    /// Reclaims bytes orphaned by [`update_node_content`](Self::update_node_content),
    /// [`merge_nodes`](Self::merge_nodes), and tombstoned nodes. Node ids,
    /// edges, and all indexes are untouched (they key on [`NodeId`](crate::ids::NodeId)).
    ///
    /// Notes: a tombstoned node's bytes are dropped (empty blob), so its
    /// `content_hash` becomes meaningless after compaction (it is never
    /// verified — `verify_node` returns `Unknown` for deleted nodes). When the
    /// source store is memory-mapped, `bytes_before` is the mapped-file length,
    /// so `bytes_reclaimed` is a reclaim estimate rather than an exact delta.
    pub fn vacuum_blobs(&mut self) -> BlobVacuumReport {
        let bytes_before = self.blobs.total_bytes();
        let n = self.nodes.len();
        let mut new_blobs = crate::blob_store::BlobStore::new();
        let mut new_refs: Vec<(crate::blob_store::BlobRef, Option<crate::blob_store::BlobRef>)> =
            Vec::with_capacity(n);
        // Pass 1: copy live bytes into a fresh store, recording new refs.
        for i in 0..n {
            let deleted = self.deleted.get(i).copied().unwrap_or(false);
            if deleted {
                new_refs.push((new_blobs.store(""), None));
                continue;
            }
            let text = self.blobs.read(&self.nodes[i].blob).to_string();
            let emb: Option<Vec<f32>> = self.nodes[i]
                .embedding
                .as_ref()
                .and_then(|e| self.blobs.read_embedding(e));
            let bref = new_blobs.store(&text);
            let eref = emb.map(|v| new_blobs.store_embedding(&v));
            new_refs.push((bref, eref));
        }
        // Pass 2: reassign refs (old store no longer borrowed).
        for (i, (bref, eref)) in new_refs.into_iter().enumerate() {
            self.nodes[i].blob = bref;
            self.nodes[i].embedding = eref;
        }
        self.blobs = new_blobs;
        let bytes_after = self.blobs.total_bytes();
        BlobVacuumReport {
            bytes_before,
            bytes_after,
            bytes_reclaimed: bytes_before.saturating_sub(bytes_after),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::KnowledgeGraph;
    use crate::ids::NodeId;

    mod merge_tests {
        use crate::graph::KnowledgeGraph;

        #[test]
        fn merge_nodes_no_collision() {
            let mut g = KnowledgeGraph::new();
            let keep = g.add_node("keep", None, None);
            let absorb = g.add_node("absorb", None, None);
            let other1 = g.add_node("other1", None, None);
            let other2 = g.add_node("other2", None, None);
            g.add_edge(keep, other1, 0.8, vec![]);
            g.add_edge(absorb, other2, 0.7, vec![]);

            let report = g.merge_nodes(keep, absorb).unwrap();

            assert_eq!(report.absorbed, absorb);
            assert_eq!(report.edges_redirected, 1); // absorb→other2 → keep→other2
            assert_eq!(report.edges_merged, 0);
            assert_eq!(report.self_loops_dropped, 0);
            // absorb must be tombstoned
            assert!(g.is_node_deleted(absorb));
            // keep must now reach other2
            let keep_targets: Vec<_> = g.nodes[keep.0 as usize]
                .outgoing
                .iter()
                .map(|&eid| g.edges[eid.0 as usize].target)
                .collect();
            assert!(keep_targets.contains(&other2));
        }

        #[test]
        fn merge_nodes_collision_strength_formula() {
            let mut g = KnowledgeGraph::new();
            let keep = g.add_node("keep", None, None);
            let absorb = g.add_node("absorb", None, None);
            let target = g.add_node("target", None, None);
            // Both point to target with different strengths
            g.add_edge(keep, target, 0.6, vec![]); // a = 0.6
            g.add_edge(absorb, target, 0.4, vec![]); // b = 0.4

            let report = g.merge_nodes(keep, absorb).unwrap();

            assert_eq!(report.edges_merged, 1);
            // Expected: (0.6² + 0.4²) / (0.6 + 0.4) = (0.36 + 0.16) / 1.0 = 0.52
            let merged_edge = g.nodes[keep.0 as usize]
                .outgoing
                .iter()
                .map(|&eid| &g.edges[eid.0 as usize])
                .find(|e| e.target == target)
                .unwrap();
            let expected = (0.6f64 * 0.6 + 0.4 * 0.4) / (0.6 + 0.4);
            assert!((merged_edge.strength - expected).abs() < 1e-10);
        }

        #[test]
        fn merge_nodes_self_loop_dropped() {
            let mut g = KnowledgeGraph::new();
            let keep = g.add_node("keep", None, None);
            let absorb = g.add_node("absorb", None, None);
            // Edge keep→absorb: becomes a self-loop after merge
            g.add_edge(keep, absorb, 0.5, vec![]);

            let report = g.merge_nodes(keep, absorb).unwrap();

            assert_eq!(report.self_loops_dropped, 1);
            // keep must have no outgoing edges (the loop was dropped)
            let keep_out_count = g.nodes[keep.0 as usize]
                .outgoing
                .iter()
                .filter(|&&eid| !g.is_node_deleted(g.edges[eid.0 as usize].target))
                .count();
            assert_eq!(keep_out_count, 0);
        }

        #[test]
        fn merge_nodes_embedding_propagation() {
            let mut g = KnowledgeGraph::new();
            let keep = g.add_node("keep", None, None); // no embedding
            let absorb = g.add_node("absorb", None, Some(&[1.0f32, 0.0])); // has embedding

            let report = g.merge_nodes(keep, absorb).unwrap();

            assert!(report.embedding_updated);
            assert!(g.nodes[keep.0 as usize].embedding.is_some());
        }

        #[test]
        fn merge_nodes_embedding_not_overwritten() {
            let mut g = KnowledgeGraph::new();
            let keep = g.add_node("keep", None, Some(&[1.0f32, 0.0]));
            let absorb = g.add_node("absorb", None, Some(&[0.0f32, 1.0]));

            let report = g.merge_nodes(keep, absorb).unwrap();

            assert!(!report.embedding_updated);
            // keep's embedding should be unchanged: first element still ≈ 1.0
            let emb = g.read_embedding(keep).unwrap();
            assert!((emb[0] - 1.0f32).abs() < 1e-6);
        }

        #[test]
        fn merge_nodes_validation_errors() {
            let mut g = KnowledgeGraph::new();
            let a = g.add_node("a", None, None);
            let b = g.add_node("b", None, None);

            // Same node
            assert!(g.merge_nodes(a, a).is_err());
            // Out-of-bounds absorb
            assert!(g.merge_nodes(a, crate::NodeId(999)).is_err());
            // Deleted absorb
            g.remove_node(b);
            assert!(g.merge_nodes(a, b).is_err());
        }
    }

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

        g.update_node_content(id, "node", Some(&[0.0f32, 1.0]))
            .unwrap();

        let emb = g.read_embedding(id).unwrap();
        assert!((emb[1] - 1.0f32).abs() < 1e-6);
    }

    #[test]
    fn update_node_content_refreshes_hash() {
        let mut g = KnowledgeGraph::new();
        let n = g.add_node("old text", None, None);
        let before = g.node_content_hash(n).unwrap();
        g.update_node_content(n, "new text", None).unwrap();
        let after = g.node_content_hash(n).unwrap();
        assert_ne!(before, after);
        assert_eq!(after, crate::lifecycle::hash::sha256_digest(b"new text"));
    }

    #[test]
    fn vacuum_blobs_reclaims_orphaned_and_tombstoned_bytes() {
        let mut g = KnowledgeGraph::new();
        let a = g.add_node("a short node", Some("A"), None);
        let b = g.add_node("b node to be deleted with a fairly long body", Some("B"), None);
        g.update_node_content(a, &"X".repeat(5000), None).unwrap();
        g.update_node_content(a, "tiny", None).unwrap();
        assert!(g.remove_node(b));

        let before = g.blob_bytes();
        let report = g.vacuum_blobs();
        let after = g.blob_bytes();

        assert_eq!(report.bytes_before, before);
        assert_eq!(report.bytes_after, after);
        assert!(report.bytes_reclaimed > 5000);
        assert!(after < before);
        assert_eq!(g.read_blob(a), "tiny");
        assert_eq!(g.verify_node(a), crate::graph::NodeIntegrity::Match);
        assert!(g.is_node_deleted(b));
    }

    #[test]
    fn vacuum_blobs_preserves_edges_and_embeddings() {
        let mut g = KnowledgeGraph::new();
        let a = g.add_node("alpha", Some("A"), Some(&[1.0_f32, 2.0, 3.0]));
        let b = g.add_node("beta", Some("B"), None);
        g.add_edge(a, b, 0.9, vec![]);
        g.update_node_content(a, "alpha-v2", Some(&[4.0_f32, 5.0, 6.0])).unwrap();
        g.vacuum_blobs();
        assert_eq!(g.read_blob(a), "alpha-v2");
        assert_eq!(g.read_embedding(a), Some(vec![4.0_f32, 5.0, 6.0]));
        assert_eq!(g.edge_count(), 1);
        assert_eq!(g.node_label(a), Some("A"));
    }
}
