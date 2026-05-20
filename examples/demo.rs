//! Runnable demo of the core KnowledgeGraph (Phase 1).
//! Adapted directly from the end-to-end test in core_graph_design.md:859-999.
//! Uses String labels instead of the original CompactString simulation.

use grap::{KnowledgeGraph, PropertyValue};

fn main() {
    println!("=== grap core demo (ported from core_graph_design.md) ===\n");

    let mut g = KnowledgeGraph::new();

    // Register property dimensions (u16 tags + bitmasks under the hood)
    let topic = g.properties.register("topic");
    let confidence = g.properties.register("confidence");
    let relation_type = g.properties.register("relation_type");

    println!(
        "Registered properties: topic={:?}, confidence={:?}, relation_type={:?}",
        topic, confidence, relation_type
    );

    // Add nodes with realistic Rust text (the "blobs")
    let rust_node = g.add_node(
        "Rust is a systems programming language focused on safety, \
         concurrency, and performance. It achieves memory safety without \
         garbage collection through its ownership system...",
        Some("Rust Overview"),
    );

    let ownership_node = g.add_node(
        "The ownership system is Rust's most distinctive feature. \
         Every value has exactly one owner, and when the owner goes \
         out of scope, the value is dropped...",
        Some("Ownership"),
    );

    let borrowing_node = g.add_node(
        "Borrowing allows you to reference data without taking ownership. \
         You can have multiple immutable references or exactly one mutable \
         reference at any given time...",
        Some("Borrowing"),
    );

    let lifetimes_node = g.add_node(
        "Lifetimes are Rust's way of ensuring that references are always \
         valid. The borrow checker uses lifetime annotations to verify \
         that references don't outlive the data they point to...",
        Some("Lifetimes"),
    );

    let concurrency_node = g.add_node(
        "Rust's ownership system naturally prevents data races at compile \
         time. Combined with types like Mutex<T> and Arc<T>, it enables \
         fearless concurrency...",
        Some("Concurrency"),
    );

    println!(
        "Added {} nodes, {} edges so far",
        g.node_count(),
        g.edge_count()
    );

    // Add edges with strength + property tuples (bitmask built automatically)
    g.add_edge(
        rust_node,
        ownership_node,
        0.95,
        vec![
            (topic, PropertyValue::Label("memory_safety".to_string())),
            (confidence, PropertyValue::Float(0.99)),
            (
                relation_type,
                PropertyValue::Label("core_concept".to_string()),
            ),
        ],
    );

    g.add_edge(
        ownership_node,
        borrowing_node,
        0.90,
        vec![
            (topic, PropertyValue::Label("memory_safety".to_string())),
            (confidence, PropertyValue::Float(0.95)),
            (relation_type, PropertyValue::Label("extends".to_string())),
        ],
    );

    g.add_edge(
        borrowing_node,
        lifetimes_node,
        0.85,
        vec![
            (topic, PropertyValue::Label("memory_safety".to_string())),
            (confidence, PropertyValue::Float(0.88)),
            (relation_type, PropertyValue::Label("requires".to_string())),
        ],
    );

    g.add_edge(
        rust_node,
        concurrency_node,
        0.80,
        vec![
            (topic, PropertyValue::Label("parallelism".to_string())),
            (confidence, PropertyValue::Float(0.85)),
            (relation_type, PropertyValue::Label("enables".to_string())),
        ],
    );

    g.add_edge(
        ownership_node,
        concurrency_node,
        0.75,
        vec![
            (topic, PropertyValue::Label("parallelism".to_string())),
            (confidence, PropertyValue::Float(0.80)),
            (relation_type, PropertyValue::Label("enables".to_string())),
        ],
    );

    println!(
        "Graph now has {} nodes, {} edges\n",
        g.node_count(),
        g.edge_count()
    );

    // THE KEY QUERY from the design doc:
    // "Find all nodes connected to Rust via memory_safety with high confidence, up to 3 hops"
    let results = g
        .find()
        .from_node(rust_node)
        .where_property("topic", PropertyValue::Label("memory_safety".to_string()))
        .where_in_range("confidence", 0.85, 1.0)
        .min_strength(0.8)
        .depth(3)
        .limit(10)
        .execute_with_text();

    println!("=== Query Results (memory_safety + confidence >= 0.85, depth<=3) ===");
    for (i, (result, text)) in results.iter().enumerate() {
        let preview = &text[..text.len().min(80)];
        println!(
            "{}. {:?} depth={} strength={:.3}  \"{}\" ...",
            i + 1,
            result.node_id,
            result.depth,
            result.cumulative_strength,
            preview
        );
    }

    println!(
        "\nGraph stats: {} nodes, {} edges",
        g.node_count(),
        g.edge_count()
    );

    // Verify the important semantic properties from the design
    assert!(
        results.len() >= 3,
        "Expected at least rust + ownership + borrowing"
    );
    assert!(g.read_blob(rust_node).contains("systems programming"));
    assert!(g.read_blob(ownership_node).contains("exactly one owner"));

    println!("\n✅ Core KnowledgeGraph + bitmask pruning + QueryBuilder working as specified!");
    println!("   (All assertions from the design document test passed.)\n");
}
