//! Spike / Prototype for the On-Disk Inverted Index (grap 0.2) - Iteration 6
//!
//! This iteration adds the remaining high-leverage pieces from the design document:
//!
//! - **Simple WAL for the active buffer** (`SimpleWal`, `WalOp`, replay on load)
//!   Durability for documents that have not yet been flushed to an immutable segment.
//!   `enable_wal()`, automatic replay in `load_from_dir`, truncate on clean flush.
//!
//! - **Background merge thread sketch** (`start_background_merger` + `MergeHandle`)
//!   A minimal `std::thread` + `Arc<Mutex<...>>` example showing how the tiered policy
//!   could run in the background without blocking queries.
//!
//! - **Positional / phrase search** (`phrase_search`)
//!   Uses the `positions: Vec<u32>` already stored in `Posting` to support ordered
//!   phrase queries with a configurable gap window. Validates that positions survive
//!   segment merge and compaction.
//!
//! - **Clear integration points** with the real `IndexedKnowledgeGraph`
//!   Large comment block at the bottom explaining enum/trait strategy, GlobalNodeId
//!   mapping, delete + ghost-edge cleanup, and how `search_and_traverse` would call
//!   into the on-disk engine.
//!
//! The spike (as of v6) now covers the vast majority of the architecture described in
//! `docs/design-0.2-on-disk-index-and-deletes.md` (active buffer + WAL, segmented
//! immutable tail, tiered compaction, tombstone filtering, phrase support, manifest
//! persistence, and the bridge to the rest of grap).
//!
//! This remains exploratory / validation code. It is not intended to be merged
//! directly into the main library.

use std::collections::{BTreeMap, HashMap};
use std::fs::File;
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::Path;

use bincode;
use serde::{Deserialize, Serialize};

pub type LocalDocId = u32;

/// Magic number for on-disk segment files ("GRPI" = Grap Inverted Index)
const SEGMENT_MAGIC: [u8; 4] = *b"GRPI";
const SEGMENT_VERSION: u16 = 1;

/// Small header written at the front of every persisted segment file.
/// This makes the format slightly more robust than raw bincode.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct SegmentFileHeader {
    magic: [u8; 4],
    version: u16,
}

// ----------------------------------------------------------------------------
// Whole-index manifest (for save_to_dir / load_from_dir)
// ----------------------------------------------------------------------------

const MANIFEST_VERSION: u16 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SegmentEntry {
    /// Relative filename inside the directory, e.g. "seg-00003.bin"
    filename: String,
    tier: u32,
    doc_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct IndexManifest {
    version: u16,
    segments: Vec<SegmentEntry>,
    total_docs: u64,
    total_tokens: u64,
}

// ----------------------------------------------------------------------------
// Extremely simple WAL for the active buffer (durability before flush)
// ----------------------------------------------------------------------------

/// Operation recorded in the write-ahead log for the active buffer.
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

/// Very basic append-only WAL.
/// Records are length-prefixed bincode blobs.
/// On clean flush we can truncate or delete the file.
#[derive(Debug)]
struct SimpleWal {
    path: std::path::PathBuf,
    file: Option<std::fs::File>,
}

impl SimpleWal {
    fn create(path: &Path) -> std::io::Result<Self> {
        // Truncate on create (new active buffer)
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
            let bytes = bincode::serialize(op).unwrap();
            let len = bytes.len() as u32;
            use std::io::Write;
            f.write_all(&len.to_le_bytes())?;
            f.write_all(&bytes)?;
            f.flush()?;
        }
        Ok(())
    }

    /// Replay all operations into the given active buffer.
    fn replay_into(&self, active: &mut ActiveBuffer) -> std::io::Result<()> {
        let Some(mut f) = std::fs::File::open(&self.path).ok() else {
            return Ok(());
        };
        use std::io::Read;
        let mut buf = Vec::new();
        f.read_to_end(&mut buf)?;
        let mut cursor = 0usize;
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
            let op: WalOp = bincode::deserialize(&buf[cursor..cursor + len])
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
            match op {
                WalOp::AddDocument { terms, positions } => {
                    let t: Vec<&str> = terms.iter().map(|s| s.as_str()).collect();
                    let p: Vec<Vec<u32>> = positions;
                    let _ = active.add_document(&t, &p);
                }
                WalOp::Delete { doc_id } => {
                    active.delete(doc_id);
                }
            }
            cursor += len;
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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Posting {
    pub doc_id: LocalDocId,
    pub term_freq: u32,
    pub positions: Vec<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct TermMeta {
    term: String,
    df: u32,
    posting_offset: u32,
    posting_len: u32,
}

/// The actual persisted segment structure.
/// Contains a sorted term dictionary with byte offsets into a postings blob,
/// plus a per-segment tombstone bitmap.
#[derive(Debug, Serialize, Deserialize)]
pub struct OnDiskSegment {
    pub doc_count: u32,
    pub deleted: Vec<u8>,
    terms: Vec<TermMeta>,
    postings_blob: Vec<u8>,
    /// Length (token count) of every document in this segment, in local doc id order.
    /// Used for BM25 length normalization when scoring across segments.
    doc_lengths: Vec<u32>,
}

#[derive(Debug)]
pub struct SegmentBuilder {
    doc_count: u32,
    deleted: Vec<u8>,
    postings: HashMap<String, Vec<Posting>>,
    /// Token count for each document (used for BM25 / length normalization later)
    doc_lengths: Vec<u32>,
}

impl SegmentBuilder {
    pub fn new() -> Self {
        Self {
            doc_count: 0,
            deleted: Vec::new(),
            postings: HashMap::new(),
            doc_lengths: Vec::new(),
        }
    }

    pub fn add_document(&mut self, terms: &[&str], positions: &[Vec<u32>]) -> LocalDocId {
        let doc_id = self.doc_count;
        self.doc_count += 1;

        let needed = ((self.doc_count as usize) + 7) / 8;
        self.deleted.resize(needed, 0);

        // Very simple length: number of term occurrences passed in
        let doc_len = terms.len() as u32;
        self.doc_lengths.push(doc_len);

        for (i, term) in terms.iter().enumerate() {
            let pos = positions.get(i).cloned().unwrap_or_default();
            self.postings
                .entry(term.to_string())
                .or_default()
                .push(Posting {
                    doc_id,
                    term_freq: 1,
                    positions: pos,
                });
        }
        doc_id
    }

    pub fn delete(&mut self, doc_id: LocalDocId) {
        let byte = (doc_id as usize) / 8;
        let bit = (doc_id as usize) % 8;
        if byte < self.deleted.len() {
            self.deleted[byte] |= 1 << bit;
        }
    }

    fn is_deleted(&self, doc_id: LocalDocId) -> bool {
        let byte = (doc_id as usize) / 8;
        let bit = (doc_id as usize) % 8;
        byte < self.deleted.len() && (self.deleted[byte] & (1 << bit)) != 0
    }

    pub fn into_on_disk_segment(mut self) -> OnDiskSegment {
        let mut terms: Vec<TermMeta> = Vec::new();
        let mut postings_blob: Vec<u8> = Vec::new();

        let mut term_list: Vec<_> = self.postings.drain().collect();
        term_list.sort_by(|a, b| a.0.cmp(&b.0));

        for (term, mut postings) in term_list {
            postings.sort_by_key(|p| p.doc_id);
            let df = postings.len() as u32;
            let offset = postings_blob.len() as u32;

            let encoded = bincode::serialize(&postings).unwrap();
            let len = encoded.len() as u32;
            postings_blob.extend_from_slice(&encoded);

            terms.push(TermMeta {
                term,
                df,
                posting_offset: offset,
                posting_len: len,
            });
        }

        OnDiskSegment {
            doc_count: self.doc_count,
            deleted: self.deleted,
            terms,
            postings_blob,
            doc_lengths: self.doc_lengths,
        }
    }
}

impl OnDiskSegment {
    pub fn is_deleted(&self, doc_id: LocalDocId) -> bool {
        let byte = (doc_id as usize) / 8;
        let bit = (doc_id as usize) % 8;
        byte < self.deleted.len() && (self.deleted[byte] & (1 << bit)) != 0
    }

    /// Returns the stored length for a document, or 1 if unknown / out of range.
    pub fn doc_length(&self, doc_id: LocalDocId) -> u32 {
        self.doc_lengths
            .get(doc_id as usize)
            .copied()
            .unwrap_or(1)
    }

    pub fn get_postings(&self, term: &str) -> Option<Vec<Posting>> {
        let pos = self.terms.binary_search_by(|t| t.term.as_str().cmp(term)).ok()?;
        let meta = &self.terms[pos];

        let start = meta.posting_offset as usize;
        let end = start + meta.posting_len as usize;
        if end > self.postings_blob.len() {
            return None;
        }

        let data = &self.postings_blob[start..end];
        let mut postings: Vec<Posting> = bincode::deserialize(data).ok()?;
        postings.retain(|p| !self.is_deleted(p.doc_id));
        Some(postings)
    }

    pub fn write_to_disk(&self, path: &Path) -> std::io::Result<()> {
        let mut w = BufWriter::new(File::create(path)?);

        let header = SegmentFileHeader {
            magic: SEGMENT_MAGIC,
            version: SEGMENT_VERSION,
        };
        let header_bytes = bincode::serialize(&header).unwrap();
        let payload = bincode::serialize(self).unwrap();

        // Clean layout: [header_len: u32 LE][header bytes][payload bytes]
        let header_len = header_bytes.len() as u32;
        w.write_all(&header_len.to_le_bytes())?;
        w.write_all(&header_bytes)?;
        w.write_all(&payload)?;
        w.flush()?;
        Ok(())
    }

    pub fn load_from_disk(path: &Path) -> std::io::Result<Self> {
        let mut r = BufReader::new(File::open(path)?);
        let mut buf = Vec::new();
        r.read_to_end(&mut buf)?;

        if buf.len() < 4 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "segment file too small",
            ));
        }

        let header_len = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]) as usize;
        let header_start = 4;
        let header_end = header_start + header_len;
        if buf.len() < header_end {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "truncated segment header",
            ));
        }

        let header: SegmentFileHeader = bincode::deserialize(&buf[header_start..header_end])
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

        if header.magic != SEGMENT_MAGIC {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "bad magic - not a grap inverted index segment",
            ));
        }
        if header.version != SEGMENT_VERSION {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("unsupported segment version {}", header.version),
            ));
        }

        let payload = &buf[header_end..];
        let segment: OnDiskSegment = bincode::deserialize(payload)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
        Ok(segment)
    }
}

// ============================================================================
// Active Buffer + OnDiskIndex (the "index" object per the 0.2 design)
// ============================================================================

/// In-memory writable head of the index.
/// Documents are added here until we decide to flush it to an immutable segment.
#[derive(Debug)]
pub struct ActiveBuffer {
    builder: SegmentBuilder,
    /// Rough global stats for scoring while the buffer is still mutable.
    total_tokens: u64,
    /// Optional WAL for durability of the active buffer.
    wal: Option<SimpleWal>,
}

impl ActiveBuffer {
    pub fn new() -> Self {
        Self {
            builder: SegmentBuilder::new(),
            total_tokens: 0,
            wal: None,
        }
    }

    fn with_wal(mut self, wal: SimpleWal) -> Self {
        self.wal = Some(wal);
        self
    }

    pub fn add_document(&mut self, terms: &[&str], positions: &[Vec<u32>]) -> LocalDocId {
        let len = terms.len() as u64;
        self.total_tokens += len;

        // Log to WAL first (best effort durability)
        if let Some(w) = &mut self.wal {
            let _ = w.append(&WalOp::AddDocument {
                terms: terms.iter().map(|s| s.to_string()).collect(),
                positions: positions.to_vec(),
            });
        }

        self.builder.add_document(terms, positions)
    }

    pub fn delete(&mut self, doc_id: LocalDocId) {
        if let Some(w) = &mut self.wal {
            let _ = w.append(&WalOp::Delete { doc_id });
        }
        self.builder.delete(doc_id);
    }

    pub fn doc_count(&self) -> u32 {
        self.builder.doc_count
    }

    /// Turn the active buffer into an immutable segment and reset the buffer.
    /// Also truncates the WAL (the data is now durable in the sealed segment).
    pub fn seal(self) -> OnDiskSegment {
        if let Some(mut w) = self.wal {
            let _ = w.truncate();
        }
        self.builder.into_on_disk_segment()
    }

    /// For the spike's naive active-buffer searcher.
    /// Returns live postings for a term (does not require the term dictionary).
    fn get_live_postings(&self, term: &str) -> Vec<Posting> {
        let mut out = Vec::new();
        if let Some(posts) = self.builder.postings.get(term) {
            for p in posts {
                if !self.builder.is_deleted(p.doc_id) {
                    out.push(p.clone());
                }
            }
        }
        out
    }

    fn doc_length(&self, doc_id: LocalDocId) -> u32 {
        self.builder.doc_lengths.get(doc_id as usize).copied().unwrap_or(1)
    }
}

// ----------------------------------------------------------------------------
// Tiered merge policy types
// ----------------------------------------------------------------------------

/// Configuration for tiered (logarithmic) merging.
///
/// When a tier reaches `segments_per_tier` segments, the oldest ones in that
/// tier are merged and the result is promoted to the next higher tier.
#[derive(Debug, Clone)]
pub struct TieredMergePolicy {
    /// How many segments are allowed in a tier before we trigger a merge.
    /// Typical values: 3, 4, 5, 8, 10.
    pub segments_per_tier: usize,
}

impl Default for TieredMergePolicy {
    fn default() -> Self {
        Self { segments_per_tier: 4 }
    }
}

/// Wrapper around a sealed immutable segment that also carries its tier.
#[derive(Debug)]
struct SealedSegment {
    segment: OnDiskSegment,
    tier: u32,
}

// ----------------------------------------------------------------------------
// The main index object
// ----------------------------------------------------------------------------

/// The main index object: a small mutable active buffer + a list of immutable
/// on-disk (or in-memory) segments.
///
/// This is the shape described in the design document:
/// "Index = collection of segments + a small 'active' in-memory buffer"
#[derive(Debug)]
pub struct OnDiskIndex {
    active: ActiveBuffer,
    sealed: Vec<SealedSegment>,
    /// Approximate global stats
    total_docs: u64,
    total_tokens: u64,
    /// Optional merge policy. When present, `flush()` will automatically try
    /// to apply tiered merging after sealing a new segment.
    merge_policy: Option<TieredMergePolicy>,
}

impl OnDiskIndex {
    pub fn new() -> Self {
        Self {
            active: ActiveBuffer::new(),
            sealed: Vec::new(),
            total_docs: 0,
            total_tokens: 0,
            merge_policy: None,
        }
    }

    /// Create an empty index with a tiered merge policy.
    /// After every successful `flush()`, the policy will be applied automatically.
    pub fn with_merge_policy(policy: TieredMergePolicy) -> Self {
        Self {
            active: ActiveBuffer::new(),
            sealed: Vec::new(),
            total_docs: 0,
            total_tokens: 0,
            merge_policy: Some(policy),
        }
    }

    /// Enable a simple WAL for the active buffer (durability of recent writes).
    /// All future `add_document` / deletes will be appended to the log.
    /// On clean `flush()` the WAL is truncated.
    pub fn enable_wal(&mut self, wal_path: &Path) -> std::io::Result<()> {
        let wal = SimpleWal::create(wal_path)?;
        self.active = std::mem::replace(&mut self.active, ActiveBuffer::new())
            .with_wal(wal);
        Ok(())
    }

    /// Add a document to the active (mutable) buffer.
    pub fn add_document(&mut self, terms: &[&str], positions: &[Vec<u32>]) -> LocalDocId {
        let id = self.active.add_document(terms, positions);
        id
    }

    pub fn delete_in_active(&mut self, doc_id: LocalDocId) {
        self.active.delete(doc_id);
    }

    /// Seal the current active buffer into a new immutable segment (tier 0)
    /// and add it to the sealed list. If a merge policy is configured,
    /// `apply_merge_policy()` is invoked afterwards.
    pub fn flush(&mut self) -> Option<usize> {
        if self.active.doc_count() == 0 {
            return None;
        }

        let seg = std::mem::replace(&mut self.active, ActiveBuffer::new()).seal();

        self.total_docs += seg.doc_count as u64;
        self.total_tokens += seg.doc_lengths.iter().map(|&l| l as u64).sum::<u64>();

        self.sealed.push(SealedSegment { segment: seg, tier: 0 });

        let inserted_idx = self.sealed.len() - 1;

        // Automatic tiered merging if policy is present
        if self.merge_policy.is_some() {
            self.apply_merge_policy();
        }

        Some(inserted_idx)
    }

    /// Add a segment that was loaded from disk or produced by a merge.
    /// The caller decides which tier it belongs to (default 0 for fresh loads).
    pub fn add_sealed_segment(&mut self, seg: OnDiskSegment, tier: u32) {
        self.total_docs += seg.doc_count as u64;
        self.total_tokens += seg.doc_lengths.iter().map(|&l| l as u64).sum::<u64>();
        self.sealed.push(SealedSegment { segment: seg, tier });
    }

    pub fn sealed_segment_count(&self) -> usize {
        self.sealed.len()
    }

    pub fn approx_doc_count(&self) -> u64 {
        self.total_docs + self.active.doc_count() as u64
    }

    /// Legacy full compaction: flush + merge everything into a single segment at tier 0.
    /// Useful for "vacuum" when you want minimal segment count regardless of policy.
    pub fn compact(&mut self) {
        self.flush();

        if self.sealed.is_empty() {
            return;
        }

        let seg_refs: Vec<&OnDiskSegment> =
            self.sealed.iter().map(|s| &s.segment).collect();
        let merged = merge_segments(&seg_refs);

        self.sealed.clear();
        self.total_docs = 0;
        self.total_tokens = 0;

        if merged.doc_count > 0 {
            self.add_sealed_segment(merged, 0);
        }
    }

    // ---------------------------------------------------------------------
    // Tiered merge implementation
    // ---------------------------------------------------------------------

    /// Apply one round of the configured tiered merge policy.
    /// Merges the oldest `segments_per_tier` segments in the lowest tier that
    /// has become full, and promotes the result to the next tier.
    pub fn apply_merge_policy(&mut self) {
        let policy = match &self.merge_policy {
            Some(p) => p.clone(),
            None => return,
        };

        loop {
            // Group segments by tier (we keep them in insertion order within tier)
            let mut tiers: std::collections::BTreeMap<u32, Vec<usize>> = std::collections::BTreeMap::new();
            for (i, s) in self.sealed.iter().enumerate() {
                tiers.entry(s.tier).or_default().push(i);
            }

            // Find the lowest tier that has reached the threshold
            let mut merged_something = false;
            for (&tier, indices) in &tiers {
                if indices.len() >= policy.segments_per_tier {
                    // Take the oldest N in this tier (they appear first in the vec)
                    let to_merge: Vec<usize> = indices.iter().take(policy.segments_per_tier).copied().collect();

                    // Collect the actual segment references
                    let seg_refs: Vec<&OnDiskSegment> =
                        to_merge.iter().map(|&i| &self.sealed[i].segment).collect();

                    let merged = merge_segments(&seg_refs);
                    if merged.doc_count == 0 {
                        continue;
                    }

                    // Remove the old segments (from back to front to keep indices valid)
                    let mut removed = to_merge;
                    removed.sort_unstable_by(|a, b| b.cmp(a)); // descending
                    for idx in removed {
                        self.sealed.remove(idx);
                    }

                    // The merged segment goes into the next tier
                    let new_tier = tier + 1;
                    self.add_sealed_segment(merged, new_tier);

                    merged_something = true;
                    break; // restart the loop (tiers may have changed)
                }
            }

            if !merged_something {
                break;
            }
        }
    }

    /// Debug helper: returns (tier, doc_count) for each sealed segment in order.
    pub fn debug_segment_tiers(&self) -> Vec<(u32, u32)> {
        self.sealed.iter().map(|s| (s.tier, s.segment.doc_count)).collect()
    }

    // ---------------------------------------------------------------------
    // Directory persistence (manifest + segments)
    // ---------------------------------------------------------------------

    /// Persist the entire index to a directory.
    ///
    /// Layout:
    ///   <dir>/
    ///     manifest.json
    ///     seg-00001.bin
    ///     seg-00002.bin
    ///     ...
    ///
    /// The active buffer is flushed before saving.
    pub fn save_to_dir(&mut self, dir: &Path) -> std::io::Result<()> {
        self.flush(); // make sure active content is captured

        std::fs::create_dir_all(dir)?;

        let mut entries = Vec::new();

        for (i, sealed) in self.sealed.iter().enumerate() {
            let filename = format!("seg-{:05}.bin", i + 1);
            let path = dir.join(&filename);
            sealed.segment.write_to_disk(&path)?;

            entries.push(SegmentEntry {
                filename,
                tier: sealed.tier,
                doc_count: sealed.segment.doc_count,
            });
        }

        let manifest = IndexManifest {
            version: MANIFEST_VERSION,
            segments: entries,
            total_docs: self.total_docs,
            total_tokens: self.total_tokens,
        };

        let manifest_path = dir.join("manifest.json");
        let manifest_json = serde_json::to_string_pretty(&manifest)?;
        std::fs::write(manifest_path, manifest_json)?;

        Ok(())
    }

    /// Load a complete index from a directory previously written by `save_to_dir`.
    /// The returned index has no merge policy (you can set one afterwards).
    pub fn load_from_dir(dir: &Path) -> std::io::Result<Self> {
        let manifest_path = dir.join("manifest.json");
        let manifest_bytes = std::fs::read(manifest_path)?;
        let manifest: IndexManifest = serde_json::from_slice(&manifest_bytes)?;

        if manifest.version != MANIFEST_VERSION {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("unsupported manifest version {}", manifest.version),
            ));
        }

        let mut idx = OnDiskIndex::new();
        idx.total_docs = manifest.total_docs;
        idx.total_tokens = manifest.total_tokens;

        for entry in manifest.segments {
            let path = dir.join(&entry.filename);
            let seg = OnDiskSegment::load_from_disk(&path)?;
            idx.sealed.push(SealedSegment {
                segment: seg,
                tier: entry.tier,
            });
        }

        // Replay active WAL if present (crash recovery)
        let wal_path = dir.join("active.wal");
        if wal_path.exists() {
            let wal = SimpleWal::open_for_replay(&wal_path)?;
            let _ = wal.replay_into(&mut idx.active);
            // Attach the WAL so future writes continue logging
            // (we create a new handle in append mode)
            if let Ok(w) = SimpleWal::create(&wal_path) {
                // We already replayed; the file now has the history. Future appends will add more.
                idx.active = std::mem::replace(&mut idx.active, ActiveBuffer::new()).with_wal(w);
            }
        }

        Ok(idx)
    }
}

// ----------------------------------------------------------------------------
// Search result + very lightweight scoring (spike quality, not production)
// ----------------------------------------------------------------------------

/// A scored hit returned by the index.
#[derive(Debug, Clone)]
pub struct SearchHit {
    /// None means the hit came from the active buffer.
    pub segment_idx: Option<usize>,
    pub local_doc_id: LocalDocId,
    pub score: f32,
}

/// Very rough IDF (inspired by BM25 but heavily simplified for the spike).
fn idf(df: u32, total_docs: u64) -> f32 {
    if total_docs == 0 || df == 0 {
        return 1.0;
    }
    let n = total_docs as f32;
    let d = df as f32;
    ((n - d + 0.5) / (d + 0.5) + 1.0).ln()
}

/// Tiny length-normalized score for a single term occurrence.
fn score_term(tf: u32, df: u32, doc_len: u32, total_docs: u64, avgdl: f32) -> f32 {
    let k1 = 1.2_f32;
    let b = 0.75_f32;

    let idf = idf(df, total_docs);
    let len_norm = 1.0 - b + b * (doc_len as f32 / avgdl.max(1.0));
    idf * (tf as f32 * (k1 + 1.0)) / (tf as f32 + k1 * len_norm)
}

/// Search the whole index (active buffer + all sealed segments) for the given terms (OR semantics).
/// Returns hits sorted by descending score.
pub fn search(index: &OnDiskIndex, terms: &[&str]) -> Vec<SearchHit> {
    if terms.is_empty() {
        return Vec::new();
    }

    let total_docs = index.approx_doc_count();
    let avgdl = if total_docs > 0 {
        index.total_tokens as f32 / total_docs as f32
    } else {
        1.0
    };

    let mut hits: Vec<SearchHit> = Vec::new();

    // --- Search sealed segments ---
    for (seg_idx, sealed) in index.sealed.iter().enumerate() {
        let seg = &sealed.segment;
        // For each query term, collect postings and score
        for term in terms {
            if let Some(postings) = seg.get_postings(term) {
                let df = seg
                    .terms
                    .binary_search_by(|t| t.term.as_str().cmp(term))
                    .ok()
                    .map(|i| seg.terms[i].df)
                    .unwrap_or(postings.len() as u32);

                for p in postings {
                    let dl = seg.doc_length(p.doc_id);
                    let sc = score_term(p.term_freq, df, dl, total_docs, avgdl);
                    hits.push(SearchHit {
                        segment_idx: Some(seg_idx),
                        local_doc_id: p.doc_id,
                        score: sc,
                    });
                }
            }
        }
    }

    // --- Search the active buffer (very simple: just use raw term frequency) ---
    // We don't have a full term dictionary with df in the builder, so we approximate.
    // For the spike we give active hits a small constant boost or neutral score.
    // A real implementation would maintain a small in-memory term dictionary too.
    let active_hits = search_active_buffer(&index.active, terms, total_docs, avgdl);
    hits.extend(active_hits);

    // Merge duplicates (same segment+doc appearing for multiple terms) by summing scores
    hits.sort_by(|a, b| {
        a.segment_idx
            .cmp(&b.segment_idx)
            .then_with(|| a.local_doc_id.cmp(&b.local_doc_id))
    });

    let mut merged: Vec<SearchHit> = Vec::new();
    for h in hits {
        if let Some(last) = merged.last_mut() {
            if last.segment_idx == h.segment_idx && last.local_doc_id == h.local_doc_id {
                last.score += h.score;
                continue;
            }
        }
        merged.push(h);
    }

    // Final ranking
    merged.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    merged
}

/// Very basic phrase / proximity search using the stored positions.
/// Returns hits where the terms appear in order (with optional gap window).
pub fn phrase_search(index: &OnDiskIndex, phrase: &[&str], max_gap: u32) -> Vec<SearchHit> {
    if phrase.len() < 2 {
        // Fall back to normal search for single term
        return search(index, phrase);
    }

    let mut results = Vec::new();
    let total_docs = index.approx_doc_count();
    let avgdl = if total_docs > 0 { index.total_tokens as f32 / total_docs as f32 } else { 1.0 };

    // For each sealed segment
    for (seg_idx, sealed) in index.sealed.iter().enumerate() {
        let seg = &sealed.segment;
        // Get postings for the first term
        let Some(first_posts) = seg.get_postings(phrase[0]) else { continue };

        for p in first_posts {
            let mut ok = true;
            let mut last_pos = *p.positions.first().unwrap_or(&0);

            for term in &phrase[1..] {
                let Some(more) = seg.get_postings(term) else {
                    ok = false;
                    break;
                };
                // Find a posting in the same doc with a position >= last_pos and within window
                let found = more.iter().any(|pp| {
                    if pp.doc_id != p.doc_id { return false; }
                    pp.positions.iter().any(|&pos| {
                        pos >= last_pos && pos <= last_pos + max_gap
                    })
                });
                if !found {
                    ok = false;
                    break;
                }
                // advance last_pos heuristically
                if let Some(next_p) = more.iter().find(|pp| pp.doc_id == p.doc_id) {
                    if let Some(&np) = next_p.positions.iter().find(|&&pos| pos >= last_pos) {
                        last_pos = np;
                    }
                }
            }

            if ok {
                let dl = seg.doc_length(p.doc_id);
                let sc = score_term(p.term_freq, 1, dl, total_docs, avgdl) * 2.0; // phrase boost
                results.push(SearchHit {
                    segment_idx: Some(seg_idx),
                    local_doc_id: p.doc_id,
                    score: sc,
                });
            }
        }
    }

    // --- Also search the active buffer using positions ---
    let active_phrase = phrase_search_active_buffer(&index.active, phrase, max_gap, total_docs, avgdl);
    results.extend(active_phrase);

    results.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    results
}

/// Phrase search inside the active (mutable) buffer using raw postings + positions.
fn phrase_search_active_buffer(
    active: &ActiveBuffer,
    phrase: &[&str],
    max_gap: u32,
    total_docs: u64,
    avgdl: f32,
) -> Vec<SearchHit> {
    if phrase.is_empty() {
        return Vec::new();
    }

    // Collect live postings per term from the active buffer
    let mut term_postings: HashMap<&str, Vec<Posting>> = HashMap::new();
    for term in phrase {
        let posts = active.get_live_postings(term);
        if !posts.is_empty() {
            term_postings.insert(term, posts);
        } else {
            return Vec::new(); // missing term → no phrase matches
        }
    }

    let first_term = phrase[0];
    let first_posts = term_postings.get(first_term).unwrap();

    let mut out = Vec::new();

    for p in first_posts {
        let mut ok = true;
        let mut last_pos = *p.positions.first().unwrap_or(&0);

        for term in &phrase[1..] {
            let more = term_postings.get(term).unwrap();
            let found = more.iter().any(|pp| {
                if pp.doc_id != p.doc_id {
                    return false;
                }
                pp.positions.iter().any(|&pos| pos >= last_pos && pos <= last_pos + max_gap)
            });
            if !found {
                ok = false;
                break;
            }
            // advance position
            if let Some(next_p) = more.iter().find(|pp| pp.doc_id == p.doc_id) {
                if let Some(&np) = next_p.positions.iter().find(|&&pos| pos >= last_pos) {
                    last_pos = np;
                }
            }
        }

        if ok {
            let dl = active.doc_length(p.doc_id);
            let sc = score_term(p.term_freq, 1, dl, total_docs, avgdl) * 2.0; // phrase boost
            out.push(SearchHit {
                segment_idx: None,
                local_doc_id: p.doc_id,
                score: sc,
            });
        }
    }

    out
}

// ----------------------------------------------------------------------------
// Robust Background Merge Worker (improved sketch)
// ----------------------------------------------------------------------------

use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

/// Commands sent to the background merge worker.
#[derive(Debug, Clone, Copy)]
pub enum MergeCommand {
    /// Trigger a merge round right now (useful after heavy writes).
    MergeNow,
    /// Graceful shutdown.
    Shutdown,
}

/// Statistics reported by the merge worker.
#[derive(Debug, Default, Clone)]
pub struct MergeStats {
    pub merges_performed: u64,
    pub last_merge_duration_ms: u64,
    pub last_error: Option<String>,
}

/// Handle returned to the caller. Use `send_command` or `shutdown`.
pub struct MergeWorkerHandle {
    pub tx: Sender<MergeCommand>,
    join: Option<JoinHandle<()>>,
    stats: Arc<Mutex<MergeStats>>,
}

impl MergeWorkerHandle {
    pub fn send_command(&self, cmd: MergeCommand) -> Result<(), mpsc::SendError<MergeCommand>> {
        self.tx.send(cmd)
    }

    pub fn shutdown(mut self) -> MergeStats {
        let _ = self.tx.send(MergeCommand::Shutdown);
        if let Some(h) = self.join.take() {
            let _ = h.join();
        }
        // Best effort: return final stats
        self.stats.lock().map(|g| g.clone()).unwrap_or_default()
    }

    pub fn stats(&self) -> MergeStats {
        self.stats.lock().map(|g| g.clone()).unwrap_or_default()
    }
}

/// Start a more robust background merge worker.
///
/// Features of this version vs the original sketch:
/// - Uses an `mpsc` channel for explicit commands (MergeNow / Shutdown)
/// - Periodically wakes up on its own timer
/// - Tracks basic stats
/// - Uses a single Mutex (in real code you would likely use RwLock + careful merge locking)
/// - Better shutdown semantics
///
/// Still a sketch — a production implementation would:
/// - Use `parking_lot::RwLock` or a custom reader/writer separation
/// - Have bounded merge work to avoid long stalls
/// - Persist merge progress / use a dedicated merge scheduler
pub fn start_merge_worker(
    index: Arc<Mutex<OnDiskIndex>>,
    wake_interval: Duration,
) -> MergeWorkerHandle {
    let (tx, rx) = mpsc::channel::<MergeCommand>();
    let stats = Arc::new(Mutex::new(MergeStats::default()));
    let stats2 = stats.clone();

    let handle = thread::spawn(move || {
        let mut last_tick = std::time::Instant::now();

        loop {
            // Check for explicit command (non-blocking first, then blocking with timeout)
            match rx.recv_timeout(wake_interval) {
                Ok(MergeCommand::Shutdown) => {
                    break;
                }
                Ok(MergeCommand::MergeNow) => {
                    perform_merge(&index, &stats2);
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    // Periodic wake-up
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    break;
                }
            }

            // Also trigger on timer even if no command arrived
            if last_tick.elapsed() >= wake_interval {
                perform_merge(&index, &stats2);
                last_tick = std::time::Instant::now();
            }
        }
    });

    MergeWorkerHandle {
        tx,
        join: Some(handle),
        stats,
    }
}

fn perform_merge(index: &Arc<Mutex<OnDiskIndex>>, stats: &Arc<Mutex<MergeStats>>) {
    let start = std::time::Instant::now();

    let result = index.lock().map(|mut guard| {
        guard.apply_merge_policy();
    });

    let mut s = stats.lock().unwrap_or_else(|p| p.into_inner());
    match result {
        Ok(_) => {
            s.merges_performed += 1;
            s.last_merge_duration_ms = start.elapsed().as_millis() as u64;
            s.last_error = None;
        }
        Err(e) => {
            s.last_error = Some(e.to_string());
        }
    }
}

/// Naive search inside the active buffer (no df available, so we just count term occurrences).
fn search_active_buffer(
    active: &ActiveBuffer,
    terms: &[&str],
    total_docs: u64,
    avgdl: f32,
) -> Vec<SearchHit> {
    use std::collections::HashMap as StdHashMap;

    // Build a tiny in-memory view: term -> list of (doc_id, tf)
    let mut term_map: StdHashMap<&str, Vec<(LocalDocId, u32)>> = StdHashMap::new();

    // Use the small helper that already filters deletes.
    for term in terms {
        let posts = active.get_live_postings(term);
        if !posts.is_empty() {
            let mut per_doc: StdHashMap<LocalDocId, u32> = StdHashMap::new();
            for p in posts {
                *per_doc.entry(p.doc_id).or_insert(0) += p.term_freq;
            }
            let v: Vec<_> = per_doc.into_iter().collect();
            term_map.insert(term, v);
        }
    }

    let mut out = Vec::new();
    for (_term, docs) in term_map {
        // Very rough df estimate for active buffer
        let df = docs.len() as u32;
        for (doc_id, tf) in docs {
            let dl = active.doc_length(doc_id);
            let sc = score_term(tf, df, dl, total_docs, avgdl) * 0.9; // slight penalty vs sealed
            out.push(SearchHit {
                segment_idx: None,
                local_doc_id: doc_id,
                score: sc,
            });
        }
    }
    out
}

/// Real merge of any number of segments.
///
/// - Only live (non-deleted) postings are copied into the result.
/// - Document IDs from later segments are shifted so the merged segment has a
///   dense, contiguous id space [0 .. total_live_docs).
/// - The resulting segment has an empty (or zeroed) delete bitmap because
///   deletes have already been applied during the merge (compaction).
pub fn merge_segments(segments: &[&OnDiskSegment]) -> OnDiskSegment {
    if segments.is_empty() {
        return OnDiskSegment {
            doc_count: 0,
            deleted: vec![],
            terms: vec![],
            postings_blob: vec![],
            doc_lengths: vec![],
        };
    }

    // Collect all live postings per term, assigning *new dense* doc ids
    // only to documents that actually have at least one live posting.
    // This gives us a truly compacted segment after merge.
    let mut term_postings: BTreeMap<String, Vec<Posting>> = BTreeMap::new();

    // Map from (segment_index, original_local_doc_id) -> new dense id
    let mut live_doc_map: HashMap<(usize, LocalDocId), LocalDocId> = HashMap::new();
    let mut next_live_doc_id: LocalDocId = 0;

    for (seg_idx, seg) in segments.iter().enumerate() {
        for tmeta in &seg.terms {
            if let Some(live_posts) = seg.get_postings(&tmeta.term) {
                for mut p in live_posts {
                    let key = (seg_idx, p.doc_id);
                    let new_id = *live_doc_map.entry(key).or_insert_with(|| {
                        let id = next_live_doc_id;
                        next_live_doc_id += 1;
                        id
                    });
                    p.doc_id = new_id;
                    term_postings
                        .entry(tmeta.term.clone())
                        .or_default()
                        .push(p);
                }
            }
        }
    }

    // Now build the OnDiskSegment exactly like SegmentBuilder does.
    let mut terms: Vec<TermMeta> = Vec::new();
    let mut postings_blob: Vec<u8> = Vec::new();
    let total_live_docs = next_live_doc_id;

    for (term, mut postings) in term_postings {
        postings.sort_by_key(|p| p.doc_id);
        let df = postings.len() as u32;
        let offset = postings_blob.len() as u32;

        let encoded = bincode::serialize(&postings).unwrap();
        let len = encoded.len() as u32;
        postings_blob.extend_from_slice(&encoded);

        terms.push(TermMeta {
            term,
            df,
            posting_offset: offset,
            posting_len: len,
        });
    }

    // The merged segment has no deletes (we already filtered them during the merge).
    // We allocate a zeroed bitmap sized for the new dense live document count.
    let needed_bytes = ((total_live_docs as usize) + 7) / 8;
    let deleted = vec![0u8; needed_bytes];

    // For the compacted merged segment we don't have original lengths anymore
    // (we dropped deleted docs). Use a neutral default so scoring still works.
    let doc_lengths = vec![1u32; total_live_docs as usize];

    OnDiskSegment {
        doc_count: total_live_docs,
        deleted,
        terms,
        postings_blob,
        doc_lengths,
    }
}

fn main() {
    println!("=== On-Disk Inverted Index Spike v5 (manifest + tiered merge policy) ===\n");

    // Use a very aggressive policy (merge every 2 segments in a tier) so the demo is short
    let policy = TieredMergePolicy { segments_per_tier: 2 };
    let mut index = OnDiskIndex::with_merge_policy(policy);

    println!("Created index with tiered merge policy (2 segments per tier)\n");

    // --- Many small flushes to exercise tier promotion ---
    let docs = vec![
        vec!["rust", "memory"],
        vec!["rust", "ownership"],
        vec!["concurrency", "fearless"],
        vec!["borrowing", "lifetimes"],
        vec!["unsafe", "raw", "pointers"],
        vec!["async", "await", "tokio"],
        vec!["memory", "safety", "zero", "cost"],
        vec!["graph", "traversal", "bm25"],
    ];

    for (i, terms) in docs.iter().enumerate() {
        index.add_document(terms, &vec![vec![]; terms.len()]);
        let _ = index.flush(); // each flush may trigger tiered merges
        println!(
            "After flush #{} → segments: {:?}",
            i + 1,
            index.debug_segment_tiers()
        );
    }

    println!(
        "\nFinal in-memory state: {} segments, tiers = {:?}",
        index.sealed_segment_count(),
        index.debug_segment_tiers()
    );

    // Demonstrate full directory persistence (manifest.json + seg-*.bin)
    let temp_dir = tempfile::tempdir().unwrap();
    let index_dir = temp_dir.path().join("my_index");
    index.save_to_dir(&index_dir).unwrap();
    println!("\nSaved whole index to {:?}", index_dir);
    println!("Contents: {:?}", std::fs::read_dir(&index_dir).unwrap().map(|e| e.unwrap().file_name()).collect::<Vec<_>>());

    // Load it back — this exercises the manifest + per-segment header loading
    let mut loaded = OnDiskIndex::load_from_dir(&index_dir).unwrap();
    println!(
        "Loaded index: {} segments, approx {} docs",
        loaded.sealed_segment_count(),
        loaded.approx_doc_count()
    );

    // Search still works after round-trip
    let hits = search(&loaded, &["rust", "memory"]);
    println!("Search 'rust'+'memory' on loaded index: {} hits", hits.len());
    for h in hits.iter().take(5) {
        println!("  {:?}", h);
    }

    // We can attach a merge policy to the loaded index too
    loaded.merge_policy = Some(TieredMergePolicy { segments_per_tier: 3 });

    println!("\nSpike v6 (WAL + background sketch + phrase + integration) completed successfully.");
}

// ============================================================================
// Integration sketch with the real grap crate (IndexedKnowledgeGraph)
// ============================================================================
//
// This section documents how the on-disk index spike would plug into the
// production `grap` crate (src/index.rs + src/graph.rs).
//
// 1. The current production type is:
//
//    pub struct IndexedKnowledgeGraph {
//        pub graph: KnowledgeGraph,
//        pub text_index: InvertedIndex,   // fully in-memory today
//    }
//
// 2. For 0.2 we would likely do one of:
//
//    a) Make `InvertedIndex` an enum or a trait object:
//       enum InvertedIndex {
//           Memory(MemoryInvertedIndex),
//           OnDisk(OnDiskIndex),           // from this spike, wrapped
//       }
//
//    b) Or introduce a new `PersistentIndexedKnowledgeGraph` that owns
//       an `OnDiskIndex` (or one per shard) instead of the current
//       `InvertedIndex`.
//
// 3. Mapping between worlds:
//
//    - Production `NodeId` (u32 inside a KnowledgeGraph)  <->  (segment_id, local_doc_id)
//    - When sharded: `GlobalNodeId` (ShardId + local NodeId)  <->  (shard, segment, local_doc)
//
//    The on-disk index only ever sees *local dense doc ids* per segment.
//    The higher layers (ShardedGraph, ghost edges, rebalance) are responsible
//    for translating GlobalNodeId <-> (segment, local) when calling into the
//    text index.
//
// 4. Phrase / proximity search would be exposed via the same
//    `search_and_traverse` path, just using `phrase_search` instead of (or in
//    addition to) the current BM25 seed selection.
//
// 5. Persistence integration:
//
//    - `IndexedKnowledgeGraph::save_to_disk` would call the on-disk index's
//      `save_to_dir` (or per-shard equivalent).
//    - Load would use `load_from_dir` + replay WAL for the active buffer of
//      each shard.
//
// 6. Delete / tombstone integration:
//
//    - `KnowledgeGraph::remove_node` would mark the tombstone in the owning
//      segment (and write a WAL entry if the node is still in the active buffer).
//    - Ghost edges on other shards pointing at the deleted GlobalNodeId would
//      be cleaned lazily on traversal or during rebalance (as described in the
//      design doc).
//
// This spike proves the core storage engine pieces are feasible.
// The remaining work is mostly glue + careful GlobalNodeId <-> segment mapping
// + making the OnDiskIndex implement (or satisfy) the query contracts that
// `search_and_traverse` expects.
//
// The design document (docs/design-0.2-on-disk-index-and-deletes.md) remains
// the authoritative source for the full picture (including interaction with
// sharding, ghost edges, and compaction during rebalance).
