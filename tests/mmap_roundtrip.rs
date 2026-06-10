#![cfg(feature = "mmap")]
use grap::KnowledgeGraph;
use tempfile::tempdir;

#[test]
fn mmap_open_reads_blobs_from_disk_without_copying() {
    let dir = tempdir().unwrap();
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("brake-by-wire redundancy requirement", Some("REQ-1"), None);
    let m = g.add_node("second node", None, None);
    g.add_edge(n, m, 0.9, vec![]);
    let h = g.node_content_hash(n).unwrap();
    g.save_to_disk(dir.path()).unwrap();

    let g2 = KnowledgeGraph::load_mmap(dir.path()).unwrap();
    assert!(g2.blobs_are_mapped()); // backed by the map
    assert_eq!(g2.read_blob(n), "brake-by-wire redundancy requirement"); // zero-copy from the map
    assert_eq!(g2.read_blob(m), "second node");
    assert_eq!(g2.node_content_hash(n), Some(h)); // recomputed via mmap read
    assert_eq!(g2.edge_count(), 1);
}

#[test]
fn indexed_mmap_open_supports_search_and_read() {
    let dir = tempdir().unwrap();
    // Use the on-disk text index so the index persists across save/load; the
    // default in-memory `InvertedIndex` has a no-op `save_to_dir`, so a reloaded
    // graph (mmap or portable) would have an empty index and search nothing.
    let mut g = grap::IndexedKnowledgeGraph::new_on_disk();
    let a = g.add_node("brake redundancy requirement", Some("REQ-1"), None);
    let b = g.add_node("brake pedal position signal", Some("SIG-1"), None);
    g.add_edge(a, b, 0.9, vec![]);
    g.save_to_dir(dir.path()).unwrap();

    let g2 = grap::IndexedKnowledgeGraph::load_mmap(dir.path()).unwrap();
    assert_eq!(g2.read_blob(a), "brake redundancy requirement");
    let hits = g2.search_and_traverse_default("brake", 5, &[], 0.0, 2, 10);
    assert!(!hits.is_empty());
}
