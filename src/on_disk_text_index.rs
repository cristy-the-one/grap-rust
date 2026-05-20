//! On-Disk / Persistent Text Index (thin bridge layer for 0.2).
//!
//! This module is the start of the production integration of the segmented
//! on-disk inverted index developed in `spikes/on_disk_index/`.
//!
//! Current status (as of this commit):
//! - Defines `OnDiskTextIndex`, a placeholder that will eventually wrap
//!   the `OnDiskIndex` (or a cleaned-up version of it) from the spike.
//! - Implements the common `TextIndex` trait defined in `index.rs`.
//! - When the `on-disk-index` feature is enabled, `IndexedKnowledgeGraph`
//!   can be constructed with a persistent backend.
//!
//! Long-term plan:
//! - Move the good parts of the spike (SegmentBuilder, OnDiskSegment,
//!   tiered merge, WAL, phrase search with positions, manifest) into this
//!   module (or a sibling `persistent_inverted_index`).
//! - Make `OnDiskTextIndex` own one (or per-shard) `OnDiskIndex` instances.
//! - Wire `GlobalNodeId` translation at this layer or above.
//!
//! For now this is intentionally a skeleton so the rest of the codebase
//! can start depending on the `TextIndex` trait.

use crate::ids::NodeId;
use crate::index::{DefaultTokenizer, IndexStats, TextIndex, Tokenizer};
use crate::on_disk_index::{self as engine, OnDiskIndex};

/// Placeholder for the persistent on-disk text index.
///
/// In a full implementation this would contain:
/// - One or more `OnDiskIndex` (from the spike design)
/// - A tokenizer
/// - Mapping from `NodeId` (or `GlobalNodeId`) to (segment, local_doc)
/// - Background merge worker handle
/// - WAL management, etc.
pub struct OnDiskTextIndex {
    engine: OnDiskIndex,
    tokenizer: Box<dyn Tokenizer>,
    /// Simple mapping for non-sharded case: NodeId (as u32) -> local doc id inside the engine.
    /// In a real sharded setup this mapping lives per-shard.
    node_to_local: std::collections::HashMap<u32, engine::LocalDocId>,
    next_local_id: engine::LocalDocId,
}

impl OnDiskTextIndex {
    pub fn new() -> Self {
        Self {
            engine: OnDiskIndex::new(),
            tokenizer: Box::new(DefaultTokenizer::new()),
            node_to_local: std::collections::HashMap::new(),
            next_local_id: 0,
        }
    }

    pub fn new_in_dir(dir: &std::path::Path) -> std::io::Result<Self> {
        let mut s = Self::new();
        // In a full port we would load the engine from dir here.
        // For now we just ensure the directory exists for future saves.
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
        // We pass the local id as the "doc" for the engine.
        // In a real mapping we would store the association.
        let _ = self.engine.add_document(&terms, &positions);
        // For simplicity in this step we just use the engine's own local ids.
        // A production version would maintain a bidirectional map.
    }

    fn search(&self, query: &str, max_results: usize) -> Vec<(NodeId, f64)> {
        // Use the engine's search (which now has full implementation)
        // and map local results back to NodeId (naive reverse lookup for demo).
        let raw = self.engine.search(&[query]); // simplified
        raw.into_iter()
            .take(max_results)
            .filter_map(|(local, score)| {
                // Reverse map (slow but correct for small graphs during integration)
                self.node_to_local.iter().find(|(_, &v)| v == local).map(|(&nid, _)| (NodeId(nid), score as f64))
            })
            .collect()
    }

    fn phrase_search(&self, phrase: &str, max_gap: u32, max_results: usize) -> Vec<(NodeId, f64)> {
        let terms: Vec<&str> = phrase.split_whitespace().collect();
        let raw = self.engine.phrase_search(&terms, max_gap); // when fully restored
        raw.into_iter().take(max_results).map(|(local, sc)| (NodeId(local), sc as f64)).collect()
    }

    fn stats(&self) -> IndexStats {
        crate::index::IndexStats {
            unique_terms: 0,
            total_postings: 0,
            document_count: self.engine.approx_doc_count() as u32,
            avg_doc_length: 0.0,
        }
    }

    fn delete_document(&mut self, node: NodeId) {
        if let Some(&local) = self.node_to_local.get(&node.0) {
            self.engine.delete(local);
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

    /// Expose vacuum / compaction to the higher layers.
    pub fn vacuum(&mut self) {
        self.engine.vacuum();
    }

    /// Compact the underlying on-disk index (merge segments, reclaim space).
    pub fn compact(&mut self) {
        self.engine.compact();
    }

    /// Returns segment-level information (doc counts, tiers, etc.).
    /// Useful for segment-aware rebalancing decisions.
    pub fn sealed_segment_info(&self) -> Vec<crate::on_disk_index::SegmentInfo> {
        self.engine.sealed_segment_info()
    }

    pub fn remove_sealed_segment(&mut self, index: usize) -> Option<(crate::on_disk_index::OnDiskSegment, u32)> {
        self.engine.remove_sealed_segment(index)
    }

    pub fn add_sealed_segment(&mut self, seg: crate::on_disk_index::OnDiskSegment, tier: u32) {
        self.engine.add_sealed_segment(seg, tier);
        self.node_to_local.clear();
        self.next_local_id = 0;
    }

    // Trait overrides for rebalancer
    fn take_sealed_segment(&mut self, index: usize) -> Option<(crate::on_disk_index::OnDiskSegment, u32)> {
        self.engine.remove_sealed_segment(index)
    }
    fn add_sealed_segment(&mut self, seg: crate::on_disk_index::OnDiskSegment, tier: u32) {
        self.engine.add_sealed_segment(seg, tier);
        self.node_to_local.clear();
        self.next_local_id = 0;
    }
    fn sample_representative_for_segment(&self, seg_idx: usize) -> Option<String> {
        let infos = self.engine.sealed_segment_info();
        if seg_idx >= infos.len() { return None; }
        let info = &infos[seg_idx];
        Some(format!("seg-sample-tier{}-docs{}", info.tier, info.doc_count))
    }

    // === Persistence (delegated to the inner OnDiskIndex) ===

    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        self.engine
            .save_to_dir(dir)
            .map_err(|e| crate::GraphError::Other(e.to_string()))
    }

    /// Load an `OnDiskTextIndex` from a directory previously written by `save_to_dir`.
    pub fn load_from_dir(dir: &std::path::Path) -> Result<Box<dyn crate::index::TextIndex>, crate::GraphError> {
        // Load the real engine state (this is the important part that was missing).
        let engine = crate::on_disk_index::OnDiskIndex::load_from_dir(dir)
            .map_err(|e| crate::GraphError::Other(format!("failed to load on-disk index: {}", e)))?;

        let idx = Self {
            engine,
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