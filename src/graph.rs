//! Core KnowledgeGraph, Node/Edge arena storage, add/query primitives.
//! This is the heart of the engine (core_graph_design.md Sections 3,5,6).

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
pub(crate) struct Edge {
    pub(crate) source: NodeId,
    pub(crate) target: NodeId,
    pub(crate) strength: f64,
    pub(crate) properties: Vec<(PropertyTag, PropertyValue)>,
    pub(crate) property_mask: u64,
}

impl Edge {
    #[inline]
    pub(crate) fn matches_mask(&self, required: u64) -> bool {
        (self.property_mask & required) == required
    }
}

#[derive(Debug)]
pub struct KnowledgeGraph {
    pub(crate) nodes: Vec<Node>,
    pub(crate) edges: Vec<Edge>,
    pub(crate) blobs: BlobStore,
    pub properties: PropertyRegistry,
}

impl KnowledgeGraph {
    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            edges: Vec::new(),
            blobs: BlobStore::new(),
            properties: PropertyRegistry::new(),
        }
    }

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
        id
    }

    pub fn read_blob(&self, node: NodeId) -> &str {
        self.blobs.read(&self.nodes[node.0 as usize].blob)
    }

    pub fn node_label(&self, node: NodeId) -> Option<&str> {
        self.nodes[node.0 as usize].label.as_deref()
    }

    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

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
