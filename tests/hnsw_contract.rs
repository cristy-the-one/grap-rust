#![cfg(feature = "ann-hnsw")]
use grap::{HNSWVectorIndex, NodeId, VectorIndex};

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
