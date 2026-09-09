//! Engine workloads independent of the Python/MCP harness and feature aliases.
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use grap::{InvertedIndex, KnowledgeGraph, NodeId, OnDiskTextIndex, TextIndex};

fn bounded_traversal(c: &mut Criterion) {
    let mut group = c.benchmark_group("bounded_traversal");
    for n in [1_000, 100_000, 500_000] {
        let mut graph = KnowledgeGraph::new();
        for _ in 0..n {
            graph.add_node("node", None, None);
        }
        // Identical 32-node reachable region in differently sized arenas.
        for id in 0..31 {
            graph.add_edge(NodeId(id), NodeId(id + 1), 0.99, vec![]);
        }
        group.bench_function(format!("chain32_arena{n}"), |b| {
            b.iter(|| {
                black_box(
                    graph
                        .find()
                        .from_node(NodeId(0))
                        .depth(32)
                        .limit(32)
                        .execute(),
                )
            })
        });
    }
    group.finish();
}

fn selective_disk_search(c: &mut Criterion) {
    let mut group = c.benchmark_group("selective_disk_search");
    for n in [1_000, 10_000, 50_000] {
        let mut index = OnDiskTextIndex::new();
        for id in 0..n {
            index.index_document(NodeId(id), &format!("term{id} shared vocabulary"));
        }
        group.bench_function(format!("one_hit_n{n}"), |b| {
            b.iter(|| {
                let hits = index.search(black_box("term42"), 10);
                assert_eq!(hits.len(), 1);
                black_box(hits)
            })
        });
    }
    group.finish();
}

fn replace_document(c: &mut Criterion) {
    let mut group = c.benchmark_group("replace_document");
    for n in [1_000, 10_000] {
        let mut index = InvertedIndex::new();
        for id in 0..n {
            index.index_document(NodeId(id), &format!("term{id} shared vocabulary"));
        }
        group.bench_function(format!("three_terms_n{n}"), |b| {
            b.iter(|| {
                index.index_document(NodeId(42), black_box("replacement shared vocabulary"));
                black_box(index.stats().document_count)
            })
        });
    }
    group.finish();
}

fn disk_common_ingestion(c: &mut Criterion) {
    let mut group = c.benchmark_group("disk_common_ingestion");
    for n in [1_000, 10_000] {
        group.bench_function(format!("documents{n}"), |b| {
            b.iter_batched(
                OnDiskTextIndex::new,
                |mut index| {
                    for id in 0..n {
                        index.index_document(NodeId(id), "credit contract termen credit");
                    }
                    black_box(index);
                },
                criterion::BatchSize::LargeInput,
            );
        });
    }
    group.finish();
}

criterion_group!(
    benches,
    bounded_traversal,
    selective_disk_search,
    replace_document,
    disk_common_ingestion
);
criterion_main!(benches);
