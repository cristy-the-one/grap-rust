//! Serialization & Memory-Mapped Persistence (Section 10 of core_graph_design.md).
//!
//! Two modes:
//! - Portable: bincode + JSON meta (always available)
//! - Fast load: memory-mapped blobs when the `mmap` feature is enabled

use std::fs::{self, File};
use std::io::{self, BufReader, BufWriter, Read, Write};
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::blob_store::BlobRef;
use crate::graph::KnowledgeGraph;
use crate::ids::{EdgeId, NodeId};
use crate::ids::PropertyTag;
use crate::properties::PropertyValue;

// ============================================================================
// Serializable mirror types
// ============================================================================

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
pub enum SerializablePropertyValue {
    Float(f64),
    Int(i64),
    Bool(bool),
    Label(Vec<u8>), // store as bytes for robustness
}

#[derive(Serialize, Deserialize, Debug)]
pub struct SerializableNode {
    pub blob_global_offset: u64,
    pub blob_length: u32,
    pub blob_uncompressed_len: u32,
    pub outgoing: Vec<u32>,
    pub incoming: Vec<u32>,
    pub label: Option<String>,
    pub outgoing_property_mask: u64,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct SerializableEdge {
    pub source: u32,
    pub target: u32,
    pub strength: f64,
    pub properties: Vec<(u16, SerializablePropertyValue)>,
    pub property_mask: u64,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct GraphMeta {
    pub version: u32,
    pub node_count: u32,
    pub edge_count: u32,
    pub blob_total_bytes: u64,
    pub chunk_boundaries: Vec<u64>,
    pub chunk_checksums: Vec<u32>,
    pub created_timestamp: u64,
}

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
    pub fn save_to_disk(&self, dir: &Path) -> io::Result<()> {
        fs::create_dir_all(dir)?;

        let chunk_offsets = self.compute_chunk_offsets();

        // 1. Write flat blob file
        let blob_path = dir.join("graph.blobs");
        let mut checksums = Vec::new();
        {
            let mut writer = BufWriter::new(File::create(&blob_path)?);
            for chunk in &self.blobs.chunks {
                writer.write_all(chunk)?;
                checksums.push(crc32(chunk));
            }
            writer.flush()?;
        }

        let total_blob_bytes: u64 = self.blobs.chunks.iter().map(|c| c.len() as u64).sum();

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
            version: 1,
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
        let topo_data = bincode::serialize(&(
            serializable_nodes,
            serializable_edges,
            &self.properties.tag_to_name, // simple for now
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
        let mut offsets = Vec::with_capacity(self.blobs.chunks.len());
        let mut running = 0u64;
        for chunk in &self.blobs.chunks {
            offsets.push(running);
            running += chunk.len() as u64;
        }
        offsets
    }

    /// Load a graph from disk (portable path, no mmap).
    pub fn load_from_disk(dir: &Path) -> io::Result<Self> {
        // Read meta
        let meta_str = fs::read_to_string(dir.join("graph.meta"))?;
        let meta: GraphMeta =
            serde_json::from_str(&meta_str).map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;

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
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("Checksum mismatch for chunk {}", i),
                ));
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

        // TODO: reconstruct full KnowledgeGraph from topology + blobs
        // This is a large piece — will be completed in subsequent steps.

        let mut graph = KnowledgeGraph::new();
        // For now we just return an empty graph with correct blob data reconstructed
        // Full reconstruction will be done next.
        graph.blobs.chunks = chunks;

        Ok(graph)
    }
}

#[cfg(feature = "mmap")]
pub fn load_with_mmap(dir: &Path) -> io::Result<KnowledgeGraph> {
    // Placeholder for mmap-based fast load path.
    // Will use memmap2 to memory-map graph.blobs and reconstruct BlobRefs.
    KnowledgeGraph::load_from_disk(dir)
}
