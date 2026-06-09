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
pub mod vector_index; // ANN / vector index (0.3/0.4 Phase 0+)

pub mod serialize; // Persistence (save/load + optional mmap) - Phase 3 in progress

pub mod graph_ops; // Graph mutations (node merging, content updates)

pub mod lifecycle; // Versioning, activation, and lifecycle (Task 01)

// Re-exports for ergonomic use
pub use crate::index::TextIndex;
pub use graph_ops::MergeReport;
pub use lifecycle::{
    Activation, ActivationState, AuditEvent, ChangeStatus, ContentHash, EventLink, LifecycleError,
    PublishLevel, ReconstructionReport, RetentionReport, RunKind, RunRecord, RunStatus, Version,
    VersionKind, Workspace,
};

// Flat re-exports for the primary on-disk types (first-class public API).
// Advanced/escape-hatch types (LocalDocId, Posting, SegmentBuilder, ActiveBuffer,
// merge_segments, etc.) remain available under the full `on_disk_index` module.
pub use on_disk_index::{
    MergeCommand, MergeStats, MergeWorkerHandle, OnDiskIndex, OnDiskSegment, SegmentInfo,
    TieredMergePolicy,
};
pub use on_disk_text_index::OnDiskTextIndex;

pub use graph::{KnowledgeGraph, NodeIntegrity};
pub use ids::{EdgeId, NodeId, PropertyTag};
pub use properties::{PropertyFilter, PropertyRegistry, PropertyValue};
pub use query::{GraphQuery, QueryBuilder, QueryResult};

// Index types (always available – makes the engine truly useful)
pub use index::{
    CombinedResult, DefaultTokenizer, FusionMode, FusionWeights, HybridWeights,
    IndexedKnowledgeGraph, InvertedIndex, TextSearchResult, Tokenizer,
};

pub use vector_index::{BruteForceVectorIndex, VectorIndex, VectorIndexStats};

#[cfg(feature = "ann-hnsw")]
pub use vector_index::{HNSWParams, HNSWVectorIndex};

pub use shard::{
    GhostEdge, GlobalNodeId, RebalancePlan, ShardId, ShardRouter, ShardedGraph, ShardedQueryResult,
};

pub use serialize::{GraphMeta, RepairReport, CURRENT_GRAPH_FORMAT_VERSION};

pub mod shard; // Graph sharding (Phase 4)

pub mod analytics; // Structural graph analytics (blast_radius, cycles, orphans, health)

pub mod ranking; // Rank-based fusion (RRF) and centrality scoring

pub mod wire; // Columnar string-interned wire encoding (`#MUNCH`-style)

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

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

/// Internal shared counter storage for [`CountingTelemetry`].
///
/// Not part of the public API; users interact through [`CountingTelemetry`]
/// and [`TelemetrySnapshot`].
#[derive(Debug, Default)]
struct TelemetryCounters {
    traversal_nodes_visited: AtomicU64,
    mask_prunes: AtomicU64,
    remote_fetch_hits: AtomicU64,
    remote_fetch_misses: AtomicU64,
    rebalance_nodes_moved: AtomicU64,
}

/// A concrete [`Telemetry`] that accumulates counters in shared atomics.
///
/// Cheap and thread-safe. Clones share the same counters, so hand one clone
/// to `ShardedGraph::set_telemetry` and keep another to read [`snapshot`].
///
/// # Example
///
/// ```rust
/// use grap::{CountingTelemetry, Telemetry};
///
/// let t = CountingTelemetry::new();
/// let reader = t.clone();
/// t.record_traversal_nodes_visited(10);
/// assert_eq!(reader.snapshot().traversal_nodes_visited, 10);
/// ```
///
/// [`snapshot`]: CountingTelemetry::snapshot
#[derive(Debug, Clone, Default)]
pub struct CountingTelemetry {
    inner: Arc<TelemetryCounters>,
}

/// A point-in-time copy of [`CountingTelemetry`]'s counters.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TelemetrySnapshot {
    /// Nodes visited during traversals.
    pub traversal_nodes_visited: u64,
    /// Edges rejected early by the 64-bit property mask.
    pub mask_prunes: u64,
    /// Remote shard fetches that hit.
    pub remote_fetch_hits: u64,
    /// Remote shard fetches that missed.
    pub remote_fetch_misses: u64,
    /// Nodes moved by rebalancing.
    pub rebalance_nodes_moved: u64,
}

impl CountingTelemetry {
    /// Create a fresh recorder with zeroed counters.
    pub fn new() -> Self {
        Self::default()
    }

    /// Read the current counter values atomically.
    pub fn snapshot(&self) -> TelemetrySnapshot {
        TelemetrySnapshot {
            traversal_nodes_visited: self
                .inner
                .traversal_nodes_visited
                .load(Ordering::Relaxed),
            mask_prunes: self.inner.mask_prunes.load(Ordering::Relaxed),
            remote_fetch_hits: self.inner.remote_fetch_hits.load(Ordering::Relaxed),
            remote_fetch_misses: self.inner.remote_fetch_misses.load(Ordering::Relaxed),
            rebalance_nodes_moved: self.inner.rebalance_nodes_moved.load(Ordering::Relaxed),
        }
    }
}

impl Telemetry for CountingTelemetry {
    fn record_traversal_nodes_visited(&self, count: u64) {
        self.inner
            .traversal_nodes_visited
            .fetch_add(count, Ordering::Relaxed);
    }

    fn record_mask_prune(&self, pruned: u64) {
        self.inner.mask_prunes.fetch_add(pruned, Ordering::Relaxed);
    }

    fn record_remote_fetch(&self, hit: bool) {
        if hit {
            self.inner
                .remote_fetch_hits
                .fetch_add(1, Ordering::Relaxed);
        } else {
            self.inner
                .remote_fetch_misses
                .fetch_add(1, Ordering::Relaxed);
        }
    }

    fn record_rebalance(&self, nodes_moved: u64) {
        self.inner
            .rebalance_nodes_moved
            .fetch_add(nodes_moved, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::{CountingTelemetry, Telemetry};

    #[test]
    fn counting_telemetry_accumulates_and_snapshots() {
        let t = CountingTelemetry::new();
        let handle = t.clone(); // clone shares the same atomics
        t.record_traversal_nodes_visited(3);
        t.record_traversal_nodes_visited(2);
        t.record_mask_prune(4);
        t.record_remote_fetch(true);
        t.record_remote_fetch(false);
        t.record_rebalance(7);
        let s = handle.snapshot();
        assert_eq!(s.traversal_nodes_visited, 5);
        assert_eq!(s.mask_prunes, 4);
        assert_eq!(s.remote_fetch_hits, 1);
        assert_eq!(s.remote_fetch_misses, 1);
        assert_eq!(s.rebalance_nodes_moved, 7);
    }
}
