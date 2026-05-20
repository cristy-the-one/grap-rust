//! Opaque, cheap identifiers used throughout the graph.
//!
//! All identifiers are small `Copy` newtypes wrapping `u32` or `u16` integers.
//! They are designed for high-performance arena-based storage as described in
//! `core_graph_design.md`.

use std::fmt;

/// A handle to a node inside a [`KnowledgeGraph`](crate::KnowledgeGraph).
///
/// `NodeId`s are cheap, `Copy`, and stable for the lifetime of the graph
/// (they are indices into internal `Vec`s).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct NodeId(pub u32);

/// A handle to an edge inside a [`KnowledgeGraph`](crate::KnowledgeGraph).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct EdgeId(pub u32);

/// A tag identifying a registered property type.
///
/// Property tags are assigned by [`PropertyRegistry`](crate::PropertyRegistry)
/// and are used to build the 64-bit property bitmasks that enable O(1) edge filtering.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct PropertyTag(pub u16);

impl fmt::Display for NodeId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "N{}", self.0)
    }
}

impl fmt::Display for EdgeId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "E{}", self.0)
    }
}

impl fmt::Display for PropertyTag {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "P{}", self.0)
    }
}
