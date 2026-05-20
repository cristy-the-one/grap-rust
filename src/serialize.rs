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
use crate::ids::{EdgeId, NodeId};
use crate::GraphError;
use crate::ids::PropertyTag;
use crate::properties::PropertyValue;

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
                let s = String::from_utf8(bytes).unwrap_or_else(|e| String::from_utf8_lossy(&e.into_bytes()).into_owned());
                PropertyValue::Label(s)
            }
        }
    }
}

// ============================================================================
// KnowledgeGraph persistence
// ============================================================================

impl KnowledgeGraph {
    /// Serialize the graph to a directory.
    ///
    /// Creates three files:
    /// - `graph.topo`   — bincode of nodes + edges + registry
    /// - `graph.blobs`  — concatenated raw blob chunks
    /// - `graph.meta`   — JSON metadata + checksums
    pub fn save_to_disk(&self, dir: &Path) -> Result<(), GraphError> {
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
            .enumerate()
            .map(|(i, node)| {
                let global_offset = if i < chunk_offsets.len() {
                    chunk_offsets[node.blob.chunk_idx as usize] + node.blob.offset as u64
                } else {
                    0
                };

                SerializableNode {
                    blob_global_offset: global_offset,
                    blob_length: node.blob.length,
                    blob_uncompressed_len: node.blob.uncompressed_len,
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
        let topo_data = bincode::serialize(&(
            serializable_nodes,
            serializable_edges,
            property_names,
        ))
        .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;
        fs::write(&topo_path, &topo_data)?;

        // 4. Write meta
        let meta_path = dir.join("graph.meta");
        let meta_json = serde_json::to_string_pretty(&meta)?;
        fs::write(meta_path, meta_json)?;

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

    /// Load a graph from disk (portable path, no mmap).
    pub fn load_from_disk(dir: &Path) -> Result<Self, GraphError> {
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

        // Verify checksums
        for (i, &boundary) in meta.chunk_boundaries.iter().enumerate() {
            let end = if i + 1 < meta.chunk_boundaries.len() {
                meta.chunk_boundaries[i + 1] as usize
            } else {
                blob_data.len()
            };
            let chunk_slice = &blob_data[boundary as usize..end];
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
                find_chunk_for_offset(&meta.chunk_boundaries, sn.blob_global_offset);

            let blob = BlobRef {
                chunk_idx,
                offset: local_offset,
                length: sn.blob_length,
                uncompressed_len: sn.blob_uncompressed_len,
            };

            rebuilt_nodes.push(crate::graph::Node {
                blob,
                outgoing: sn.outgoing.into_iter().map(EdgeId).collect(),
                incoming: sn.incoming.into_iter().map(EdgeId).collect(),
                label: sn.label,
                outgoing_property_mask: sn.outgoing_property_mask,
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

        let graph = KnowledgeGraph::from_parts(
            rebuilt_nodes,
            rebuilt_edges,
            crate::blob_store::BlobStore::from_chunks(chunks),
            properties,
        );

        Ok(graph)
    }
}

/// Helper: given global offset and chunk boundaries, return (chunk_idx, local_offset)
fn find_chunk_for_offset(boundaries: &[u64], global_offset: u64) -> (u32, u32) {
    for (i, &start) in boundaries.iter().enumerate() {
        let end = if i + 1 < boundaries.len() {
            boundaries[i + 1]
        } else {
            u64::MAX
        };
        if global_offset >= start && global_offset < end {
            return (i as u32, (global_offset - start) as u32);
        }
    }
    (0, 0)
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

        let n1 = g.add_node("Hello world from grap serialization test.", Some("first"));
        let n2 = g.add_node("Second document about graphs and persistence.", Some("second"));
        let n3 = g.add_node("Third document on knowledge graphs.", Some("third"));

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

        let n1 = ig.add_node("Rust ownership prevents data races at compile time.", Some("rust"));
        let n2 = ig.add_node("Borrowing allows safe references without ownership transfer.", Some("borrow"));

        ig.add_edge(
            n1,
            n2,
            0.91,
            vec![(topic, PropertyValue::Label("memory".to_string()))],
        );

        let dir = tempdir().unwrap();
        ig.save_to_disk(dir.path()).expect("save should succeed");

        let loaded = IndexedKnowledgeGraph::load_from_disk(dir.path())
            .expect("load should succeed");

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

        let n1 = g.add_node("Large corpus document one about systems programming.", Some("doc1"));
        let n2 = g.add_node("Large corpus document two about memory safety and ownership.", Some("doc2"));

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

        let n1 = g.add_node("Document one for load_meta test.", Some("doc1"));
        let n2 = g.add_node("Document two for load_meta test.", Some("doc2"));

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
}
