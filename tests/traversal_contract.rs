use grap::{KnowledgeGraph, NodeId, PropertyValue};

#[test]
fn property_weights_choose_best_path_and_invalid_weights_are_skipped() {
    let mut graph = KnowledgeGraph::new();
    let confidence = graph.properties.register("confidence");
    let start = graph.add_node("start", None, None);
    let bridge = graph.add_node("bridge", None, None);
    let target = graph.add_node("target", None, None);
    graph.add_edge(
        start,
        target,
        0.9,
        vec![(confidence, PropertyValue::Float(0.1))],
    );
    let first = graph.add_edge(
        start,
        bridge,
        0.5,
        vec![(confidence, PropertyValue::Float(0.9))],
    );
    let second = graph.add_edge(
        bridge,
        target,
        0.5,
        vec![(confidence, PropertyValue::Float(0.9))],
    );
    for value in [f64::NAN, f64::INFINITY, -0.5, 1.1] {
        let invalid = graph.add_node("invalid", None, None);
        graph.add_edge(
            start,
            invalid,
            0.8,
            vec![(confidence, PropertyValue::Float(value))],
        );
    }
    let hits = graph
        .find()
        .from_node(start)
        .weight_by_property("confidence")
        .min_strength(0.5)
        .depth(2)
        .execute();
    assert_eq!(hits.len(), 3);
    assert_eq!(hits[2].path, vec![first, second]);
    assert_eq!(hits[2].cumulative_strength, 0.81);
}

#[test]
fn strongest_path_can_replace_an_earlier_weak_direct_edge() {
    let mut graph = KnowledgeGraph::new();
    let a = graph.add_node("seed", None, None);
    let b = graph.add_node("bridge", None, None);
    let c = graph.add_node("target", None, None);
    graph.add_edge(a, c, 0.1, vec![]);
    let ab = graph.add_edge(a, b, 0.9, vec![]);
    let bc = graph.add_edge(b, c, 0.9, vec![]);
    let hits = graph.find().from_node(a).depth(2).execute();
    let hit = hits.iter().find(|r| r.node_id == c).unwrap();
    assert_eq!(hit.path, vec![ab, bc]);
    assert_eq!(hit.cumulative_strength, 0.81);
}

#[test]
fn shorter_weaker_route_must_still_expand_within_hop_budget() {
    let mut graph = KnowledgeGraph::new();
    let ids: Vec<_> = (0..5).map(|_| graph.add_node("node", None, None)).collect();
    graph.add_edge(ids[0], ids[1], 0.9, vec![]);
    graph.add_edge(ids[1], ids[2], 0.9, vec![]);
    graph.add_edge(ids[2], ids[3], 0.9, vec![]);
    let shortcut = graph.add_edge(ids[0], ids[3], 0.2, vec![]);
    let tail = graph.add_edge(ids[3], ids[4], 0.8, vec![]);
    let hits = graph.find().from_node(ids[0]).depth(3).execute();
    let best = hits.iter().find(|r| r.node_id == ids[3]).unwrap();
    assert_eq!(best.depth, 3);
    let descendant = hits.iter().find(|r| r.node_id == ids[4]).unwrap();
    assert_eq!(descendant.path, vec![shortcut, tail]);
    assert_eq!(hits.len(), 5);
}

#[test]
fn seeds_filters_and_limits_have_explicit_empty_behavior() {
    let mut graph = KnowledgeGraph::new();
    let live = graph.add_node("live", None, None);
    let dead = graph.add_node("dead", None, None);
    graph.remove_node(dead);
    assert!(graph.find().from_node(live).limit(0).execute().is_empty());
    assert!(graph.find().from_node(dead).execute_with_text().is_empty());
    assert!(graph
        .find()
        .from_node(NodeId(u32::MAX))
        .execute_with_text()
        .is_empty());
    let hits = graph
        .find()
        .from_nodes(&[live, live, dead, NodeId(u32::MAX)])
        .execute();
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].node_id, live);
    assert_eq!(graph.find().execute().len(), 1);
    assert!(graph
        .find()
        .from_node(live)
        .with_property("missing")
        .execute()
        .is_empty());
    assert!(graph
        .find()
        .from_node(live)
        .where_in_range("missing", 0.0, 1.0)
        .execute()
        .is_empty());
    assert!(graph
        .traverse_by_relation(live, "missing", PropertyValue::Int(1), 3, 10)
        .is_empty());
}

#[test]
fn bounded_traversal_matches_exhaustive_walks_on_small_cyclic_graphs() {
    // Independent depth-bounded dynamic program: enumerate all walks by hop
    // count, then compare each node's best score and validate returned paths.
    let mut rng = 73u64;
    for _case in 0..30 {
        let mut graph = KnowledgeGraph::new();
        for _ in 0..7 {
            graph.add_node("node", None, None);
        }
        let mut edges = Vec::new();
        for from in 0..7 {
            for to in 0..7 {
                rng = rng.wrapping_mul(6364136223846793005).wrapping_add(1);
                if rng >> 61 < 3 {
                    let w = ((rng >> 32) % 5) as f64 / 4.0;
                    let edge = graph.add_edge(NodeId(from), NodeId(to), w, vec![]);
                    edges.push((from as usize, to as usize, w, edge));
                }
            }
        }
        for depth in 0..5 {
            let mut layer: Vec<Option<f64>> = vec![None; 7];
            layer[0] = Some(1.0);
            let mut best = layer.clone();
            for _ in 0..depth {
                let mut next: Vec<Option<f64>> = vec![None; 7];
                for &(from, to, w, _) in &edges {
                    if let Some(score) = layer[from] {
                        let score = score * w;
                        next[to] = Some(next[to].map_or(score, |old| old.max(score)));
                    }
                }
                for node in 0..7 {
                    if let Some(score) = next[node] {
                        best[node] = Some(best[node].map_or(score, |old| old.max(score)));
                    }
                }
                layer = next;
            }
            let hits = graph.find().from_node(NodeId(0)).depth(depth).execute();
            assert_eq!(hits.len(), best.iter().flatten().count());
            assert!(hits
                .windows(2)
                .all(|p| p[0].cumulative_strength >= p[1].cumulative_strength));
            for hit in hits {
                assert_eq!(Some(hit.cumulative_strength), best[hit.node_id.0 as usize]);
                let (mut cursor, mut score) = (NodeId(0), 1.0);
                for edge in &hit.path {
                    let (from, to) = graph.edge_endpoints(*edge);
                    assert_eq!(cursor, from);
                    cursor = to;
                    score *= graph.edge_strength(*edge);
                }
                assert_eq!(cursor, hit.node_id);
                assert_eq!(score, hit.cumulative_strength);
                assert_eq!(hit.path.len(), hit.depth as usize);
                assert!(hit.depth <= depth);
            }
        }
    }
}
