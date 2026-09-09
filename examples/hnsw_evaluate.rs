//! Read-only recall/latency evaluation of a saved vectors.bin, with held-out
//! query vectors. Run with --features ann-hnsw; outputs JSON lines.
#[cfg(feature = "ann-hnsw")]
fn main() {
    use grap::{BruteForceVectorIndex, HNSWParams, HNSWVectorIndex, NodeId, VectorIndex};
    use sha2::{Digest, Sha256};
    use std::time::Instant;
    let path = std::env::args().nth(1).expect("supply vectors.bin path");
    let data = std::fs::read(&path).unwrap();
    let mut vectors: Vec<(u32, Vec<f32>)> = bincode::deserialize(&data).unwrap();
    vectors.sort_unstable_by_key(|(id, _)| *id);
    let stride = (vectors.len() / 64).max(2);
    let (queries, corpus): (Vec<_>, Vec<_>) = vectors
        .into_iter()
        .enumerate()
        .partition(|(i, _)| i % stride == 0);
    let queries: Vec<_> = queries.into_iter().map(|(_, (_, v))| v).collect();
    let corpus: Vec<_> = corpus.into_iter().map(|(_, v)| v).collect();
    println!(
        "{}",
        serde_json::json!({"source":path,"sha256":format!("{:x}",Sha256::digest(&data)),
        "vectors":corpus.len(),"dimension":corpus[0].1.len(),"held_out_queries":queries.len(),
        "k":10,"note":"Retrieval agreement with exact cosine; not legal or model accuracy. HNSW hierarchy randomness is upstream."})
    );
    for ef in [50, 150] {
        for trial in 0..2 {
            let mut exact = BruteForceVectorIndex::new();
            let mut ann = HNSWVectorIndex::with_params(HNSWParams {
                ef_search: ef,
                ..Default::default()
            });
            let start = Instant::now();
            for (id, v) in &corpus {
                ann.try_add_vector(NodeId(*id), v).unwrap();
            }
            let build_ms = start.elapsed().as_secs_f64() * 1000.0;
            for (id, v) in &corpus {
                exact.add_vector(NodeId(*id), v);
            }
            for stage in ["fresh", "mutated", "reloaded"] {
                let start = Instant::now();
                if stage == "mutated" {
                    for (i, (id, v)) in corpus.iter().enumerate() {
                        if i % 17 == 0 {
                            ann.remove_vector(NodeId(*id));
                            exact.remove_vector(NodeId(*id));
                        } else if i % 19 == 0 {
                            let mut replacement = v.clone();
                            replacement.rotate_left(1);
                            ann.try_add_vector(NodeId(*id), &replacement).unwrap();
                            exact.add_vector(NodeId(*id), &replacement);
                        }
                    }
                } else if stage == "reloaded" {
                    let dir = tempfile::tempdir().unwrap();
                    ann.save_to_dir(dir.path()).unwrap();
                    let mut loaded = HNSWVectorIndex::new();
                    assert!(loaded.try_load_from_dir(dir.path()).unwrap());
                    ann = loaded;
                }
                let maintenance_ms = start.elapsed().as_secs_f64() * 1000.0;
                let mut exact_us = Vec::new();
                let mut ann_us = Vec::new();
                let mut recalls = Vec::new();
                let mut threshold_hits = 0;
                for query in &queries {
                    let start = Instant::now();
                    let truth = exact.search(query, 10);
                    exact_us.push(start.elapsed().as_secs_f64() * 1e6);
                    let start = Instant::now();
                    let hits = ann.search(query, 10);
                    ann_us.push(start.elapsed().as_secs_f64() * 1e6);
                    assert_eq!(hits.len(), truth.len());
                    let ids: std::collections::HashSet<_> =
                        hits.iter().map(|(id, _)| *id).collect();
                    assert_eq!(ids.len(), hits.len());
                    assert!(hits
                        .iter()
                        .all(|(id, score)| exact.contains_vector(*id) && score.is_finite()));
                    recalls.push(
                        truth.iter().filter(|(id, _)| ids.contains(id)).count() as f64
                            / truth.len() as f64,
                    );
                    threshold_hits += hits
                        .iter()
                        .filter(|(_, score)| *score + 1e-7 >= truth.last().unwrap().1)
                        .count();
                }
                exact_us.sort_by(f64::total_cmp);
                ann_us.sort_by(f64::total_cmp);
                println!(
                    "{}",
                    serde_json::json!({"ef_search":ef,"trial":trial,"stage":stage,
                    "build_ms":build_ms,"maintenance_ms":maintenance_ms,
                    "mean_id_recall_at_10":recalls.iter().sum::<f64>()/recalls.len() as f64,
                    "worst_id_recall_at_10":recalls.iter().copied().fold(1.0,f64::min),
                    "score_threshold_recall_at_10":threshold_hits as f64/(queries.len()*10) as f64,
                    "exact_p50_us":exact_us[exact_us.len()/2],"exact_p95_us":exact_us[exact_us.len()*95/100],
                    "ann_p50_us":ann_us[ann_us.len()/2],"ann_p95_us":ann_us[ann_us.len()*95/100],
                    "live_vectors":ann.stats().vector_count,"physical_vectors":ann.physical_vector_count(),
                    "pending_changes":ann.pending_change_count()})
                );
            }
        }
    }
}

#[cfg(not(feature = "ann-hnsw"))]
fn main() {
    eprintln!("Enable --features ann-hnsw to run this evaluation.");
}
