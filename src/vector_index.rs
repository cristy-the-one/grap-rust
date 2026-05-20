//! Vector / ANN index abstraction (Phase 0+ of the 0.3/0.4 ANN design).
//!
//! This module defines the `VectorIndex` trait and a simple brute-force
//! reference implementation. Future implementations (HNSW, etc.) will live
//! behind the same trait so that `IndexedKnowledgeGraph` stays backend-agnostic.

use crate::ids::NodeId;
use crate::index::cosine_similarity; // reuse the existing helper

/// Statistics about a vector index.
#[derive(Debug, Clone, Default)]
pub struct VectorIndexStats {
    /// Number of vectors currently stored in the index.
    pub vector_count: usize,
    /// Dimensionality of the vectors (0 if empty or unknown).
    pub dimension: usize,
    /// Optional name of the backend (e.g. "brute_force", "hnsw").
    pub backend: &'static str,
}

/// Common contract for vector / ANN index backends.
///
/// This is the vector analogue of the `TextIndex` trait.
/// All implementations (brute-force reference, HNSW, future quantized or
/// persistent indexes) must implement this trait.
pub trait VectorIndex: Send + Sync + std::any::Any {
    /// Add or replace the embedding for a node.
    fn add_vector(&mut self, node: NodeId, embedding: &[f32]);

    /// Remove the vector for a node (if present). This is a logical removal.
    /// Implementations may choose to physically delete or just mark as deleted.
    fn remove_vector(&mut self, node: NodeId);

    /// Perform approximate (or exact) nearest neighbor search.
    ///
    /// Returns up to `max_results` pairs of `(NodeId, similarity)` where
    /// similarity is typically cosine similarity in [0.0, 1.0] or [-1.0, 1.0]
    /// depending on the normalization used by the implementation.
    fn search(&self, query: &[f32], max_results: usize) -> Vec<(NodeId, f64)>;

    /// Return diagnostic statistics about the index.
    fn stats(&self) -> VectorIndexStats;

    // --- Optional / future hooks (default implementations) ---

    /// Save the vector index to a directory.
    /// Memory-only backends may be a no-op.
    fn save_to_dir(&mut self, _dir: &std::path::Path) -> Result<(), crate::GraphError> {
        Ok(())
    }

    /// Load a vector index from a directory.
    fn load_from_dir(_dir: &std::path::Path) -> Result<Box<dyn VectorIndex>, crate::GraphError>
    where
        Self: Sized,
    {
        Err(crate::GraphError::Other(
            "load_from_dir not supported for this VectorIndex backend".into(),
        ))
    }

    /// Trigger any compaction / cleanup the backend supports (no-op by default).
    fn vacuum(&mut self) {}
}

/// A trivial exact (brute-force) vector index.
///
/// This is intended as:
/// - A reference implementation for testing
/// - A safe fallback when no ANN index is configured
/// - The initial implementation while we bring up HNSW
#[derive(Debug, Clone)]
pub struct BruteForceVectorIndex {
    /// node_id -> embedding
    vectors: std::collections::HashMap<NodeId, Vec<f32>>,
    /// Cached dimension (taken from the first inserted vector)
    dimension: usize,
}

impl Default for BruteForceVectorIndex {
    fn default() -> Self {
        Self {
            vectors: std::collections::HashMap::new(),
            dimension: 0,
        }
    }
}

impl BruteForceVectorIndex {
    /// Creates a new empty brute-force vector index.
    pub fn new() -> Self {
        Self::default()
    }
}

impl VectorIndex for BruteForceVectorIndex {
    fn add_vector(&mut self, node: NodeId, embedding: &[f32]) {
        if self.dimension == 0 && !embedding.is_empty() {
            self.dimension = embedding.len();
        }
        self.vectors.insert(node, embedding.to_vec());
    }

    fn remove_vector(&mut self, node: NodeId) {
        self.vectors.remove(&node);
    }

    fn search(&self, query: &[f32], max_results: usize) -> Vec<(NodeId, f64)> {
        if self.vectors.is_empty() || query.is_empty() {
            return Vec::new();
        }

        let mut scored: Vec<(NodeId, f64)> = self
            .vectors
            .iter()
            .map(|(&node, vec)| {
                let sim = cosine_similarity(query, vec);
                (node, sim)
            })
            .collect();

        // Sort by similarity descending
        scored.sort_by(|a, b| {
            b.1.partial_cmp(&a.1)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        scored.truncate(max_results);
        scored
    }

    fn stats(&self) -> VectorIndexStats {
        VectorIndexStats {
            vector_count: self.vectors.len(),
            dimension: self.dimension,
            backend: "brute_force",
        }
    }
}

// ============================================================================
// Basic tests (Phase 0)
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn brute_force_basic_search() {
        let mut idx = BruteForceVectorIndex::new();

        // 2D vectors for easy verification
        idx.add_vector(NodeId(1), &[1.0, 0.0]);
        idx.add_vector(NodeId(2), &[0.0, 1.0]);
        idx.add_vector(NodeId(3), &[0.9, 0.1]);

        let results = idx.search(&[1.0, 0.0], 2);

        assert_eq!(results.len(), 2);
        assert_eq!(results[0].0, NodeId(1)); // closest
        assert!(results[0].1 > 0.99);
    }

    #[test]
    fn brute_force_remove() {
        let mut idx = BruteForceVectorIndex::new();
        idx.add_vector(NodeId(42), &[0.5, 0.5]);
        assert_eq!(idx.stats().vector_count, 1);

        idx.remove_vector(NodeId(42));
        assert_eq!(idx.stats().vector_count, 0);
    }
}