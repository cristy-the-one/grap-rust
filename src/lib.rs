//! # grap - Rust-native Knowledge Graph Engine
//!
//! A high-performance, read-optimized graph database purpose-built for
//! storing interconnected text documents and retrieving them by relationship
//! characteristics (property bitmasks, strength-weighted edges, fast traversal).
//!
//! ## Architecture (from core_graph_design.md)
//! - **Hot path**: Arena-allocated `Vec` topology (NodeId/EdgeId handles),
//!   64-bit property bitmasks for O(1) filtering, `outgoing_property_mask`
//!   node-level pruning.
//! - **Cold path**: Chunked `BlobStore` for full text; zero-copy `&str` reads,
//!   optional memory-mapped persistence.
//! - **Entry point**: Incremental BM25 inverted index over blob content for
//!   high-quality seed selection before traversal.
//!
//! The design and all core algorithms are taken directly from the
//! `core_graph_design.md` specification that ships with this crate
//! (see `docs/implementation-plan.md` and the original design doc).
//!
//! ## Status
//! This is an in-progress faithful port of the prototype in the design document.
//! Phase 1 (Core `KnowledgeGraph` + `QueryBuilder`) is the current focus.

#![deny(unsafe_code)]
// The design (core_graph_design.md) intentionally uses one unsafe block for zero-copy UTF-8
// in BlobStore::read. It is isolated, documented, and only constructs from known-valid &str.

#![warn(missing_docs, missing_debug_implementations)]

pub mod ids;
pub mod blob_store;
pub mod properties;
pub mod graph;
pub mod query;

// Re-exports for ergonomic use (will grow with features)
pub use ids::{EdgeId, NodeId, PropertyTag};
pub use properties::{PropertyFilter, PropertyRegistry, PropertyValue};
pub use graph::KnowledgeGraph;
pub use query::{GraphQuery, QueryBuilder, QueryResult};

// Feature-gated modules (stubs for now)
#[cfg(feature = "fulltext")]
pub mod index;

#[cfg(feature = "mmap")]
pub mod serialize;

#[cfg(feature = "sharding")]
pub mod shard;

/// Crate-wide error type (will be expanded in later phases).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GraphError {
    /// Generic I/O or serialization failure.
    Io(String),
    /// Attempted to use more than 64 distinct property types.
    TooManyProperties,
}

impl std::fmt::Display for GraphError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            GraphError::Io(s) => write!(f, "graph io error: {}", s),
            GraphError::TooManyProperties => write!(f, "maximum of 64 property types exceeded"),
        }
    }
}

impl std::error::Error for GraphError {}
