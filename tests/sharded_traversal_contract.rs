use grap::ShardedGraph;

#[test]
fn ordinary_documents_are_distributed_across_shards() {
    let mut graph = ShardedGraph::new(3);
    let mut counts = [0; 3];
    for i in 0..3000 {
        let node = graph
            .add_node(&format!("Romanian legal provision number {i}"), None)
            .unwrap();
        counts[node.shard.0 as usize] += 1;
    }
    assert!(
        counts.iter().all(|&n| n > 300 && n < 1800),
        "unbalanced routing: {counts:?}"
    );
}

#[test]
fn sharded_search_follows_local_edges_and_best_bounded_paths() {
    for shards in [1, 3] {
        let mut graph = ShardedGraph::new(shards);
        let nodes: Vec<_> = ["uniqueseed", "bridgeone", "bridgetwo", "target", "tail"]
            .iter()
            .map(|text| graph.add_node(text, None).unwrap())
            .collect();
        for (s, t, w) in [
            (0, 3, 0.2),
            (0, 1, 0.9),
            (1, 2, 0.9),
            (2, 3, 0.9),
            (3, 4, 0.8),
        ] {
            graph.add_edge(nodes[s], nodes[t], w, vec![]).unwrap();
        }
        let hits = graph.search_and_traverse("uniqueseed", 1, &[], 0.0, 3, 20);
        assert_eq!(hits.len(), 5, "missing local or cross-shard reachability");
        let target = hits.iter().find(|h| h.node == nodes[3]).unwrap();
        assert!((target.graph_strength - 0.729).abs() < 1e-12);
        assert_eq!(target.depth, 3);
        assert_eq!(target.path.len(), 3);
        let tail = hits.iter().find(|h| h.node == nodes[4]).unwrap();
        assert!((tail.graph_strength - 0.16).abs() < 1e-12);
        assert_eq!(tail.depth, 2);
    }
}

#[test]
fn sharded_filters_and_zero_limit_are_not_ignored() {
    let mut graph = ShardedGraph::new(2);
    graph.add_node("uniqueseed", None).unwrap();
    assert!(graph
        .search_and_traverse("uniqueseed", 1, &[], 0.0, 3, 0)
        .is_empty());
    assert!(graph
        .search_and_traverse("uniqueseed", 1, &["missing"], 0.0, 3, 20)
        .is_empty());
}

#[test]
fn cross_shard_properties_and_thresholds_survive_reload() {
    use grap::PropertyValue;
    let mut graph = ShardedGraph::new(3);
    let seed = graph.add_node("uniqueseed", None).unwrap();
    let remote = (0..100)
        .map(|i| graph.add_node(&format!("remote{i}"), None).unwrap())
        .find(|id| id.shard != seed.shard)
        .unwrap();
    let tail = graph.add_node("tail", None).unwrap();
    graph
        .router_mut()
        .add_edge_with_properties(
            seed,
            remote,
            0.6,
            vec![("cites".into(), PropertyValue::Bool(true))],
        )
        .unwrap();
    graph
        .router_mut()
        .add_edge_with_properties(
            remote,
            tail,
            0.6,
            vec![("cites".into(), PropertyValue::Bool(true))],
        )
        .unwrap();
    let mut invalid_nodes = Vec::new();
    for weight in [f64::NAN, f64::INFINITY, -0.2, 1.1] {
        let invalid = graph.add_node("invalid", None).unwrap();
        graph.add_edge(seed, invalid, weight, vec![]).unwrap();
        invalid_nodes.push(invalid);
    }
    let check = |graph: &ShardedGraph| {
        let hits = graph.search_and_traverse("uniqueseed", 1, &["cites"], 0.5, 2, 20);
        assert_eq!(hits.len(), 3);
        assert!(
            (hits.iter().find(|h| h.node == tail).unwrap().graph_strength - 0.36).abs() < 1e-12
        );
        assert_eq!(
            graph
                .search_and_traverse("uniqueseed", 1, &[], 0.5, 2, 20)
                .len(),
            3
        );
    };
    check(&graph);
    // Invalid non-finite weights are not JSON-serializable; remove their nodes.
    // Only the valid graph is persisted for the property roundtrip.
    for node in invalid_nodes {
        graph.remove_node(node).unwrap();
    }
    let dir = tempfile::tempdir().unwrap();
    graph.save_to_dir(dir.path()).unwrap();
    check(&ShardedGraph::load_from_dir(dir.path()).unwrap());
}

#[test]
fn sharded_paths_match_an_independent_bounded_walk_oracle() {
    for case in 0..20u32 {
        let mut graph = ShardedGraph::new(3);
        let nodes: Vec<_> = (0..8)
            .map(|i| {
                graph
                    .add_node(
                        &format!(
                            "{}case{case}node{i}",
                            if i == 0 { "uniqueseed " } else { "" }
                        ),
                        None,
                    )
                    .unwrap()
            })
            .collect();
        let mut edges = Vec::new();
        for s in 0..8 {
            for t in 0..8 {
                let hash = (s * 37 + t * 19 + case as usize * 11) % 17;
                if hash < 5 {
                    let w = (hash + 1) as f64 / 5.0;
                    edges.push((s, t, w));
                    graph.add_edge(nodes[s], nodes[t], w, vec![]).unwrap();
                }
            }
        }
        for depth in 0..5 {
            let mut best = [None::<f64>; 8];
            let mut layer = best;
            layer[0] = Some(1.0);
            best[0] = Some(1.0);
            for _ in 0..depth {
                let mut next = [None::<f64>; 8];
                for &(s, t, w) in &edges {
                    if let Some(score) = layer[s] {
                        next[t] = Some(next[t].unwrap_or(0.0).max(score * w));
                        best[t] = Some(best[t].unwrap_or(0.0).max(score * w));
                    }
                }
                layer = next;
            }
            let hits = graph.search_and_traverse("uniqueseed", 1, &[], 0.0, depth, 100);
            assert_eq!(hits.len(), best.iter().filter(|x| x.is_some()).count());
            for (i, expected) in best.iter().enumerate() {
                if let Some(score) = expected {
                    let hit = hits.iter().find(|h| h.node == nodes[i]).unwrap();
                    assert!((hit.graph_strength - score).abs() < 1e-12);
                    assert_eq!(hit.path.len(), hit.depth as usize);
                }
            }
        }
    }
}

#[test]
fn rebalance_preserves_live_content_vectors_and_qualified_topology() {
    use grap::PropertyValue;
    let mut graph = ShardedGraph::new(2);
    let mut nodes = Vec::new();
    for i in 0..20 {
        nodes.push(
            graph
                .add_node_with_embedding(
                    &format!("{}legalnode{i}", if i == 0 { "uniqueseed " } else { "" }),
                    Some("concept"),
                    Some(&[1.0, i as f32]),
                )
                .unwrap(),
        );
    }
    for pair in nodes.windows(2) {
        graph
            .router_mut()
            .add_edge_with_properties(
                pair[0],
                pair[1],
                0.9,
                vec![(
                    "cites".into(),
                    PropertyValue::Label("legal-reference".into()),
                )],
            )
            .unwrap();
    }
    let dead = graph.add_node("obsolete", None).unwrap();
    graph.remove_node(dead).unwrap();
    for count in [3, 5, 2] {
        let hashes: Vec<_> = nodes
            .iter()
            .map(|&n| graph.node_content_hash(n).unwrap())
            .collect();
        let plan = graph.plan_rebalance(count);
        let mapping = graph
            .router_mut()
            .execute_rebalance_with_mapping(plan)
            .unwrap();
        assert_eq!(mapping.len(), 20);
        nodes = nodes.iter().map(|n| mapping[n]).collect();
        assert_eq!(
            nodes
                .iter()
                .map(|&n| graph.node_content_hash(n).unwrap())
                .collect::<Vec<_>>(),
            hashes
        );
        let hits = graph.search_and_traverse("uniqueseed", 1, &["cites"], 0.0, 20, 100);
        assert_eq!(hits.len(), 20);
        for (i, node) in nodes.iter().enumerate() {
            let hit = hits.iter().find(|h| h.node == *node).unwrap();
            assert_eq!(hit.depth, i as u32);
            assert!((hit.graph_strength - 0.9f64.powi(i as i32)).abs() < 1e-12);
        }
        for (i, &node) in nodes.iter().enumerate() {
            assert_eq!(graph.read_embedding(node), Some(vec![1.0, i as f32]));
        }
        let dir = tempfile::tempdir().unwrap();
        graph.save_to_dir(dir.path()).unwrap();
        graph = ShardedGraph::load_from_dir(dir.path()).unwrap();
        assert_eq!(
            graph
                .search_and_traverse("uniqueseed", 1, &["cites"], 0.0, 20, 100)
                .len(),
            20
        );
    }
    let mut invalid = graph.plan_rebalance(3);
    invalid.node_migrations[0].1 = grap::ShardId(99);
    assert!(graph.execute_rebalance(invalid).is_err());
    assert_eq!(
        graph
            .search_and_traverse("uniqueseed", 1, &["cites"], 0.0, 20, 100)
            .len(),
        20
    );
}

#[test]
fn legacy_routing_is_preserved_until_explicit_rebalance() {
    let dir = tempfile::tempdir().unwrap();
    ShardedGraph::new(3).save_to_dir(dir.path()).unwrap();
    std::fs::write(
        dir.path().join("manifest.json"),
        r#"{"version":1,"shard_count":3}"#,
    )
    .unwrap();
    let mut legacy = ShardedGraph::load_from_dir(dir.path()).unwrap();
    let mut counts = [0; 3];
    for i in 0..3000 {
        let node = legacy
            .add_node(&format!("Romanian legal provision number {i}"), None)
            .unwrap();
        counts[node.shard.0 as usize] += 1;
    }
    assert_eq!(counts, [100, 2800, 100]);
    let plan = legacy.plan_rebalance(3);
    let mapping = legacy
        .router_mut()
        .execute_rebalance_with_mapping(plan)
        .unwrap();
    let mut balanced = [0; 3];
    for id in mapping.values() {
        balanced[id.shard.0 as usize] += 1;
    }
    assert!(balanced.iter().all(|&n| n > 300 && n < 1800));
    legacy.save_to_dir(dir.path()).unwrap();
    let manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read(dir.path().join("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["routing_version"], 2);
    std::fs::write(dir.path().join("ghost_edges.json"), b"broken topology").unwrap();
    assert!(ShardedGraph::load_from_dir(dir.path()).is_err());
}

#[test]
fn sharded_phrase_traversal_uses_phrase_seeds_and_gap() {
    let mut graph = ShardedGraph::new(3);
    let exact = graph.add_node("credit bancar", None).unwrap();
    graph.add_node("bancar credit", None).unwrap();
    let tail = graph.add_node("tail", None).unwrap();
    graph.add_edge(exact, tail, 0.9, vec![]).unwrap();
    let hits = graph.phrase_search_and_traverse("credit bancar", 10, 10, &[], 0.0, 1, 20);
    assert_eq!(hits.len(), 2);
    assert!(hits.iter().any(|h| h.node == tail));
    assert!(graph
        .phrase_search_and_traverse("credit bancar", 0, 10, &[], 0.0, 1, 20)
        .is_empty());
}
