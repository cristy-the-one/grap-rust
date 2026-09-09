use grap::IndexedKnowledgeGraph;

#[test]
fn stale_vector_cache_cannot_override_current_graph_embeddings_or_deletions() {
    for update in [false, true] {
        let dir = tempfile::tempdir().unwrap();
        let mut graph = IndexedKnowledgeGraph::new();
        let a = graph.add_node("first", None, Some(&[1.0, 0.0]));
        let b = graph.add_node("second", None, Some(&[0.8, 0.6]));
        graph.save_to_dir(dir.path()).unwrap();
        // Simulate independently restored cache files or an interrupted index save.
        let old = std::fs::read(dir.path().join("vector_index/vectors.bin")).unwrap();
        let manifest = std::fs::read(dir.path().join("vector_index/manifest.json")).unwrap();
        if update {
            graph.update_node(a, "changed", Some(&[-1.0, 0.0])).unwrap();
        }
        graph.remove_node(b);
        graph.save_to_dir(dir.path()).unwrap();
        std::fs::write(dir.path().join("vector_index/vectors.bin"), old).unwrap();
        std::fs::write(dir.path().join("vector_index/manifest.json"), manifest).unwrap();
        let loaded = IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap();
        assert_eq!(
            loaded.vector_search(&[1.0, 0.0], 10),
            vec![(a, if update { -1.0 } else { 1.0 })]
        );
    }
}

#[test]
fn a_matching_older_cache_can_still_pick_up_new_graph_embeddings() {
    let dir = tempfile::tempdir().unwrap();
    let mut graph = IndexedKnowledgeGraph::new();
    graph.add_node("first", None, Some(&[0.0, 1.0]));
    graph.save_to_dir(dir.path()).unwrap();
    let old = std::fs::read(dir.path().join("vector_index/vectors.bin")).unwrap();
    let manifest = std::fs::read(dir.path().join("vector_index/manifest.json")).unwrap();
    let added = graph.add_node("new", None, Some(&[1.0, 0.0]));
    graph.save_to_dir(dir.path()).unwrap();
    std::fs::write(dir.path().join("vector_index/vectors.bin"), old).unwrap();
    std::fs::write(dir.path().join("vector_index/manifest.json"), manifest).unwrap();
    let loaded = IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap();
    assert_eq!(loaded.vector_search(&[1.0, 0.0], 1), vec![(added, 1.0)]);
    assert_eq!(loaded.vector_index_stats().unwrap().vector_count, 2);
}
