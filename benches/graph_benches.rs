//! Criterion benchmarks for the grap knowledge graph engine.
//!
//! Run with:
//!   cargo bench --features "fulltext,sharding"
//!
//! These benchmarks validate the core architectural claims from core_graph_design.md:
//! - O(1) property bitmask pruning during traversal
//! - Acceptable overhead of sharded distributed traversal (ghost edges)
//! - Cost of rebalancing

use criterion::{black_box, criterion_group, criterion_main, Criterion};

use grap::{FusionWeights, IndexedKnowledgeGraph, PropertyValue, ShardedGraph};

/// Build a medium-sized synthetic graph with several properties.
/// This is used to demonstrate bitmask pruning effectiveness.
fn build_synthetic_graph(node_count: usize, edges_per_node: usize) -> IndexedKnowledgeGraph {
    let mut g = IndexedKnowledgeGraph::new();

    let topic = g.graph.properties.register("topic");
    let confidence = g.graph.properties.register("confidence");
    let category = g.graph.properties.register("category");
    let relation = g.graph.properties.register("relation");

    let topics = [
        "rust",
        "memory",
        "safety",
        "concurrency",
        "performance",
        "ownership",
    ];
    let categories = ["core", "advanced", "tooling", "ecosystem"];

    let mut node_ids = Vec::with_capacity(node_count);

    for i in 0..node_count {
        let text = format!(
            "Document {} about Rust systems programming, memory safety, \
             ownership, borrowing, lifetimes, and fearless concurrency. \
             This is node number {} in the synthetic benchmark graph.",
            i, i
        );
        let label = Some(format!("Node{}", i));
        let id = g.add_node(&text, label.as_deref());
        node_ids.push(id);
    }

    // Create a connected graph with varying property sets
    for i in 0..node_count {
        for j in 1..=edges_per_node {
            let target = (i + j * 17) % node_count; // pseudo-random but deterministic
            if target == i {
                continue;
            }

            let mut props = Vec::new();

            // Every edge gets a topic
            let t = topics[i % topics.len()];
            props.push((topic, PropertyValue::Label(t.to_string())));

            // ~60% get confidence
            if (i + j) % 3 != 0 {
                let conf = 0.7 + ((i * j) % 30) as f64 / 100.0;
                props.push((confidence, PropertyValue::Float(conf)));
            }

            // Some get category / relation
            if (i + j) % 4 == 0 {
                let cat = categories[(i + j) % categories.len()];
                props.push((category, PropertyValue::Label(cat.to_string())));
            }
            if (i + j) % 5 == 0 {
                props.push((relation, PropertyValue::Label("related".to_string())));
            }

            let strength = 0.6 + ((i + j) % 40) as f64 / 100.0;
            g.add_edge(node_ids[i], node_ids[target], strength, props);
        }
    }

    g
}

/// Build a sharded version of a similar graph.
fn build_synthetic_sharded_graph(
    shard_count: u16,
    node_count: usize,
    edges_per_node: usize,
) -> ShardedGraph {
    let mut sg = ShardedGraph::new(shard_count);

    // We use the high-level add_node which hashes content into shards.
    // Property registration is done lazily inside each shard for this benchmark.
    let mut ids = Vec::new();
    for i in 0..node_count {
        let text = format!(
            "Sharded doc {} - Rust memory safety and concurrency {}",
            i, i
        );
        let id = sg
            .add_node(&text, Some(&format!("SNode{}", i)))
            .expect("add_node failed in benchmark setup");
        ids.push(id);
    }

    // Add some cross-shard edges (the router will create ghost edges automatically)
    for i in 0..node_count {
        for j in 1..=edges_per_node {
            let target_idx = (i + j * 31) % node_count;
            if target_idx == i {
                continue;
            }
            let strength = 0.65;
            sg.add_edge(ids[i], ids[target_idx], strength, vec![])
                .expect("add_edge failed in benchmark setup");
        }
    }

    sg
}

fn bench_bitmask_pruning(c: &mut Criterion) {
    let g = build_synthetic_graph(2_000, 6);

    let mut group = c.benchmark_group("bitmask_pruning");

    // Unfiltered traversal (should visit many more nodes/edges)
    group.bench_function("no_property_filter", |b| {
        b.iter(|| {
            let results = g.search_and_traverse(
                "memory safety ownership",
                20,
                &[], // no required properties → no mask pruning
                0.3,
                4,
                200,
                FusionWeights {
                    text: 0.5,
                    graph: 0.5,
                },
            );
            black_box(results.len())
        })
    });

    // Strongly filtered (the mask should cause massive early pruning)
    group.bench_function("with_topic_filter", |b| {
        b.iter(|| {
            let results = g.search_and_traverse(
                "memory safety ownership",
                20,
                &["topic"], // requires the "topic" bit → strong pruning via masks
                0.3,
                4,
                200,
                FusionWeights {
                    text: 0.5,
                    graph: 0.5,
                },
            );
            black_box(results.len())
        })
    });

    group.finish();
}

fn bench_sharded_vs_monolithic(c: &mut Criterion) {
    // We compare a 4-shard cluster against a single IndexedKnowledgeGraph
    // with roughly the same amount of data.
    let single = build_synthetic_graph(1_200, 5);
    let sharded = build_synthetic_sharded_graph(4, 1_200, 5);

    let mut group = c.benchmark_group("sharded_vs_single");

    group.bench_function("single_shard_search_and_traverse", |b| {
        b.iter(|| {
            let res = single.search_and_traverse(
                "rust concurrency",
                10,
                &[],
                0.4,
                3,
                80,
                FusionWeights::default(),
            );
            black_box(res.len())
        })
    });

    group.bench_function("4_shard_distributed_search", |b| {
        b.iter(|| {
            let res = sharded.search_and_traverse("rust concurrency", 10, &[], 0.4, 3, 80);
            black_box(res.len())
        })
    });

    group.finish();
}

fn bench_rebalance_planning(c: &mut Criterion) {
    let sg = build_synthetic_sharded_graph(4, 3_000, 4);

    c.bench_function("plan_rebalance_4_to_6_shards", |b| {
        b.iter(|| {
            let plan = sg.plan_rebalance(6);
            black_box(plan.node_migrations.len())
        })
    });
}

criterion_group!(
    benches,
    bench_bitmask_pruning,
    bench_sharded_vs_monolithic,
    bench_rebalance_planning
);
criterion_main!(benches);
