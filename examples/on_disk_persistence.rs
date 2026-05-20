//! Demonstrates the major new 0.2 capabilities:
//! - On-disk segmented text index (`IndexedKnowledgeGraph::new_on_disk`)
//! - Full persistence round-trips (save_to_dir + load_from_dir with auto-detection)
//! - Segment information (live counts, sizes, delete ratios)
//! - Incremental vacuum / compaction after deletes
//! - Sharded graphs with persisted ghost edges
//! - Background maintenance with real periodic vacuum
//!
//! Run with:
//!   cargo run --example on_disk_persistence --features "fulltext,on-disk-index,sharding"

use std::sync::{Arc, Mutex};
use std::time::Duration;

use grap::{GlobalNodeId, IndexedKnowledgeGraph, ShardedGraph};

fn main() {
    println!("=== grap 0.2 On-Disk Persistence, Segments & Maintenance Demo ===\n");

    // =====================================================================
    // 1. Single-graph on-disk usage + persistence round-trip
    // =====================================================================
    println!("--- 1. On-disk IndexedKnowledgeGraph ---");

    let mut g = IndexedKnowledgeGraph::new_on_disk();

    let intro = g.add_node(
        "Knowledge graphs combined with on-disk indexes enable massive scale RAG systems.",
        Some("On-Disk Intro"),
    );
    let scale = g.add_node(
        "The segmented immutable design with tiered merging and incremental vacuum is key.",
        Some("Scale"),
    );
    let safety = g.add_node(
        "Tombstones + ghost edges allow safe deletes and cross-shard queries.",
        Some("Safety"),
    );

    g.add_edge(intro, scale, 0.92, vec![]);
    g.add_edge(scale, safety, 0.88, vec![]);

    println!("Created on-disk graph with {} nodes", g.graph.node_count());

    let segments = g.text_index_segment_info();
    println!("Current segments: {:?}", segments);

    // Save (this also flushes the active buffer into sealed segments)
    let on_disk_dir = std::env::temp_dir().join("grap_0_2_on_disk_demo");
    g.save_to_dir(&on_disk_dir).expect("save on-disk graph");
    println!("Saved on-disk graph to {:?}", on_disk_dir);

    // Load — because we detect manifest.json, we get back an OnDiskTextIndex, not memory!
    let mut loaded = IndexedKnowledgeGraph::load_from_dir(&on_disk_dir)
        .expect("load on-disk graph");
    println!(
        "Loaded graph has {} nodes (text index is on-disk: {})",
        loaded.graph.node_count(),
        loaded.text_index_segment_info().len() > 0
    );

    let loaded_segments = loaded.text_index_segment_info();
    println!("Loaded segments: {:?}", loaded_segments);

    // Simulate deletes (soft-delete via the graph + text index)
    loaded.graph.remove_node(intro); // marks tombstone
    println!("Removed one node (soft delete / tombstone created)");

    // Incremental vacuum — only rewrites segments that have meaningful deletes
    loaded.vacuum_deleted_nodes();
    let after_vacuum = loaded.text_index_segment_info();
    println!("After vacuum: {:?}", after_vacuum);

    // =====================================================================
    // 2. Sharded persistence (ghost edges are now round-tripped)
    // =====================================================================
    println!("\n--- 2. ShardedGraph with persistence ---");

    let mut sg = ShardedGraph::new(3);

    let n1: GlobalNodeId = sg.add_node("Node that will live on one shard", None).unwrap();
    let n2: GlobalNodeId = sg.add_node("Node that may live on a different shard", None).unwrap();
    let _ = sg.add_edge(n1, n2, 0.85, vec![]);

    println!(
        "Created 3-shard graph ({} total nodes, {} ghost edges)",
        sg.global_node_count(),
        sg.ghost_edge_count()
    );

    let sharded_dir = std::env::temp_dir().join("grap_0_2_sharded_demo");
    sg.save_to_dir(&sharded_dir).expect("save sharded");
    println!("Saved sharded graph (ghost_edges.json + per-shard manifests written)");

    let loaded_sg = ShardedGraph::load_from_dir(&sharded_dir).expect("load sharded");
    println!(
        "Loaded sharded graph: {} shards, {} ghost edges restored",
        loaded_sg.shard_count(),
        loaded_sg.ghost_edge_count()
    );

    // =====================================================================
    // 3. Background maintenance (real periodic vacuum)
    // =====================================================================
    println!("\n--- 3. Background Maintenance ---");

    let sg_for_bg = Arc::new(Mutex::new(ShardedGraph::new(2)));
    let handle = {
        let sg = sg_for_bg.lock().unwrap();
        sg.start_background_maintenance_with_shared(sg_for_bg.clone(), Duration::from_secs(2))
    };

    println!("Started background maintenance thread (calls incremental vacuum every 2s)");

    // In a real application you would do work here while the background thread runs.
    std::thread::sleep(Duration::from_millis(1500));

    handle.shutdown();
    println!("Background maintenance shut down cleanly");

    println!("\n✅ All 0.2 capabilities demonstrated successfully!");
    println!("   (on-disk round-trips, segment visibility, incremental vacuum, sharded ghosts, background maintenance)");
}
