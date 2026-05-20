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
//! Phases 1–4 are complete (core graph, full-text index, persistence + mmap, sharding + ghost edges + rebalancing).
//! The 0.2 on-disk segmented text index work is fully integrated and always available (no feature flag).
//!
//! Persistence is versioned. Use [`load_meta`](KnowledgeGraph::load_meta) + [`GraphMeta`]
//! to inspect saved graphs (version, size, checksums) without loading the full data.
//!
//! ## On-Disk Segmented Text Index
//! The crate ships with a production-quality segmented on-disk inverted index:
//!
//! - `IndexedKnowledgeGraph::new_on_disk()` and `OnDiskTextIndex`
//! - WAL + tiered merge + true incremental `vacuum()`
//! - `save_to_dir` / `load_from_dir` with automatic backend detection
//! - Background workers, `SegmentInfo`, and segment migration support
//! - First-class integration with `ShardedGraph` (per-shard indexes + ghost edges)
//!
//! The main types (`OnDiskIndex`, `OnDiskSegment`, `SegmentInfo`, `MergeWorkerHandle`, etc.)
//! are public and stable. Power users can work directly with the engine in `on_disk_index`.

#![deny(unsafe_code)]
// The design (core_graph_design.md) intentionally uses one unsafe block for zero-copy UTF-8
// in BlobStore::read. It is isolated, documented, and only constructs from known-valid &str.
#![warn(missing_docs, missing_debug_implementations)]

pub mod blob_store;
pub mod graph;
pub mod ids;
pub mod index;
pub mod properties;
pub mod query; // Full-text BM25 index (Phase 2)

// On-disk segmented text index (0.2) — always available.
// Power users can access the full modules at `grap::on_disk_index` and `grap::on_disk_text_index`.
pub mod on_disk_index;
pub mod on_disk_text_index;

pub mod serialize; // Persistence (save/load + optional mmap) - Phase 3 in progress

// Re-exports for ergonomic use
pub use crate::index::TextIndex;

// Flat re-exports for the primary on-disk types (first-class public API).
// Advanced/escape-hatch types (LocalDocId, Posting, SegmentBuilder, ActiveBuffer,
// merge_segments, etc.) remain available under the full `on_disk_index` module.
pub use on_disk_index::{
    MergeCommand, MergeStats, MergeWorkerHandle, OnDiskIndex, OnDiskSegment, SegmentInfo,
    TieredMergePolicy,
};
pub use on_disk_text_index::OnDiskTextIndex;

pub use graph::KnowledgeGraph;
pub use ids::{EdgeId, NodeId, PropertyTag};
pub use properties::{PropertyFilter, PropertyRegistry, PropertyValue};
pub use query::{GraphQuery, QueryBuilder, QueryResult};

// Index types (always available – makes the engine truly useful)
pub use index::{
    CombinedResult, DefaultTokenizer, FusionWeights, HybridWeights, IndexedKnowledgeGraph,
    InvertedIndex, TextSearchResult, Tokenizer,
};

pub use shard::{
    GhostEdge, GlobalNodeId, RebalancePlan, ShardId, ShardRouter, ShardedGraph, ShardedQueryResult,
};

pub use serialize::{GraphMeta, RepairReport, CURRENT_GRAPH_FORMAT_VERSION};

pub mod shard; // Graph sharding (Phase 4)

/// Crate-wide error type for all fallible operations in `grap`.
///
/// This type is used (or will be used) by persistence, sharding, and
/// property registry operations.
#[derive(thiserror::Error, Debug, Clone, PartialEq, Eq)]
pub enum GraphError {
    /// I/O or (de)serialization failure.
    #[error("I/O error: {0}")]
    Io(String),

    /// The file was written with a newer (or unknown) format version.
    #[error("unsupported graph format version {found} (supported: {supported})")]
    UnsupportedVersion {
        /// Version found in the on-disk file.
        found: u16,
        /// Highest version this library understands.
        supported: u16,
    },

    /// CRC32 checksum did not match — file is likely corrupted.
    #[error("data corruption detected (checksum mismatch)")]
    ChecksumMismatch,

    /// Attempted to register more than 64 distinct property types.
    #[error("maximum of 64 property types exceeded (bitmask limit)")]
    TooManyProperties,

    /// A requested node or shard does not exist in the current graph.
    #[error("not found: {0}")]
    NotFound(String),

    /// A sharding or distributed query operation failed.
    #[error("sharding error: {0}")]
    Sharding(String),

    /// Generic error with a message (used as a last resort during early Phase 5).
    #[error("{0}")]
    Other(String),
}

// Allow easy conversion from io::Error in save/load paths.
impl From<std::io::Error> for GraphError {
    fn from(e: std::io::Error) -> Self {
        GraphError::Io(e.to_string())
    }
}

impl From<serde_json::Error> for GraphError {
    fn from(e: serde_json::Error) -> Self {
        GraphError::Io(format!("json error: {}", e))
    }
}

impl From<bincode::Error> for GraphError {
    fn from(e: bincode::Error) -> Self {
        GraphError::Io(format!("bincode error: {}", e))
    }
}

/// A trait for receiving internal observability events from the graph engine.
///
/// This allows users to integrate `grap` with their own metrics backend
/// (Prometheus, OpenTelemetry, StatsD, custom dashboards, etc.) without
/// the library pulling in any particular observability crate.
///
/// All methods have default no-op implementations, so you only override
/// the events you care about.
///
/// # Example
///
/// ```rust
/// use std::sync::atomic::{AtomicU64, Ordering};
/// use grap::Telemetry;
///
/// #[derive(Default)]
/// struct MyMetrics {
///     nodes_visited: AtomicU64,
/// }
///
/// impl Telemetry for MyMetrics {
///     fn record_traversal_nodes_visited(&self, n: u64) {
///         self.nodes_visited.fetch_add(n, Ordering::Relaxed);
///     }
/// }
/// ```
pub trait Telemetry: Send + Sync + 'static {
    /// Called for every node visited during a `search_and_traverse` call.
    fn record_traversal_nodes_visited(&self, _count: u64) {}

    /// Called when the 64-bit property mask causes early pruning of edges.
    fn record_mask_prune(&self, _pruned: u64) {}

    /// Called when the router attempts to fetch a node from a remote shard
    /// (via a ghost edge). `hit` is true if the value was served from the
    /// per-shard remote cache.
    fn record_remote_fetch(&self, _hit: bool) {}

    /// Called after `execute_rebalance` finishes.
    fn record_rebalance(&self, _nodes_moved: u64) {}
}

/// A no-op implementation of [`Telemetry`].
///
/// This is used by default when no custom telemetry is provided.
#[derive(Debug, Clone, Copy, Default)]
pub struct NoopTelemetry;

impl Telemetry for NoopTelemetry {}
