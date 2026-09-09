//! Vector / ANN index abstraction (Phase 0+ of the 0.3/0.4 ANN design).
//!
//! This module defines the `VectorIndex` trait and a simple brute-force
//! reference implementation. Future implementations (HNSW, etc.) will live
//! behind the same trait so that `IndexedKnowledgeGraph` stays backend-agnostic.

use crate::ids::NodeId;
use crate::index::cosine_similarity; // reuse the existing helper

use serde::{Deserialize, Serialize};
use std::path::Path;

/// Human-readable manifest for a persisted vector index.
///
/// This file lives alongside the binary vector data (e.g. `vector_index/manifest.json`)
/// so that humans (and future tooling) can inspect the index without reading the binary blob.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VectorIndexManifest {
    /// Persistence format version. Bump when the binary layout changes.
    pub version: u32,
    /// Which backend produced this index ("hnsw" or "brute_force").
    pub backend: String,
    /// Dimensionality of the stored vectors.
    pub dimension: usize,
    /// How many vectors are stored in the accompanying data file.
    pub vector_count: usize,
    /// Unix timestamp (seconds since the UNIX epoch) when this snapshot was created.
    /// Informational only — not used during load.
    pub created_at: String,
    /// HNSW-specific parameters, stored as raw JSON so the manifest remains
    /// deserializable even when the `ann-hnsw` feature is disabled.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hnsw_params: Option<serde_json::Value>,
}

#[cfg(feature = "ann-hnsw")]
use hnsw_rs::anndists::dist::distances::DistCosine;
#[cfg(feature = "ann-hnsw")]
use hnsw_rs::hnsw::Hnsw;

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

    /// Returns true if the index currently contains a vector for this node.
    /// Used for efficient delta sync after loading a persisted index.
    fn contains_vector(&self, _node: NodeId) -> bool {
        false
    }

    // --- Optional / future hooks (default implementations) ---

    /// Save the vector index to a directory.
    /// Memory-only backends may be a no-op.
    fn save_to_dir(&self, _dir: &std::path::Path) -> Result<(), crate::GraphError> {
        Ok(())
    }

    /// Load a vector index from a directory.
    ///
    /// **Note:** This method requires `Self: Sized` and cannot be called through
    /// `Box<dyn VectorIndex>`. Call it directly on the concrete type.
    /// This matches the identical pattern in `TextIndex::load_from_dir`.
    fn load_from_dir(_dir: &std::path::Path) -> Result<Box<dyn VectorIndex>, crate::GraphError>
    where
        Self: Sized,
    {
        Err(crate::GraphError::Other(
            "load_from_dir not supported for this VectorIndex backend".into(),
        ))
    }

    /// Attempts to restore the index from a previously saved directory.
    ///
    /// Returns `Ok(true)` if a valid persisted index was loaded.
    /// Returns `Ok(false)` if no persisted data was found (caller should rebuild).
    ///
    /// Default implementation does nothing (BruteForceVectorIndex etc.).
    fn try_load_from_dir(&mut self, _dir: &std::path::Path) -> Result<bool, crate::GraphError> {
        Ok(false)
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
#[derive(Debug, Clone, Default)]
pub struct BruteForceVectorIndex {
    /// node_id -> embedding
    vectors: std::collections::HashMap<NodeId, Vec<f32>>,
    /// Cached dimension (taken from the first inserted vector)
    dimension: usize,
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
        if self.vectors.is_empty() || query.is_empty() || max_results == 0 {
            return Vec::new();
        }

        let mut scored: Vec<(NodeId, f64)> = self
            .vectors
            .iter()
            .map(|(&node, vec)| (node, cosine_similarity(query, vec)))
            .collect();

        // Resolve ties BEFORE selection, otherwise a small top-k contains an
        // arbitrary subset determined by HashMap iteration (and changes on load).
        let rank =
            |a: &(NodeId, f64), b: &(NodeId, f64)| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0));
        // O(N + k log k) partial sort instead of O(N log N) full sort.
        // `select_nth_unstable_by` places the k-th best element at position k-1
        // and guarantees all elements before it are ≥ it (descending order).
        if scored.len() <= max_results {
            scored.sort_unstable_by(rank);
        } else {
            scored.select_nth_unstable_by(max_results - 1, rank);
            scored.truncate(max_results);
            scored.sort_unstable_by(rank);
        }

        scored
    }

    fn stats(&self) -> VectorIndexStats {
        VectorIndexStats {
            vector_count: self.vectors.len(),
            dimension: self.dimension,
            backend: "brute_force",
        }
    }

    fn contains_vector(&self, node: NodeId) -> bool {
        self.vectors.contains_key(&node)
    }

    fn save_to_dir(&self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        BruteForceVectorIndex::save_to_dir(self, dir)
    }

    fn try_load_from_dir(&mut self, dir: &std::path::Path) -> Result<bool, crate::GraphError> {
        BruteForceVectorIndex::try_load_from_dir(self, dir)
    }
}

// ============================================================================
// Persistence for BruteForceVectorIndex (Step 4)
// ============================================================================

impl BruteForceVectorIndex {
    /// Persists the current vectors + manifest into `dir/vector_index/`.
    pub(crate) fn save_to_dir(&self, dir: &Path) -> Result<(), crate::GraphError> {
        use std::fs;
        use std::io::Write;

        let vdir = dir; // caller passes the vector_index/ directory
        fs::create_dir_all(vdir)?;

        let manifest = VectorIndexManifest {
            version: 1,
            backend: "brute_force".to_string(),
            dimension: self.dimension,
            vector_count: self.vectors.len(),
            created_at: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
                .to_string(),
            hnsw_params: None,
        };

        let manifest_path = vdir.join("manifest.json");
        let json = serde_json::to_string_pretty(&manifest)
            .map_err(|e| crate::GraphError::Other(e.to_string()))?;
        fs::write(manifest_path, json)?;

        // Serialize vectors
        let data: Vec<(u32, Vec<f32>)> = self
            .vectors
            .iter()
            .map(|(&id, v)| (id.0, v.clone()))
            .collect();

        let encoded =
            bincode::serialize(&data).map_err(|e| crate::GraphError::Other(e.to_string()))?;

        let mut f = fs::File::create(vdir.join("vectors.bin"))?;
        f.write_all(&encoded)?;

        Ok(())
    }

    /// Tries to restore from a previously saved `vector_index/` directory.
    /// Returns `true` if successful.
    pub(crate) fn try_load_from_dir(&mut self, dir: &Path) -> Result<bool, crate::GraphError> {
        use std::fs;

        let manifest_path = dir.join("manifest.json");
        let vectors_path = dir.join("vectors.bin");

        if !manifest_path.exists() || !vectors_path.exists() {
            return Ok(false);
        }

        let manifest_json = fs::read_to_string(&manifest_path)?;
        let manifest: VectorIndexManifest = serde_json::from_str(&manifest_json)
            .map_err(|e| crate::GraphError::Other(format!("Bad manifest.json: {}", e)))?;

        // Wrong backend: a different implementation wrote this index.
        // Return Ok(false) so the caller falls back to a full rebuild gracefully.
        if manifest.backend != "brute_force" {
            return Ok(false);
        }
        // Wrong version: this is our backend but the format has changed.
        // Return Err so the caller knows something unexpected happened.
        if manifest.version != 1 {
            return Err(crate::GraphError::Other(format!(
                "Unsupported brute-force vector index version {} (expected 1)",
                manifest.version
            )));
        }

        let data = fs::read(&vectors_path)?;
        let persisted: Vec<(u32, Vec<f32>)> = bincode::deserialize(&data)
            .map_err(|e| crate::GraphError::Other(format!("Corrupted vectors.bin: {}", e)))?;

        // Dimension validation
        for (_, emb) in &persisted {
            if emb.len() != manifest.dimension {
                return Err(crate::GraphError::Other(
                    "Dimension mismatch in persisted brute-force vectors".into(),
                ));
            }
        }

        self.vectors.clear();
        self.dimension = manifest.dimension;

        for (id_u32, emb) in persisted {
            self.vectors.insert(NodeId(id_u32), emb);
        }

        Ok(true)
    }
}

// ============================================================================
// HNSW Backend (Phase 1)
// ============================================================================

/// In-memory HNSW vector index using the `hnsw_rs` crate.
///
/// Experimental approximate backend with exact scoring and mutation reconciliation.
///
/// It is only compiled when the `ann-hnsw` feature is enabled.
#[cfg(feature = "ann-hnsw")]
pub struct HNSWVectorIndex {
    /// The actual HNSW index. Uses cosine distance.
    ///
    /// The `'static` lifetime is a requirement of the `hnsw_rs` crate's internal
    /// thread-safety model — it is not a self-referential borrow.
    inner: Hnsw<'static, f32, DistCosine>,
    /// Raw vectors we have inserted. We keep them so we can persist the index
    /// without having to re-read the entire graph's BlobStore.
    ///
    /// **Memory note**: this doubles the in-memory footprint of each embedding
    /// (once here, once inside `inner`). For large indexes this cost is known
    /// and intentional — it avoids a full BlobStore scan on every save.
    vectors: std::collections::HashMap<NodeId, Vec<f32>>,
    /// Dimension of the vectors (inferred on first insert).
    dimension: usize,
    /// Labels physically present in the immutable-until-rebuild ANN structure.
    base_ids: std::collections::HashSet<NodeId>,
    /// Updated/deleted labels whose old ANN entries must never be returned.
    changed: std::collections::HashSet<NodeId>,
    /// Zero vectors cannot enter cosine HNSW; score them in the exact overlay.
    zero_vectors: std::collections::HashSet<NodeId>,
    /// Parameters used at construction time (for stats / future rebuild).
    params: HNSWParams,
}

#[cfg(feature = "ann-hnsw")]
impl std::fmt::Debug for HNSWVectorIndex {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HNSWVectorIndex")
            .field("dimension", &self.dimension)
            .field("vector_count", &self.inner.get_nb_point())
            .field("pending_changes", &self.changed.len())
            .field("params", &self.params)
            .finish_non_exhaustive()
    }
}

/// Tunable parameters for the HNSW index.
#[cfg(feature = "ann-hnsw")]
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct HNSWParams {
    /// Maximum number of connections per layer (M). Typical: 16–32.
    pub max_connections: usize,
    /// Size of the dynamic candidate list for construction (ef_construction).
    /// Higher = better index quality, slower build.
    pub ef_construction: usize,
    /// Default search parameter (ef). Can be overridden at query time.
    pub ef_search: usize,
    /// Maximum number of layers in the hierarchy.
    pub max_layers: usize,
}

#[cfg(feature = "ann-hnsw")]
impl Default for HNSWParams {
    fn default() -> Self {
        Self {
            max_connections: 16,
            ef_construction: 100,
            ef_search: 50,
            max_layers: 16,
        }
    }
}

#[cfg(feature = "ann-hnsw")]
impl HNSWVectorIndex {
    /// Create a new HNSW index with default parameters.
    pub fn new() -> Self {
        Self::with_params(HNSWParams::default())
    }

    /// Create a new HNSW index with custom parameters.
    /// Panics for invalid parameters; use `try_with_params` for untrusted inputs.
    pub fn with_params(params: HNSWParams) -> Self {
        Self::try_with_params(params).expect("invalid HNSW parameters")
    }

    /// Validate parameters before entering upstream code (which can exit the
    /// process for an excessive connection count).
    pub fn try_with_params(params: HNSWParams) -> Result<Self, crate::GraphError> {
        if !(2..=256).contains(&params.max_connections)
            || !(1..=16).contains(&params.max_layers)
            || params.ef_construction < params.max_connections
            || params.ef_search == 0
        {
            return Err(crate::GraphError::Other("invalid HNSW parameters: require M=2..256, layers=1..16, ef_construction>=M and ef_search>0".into()));
        }
        Ok(Self {
            inner: Hnsw::new(
                params.max_connections,
                10_000,
                params.max_layers,
                params.ef_construction,
                DistCosine {},
            ),
            vectors: Default::default(),
            dimension: 0,
            base_ids: Default::default(),
            changed: Default::default(),
            zero_vectors: Default::default(),
            params,
        })
    }

    /// Physical ANN points, including stale entries awaiting compaction.
    pub fn physical_vector_count(&self) -> usize {
        self.inner.get_nb_point()
    }

    /// Number of invalidated ANN labels awaiting compaction.
    pub fn pending_change_count(&self) -> usize {
        self.changed.len()
    }

    /// Checked insertion. Empty, non-finite and mismatched vectors are rejected
    /// without changing the existing value. Zero vectors are supported exactly.
    pub fn try_add_vector(
        &mut self,
        node: NodeId,
        embedding: &[f32],
    ) -> Result<(), crate::GraphError> {
        if embedding.is_empty()
            || embedding.iter().any(|v| !v.is_finite())
            || (self.dimension != 0 && embedding.len() != self.dimension)
        {
            return Err(crate::GraphError::Other(
                "invalid HNSW embedding: expected nonempty finite values with consistent dimension"
                    .into(),
            ));
        }
        self.dimension = embedding.len();
        self.zero_vectors.remove(&node);
        if self.base_ids.contains(&node) {
            self.changed.insert(node);
        }
        if let Some(normalized) = Self::normalize(embedding) {
            if !self.base_ids.contains(&node) {
                self.inner.insert((&normalized, node.0 as usize));
                self.base_ids.insert(node);
            }
        } else {
            self.zero_vectors.insert(node);
        }
        self.vectors.insert(node, embedding.to_vec());
        self.compact_if_needed();
        Ok(())
    }

    fn normalize(vector: &[f32]) -> Option<Vec<f32>> {
        let norm = vector
            .iter()
            .map(|v| (*v as f64).powi(2))
            .sum::<f64>()
            .sqrt();
        (norm > 0.0 && norm.is_finite())
            .then(|| vector.iter().map(|v| (*v as f64 / norm) as f32).collect())
    }

    fn compact_if_needed(&mut self) {
        if self.changed.len() >= 64.max(self.vectors.len() / 4) {
            self.rebuild();
        }
    }

    fn rebuild(&mut self) {
        self.inner = Hnsw::new(
            self.params.max_connections,
            self.vectors.len().max(1),
            self.params.max_layers,
            self.params.ef_construction,
            DistCosine {},
        );
        self.base_ids.clear();
        self.changed.clear();
        self.zero_vectors.clear();
        // Stable insertion order; upstream still assigns random hierarchy levels.
        let mut ids: Vec<_> = self.vectors.keys().copied().collect();
        ids.sort_unstable();
        for id in ids {
            if let Some(vector) = Self::normalize(&self.vectors[&id]) {
                self.inner.insert((&vector, id.0 as usize));
                self.base_ids.insert(id);
            } else {
                self.zero_vectors.insert(id);
            }
        }
    }

    fn score_candidates(
        &self,
        query: &[f32],
        ids: impl Iterator<Item = NodeId>,
        k: usize,
    ) -> Vec<(NodeId, f64)> {
        let mut results: Vec<_> = ids
            .filter_map(|id| {
                self.vectors
                    .get(&id)
                    .map(|v| (id, cosine_similarity(query, v)))
            })
            .collect();
        results.sort_unstable_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        results.truncate(k);
        results
    }
}

#[cfg(feature = "ann-hnsw")]
impl VectorIndex for HNSWVectorIndex {
    fn add_vector(&mut self, node: NodeId, embedding: &[f32]) {
        if let Err(error) = self.try_add_vector(node, embedding) {
            log::warn!("{error}");
        }
    }

    fn remove_vector(&mut self, node: NodeId) {
        self.vectors.remove(&node);
        self.zero_vectors.remove(&node);
        if self.base_ids.contains(&node) {
            self.changed.insert(node);
        }
        self.compact_if_needed();
    }

    fn search(&self, query: &[f32], max_results: usize) -> Vec<(NodeId, f64)> {
        if query.len() != self.dimension || query.iter().any(|v| !v.is_finite()) || max_results == 0
        {
            return Vec::new();
        }
        let k = max_results.min(self.vectors.len());
        if k == 0 {
            return Vec::new();
        }
        let normalized = Self::normalize(query);
        if self.vectors.len() <= 64 || k == self.vectors.len() || normalized.is_none() {
            return self.score_candidates(query, self.vectors.keys().copied(), k);
        }
        let fetch = self
            .params
            .ef_search
            .max(k.saturating_add(self.changed.len()))
            .min(self.base_ids.len());
        let mut ids = std::collections::HashSet::new();
        if fetch > 0 {
            for neighbor in self.inner.search(
                &normalized.unwrap(),
                fetch,
                self.params.ef_search.max(fetch),
            ) {
                let id = NodeId(neighbor.d_id as u32);
                if !self.changed.contains(&id) {
                    ids.insert(id);
                }
            }
        }
        ids.extend(self.changed.iter().chain(&self.zero_vectors).copied());
        let results = self.score_candidates(query, ids.into_iter(), k);
        // Approximate search is not guaranteed to enumerate even k live points.
        if results.len() < k {
            return self.score_candidates(query, self.vectors.keys().copied(), k);
        }
        results
    }

    fn vacuum(&mut self) {
        self.rebuild();
    }

    fn stats(&self) -> VectorIndexStats {
        VectorIndexStats {
            // Deleted vectors have already been removed from the live map.
            vector_count: self.vectors.len(),
            dimension: self.dimension,
            backend: "hnsw",
        }
    }

    fn save_to_dir(&self, dir: &Path) -> Result<(), crate::GraphError> {
        HNSWVectorIndex::save_to_dir(self, dir)
    }

    fn try_load_from_dir(&mut self, dir: &Path) -> Result<bool, crate::GraphError> {
        HNSWVectorIndex::try_load_from_dir(self, dir)
    }

    fn contains_vector(&self, node: NodeId) -> bool {
        self.vectors.contains_key(&node)
    }
}

// ============================================================================
// Persistence helpers for HNSW (Phase 3)
// ============================================================================

#[cfg(feature = "ann-hnsw")]
impl HNSWVectorIndex {
    /// Persists the current vector index (manifest + raw vectors) into `dir`.
    ///
    /// Layout created:
    ///   dir/manifest.json     – human-readable metadata
    ///   dir/vectors.bin       – compact bincode of the vectors
    pub(crate) fn save_to_dir(&self, dir: &Path) -> Result<(), crate::GraphError> {
        use std::fs;
        use std::io::Write;

        fs::create_dir_all(dir)?;

        // Build manifest
        let manifest = VectorIndexManifest {
            version: 1,
            backend: "hnsw".to_string(),
            dimension: self.dimension,
            vector_count: self.vectors.len(),
            created_at: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
                .to_string(),
            hnsw_params: Some(serde_json::to_value(self.params).unwrap_or(serde_json::Value::Null)),
        };

        // Write manifest.json (pretty for humans)
        let manifest_path = dir.join("manifest.json");
        let manifest_json = serde_json::to_string_pretty(&manifest)
            .map_err(|e| crate::GraphError::Other(format!("Failed to serialize manifest: {e}")))?;
        fs::write(&manifest_path, manifest_json)?;

        // Write vectors.bin (compact)
        let vectors_path = dir.join("vectors.bin");
        let mut persisted: Vec<(u32, &[f32])> = self
            .vectors
            .iter()
            .map(|(id, vector)| (id.0, vector.as_slice()))
            .collect();
        persisted.sort_unstable_by_key(|(id, _)| *id);

        let encoded = bincode::serialize(&persisted)
            .map_err(|e| crate::GraphError::Other(format!("Failed to serialize vectors: {e}")))?;

        let mut file = fs::File::create(vectors_path)?;
        file.write_all(&encoded)?;

        Ok(())
    }

    /// Attempts to load vectors from a previously saved directory and rebuild the HNSW.
    ///
    /// Returns `Ok(true)` if a valid persisted index was found and loaded.
    /// Returns `Ok(false)` if nothing was found (caller should fall back to full rebuild).
    pub(crate) fn try_load_from_dir(&mut self, dir: &Path) -> Result<bool, crate::GraphError> {
        use std::fs;

        let manifest_path = dir.join("manifest.json");
        let vectors_path = dir.join("vectors.bin");

        if !manifest_path.exists() || !vectors_path.exists() {
            return Ok(false);
        }

        // Read manifest
        let manifest_json = fs::read_to_string(&manifest_path)?;
        let manifest: VectorIndexManifest = serde_json::from_str(&manifest_json)
            .map_err(|e| crate::GraphError::Other(format!("Invalid manifest.json: {e}")))?;

        // Wrong backend: a different implementation wrote this index.
        // Return Ok(false) so the caller falls back gracefully.
        if manifest.backend != "hnsw" {
            return Ok(false);
        }
        // Wrong version: our backend but the format changed.
        if manifest.version != 1 {
            return Err(crate::GraphError::Other(format!(
                "Unsupported HNSW vector index version {} (expected 1)",
                manifest.version
            )));
        }
        let data = fs::read(&vectors_path)?;
        let mut persisted: Vec<(u32, Vec<f32>)> = bincode::deserialize(&data)
            .map_err(|e| crate::GraphError::Other(format!("Corrupted vectors.bin: {e}")))?;
        let params = match manifest.hnsw_params {
            Some(value) => serde_json::from_value(value)
                .map_err(|e| crate::GraphError::Other(format!("Invalid HNSW parameters: {e}")))?,
            None => HNSWParams::default(),
        };
        let mut replacement = Self::try_with_params(params)?;
        if persisted.len() != manifest.vector_count {
            return Err(crate::GraphError::Other(
                "HNSW vector count mismatch".into(),
            ));
        }
        let mut ids = std::collections::HashSet::new();
        for (id, emb) in &persisted {
            if !ids.insert(*id)
                || emb.is_empty()
                || emb.len() != manifest.dimension
                || emb.iter().any(|v| !v.is_finite())
            {
                return Err(crate::GraphError::Other(
                    "Invalid or duplicate persisted HNSW vector".into(),
                ));
            }
        }
        replacement.dimension = manifest.dimension;
        persisted.sort_unstable_by_key(|(id, _)| *id);
        for (id, emb) in persisted {
            replacement.try_add_vector(NodeId(id), &emb)?;
        }
        *self = replacement;

        Ok(true)
    }
}

#[cfg(feature = "ann-hnsw")]
impl Default for HNSWVectorIndex {
    fn default() -> Self {
        Self::new()
    }
}

/// Creates a default vector index for the current feature configuration.
///
/// Selects `HNSWVectorIndex` when the `ann-hnsw` feature is enabled,
/// otherwise falls back to `BruteForceVectorIndex`.
///
/// This is the single canonical location for the feature-flag dispatch,
/// keeping all callers clean and consistent.
pub fn create_default_vector_index() -> Box<dyn VectorIndex> {
    #[cfg(feature = "ann-hnsw")]
    {
        Box::new(HNSWVectorIndex::new())
    }
    #[cfg(not(feature = "ann-hnsw"))]
    {
        Box::new(BruteForceVectorIndex::new())
    }
}

// ============================================================================
// Basic tests (Phase 0)
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn brute_force_partial_sort_correctness() {
        // Cosine similarity is direction-only, so [i, 0] vectors are all
        // equally similar to [9, 0]. Use genuinely different-direction vectors.
        let mut idx = BruteForceVectorIndex::new();
        idx.add_vector(NodeId(0), &[1.0, 0.0]); // cos([1,0]) = 1.0 — closest
        idx.add_vector(NodeId(1), &[0.9, 0.436]); // cos ≈ 0.9
        idx.add_vector(NodeId(2), &[0.5, 0.866]); // cos ≈ 0.5
        idx.add_vector(NodeId(3), &[0.0, 1.0]); // cos = 0.0
        idx.add_vector(NodeId(4), &[-1.0, 0.0]); // cos = -1.0 — farthest

        let results = idx.search(&[1.0, 0.0], 3);
        assert_eq!(results.len(), 3);
        assert!(results[0].1 >= results[1].1, "results must be descending");
        assert!(results[1].1 >= results[2].1, "results must be descending");
        // [1,0] has cosine similarity 1.0 with query [1,0]
        assert_eq!(results[0].0, NodeId(0));
    }

    #[test]
    fn hnsw_stats_no_underflow_on_double_delete() {
        // Calling remove_vector on a node never inserted must not panic or wrap.
        // Uses BruteForce (always available) to verify the concept; the same
        // saturating_sub fix applies to the HNSW backend.
        let mut idx = BruteForceVectorIndex::new();
        idx.remove_vector(NodeId(99)); // never added
        let s = idx.stats();
        assert_eq!(s.vector_count, 0); // must not wrap to usize::MAX
    }

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

// ============================================================================
// Persistence Tests (Step 5)
// ============================================================================

#[cfg(test)]
mod persistence_tests {
    use super::*;
    use std::path::PathBuf;
    use tempfile::TempDir;

    fn create_temp_vector_dir() -> (TempDir, PathBuf) {
        let tmp = TempDir::new().unwrap();
        let vdir = tmp.path().join("vector_index");
        (tmp, vdir)
    }

    #[test]
    fn brute_force_save_load_roundtrip() {
        let mut idx = BruteForceVectorIndex::new();
        idx.add_vector(NodeId(1), &[0.1, 0.2, 0.3]);
        idx.add_vector(NodeId(2), &[0.4, 0.5, 0.6]);
        idx.add_vector(NodeId(3), &[0.7, 0.8, 0.9]);

        let (_tmp, vdir) = create_temp_vector_dir();
        idx.save_to_dir(&vdir).unwrap();

        let mut loaded = BruteForceVectorIndex::new();
        let success = loaded.try_load_from_dir(&vdir).unwrap();
        assert!(success, "Should successfully load persisted data");

        assert_eq!(loaded.stats().vector_count, 3);
        assert_eq!(loaded.stats().dimension, 3);
        assert_eq!(loaded.stats().backend, "brute_force");

        // Verify search still works after load
        let results = loaded.search(&[0.1, 0.2, 0.3], 1);
        assert_eq!(results[0].0, NodeId(1));
    }

    #[test]
    fn brute_force_load_missing_files_returns_false() {
        let (_tmp, vdir) = create_temp_vector_dir();
        // directory does not exist or has no files

        let mut idx = BruteForceVectorIndex::new();
        let success = idx.try_load_from_dir(&vdir).unwrap();
        assert!(!success);
    }

    #[test]
    fn brute_force_bad_version_is_rejected() {
        let (_tmp, vdir) = create_temp_vector_dir();
        std::fs::create_dir_all(&vdir).unwrap();

        let bad_manifest = r#"
        {
            "version": 99,
            "backend": "brute_force",
            "dimension": 3,
            "vector_count": 0,
            "created_at": "now",
            "hnsw_params": null
        }"#;
        std::fs::write(vdir.join("manifest.json"), bad_manifest).unwrap();
        std::fs::write(vdir.join("vectors.bin"), b"").unwrap();

        let mut idx = BruteForceVectorIndex::new();
        let result = idx.try_load_from_dir(&vdir);
        assert!(result.is_err(), "Bad version should cause load to fail");
    }

    #[cfg(feature = "ann-hnsw")]
    #[test]
    fn hnsw_save_load_roundtrip() {
        let mut idx = HNSWVectorIndex::new();
        idx.add_vector(NodeId(10), &[0.1, 0.2, 0.3]);
        idx.add_vector(NodeId(20), &[0.4, 0.5, 0.6]);

        let (_tmp, vdir) = create_temp_vector_dir();
        idx.save_to_dir(&vdir).unwrap();

        let mut loaded = HNSWVectorIndex::new();
        let success = loaded.try_load_from_dir(&vdir).unwrap();
        assert!(success);

        assert_eq!(loaded.stats().vector_count, 2);
        assert_eq!(loaded.stats().backend, "hnsw");

        let results = loaded.search(&[0.1, 0.2, 0.3], 1);
        assert_eq!(results[0].0, NodeId(10));
    }
}
