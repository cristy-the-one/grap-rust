use grap::KnowledgeGraph;

#[test]
fn streaming_snapshots_preserve_empty_oversized_and_boundary_payloads() {
    let mut graph = KnowledgeGraph::new();
    let texts = [
        String::new(),
        "a".repeat(4 * 1024 * 1024),
        String::new(),
        "b".repeat(4 * 1024 * 1024 + 1),
        "final Romanian rule șț".into(),
        String::new(),
    ];
    let ids: Vec<_> = texts
        .iter()
        .map(|s| graph.add_node(s, Some("label"), Some(&[])))
        .collect();
    let dir = tempfile::tempdir().unwrap();
    graph.save_to_disk(dir.path()).unwrap();
    let loaded = KnowledgeGraph::load_from_disk(dir.path()).unwrap();
    for (&id, text) in ids.iter().zip(&texts) {
        assert_eq!(loaded.read_blob(id), text);
        assert_eq!(loaded.read_embedding(id), Some(vec![]));
    }
    #[cfg(feature = "mmap")]
    {
        let mapped = KnowledgeGraph::load_mmap(dir.path()).unwrap();
        for (&id, text) in ids.iter().zip(&texts) {
            assert_eq!(mapped.read_blob(id), text);
        }
    }
}
