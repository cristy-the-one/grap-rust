#![cfg(feature = "ann-hnsw")]
use grap::{HNSWVectorIndex, NodeId, VectorIndex};

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
    assert_eq!(index.search(&[1.0, 0.0], 1), vec![(NodeId(1), 1.0)]);
    assert_eq!(index.search(&[f32::MAX, f32::MAX], 1)[0].0, NodeId(101));
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
    assert_eq!(loaded.search(&[1.0, 0.0], 1), vec![(NodeId(0), 1.0)]);
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
