//! Basic integration test for the new 0.2 ghost edge persistence + rebalance round-trip.
//!
//! Uses only public APIs and directly exercises the ghost snapshot/restore
//! helpers we added for persistence.

#![cfg(all(feature = "fulltext", feature = "on-disk-index", feature = "sharding"))]

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
