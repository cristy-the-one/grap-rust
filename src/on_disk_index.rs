//! Segmented On-Disk Inverted Index Engine (first-class public API).
//!
//! This module contains the core persistent text index implementation:
//! - Immutable segments (`OnDiskSegment`) with term dictionary + postings + tombstone bitmap
//! - Active buffer + simple WAL for durability before sealing
//! - Tiered merge policy (`TieredMergePolicy`) and background `MergeWorker`
//! - Real incremental `vacuum()` (only rewrites segments above a delete threshold)
//! - `save_to_dir` / `load_from_dir` with manifest + per-segment files
//! - Segment-aware primitives (`SegmentInfo`, `take_sealed_segment`, etc.) used by sharded rebalancing
//!
//! Most application code should use the higher-level adapter
//! [`crate::on_disk_text_index::OnDiskTextIndex`] (which implements [`crate::TextIndex`])
//! or the convenience constructors on [`crate::IndexedKnowledgeGraph`].
//!
//! Advanced users who want direct access to the engine (custom merging, low-level
//! segment migration, etc.) can work with [`OnDiskIndex`], [`OnDiskSegment`],
//! [`merge_segments`], and the worker types directly.
//!
//! This module is always available — the on-disk index is a core, supported feature
//! of the library, not an optional experiment.

use std::collections::{BTreeMap, HashMap};
use std::fs::File;
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::Path;
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use bincode;
use serde::{Deserialize, Serialize};

/// A doc-local identifier within a single segment (`u32` alias for readability).
pub type LocalDocId = u32;

/// Lightweight description of one sealed segment.
/// Used by segment-aware rebalancing, vacuum planning, and monitoring.
#[derive(Debug, Clone, Copy)]
pub struct SegmentInfo {
    /// Total (including deleted) document count in this segment.
    pub doc_count: u32,
    /// Merge tier (0 = freshly sealed, higher = more merges).
    pub tier: u32,
    /// Live (non-deleted) document count in the segment.
    pub live_doc_count: u32,
    /// Approximate size in bytes of the segment data (postings + metadata + delete bitmap).
    /// Useful for cost-based rebalancing decisions.
    pub size_bytes: u64,
}

impl SegmentInfo {
    /// Fraction of documents in this segment that have been deleted (tombstoned).
    /// 0.0 = perfectly clean, 1.0 = all documents deleted.
    pub fn delete_ratio(&self) -> f32 {
        if self.doc_count == 0 {
            0.0
        } else {
            let deleted = self.doc_count - self.live_doc_count;
            deleted as f32 / self.doc_count as f32
        }
    }

    /// Convenience: number of deleted documents in the segment.
    pub fn deleted_count(&self) -> u32 {
        self.doc_count - self.live_doc_count
    }
}

const SEGMENT_MAGIC: [u8; 4] = *b"GRPI";
const SEGMENT_VERSION: u16 = 2;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SegmentFileHeader {
    magic: [u8; 4],
    version: u16,
}

const MANIFEST_VERSION: u16 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SegmentEntry {
    filename: String,
    tier: u32,
    doc_count: u32,
    /// Global doc id of the first document in this segment (0 for merged segments).
    #[serde(default)]
    start_global_id: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct IndexManifest {
    version: u16,
    segments: Vec<SegmentEntry>,
    total_docs: u64,
    total_tokens: u64,
    /// The next global doc id to assign. Restored on load so the counter never resets.
    #[serde(default)]
    global_doc_counter: u32,
    /// The global id that the active buffer starts at after the last flush.
    #[serde(default)]
    active_start_global_id: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
enum WalOp {
    AddDocument {
        terms: Vec<String>,
        positions: Vec<Vec<u32>>,
    },
    Delete {
        doc_id: LocalDocId,
    },
}

#[derive(Debug)]
struct SimpleWal {
    path: std::path::PathBuf,
    file: Option<std::fs::File>,
}

impl SimpleWal {
    fn create(path: &Path) -> std::io::Result<Self> {
        let f = std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(path)?;
        Ok(Self {
            path: path.to_path_buf(),
            file: Some(f),
        })
    }

    fn open_for_replay(path: &Path) -> std::io::Result<Self> {
        let f = std::fs::OpenOptions::new().read(true).open(path).ok();
        Ok(Self {
            path: path.to_path_buf(),
            file: f,
        })
    }

    fn append(&mut self, op: &WalOp) -> std::io::Result<()> {
        if let Some(f) = &mut self.file {
            let bytes = bincode::serialize(op).map_err(std::io::Error::other)?;
            let len = bytes.len() as u32;
            use std::io::Write;
            f.write_all(&len.to_le_bytes())?;
            f.write_all(&bytes)?;
            f.flush()?;
        }
        Ok(())
    }

    fn replay_into(&self, active: &mut ActiveBuffer) -> std::io::Result<()> {
        if let Ok(mut f) = File::open(&self.path) {
            let mut buf = Vec::new();
            f.read_to_end(&mut buf)?;
            let mut cursor = 0;
            while cursor + 4 <= buf.len() {
                let len = u32::from_le_bytes([
                    buf[cursor],
                    buf[cursor + 1],
                    buf[cursor + 2],
                    buf[cursor + 3],
                ]) as usize;
                cursor += 4;
                if cursor + len > buf.len() {
                    break;
                }
                if let Ok(op) = bincode::deserialize::<WalOp>(&buf[cursor..cursor + len]) {
                    match op {
                        WalOp::AddDocument { terms, positions } => {
                            let t: Vec<&str> = terms.iter().map(|s| s.as_str()).collect();
                            let _ = active.add_document(&t, &positions);
                        }
                        WalOp::Delete { doc_id } => {
                            active.delete(doc_id);
                        }
                    }
                }
                cursor += len;
            }
        }
        Ok(())
    }

    fn truncate(&mut self) -> std::io::Result<()> {
        if let Some(f) = &mut self.file {
            f.set_len(0)?;
            f.flush()?;
        }
        Ok(())
    }
}

/// One entry in a posting list: records which document contains a term,
/// how many times, and at which byte positions.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Posting {
    /// The local document id within the segment.
    pub doc_id: LocalDocId,
    /// Number of times the term appears in this document.
    pub term_freq: u32,
    /// Byte positions of each occurrence (used for phrase queries).
    pub positions: Vec<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct TermMeta {
    term: String,
    df: u32,
    posting_offset: u32,
    posting_len: u32,
}

/// A sealed, immutable segment of the on-disk inverted index.
///
/// Produced by [`ActiveBuffer::seal`] or [`merge_segments_with_offsets`].
/// Once sealed, postings are read-only; deletions are tracked via the
/// `deleted` bitmap.
#[derive(Debug, Serialize, Deserialize)]
pub struct OnDiskSegment {
    /// Total number of documents (including deleted ones) in this segment.
    pub doc_count: u32,
    /// Bit-packed tombstone array (`bit i` set → document `i` is deleted).
    pub deleted: Vec<u8>,
    terms: Vec<TermMeta>,
    postings_blob: Vec<u8>,
    doc_lengths: Vec<u32>,
    /// Maps new_local_id → original global doc id, populated after a merge/compact.
    /// Empty for directly-indexed segments (use `start_global_id + local_id` instead).
    #[serde(default)]
    pub global_id_map: Vec<u32>,
}

impl OnDiskSegment {
    /// Returns `true` if the given local doc id has been tombstoned.
    pub fn is_deleted(&self, doc_id: LocalDocId) -> bool {
        let byte = (doc_id as usize) / 8;
        let bit = (doc_id as usize) % 8;
        byte < self.deleted.len() && (self.deleted[byte] & (1 << bit)) != 0
    }

    /// Compute the stable global doc id for a given local id in this segment.
    /// Uses `global_id_map` if populated (post-merge), otherwise `start_global_id + local_id`.
    pub fn get_global_id(&self, start_global_id: u32, local_id: u32) -> u32 {
        self.global_id_map
            .get(local_id as usize)
            .copied()
            .unwrap_or(start_global_id + local_id)
    }

    /// Mark a local doc id as deleted in this segment's bitmap.
    pub fn mark_deleted(&mut self, local_id: u32) {
        let byte = (local_id as usize) / 8;
        let bit = (local_id as usize) % 8;
        if byte < self.deleted.len() {
            self.deleted[byte] |= 1 << bit;
        }
    }

    /// Returns the live (non-deleted) posting list for `term`, or `None` if the term is absent.
    pub fn get_postings(&self, term: &str) -> Option<Vec<Posting>> {
        let pos = self
            .terms
            .binary_search_by(|t| t.term.as_str().cmp(term))
            .ok()?;
        let meta = &self.terms[pos];
        let start = meta.posting_offset as usize;
        let end = start + meta.posting_len as usize;
        if end > self.postings_blob.len() {
            return None;
        }
        let data = &self.postings_blob[start..end];
        let mut p: Vec<Posting> = bincode::deserialize(data).ok()?;
        p.retain(|x| !self.is_deleted(x.doc_id));
        Some(p)
    }

    /// Returns the token count for `doc_id` (used for BM25 length normalization).
    pub fn doc_length(&self, doc_id: LocalDocId) -> u32 {
        self.doc_lengths.get(doc_id as usize).copied().unwrap_or(1)
    }

    /// Number of live (non-deleted) documents in this segment.
    pub fn live_doc_count(&self) -> u32 {
        (0..self.doc_count).filter(|&d| !self.is_deleted(d)).count() as u32
    }

    /// Fraction of documents that are deleted (0.0 = clean, 1.0 = all deleted).
    pub fn delete_ratio(&self) -> f32 {
        if self.doc_count == 0 {
            return 0.0;
        }
        let deleted = self.doc_count - self.live_doc_count();
        deleted as f32 / self.doc_count as f32
    }

    /// Rough estimate of the on-disk / in-memory footprint of this segment.
    /// Includes postings blob, term metadata, delete bitmap, and doc lengths.
    pub fn approx_size_bytes(&self) -> u64 {
        let postings = self.postings_blob.len() as u64;
        let terms_meta = (self.terms.len() * 32) as u64; // rough: String + 3 u32s
        let deletes = self.deleted.len() as u64;
        let lengths = (self.doc_lengths.len() * 4) as u64;
        postings + terms_meta + deletes + lengths
    }

    /// Serialize this segment to a binary file at `path`.
    pub fn write_to_disk(&self, path: &Path) -> std::io::Result<()> {
        let mut w = BufWriter::new(File::create(path)?);
        let h = SegmentFileHeader {
            magic: SEGMENT_MAGIC,
            version: SEGMENT_VERSION,
        };
        let hb = bincode::serialize(&h).unwrap();
        let pb = bincode::serialize(self).unwrap();
        let hl = hb.len() as u32;
        w.write_all(&hl.to_le_bytes())?;
        w.write_all(&hb)?;
        w.write_all(&pb)?;
        w.flush()?;
        Ok(())
    }

    /// Deserialize a segment from a binary file written by `write_to_disk`.
    pub fn load_from_disk(path: &Path) -> std::io::Result<Self> {
        let mut r = BufReader::new(File::open(path)?);
        let mut buf = Vec::new();
        r.read_to_end(&mut buf)?;
        if buf.len() < 4 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "small",
            ));
        }
        let hl = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]) as usize;
        let he = 4 + hl;
        if buf.len() < he {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "trunc",
            ));
        }
        let header: SegmentFileHeader = bincode::deserialize(&buf[4..he])
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        if header.magic != SEGMENT_MAGIC || header.version != SEGMENT_VERSION {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "bad magic",
            ));
        }
        bincode::deserialize(&buf[he..]).map_err(std::io::Error::other)
    }
}

/// Accumulates documents into an in-memory inverted index structure before sealing
/// them into an immutable [`OnDiskSegment`].
#[derive(Debug)]
pub struct SegmentBuilder {
    doc_count: u32,
    deleted: Vec<u8>,
    postings: HashMap<String, Vec<Posting>>,
    doc_lengths: Vec<u32>,
}

impl Default for SegmentBuilder {
    fn default() -> Self {
        Self::new()
    }
}

impl SegmentBuilder {
    /// Creates a new empty builder.
    pub fn new() -> Self {
        Self {
            doc_count: 0,
            deleted: vec![],
            postings: HashMap::new(),
            doc_lengths: vec![],
        }
    }

    /// Index one document. Returns the segment-local doc id assigned to it.
    pub fn add_document(&mut self, terms: &[&str], positions: &[Vec<u32>]) -> LocalDocId {
        let doc_id = self.doc_count;
        self.doc_count += 1;
        let needed = (self.doc_count as usize).div_ceil(8);
        self.deleted.resize(needed, 0);
        self.doc_lengths.push(terms.len() as u32);
        for (i, t) in terms.iter().enumerate() {
            let p = positions.get(i).cloned().unwrap_or_default();
            let entries = self.postings.entry(t.to_string()).or_default();
            // Document ids increase monotonically. Repeated terms in this
            // document can only be in the last posting; avoid a full-list scan.
            if let Some(existing) = entries.last_mut().filter(|x| x.doc_id == doc_id) {
                existing.term_freq += 1;
                existing.positions.extend_from_slice(&p);
            } else {
                entries.push(Posting {
                    doc_id,
                    term_freq: 1,
                    positions: p,
                });
            }
        }
        doc_id
    }

    /// Mark a document as deleted in this builder's tombstone bitmap.
    pub fn delete(&mut self, doc_id: LocalDocId) {
        let b = (doc_id as usize) / 8;
        let bit = (doc_id as usize) % 8;
        if b < self.deleted.len() {
            self.deleted[b] |= 1 << bit;
        }
    }

    /// Consume this builder and produce an immutable [`OnDiskSegment`].
    pub fn into_on_disk_segment(mut self) -> OnDiskSegment {
        let mut terms = vec![];
        let mut blob = vec![];
        let mut list: Vec<_> = self.postings.drain().collect();
        list.sort_by(|a, b| a.0.cmp(&b.0));
        for (term, mut posts) in list {
            posts.sort_by_key(|p| p.doc_id);
            let df = posts.len() as u32;
            let off = blob.len() as u32;
            let enc = bincode::serialize(&posts).unwrap();
            let ln = enc.len() as u32;
            blob.extend_from_slice(&enc);
            terms.push(TermMeta {
                term,
                df,
                posting_offset: off,
                posting_len: ln,
            });
        }
        OnDiskSegment {
            doc_count: self.doc_count,
            deleted: self.deleted,
            terms,
            postings_blob: blob,
            doc_lengths: self.doc_lengths,
            global_id_map: vec![], // directly-indexed; uses start_global_id
        }
    }
}

/// Wraps a [`SegmentBuilder`] with optional write-ahead logging.
///
/// Documents are added here first; when the buffer grows large enough (or on
/// explicit [`OnDiskIndex::flush`]) it is sealed into an [`OnDiskSegment`].
#[derive(Debug)]
pub struct ActiveBuffer {
    pub(crate) builder: SegmentBuilder,
    total_tokens: u64,
    wal: Option<SimpleWal>,
}

impl Default for ActiveBuffer {
    fn default() -> Self {
        Self::new()
    }
}

impl ActiveBuffer {
    /// Creates a new empty active buffer (no WAL).
    pub fn new() -> Self {
        Self {
            builder: SegmentBuilder::new(),
            total_tokens: 0,
            wal: None,
        }
    }

    fn with_wal(mut self, w: SimpleWal) -> Self {
        self.wal = Some(w);
        self
    }

    /// Add a document to the buffer (optionally writing a WAL record first).
    pub fn add_document(&mut self, terms: &[&str], pos: &[Vec<u32>]) -> LocalDocId {
        if let Some(w) = &mut self.wal {
            let _ = w.append(&WalOp::AddDocument {
                terms: terms.iter().map(|s| s.to_string()).collect(),
                positions: pos.to_vec(),
            });
        }
        self.total_tokens += terms.len() as u64;
        self.builder.add_document(terms, pos)
    }

    /// Mark a document deleted in the buffer (optionally writing a WAL record first).
    pub fn delete(&mut self, id: LocalDocId) {
        if let Some(w) = &mut self.wal {
            let _ = w.append(&WalOp::Delete { doc_id: id });
        }
        self.builder.delete(id);
    }

    /// Returns the number of documents in the buffer (including deleted ones).
    pub fn doc_count(&self) -> u32 {
        self.builder.doc_count
    }
    /// Seal the buffer into an immutable [`OnDiskSegment`], truncating the WAL.
    pub fn seal(self) -> OnDiskSegment {
        if let Some(mut w) = self.wal {
            let _ = w.truncate();
        }
        self.builder.into_on_disk_segment()
    }
    /// Returns all non-deleted postings for `term` from the active buffer.
    pub fn get_live_postings(&self, term: &str) -> Vec<Posting> {
        if let Some(p) = self.builder.postings.get(term) {
            p.iter()
                .filter(|x| {
                    !self
                        .builder
                        .deleted
                        .get((x.doc_id as usize) / 8)
                        .is_some_and(|b| (b & (1 << (x.doc_id as usize % 8))) != 0)
                })
                .cloned()
                .collect()
        } else {
            vec![]
        }
    }
    /// Returns the token count (document length) for normalization in BM25.
    pub fn doc_length(&self, id: LocalDocId) -> u32 {
        self.builder
            .doc_lengths
            .get(id as usize)
            .copied()
            .unwrap_or(1)
    }
}

/// Controls when the engine merges sealed segments together.
///
/// When the number of segments at a given tier reaches `segments_per_tier`,
/// they are merged into a single segment at the next tier.
#[derive(Debug, Clone)]
pub struct TieredMergePolicy {
    /// Trigger a merge when this many segments accumulate at the same tier.
    pub segments_per_tier: usize,
}
impl Default for TieredMergePolicy {
    fn default() -> Self {
        Self {
            segments_per_tier: 4,
        }
    }
}

#[derive(Debug)]
pub(crate) struct SealedSegment {
    segment: OnDiskSegment,
    tier: u32,
    /// Global doc id of the first document in this segment.
    /// For merged segments that use `global_id_map`, this field is 0 (the map is authoritative).
    start_global_id: u32,
}

/// The main on-disk inverted index engine.
///
/// Documents are added to an in-memory [`ActiveBuffer`] which is periodically
/// flushed into immutable `SealedSegment`s. A configurable [`TieredMergePolicy`]
/// merges small segments together to keep query performance high.
///
/// Use [`OnDiskIndex::new`] for a simple in-memory-only index, or pair it with
/// an explicit `save_to_dir` / `load_from_dir` round-trip for durability.
#[derive(Debug)]
pub struct OnDiskIndex {
    active: ActiveBuffer,
    /// Global doc id that will be assigned to the next document added to the active buffer.
    active_start_global_id: u32,
    sealed: Vec<SealedSegment>,
    /// Monotonically increasing global doc id counter. Never decrements across flushes.
    global_doc_counter: u32,
    total_docs: u64,
    total_tokens: u64,
    live_docs: u64,
    live_tokens: u64,
    merge_policy: Option<TieredMergePolicy>,
}

impl Default for OnDiskIndex {
    fn default() -> Self {
        Self::new()
    }
}

impl OnDiskIndex {
    /// Creates an empty index with no merge policy (segments are never merged automatically).
    pub fn new() -> Self {
        Self {
            active: ActiveBuffer::new(),
            active_start_global_id: 0,
            sealed: vec![],
            global_doc_counter: 0,
            total_docs: 0,
            total_tokens: 0,
            live_docs: 0,
            live_tokens: 0,
            merge_policy: None,
        }
    }
    /// Creates an empty index with automatic tiered merging enabled.
    pub fn with_merge_policy(p: TieredMergePolicy) -> Self {
        let mut s = Self::new();
        s.merge_policy = Some(p);
        s
    }
    /// Enable write-ahead logging for the active buffer.
    ///
    /// # Warning
    ///
    /// This method replaces the current active buffer with a new one that has
    /// WAL enabled. Any documents added **before** calling `enable_wal` are
    /// **not** replayed from the WAL on recovery — only documents added after
    /// this call are durable via the WAL.
    ///
    /// To avoid this pitfall, enable the WAL at construction time by calling
    /// `enable_wal` immediately after `new()`, before adding any documents.
    pub fn enable_wal(&mut self, p: &Path) -> std::io::Result<()> {
        let w = SimpleWal::create(p)?;
        self.active = std::mem::take(&mut self.active).with_wal(w);
        Ok(())
    }

    /// Add a document and return its stable global doc id (never resets across flushes).
    pub fn add_document(&mut self, t: &[&str], p: &[Vec<u32>]) -> u32 {
        let global_id = self.global_doc_counter;
        self.global_doc_counter += 1;
        self.active.add_document(t, p);
        self.live_docs += 1;
        self.live_tokens += t.len() as u64;
        global_id
    }

    /// Delete the document identified by its global doc id.
    pub fn delete(&mut self, global_id: u32) {
        // Check active buffer first
        if global_id >= self.active_start_global_id {
            let local = global_id - self.active_start_global_id;
            if (local as usize) < self.active.doc_count() as usize {
                if !self.is_deleted_global(global_id) {
                    self.live_docs -= 1;
                    self.live_tokens -= self.active.doc_length(local) as u64;
                }
                self.active.delete(local);
                return;
            }
        }
        // Search sealed segments
        for sealed in &mut self.sealed {
            if !sealed.segment.global_id_map.is_empty() {
                // Post-merge segment: global_id_map[local] = original_global_id
                if let Some(local) = sealed
                    .segment
                    .global_id_map
                    .iter()
                    .position(|&gid| gid == global_id)
                {
                    if !sealed.segment.is_deleted(local as u32) {
                        self.live_docs -= 1;
                        self.live_tokens -= sealed.segment.doc_length(local as u32) as u64;
                    }
                    sealed.segment.mark_deleted(local as u32);
                    return;
                }
            } else {
                let start = sealed.start_global_id;
                if global_id >= start {
                    let local = global_id - start;
                    if (local as usize) < sealed.segment.doc_count as usize {
                        if !sealed.segment.is_deleted(local) {
                            self.live_docs -= 1;
                            self.live_tokens -= sealed.segment.doc_length(local) as u64;
                        }
                        sealed.segment.mark_deleted(local);
                        return;
                    }
                }
            }
        }
    }

    /// Returns true if the given global doc id has been tombstoned.
    pub fn is_deleted_global(&self, global_id: u32) -> bool {
        // Check active buffer
        if global_id >= self.active_start_global_id {
            let local = global_id - self.active_start_global_id;
            if (local as usize) < self.active.doc_count() as usize {
                let byte = (local as usize) / 8;
                let bit = (local as usize) % 8;
                return self
                    .active
                    .builder
                    .deleted
                    .get(byte)
                    .is_some_and(|b| (b & (1 << bit)) != 0);
            }
        }
        // Search sealed segments
        for sealed in &self.sealed {
            if !sealed.segment.global_id_map.is_empty() {
                if let Some(local) = sealed
                    .segment
                    .global_id_map
                    .iter()
                    .position(|&gid| gid == global_id)
                {
                    return sealed.segment.is_deleted(local as u32);
                }
            } else {
                let start = sealed.start_global_id;
                if global_id >= start {
                    let local = global_id - start;
                    if (local as usize) < sealed.segment.doc_count as usize {
                        return sealed.segment.is_deleted(local);
                    }
                }
            }
        }
        false
    }

    /// Seal the active buffer into a new `SealedSegment`.
    ///
    /// Returns `Some(segment_index)` — the index of the newly created segment in
    /// `self.sealed` — or `None` if the active buffer was empty (nothing to flush).
    /// The returned index is informational; callers that only need to know whether
    /// a segment was created can treat `Some(_)` as `true`.
    pub fn flush(&mut self) -> Option<usize> {
        if self.active.doc_count() == 0 {
            return None;
        }
        let seg_start = self.active_start_global_id;
        let seg = std::mem::take(&mut self.active).seal();
        self.active_start_global_id = self.global_doc_counter;
        self.total_docs += seg.doc_count as u64;
        self.total_tokens += seg.doc_lengths.iter().map(|&l| l as u64).sum::<u64>();
        self.sealed.push(SealedSegment {
            segment: seg,
            tier: 0,
            start_global_id: seg_start,
        });
        let idx = self.sealed.len() - 1;
        if self.merge_policy.is_some() {
            self.apply_merge_policy();
        }
        Some(idx)
    }

    /// Add an externally-created (e.g. rebalancer) segment. Uses `global_id_map` for id lookup.
    pub fn add_sealed_segment(&mut self, seg: OnDiskSegment, tier: u32) {
        let (docs, tokens) = live_segment_totals(&seg);
        self.live_docs += docs;
        self.live_tokens += tokens;
        self.total_docs += seg.doc_count as u64;
        self.total_tokens += seg.doc_lengths.iter().map(|&l| l as u64).sum::<u64>();
        // start_global_id=0 is correct for merged segments that use global_id_map
        self.sealed.push(SealedSegment {
            segment: seg,
            tier,
            start_global_id: 0,
        });
    }

    /// Remove and return a sealed segment by index (used by the rebalancer for bulk segment moves).
    /// Returns the segment and its tier so the receiver can preserve tier information.
    pub fn remove_sealed_segment(&mut self, index: usize) -> Option<(OnDiskSegment, u32)> {
        if index >= self.sealed.len() {
            return None;
        }
        let removed = self.sealed.remove(index);
        let (docs, tokens) = live_segment_totals(&removed.segment);
        self.live_docs -= docs;
        self.live_tokens -= tokens;
        self.total_docs = self
            .total_docs
            .saturating_sub(removed.segment.doc_count as u64);
        self.total_tokens = self.total_tokens.saturating_sub(
            removed
                .segment
                .doc_lengths
                .iter()
                .map(|&l| l as u64)
                .sum::<u64>(),
        );
        Some((removed.segment, removed.tier))
    }

    /// Persist the entire index (active buffer is flushed first) into a directory.
    /// Layout:
    ///   manifest.json
    ///   seg-0000.bin
    ///   seg-0001.bin
    ///   ...
    ///   active.wal   (if a WAL was configured)
    pub fn save_to_dir(&mut self, dir: &Path) -> std::io::Result<()> {
        std::fs::create_dir_all(dir)?;

        // Make sure everything is in sealed segments for a clean snapshot.
        self.flush();

        let mut entries = Vec::new();
        for (i, sealed) in self.sealed.iter().enumerate() {
            let filename = format!("seg-{:04}.bin", i);
            let path = dir.join(&filename);
            sealed.segment.write_to_disk(&path)?;

            entries.push(SegmentEntry {
                filename,
                tier: sealed.tier,
                doc_count: sealed.segment.doc_count,
                start_global_id: sealed.start_global_id,
            });
        }

        let manifest = IndexManifest {
            version: MANIFEST_VERSION,
            segments: entries,
            total_docs: self.total_docs,
            total_tokens: self.total_tokens,
            global_doc_counter: self.global_doc_counter,
            active_start_global_id: self.active_start_global_id,
        };

        let manifest_json = serde_json::to_string_pretty(&manifest)?;
        std::fs::write(dir.join("manifest.json"), manifest_json)?;

        Ok(())
    }

    /// Load a previously saved `OnDiskIndex` from a directory created by `save_to_dir`.
    pub fn load_from_dir(dir: &Path) -> std::io::Result<Self> {
        let manifest_path = dir.join("manifest.json");
        let manifest_data = std::fs::read(manifest_path)?;
        let manifest: IndexManifest = serde_json::from_slice(&manifest_data)?;

        let mut idx = Self::new();
        idx.total_docs = manifest.total_docs;
        idx.total_tokens = manifest.total_tokens;
        idx.global_doc_counter = manifest.global_doc_counter;
        idx.active_start_global_id = manifest.active_start_global_id;

        // Reconstruct start_global_id for old manifests (serde default = 0).
        let mut cumulative: u32 = 0;
        for entry in manifest.segments {
            let seg_path = dir.join(&entry.filename);
            let segment = OnDiskSegment::load_from_disk(&seg_path)?;
            // For old manifests, start_global_id defaults to 0 in the entry.
            // Reconstruct it from cumulative doc counts only when global_doc_counter == 0
            // (which signals an old manifest with no counter info).
            let start_global_id = if manifest.global_doc_counter == 0 {
                cumulative
            } else {
                entry.start_global_id
            };
            cumulative += entry.doc_count;
            idx.sealed.push(SealedSegment {
                segment,
                tier: entry.tier,
                start_global_id,
            });
        }

        // For old manifests, reconstruct global_doc_counter from last segment
        if manifest.global_doc_counter == 0 && !idx.sealed.is_empty() {
            let last = idx.sealed.last().unwrap();
            idx.global_doc_counter = last.start_global_id + last.segment.doc_count;
            idx.active_start_global_id = idx.global_doc_counter;
        }

        // If an active.wal exists next to the manifest, replay it.
        let wal_path = dir.join("active.wal");
        if wal_path.exists() {
            let wal = SimpleWal::open_for_replay(&wal_path)?;
            let _ = wal.replay_into(&mut idx.active);
        }

        for sealed in &idx.sealed {
            let (docs, tokens) = live_segment_totals(&sealed.segment);
            idx.live_docs += docs;
            idx.live_tokens += tokens;
        }
        for local in 0..idx.active.doc_count() {
            if !idx.is_deleted_global(idx.active_start_global_id + local) {
                idx.live_docs += 1;
                idx.live_tokens += idx.active.doc_length(local) as u64;
            }
        }
        idx.global_doc_counter = idx
            .global_doc_counter
            .max(idx.active_start_global_id + idx.active.doc_count());
        Ok(idx)
    }

    /// Returns the number of sealed (immutable) segments currently held by this index.
    pub fn sealed_segment_count(&self) -> usize {
        self.sealed.len()
    }
    /// Returns the live document count (sealed + active buffer).
    pub fn approx_doc_count(&self) -> u64 {
        self.live_docs
    }

    /// Returns basic info about each sealed segment (useful for segment-aware rebalancing).
    pub fn sealed_segment_info(&self) -> Vec<SegmentInfo> {
        self.sealed
            .iter()
            .map(|s| {
                let live = s.segment.live_doc_count();
                SegmentInfo {
                    doc_count: s.segment.doc_count,
                    tier: s.tier,
                    live_doc_count: live,
                    size_bytes: s.segment.approx_size_bytes(),
                }
            })
            .collect()
    }

    /// Full compaction: merges all sealed segments into a single segment.
    ///
    /// More expensive than [`OnDiskIndex::vacuum`] but guarantees the smallest possible
    /// segment count. Call this when you want a one-time deep compaction rather than
    /// the incremental vacuum.
    pub fn compact(&mut self) {
        // Full compaction: merge *everything* into the smallest possible number of segments.
        // More expensive than `vacuum()`, but guarantees a single (or very few) segment(s).
        self.flush();
        if self.sealed.is_empty() {
            return;
        }
        let refs: Vec<(&OnDiskSegment, u32)> = self
            .sealed
            .iter()
            .map(|s| (&s.segment, s.start_global_id))
            .collect();
        let merged = merge_segments_with_offsets(&refs);
        self.sealed.clear();
        self.total_docs = 0;
        self.total_tokens = 0;
        self.live_docs = 0;
        self.live_tokens = 0;
        if merged.doc_count > 0 {
            self.add_sealed_segment(merged, 0);
            // Recompute totals after add_sealed_segment set them
            self.total_docs = self.sealed.iter().map(|s| s.segment.doc_count as u64).sum();
            self.total_tokens = self
                .sealed
                .iter()
                .flat_map(|s| s.segment.doc_lengths.iter().map(|&l| l as u64))
                .sum();
        }
    }

    /// Full vacuum / space reclamation for the on-disk index.
    ///
    /// Currently this is mostly a synonym for `compact()`, but it is the
    /// official extension point for more aggressive reclamation work:
    /// - WAL truncation after a full compaction
    /// - Dropping completely empty / tombstoned segments
    /// - Future: defragmentation, roaring bitmap compaction for deletes, etc.
    ///
    /// Call this (via `ShardedGraph::vacuum_deleted_nodes()` or directly)
    /// when you want to reclaim disk space after many deletes.
    pub fn vacuum(&mut self) {
        // Incremental, tombstone-aware vacuum.
        // Only rewrites segments whose delete ratio exceeds the threshold.
        // This is cheap when deletes are concentrated in a few segments (common case after bulk deletes or rebalancing).
        const DELETE_THRESHOLD: f32 = 0.05; // 5% — tune as needed

        let mut i = 0;
        while i < self.sealed.len() {
            let ratio = self.sealed[i].segment.delete_ratio();
            if ratio > DELETE_THRESHOLD {
                let start_gid = self.sealed[i].start_global_id;
                let old_seg = &self.sealed[i].segment;
                let cleaned = merge_segments_with_offsets(&[(old_seg, start_gid)]);

                if cleaned.doc_count == 0 {
                    // Segment became completely empty — drop it
                    self.sealed.remove(i);
                    continue;
                } else {
                    let tier = self.sealed[i].tier;
                    // After vacuum, global_id_map is authoritative (start_global_id = 0)
                    self.sealed[i] = SealedSegment {
                        segment: cleaned,
                        tier,
                        start_global_id: 0,
                    };
                }
            }
            i += 1;
        }

        // After rewriting dirty segments, give the tiered policy a chance
        // to merge any newly created small segments.
        self.apply_merge_policy();

        // Future: truncate WAL here if we have a clean state.
    }

    /// Runs one round of the configured tiered merge policy.
    ///
    /// Scans sealed segments grouped by tier; when a tier has at least
    /// `segments_per_tier` segments they are merged into a single segment
    /// promoted to the next tier. The loop repeats until no tier is over-full.
    /// Does nothing if no merge policy was set at construction time.
    pub fn apply_merge_policy(&mut self) {
        let policy = match &self.merge_policy {
            Some(p) => p.clone(),
            None => return,
        };
        loop {
            let mut tiers: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
            for (i, s) in self.sealed.iter().enumerate() {
                tiers.entry(s.tier).or_default().push(i);
            }
            let mut did = false;
            for (&tier, inds) in &tiers {
                if inds.len() >= policy.segments_per_tier {
                    let to_take: Vec<usize> = inds
                        .iter()
                        .take(policy.segments_per_tier)
                        .copied()
                        .collect();
                    let refs: Vec<(&OnDiskSegment, u32)> = to_take
                        .iter()
                        .map(|&i| (&self.sealed[i].segment, self.sealed[i].start_global_id))
                        .collect();
                    let m = merge_segments_with_offsets(&refs);
                    if m.doc_count == 0 {
                        continue;
                    }
                    let mut rm = to_take;
                    rm.sort_by(|a, b| b.cmp(a));
                    for i in rm {
                        self.sealed.remove(i);
                    }
                    // Merged segment uses global_id_map — start_global_id=0
                    self.sealed.push(SealedSegment {
                        segment: m,
                        tier: tier + 1,
                        start_global_id: 0,
                    });
                    did = true;
                    break;
                }
            }
            if !did {
                break;
            }
        }
    }

    /// BM25 search across all sealed segments and the active buffer.
    /// Returns `(global_doc_id, score)` pairs sorted by score descending.
    pub fn search(&self, terms: &[&str]) -> Vec<(u32, f32)> {
        const K1: f32 = 1.2;
        const B: f32 = 0.75;

        let n = self.live_docs as f32;
        if n == 0.0 || terms.is_empty() {
            return vec![];
        }
        let avg_dl = (self.live_tokens as f32 / n).max(1.0);
        let mut scores: HashMap<u32, f32> = HashMap::new();
        for &term in terms {
            // Decode each matching posting list once. IDF must use the whole
            // live corpus, regardless of buffer/flush/merge boundaries.
            let sealed_posts: Vec<_> = self
                .sealed
                .iter()
                .map(|sealed| {
                    (
                        sealed,
                        sealed.segment.get_postings(term).unwrap_or_default(),
                    )
                })
                .collect();
            let active_posts = self.active.get_live_postings(term);
            let df = (active_posts.len() + sealed_posts.iter().map(|(_, p)| p.len()).sum::<usize>())
                as f32;
            let idf = ((n - df + 0.5) / (df + 0.5) + 1.0).ln().max(0.0);
            let mut score = |global_id, posting: &Posting, dl: u32| {
                let tf = posting.term_freq as f32;
                let tf_norm = (tf * (K1 + 1.0)) / (tf + K1 * (1.0 - B + B * dl as f32 / avg_dl));
                *scores.entry(global_id).or_default() += idf * tf_norm;
            };
            for (sealed, postings) in &sealed_posts {
                for posting in postings {
                    score(
                        sealed
                            .segment
                            .get_global_id(sealed.start_global_id, posting.doc_id),
                        posting,
                        sealed.segment.doc_length(posting.doc_id),
                    );
                }
            }
            for posting in &active_posts {
                score(
                    self.active_start_global_id + posting.doc_id,
                    posting,
                    self.active.doc_length(posting.doc_id),
                );
            }
        }

        let mut results: Vec<(u32, f32)> = scores.into_iter().collect();
        results.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        results
    }

    /// Phrase / proximity search using per-term position lists.
    pub fn phrase_search(&self, terms: &[&str], max_gap: u32) -> Vec<(u32, f32)> {
        if terms.is_empty() {
            return vec![];
        }
        if terms.len() == 1 {
            return self.search(terms);
        }

        // Collect per-term positions per global doc id
        let mut doc_positions: HashMap<u32, Vec<Vec<u32>>> = HashMap::new();

        for sealed in &self.sealed {
            for (term_idx, &term) in terms.iter().enumerate() {
                if let Some(postings) = sealed.segment.get_postings(term) {
                    for posting in postings {
                        let global_id = sealed
                            .segment
                            .get_global_id(sealed.start_global_id, posting.doc_id);
                        let entry = doc_positions
                            .entry(global_id)
                            .or_insert_with(|| vec![vec![]; terms.len()]);
                        entry[term_idx].extend_from_slice(&posting.positions);
                    }
                }
            }
        }
        for (term_idx, &term) in terms.iter().enumerate() {
            for posting in self.active.get_live_postings(term) {
                let global_id = self.active_start_global_id + posting.doc_id;
                let entry = doc_positions
                    .entry(global_id)
                    .or_insert_with(|| vec![vec![]; terms.len()]);
                entry[term_idx].extend_from_slice(&posting.positions);
            }
        }

        // BM25 baseline for blending
        let bm25_map: HashMap<u32, f32> = self.search(terms).into_iter().collect();

        let mut results = Vec::new();
        for (global_id, term_positions) in doc_positions {
            if term_positions.iter().any(|p| p.is_empty()) {
                continue; // document doesn't contain all terms
            }
            let proximity = compute_ondisk_proximity(&term_positions, max_gap);
            if proximity > 0.0 {
                let bm25 = bm25_map.get(&global_id).copied().unwrap_or(0.1);
                results.push((global_id, bm25 * (0.4 + 0.6 * proximity)));
            }
        }
        results.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        results
    }
}

fn live_segment_totals(segment: &OnDiskSegment) -> (u64, u64) {
    let mut docs = 0;
    let mut tokens = 0;
    for local in 0..segment.doc_count {
        if !segment.is_deleted(local) {
            docs += 1;
            tokens += segment.doc_length(local) as u64;
        }
    }
    (docs, tokens)
}

fn compute_ondisk_proximity(term_positions: &[Vec<u32>], max_gap: u32) -> f32 {
    let positions: Vec<&[u32]> = term_positions.iter().map(Vec::as_slice).collect();
    crate::index::compute_proximity_score(&positions, max_gap) as f32
}

/// Real merge of one or more segments.
/// - Filters out deleted documents (tombstones) from each input segment.
/// - Remaps live document IDs to a dense 0..live_count range.
/// - Rebuilds a single clean `OnDiskSegment` with merged term dictionary and postings.
///
/// This is the core primitive used by both full `compact()` and incremental `vacuum()`.
pub fn merge_segments(segs: &[&OnDiskSegment]) -> OnDiskSegment {
    if segs.is_empty() {
        return OnDiskSegment {
            doc_count: 0,
            deleted: vec![],
            terms: vec![],
            postings_blob: vec![],
            doc_lengths: vec![],
            global_id_map: vec![],
        };
    }

    // First pass: compute per-segment old_doc -> new_live_id remapping and total live count.
    let mut remaps: Vec<Vec<Option<LocalDocId>>> = Vec::with_capacity(segs.len());
    let mut global_live: LocalDocId = 0;

    for seg in segs {
        let mut map = vec![None; seg.doc_count as usize];
        for old_id in 0..seg.doc_count {
            if !seg.is_deleted(old_id) {
                map[old_id as usize] = Some(global_live);
                global_live += 1;
            }
        }
        remaps.push(map);
    }

    // Collect live doc_lengths while we are at it (remapped)
    let mut new_doc_lengths = vec![0u32; global_live as usize];
    for (sidx, seg) in segs.iter().enumerate() {
        for old_id in 0..seg.doc_count {
            if let Some(&new_id) = remaps[sidx].get(old_id as usize).and_then(|x| x.as_ref()) {
                if (old_id as usize) < seg.doc_lengths.len() {
                    new_doc_lengths[new_id as usize] = seg.doc_lengths[old_id as usize];
                }
            }
        }
    }

    // Second pass: aggregate postings per term, remapping doc_ids on the fly.
    let mut term_postings: BTreeMap<String, Vec<Posting>> = BTreeMap::new();

    for (sidx, seg) in segs.iter().enumerate() {
        for tmeta in &seg.terms {
            let start = tmeta.posting_offset as usize;
            let end = start + tmeta.posting_len as usize;
            if end > seg.postings_blob.len() {
                continue;
            }

            if let Ok(posts) = bincode::deserialize::<Vec<Posting>>(&seg.postings_blob[start..end])
            {
                for mut p in posts {
                    if let Some(&new_id) =
                        remaps[sidx].get(p.doc_id as usize).and_then(|x| x.as_ref())
                    {
                        p.doc_id = new_id;
                        term_postings.entry(tmeta.term.clone()).or_default().push(p);
                    }
                }
            }
        }
    }

    // Build final term list + postings blob (sorted for determinism)
    let mut new_terms: Vec<TermMeta> = Vec::new();
    let mut new_blob: Vec<u8> = Vec::new();

    for (term, mut posts) in term_postings {
        posts.sort_by_key(|p| p.doc_id);
        let df = posts.len() as u32;
        let serialized = bincode::serialize(&posts).expect("serialize postings");
        let offset = new_blob.len() as u32;
        let len = serialized.len() as u32;
        new_blob.extend(serialized);

        new_terms.push(TermMeta {
            term,
            df,
            posting_offset: offset,
            posting_len: len,
        });
    }

    let new_deleted = if global_live > 0 {
        vec![0u8; (global_live as usize).div_ceil(8)]
    } else {
        vec![]
    };

    OnDiskSegment {
        doc_count: global_live,
        deleted: new_deleted,
        terms: new_terms,
        postings_blob: new_blob,
        doc_lengths: new_doc_lengths,
        global_id_map: vec![], // old merge_segments does not preserve global ids
    }
}

/// Handle to the background merge/vacuum worker thread.
///
/// Drop this handle (or call [`MergeWorkerHandle::shutdown`]) to stop the worker.
pub struct MergeWorkerHandle {
    tx: mpsc::Sender<MergeCommand>,
    join: Option<thread::JoinHandle<()>>,
    stats: Arc<Mutex<MergeStats>>,
}

impl std::fmt::Debug for MergeWorkerHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MergeWorkerHandle")
            .field("stats", &self.stats.lock().ok())
            .finish_non_exhaustive()
    }
}
/// Commands that can be sent to a running [`MergeWorkerHandle`].
#[derive(Debug, Clone, Copy)]
pub enum MergeCommand {
    /// Trigger an immediate vacuum/merge cycle without waiting for the next interval.
    MergeNow,
    /// Instruct the worker thread to stop after the current cycle completes.
    Shutdown,
}
/// Metrics collected by the background merge worker.
#[derive(Debug, Default, Clone)]
pub struct MergeStats {
    /// Total number of vacuum/merge cycles completed since the worker started.
    pub merges_performed: u64,
    /// Wall-clock time of the most recent vacuum/merge cycle, in milliseconds.
    pub last_merge_duration_ms: u64,
    /// If the most recent cycle produced an error, its description is stored here.
    pub last_error: Option<String>,
}

/// Spawns a background thread that calls [`OnDiskIndex::vacuum`] on `index`
/// every `interval`, and whenever a [`MergeCommand::MergeNow`] message is sent.
///
/// Returns a [`MergeWorkerHandle`] — keep it alive for as long as merging should run.
/// Dropping the handle or calling [`MergeWorkerHandle::shutdown`] stops the thread.
pub fn start_merge_worker(index: Arc<Mutex<OnDiskIndex>>, interval: Duration) -> MergeWorkerHandle {
    let (tx, rx) = mpsc::channel();
    let stats = Arc::new(Mutex::new(MergeStats::default()));
    let stats2 = stats.clone();
    let h = thread::spawn(move || loop {
        match rx.recv_timeout(interval) {
            Ok(MergeCommand::Shutdown) => break,
            Ok(MergeCommand::MergeNow) | Err(_) => {}
        }
        if let Ok(mut g) = index.lock() {
            let t0 = std::time::Instant::now();
            g.vacuum();
            let elapsed = t0.elapsed().as_millis() as u64;
            if let Ok(mut s) = stats2.lock() {
                s.merges_performed += 1;
                s.last_merge_duration_ms = elapsed;
            }
        }
    });
    MergeWorkerHandle {
        tx,
        join: Some(h),
        stats,
    }
}

impl MergeWorkerHandle {
    /// Sends a shutdown signal to the background worker thread and waits for it to finish.
    ///
    /// Returns the final [`MergeStats`] collected by the worker.
    pub fn shutdown(mut self) -> MergeStats {
        let _ = self.tx.send(MergeCommand::Shutdown);
        if let Some(j) = self.join.take() {
            let _ = j.join();
        }
        self.stats.lock().map(|g| g.clone()).unwrap_or_default()
    }
}

/// Merge multiple segments into one, compacting away deleted documents.
///
/// This is a thin wrapper around the internal `merge_segments` helper.
/// The resulting segment does **not** preserve original global doc IDs —
/// use [`merge_segments_with_offsets`] when you need to maintain the
/// global-id mapping for live search.
pub fn merge_segments_full(segs: &[&OnDiskSegment]) -> OnDiskSegment {
    merge_segments(segs)
}

/// Merge segments while preserving the original global doc ids in `global_id_map`.
/// `segs` is a slice of `(segment_ref, start_global_id)`.
/// Deleted documents are dropped; live ones are renumbered locally, with
/// `global_id_map[new_local_id] = original_global_id` stored in the result.
pub fn merge_segments_with_offsets(segs: &[(&OnDiskSegment, u32)]) -> OnDiskSegment {
    if segs.is_empty() {
        return OnDiskSegment {
            doc_count: 0,
            deleted: vec![],
            terms: vec![],
            postings_blob: vec![],
            doc_lengths: vec![],
            global_id_map: vec![],
        };
    }

    // First pass: build per-segment old_local → new_local remap + global_id_map
    let mut remaps: Vec<Vec<Option<u32>>> = Vec::with_capacity(segs.len());
    let mut global_live: u32 = 0;
    let mut global_id_map: Vec<u32> = Vec::new();

    for (seg, start_gid) in segs {
        let mut map = vec![None; seg.doc_count as usize];
        for old_id in 0..seg.doc_count {
            if !seg.is_deleted(old_id) {
                let original_global_id = seg.get_global_id(*start_gid, old_id);
                map[old_id as usize] = Some(global_live);
                global_id_map.push(original_global_id);
                global_live += 1;
            }
        }
        remaps.push(map);
    }

    // Rebuild doc_lengths with remapped ids
    let mut new_doc_lengths = vec![0u32; global_live as usize];
    for (sidx, (seg, _)) in segs.iter().enumerate() {
        for old_id in 0..seg.doc_count {
            if let Some(new_id) = remaps[sidx][old_id as usize] {
                if (old_id as usize) < seg.doc_lengths.len() {
                    new_doc_lengths[new_id as usize] = seg.doc_lengths[old_id as usize];
                }
            }
        }
    }

    // Aggregate postings per term, remapping doc_ids on the fly
    let mut term_postings: BTreeMap<String, Vec<Posting>> = BTreeMap::new();
    for (sidx, (seg, _)) in segs.iter().enumerate() {
        for tmeta in &seg.terms {
            let start = tmeta.posting_offset as usize;
            let end = start + tmeta.posting_len as usize;
            if end > seg.postings_blob.len() {
                continue;
            }
            if let Ok(posts) = bincode::deserialize::<Vec<Posting>>(&seg.postings_blob[start..end])
            {
                for mut p in posts {
                    if let Some(new_id) = remaps[sidx].get(p.doc_id as usize).and_then(|x| *x) {
                        p.doc_id = new_id;
                        term_postings.entry(tmeta.term.clone()).or_default().push(p);
                    }
                }
            }
        }
    }

    let mut new_terms: Vec<TermMeta> = Vec::new();
    let mut new_blob: Vec<u8> = Vec::new();
    for (term, mut posts) in term_postings {
        posts.sort_by_key(|p| p.doc_id);
        let df = posts.len() as u32;
        let serialized = bincode::serialize(&posts).expect("serialize postings");
        let offset = new_blob.len() as u32;
        let len = serialized.len() as u32;
        new_blob.extend(serialized);
        new_terms.push(TermMeta {
            term,
            df,
            posting_offset: offset,
            posting_len: len,
        });
    }

    let new_deleted = if global_live > 0 {
        vec![0u8; (global_live as usize).div_ceil(8)]
    } else {
        vec![]
    };

    OnDiskSegment {
        doc_count: global_live,
        deleted: new_deleted,
        terms: new_terms,
        postings_blob: new_blob,
        doc_lengths: new_doc_lengths,
        global_id_map,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn search_finds_indexed_documents() {
        let mut idx = OnDiskIndex::new();
        let id0 = idx.add_document(
            &["rust", "ownership", "memory"],
            &[vec![0], vec![1], vec![2]],
        );
        let id1 = idx.add_document(
            &["python", "garbage", "collection"],
            &[vec![0], vec![1], vec![2]],
        );
        let id2 = idx.add_document(&["rust", "borrow", "checker"], &[vec![0], vec![1], vec![2]]);

        let results = idx.search(&["rust"]);
        assert!(
            !results.is_empty(),
            "should find rust docs in active buffer"
        );
        let ids: Vec<u32> = results.iter().map(|(id, _)| *id).collect();
        assert!(ids.contains(&id0), "id0 should appear");
        assert!(ids.contains(&id2), "id2 should appear");
        assert!(!ids.contains(&id1), "id1 should NOT appear for 'rust'");
    }

    #[test]
    fn search_survives_flush() {
        let mut idx = OnDiskIndex::new();
        let id0 = idx.add_document(&["alpha", "beta"], &[vec![0], vec![1]]);
        let id1 = idx.add_document(&["gamma", "delta"], &[vec![0], vec![1]]);
        idx.flush();
        let id2 = idx.add_document(&["alpha", "epsilon"], &[vec![0], vec![1]]);

        let results = idx.search(&["alpha"]);
        let ids: Vec<u32> = results.iter().map(|(id, _)| *id).collect();
        assert!(ids.contains(&id0), "id0 (pre-flush) should be found");
        assert!(ids.contains(&id2), "id2 (post-flush) should be found");
        assert!(!ids.contains(&id1), "id1 should not appear for 'alpha'");
    }

    #[test]
    fn global_ids_are_unique_across_flushes() {
        let mut idx = OnDiskIndex::new();
        let a = idx.add_document(&["term"], &[vec![0]]);
        idx.flush();
        let b = idx.add_document(&["term"], &[vec![0]]);
        idx.flush();
        let c = idx.add_document(&["term"], &[vec![0]]);
        assert_ne!(a, b);
        assert_ne!(b, c);
        assert_ne!(a, c);
    }

    #[test]
    fn delete_hides_document_from_search() {
        let mut idx = OnDiskIndex::new();
        let id0 = idx.add_document(&["hello", "world"], &[vec![0], vec![1]]);
        let id1 = idx.add_document(&["hello", "rust"], &[vec![0], vec![1]]);
        idx.flush();
        idx.delete(id0);

        let results = idx.search(&["hello"]);
        let ids: Vec<u32> = results.iter().map(|(id, _)| *id).collect();
        assert!(!ids.contains(&id0), "id0 should be deleted");
        assert!(ids.contains(&id1), "id1 should still appear");
    }

    #[test]
    fn term_freq_reflects_actual_frequency() {
        let mut idx = OnDiskIndex::new();
        // "rust" appears 3 times in doc0, once in doc1
        let id0 = idx.add_document(
            &["rust", "rust", "rust", "programming"],
            &[vec![0], vec![1], vec![2], vec![3]],
        );
        let id1 = idx.add_document(&["rust", "programming"], &[vec![0], vec![1]]);
        let results = idx.search(&["rust"]);
        let score0 = results
            .iter()
            .find(|(id, _)| *id == id0)
            .map(|(_, s)| *s)
            .unwrap_or(0.0);
        let score1 = results
            .iter()
            .find(|(id, _)| *id == id1)
            .map(|(_, s)| *s)
            .unwrap_or(0.0);
        assert!(
            score0 > score1,
            "doc with 3x 'rust' (score={}) should score higher than 1x 'rust' (score={})",
            score0,
            score1
        );
    }

    #[test]
    fn search_roundtrips_through_save_load() {
        use tempfile::tempdir;
        let mut idx = OnDiskIndex::new();
        let id0 = idx.add_document(&["knowledge", "graph"], &[vec![0], vec![1]]);
        let id1 = idx.add_document(&["text", "search"], &[vec![0], vec![1]]);

        let dir = tempdir().unwrap();
        idx.save_to_dir(dir.path()).unwrap();

        let loaded = OnDiskIndex::load_from_dir(dir.path()).unwrap();
        let results = loaded.search(&["knowledge"]);
        assert!(!results.is_empty(), "should find docs after save/load");
        let ids: Vec<u32> = results.iter().map(|(id, _)| *id).collect();
        assert!(ids.contains(&id0));
        assert!(!ids.contains(&id1));
    }
}
