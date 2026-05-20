//! Regression test for correct per-node blob offset calculation during
//! `save_to_dir` / `load_from_dir`.
//!
//! Historical bug: the serialization code used `if i < chunk_offsets.len()`
//! (where `i` was the node index) when computing `blob_global_offset`.
//! This caused every node whose index was >= number of blob chunks to be
//! written with offset 0. After loading, all such nodes would return the
//! text of the very first document instead of their own content.
//!
//! This test adds enough distinct nodes that the bug would have been
//! immediately visible on round-trip.

#![cfg(feature = "fulltext")]

use grap::{ids::NodeId, IndexedKnowledgeGraph};
use tempfile::tempdir;

#[test]
fn node_blobs_and_labels_roundtrip_after_persistence() {
    let mut ig = IndexedKnowledgeGraph::new_on_disk();

    let mut expected: Vec<(Option<String>, String)> = Vec::new();

    // 25 nodes is plenty to trigger the old bug even with a single blob chunk.
    for i in 0..25 {
        let label = Some(format!("document-{}", i));
        let text = format!(
            "Unique content for node {}.\n\
             This text is deliberately long and distinct so that it is easy \
             to detect when the wrong blob is returned after a persistence \
             round-trip. Additional unique marker: VALUE={}\n\
             Line three with even more entropy: {}",
            i, i * 12345, i
        );

        let _ = ig.add_node(&text, label.as_deref(), None);
        expected.push((label, text));
    }

    // Persist
    let tmp = tempdir().expect("failed to create temp dir");
    let dir = tmp.path().join("blob-roundtrip");
    ig.save_to_dir(&dir).expect("save_to_dir failed");

    // Load fresh
    let loaded = IndexedKnowledgeGraph::load_from_dir(&dir)
        .expect("load_from_dir failed after save");

    // Verify every node comes back with the exact original text and label
    assert_eq!(
        loaded.graph.node_count(),
        expected.len(),
        "node count should be preserved"
    );

    for (i, (expected_label, expected_text)) in expected.into_iter().enumerate() {
        let id = NodeId(i as u32);

        let got_label = loaded.graph.node_label(id).map(|s| s.to_string());
        assert_eq!(
            got_label, expected_label,
            "label mismatch for node {}",
            i
        );

        let got_text = loaded.graph.read_blob(id).to_string();
        assert_eq!(
            got_text, expected_text,
            "blob/text mismatch for node {}",
            i
        );
    }
}
