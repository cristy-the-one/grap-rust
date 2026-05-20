//! Criterion benchmarks for the grap knowledge graph engine.
//!
//! These benchmarks aim to validate the key architectural claims from `core_graph_design.md`.
//!
//! Run with:
//!   cargo bench --features "fulltext,sharding"
//!
//! For the mmap scenario you also need the `mmap` feature:
//!   cargo bench --features "fulltext,sharding,mmap"
//!
//! See `benches/README.md` for methodology and example output.

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
        let id = g.add_node(&text, label.as_deref(), None);
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

/// Builds a graph with larger per-node text blobs (useful for mmap cold-cache benchmarks).
#[cfg(feature = "mmap")]
fn build_graph_with_large_blobs(node_count: usize) -> IndexedKnowledgeGraph {
    let mut g = IndexedKnowledgeGraph::new();

    for i in 0..node_count {
        // ~4-6 KiB of text per node
        let text = format!(
            "Large document {}.\n\n{}",
            i,
            "This is filler text to create reasonably sized blobs for mmap benchmarking. \
             Rust memory safety, ownership, borrowing, lifetimes, and fearless concurrency are \
             important topics in systems programming. ".repeat(40)
        );
        g.add_node(&text, Some(&format!("LargeNode{}", i)), None);
    }

    // Add a modest number of edges so traversal has something to do
    for i in 0..node_count {
        let target = (i * 17 + 3) % node_count;
        if target != i {
            g.add_edge(
                grap::ids::NodeId(i as u32),
                grap::ids::NodeId(target as u32),
                0.7,
                vec![],
            );
        }
    }

    g
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

fn bench_rebalance_cost(c: &mut Criterion) {
    // Use a reasonably sized 4-shard cluster
    let sg = build_synthetic_sharded_graph(4, 4_000, 5);

    let mut group = c.benchmark_group("rebalance_cost");

    // Planning cost (should be relatively cheap)
    group.bench_function("plan_rebalance_4_to_6_shards", |b| {
        b.iter(|| {
            let plan = sg.plan_rebalance(6);
            black_box(plan.node_migrations.len())
        })
    });

    // Full execution cost (the expensive part)
    group.bench_function("execute_rebalance_4_to_6_shards", |b| {
        b.iter(|| {
            // We create a fresh copy each iteration because rebalance mutates the graph
            let mut fresh = build_synthetic_sharded_graph(4, 4_000, 5);
            let plan = fresh.plan_rebalance(6);
            let _ = fresh.execute_rebalance(plan);
            black_box(())
        })
    });

    group.finish();
}

/// Measures the cost of `search_and_traverse` under different text vs graph weighting.
/// This validates that BM25 seed selection + pruned traversal remains efficient
/// even when the text component is dominant.
fn bench_text_graph_fusion_cost(c: &mut Criterion) {
    let g = build_synthetic_graph(3_000, 5);

    let mut group = c.benchmark_group("text_graph_fusion");

    let query = "memory safety concurrency ownership";

    group.bench_function("text_heavy_fusion", |b| {
        b.iter(|| {
            let res = g.search_and_traverse(
                query,
                15,
                &["topic"],
                0.4,
                4,
                100,
                FusionWeights { text: 0.9, graph: 0.1 },
            );
            black_box(res.len())
        })
    });

    group.bench_function("balanced_fusion", |b| {
        b.iter(|| {
            let res = g.search_and_traverse(
                query,
                15,
                &["topic"],
                0.4,
                4,
                100,
                FusionWeights { text: 0.5, graph: 0.5 },
            );
            black_box(res.len())
        })
    });

    group.bench_function("graph_heavy_fusion", |b| {
        b.iter(|| {
            let res = g.search_and_traverse(
                query,
                15,
                &["topic"],
                0.4,
                4,
                100,
                FusionWeights { text: 0.1, graph: 0.9 },
            );
            black_box(res.len())
        })
    });

    group.finish();
}

/// Benchmarks mmap cold-cache vs hot-cache behavior vs plain load_from_disk.
/// This is only compiled when the `mmap` feature is enabled.
#[cfg(feature = "mmap")]
fn bench_mmap_cold_vs_hot(c: &mut Criterion) {
    use std::time::Duration;

    // Build a graph with decent-sized blobs
    let mut g = build_graph_with_large_blobs(1_500);

    let tmp = tempfile::tempdir().expect("failed to create tempdir for mmap bench");
    let dir = tmp.path().join("mmap_bench_graph");

    // Persist it
    g.save_to_dir(&dir).expect("save_to_dir failed in benchmark");

    let mut group = c.benchmark_group("mmap_cold_vs_hot");
    group.measurement_time(Duration::from_secs(8));

    // Cold mmap load (many page faults on first access)
    group.bench_function("mmap_cold_first_traversal", |b| {
        b.iter(|| {
            let loaded = IndexedKnowledgeGraph::load_mmap(&dir).expect("load_mmap failed");
            // First traversal after load — this is where cold cache cost appears
            let res = loaded.search_and_traverse(
                "memory safety",
                10,
                &[],
                0.3,
                3,
                50,
                FusionWeights::default(),
            );
            black_box(res.len())
        })
    });

    // Hot mmap (subsequent traversals on already-faulted pages)
    let loaded_mmap = IndexedKnowledgeGraph::load_mmap(&dir).expect("load_mmap failed");
    group.bench_function("mmap_hot_traversal", |b| {
        b.iter(|| {
            let res = loaded_mmap.search_and_traverse(
                "memory safety",
                10,
                &[],
                0.3,
                3,
                50,
                FusionWeights::default(),
            );
            black_box(res.len())
        })
    });

    // Traditional load_from_disk (everything deserialized into RAM)
    group.bench_function("load_from_disk_traversal", |b| {
        b.iter(|| {
            let loaded = IndexedKnowledgeGraph::load_from_dir(&dir).expect("load_from_dir failed");
            let res = loaded.search_and_traverse(
                "memory safety",
                10,
                &[],
                0.3,
                3,
                50,
                FusionWeights::default(),
            );
            black_box(res.len())
        })
    });

    group.finish();
}

#[cfg(feature = "mmap")]
criterion_group!(
    benches,
    bench_bitmask_pruning,
    bench_sharded_vs_monolithic,
    bench_rebalance_cost,
    bench_text_graph_fusion_cost,
    bench_mmap_cold_vs_hot
);

#[cfg(not(feature = "mmap"))]
criterion_group!(
    benches,
    bench_bitmask_pruning,
    bench_sharded_vs_monolithic,
    bench_rebalance_cost,
    bench_text_graph_fusion_cost
);

criterion_main!(benches);
