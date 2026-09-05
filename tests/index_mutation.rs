use grap::{IndexedKnowledgeGraph, InvertedIndex, NodeId, TextIndex};

#[test]
fn replacing_topology_preserves_retrieval_and_survives_reload() {
    let mut graph = IndexedKnowledgeGraph::new_on_disk();
    let a = graph.add_node("alpha", None, Some(&[1.0, 0.0]));
    let b = graph.add_node("beta", None, Some(&[0.0, 1.0]));
    graph.add_edge(a, b, 1.0, vec![]);
    assert_eq!(graph.graph.edge_count(), 1);
    graph.clear_edges();
    assert_eq!(graph.graph.edge_count(), 0);
    assert_eq!(graph.search("alpha", 1)[0].node_id, a);
    assert_eq!(graph.vector_search(&[0.0, 1.0], 1)[0].0, b);
    let dir = tempfile::tempdir().unwrap();
    graph.save_to_dir(dir.path()).unwrap();
    let mut restored = IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap();
    assert_eq!(restored.graph.edge_count(), 0);
    restored.add_edge(b, a, 1.0, vec![]);
    assert_eq!(restored.graph.edge_count(), 1);
    assert_eq!(restored.graph.all_edges()[0].source, b);
    assert_eq!(restored.graph.all_edges()[0].target, a);
}

#[test]
fn disk_replacement_mapping_survives_save_load_and_vacuum() {
    let dir = tempfile::tempdir().unwrap();
    let mut index = grap::OnDiskTextIndex::new();
    index.index_document(NodeId(17), "obsolete amber violet");
    index.index_document(NodeId(3), "stable amber violet");
    index.save_to_dir(dir.path()).unwrap();
    // Replace through TextIndex itself, without a separate delete call.
    index.index_document(NodeId(17), "fresh indigo");
    assert!(index.search("obsolete", 1).is_empty());
    assert_eq!(index.phrase_search("amber violet", 10, 1)[0].0, NodeId(3));
    index.save_to_dir(dir.path()).unwrap();
    let mut loaded = <grap::OnDiskTextIndex as TextIndex>::load_from_dir(dir.path()).unwrap();
    assert_eq!(loaded.search("fresh", 1)[0].0, NodeId(17));
    loaded.delete_document(NodeId(17));
    loaded.vacuum();
    assert!(loaded.search("fresh", 10).is_empty());
    assert_eq!(loaded.search("stable", 1)[0].0, NodeId(3));
    loaded.index_document(NodeId(17), "reborn amber violet");
    loaded.save_to_dir(dir.path()).unwrap();
    let reloaded = <grap::OnDiskTextIndex as TextIndex>::load_from_dir(dir.path()).unwrap();
    assert_eq!(reloaded.search("reborn", 1)[0].0, NodeId(17));
    assert_eq!(reloaded.phrase_search("amber violet", 10, 10).len(), 2);
}

#[test]
fn replacement_matches_a_fresh_index_including_phrase_positions_and_scores() {
    let mut edited = InvertedIndex::new();
    edited.index_document(NodeId(8), "obsolete amber violet amber");
    edited.index_document(NodeId(2), "amber violet");
    for _ in 0..3 {
        edited.index_document(NodeId(8), "violet amber");
    }
    let mut fresh = InvertedIndex::new();
    fresh.index_document(NodeId(2), "amber violet");
    fresh.index_document(NodeId(8), "violet amber");
    assert!(edited.search("obsolete", 10).is_empty());
    for query in ["amber", "violet amber", "amber violet"] {
        let scores = |index: &InvertedIndex| {
            index
                .search(query, 10)
                .into_iter()
                .map(|r| (r.node_id, r.score, r.matched_terms))
                .collect::<Vec<_>>()
        };
        assert_eq!(scores(&edited), scores(&fresh));
        let phrase = |index: &InvertedIndex| {
            index
                .phrase_search(query, 0, 10)
                .into_iter()
                .map(|r| (r.node_id, r.score))
                .collect::<Vec<_>>()
        };
        assert_eq!(phrase(&edited), phrase(&fresh));
    }
    assert_eq!(edited.stats().document_count, 2);
    assert_eq!(edited.stats().avg_doc_length, fresh.stats().avg_doc_length);
    assert_eq!(edited.stats().total_postings, fresh.stats().total_postings);
}

#[test]
fn deletion_is_idempotent_and_empty_documents_are_live() {
    let mut index = InvertedIndex::new();
    index.index_document(NodeId(5), "amber violet");
    index.index_document(NodeId(1), "");
    assert_eq!(index.stats().document_count, 2);
    assert_eq!(index.stats().avg_doc_length, 1.0);
    TextIndex::delete_document(&mut index, NodeId(5));
    TextIndex::delete_document(&mut index, NodeId(5));
    index.delete_document(NodeId(u32::MAX));
    assert!(index.search("amber", 1).is_empty());
    assert!(TextIndex::is_document_deleted(&index, NodeId(5)));
    assert!(!TextIndex::is_document_deleted(&index, NodeId(1)));
    assert_eq!(index.stats().document_count, 1);
    assert_eq!(index.stats().total_postings, 0);
    assert_eq!(index.stats().unique_terms, 0);
    assert_eq!(index.stats().avg_doc_length, 0.0);
    index.index_document(NodeId(5), "reborn");
    assert!(!TextIndex::is_document_deleted(&index, NodeId(5)));
    assert_eq!(index.search("reborn", 1)[0].node_id, NodeId(5));
    index.delete_document(NodeId(1));
    assert_eq!(index.stats().document_count, 1);
    assert_eq!(index.stats().avg_doc_length, 1.0);
}

#[test]
fn graph_mutations_keep_text_and_vector_membership_consistent() {
    for mut graph in [
        IndexedKnowledgeGraph::new(),
        IndexedKnowledgeGraph::new_on_disk(),
    ] {
        let node = graph.add_node("obsolete amber violet", None, None);
        graph
            .update_node(node, "fresh indigo", Some(&[1.0, 0.0]))
            .unwrap();
        assert!(graph.search("obsolete", 10).is_empty());
        assert!(graph.phrase_search("amber violet", 0, 10).is_empty());
        assert_eq!(graph.search("fresh", 1)[0].node_id, node);
        assert_eq!(graph.vector_search(&[1.0, 0.0], 1)[0].0, node);
        assert!(graph.remove_node(node));
        assert!(graph.search("fresh", 10).is_empty());
        assert!(graph.vector_search(&[1.0, 0.0], 10).is_empty());
    }
}

#[test]
fn tied_text_results_have_stable_node_order_and_zero_limit_is_empty() {
    let mut index = InvertedIndex::new();
    for id in (0..32).rev() {
        index.index_document(NodeId(id), "amber violet");
    }
    for _ in 0..10 {
        assert_eq!(
            index
                .search("violet amber", 3)
                .iter()
                .map(|r| r.node_id)
                .collect::<Vec<_>>(),
            vec![NodeId(0), NodeId(1), NodeId(2)]
        );
    }
    assert!(index.search("amber", 0).is_empty());
    assert!(index.phrase_search("amber violet", 0, 0).is_empty());
}
