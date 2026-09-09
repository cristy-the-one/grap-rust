use grap::{IndexedKnowledgeGraph, NodeId};

#[test]
fn batch_matches_scalar_order_search_and_embeddings() {
    let mut batch = IndexedKnowledgeGraph::new();
    let mut scalar = IndexedKnowledgeGraph::new();
    let a = [1.0, 0.0];
    let b = [0.0, 1.0];
    let rows = [
        ("lege română", Some("law"), Some(a.as_slice())),
        ("termen procedural", None, None),
        ("lege nouă", Some("new"), Some(b.as_slice())),
    ];
    let ids = batch.add_nodes(&rows).unwrap();
    let expected: Vec<_> = rows
        .iter()
        .map(|&(t, l, e)| scalar.add_node(t, l, e))
        .collect();
    assert_eq!(ids, expected);
    for id in ids {
        assert_eq!(batch.read_blob(id), scalar.read_blob(id));
        assert_eq!(batch.read_embedding(id), scalar.read_embedding(id));
        assert_eq!(batch.graph.node_label(id), scalar.graph.node_label(id));
    }
    assert_eq!(batch.vector_search(&a, 100), scalar.vector_search(&a, 100));
    assert_eq!(batch.search("lege", 10).len(), scalar.search("lege", 10).len());
}

#[test]
fn invalid_batches_do_not_partially_insert_or_consume_ids() {
    let mut graph = IndexedKnowledgeGraph::new();
    graph.add_node("original", None, Some(&[1.0, 0.0]));
    for bad in [vec![], vec![1.0], vec![f32::NAN, 1.0]] {
        assert!(graph
            .add_nodes(&[
                ("would be first", None, None),
                ("bad", None, Some(bad.as_slice()))
            ])
            .is_err());
        assert_eq!(graph.live_node_count(), 1);
    }
    assert!(graph.add_nodes(&[]).unwrap().is_empty());
    assert_eq!(
        graph.add_nodes(&[("next", None, None)]).unwrap(),
        vec![NodeId(1)]
    );
}
