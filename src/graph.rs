//! The core arena-based knowledge graph (Sections 1–9 of core_graph_design.md).
//!
//! `KnowledgeGraph` stores nodes and directed, weighted, property-carrying edges
//! in contiguous `Vec`s. It provides:
//!
//! - Extremely fast `O(1)` edge filtering via 64-bit property bitmasks
//! - `outgoing_property_mask` node-level pruning
//! - A best-first priority traversal (`QueryBuilder`)
//! - Separate blob storage for large text payloads (zero-copy reads)
//!
//! This is the lowest-level, most performance-critical component of the engine.

use crate::blob_store::{BlobRef, BlobStore};
use crate::ids::{EdgeId, NodeId, PropertyTag};
use crate::properties::{PropertyRegistry, PropertyValue};

#[derive(Debug)]
pub(crate) struct Node {
    pub(crate) blob: BlobRef,
    pub(crate) outgoing: Vec<EdgeId>,
    pub(crate) incoming: Vec<EdgeId>,
    pub(crate) label: Option<String>,
    pub(crate) outgoing_property_mask: u64,
}

#[derive(Debug)]
pub struct Edge {
    pub source: NodeId,
    pub target: NodeId,
    pub strength: f64,
    pub(crate) properties: Vec<(PropertyTag, PropertyValue)>,
    pub(crate) property_mask: u64,
}

impl Edge {
    #[inline]
    pub(crate) fn matches_mask(&self, required: u64) -> bool {
        (self.property_mask & required) == required
    }
}

/// The main in-memory knowledge graph.
///
/// Nodes and edges are stored in arena-style `Vec`s. Each node owns a blob
/// reference for its text content. Edges carry a strength (0.0–1.0) and a
/// set of typed properties that are automatically summarized into a `u64`
/// bitmask for fast filtering.
///
/// The graph also holds a [`PropertyRegistry`] that must be used to register
/// all property names before (or during) edge insertion.
#[derive(Debug)]
pub struct KnowledgeGraph {
    pub(crate) nodes: Vec<Node>,
    pub(crate) edges: Vec<Edge>,
    pub(crate) blobs: BlobStore,
    /// Public property registry. Register names here before using them on edges.
    pub properties: PropertyRegistry,
    /// Tombstone markers for soft deletes (critical for sharding + ghost edges).
    /// A node that is `true` here is logically deleted and should be skipped
    /// by traversal, search, and rebalancing.
    pub(crate) deleted: Vec<bool>,
}

impl KnowledgeGraph {
    /// Creates an empty knowledge graph.
    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            edges: Vec::new(),
            blobs: BlobStore::new(),
            properties: PropertyRegistry::new(),
            deleted: Vec::new(),
        }
    }

    /// Adds a new node with the given text content and optional human label.
    ///
    /// Returns a stable [`NodeId`] that can be used to refer to this node
    /// for the rest of the graph's lifetime.
    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> NodeId {
        let blob = self.blobs.store(text);
        let id = NodeId(self.nodes.len() as u32);
        self.nodes.push(Node {
            blob,
            outgoing: Vec::new(),
            incoming: Vec::new(),
            label: label.map(|s| s.to_string()),
            outgoing_property_mask: 0,
        });
        self.deleted.push(false);
        id
    }

    /// Logically removes a node and all its incident edges (soft delete / tombstone).
    ///
    /// The `NodeId` remains valid but the node is marked deleted. This is
    /// required for correct behavior with sharding and ghost edges.
    ///
    /// Returns `true` if the node existed and was removed.
    pub fn remove_node(&mut self, node: NodeId) -> bool {
        let idx = node.0 as usize;
        if idx >= self.nodes.len() || self.deleted.get(idx).copied().unwrap_or(true) {
            return false;
        }

        // Mark as deleted (tombstone)
        self.deleted[idx] = true;

        // Remove from neighbors' edge lists and clear this node's lists
        let outgoing = std::mem::take(&mut self.nodes[idx].outgoing);
        let incoming = std::mem::take(&mut self.nodes[idx].incoming);

        for eid in &outgoing {
            if let Some(e) = self.edges.get(eid.0 as usize) {
                // Remove from target's incoming list
                if let Some(target_incoming) = self.nodes.get_mut(e.target.0 as usize) {
                    target_incoming.incoming.retain(|&x| x != *eid);
                }
            }
        }

        for eid in &incoming {
            if let Some(e) = self.edges.get(eid.0 as usize) {
                // Remove from source's outgoing list
                if let Some(source_outgoing) = self.nodes.get_mut(e.source.0 as usize) {
                    source_outgoing.outgoing.retain(|&x| x != *eid);
                }
            }
        }

        // Optionally clear property mask
        if let Some(n) = self.nodes.get_mut(idx) {
            n.outgoing_property_mask = 0;
        }

        true
    }

    /// Returns true if the node has been logically deleted (tombstone).
    pub fn is_node_deleted(&self, node: NodeId) -> bool {
        self.deleted.get(node.0 as usize).copied().unwrap_or(true)
    }

    /// Returns the full text blob associated with a node (zero-copy when possible).
    pub fn read_blob(&self, node: NodeId) -> &str {
        self.blobs.read(&self.nodes[node.0 as usize].blob)
    }

    /// Returns the optional human-readable label of a node, if one was provided.
    pub fn node_label(&self, node: NodeId) -> Option<&str> {
        self.nodes[node.0 as usize].label.as_deref()
    }

    /// Number of nodes currently in the graph.
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Returns a reference to all edges. Exposed for CLI export / visualization tools.
    pub fn all_edges(&self) -> &Vec<Edge> {
        &self.edges
    }

    /// Number of edges currently in the graph.
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// Adds a directed, weighted edge from `source` to `target`.
    ///
    /// `strength` should normally be in `[0.0, 1.0]`. Higher values indicate
    /// stronger semantic connection.
    ///
    /// Properties are automatically turned into a bitmask and also merged
    /// into the source node's `outgoing_property_mask` for early pruning.
    pub fn add_edge(
        &mut self,
        source: NodeId,
        target: NodeId,
        strength: f64,
        mut props: Vec<(PropertyTag, PropertyValue)>,
    ) -> EdgeId {
        let mut mask = 0u64;
        for (tag, _) in &props {
            mask |= 1u64 << tag.0;
        }
        props.sort_by_key(|(t, _)| t.0);

        let edge_id = EdgeId(self.edges.len() as u32);
        self.edges.push(Edge {
            source,
            target,
            strength,
            properties: props,
            property_mask: mask,
        });

        self.nodes[source.0 as usize].outgoing.push(edge_id);
        self.nodes[target.0 as usize].incoming.push(edge_id);
        self.nodes[source.0 as usize].outgoing_property_mask |= mask;

        edge_id
    }

    // Stubs for the rest of the API (implemented in Phase 1 query work)
    pub fn edge_strength(&self, edge: EdgeId) -> f64 {
        self.edges[edge.0 as usize].strength
    }

    pub fn edge_property(&self, edge: EdgeId, tag: PropertyTag) -> Option<&PropertyValue> {
        self.edges[edge.0 as usize]
            .properties
            .iter()
            .find(|(t, _)| *t == tag)
            .map(|(_, v)| v)
    }

    pub fn edge_endpoints(&self, edge: EdgeId) -> (NodeId, NodeId) {
        let e = &self.edges[edge.0 as usize];
        (e.source, e.target)
    }
}

impl Default for KnowledgeGraph {
    fn default() -> Self {
        Self::new()
    }
}

/// Internal constructor used by the serialization layer.
impl KnowledgeGraph {
    /// Construct a graph from its parts, supplying an explicit deleted bitmap.
    /// `deleted` is padded with `false` if shorter than `nodes.len()`.
    pub(crate) fn from_parts_with_deleted(
        nodes: Vec<Node>,
        edges: Vec<Edge>,
        blobs: BlobStore,
        properties: PropertyRegistry,
        deleted: Vec<bool>,
    ) -> Self {
        let n = nodes.len();
        let mut d = deleted;
        d.resize(n, false); // pad if shorter
        Self { nodes, edges, blobs, properties, deleted: d }
    }

    /// Convenience wrapper that starts with all nodes alive.
    pub(crate) fn from_parts(
        nodes: Vec<Node>,
        edges: Vec<Edge>,
        blobs: BlobStore,
        properties: PropertyRegistry,
    ) -> Self {
        let n = nodes.len();
        Self::from_parts_with_deleted(nodes, edges, blobs, properties, vec![false; n])
    }
}
