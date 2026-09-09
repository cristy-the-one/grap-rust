#![cfg(feature = "ann-hnsw")]
use grap::{HNSWVectorIndex, NodeId, VectorIndex};

#[test]
fn approximate_recall_and_current_scores_survive_churn_and_reload() {
    let mut state = 20260909u64;
    let mut vector = || {
        (0..24)
            .map(|_| {
                state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
                ((state >> 32) as u32 as f64 / u32::MAX as f64 * 2.0 - 1.0) as f32
            })
            .collect::<Vec<_>>()
    };
    let mut ann = HNSWVectorIndex::new();
    let mut exact = grap::BruteForceVectorIndex::new();
    for id in 0..512 {
        let v = vector();
        ann.add_vector(NodeId(id), &v);
        exact.add_vector(NodeId(id), &v);
    }
    let queries: Vec<_> = (0..32).map(|_| vector()).collect();
    for stage in 0..3 {
        if stage == 1 {
            for id in 0..80 {
                if id % 2 == 0 {
                    ann.remove_vector(NodeId(id));
                    exact.remove_vector(NodeId(id));
                } else {
                    let v = vector();
                    ann.add_vector(NodeId(id), &v);
                    exact.add_vector(NodeId(id), &v);
                }
            }
        } else if stage == 2 {
            let dir = tempfile::tempdir().unwrap();
            ann.save_to_dir(dir.path()).unwrap();
            assert!(ann.try_load_from_dir(dir.path()).unwrap());
        }
        let mut recovered = 0;
        for query in &queries {
            let truth = exact.search(query, 1000);
            let hits = ann.search(query, 10);
            assert_eq!(hits.len(), 10);
            for &(id, score) in &hits {
                assert_eq!(truth.iter().find(|(n, _)| *n == id).unwrap().1, score);
            }
            recovered += truth[..10]
                .iter()
                .filter(|(id, _)| hits.iter().any(|(n, _)| n == id))
                .count();
        }
        // Broad regression guard, not a guarantee for arbitrary corpora. Upstream
        // hierarchy levels remain randomized despite the deterministic input.
        assert!(
            recovered as f64 / 320.0 >= 0.90,
            "stage {stage}: {recovered}/320"
        );
    }
}

#[test]
fn ann_updates_are_exactly_scored_and_physical_growth_is_bounded() {
    let mut index = HNSWVectorIndex::new();
    for i in 0..256 {
        index.add_vector(NodeId(i), &[0.0, 1.0, i as f32 + 1.0]);
    }
    // Exercise the ANN branch, not the small-index exact fallback.
    for _ in 0..1000 {
        index.add_vector(NodeId(0), &[1.0, 0.0, 0.0]);
    }
    assert_eq!(index.physical_vector_count(), 256);
    assert_eq!(index.search(&[1.0, 0.0, 0.0], 1), vec![(NodeId(0), 1.0)]);
    for i in 0..200 {
        index.remove_vector(NodeId(i));
    }
    assert!(index.physical_vector_count() <= index.stats().vector_count + 64);
    let results = index.search(&[1.0, 0.0, 0.0], 1000);
    assert_eq!(results.len(), 56);
    assert!(results.iter().all(|(id, _)| id.0 >= 200));
    index.vacuum();
    assert_eq!(index.physical_vector_count(), 56);
    assert_eq!(index.pending_change_count(), 0);
}

#[test]
fn invalid_inputs_preserve_values_and_zero_and_extreme_vectors_are_safe() {
    let mut index = HNSWVectorIndex::new();
    index.try_add_vector(NodeId(1), &[1.0, 0.0]).unwrap();
    for vector in [
        vec![],
        vec![1.0],
        vec![f32::NAN, 0.0],
        vec![f32::INFINITY, 0.0],
    ] {
        assert!(index.try_add_vector(NodeId(1), &vector).is_err());
    }
    for i in 2..100 {
        index.try_add_vector(NodeId(i), &[0.0, 1.0]).unwrap();
    }
    index.try_add_vector(NodeId(100), &[0.0, 0.0]).unwrap();
    index
        .try_add_vector(NodeId(101), &[f32::MAX, f32::MAX])
        .unwrap();
    for query in [[1.0, 0.0], [f32::MAX, f32::MAX]] {
        let truth = index.search(&query, 1000);
        let approximate = index.search(&query, 10);
        assert_eq!(approximate.len(), 10);
        for (id, score) in approximate {
            assert!(score.is_finite());
            assert_eq!(score, truth.iter().find(|(n, _)| *n == id).unwrap().1);
        }
    }
    assert_eq!(index.search(&[1.0, 0.0], 1000)[0], (NodeId(1), 1.0));
    assert_eq!(index.search(&[f32::MAX, f32::MAX], 1000)[0].0, NodeId(101));
    assert_eq!(index.search(&[0.0, 0.0], 1), vec![(NodeId(1), 0.0)]);
    assert!(index.search(&[f32::NAN, 1.0], 2).is_empty());
}

#[test]
fn corrupt_snapshots_fail_without_changing_the_live_index() {
    let dir = tempfile::tempdir().unwrap();
    let mut index = HNSWVectorIndex::new();
    index.add_vector(NodeId(3), &[1.0, 0.0]);
    index.save_to_dir(dir.path()).unwrap();
    let manifest_path = dir.path().join("manifest.json");
    let original: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&manifest_path).unwrap()).unwrap();
    for bad in [
        serde_json::json!(0),
        serde_json::json!(257),
        serde_json::json!("bad"),
    ] {
        let mut manifest = original.clone();
        manifest["hnsw_params"]["max_connections"] = bad;
        std::fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
        assert!(index.try_load_from_dir(dir.path()).is_err());
        assert_eq!(index.search(&[1.0, 0.0], 1), vec![(NodeId(3), 1.0)]);
    }
    std::fs::write(&manifest_path, serde_json::to_vec(&original).unwrap()).unwrap();
    std::fs::write(
        dir.path().join("vectors.bin"),
        bincode::serialize(&vec![(3u32, vec![f32::NAN, 0.0])]).unwrap(),
    )
    .unwrap();
    assert!(index.try_load_from_dir(dir.path()).is_err());
    assert!(index.contains_vector(NodeId(3)));
    std::fs::remove_file(dir.path().join("vectors.bin")).unwrap();
    assert!(index.try_load_from_dir(dir.path()).is_err());
    assert!(index.contains_vector(NodeId(3)));
}

#[test]
fn empty_and_mutated_snapshots_preserve_current_vectors() {
    let dir = tempfile::tempdir().unwrap();
    let mut index = HNSWVectorIndex::new();
    index.save_to_dir(dir.path()).unwrap();
    assert!(index.try_load_from_dir(dir.path()).unwrap());
    for i in 0..100 {
        index.add_vector(NodeId(i), &[0.0, 1.0]);
    }
    index.add_vector(NodeId(0), &[1.0, 0.0]);
    index.remove_vector(NodeId(1));
    index.save_to_dir(dir.path()).unwrap();
    let mut loaded = HNSWVectorIndex::new();
    assert!(loaded.try_load_from_dir(dir.path()).unwrap());
    assert_eq!(loaded.search(&[1.0, 0.0], 1000)[0], (NodeId(0), 1.0));
    assert!(!loaded.contains_vector(NodeId(1)));
    assert_eq!(loaded.stats().vector_count, 99);
}

#[test]
fn replacements_never_return_stale_vectors_or_duplicate_nodes() {
    let mut index = HNSWVectorIndex::new();
    index.add_vector(NodeId(1), &[1.0, 0.0]);
    index.add_vector(NodeId(2), &[0.8, 0.6]);
    for _ in 0..10 {
        index.remove_vector(NodeId(1));
        index.add_vector(NodeId(1), &[-1.0, 0.0]);
    }
    assert_eq!(index.search(&[1.0, 0.0], 1)[0].0, NodeId(2));
    let results = index.search(&[1.0, 0.0], 10);
    assert_eq!(results.len(), 2);
    assert_eq!(results[1], (NodeId(1), -1.0));
}

#[test]
fn cosine_scores_preserve_negative_similarity() {
    let mut index = HNSWVectorIndex::new();
    index.add_vector(NodeId(1), &[-1.0, 0.0]);
    assert_eq!(index.search(&[1.0, 0.0], 1), vec![(NodeId(1), -1.0)]);
}

#[test]
fn hnsw_trait_persistence_and_live_count_work_after_delete() {
    let mut index: Box<dyn VectorIndex> = Box::new(HNSWVectorIndex::new());
    index.add_vector(NodeId(0), &[1.0, 0.0]);
    index.add_vector(NodeId(1), &[0.0, 1.0]);
    index.remove_vector(NodeId(1));
    assert_eq!(index.stats().vector_count, 1);
    assert!(index.search(&[1.0, 0.0], 0).is_empty());
    let dir = tempfile::tempdir().unwrap();
    index.save_to_dir(dir.path()).unwrap();
    assert!(dir.path().join("vectors.bin").exists());
    let mut loaded: Box<dyn VectorIndex> = Box::new(HNSWVectorIndex::new());
    assert!(loaded.try_load_from_dir(dir.path()).unwrap());
    assert!(loaded.contains_vector(NodeId(0)));
    assert!(!loaded.contains_vector(NodeId(1)));
    assert_eq!(loaded.stats().vector_count, 1);
}
