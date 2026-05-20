//! Demonstrates hybrid text + vector + graph retrieval with `search_and_traverse_hybrid`.
//!
//! This example shows how to ingest nodes that carry both text and embeddings,
//! then perform queries that fuse three signals:
//!   - BM25 text relevance
//!   - Cosine similarity to a query vector
//!   - Graph path strength
//!
//! In a real application you would obtain the embeddings from a model
//! (e.g. sentence-transformers, OpenAI, or a local Candle model).
//! Here we use tiny hand-crafted 4D vectors for a self-contained demo.
//!
//! Run with:
//!   cargo run --example hybrid_search

use grap::{FusionWeights, HybridWeights, IndexedKnowledgeGraph, PropertyValue};

fn main() {
    println!("=== grap Hybrid Text + Graph Search Demo ===\n");

    let mut g = IndexedKnowledgeGraph::new();

    let topic = g.graph.properties.register("topic");
    let confidence = g.graph.properties.register("confidence");

    // === Build a small knowledge graph about Rust ===
    //
    // Each node gets a tiny 4-dimensional "embedding" that captures topical emphasis.
    // Dimensions: [safety/memory, ownership, borrowing/lifetimes, concurrency]

    let rust = g.add_node(
        "Rust is a systems programming language that guarantees memory safety without garbage collection.",
        Some("Rust Overview"),
        Some(&[0.95, 0.70, 0.40, 0.60]),
    );

    let ownership = g.add_node(
        "The ownership system is the core of Rust's memory safety model. Every value has exactly one owner.",
        Some("Ownership"),
        Some(&[0.85, 0.98, 0.55, 0.30]),
    );

    let borrowing = g.add_node(
        "Borrowing allows temporary access to values without taking ownership. The borrow checker enforces rules at compile time.",
        Some("Borrowing"),
        Some(&[0.70, 0.65, 0.95, 0.25]),
    );

    let lifetimes = g.add_node(
        "Lifetimes describe how long references are valid. They are essential when working with borrowed data.",
        Some("Lifetimes"),
        Some(&[0.60, 0.50, 0.92, 0.20]),
    );

    let concurrency = g.add_node(
        "Fearless concurrency is possible in Rust because the ownership system prevents data races at compile time.",
        Some("Concurrency"),
        Some(&[0.75, 0.45, 0.30, 0.95]),
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
    let results = g.search_and_traverse_default("ownership borrowing", 5, &["topic"], 0.7, 3, 10);

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
    let text_heavy = FusionWeights {
        text: 0.9,
        graph: 0.1,
    };
    let results =
        g.search_and_traverse("ownership borrowing", 5, &["topic"], 0.7, 3, 10, text_heavy);

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
    let graph_heavy = FusionWeights {
        text: 0.2,
        graph: 0.8,
    };
    let results = g.search_and_traverse("memory safety", 5, &["topic"], 0.6, 3, 10, graph_heavy);

    for (i, r) in results.iter().enumerate().take(4) {
        println!(
            "{}. fused={:.2} (graph strength={:.2}) — depth {}",
            i + 1,
            r.fused_score,
            r.graph_strength,
            r.graph_result.depth
        );
    }

    println!(
        "\n✅ Both text relevance and graph relationship strength are now visible and tunable!"
    );

    // =====================================================================
    // NEW: Vector + Hybrid Search demonstrations
    // =====================================================================

    println!("\n=== Vector + Hybrid (text + vector + graph) Search ===\n");

    // A query vector that emphasizes "concurrency + safety"
    let concurrency_safety_query: &[f32] = &[0.80, 0.50, 0.35, 0.90];

    // --- Hybrid default (balanced text / vector / graph) ---
    println!("--- 4. Hybrid search (default HybridWeights) ---");
    println!("Query text: 'concurrency safety'  |  Query vector emphasizes concurrency + safety\n");

    let hybrid_results = g.search_and_traverse_hybrid_default(
        "concurrency safety",
        Some(concurrency_safety_query),
        5,
        &["topic"],
        0.6,
        3,
        10,
    );

    for (i, r) in hybrid_results.iter().enumerate().take(6) {
        println!(
            "{}. fused={:.3}  (text={:.2}, vec={:.2}, graph={:.2})  depth={}  — {}",
            i + 1,
            r.fused_score,
            r.text_score,
            r.vector_score,
            r.graph_strength,
            r.graph_result.depth,
            r.text.chars().take(65).collect::<String>()
        );
    }

    // --- Custom HybridWeights that heavily favors the vector signal ---
    println!("\n--- 5. Vector-heavy hybrid (vector weight = 0.7) ---");

    let vector_heavy = HybridWeights {
        text: 0.15,
        vector: 0.70,
        graph: 0.15,
    };

    let vector_biased = g.search_and_traverse_hybrid(
        "rust",
        Some(concurrency_safety_query),
        5,
        &["topic"],
        0.5,
        3,
        10,
        vector_heavy,
    );

    for (i, r) in vector_biased.iter().enumerate().take(5) {
        println!(
            "{}. fused={:.3}  (vec={:.3})  — {}",
            i + 1,
            r.fused_score,
            r.vector_score,
            r.text.chars().take(70).collect::<String>()
        );
    }

    println!(
        "\n🎉 Hybrid search successfully fuses text BM25 + cosine(vector) + graph strength!"
    );
    println!("   You can now tune HybridWeights to emphasize semantic similarity (vector),");
    println!("   keyword matching (text), or relationship strength (graph).");
}
