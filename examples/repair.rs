//! Demonstrates the `repair()` API and `RepairReport` for diagnosing and
//! recovering from interrupted saves.
//!
//! Run with:
//!   cargo run --example repair

use grap::KnowledgeGraph;
use std::fs;

fn main() {
    println!("=== grap Repair & RepairReport Demo ===\n");

    let mut g = KnowledgeGraph::new();
    let topic = g.properties.register("topic");

    let n1 = g.add_node("Document about Rust memory safety.", Some("rust-memory"));
    let n2 = g.add_node("Document about ownership and borrowing.", Some("ownership"));

    g.add_edge(n1, n2, 0.91, vec![(topic, grap::PropertyValue::Label("memory".to_string()))]);

    // Create a directory and save the graph normally
    let base = std::env::temp_dir().join("grap-repair-demo");
    let graph_dir = base.join("my-graph");
    let _ = fs::remove_dir_all(&base); // clean previous runs
    fs::create_dir_all(&graph_dir).unwrap();

    g.save_to_disk(&graph_dir).expect("initial save should succeed");
    println!("✓ Saved graph to {:?}", graph_dir);

    // Simulate a crashed save by manually creating a stale temp directory
    let stale = base.join(".grap-save-crash-98765");
    fs::create_dir(&stale).unwrap();
    fs::write(stale.join("partial-blob"), b"incomplete data").unwrap();
    println!("✓ Created simulated stale temp directory (as if a save crashed)");

    // Now call repair
    println!("\n--- Running repair() ---");
    let report = KnowledgeGraph::repair(&graph_dir).expect("repair should succeed");

    println!("RepairReport:");
    println!("  removed_temp_dirs   : {}", report.removed_temp_dirs);
    println!("  graph_files_present : {}", report.graph_files_present);
    println!("  meta_present        : {}", report.meta_present);
    println!("  topo_present        : {}", report.topo_present);
    println!("  blobs_present       : {}", report.blobs_present);
    println!("  meta_version        : {:?}", report.meta_version);
    println!("  version_compatible  : {:?}", report.version_compatible);
    println!("  checksum_failures   : {}", report.checksum_failures);
    println!("  notes               : {:?}", report.notes);

    if report.removed_temp_dirs > 0 {
        println!("\n✓ Successfully cleaned up stale save artifacts.");
    }

    // Verify we can still load the graph
    let reloaded = KnowledgeGraph::load_from_disk(&graph_dir).expect("load after repair should work");
    println!("\n✓ Successfully reloaded graph after repair ({} nodes)", reloaded.node_count());

    // Cleanup
    let _ = fs::remove_dir_all(&base);
    println!("\nDemo directory cleaned up.");
}