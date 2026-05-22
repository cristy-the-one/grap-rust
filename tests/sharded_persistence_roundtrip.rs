//! Basic integration test for the new 0.2 ghost edge persistence + rebalance round-trip.
//!
//! Uses only public APIs and directly exercises the ghost snapshot/restore
//! helpers we added for persistence.

#![cfg(all(feature = "fulltext", feature = "sharding"))]

use grap::{GlobalNodeId, NodeId, ShardId, ShardedGraph};

#[test]
fn ghost_edges_survive_rebalance_and_manual_roundtrip() {
    let mut sg = ShardedGraph::new(2);

    // Create many nodes so we are likely to get cross-shard edges
    for i in 0..20 {
        let _ = sg.add_node(&format!("doc {}", i), None);
    }
    // Add edges between them
    for i in 0..19 {
        let _ = sg.add_edge(
            GlobalNodeId {
                shard: ShardId(0),
                local: NodeId(i),
            },
            GlobalNodeId {
                shard: ShardId(1),
                local: NodeId(i + 1),
            },
            0.7,
            vec![],
        );
    }

    let before = sg.ghost_edge_count();
    assert!(before > 0, "we should have created at least one ghost edge");

    // Rebalance
    let plan = sg.plan_rebalance(3);
    sg.execute_rebalance(plan).expect("rebalance");

    // Use the public snapshot/restore helpers (what save/load now use)
    let snapshot = sg.ghost_edges_snapshot();

    // Fresh router (like load_from_dir does)
    let mut fresh = ShardedGraph::new(3);
    fresh.restore_ghost_edges(snapshot);

    assert_eq!(
        fresh.ghost_edge_count(),
        sg.ghost_edge_count(),
        "ghost edges must round-trip after rebalance using the public persistence helpers"
    );
}

/// Tests that vector embeddings survive a full sharded save/load cycle,
/// including the new Phase 3 vector index persistence (manifest + vectors.bin).
#[test]
fn vector_index_survives_sharded_save_load() {
    use grap::ShardedGraph;

    let mut sg = ShardedGraph::new(3);

    // Add nodes with embeddings across shards
    let n0 = sg.add_node_with_embedding("rust ownership", None, Some(&[1.0f32, 0.0, 0.0])).unwrap();
    let n1 = sg.add_node_with_embedding("borrowing rules", None, Some(&[0.0f32, 1.0, 0.0])).unwrap();
    let _n2 = sg.add_node_with_embedding("lifetimes are hard", None, Some(&[0.0f32, 0.0, 1.0])).unwrap();

    // Save
    let dir = tempfile::tempdir().unwrap();
    sg.save_to_dir(dir.path()).expect("save sharded graph");

    // Load fresh
    let loaded = ShardedGraph::load_from_dir(dir.path()).expect("load sharded graph");

    // Verify standalone vector search works after load
    let results = loaded.vector_search(&[1.0f32, 0.0, 0.0], 5);
    let ids: Vec<_> = results.iter().map(|(gid, _)| (gid.shard.0, gid.local.0)).collect();

    assert!(
        ids.contains(&(n0.shard.0, n0.local.0)),
        "n0 should be findable via vector search after sharded load"
    );

    // Also sanity-check that we have the other embeddings
    let results2 = loaded.vector_search(&[0.0f32, 1.0, 0.0], 5);
    assert!(!results2.is_empty(), "should find at least one vector result");
}
