//! Demonstrates the rich text + graph retrieval capabilities of `IndexedKnowledgeGraph`.
//!
//! Run with:
//!   cargo run --example hybrid_search

use grap::{FusionWeights, IndexedKnowledgeGraph, PropertyValue};

fn main() {
    println!("=== grap Hybrid Text + Graph Search Demo ===\n");

    let mut g = IndexedKnowledgeGraph::new();

    let topic = g.graph.properties.register("topic");
    let confidence = g.graph.properties.register("confidence");

    // === Build a small knowledge graph about Rust ===

    let rust = g.add_node(
        "Rust is a systems programming language that guarantees memory safety without garbage collection.",
        Some("Rust Overview"),
    );

    let ownership = g.add_node(
        "The ownership system is the core of Rust's memory safety model. Every value has exactly one owner.",
        Some("Ownership"),
    );

    let borrowing = g.add_node(
        "Borrowing allows temporary access to values without taking ownership. The borrow checker enforces rules at compile time.",
        Some("Borrowing"),
    );

    let lifetimes = g.add_node(
        "Lifetimes describe how long references are valid. They are essential when working with borrowed data.",
        Some("Lifetimes"),
    );

    let concurrency = g.add_node(
        "Fearless concurrency is possible in Rust because the ownership system prevents data races at compile time.",
        Some("Concurrency"),
    );

    // Add strong, property-carrying edges
    g.add_edge(
        rust,
        ownership,
        0.95,
        vec![
            (topic, PropertyValue::Label("memory_safety".into())),
            (confidence, PropertyValue::Float(0.98)),
        ],
    );

    g.add_edge(
        ownership,
        borrowing,
        0.92,
        vec![
            (topic, PropertyValue::Label("memory_safety".into())),
            (confidence, PropertyValue::Float(0.93)),
        ],
    );

    g.add_edge(
        borrowing,
        lifetimes,
        0.88,
        vec![
            (topic, PropertyValue::Label("memory_safety".into())),
            (confidence, PropertyValue::Float(0.90)),
        ],
    );

    g.add_edge(
        rust,
        concurrency,
        0.85,
        vec![
            (topic, PropertyValue::Label("concurrency".into())),
            (confidence, PropertyValue::Float(0.87)),
        ],
    );

    println!("Graph built with 5 nodes and rich relationships.\n");

    // === Example 1: Default balanced search ===
    println!("--- 1. Balanced search (default weights) ---");
    let results = g.search_and_traverse_default(
        "ownership borrowing",
        5,
        &["topic"],
        0.7,
        3,
        10,
    );

    for (i, r) in results.iter().enumerate().take(5) {
        println!(
            "{}. {} (text={:.2}, graph={:.2}, fused={:.2}) — {}",
            i + 1,
            r.graph_result.node_id,
            r.text_score,
            r.graph_strength,
            r.fused_score,
            r.text.chars().take(70).collect::<String>()
        );
    }

    // === Example 2: Bias strongly toward text relevance ===
    println!("\n--- 2. Text-heavy fusion (text=0.9, graph=0.1) ---");
    let text_heavy = FusionWeights { text: 0.9, graph: 0.1 };
    let results = g.search_and_traverse(
        "ownership borrowing",
        5,
        &["topic"],
        0.7,
        3,
        10,
        text_heavy,
    );

    for (i, r) in results.iter().enumerate().take(4) {
        println!(
            "{}. fused={:.2} (text={:.2}) — {}",
            i + 1,
            r.fused_score,
            r.text_score,
            r.text.chars().take(60).collect::<String>()
        );
    }

    // === Example 3: Bias toward strong graph paths ===
    println!("\n--- 3. Graph-heavy fusion (text=0.2, graph=0.8) ---");
    let graph_heavy = FusionWeights { text: 0.2, graph: 0.8 };
    let results = g.search_and_traverse(
        "memory safety",
        5,
        &["topic"],
        0.6,
        3,
        10,
        graph_heavy,
    );

    for (i, r) in results.iter().enumerate().take(4) {
        println!(
            "{}. fused={:.2} (graph strength={:.2}) — depth {}",
            i + 1,
            r.fused_score,
            r.graph_strength,
            r.graph_result.depth
        );
    }

    println!("\n✅ Both text relevance and graph relationship strength are now visible and tunable!");
}
