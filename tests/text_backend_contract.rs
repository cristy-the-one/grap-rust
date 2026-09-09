use grap::{InvertedIndex, NodeId, OnDiskTextIndex, TextIndex};

#[test]
fn disk_scores_are_invariant_under_flush_merge_delete_and_reload() {
    let dir = tempfile::tempdir().unwrap();
    let mut disk = OnDiskTextIndex::new();
    disk.index_document(NodeId(0), "credit credit bancar");
    disk.index_document(NodeId(1), "credit bancar contract termen");
    let before = disk.search("credit bancar", 20);
    disk.save_to_dir(dir.path()).unwrap();
    assert_eq!(
        disk.search("credit bancar", 20),
        before,
        "flush changed scores"
    );
    disk.index_document(NodeId(2), "contract credit");
    let mixed = disk.search("credit bancar", 20);
    disk.save_to_dir(dir.path()).unwrap();
    disk.compact();
    assert_eq!(
        disk.search("credit bancar", 20),
        mixed,
        "merge changed scores"
    );
    disk.delete_document(NodeId(1));
    let deleted = disk.search("credit bancar", 20);
    let mut fresh = OnDiskTextIndex::new();
    fresh.index_document(NodeId(0), "credit credit bancar");
    fresh.index_document(NodeId(2), "contract credit");
    assert_eq!(
        deleted,
        fresh.search("credit bancar", 20),
        "tombstones skew statistics"
    );
    disk.vacuum();
    disk.save_to_dir(dir.path()).unwrap();
    let loaded = <OnDiskTextIndex as TextIndex>::load_from_dir(dir.path()).unwrap();
    assert_eq!(loaded.search("credit bancar", 20), deleted);
}

#[test]
fn phrase_requires_every_term_in_order_at_distinct_positions() {
    for mut index in [
        Box::new(InvertedIndex::new()) as Box<dyn TextIndex>,
        Box::new(OnDiskTextIndex::new()),
    ] {
        for (i, text) in [
            "credit bancar legal",
            "legal bancar credit",
            "credit bancar distant distant legal",
            "credit legal bancar",
            "credit credit bancar",
        ]
        .iter()
        .enumerate()
        {
            index.index_document(NodeId(i as u32), text);
        }
        let ids = |query, gap| {
            index
                .phrase_search(query, gap, 20)
                .into_iter()
                .map(|x| x.0)
                .collect::<Vec<_>>()
        };
        assert_eq!(ids("credit bancar legal", 10), vec![NodeId(0)]);
        assert_eq!(ids("credit credit", 10), vec![NodeId(4)]);
        assert!(ids("credit bancar", 0).is_empty());
    }
}

#[test]
fn disk_ties_use_node_ids_even_after_replacement() {
    let mut index = OnDiskTextIndex::new();
    index.index_document(NodeId(8), "credit bancar");
    index.index_document(NodeId(2), "credit bancar");
    index.index_document(NodeId(2), "credit bancar");
    assert_eq!(index.search("credit", 1)[0].0, NodeId(2));
    assert_eq!(index.phrase_search("credit bancar", 10, 1)[0].0, NodeId(2));
}

#[test]
fn ordered_proximity_can_use_a_later_intermediate_occurrence() {
    use grap::on_disk_index::OnDiskIndex;
    let mut index = OnDiskIndex::new();
    index.add_document(
        &["a", "b", "b", "c"],
        &[vec![0], vec![1], vec![8], vec![15]],
    );
    assert_eq!(index.phrase_search(&["a", "b", "c"], 8).len(), 1);
    assert!(index.phrase_search(&["a", "b", "c"], 7).is_empty());
    index.flush();
    assert_eq!(index.phrase_search(&["a", "b", "c"], 8).len(), 1);
    assert!(index.phrase_search(&["c", "b", "a"], u32::MAX).is_empty());
}

#[test]
fn phrase_after_many_repeated_terms_is_not_lost() {
    for mut index in [
        Box::new(InvertedIndex::new()) as Box<dyn TextIndex>,
        Box::new(OnDiskTextIndex::new()),
    ] {
        index.index_document(NodeId(0), &("credit ".repeat(300) + "bancar"));
        assert_eq!(index.phrase_search("credit bancar", 10, 5).len(), 1);
    }
}

#[test]
fn memory_backed_graph_is_searchable_after_portable_and_mmap_reload() {
    use grap::IndexedKnowledgeGraph;
    let mut graph = IndexedKnowledgeGraph::new();
    let live = graph.add_node("credit bancar", None, None);
    let dead = graph.add_node("obsolete", None, None);
    graph.remove_node(dead);
    let dir = tempfile::tempdir().unwrap();
    graph.save_to_dir(dir.path()).unwrap();
    for loaded in [
        IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap(),
        IndexedKnowledgeGraph::load_mmap(dir.path()).unwrap(),
    ] {
        assert_eq!(
            loaded
                .search("credit", 5)
                .iter()
                .map(|r| r.node_id)
                .collect::<Vec<_>>(),
            vec![live]
        );
        assert!(loaded.search("obsolete", 5).is_empty());
        assert_eq!(loaded.phrase_search("credit bancar", 10, 5).len(), 1);
    }
}
