//! Regression test: `content_hash` must survive a save/load round-trip.
//!
//! The design decision (Task 4) is that `content_hash` is NOT persisted as a
//! separate field in `SerializableNode`.  Instead, `load_from_disk` recomputes
//! it from the raw blob bytes that are already stored.  This test locks that
//! behaviour so that any accidental removal of the recompute step is caught
//! immediately.

use grap::KnowledgeGraph;
use tempfile::tempdir;

#[test]
fn content_hash_survives_save_load() {
    let dir = tempdir().unwrap();
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("durable knowledge", Some("L"), None);
    let h = g.node_content_hash(n).unwrap();
    g.save_to_disk(dir.path()).unwrap();

    let g2 = KnowledgeGraph::load_from_disk(dir.path()).unwrap();
    assert_eq!(g2.node_content_hash(n), Some(h));
}
