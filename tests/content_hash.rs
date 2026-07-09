//! Thorough tests for node content hashing + verification.
//!
//! The design decision (Task 4) is that `content_hash` is NOT persisted as a
//! separate field in `SerializableNode`.  Instead, `load_from_disk` recomputes
//! it from the raw blob bytes that are already stored.  This test locks that
//! behaviour so that any accidental removal of the recompute step is caught
//! immediately.

use grap::{KnowledgeGraph, NodeIntegrity};
use tempfile::tempdir;

#[test]
fn content_hash_survives_classic_save_load_and_verify() {
    let dir = tempdir().unwrap();
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("durable knowledge about Rust ownership", Some("L"), None);
    let h = g.node_content_hash(n).unwrap();
    assert_eq!(g.verify_node(n), NodeIntegrity::Match);

    g.save_to_disk(dir.path()).unwrap();

    let g2 = KnowledgeGraph::load_from_disk(dir.path()).unwrap();
    assert_eq!(g2.node_content_hash(n), Some(h));
    assert_eq!(g2.verify_node(n), NodeIntegrity::Match);
}

#[test]
fn content_hash_survives_modern_dir_persistence() {
    use grap::IndexedKnowledgeGraph;
    let dir = tempdir().unwrap();
    let mut g = IndexedKnowledgeGraph::new_on_disk();
    let n = g.add_node("modern on-disk content hash test", Some("modern"), None);
    let h = g.node_content_hash(n).unwrap();

    g.save_to_dir(dir.path()).unwrap();

    let g2 = IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap();
    assert_eq!(g2.node_content_hash(n), Some(h));
    assert_eq!(g2.verify_node(n), NodeIntegrity::Match);
}

#[test]
fn verify_reports_unknown_for_deleted_or_bad_id() {
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("will be removed", None, None);
    g.remove_node(n);
    assert_eq!(g.verify_node(n), NodeIntegrity::Unknown);

    let bad = grap::ids::NodeId(9999);
    assert_eq!(g.verify_node(bad), NodeIntegrity::Unknown);
}

#[test]
fn indexed_knowledge_graph_exposes_hash_and_verify() {
    use grap::IndexedKnowledgeGraph;

    let dir = tempdir().unwrap();
    let mut g = IndexedKnowledgeGraph::new_on_disk();
    let n = g.add_node("indexed hash test", Some("idx"), None);

    let _hex: Option<String> = g.node_content_hash(n).map(|bytes| {
        bytes.iter().map(|b| format!("{:02x}", b)).collect()
    });
    assert_eq!(g.verify_node(n), grap::NodeIntegrity::Match);

    g.save_to_dir(dir.path()).unwrap();
    let g2 = IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap();
    assert_eq!(g2.verify_node(n), grap::NodeIntegrity::Match);
}
