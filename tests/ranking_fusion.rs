use grap::FusionMode;
use grap::{HybridWeights, IndexedKnowledgeGraph};

#[test]
fn exact_vector_ties_use_node_id_before_top_k_selection_and_after_reload() {
    use grap::{BruteForceVectorIndex, NodeId, VectorIndex};
    for shift in [0, 3, 19] {
        let mut index = BruteForceVectorIndex::new();
        for i in 0..32 {
            index.add_vector(NodeId((i + shift) % 32), &[1.0, 0.0]);
        }
        for limit in [1, 4, 32, 100] {
            let ids: Vec<_> = index
                .search(&[1.0, 0.0], limit)
                .into_iter()
                .map(|x| x.0)
                .collect();
            assert_eq!(
                ids,
                (0..limit.min(32) as u32).map(NodeId).collect::<Vec<_>>()
            );
        }
        let dir = tempfile::tempdir().unwrap();
        VectorIndex::save_to_dir(&index, dir.path()).unwrap();
        let mut restored = BruteForceVectorIndex::new();
        assert!(restored.try_load_from_dir(dir.path()).unwrap());
        assert_eq!(
            restored.search(&[1.0, 0.0], 4),
            index.search(&[1.0, 0.0], 4)
        );
    }
}

#[test]
fn repeated_channel_hits_do_not_boost_a_document_or_penalize_following_ranks() {
    use grap::ranking::{rrf_fuse, RankedChannel};
    use grap::NodeId;
    let channel = |ranked| RankedChannel {
        name: "text",
        weight: 1.0,
        ranked,
    };
    let clean = rrf_fuse(&[channel(vec![NodeId(2), NodeId(1), NodeId(3)])], 60.0);
    let repeated = rrf_fuse(
        &[channel(vec![
            NodeId(2),
            NodeId(2),
            NodeId(1),
            NodeId(3),
            NodeId(1),
        ])],
        60.0,
    );
    let signature = |rows: Vec<grap::ranking::FusedNode>| {
        rows.into_iter()
            .map(|r| (r.node, r.score, r.ranks))
            .collect::<Vec<_>>()
    };
    assert_eq!(signature(clean), signature(repeated));
}

#[test]
fn equal_fused_scores_have_stable_node_order() {
    use grap::ranking::{rrf_fuse, RankedChannel};
    use grap::NodeId;
    let channels = [
        RankedChannel {
            name: "text",
            weight: 1.0,
            ranked: vec![NodeId(8), NodeId(2)],
        },
        RankedChannel {
            name: "vector",
            weight: 1.0,
            ranked: vec![NodeId(2), NodeId(8)],
        },
    ];
    for _ in 0..32 {
        let result = rrf_fuse(&channels, 60.0);
        assert_eq!(result[0].score, result[1].score);
        assert_eq!(
            result.iter().map(|r| r.node).collect::<Vec<_>>(),
            vec![NodeId(2), NodeId(8)]
        );
    }
}

#[test]
#[cfg(not(feature = "ann-hnsw"))] // Exact top-k contract; ANN may choose different candidates.
fn hybrid_top_k_keeps_the_same_tied_seeds_after_reload() {
    use grap::NodeId;
    let mut graph = IndexedKnowledgeGraph::new();
    for i in 0..16 {
        graph.add_node(&format!("legal concept {i}"), None, Some(&[1.0, 0.0]));
    }
    let ids = |graph: &IndexedKnowledgeGraph| {
        graph
            .search_and_traverse_hybrid(
                "",
                Some(&[1.0, 0.0]),
                4,
                &[],
                0.0,
                0,
                4,
                HybridWeights {
                    mode: FusionMode::Rrf { k: 60.0 },
                    ..HybridWeights::default()
                },
            )
            .into_iter()
            .map(|r| r.graph_result.node_id)
            .collect::<Vec<_>>()
    };
    assert_eq!(
        ids(&graph),
        vec![NodeId(0), NodeId(1), NodeId(2), NodeId(3)]
    );
    let dir = tempfile::tempdir().unwrap();
    graph.save_to_dir(dir.path()).unwrap();
    let restored = IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap();
    assert_eq!(ids(&restored), ids(&graph));
}

#[test]
fn rrf_and_linear_both_return_results() {
    let mut g = IndexedKnowledgeGraph::new();
    let a = g.add_node("rust memory safety ownership", Some("A"), None);
    let b = g.add_node("garbage collection tradeoffs", Some("B"), None);
    let c = g.add_node("memory safety without gc", Some("C"), None);
    g.add_edge(a, c, 0.9, vec![]);
    g.add_edge(b, c, 0.2, vec![]);

    let mut w = HybridWeights::default();
    w.mode = FusionMode::Rrf { k: 60.0 };
    let rrf = g.search_and_traverse_hybrid("memory safety", None, 5, &[], 0.0, 2, 10, w);
    assert!(!rrf.is_empty());

    let mut wl = HybridWeights::default();
    wl.mode = FusionMode::Linear;
    let lin = g.search_and_traverse_hybrid("memory safety", None, 5, &[], 0.0, 2, 10, wl);
    assert!(!lin.is_empty());
}
