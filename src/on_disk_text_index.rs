//! On-Disk Text Index Adapter
//!
//! This module provides [`OnDiskTextIndex`], a thin adapter that implements
//! the [`TextIndex`] trait on top of the real on-disk engine
//! (`on_disk_index` module).
//!
//! It is responsible for:
//! - Tokenization
//! - Mapping between `NodeId` and the engine's internal local document IDs
//! - Delegation of search, phrase search, persistence, compaction, and
//!   segment information
//! - Starting background merge/vacuum workers
//!
//! Most of the heavy lifting (segment storage, tiered merging, WAL, etc.)
//! lives in `on_disk_index.rs`. This module exists so the rest of the
//! crate can stay backend-agnostic via the `TextIndex` trait.

use crate::ids::NodeId;
use crate::index::{DefaultTokenizer, IndexStats, TextIndex, Tokenizer};
use crate::on_disk_index::{self as engine, OnDiskIndex};

/// Adapter that makes the on-disk engine usable through the `TextIndex` trait.
///
/// See the module-level documentation for design rationale.
pub struct OnDiskTextIndex {
    /// The engine is behind an Arc<Mutex<>> so that background merge/vacuum
    /// workers can safely hold a reference to it.
    engine: std::sync::Arc<std::sync::Mutex<OnDiskIndex>>,
    tokenizer: Box<dyn Tokenizer>,
    /// Simple mapping for non-sharded case: NodeId (as u32) -> local doc id inside the engine.
    /// In a real sharded setup this mapping lives per-shard.
    node_to_local: std::collections::HashMap<u32, engine::LocalDocId>,
    next_local_id: engine::LocalDocId,
}

impl OnDiskTextIndex {
    pub fn new() -> Self {
        Self {
            engine: std::sync::Arc::new(std::sync::Mutex::new(OnDiskIndex::new())),
            tokenizer: Box::new(DefaultTokenizer::new()),
            node_to_local: std::collections::HashMap::new(),
            next_local_id: 0,
        }
    }

    pub fn new_in_dir(dir: &std::path::Path) -> std::io::Result<Self> {
        let mut s = Self::new();
        std::fs::create_dir_all(dir)?;
        Ok(s)
    }

    fn ensure_local(&mut self, node: NodeId) -> engine::LocalDocId {
        let key = node.0;
        if let Some(&lid) = self.node_to_local.get(&key) {
            return lid;
        }
        let lid = self.next_local_id;
        self.next_local_id += 1;
        self.node_to_local.insert(key, lid);
        lid
    }

    // Public inherent API (so callers can do idx.vacuum() directly on the concrete type)
    pub fn vacuum(&mut self) {
        self.engine.lock().unwrap().vacuum();
    }
    pub fn compact(&mut self) {
        self.engine.lock().unwrap().compact();
    }
    pub fn remove_sealed_segment(&mut self, index: usize) -> Option<(crate::on_disk_index::OnDiskSegment, u32)> {
        self.engine.lock().unwrap().remove_sealed_segment(index)
    }
    pub fn add_sealed_segment(&mut self, seg: crate::on_disk_index::OnDiskSegment, tier: u32) {
        self.engine.lock().unwrap().add_sealed_segment(seg, tier);
        self.node_to_local.clear();
        self.next_local_id = 0;
    }
    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        self.engine
            .lock()
            .unwrap()
            .save_to_dir(dir)
            .map_err(|e| crate::GraphError::Other(e.to_string()))
    }

    /// Start a background merge + incremental vacuum worker for this on-disk index.
    /// Keep the returned handle alive for as long as you want the worker running.
    pub fn start_merge_worker(&self, interval: std::time::Duration) -> crate::on_disk_index::MergeWorkerHandle {
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
        let tokens = self.tokenizer.tokenize(text);
        let terms: Vec<&str> = tokens.iter().map(|(t, _)| *t).collect();
        let positions: Vec<Vec<u32>> = tokens.iter().map(|(_, pos)| vec![*pos]).collect();

        let local = self.ensure_local(node);
        let _ = self.engine.lock().unwrap().add_document(&terms, &positions);
        // For simplicity in this step we just use the engine's own local ids.
        // A production version would maintain a bidirectional map.
    }

    fn search(&self, query: &str, max_results: usize) -> Vec<(NodeId, f64)> {
        let raw = self.engine.lock().unwrap().search(&[query]);
        raw.into_iter()
            .take(max_results)
            .filter_map(|(local, score)| {
                self.node_to_local.iter().find(|(_, &v)| v == local).map(|(&nid, _)| (NodeId(nid), score as f64))
            })
            .collect()
    }

    fn phrase_search(&self, phrase: &str, max_gap: u32, max_results: usize) -> Vec<(NodeId, f64)> {
        let terms: Vec<&str> = phrase.split_whitespace().collect();
        let raw = self.engine.lock().unwrap().phrase_search(&terms, max_gap);
        raw.into_iter().take(max_results).map(|(local, sc)| (NodeId(local), sc as f64)).collect()
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
        if let Some(&local) = self.node_to_local.get(&node.0) {
            self.engine.lock().unwrap().delete(local);
        }
    }

    fn is_document_deleted(&self, node: NodeId) -> bool {
        if let Some(&local) = self.node_to_local.get(&node.0) {
            // In a full engine this would ask the owning segment's delete bitmap
            false
        } else {
            false
        }
    }

    // The trait now declares vacuum/compact/take/add/sample as defaulted methods.
    // We only override the ones we actually need for the on-disk backend.
    fn vacuum(&mut self) {
        self.engine.lock().unwrap().vacuum();
    }
    fn compact(&mut self) {
        self.engine.lock().unwrap().compact();
    }

    // Trait overrides for rebalancer
    fn take_sealed_segment(&mut self, index: usize) -> Option<(crate::on_disk_index::OnDiskSegment, u32)> {
        self.engine.lock().unwrap().remove_sealed_segment(index)
    }
    fn add_sealed_segment(&mut self, seg: crate::on_disk_index::OnDiskSegment, tier: u32) {
        self.engine.lock().unwrap().add_sealed_segment(seg, tier);
        self.node_to_local.clear();
        self.next_local_id = 0;
    }
    fn sample_representative_for_segment(&self, seg_idx: usize) -> Option<String> {
        let infos = self.engine.lock().unwrap().sealed_segment_info();
        if seg_idx >= infos.len() { return None; }
        let info = &infos[seg_idx];
        Some(format!("seg-sample-tier{}-docs{}", info.tier, info.doc_count))
    }

    fn segment_info(&self) -> Vec<crate::on_disk_index::SegmentInfo> {
        self.engine.lock().unwrap().sealed_segment_info()
    }

    fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        self.engine
            .lock()
            .unwrap()
            .save_to_dir(dir)
            .map_err(|e| crate::GraphError::Other(e.to_string()))
    }

    /// Load an `OnDiskTextIndex` from a directory previously written by `save_to_dir`.
    fn load_from_dir(dir: &std::path::Path) -> Result<Box<dyn crate::index::TextIndex>, crate::GraphError> {
        // Load the real engine state (this is the important part that was missing).
        let engine = crate::on_disk_index::OnDiskIndex::load_from_dir(dir)
            .map_err(|e| crate::GraphError::Other(format!("failed to load on-disk index: {}", e)))?;

        let idx = Self {
            engine: std::sync::Arc::new(std::sync::Mutex::new(engine)),
            tokenizer: Box::new(crate::index::DefaultTokenizer::new()),
            node_to_local: std::collections::HashMap::new(),
            next_local_id: 0,
        };

        Ok(Box::new(idx))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn placeholder_compiles_and_implements_trait() {
        let idx = OnDiskTextIndex::new();
        let _s = idx.stats();
    }
}