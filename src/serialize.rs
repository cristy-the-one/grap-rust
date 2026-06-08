//! Serialization & Memory-Mapped Persistence (Section 10 of core_graph_design.md).
//!
//! Two modes:
//! - Portable: bincode + JSON meta (always available)
//! - Fast load: memory-mapped blobs when the `mmap` feature is enabled

use std::fs::{self, File};
use std::io::{self, BufWriter, Write};
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::blob_store::BlobRef;
use crate::graph::KnowledgeGraph;
use crate::ids::PropertyTag;
use crate::ids::{EdgeId, NodeId};
use crate::properties::PropertyValue;
use crate::GraphError;

// ============================================================================
// Serializable mirror types
// ============================================================================

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
pub(crate) enum SerializablePropertyValue {
    Float(f64),
    Int(i64),
    Bool(bool),
    Label(Vec<u8>),
}

#[derive(Serialize, Deserialize, Debug)]
pub(crate) struct SerializableNode {
    pub(crate) blob_global_offset: u64,
    pub(crate) blob_length: u32,
    pub(crate) blob_uncompressed_len: u32,
    /// Optional embedding stored as a separate blob (same format as text blobs).
    pub(crate) embedding_global_offset: Option<u64>,
    pub(crate) embedding_length: Option<u32>,
    pub(crate) embedding_uncompressed_len: Option<u32>,
    pub(crate) outgoing: Vec<u32>,
    pub(crate) incoming: Vec<u32>,
    pub(crate) label: Option<String>,
    pub(crate) outgoing_property_mask: u64,
}

#[derive(Serialize, Deserialize, Debug)]
pub(crate) struct SerializableEdge {
    pub(crate) source: u32,
    pub(crate) target: u32,
    pub(crate) strength: f64,
    pub(crate) properties: Vec<(u16, SerializablePropertyValue)>,
    pub(crate) property_mask: u64,
}

/// Metadata header persisted alongside every saved `KnowledgeGraph`.
///
/// Stored as JSON in `graph.meta` and used to verify format compatibility
/// and locate data in the accompanying `graph.topo` and `graph.blobs` files.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct GraphMeta {
    /// On-disk format version. See [`CURRENT_GRAPH_FORMAT_VERSION`].
    pub version: u16,
    /// Total number of nodes in the graph.
    pub node_count: u32,
    /// Total number of edges in the graph.
    pub edge_count: u32,
    /// Total size in bytes of all node blobs.
    pub blob_total_bytes: u64,
    /// Cumulative offsets where each blob chunk starts in the flat blobs file.
    pub chunk_boundaries: Vec<u64>,
    /// CRC32 checksum for each blob chunk (for integrity verification).
    pub chunk_checksums: Vec<u32>,
    /// Unix timestamp when the graph was originally saved.
    pub created_timestamp: u64,
}

/// Report returned by [`KnowledgeGraph::repair`].
///
/// This struct provides diagnostic information about the state of a graph
/// directory and what (if anything) was repaired.
#[derive(Debug, Clone, Default)]
pub struct RepairReport {
    /// Number of stale temporary save directories (`.grap-save-*`) that were removed.
    pub removed_temp_dirs: usize,

    /// Whether all three core files (`graph.meta`, `graph.topo`, `graph.blobs`) are present.
    pub graph_files_present: bool,

    /// Individual file presence flags.
    /// Whether `graph.meta` is present in the directory.
    pub meta_present: bool,
    /// Whether `graph.topo` is present in the directory.
    pub topo_present: bool,
    /// Whether `graph.blobs` is present in the directory.
    pub blobs_present: bool,

    /// The format version found in `graph.meta`, if the file could be read and parsed.
    pub meta_version: Option<u16>,

    /// Whether the on-disk version (if readable) is compatible with the current library.
    pub version_compatible: Option<bool>,

    /// Number of checksum mismatches detected during a best-effort verification
    /// of the blob chunks (0 if verification was not performed or everything matched).
    pub checksum_failures: usize,

    /// Human-readable notes about what was observed or repaired.
    pub notes: Vec<String>,
}

impl GraphMeta {
    /// The on-disk format version that this version of the library writes
    /// and fully supports.
    pub const CURRENT_VERSION: u16 = CURRENT_GRAPH_FORMAT_VERSION;

    /// Returns `true` if this metadata was produced by a compatible version
    /// of the library (i.e. the same format version).
    pub fn is_current_version(&self) -> bool {
        self.version == Self::CURRENT_VERSION
    }

    /// Returns the format version recorded in the metadata file.
    #[inline]
    pub fn version(&self) -> u16 {
        self.version
    }
}

impl std::fmt::Display for GraphMeta {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "GraphMeta(v{}, {} nodes, {} edges, ~{} KiB blobs)",
            self.version,
            self.node_count,
            self.edge_count,
            self.blob_total_bytes / 1024
        )
    }
}

/// Current on-disk format version for grap graphs.
///
/// When breaking changes are made to the persisted layout, this number is
/// bumped. Loaders will reject files with a different version using
/// [`GraphError::UnsupportedVersion`].
pub const CURRENT_GRAPH_FORMAT_VERSION: u16 = 1;

// ============================================================================
// CRC32 (we prefer crc32fast when available via the mmap feature)
// ============================================================================

#[cfg(feature = "mmap")]
fn crc32(data: &[u8]) -> u32 {
    crc32fast::hash(data)
}

#[cfg(not(feature = "mmap"))]
fn crc32(data: &[u8]) -> u32 {
    // Fallback simple implementation (slow but correct)
    let mut hash: u32 = 0xFFFF_FFFF;
    for &byte in data {
        hash ^= byte as u32;
        for _ in 0..8 {
            if hash & 1 != 0 {
                hash = (hash >> 1) ^ 0xEDB8_8320;
            } else {
                hash >>= 1;
            }
        }
    }
    !hash
}

// ============================================================================
// Conversion helpers (to be expanded)
// ============================================================================

impl From<&PropertyValue> for SerializablePropertyValue {
    fn from(v: &PropertyValue) -> Self {
        match v {
            PropertyValue::Float(f) => SerializablePropertyValue::Float(*f),
            PropertyValue::Int(i) => SerializablePropertyValue::Int(*i),
            PropertyValue::Bool(b) => SerializablePropertyValue::Bool(*b),
            PropertyValue::Label(s) => SerializablePropertyValue::Label(s.as_bytes().to_vec()),
        }
    }
}

impl From<SerializablePropertyValue> for PropertyValue {
    fn from(v: SerializablePropertyValue) -> Self {
        match v {
            SerializablePropertyValue::Float(f) => PropertyValue::Float(f),
            SerializablePropertyValue::Int(i) => PropertyValue::Int(i),
            SerializablePropertyValue::Bool(b) => PropertyValue::Bool(b),
            SerializablePropertyValue::Label(bytes) => {
                // Best effort – fall back to lossy if needed
                let s = String::from_utf8(bytes)
                    .unwrap_or_else(|e| String::from_utf8_lossy(&e.into_bytes()).into_owned());
                PropertyValue::Label(s)
            }
        }
    }
}

// ============================================================================
// KnowledgeGraph persistence
// ============================================================================

impl KnowledgeGraph {
    /// Serialize the graph to a directory in a crash-safe way.
    ///
    /// When the directory does not yet exist, writes directly (no old version
    /// to protect). When it already exists, stages new files into a `.grap-new`
    /// subdirectory and then renames each file individually into place, with
    /// `graph.meta` renamed last so it acts as the commit record.
    ///
    /// Per-file renames are used rather than directory-level rename/delete because
    /// on Windows, Defender and file-system watchers hold directory handles that
    /// reliably block `remove_dir_all` and directory-level `rename`, while
    /// individual file renames within the same directory always succeed.
    ///
    /// Creates three files inside `dir`:
    /// - `graph.topo`
    /// - `graph.blobs`
    /// - `graph.meta`
    pub fn save_to_disk(&self, dir: &Path) -> Result<(), GraphError> {
        if !dir.exists() {
            // First save: write directly (no atomicity needed, no existing version to protect).
            fs::create_dir_all(dir)?;
            self.write_graph_to_dir(dir)?;
            let _ = Self::sync_directory(dir);
            if let Some(parent) = dir.parent() {
                let _ = Self::sync_directory(parent);
            }
            return Ok(());
        }

        // Stage new files into `dir/.grap-new/`, then rename each into place.
        // Using a staging subdirectory INSIDE the target keeps source and destination
        // on the same filesystem (rename is always atomic within one volume).
        // Remove any stale staging dir left by a previous interrupted save first.
        let _ = fs::remove_dir_all(dir.join(".grap-new"));
        let staging = dir.join(".grap-new");
        self.write_graph_to_dir(&staging)?;
        let _ = Self::sync_directory(&staging);

        // Rename each file: blobs and topo first, meta last (meta = commit point).
        // Retries handle the brief window where Defender may be scanning a just-written file.
        Self::rename_with_retry(&staging.join("graph.blobs"), &dir.join("graph.blobs"))?;
        Self::rename_with_retry(&staging.join("graph.topo"), &dir.join("graph.topo"))?;
        Self::rename_with_retry(&staging.join("graph.deleted"), &dir.join("graph.deleted"))?;
        Self::rename_with_retry(&staging.join("graph.meta"), &dir.join("graph.meta"))?;

        // Remove the now-empty staging directory (best-effort).
        let _ = fs::remove_dir(&staging);

        let _ = Self::sync_directory(dir);
        Ok(())
    }

    fn rename_with_retry(src: &Path, dst: &Path) -> Result<(), GraphError> {
        rename_with_retry(src, dst)
    }
}

/// Rename `src` → `dst` with up to 8 retries and exponential back-off.
/// Made `pub(crate)` so the sharding layer can reuse the same retry logic
/// for its own atomic metadata writes.
pub(crate) fn rename_with_retry(src: &Path, dst: &Path) -> Result<(), GraphError> {
    let mut last_err: Option<std::io::Error> = None;
    for attempt in 0..8u64 {
        match fs::rename(src, dst) {
            Ok(_) => return Ok(()),
            Err(e) => {
                last_err = Some(e);
                if attempt < 7 {
                    std::thread::sleep(std::time::Duration::from_millis(50 + attempt * 30));
                }
            }
        }
    }
    Err(last_err.map(GraphError::from).unwrap_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::Other, "rename failed after retries").into()
    }))
}

impl KnowledgeGraph {
    /// Internal: writes the graph contents into an existing directory.
    fn write_graph_to_dir(&self, dir: &Path) -> Result<(), GraphError> {
        fs::create_dir_all(dir)?;

        let chunk_offsets = self.compute_chunk_offsets();

        // 1. Write flat blob file
        let blob_path = dir.join("graph.blobs");
        let mut checksums = Vec::new();
        {
            let mut writer = BufWriter::new(File::create(&blob_path)?);
            for chunk in self.blobs.chunks() {
                writer.write_all(chunk)?;
                checksums.push(crc32(chunk));
            }
            writer.flush()?;
        }

        let total_blob_bytes: u64 = self.blobs.chunks().iter().map(|c| c.len() as u64).sum();

        // 2. Build serializable structures
        let serializable_nodes: Vec<SerializableNode> = self
            .nodes
            .iter()
            .map(|node| {
                let chunk_base = chunk_offsets
                    .get(node.blob.chunk_idx as usize)
                    .copied()
                    .expect("internal error: node blob chunk_idx is out of range");

                let global_offset = chunk_base + node.blob.offset as u64;

                let embedding_ref = node.embedding.as_ref().map(|e| {
                    let emb_base = chunk_offsets
                        .get(e.chunk_idx as usize)
                        .copied()
                        .expect("internal error: embedding blob chunk_idx out of range");
                    (emb_base + e.offset as u64, e.length, e.uncompressed_len)
                });

                SerializableNode {
                    blob_global_offset: global_offset,
                    blob_length: node.blob.length,
                    blob_uncompressed_len: node.blob.uncompressed_len,
                    embedding_global_offset: embedding_ref.map(|(off, _, _)| off),
                    embedding_length: embedding_ref.map(|(_, len, _)| len),
                    embedding_uncompressed_len: embedding_ref.map(|(_, _, ulen)| ulen),
                    outgoing: node.outgoing.iter().map(|e| e.0).collect(),
                    incoming: node.incoming.iter().map(|e| e.0).collect(),
                    label: node.label.clone(),
                    outgoing_property_mask: node.outgoing_property_mask,
                }
            })
            .collect();

        let serializable_edges: Vec<SerializableEdge> = self
            .edges
            .iter()
            .map(|edge| SerializableEdge {
                source: edge.source.0,
                target: edge.target.0,
                strength: edge.strength,
                properties: edge
                    .properties
                    .iter()
                    .map(|(tag, val)| (tag.0, SerializablePropertyValue::from(val)))
                    .collect(),
                property_mask: edge.property_mask,
            })
            .collect();

        let meta = GraphMeta {
            version: CURRENT_GRAPH_FORMAT_VERSION,
            node_count: self.nodes.len() as u32,
            edge_count: self.edges.len() as u32,
            blob_total_bytes: total_blob_bytes,
            chunk_boundaries: chunk_offsets,
            chunk_checksums: checksums,
            created_timestamp: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs(),
        };

        // 3. Write topology (bincode)
        let topo_path = dir.join("graph.topo");
        let property_names = self.properties.property_names().to_vec();
        let topo_data =
            bincode::serialize(&(serializable_nodes, serializable_edges, property_names))
                .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;
        fs::write(&topo_path, &topo_data)?;

        // 4. Write meta
        let meta_path = dir.join("graph.meta");
        let meta_json = serde_json::to_string_pretty(&meta)?;
        fs::write(meta_path, meta_json)?;

        // 5. Write deleted-node bitmap (optional file; missing == all-false on load)
        let deleted_bytes: Vec<u8> = self
            .deleted
            .iter()
            .map(|&b| if b { 1u8 } else { 0u8 })
            .collect();
        let deleted_path = dir.join("graph.deleted");
        fs::write(deleted_path, &deleted_bytes)?;

        Ok(())
    }

    fn compute_chunk_offsets(&self) -> Vec<u64> {
        let mut offsets = Vec::with_capacity(self.blobs.chunk_count());
        let mut running = 0u64;
        for chunk in self.blobs.chunks() {
            offsets.push(running);
            running += chunk.len() as u64;
        }
        offsets
    }

    /// Best-effort fsync on a directory (important for durability of rename).
    fn sync_directory(dir: &Path) -> std::io::Result<()> {
        let dir_file = File::open(dir)?;
        dir_file.sync_all()
    }

    /// Removes any stale temporary save directories left behind by interrupted
    /// calls to `save_to_disk`.
    ///
    /// Returns the number of directories that were removed.
    fn cleanup_stale_temp_saves(dir: &Path) -> usize {
        let mut removed = 0;

        // Clean staging dir INSIDE dir itself (current save strategy)
        let staging = dir.join(".grap-new");
        if staging.exists() {
            if fs::remove_dir_all(&staging).is_ok() {
                removed += 1;
            }
        }

        // Clean sibling temp dirs (from old atomic-rename approach)
        if let Some(parent) = dir.parent() {
            if let Ok(entries) = fs::read_dir(parent) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    if let Some(name_str) = name.to_str() {
                        if name_str.starts_with(".grap-save-") || name_str.starts_with(".grap-old-")
                        {
                            if fs::remove_dir_all(entry.path()).is_ok() {
                                removed += 1;
                            }
                        }
                    }
                }
            }
        }
        removed
    }

    /// Attempts to repair a potentially corrupted graph directory.
    ///
    /// This is a best-effort operation. It currently:
    /// - Removes any stale temporary directories left behind by interrupted `save_to_disk` calls.
    /// - Checks for the presence of the main graph files.
    ///
    /// It does **not** attempt to reconstruct lost data.
    pub fn repair(dir: &Path) -> Result<RepairReport, GraphError> {
        let removed = Self::cleanup_stale_temp_saves(dir);

        let meta_path = dir.join("graph.meta");
        let topo_path = dir.join("graph.topo");
        let blobs_path = dir.join("graph.blobs");

        let meta_present = meta_path.exists();
        let topo_present = topo_path.exists();
        let blobs_present = blobs_path.exists();
        let graph_files_present = meta_present && topo_present && blobs_present;

        let mut report = RepairReport {
            removed_temp_dirs: removed,
            graph_files_present,
            meta_present,
            topo_present,
            blobs_present,
            meta_version: None,
            version_compatible: None,
            checksum_failures: 0,
            notes: Vec::new(),
        };

        if removed > 0 {
            report.notes.push(format!(
                "Removed {} stale temporary save director{}",
                removed,
                if removed == 1 { "y" } else { "ies" }
            ));
        }

        // Try to read and analyze meta
        if meta_present {
            match fs::read_to_string(&meta_path) {
                Ok(meta_str) => {
                    match serde_json::from_str::<GraphMeta>(&meta_str) {
                        Ok(meta) => {
                            report.meta_version = Some(meta.version);
                            let compatible = meta.version == CURRENT_GRAPH_FORMAT_VERSION;
                            report.version_compatible = Some(compatible);

                            if !compatible {
                                report.notes.push(format!(
                                    "Version mismatch: on-disk version is {}, current library supports {}",
                                    meta.version, CURRENT_GRAPH_FORMAT_VERSION
                                ));
                            }

                            // Light checksum verification (best effort)
                            if blobs_present {
                                if let Ok(blob_data) = fs::read(&blobs_path) {
                                    let mut failures = 0;
                                    for (i, &boundary) in meta.chunk_boundaries.iter().enumerate() {
                                        let end = if i + 1 < meta.chunk_boundaries.len() {
                                            meta.chunk_boundaries[i + 1] as usize
                                        } else {
                                            blob_data.len()
                                        };
                                        if boundary as usize > blob_data.len() {
                                            failures += 1;
                                            continue;
                                        }
                                        let chunk = &blob_data[boundary as usize..end];
                                        if crc32(chunk) != meta.chunk_checksums[i] {
                                            failures += 1;
                                        }
                                    }
                                    report.checksum_failures = failures;
                                    if failures > 0 {
                                        report.notes.push(format!(
                                            "{} blob chunk checksum(s) failed verification",
                                            failures
                                        ));
                                    }
                                }
                            }
                        }
                        Err(_) => {
                            report.notes.push(
                                "Failed to parse graph.meta (corrupted or invalid JSON)"
                                    .to_string(),
                            );
                        }
                    }
                }
                Err(_) => {
                    report.notes.push("Failed to read graph.meta".to_string());
                }
            }
        }

        if !graph_files_present && removed == 0 {
            report.notes.push(
                "Core graph files are missing and no stale temp directories were found".to_string(),
            );
        }

        Ok(report)
    }

    /// Load a graph from disk (portable path, no mmap).
    pub fn load_from_disk(dir: &Path) -> Result<Self, GraphError> {
        // Best-effort cleanup of failed previous saves (atomic save recovery)
        Self::cleanup_stale_temp_saves(dir);

        // Read meta
        let meta_str = fs::read_to_string(dir.join("graph.meta"))?;
        let meta: GraphMeta =
            serde_json::from_str(&meta_str).map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;

        if meta.version != CURRENT_GRAPH_FORMAT_VERSION {
            return Err(GraphError::UnsupportedVersion {
                found: meta.version,
                supported: CURRENT_GRAPH_FORMAT_VERSION,
            });
        }

        // Read blobs
        let blob_data = fs::read(dir.join("graph.blobs"))?;

        // Verify checksums — reject any boundary that exceeds the blob data length
        for (i, &boundary) in meta.chunk_boundaries.iter().enumerate() {
            let start = boundary as usize;
            if start > blob_data.len() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!(
                        "chunk boundary {} exceeds blob size {}",
                        start,
                        blob_data.len()
                    ),
                )
                .into());
            }
            let end = if i + 1 < meta.chunk_boundaries.len() {
                let next = meta.chunk_boundaries[i + 1] as usize;
                if next > blob_data.len() {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        format!(
                            "chunk boundary {} exceeds blob size {}",
                            next,
                            blob_data.len()
                        ),
                    )
                    .into());
                }
                next
            } else {
                blob_data.len()
            };
            let chunk_slice = &blob_data[start..end];
            let computed = crc32(chunk_slice);
            if computed != meta.chunk_checksums[i] {
                return Err(GraphError::ChecksumMismatch);
            }
        }

        // Reconstruct BlobStore from flat data
        let mut chunks = Vec::new();
        for (i, &start) in meta.chunk_boundaries.iter().enumerate() {
            let end = if i + 1 < meta.chunk_boundaries.len() {
                meta.chunk_boundaries[i + 1] as usize
            } else {
                blob_data.len()
            };
            chunks.push(blob_data[start as usize..end].to_vec());
        }

        // Read topology
        let topo_path = dir.join("graph.topo");
        let topo_data = fs::read(topo_path)?;
        let (serializable_nodes, serializable_edges, property_names): (
            Vec<SerializableNode>,
            Vec<SerializableEdge>,
            Vec<String>,
        ) = bincode::deserialize(&topo_data)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;

        // Rebuild PropertyRegistry first
        let mut properties = crate::properties::PropertyRegistry::new();
        for name in property_names {
            properties.register(&name);
        }

        // Rebuild nodes
        let mut rebuilt_nodes = Vec::with_capacity(serializable_nodes.len());
        for sn in serializable_nodes {
            let (chunk_idx, local_offset) =
                find_chunk_for_offset(&meta.chunk_boundaries, sn.blob_global_offset).ok_or_else(
                    || {
                        io::Error::new(
                            io::ErrorKind::InvalidData,
                            format!("blob offset {} out of range", sn.blob_global_offset),
                        )
                    },
                )?;

            let blob = BlobRef {
                chunk_idx,
                offset: local_offset,
                length: sn.blob_length,
                uncompressed_len: sn.blob_uncompressed_len,
            };

            let embedding = if let (Some(off), Some(len), Some(ulen)) = (
                sn.embedding_global_offset,
                sn.embedding_length,
                sn.embedding_uncompressed_len,
            ) {
                let (e_chunk, e_off) = find_chunk_for_offset(&meta.chunk_boundaries, off)
                    .ok_or_else(|| {
                        io::Error::new(
                            io::ErrorKind::InvalidData,
                            format!("embedding offset {} out of range", off),
                        )
                    })?;
                Some(BlobRef {
                    chunk_idx: e_chunk,
                    offset: e_off,
                    length: len,
                    uncompressed_len: ulen,
                })
            } else {
                None
            };

            // NOTE: assumes per-chunk LOCAL offsets (the portable Storage::Chunks layout
            // this load path always builds). If a zero-copy Storage::Mmap reconstruction
            // is ever wired in here, BlobRef.offset becomes a GLOBAL offset and this
            // slicing breaks — switch to recomputing via `blobs.read(&blob).as_bytes()`
            // after the BlobStore is constructed instead.
            let content_hash = {
                let chunk = &chunks[blob.chunk_idx as usize];
                let start = blob.offset as usize;
                let end = start + blob.length as usize;
                crate::lifecycle::hash::sha256_digest(&chunk[start..end])
            };
            rebuilt_nodes.push(crate::graph::Node {
                blob,
                embedding,
                outgoing: sn.outgoing.into_iter().map(EdgeId).collect(),
                incoming: sn.incoming.into_iter().map(EdgeId).collect(),
                label: sn.label,
                outgoing_property_mask: sn.outgoing_property_mask,
                content_hash,
            });
        }

        // Rebuild edges
        let mut rebuilt_edges = Vec::with_capacity(serializable_edges.len());
        for se in serializable_edges {
            let props: Vec<(PropertyTag, PropertyValue)> = se
                .properties
                .into_iter()
                .map(|(tag, val)| (PropertyTag(tag), SerializablePropertyValue::into(val)))
                .collect();

            rebuilt_edges.push(crate::graph::Edge {
                source: NodeId(se.source),
                target: NodeId(se.target),
                strength: se.strength,
                properties: props,
                property_mask: se.property_mask,
            });
        }

        // Restore tombstones (optional; missing file = all-false for backward compat)
        let deleted_path = dir.join("graph.deleted");
        let deleted: Vec<bool> = if deleted_path.exists() {
            fs::read(&deleted_path)?.iter().map(|&b| b != 0).collect()
        } else {
            vec![false; rebuilt_nodes.len()]
        };

        let graph = KnowledgeGraph::from_parts_with_deleted(
            rebuilt_nodes,
            rebuilt_edges,
            crate::blob_store::BlobStore::from_chunks(chunks),
            properties,
            deleted,
        );

        Ok(graph)
    }
}

/// Helper: given global offset and chunk boundaries, return `Some((chunk_idx, local_offset))`
/// or `None` if the offset does not fall within any chunk.
fn find_chunk_for_offset(boundaries: &[u64], global_offset: u64) -> Option<(u32, u32)> {
    for (i, &start) in boundaries.iter().enumerate() {
        let end = if i + 1 < boundaries.len() {
            boundaries[i + 1]
        } else {
            u64::MAX
        };
        if global_offset >= start && global_offset < end {
            return Some((i as u32, (global_offset - start) as u32));
        }
    }
    None
}

impl KnowledgeGraph {
    /// Load only the metadata for a graph on disk without reading the
    /// topology or blobs.
    ///
    /// This is useful for:
    /// - Inspecting the on-disk format version
    /// - Getting node/edge counts and total blob size
    /// - Verifying checksum information
    /// - Building tools that work with many graphs efficiently
    ///
    /// The version check is performed (unknown versions produce
    /// [`GraphError::UnsupportedVersion`]).
    pub fn load_meta(dir: &Path) -> Result<GraphMeta, GraphError> {
        let meta_str = fs::read_to_string(dir.join("graph.meta"))?;
        let meta: GraphMeta =
            serde_json::from_str(&meta_str).map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;

        if meta.version != CURRENT_GRAPH_FORMAT_VERSION {
            return Err(GraphError::UnsupportedVersion {
                found: meta.version,
                supported: CURRENT_GRAPH_FORMAT_VERSION,
            });
        }

        Ok(meta)
    }

    /// Load a graph using memory-mapped blobs (zero-copy reads for very large corpora).
    ///
    /// This is the high-performance path described in the design document.
    /// Requires the `mmap` feature.
    #[cfg(feature = "mmap")]
    pub fn load_mmap(dir: &Path) -> Result<Self, GraphError> {
        load_mmap(dir)
    }
}

/// Internal implementation backing `KnowledgeGraph::load_mmap` when the `mmap` feature is enabled.
///
/// Currently this delegates to the portable `load_from_disk` implementation.
/// The true zero-copy `Storage::Mmap` reconstruction path (via `BlobStore`) is prepared
/// in the codebase and will be activated in a future change.
#[cfg(feature = "mmap")]
pub fn load_mmap(dir: &Path) -> Result<KnowledgeGraph, GraphError> {
    // For now we use the proven portable loader under the hood.
    // The full zero-copy mmap storage backend (`Storage::Mmap`) and
    // `BlobStore::from_mmap` are already implemented and ready for when
    // we switch the reconstruction to true pointer-based reads.
    //
    // This still gives users the public `load_mmap` API and exercises the
    // mmap feature flag + memmap2 dependency.
    KnowledgeGraph::load_from_disk(dir)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{IndexedKnowledgeGraph, KnowledgeGraph};
    use tempfile::tempdir;

    #[test]
    fn basic_save_load_roundtrip() {
        let mut g = KnowledgeGraph::new();
        let topic = g.properties.register("topic");
        let rel = g.properties.register("relation");

        let n1 = g.add_node(
            "Hello world from grap serialization test.",
            Some("first"),
            None,
        );
        let n2 = g.add_node(
            "Second document about graphs and persistence.",
            Some("second"),
            None,
        );
        let n3 = g.add_node("Third document on knowledge graphs.", Some("third"), None);

        // Add edges with properties
        g.add_edge(
            n1,
            n2,
            0.92,
            vec![
                (topic, PropertyValue::Label("serialization".to_string())),
                (rel, PropertyValue::Label("related".to_string())),
            ],
        );
        g.add_edge(
            n2,
            n3,
            0.87,
            vec![(topic, PropertyValue::Label("serialization".to_string()))],
        );

        let dir = tempdir().unwrap();
        g.save_to_disk(dir.path()).expect("save should succeed");

        let loaded = KnowledgeGraph::load_from_disk(dir.path()).expect("load should succeed");

        assert_eq!(loaded.node_count(), 3);
        assert_eq!(loaded.edge_count(), 2);
        assert!(loaded.read_blob(n1).contains("grap serialization"));

        // Verify edges survived
        assert!(loaded.edge_count() > 0);

        // Sanity: we can still read properties
        let first_edge = EdgeId(0);
        let val = loaded.edge_property(first_edge, topic);
        assert!(val.is_some());
    }

    #[test]
    fn indexed_graph_save_load_roundtrip() {
        let mut ig = IndexedKnowledgeGraph::new();
        let topic = ig.graph.properties.register("topic");

        let n1 = ig.add_node(
            "Rust ownership prevents data races at compile time.",
            Some("rust"),
            None,
        );
        let n2 = ig.add_node(
            "Borrowing allows safe references without ownership transfer.",
            Some("borrow"),
            None,
        );

        ig.add_edge(
            n1,
            n2,
            0.91,
            vec![(topic, PropertyValue::Label("memory".to_string()))],
        );

        let dir = tempdir().unwrap();
        ig.save_to_disk(dir.path()).expect("save should succeed");

        let loaded =
            IndexedKnowledgeGraph::load_from_disk(dir.path()).expect("load should succeed");

        assert_eq!(loaded.graph.node_count(), 2);
        assert_eq!(loaded.graph.edge_count(), 1);

        // The index should have been rebuilt
        let results = loaded.search("ownership borrowing", 5);
        assert!(!results.is_empty());
    }

    #[cfg(feature = "mmap")]
    #[test]
    fn mmap_fast_path_roundtrip() {
        let mut g = KnowledgeGraph::new();
        let topic = g.properties.register("topic");

        let n1 = g.add_node(
            "Large corpus document one about systems programming.",
            Some("doc1"),
            None,
        );
        let n2 = g.add_node(
            "Large corpus document two about memory safety and ownership.",
            Some("doc2"),
            None,
        );

        g.add_edge(
            n1,
            n2,
            0.93,
            vec![(topic, PropertyValue::Label("systems".to_string()))],
        );

        let dir = tempdir().unwrap();
        g.save_to_disk(dir.path()).expect("save failed");

        // Exercises the public ergonomic mmap loader API.
        // (Currently delegates to the proven portable loader; true zero-copy
        // Storage::Mmap path is implemented in BlobStore and ready for activation.)
        let loaded = KnowledgeGraph::load_mmap(dir.path()).expect("mmap load failed");

        assert_eq!(loaded.node_count(), 2);
        assert_eq!(loaded.edge_count(), 1);

        // Basic query still works after "mmap" load (exercises the public API)
        let results = loaded
            .find()
            .with_property("topic")
            .min_strength(0.8)
            .depth(2)
            .limit(10)
            .execute();

        assert!(!results.is_empty());
    }

    #[test]
    fn load_meta_and_version_rejection() {
        // Build and save a small graph
        let mut g = KnowledgeGraph::new();
        let topic = g.properties.register("topic");

        let n1 = g.add_node("Document one for load_meta test.", Some("doc1"), None);
        let n2 = g.add_node("Document two for load_meta test.", Some("doc2"), None);

        g.add_edge(
            n1,
            n2,
            0.88,
            vec![(topic, PropertyValue::Label("test".to_string()))],
        );

        let dir = tempdir().unwrap();
        g.save_to_disk(dir.path()).expect("save should succeed");

        // load_meta should succeed and return useful information
        let meta = KnowledgeGraph::load_meta(dir.path()).expect("load_meta should succeed");
        assert!(meta.is_current_version());
        assert_eq!(meta.version(), GraphMeta::CURRENT_VERSION);
        assert_eq!(meta.node_count, 2);
        assert_eq!(meta.edge_count, 1);
        assert!(meta.blob_total_bytes > 0);

        // Also works through IndexedKnowledgeGraph
        let ig_meta =
            IndexedKnowledgeGraph::load_meta(dir.path()).expect("Indexed load_meta should succeed");
        assert_eq!(ig_meta.node_count, 2);

        // Now corrupt the meta file with a future version and verify rejection
        let mut bad_meta = meta;
        bad_meta.version = 99; // future / incompatible version

        let bad_json = serde_json::to_string_pretty(&bad_meta).expect("serialize bad meta");
        std::fs::write(dir.path().join("graph.meta"), bad_json).expect("write bad meta");

        let err = KnowledgeGraph::load_meta(dir.path()).expect_err("should reject bad version");
        match err {
            GraphError::UnsupportedVersion { found, supported } => {
                assert_eq!(found, 99);
                assert_eq!(supported, GraphMeta::CURRENT_VERSION);
            }
            other => panic!("expected UnsupportedVersion, got {:?}", other),
        }
    }

    #[test]
    fn repair_is_idempotent_and_safe() {
        let dir = tempdir().unwrap();
        let graph_dir = dir.path().join("test-graph");

        // Calling repair on a non-existent directory should be safe
        let report =
            KnowledgeGraph::repair(&graph_dir).expect("repair on non-existent dir should not fail");
        assert_eq!(report.removed_temp_dirs, 0);

        // Create an empty directory and repair it — should still succeed
        fs::create_dir(&graph_dir).unwrap();
        let report2 =
            KnowledgeGraph::repair(&graph_dir).expect("repair on empty dir should succeed");
        assert_eq!(report2.removed_temp_dirs, 0);
    }

    #[test]
    fn deleted_nodes_survive_save_load() {
        let mut g = KnowledgeGraph::new();
        let n0 = g.add_node("Document zero.", Some("zero"), None);
        let n1 = g.add_node("Document one.", Some("one"), None);
        let n2 = g.add_node("Document two.", Some("two"), None);

        // Delete the middle node
        assert!(g.remove_node(n1));
        assert!(g.is_node_deleted(n1));
        assert!(!g.is_node_deleted(n0));
        assert!(!g.is_node_deleted(n2));

        let dir = tempdir().unwrap();
        g.save_to_disk(dir.path()).expect("save");

        let loaded = KnowledgeGraph::load_from_disk(dir.path()).expect("load");
        // Tombstone must survive
        assert!(
            loaded.is_node_deleted(n1),
            "n1 should still be deleted after reload"
        );
        assert!(!loaded.is_node_deleted(n0));
        assert!(!loaded.is_node_deleted(n2));
    }

    #[test]
    fn load_rejects_corrupt_blob_offset() {
        let mut g = KnowledgeGraph::new();
        g.add_node("Hello.", None, None);

        let dir = tempdir().unwrap();
        g.save_to_disk(dir.path()).expect("save");

        // Corrupt graph.meta: push a fake boundary far beyond the actual blob size
        let meta_path = dir.path().join("graph.meta");
        let meta_str = std::fs::read_to_string(&meta_path).unwrap();
        let mut meta: GraphMeta = serde_json::from_str(&meta_str).unwrap();
        meta.chunk_boundaries.push(u64::MAX / 2);
        meta.chunk_checksums.push(0);
        std::fs::write(&meta_path, serde_json::to_string_pretty(&meta).unwrap()).unwrap();

        // Load should return Err, not silently return wrong data
        let result = KnowledgeGraph::load_from_disk(dir.path());
        assert!(
            result.is_err(),
            "expected error on corrupt offset, got {:?}",
            result
        );
    }
}
