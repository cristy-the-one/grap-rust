//! Spike / Prototype for the On-Disk Inverted Index (grap 0.2)
//!
//! Goal of this spike:
//! - Explore a simple segmented on-disk inverted index design
//! - Support basic indexing, persistence, and querying
//! - Support per-segment deleted document bitmaps (for tombstones)
//!
//! This is exploratory code. It is NOT intended to be merged into the main crate yet.

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::Path;

use bincode;
use serde::{Deserialize, Serialize};

/// Represents a document identifier *within a single segment*.
/// We keep local doc ids small and dense per segment.
pub type LocalDocId = u32;

/// A very simple in-memory representation of one term's posting list in a segment.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Posting {
    pub doc_id: LocalDocId,
    pub term_freq: u32,
    // For now we store positions only if needed (we can make this optional later)
    pub positions: Vec<u32>,
}

/// One segment on disk.
/// For the spike we keep everything very simple.
#[derive(Debug, Serialize, Deserialize)]
pub struct Segment {
    /// Number of documents in this segment (before deletes)
    pub doc_count: u32,

    /// Deleted document bitmap (bit = 1 means deleted)
    /// Using a simple Vec<u8> for now (bit packed). In a real impl we'd use bitvec/roaring.
    pub deleted: Vec<u8>,

    /// Term -> list of postings (very naive for the spike)
    /// In reality we'd have a separate term dictionary + offset table.
    pub postings: HashMap<String, Vec<Posting>>,
}

impl Segment {
    pub fn new() -> Self {
        Self {
            doc_count: 0,
            deleted: Vec::new(),
            postings: HashMap::new(),
        }
    }

    /// Add a document to the in-memory segment.
    pub fn add_document(&mut self, terms: &[&str], positions: &[Vec<u32>]) -> LocalDocId {
        let doc_id = self.doc_count;
        self.doc_count += 1;

        // Grow deleted bitmap if needed (very naive)
        let needed_bytes = ((self.doc_count as usize) + 7) / 8;
        self.deleted.resize(needed_bytes, 0);

        for (i, term) in terms.iter().enumerate() {
            let posting = Posting {
                doc_id,
                term_freq: 1,
                positions: positions.get(i).cloned().unwrap_or_default(),
            };
            self.postings
                .entry(term.to_string())
                .or_default()
                .push(posting);
        }

        doc_id
    }

    /// Mark a document as deleted in this segment.
    pub fn delete(&mut self, doc_id: LocalDocId) {
        let byte_index = (doc_id as usize) / 8;
        let bit_index = (doc_id as usize) % 8;
        if byte_index < self.deleted.len() {
            self.deleted[byte_index] |= 1 << bit_index;
        }
    }

    /// Check if a document is deleted.
    pub fn is_deleted(&self, doc_id: LocalDocId) -> bool {
        let byte_index = (doc_id as usize) / 8;
        let bit_index = (doc_id as usize) % 8;
        if byte_index >= self.deleted.len() {
            return false;
        }
        (self.deleted[byte_index] & (1 << bit_index)) != 0
    }

    /// Write the segment to disk (very simple format for the spike).
    pub fn write_to_disk(&self, path: &Path) -> std::io::Result<()> {
        let file = File::create(path)?;
        let mut writer = BufWriter::new(file);
        let encoded = bincode::serialize(self).map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::Other, e)
        })?;
        writer.write_all(&encoded)?;
        writer.flush()?;
        Ok(())
    }

    /// Load a segment from disk.
    pub fn load_from_disk(path: &Path) -> std::io::Result<Self> {
        let file = File::open(path)?;
        let mut reader = BufReader::new(file);
        let mut buf = Vec::new();
        reader.read_to_end(&mut buf)?;
        let segment: Segment = bincode::deserialize(&buf).map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::Other, e)
        })?;
        Ok(segment)
    }
}

/// Very naive multi-segment index (in-memory coordinator for the spike).
pub struct OnDiskIndex {
    pub segments: Vec<Segment>,
}

impl OnDiskIndex {
    pub fn new() -> Self {
        Self { segments: Vec::new() }
    }

    pub fn add_segment(&mut self, segment: Segment) {
        self.segments.push(segment);
    }

    /// Extremely naive search across all segments.
    /// Returns (segment_index, LocalDocId) pairs for matching docs.
    pub fn search_term(&self, term: &str) -> Vec<(usize, LocalDocId)> {
        let mut results = Vec::new();

        for (seg_idx, seg) in self.segments.iter().enumerate() {
            if let Some(postings) = seg.postings.get(term) {
                for p in postings {
                    if !seg.is_deleted(p.doc_id) {
                        results.push((seg_idx, p.doc_id));
                    }
                }
            }
        }

        results
    }
}

fn main() {
    println!("=== On-Disk Inverted Index Spike ===\n");

    // === In-memory indexing phase ===
    let mut seg1 = Segment::new();

    seg1.add_document(&["rust", "memory", "safety"], &[vec![0], vec![1], vec![2]]);
    seg1.add_document(&["rust", "ownership"], &[vec![0], vec![3]]);
    let doc2 = seg1.add_document(&["borrowing", "rust"], &[vec![0], vec![1]]);

    println!("Created segment with {} documents", seg1.doc_count);

    // Delete a document (tombstone)
    seg1.delete(doc2);
    println!("Deleted document {} (tombstone set)", doc2);

    // Persist the segment
    let temp_dir = tempfile::tempdir().unwrap();
    let seg_path = temp_dir.path().join("seg-00001.bin");
    seg1.write_to_disk(&seg_path).expect("failed to write segment");
    println!("Wrote segment to disk: {:?}", seg_path);

    // === Load phase ===
    let loaded = Segment::load_from_disk(&seg_path).expect("failed to load segment");
    println!("Loaded segment from disk: {} documents", loaded.doc_count);

    // === Multi-segment index simulation ===
    let mut index = OnDiskIndex::new();
    index.add_segment(loaded);

    // Search
    let results = index.search_term("rust");
    println!("\nSearch results for 'rust': {:?}", results);

    println!("\nSpike completed successfully.");
}