//! On-Disk Text Index Adapter
//!
//! [`OnDiskTextIndex`] is the adapter that implements [`crate::TextIndex`] on top
//! of the real segmented on-disk engine in [`crate::on_disk_index`].
//!
//! It handles:
//! - Tokenization and `NodeId` ↔ global doc id mapping (persisted to `node_map.json`)
//! - Search / phrase search delegation
//! - Persistence (`save_to_dir` / `load_from_dir`)
//! - Incremental `vacuum` / `compact`
//! - Segment information for rebalancing and monitoring
//! - Background merge/vacuum workers
//!
//! Most low-level segment, WAL, and merge logic lives in the `on_disk_index` module.
//! End users normally interact with this type (or via the higher-level
//! `IndexedKnowledgeGraph::new_on_disk` and `start_background_workers` APIs).
//!
//! This module (and the engine) is always compiled — the on-disk index is a
//! first-class, supported backend.

use crate::ids::NodeId;
use crate::index::{DefaultTokenizer, IndexStats, TextIndex, Tokenizer};
use crate::on_disk_index::OnDiskIndex;

/// Adapter that makes the on-disk engine usable through the `TextIndex` trait.
///
/// See the module-level documentation for design rationale.
pub struct OnDiskTextIndex {
    /// The engine is behind an Arc<Mutex<>> so that background merge/vacuum
    /// workers can safely hold a reference to it.
    engine: std::sync::Arc<std::sync::Mutex<OnDiskIndex>>,
    tokenizer: Box<dyn Tokenizer>,
    /// Maps NodeId.0 → global doc id returned by the engine.
    /// Persisted to `node_map.json` on save, restored on load.
    node_to_global: std::collections::HashMap<u32, u32>,
    // Maintain the reverse lookup on writes/loads, not once per search.
    global_to_node: std::collections::HashMap<u32, u32>,
}

impl std::fmt::Debug for OnDiskTextIndex {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OnDiskTextIndex")
            .field("indexed_nodes", &self.node_to_global.len())
            .finish_non_exhaustive()
    }
}

impl OnDiskTextIndex {
    /// Creates a new in-memory `OnDiskTextIndex` with the default tokenizer.
    pub fn new() -> Self {
        Self {
            engine: std::sync::Arc::new(std::sync::Mutex::new(OnDiskIndex::new())),
            tokenizer: Box::new(DefaultTokenizer::new()),
            node_to_global: std::collections::HashMap::new(),
            global_to_node: std::collections::HashMap::new(),
        }
    }

    /// Create an on-disk text index with a tiered merge policy enabled.
    /// `segments_per_tier` controls how aggressive the background merging is
    /// (typical good values: 3, 4, or 5).
    pub fn new_with_merge_policy(segments_per_tier: usize) -> Self {
        let policy = crate::on_disk_index::TieredMergePolicy { segments_per_tier };
        let engine = crate::on_disk_index::OnDiskIndex::with_merge_policy(policy);
        Self {
            engine: std::sync::Arc::new(std::sync::Mutex::new(engine)),
            tokenizer: Box::new(DefaultTokenizer::new()),
            node_to_global: std::collections::HashMap::new(),
            global_to_node: std::collections::HashMap::new(),
        }
    }

    /// Create a new in-memory `OnDiskTextIndex` and ensure `dir` exists on disk.
    ///
    /// **Note:** This method does *not* associate the index with the directory —
    /// it is only a convenience for callers that want to pre-create the target
    /// directory before calling [`save_to_dir`]. The index data is kept in memory
    /// until an explicit call to [`save_to_dir`] is made.
    ///
    /// To load an existing index from disk, use [`TextIndex::load_from_dir`] instead.
    pub fn new_in_dir(dir: &std::path::Path) -> std::io::Result<Self> {
        std::fs::create_dir_all(dir)?;
        Ok(Self::new())
    }

    // Public inherent API (so callers can do idx.vacuum() directly on the concrete type)

    /// Runs an incremental vacuum, rewriting segments whose delete ratio exceeds the threshold.
    pub fn vacuum(&mut self) {
        self.engine.lock().unwrap().vacuum();
    }

    /// Merges all sealed segments into a single segment (full compaction).
    pub fn compact(&mut self) {
        self.engine.lock().unwrap().compact();
    }

    /// Removes and returns the sealed segment at `index` together with its tier.
    ///
    /// Returns `None` if `index` is out of range.
    pub fn remove_sealed_segment(
        &mut self,
        index: usize,
    ) -> Option<(crate::on_disk_index::OnDiskSegment, u32)> {
        self.engine.lock().unwrap().remove_sealed_segment(index)
    }

    /// Inserts an externally-produced sealed segment at the given tier.
    pub fn add_sealed_segment(&mut self, seg: crate::on_disk_index::OnDiskSegment, tier: u32) {
        // Do NOT clear node_to_global — existing mappings remain valid (C4 fix).
        self.engine.lock().unwrap().add_sealed_segment(seg, tier);
    }

    /// Persists the index (engine state + node map) to `dir`.
    ///
    /// Delegates to the `TextIndex::save_to_dir` implementation.
    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        TextIndex::save_to_dir(self, dir)
    }

    /// Start a background merge + incremental vacuum worker for this on-disk index.
    /// Keep the returned handle alive for as long as you want the worker running.
    pub fn start_merge_worker(
        &self,
        interval: std::time::Duration,
    ) -> crate::on_disk_index::MergeWorkerHandle {
        crate::on_disk_index::start_merge_worker(self.engine.clone(), interval)
    }
}

impl Default for OnDiskTextIndex {
    fn default() -> Self {
        Self::new()
    }
}

impl TextIndex for OnDiskTextIndex {
    fn index_document(&mut self, node: NodeId, text: &str) {
        self.delete_document(node);
        // The engine stores terms verbatim, so normalization (lowercase +
        // diacritic folding) MUST happen here on both the write and read
        // paths — otherwise `sănătate` in a document is unreachable by a
        // folded query for `sanatate` (law-ro pilot finding 12).
        let tokens = self.tokenizer.tokenize(text);
        let normed: Vec<String> = tokens
            .iter()
            .map(|(t, _)| crate::index::normalize_term(t))
            .collect();
        let terms: Vec<&str> = normed.iter().map(|s| s.as_str()).collect();
        let positions: Vec<Vec<u32>> = tokens.iter().map(|(_, pos)| vec![*pos]).collect();
        let global_id = self.engine.lock().unwrap().add_document(&terms, &positions);
        self.node_to_global.insert(node.0, global_id);
        self.global_to_node.insert(global_id, node.0);
    }

    fn search(&self, query: &str, max_results: usize) -> Vec<(NodeId, f64)> {
        if max_results == 0 {
            return Vec::new();
        }
        let tokens = self.tokenizer.tokenize(query);
        let normed: Vec<String> = tokens
            .iter()
            .map(|(t, _)| crate::index::normalize_term(t))
            .collect();
        let terms: Vec<&str> = normed.iter().map(|s| s.as_str()).collect();
        if terms.is_empty() {
            return vec![];
        }
        let raw = self.engine.lock().unwrap().search(&terms);
        raw.into_iter()
            .filter_map(|(global_id, score)| {
                self.global_to_node
                    .get(&global_id)
                    .map(|&nid| (NodeId(nid), score as f64))
            })
            .take(max_results)
            .collect()
    }

    fn phrase_search(&self, phrase: &str, max_gap: u32, max_results: usize) -> Vec<(NodeId, f64)> {
        if max_results == 0 {
            return Vec::new();
        }
        let tokens = self.tokenizer.tokenize(phrase);
        let normed: Vec<String> = tokens
            .iter()
            .map(|(t, _)| crate::index::normalize_term(t))
            .collect();
        let terms: Vec<&str> = normed.iter().map(|s| s.as_str()).collect();
        if terms.is_empty() {
            return vec![];
        }
        let raw = self.engine.lock().unwrap().phrase_search(&terms, max_gap);
        raw.into_iter()
            .filter_map(|(global_id, score)| {
                self.global_to_node
                    .get(&global_id)
                    .map(|&nid| (NodeId(nid), score as f64))
            })
            .take(max_results)
            .collect()
    }

    fn stats(&self) -> IndexStats {
        let g = self.engine.lock().unwrap();
        crate::index::IndexStats {
            unique_terms: 0,
            total_postings: 0,
            document_count: g.approx_doc_count() as u32,
            avg_doc_length: 0.0,
        }
    }

    fn delete_document(&mut self, node: NodeId) {
        if let Some(&global_id) = self.node_to_global.get(&node.0) {
            self.engine.lock().unwrap().delete(global_id);
            self.global_to_node.remove(&global_id);
        }
    }

    fn is_document_deleted(&self, node: NodeId) -> bool {
        if let Some(&global_id) = self.node_to_global.get(&node.0) {
            self.engine.lock().unwrap().is_deleted_global(global_id)
        } else {
            false
        }
    }

    fn vacuum(&mut self) {
        self.engine.lock().unwrap().vacuum();
    }
    fn compact(&mut self) {
        self.engine.lock().unwrap().compact();
    }

    fn take_sealed_segment(
        &mut self,
        index: usize,
    ) -> Option<(crate::on_disk_index::OnDiskSegment, u32)> {
        self.engine.lock().unwrap().remove_sealed_segment(index)
    }

    fn add_sealed_segment(&mut self, seg: crate::on_disk_index::OnDiskSegment, tier: u32) {
        // Do NOT clear node_to_global — existing mappings remain valid (C4 fix).
        // New segments from the rebalancer have global_id_map populated, so search still works.
        self.engine.lock().unwrap().add_sealed_segment(seg, tier);
    }

    fn sample_representative_for_segment(&self, seg_idx: usize) -> Option<String> {
        let infos = self.engine.lock().unwrap().sealed_segment_info();
        if seg_idx >= infos.len() {
            return None;
        }
        let info = &infos[seg_idx];
        Some(format!(
            "seg-sample-tier{}-docs{}",
            info.tier, info.doc_count
        ))
    }

    fn segment_info(&self) -> Vec<crate::on_disk_index::SegmentInfo> {
        self.engine.lock().unwrap().sealed_segment_info()
    }

    fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        self.engine
            .lock()
            .unwrap()
            .save_to_dir(dir)
            .map_err(|e| crate::GraphError::Other(e.to_string()))?;
        // Persist the NodeId→GlobalId map alongside the text index
        let map_json = serde_json::to_string_pretty(&self.node_to_global)
            .map_err(|e| crate::GraphError::Other(e.to_string()))?;
        std::fs::write(dir.join("node_map.json"), map_json).map_err(crate::GraphError::from)?;
        Ok(())
    }

    fn load_from_dir(
        dir: &std::path::Path,
    ) -> Result<Box<dyn crate::index::TextIndex>, crate::GraphError> {
        // Load the real engine state
        let engine = crate::on_disk_index::OnDiskIndex::load_from_dir(dir).map_err(|e| {
            crate::GraphError::Other(format!("failed to load on-disk index: {}", e))
        })?;

        // Restore NodeId → GlobalId mapping
        let node_to_global: std::collections::HashMap<u32, u32> = {
            let map_path = dir.join("node_map.json");
            if map_path.exists() {
                let bytes = std::fs::read(&map_path).map_err(crate::GraphError::from)?;
                serde_json::from_slice(&bytes)
                    .map_err(|e| crate::GraphError::Other(e.to_string()))?
            } else {
                std::collections::HashMap::new()
            }
        };

        Ok(Box::new(Self {
            engine: std::sync::Arc::new(std::sync::Mutex::new(engine)),
            tokenizer: Box::new(crate::index::DefaultTokenizer::new()),
            global_to_node: node_to_global
                .iter()
                .map(|(&node, &global)| (global, node))
                .collect(),
            node_to_global,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::NodeId;
    use crate::index::TextIndex;
    use tempfile::tempdir;

    #[test]
    fn search_finds_indexed_node() {
        let mut idx = OnDiskTextIndex::new();
        idx.index_document(NodeId(0), "rust ownership memory safety");
        idx.index_document(NodeId(1), "python garbage collection");
        idx.index_document(NodeId(2), "rust borrow checker lifetime");

        let results = idx.search("rust", 10);
        let ids: Vec<u32> = results.iter().map(|(n, _)| n.0).collect();
        assert!(ids.contains(&0), "NodeId(0) should be found");
        assert!(ids.contains(&2), "NodeId(2) should be found");
        assert!(!ids.contains(&1), "NodeId(1) should NOT appear for 'rust'");
    }

    #[test]
    fn search_folds_case_and_diacritics() {
        // The engine stores terms verbatim, so the adapter must normalize on
        // both paths (law-ro pilot finding 12). Case-folding rides along: the
        // raw-term passthrough previously made this backend case-sensitive.
        let mut idx = OnDiskTextIndex::new();
        idx.index_document(NodeId(0), "asigurările sociale de sănătate");
        idx.index_document(NodeId(1), "Rust Memory Safety");

        let folded = idx.search("sanatate", 10);
        assert!(
            folded.iter().any(|(n, _)| n.0 == 0),
            "folded query must hit diacritic evidence"
        );
        let diacritic = idx.search("sănătate", 10);
        assert!(diacritic.iter().any(|(n, _)| n.0 == 0));

        let lower = idx.search("rust memory", 10);
        assert!(lower.iter().any(|(n, _)| n.0 == 1), "case must fold");

        let phrase = idx.phrase_search("asigurarile sociale", 10, 5);
        assert!(phrase.iter().any(|(n, _)| n.0 == 0));
    }

    #[test]
    fn search_survives_save_load_cycle() {
        let dir = tempdir().unwrap();

        let mut idx = OnDiskTextIndex::new();
        idx.index_document(NodeId(0), "knowledge graph engine");
        idx.index_document(NodeId(1), "text search bm25");
        idx.save_to_dir(dir.path()).unwrap();

        let loaded_box = OnDiskTextIndex::load_from_dir(dir.path()).unwrap();
        let results = loaded_box.search("knowledge", 10);
        let ids: Vec<u32> = results.iter().map(|(n, _)| n.0).collect();
        assert!(ids.contains(&0), "NodeId(0) must survive save/load");
        assert!(!ids.contains(&1));
    }

    #[test]
    fn delete_hides_node_from_search() {
        let mut idx = OnDiskTextIndex::new();
        idx.index_document(NodeId(0), "rust programming language");
        idx.index_document(NodeId(1), "rust memory safety");
        idx.delete_document(NodeId(0));

        let results = idx.search("rust", 10);
        let ids: Vec<u32> = results.iter().map(|(n, _)| n.0).collect();
        assert!(!ids.contains(&0), "deleted node should not appear");
        assert!(ids.contains(&1));
    }

    #[test]
    fn is_document_deleted_reflects_deletes() {
        let mut idx = OnDiskTextIndex::new();
        idx.index_document(NodeId(3), "some content");
        assert!(!idx.is_document_deleted(NodeId(3)));
        idx.delete_document(NodeId(3));
        assert!(idx.is_document_deleted(NodeId(3)));
    }

    #[test]
    fn is_document_deleted_after_save_load() {
        let dir = tempdir().unwrap();

        let mut idx = OnDiskTextIndex::new();
        idx.index_document(NodeId(0), "document zero content");
        idx.index_document(NodeId(1), "document one content");
        idx.delete_document(NodeId(0));
        idx.save_to_dir(dir.path()).unwrap();

        let loaded = OnDiskTextIndex::load_from_dir(dir.path()).unwrap();
        assert!(
            loaded.is_document_deleted(NodeId(0)),
            "deleted status must survive save/load"
        );
        assert!(!loaded.is_document_deleted(NodeId(1)));
    }
}
