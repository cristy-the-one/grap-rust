#[test]
fn cli_delete_removes_text_and_vectors_from_saved_indexes() {
    let dir = tempfile::tempdir().unwrap();
    let mut graph = grap::IndexedKnowledgeGraph::new_on_disk();
    let node = graph.add_node("obsolete legal evidence", None, Some(&[1.0, 0.0]));
    graph.save_to_dir(dir.path()).unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_grap-cli"))
        .arg("--graph-dir")
        .arg(dir.path())
        .args(["delete-node", &node.0.to_string()])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let loaded = grap::IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap();
    assert!(loaded.search("obsolete", 10).is_empty());
    assert!(loaded.vector_search(&[1.0, 0.0], 10).is_empty());
}
