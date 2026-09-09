//! Graph Sharding (Section 12 of core_graph_design.md)
//!
//! Distributes nodes across multiple `IndexedKnowledgeGraph` instances (shards)
//! using consistent hashing. Cross-shard edges are represented as "ghost edges".
//!
//! This is an in-process sharding implementation suitable for scaling beyond
//! single-machine memory while keeping a unified query API.

use std::collections::HashMap;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::ids::{EdgeId, NodeId};
use crate::index::IndexedKnowledgeGraph;

/// Identifies one shard inside a [`ShardRouter`] or [`ShardedGraph`].
///
/// Shard IDs are dense integers from `0` to `shard_count-1`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, serde::Serialize, serde::Deserialize,
)]
pub struct ShardId(pub u16);

/// A node identifier that is unique across an entire sharded cluster.
///
/// Contains both the shard that owns the node and the local `NodeId` inside
/// that shard. This is the primary handle returned by [`ShardedGraph::add_node`].
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct GlobalNodeId {
    /// Which shard the node lives on.
    pub shard: ShardId,
    /// The local node identifier inside that shard.
    pub local: NodeId,
}

/// Represents a cross-shard reference ("ghost edge").
///
/// When a logical edge would connect two nodes that hash to different shards,
/// the source shard stores a `GhostEdge` pointing at the remote target.
/// During distributed traversal the system follows these ghost edges by
/// fetching data from the target shard (with caching).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct GhostEdge {
    /// The local node (on this shard) that has an outgoing cross-shard reference.
    pub local_source: NodeId,
    /// The target node living on a different shard.
    pub remote_target: GlobalNodeId,
    /// Strength of the relationship.
    pub strength: f64,
    /// Property names are independent of remote shard tag numbering.
    #[serde(default)]
    pub properties: Vec<(String, crate::properties::PropertyValue)>,
}

/// One hop in a path that may have crossed shard boundaries.
#[derive(Debug, Clone)]
pub enum ShardedPathHop {
    /// A normal local edge (no shard crossing).
    Local(EdgeId),
    /// A hop that followed a ghost edge into another shard.
    CrossShard {
        /// The node we came from (on the previous shard).
        from: GlobalNodeId,
        /// The node we arrived at (on the remote shard).
        to: GlobalNodeId,
        /// Strength of the ghost edge that was followed.
        strength: f64,
    },
}

/// The result of calling [`ShardRouter::plan_rebalance`].
///
/// Contains both traditional per-node migrations and (new) segment-level
/// recommendations for more efficient rebalancing when using the on-disk
/// inverted index.
#[derive(Debug, Clone)]
pub struct RebalancePlan {
    /// Number of shards the cluster currently has.
    pub old_shard_count: u16,
    /// Target number of shards after rebalancing.
    pub new_shard_count: u16,
    /// List of nodes that must migrate, together with their new target shard.
    pub node_migrations: Vec<(GlobalNodeId, ShardId)>,

    /// Segment-level migration recommendations (new in segment-aware rebalancing).
    ///
    /// When a whole `OnDiskSegment` would benefit from moving to another shard
    /// (cheaper I/O, better locality), it is listed here. Executors of the plan
    /// can use this information to perform bulk segment moves instead of many
    /// individual node migrations.
    pub segment_migrations: Vec<SegmentMigration>,
}

/// Describes a recommendation to move an entire on-disk segment during rebalancing.
#[derive(Debug, Clone)]
pub struct SegmentMigration {
    /// The shard the segment currently lives on.
    pub source_shard: ShardId,
    /// Index of the segment within the source shard's list of sealed segments
    /// (as returned by `segment_distribution()` / `sealed_segment_info()`).
    pub segment_index: usize,
    /// The recommended target shard for this segment.
    pub target_shard: ShardId,
    /// Approximate number of documents in the segment.
    pub doc_count: u32,
}

/// Aggregate statistics for a [`ShardedGraph`].
///
/// Obtained via [`ShardedGraph::stats`]. Useful for monitoring and dashboards.
#[derive(Debug, Clone)]
pub struct ShardedGraphStats {
    /// Total number of shards in the cluster.
    pub shard_count: usize,
    /// Total nodes across all shards.
    pub total_nodes: usize,
    /// Total ghost edges in the cluster (cross-shard references).
    pub total_ghost_edges: usize,
    /// Per-shard node counts (index corresponds to shard id).
    pub nodes_per_shard: Vec<usize>,
    /// Per-shard ghost edge counts.
    pub ghost_edges_per_shard: Vec<usize>,

    /// Total number of cached remote nodes across all shards.
    /// Useful for understanding memory overhead of cross-shard caching.
    pub remote_cache_entries: usize,
    /// Remote cache size per shard.
    pub remote_cache_per_shard: Vec<usize>,
}

/// Result row returned from a distributed `search_and_traverse` (or phrase variant)
/// on a sharded graph.
///
/// Contains both relevance signals and the actual path taken, which may
/// include cross-shard hops.
#[derive(Debug, Clone)]
pub struct ShardedQueryResult {
    /// The global identifier of the reached node.
    pub node: GlobalNodeId,
    /// BM25-style text relevance score from the seed selection phase.
    pub text_score: f64,
    /// Cosine similarity to the query vector (0.0 when no vector query or no embedding).
    pub vector_score: f64,
    /// Cumulative strength of the best graph path from a seed to this node.
    pub graph_strength: f64,
    /// Final blended score using [`HybridWeights`] (or legacy FusionWeights for non-hybrid paths).
    pub fused_score: f64,
    /// Number of hops from the nearest seed node.
    pub depth: u32,
    /// The path taken. May contain [`ShardedPathHop::CrossShard`] entries when
    /// the query crossed shard boundaries via ghost edges.
    pub path: Vec<ShardedPathHop>,
    /// A short prefix of the node's text content (useful for display / debugging).
    pub text: String,
}

/// Cached information about a remote node fetched from another shard during traversal.
///
/// This is an internal optimization used by [`ShardRouter`] to reduce
/// cross-shard fetches when following ghost edges.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub(crate) struct CachedRemoteNode {
    /// Optional human-readable label of the remote node.
    pub label: Option<String>,
    /// A short prefix of the node's text content (for display / debugging).
    pub blob_snippet: String,
    /// Number of outgoing edges from this remote node (cached for pruning decisions).
    pub outgoing_edge_count: u32,
    /// Whether this remote node currently has any outgoing ghost edges.
    pub has_outgoing_ghosts: bool,
}

/// Internal representation of one shard.
///
/// Most users interact with this only indirectly through [`ShardedGraph`] or
/// [`ShardRouter`].
pub(crate) struct Shard {
    /// Identifier of this shard.
    #[allow(dead_code)]
    pub id: ShardId,
    /// The actual graph + inverted index living on this shard.
    pub graph: IndexedKnowledgeGraph,
    /// Outgoing cross-shard references ("ghost edges").
    pub ghost_edges: HashMap<u32, Vec<GhostEdge>>,
    /// Cache of remote node data fetched during distributed traversals.
    remote_cache: HashMap<GlobalNodeId, CachedRemoteNode>,
}

impl Shard {
    pub(crate) fn remote_cache_size(&self) -> usize {
        self.remote_cache.len()
    }
}

impl Shard {
    /// Create an empty shard with the given id.
    pub(crate) fn new(id: ShardId) -> Self {
        Self {
            id,
            graph: IndexedKnowledgeGraph::new(),
            ghost_edges: HashMap::new(),
            remote_cache: HashMap::new(),
        }
    }

    /// Ensure a remote node is present in the cache.
    /// This is the recommended way to populate the cache during traversal.
    /// Ensure we have a cached copy of a remote node (best-effort).
    #[allow(dead_code)]
    pub(crate) fn ensure_remote_node_cached(
        &mut self,
        target: GlobalNodeId,
        remote_shard: &IndexedKnowledgeGraph,
    ) {
        if self.remote_cache.contains_key(&target) {
            return;
        }

        let label = remote_shard
            .graph
            .node_label(target.local)
            .map(|s| s.to_string());
        let text = remote_shard.read_blob(target.local);
        let snippet = text.chars().take(120).collect::<String>();

        let cached = CachedRemoteNode {
            label,
            blob_snippet: snippet,
            outgoing_edge_count: 0,
            has_outgoing_ghosts: false,
        };

        self.remote_cache.insert(target, cached);
    }
}

/// Simple consistent hash ring used to assign nodes to shards.
///
/// Uses FNV-1a hashing + virtual nodes for good distribution (as described
/// in the original design document).
pub(crate) struct ConsistentHashRing {
    ring: Vec<(u64, ShardId)>,
    routing_version: u32,
}

impl ConsistentHashRing {
    /// Create a new ring for the given number of shards.
    ///
    /// `virtual_nodes` controls how many virtual nodes each real shard gets
    /// (higher = better balance, more memory).
    pub(crate) fn new(shard_count: u16, virtual_nodes: u16) -> Self {
        Self::with_version(shard_count, virtual_nodes, 2)
    }

    fn with_version(shard_count: u16, virtual_nodes: u16, routing_version: u32) -> Self {
        assert!(shard_count > 0, "a hash ring requires at least one shard");
        let mut ring = Vec::new();

        for s in 0..shard_count {
            let shard = ShardId(s);
            for v in 0..virtual_nodes {
                let key = if routing_version == 1 {
                    fnv64(&[s as u8, (v >> 8) as u8, v as u8])
                } else {
                    fxhash64(&[(s >> 8) as u8, s as u8, (v >> 8) as u8, v as u8])
                };
                ring.push((key, shard));
            }
        }

        ring.sort_by_key(|&(k, _)| k);
        Self {
            ring,
            routing_version,
        }
    }

    pub(crate) fn locate(&self, key: &[u8]) -> ShardId {
        let h = if self.routing_version == 1 {
            fnv64(key)
        } else {
            fxhash64(key)
        };
        // Binary search for the first entry >= h (with wrap-around)
        match self.ring.binary_search_by_key(&h, |&(k, _)| k) {
            Ok(i) => self.ring[i].1,
            Err(i) => {
                if i < self.ring.len() {
                    self.ring[i].1
                } else {
                    self.ring[0].1
                }
            }
        }
    }
}

/// Fast non-cryptographic hash (FNV-1a style) as suggested in the design.
fn fnv64(data: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf29ce484222325;
    for &byte in data {
        hash ^= byte as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

fn fxhash64(data: &[u8]) -> u64 {
    let mut hash = fnv64(data);
    // Avalanche short virtual-node keys as well as document hashes. Raw FNV
    // clusters adjacent three-byte keys into narrow arcs of the ring.
    hash ^= hash >> 30;
    hash = hash.wrapping_mul(0xbf58476d1ce4e5b9);
    hash ^= hash >> 27;
    hash = hash.wrapping_mul(0x94d049bb133111eb);
    hash ^ (hash >> 31)
}

/// Low-level coordinator that manages a set of shards and routes operations across them.
///
/// Most users should prefer the higher-level [`ShardedGraph`] facade.
/// `ShardRouter` is exposed for advanced scenarios where more control is needed
/// (direct access to per-shard graphs, custom property registration, etc.).
pub struct ShardRouter {
    /// Direct access to the individual shards (advanced use only).
    pub(crate) shards: Vec<Shard>,
    hash_ring: ConsistentHashRing,
    /// User-provided telemetry hook (defaults to no-op).
    telemetry: std::sync::Arc<dyn crate::Telemetry>,
}

impl std::fmt::Debug for ShardRouter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ShardRouter")
            .field("shard_count", &self.shards.len())
            .finish_non_exhaustive()
    }
}

impl ShardRouter {
    /// Create a new router with the given number of shards.
    ///
    /// Nodes are distributed using consistent hashing based on their content.
    pub fn new(shard_count: u16) -> Self {
        let mut shards = Vec::new();
        for i in 0..shard_count {
            shards.push(Shard::new(ShardId(i)));
        }
        Self {
            shards,
            hash_ring: ConsistentHashRing::new(shard_count, 150),
            telemetry: std::sync::Arc::new(crate::NoopTelemetry),
        }
    }

    /// Replace the telemetry hook used by this router.
    ///
    /// Useful for plugging in custom metrics after construction.
    pub fn set_telemetry(&mut self, telemetry: impl crate::Telemetry + 'static) {
        self.telemetry = std::sync::Arc::new(telemetry);
    }

    /// Decide which shard owns a new node based on its content (consistent hashing).
    fn shard_for_new_node(&self, text: &str) -> ShardId {
        self.hash_ring.locate(text.as_bytes())
    }

    /// Safe accessor for a shard by id. Returns a clear error for bad GIDs.
    fn shard(&self, id: ShardId) -> Result<&Shard, crate::GraphError> {
        let idx = id.0 as usize;
        if idx >= self.shards.len() {
            Err(crate::GraphError::NotFound(format!(
                "shard {} does not exist (have {} shards)",
                id.0,
                self.shards.len()
            )))
        } else {
            Ok(&self.shards[idx])
        }
    }

    /// Add a node to the appropriate shard (chosen via consistent hashing)
    /// and return its [`GlobalNodeId`].
    pub fn add_node(
        &mut self,
        text: &str,
        label: Option<&str>,
    ) -> Result<GlobalNodeId, crate::GraphError> {
        self.add_node_with_embedding(text, label, None)
    }

    /// Add a node (with optional embedding) to the appropriate shard
    /// (chosen via consistent hashing) and return its [`GlobalNodeId`].
    pub fn add_node_with_embedding(
        &mut self,
        text: &str,
        label: Option<&str>,
        embedding: Option<&[f32]>,
    ) -> Result<GlobalNodeId, crate::GraphError> {
        let shard_id = self.shard_for_new_node(text);
        let shard = &mut self.shards[shard_id.0 as usize];

        let local_id = shard.graph.add_node(text, label, embedding);
        Ok(GlobalNodeId {
            shard: shard_id,
            local: local_id,
        })
    }

    /// Returns the SHA-256 content hash of the node's text blob (or None if not found).
    pub fn node_content_hash(&self, gid: GlobalNodeId) -> Option<[u8; 32]> {
        let shard_idx = gid.shard.0 as usize;
        if shard_idx >= self.shards.len() {
            return None;
        }
        self.shards[shard_idx].graph.node_content_hash(gid.local)
    }

    /// Self-integrity check for a globally addressed node.
    pub fn verify_node(&self, gid: GlobalNodeId) -> crate::graph::NodeIntegrity {
        let shard_idx = gid.shard.0 as usize;
        if shard_idx >= self.shards.len() {
            return crate::graph::NodeIntegrity::Unknown;
        }
        self.shards[shard_idx].graph.verify_node(gid.local)
    }

    /// Removes a node from the cluster.
    ///
    /// - Marks a tombstone in the owning shard's text index.
    /// - Removes the node and its local edges on the owning shard.
    /// - Triggers (best-effort) cleanup of ghost edges pointing to this node
    ///   on all other shards.
    pub fn remove_node(&mut self, gid: GlobalNodeId) -> Result<bool, crate::GraphError> {
        let shard_idx = gid.shard.0 as usize;
        if shard_idx >= self.shards.len() {
            return Err(crate::GraphError::NotFound(format!(
                "shard {} does not exist",
                gid.shard.0
            )));
        }

        let removed = self.shards[shard_idx].graph.remove_node(gid.local);

        if removed {
            // Aggressive cleanup: after a delete we do a full ghost edge sweep
            // across all shards. This is the "walk edges on other shards" behavior.
            // For very large clusters this can be made optional or scheduled via
            // a background worker (see MergeWorker infrastructure).
            self.cleanup_ghost_edges();
        }

        Ok(removed)
    }

    /// Full aggressive ghost-edge cleanup across the entire cluster.
    /// Walks ghost edge structures on *all* shards and removes any that
    /// point to a node that is currently marked deleted (tombstone) on its
    /// owning shard.
    ///
    /// This is more expensive than a single-GID cleanup but guarantees
    /// a clean state. It can be called explicitly or after bulk deletes.
    pub fn cleanup_ghost_edges(&mut self) {
        let n = self.shards.len();
        for i in 0..n {
            let mut ghosts_map = std::mem::take(&mut self.shards[i].ghost_edges);
            ghosts_map.retain(|&source, ghosts| {
                if self.shards[i].graph.is_node_deleted(NodeId(source)) {
                    return false;
                }
                ghosts.retain(|g| {
                    self.shards
                        .get(g.remote_target.shard.0 as usize)
                        .is_some_and(|s| !s.graph.is_node_deleted(g.remote_target.local))
                });
                !ghosts.is_empty()
            });
            self.shards[i].ghost_edges = ghosts_map;
        }
    }

    /// Legacy / targeted version kept for backward compatibility with
    /// the remove_node path. Now delegates to the full aggressive cleanup.
    #[allow(dead_code)]
    fn cleanup_ghost_edges_pointing_to(&mut self, _deleted_gid: GlobalNodeId) {
        // For simplicity and correctness we now always do the full pass.
        // A more sophisticated version could do a targeted retain only for
        // the given GID + a general deleted check.
        self.cleanup_ghost_edges();
    }

    /// Vacuum operation for the router (called by ShardedGraph).
    pub fn vacuum_deleted_nodes(&mut self) {
        self.cleanup_ghost_edges();

        // Give each shard's text index / on-disk engine a chance to compact
        for shard in &mut self.shards {
            shard.graph.text_index.vacuum();
        }
    }

    /// Start background merge + incremental vacuum workers for on-disk shards.
    ///
    /// Returns the number of workers started.
    pub fn start_background_merge_workers(&mut self, interval: std::time::Duration) -> usize {
        let mut total = 0;
        for shard in &mut self.shards {
            if shard.graph.start_background_workers(interval) > 0 {
                total += 1;
            }
        }
        total
    }

    /// Returns a serializable snapshot of all ghost edges (used by persistence).
    pub fn ghost_edges_snapshot(&self) -> Vec<(u16, HashMap<u32, Vec<GhostEdge>>)> {
        self.shards
            .iter()
            .enumerate()
            .map(|(i, s)| (i as u16, s.ghost_edges.clone()))
            .collect()
    }

    /// Restores ghost edges from a previously saved snapshot (used by load).
    pub fn restore_ghost_edges(&mut self, data: Vec<(u16, HashMap<u32, Vec<GhostEdge>>)>) {
        for (sid, map) in data {
            if (sid as usize) < self.shards.len() {
                self.shards[sid as usize].ghost_edges = map;
            }
        }
    }

    /// Run a full maintenance cycle (vacuum + future merge/policy work).
    /// This is what the background maintenance worker will call periodically.
    pub fn run_maintenance_cycle(&mut self) {
        self.vacuum_deleted_nodes();
    }

    /// Returns, for each shard, the list of its on-disk segments (if any).
    /// Memory-backed shards return an empty list.
    /// This data is the foundation for segment-aware rebalancing.
    pub fn segment_distribution(&self) -> Vec<Vec<crate::on_disk_index::SegmentInfo>> {
        self.shards
            .iter()
            .map(|s| s.graph.text_index_segment_info())
            .collect()
    }

    // ======================================================================
    // Persistence for sharded on-disk graphs (0.2)
    // ======================================================================

    /// Save the entire sharded graph to a directory.
    ///
    /// Layout:
    ///   <dir>/
    ///     manifest.json
    ///     ghost_edges.json
    ///     shards/
    ///       0000/
    ///         graph/...
    ///         text_index/...
    ///       0001/...
    /// Save the entire sharded graph to a directory.
    ///
    /// Layout:
    ///   <dir>/
    ///     manifest.json        ← written last (commit record)
    ///     ghost_edges.json
    ///     shards/
    ///       0000/
    ///         graph/...
    ///         text_index/...
    ///       0001/...
    ///
    /// The top-level metadata files (`manifest.json` and `ghost_edges.json`) are
    /// written atomically: each is first staged as a `.new` file, then renamed
    /// into place. `manifest.json` is renamed last so it acts as the commit
    /// record — an interrupted save leaves the old manifest intact.
    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        std::fs::create_dir_all(dir)?;

        // Save per-shard data (each IndexedKnowledgeGraph now also persists
        // its vector index into shard_dir/vector_index/ via manifest.json + vectors.bin)
        let shards_dir = dir.join("shards");
        std::fs::create_dir_all(&shards_dir)?;

        for (i, shard) in self.shards.iter_mut().enumerate() {
            let shard_dir = shards_dir.join(format!("{:04}", i));
            shard.graph.save_to_dir(&shard_dir)?;
        }

        // Stage ghost_edges.json atomically (.new → final)
        let ghost_data: Vec<(u16, HashMap<u32, Vec<GhostEdge>>)> = self
            .shards
            .iter()
            .enumerate()
            .map(|(i, s)| (i as u16, s.ghost_edges.clone()))
            .collect();
        let ghost_json = serde_json::to_string_pretty(&ghost_data)?;
        let ghost_new = dir.join("ghost_edges.json.new");
        std::fs::write(&ghost_new, ghost_json)?;

        // Stage manifest.json atomically (.new → final)
        let manifest = serde_json::json!({
            "shard_count": self.shards.len(),
            "version": 1,
            "routing_version": self.hash_ring.routing_version,
        });
        let manifest_new = dir.join("manifest.json.new");
        std::fs::write(&manifest_new, serde_json::to_string_pretty(&manifest)?)?;

        // Rename into place: ghost_edges first, manifest last (commit record).
        // retry logic handles brief Defender / AV scan windows on Windows.
        crate::serialize::rename_with_retry(&ghost_new, &dir.join("ghost_edges.json"))?;
        crate::serialize::rename_with_retry(&manifest_new, &dir.join("manifest.json"))?;

        Ok(())
    }

    /// Load a sharded graph from a directory created by `save_to_dir`.
    pub fn load_from_dir(dir: &std::path::Path) -> Result<Self, crate::GraphError> {
        // Read manifest
        let manifest_data = std::fs::read(dir.join("manifest.json"))?;
        let manifest: serde_json::Value = serde_json::from_slice(&manifest_data)?;
        // A missing or non-integer shard_count is a corrupt manifest, not a
        // defaultable situation — silently creating a 1-shard router and then
        // trying to load N shard directories would produce confusing errors.
        let shard_count = manifest["shard_count"].as_u64().ok_or_else(|| {
            crate::GraphError::Other(
                "manifest.json is missing or has an invalid 'shard_count' field".to_string(),
            )
        })?;
        let shard_count = u16::try_from(shard_count)
            .ok()
            .filter(|&n| n > 0)
            .ok_or_else(|| crate::GraphError::Sharding("invalid persisted shard count".into()))?;
        let routing_version = manifest
            .get("routing_version")
            .and_then(|v| v.as_u64())
            .unwrap_or(1);
        if !(1..=2).contains(&routing_version) {
            return Err(crate::GraphError::Sharding(
                "unsupported routing version".into(),
            ));
        }
        let mut router = Self::new(shard_count);
        router.hash_ring =
            ConsistentHashRing::with_version(shard_count, 150, routing_version as u32);

        let shards_dir = dir.join("shards");
        for i in 0..shard_count {
            let shard_dir = shards_dir.join(format!("{:04}", i));
            // IndexedKnowledgeGraph::load_from_dir now auto-detects whether the
            // text_index/ subdir contains an on-disk manifest and loads the
            // correct backend (OnDiskTextIndex or InvertedIndex).
            // IndexedKnowledgeGraph::load_from_dir now handles vector index persistence:
            // it will look for vector_index/manifest.json and try a fast load
            // (HNSW or brute-force) before falling back to a full rebuild from embeddings.
            let ig = crate::index::IndexedKnowledgeGraph::load_from_dir(&shard_dir)?;
            router.shards[i as usize].graph = ig;
        }

        // A malformed topology file must fail visibly, not erase every crossing.
        let ghost_path = dir.join("ghost_edges.json");
        if ghost_path.exists() {
            let ghosts: Vec<(u16, HashMap<u32, Vec<GhostEdge>>)> =
                serde_json::from_slice(&std::fs::read(ghost_path)?)?;
            for (sid, map) in ghosts {
                let Some(shard) = router.shards.get_mut(sid as usize) else {
                    return Err(crate::GraphError::Sharding(
                        "ghost file references a missing source shard".into(),
                    ));
                };
                shard.ghost_edges = map;
            }
        }

        Ok(router)
    }

    /// Memory-map version of `load_from_dir`.
    pub fn load_mmap(dir: &std::path::Path) -> Result<Self, crate::GraphError> {
        // Similar to load_from_dir but use mmap for the graph parts where possible.
        Self::load_from_dir(dir) // fallback for now
    }

    /// Add an edge between two nodes (possibly across shards).
    ///
    /// If the nodes live on different shards, a ghost edge is automatically
    /// created. Returns an error if either `GlobalNodeId` is invalid.
    pub fn add_edge(
        &mut self,
        source: GlobalNodeId,
        target: GlobalNodeId,
        strength: f64,
        props: Vec<(crate::ids::PropertyTag, crate::properties::PropertyValue)>,
    ) -> Result<(), crate::GraphError> {
        let src_idx = source.shard.0 as usize;
        let tgt_idx = target.shard.0 as usize;

        if src_idx >= self.shards.len() || tgt_idx >= self.shards.len() {
            return Err(crate::GraphError::Sharding(format!(
                "invalid shard index in edge: source={} target={}",
                source.shard.0, target.shard.0
            )));
        }

        if self.shards[src_idx].graph.is_node_deleted(source.local)
            || self.shards[tgt_idx].graph.is_node_deleted(target.local)
        {
            return Err(crate::GraphError::Sharding(
                "edge endpoint is missing or deleted".into(),
            ));
        }
        let named_props: Vec<_> = props
            .iter()
            .map(|(tag, value)| {
                self.shards[src_idx]
                    .graph
                    .graph
                    .properties
                    .tag_to_name
                    .get(tag.0 as usize)
                    .map(|name| (name.clone(), value.clone()))
                    .ok_or_else(|| {
                        crate::GraphError::Sharding("unregistered source property tag".into())
                    })
            })
            .collect::<Result<_, _>>()?;
        if source.shard == target.shard {
            // Local edge
            let shard = &mut self.shards[src_idx];
            shard
                .graph
                .add_edge(source.local, target.local, strength, props);
        } else {
            // Cross-shard edge → create ghost edge on the source shard
            let shard = &mut self.shards[src_idx];
            let ghost = GhostEdge {
                local_source: source.local,
                remote_target: target,
                strength,
                properties: named_props,
            };
            shard
                .ghost_edges
                .entry(source.local.0)
                .or_default()
                .push(ghost);
        }

        Ok(())
    }

    /// Add an edge using property names resolved in its source shard.
    pub fn add_edge_with_properties(
        &mut self,
        source: GlobalNodeId,
        target: GlobalNodeId,
        strength: f64,
        properties: Vec<(String, crate::properties::PropertyValue)>,
    ) -> Result<(), crate::GraphError> {
        self.shard(source.shard)?;
        self.shard(target.shard)?;
        if self.shards[source.shard.0 as usize]
            .graph
            .is_node_deleted(source.local)
            || self.shards[target.shard.0 as usize]
                .graph
                .is_node_deleted(target.local)
        {
            return Err(crate::GraphError::Sharding(
                "edge endpoint is missing or deleted".into(),
            ));
        }
        let registry = &mut self.shards[source.shard.0 as usize].graph.graph.properties;
        let new_names: std::collections::HashSet<_> = properties
            .iter()
            .map(|(name, _)| name)
            .filter(|name| registry.get_tag(name).is_none())
            .collect();
        if registry.property_names().len() + new_names.len() > 64 {
            return Err(crate::GraphError::Sharding(
                "more than 64 property names in source shard".into(),
            ));
        }
        let props = properties
            .into_iter()
            .map(|(name, value)| (registry.register(&name), value))
            .collect();
        self.add_edge(source, target, strength, props)
    }

    /// Perform a distributed text search across all shards and merge the results.
    pub fn search(&self, query: &str, max_results: usize) -> Vec<crate::index::TextSearchResult> {
        let mut all = Vec::new();
        for shard in &self.shards {
            let mut local = shard.graph.search(query, max_results);
            all.append(&mut local);
        }
        // Global sort + truncate
        all.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        all.truncate(max_results);
        all
    }

    /// High-level distributed "search + traverse" with **real ghost-edge following**.
    ///
    /// When expansion reaches a node that has `GhostEdge`s, the router will
    /// cross into the remote shard, fetch the target node (with caching),
    /// and continue the traversal from there, multiplying the edge strength.
    pub fn search_and_traverse(
        &self,
        text_query: &str,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        weights: crate::index::FusionWeights,
    ) -> Vec<ShardedQueryResult> {
        use std::cmp::Ordering;
        use std::collections::BinaryHeap;
        if max_seeds == 0 || max_results == 0 {
            return vec![];
        }
        let mut seeds = Vec::new();
        for (sid, shard) in self.shards.iter().enumerate() {
            if property_names
                .iter()
                .any(|name| shard.graph.graph.properties.get_tag(name).is_none())
            {
                continue;
            }
            for hit in shard.graph.search(text_query, max_seeds) {
                seeds.push((
                    GlobalNodeId {
                        shard: ShardId(sid as u16),
                        local: hit.node_id,
                    },
                    hit.score,
                ));
            }
        }
        seeds.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        seeds.truncate(max_seeds);
        struct Entry {
            node: GlobalNodeId,
            strength: f64,
            depth: u32,
            text_score: f64,
            parent: Option<usize>,
            via: Option<ShardedPathHop>,
        }
        impl PartialEq for Entry {
            fn eq(&self, other: &Self) -> bool {
                self.cmp(other) == Ordering::Equal
            }
        }
        impl Eq for Entry {}
        impl PartialOrd for Entry {
            fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
                Some(self.cmp(other))
            }
        }
        impl Ord for Entry {
            fn cmp(&self, other: &Self) -> Ordering {
                self.strength
                    .total_cmp(&other.strength)
                    .then_with(|| other.depth.cmp(&self.depth))
                    .then_with(|| self.text_score.total_cmp(&other.text_score))
                    .then_with(|| other.node.cmp(&self.node))
                    .then_with(|| other.parent.cmp(&self.parent))
            }
        }
        let mut heap = BinaryHeap::new();
        for (node, text_score) in seeds {
            heap.push(Entry {
                node,
                strength: 1.0,
                depth: 0,
                text_score,
                parent: None,
                via: None,
            });
        }
        let mut expanded: HashMap<GlobalNodeId, u32> = HashMap::new();
        let mut paths: Vec<(Option<usize>, ShardedPathHop)> = Vec::new();
        let mut rows = Vec::new();
        while let Some(entry) = heap.pop() {
            let Some(shard) = self.shards.get(entry.node.shard.0 as usize) else {
                continue;
            };
            if shard.graph.is_node_deleted(entry.node.local) {
                continue;
            }
            let previous = expanded.get(&entry.node).copied();
            if previous.is_some_and(|depth| depth <= entry.depth) {
                continue;
            }
            expanded.insert(entry.node, entry.depth);
            self.telemetry.record_traversal_nodes_visited(1);
            let tail = entry.via.map(|hop| {
                let id = paths.len();
                paths.push((entry.parent, hop));
                id
            });
            if previous.is_none() {
                rows.push((
                    ShardedQueryResult {
                        node: entry.node,
                        text_score: entry.text_score,
                        vector_score: 0.0,
                        graph_strength: entry.strength,
                        fused_score: entry.text_score * weights.text
                            + entry.strength * weights.graph,
                        depth: entry.depth,
                        path: vec![],
                        text: String::new(),
                    },
                    tail,
                ));
            }
            if entry.depth >= max_depth {
                continue;
            }
            let mut push = |node: GlobalNodeId, strength: f64, hop: ShardedPathHop| {
                if !strength.is_finite()
                    || !(0.0..=1.0).contains(&strength)
                    || strength < min_strength
                {
                    return;
                }
                if self
                    .shards
                    .get(node.shard.0 as usize)
                    .map_or(true, |s| s.graph.is_node_deleted(node.local))
                {
                    return;
                }
                if expanded
                    .get(&node)
                    .is_some_and(|&depth| depth <= entry.depth + 1)
                {
                    return;
                }
                heap.push(Entry {
                    node,
                    strength: entry.strength * strength,
                    depth: entry.depth + 1,
                    text_score: entry.text_score,
                    parent: tail,
                    via: Some(hop),
                });
            };
            let graph = &shard.graph.graph;
            for &eid in &graph.nodes[entry.node.local.0 as usize].outgoing {
                let edge = &graph.edges[eid.0 as usize];
                if property_names.iter().all(|name| {
                    graph
                        .properties
                        .get_tag(name)
                        .is_some_and(|tag| edge.properties.iter().any(|(t, _)| *t == tag))
                }) {
                    push(
                        GlobalNodeId {
                            shard: entry.node.shard,
                            local: edge.target,
                        },
                        edge.strength,
                        ShardedPathHop::Local(eid),
                    );
                }
            }
            if let Some(ghosts) = shard.ghost_edges.get(&entry.node.local.0) {
                for ghost in ghosts {
                    if property_names
                        .iter()
                        .all(|name| ghost.properties.iter().any(|(n, _)| n == name))
                    {
                        self.telemetry.record_remote_fetch(false);
                        push(
                            ghost.remote_target,
                            ghost.strength,
                            ShardedPathHop::CrossShard {
                                from: entry.node,
                                to: ghost.remote_target,
                                strength: ghost.strength,
                            },
                        );
                    }
                }
            }
        }
        // Fused order can differ from path order. Limit only after ranking;
        // materialize paths and text only for returned rows.
        rows.sort_by(|a, b| {
            b.0.fused_score
                .total_cmp(&a.0.fused_score)
                .then_with(|| a.0.node.cmp(&b.0.node))
        });
        rows.truncate(max_results);
        rows.into_iter()
            .map(|(mut row, mut cursor)| {
                while let Some(id) = cursor {
                    row.path.push(paths[id].1.clone());
                    cursor = paths[id].0;
                }
                row.path.reverse();
                row.text = self.shards[row.node.shard.0 as usize]
                    .graph
                    .read_blob(row.node.local)
                    .to_string();
                row
            })
            .collect()
    }

    /// Hybrid "text + vector + graph" search across the sharded cluster.
    ///
    /// For the initial implementation we run `search_and_traverse_hybrid` independently
    /// on each shard (using the same query vector) and then merge the top results by
    /// fused score. This is simple and gives good results for most workloads.
    ///
    /// RRF ranks and PageRank centrality are computed in each shard's OWN local space,
    /// then merged by raw fused score across shards — so centrality is per-shard, not global.
    /// A fully cross-shard hybrid is a future enhancement.
    ///
    /// A fully cross-shard best-first hybrid traversal is possible in a follow-up if needed.
    pub fn search_and_traverse_hybrid(
        &self,
        text_query: &str,
        vector: Option<&[f32]>,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        weights: crate::index::HybridWeights,
    ) -> Vec<ShardedQueryResult> {
        let mut all_results: Vec<ShardedQueryResult> = Vec::new();

        for (shard_idx, shard) in self.shards.iter().enumerate() {
            let local_results = shard.graph.search_and_traverse_hybrid(
                text_query,
                vector,
                max_seeds,
                property_names,
                min_strength,
                max_depth,
                max_results,
                weights.clone(),
            );

            for cr in local_results {
                // Convert local CombinedResult into ShardedQueryResult
                let gnode = GlobalNodeId {
                    shard: ShardId(shard_idx as u16),
                    local: cr.graph_result.node_id,
                };

                // Build a minimal path (single shard for now)
                // For the current per-shard hybrid implementation we return an empty path.
                // A richer path reconstruction can be added later.
                let path: Vec<ShardedPathHop> = Vec::new();

                all_results.push(ShardedQueryResult {
                    node: gnode,
                    text_score: cr.text_score,
                    vector_score: cr.vector_score,
                    graph_strength: cr.graph_strength,
                    fused_score: cr.fused_score,
                    depth: cr.graph_result.depth,
                    text: cr.text.to_string(),
                    path,
                });
            }
        }

        // Global re-rank across shards
        all_results.sort_by(|a, b| {
            b.fused_score
                .partial_cmp(&a.fused_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        all_results.truncate(max_results);
        all_results
    }

    /// Standalone vector similarity search across all shards.
    ///
    /// Performs `vector_search` on every shard and merges the top results.
    /// This is a simple but effective implementation for Phase 2.
    pub fn vector_search(&self, query: &[f32], top_k: usize) -> Vec<(GlobalNodeId, f64)> {
        let mut all_results: Vec<(GlobalNodeId, f64)> = Vec::new();

        for (shard_idx, shard) in self.shards.iter().enumerate() {
            let local_results = shard.graph.vector_search(query, top_k);

            for (local_id, score) in local_results {
                let gid = GlobalNodeId {
                    shard: ShardId(shard_idx as u16),
                    local: local_id,
                };
                all_results.push((gid, score));
            }
        }

        // Global sort by score descending and truncate
        all_results.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        all_results.truncate(top_k);
        all_results
    }

    /// Compute which nodes would need to move if we changed the number of shards.
    pub fn plan_rebalance(&self, new_shard_count: u16) -> RebalancePlan {
        let new_ring = ConsistentHashRing::new(new_shard_count, 150);
        let mut migrations = Vec::new();

        for (old_idx, shard) in self.shards.iter().enumerate() {
            for i in 0..shard.graph.graph.node_count() {
                let local_id = NodeId(i as u32);

                if shard.graph.text_index.is_document_deleted(local_id) {
                    continue;
                }

                let text = shard.graph.read_blob(local_id);
                let new_shard = new_ring.locate(text.as_bytes());

                if new_shard.0 != old_idx as u16 {
                    migrations.push((
                        GlobalNodeId {
                            shard: ShardId(old_idx as u16),
                            local: local_id,
                        },
                        new_shard,
                    ));
                }
            }
        }

        // === Segment-aware recommendations ===
        let mut segment_migrations = Vec::new();

        for (old_idx, shard) in self.shards.iter().enumerate() {
            let seg_infos = shard.graph.text_index_segment_info();

            for (seg_idx, info) in seg_infos.iter().enumerate() {
                let sample = shard
                    .graph
                    .text_index
                    .sample_representative_for_segment(seg_idx);
                let seg_key = match &sample {
                    Some(s) if !s.is_empty() => format!("content-{}-seg-{}", s, seg_idx),
                    _ => format!(
                        "shard-{}-seg-{}-tier-{}-docs-{}",
                        old_idx, seg_idx, info.tier, info.doc_count
                    ),
                };
                let target = new_ring.locate(seg_key.as_bytes());

                if target.0 != old_idx as u16 {
                    segment_migrations.push(SegmentMigration {
                        source_shard: ShardId(old_idx as u16),
                        segment_index: seg_idx,
                        target_shard: target,
                        doc_count: info.doc_count,
                    });
                }
            }
        }

        RebalancePlan {
            old_shard_count: self.shards.len() as u16,
            new_shard_count,
            node_migrations: migrations,
            segment_migrations,
        }
    }

    /// Apply a rebalance plan. Use `execute_rebalance_with_mapping` when retaining
    /// external node handles: rebuilding can change local ids even within a shard.
    pub fn execute_rebalance(&mut self, plan: RebalancePlan) -> Result<(), crate::GraphError> {
        self.execute_rebalance_with_mapping(plan).map(|_| ())
    }

    /// Rebuild the requested placement off to the side, preserving live node
    /// content, labels, embeddings, and all valid edges with named properties.
    /// Swap only after the complete replacement is constructed. Returns old-to-new
    /// handles; old handles must not be used after a successful rebalance.
    /// Segment recommendations are advisory: moving postings alone cannot preserve
    /// the node/document mapping, so this correctness path rebuilds the indexes.
    pub fn execute_rebalance_with_mapping(
        &mut self,
        plan: RebalancePlan,
    ) -> Result<HashMap<GlobalNodeId, GlobalNodeId>, crate::GraphError> {
        let error = |message: &str| crate::GraphError::Sharding(message.into());
        if plan.new_shard_count == 0 || plan.old_shard_count as usize != self.shards.len() {
            return Err(error("invalid or stale rebalance shard count"));
        }
        let mut destinations = HashMap::new();
        for &(source, target) in &plan.node_migrations {
            if target.0 >= plan.new_shard_count
                || self
                    .shards
                    .get(source.shard.0 as usize)
                    .map_or(true, |s| s.graph.is_node_deleted(source.local))
            {
                return Err(error(
                    "rebalance references a missing node or destination shard",
                ));
            }
            if destinations.insert(source, target).is_some() {
                return Err(error("duplicate rebalance source"));
            }
        }
        let expected: HashMap<_, _> = self
            .plan_rebalance(plan.new_shard_count)
            .node_migrations
            .into_iter()
            .collect();
        if destinations != expected {
            return Err(error(
                "rebalance placement is stale or inconsistent with the target hash ring",
            ));
        }
        let mut replacement = Self::new(plan.new_shard_count);
        replacement.telemetry = self.telemetry.clone();
        let mut mapping = HashMap::new();
        for (sid, shard) in self.shards.iter().enumerate() {
            for local in 0..shard.graph.graph.node_count() {
                let old = GlobalNodeId {
                    shard: ShardId(sid as u16),
                    local: NodeId(local as u32),
                };
                if shard.graph.is_node_deleted(old.local) {
                    continue;
                }
                let target = destinations.get(&old).copied().unwrap_or(old.shard);
                if target.0 >= plan.new_shard_count {
                    return Err(error("missing migration from a removed shard"));
                }
                let embedding = shard.graph.read_embedding(old.local);
                let node = replacement.shards[target.0 as usize].graph.add_node(
                    shard.graph.read_blob(old.local),
                    shard.graph.graph.node_label(old.local),
                    embedding.as_deref(),
                );
                mapping.insert(
                    old,
                    GlobalNodeId {
                        shard: target,
                        local: node,
                    },
                );
            }
        }
        for (sid, shard) in self.shards.iter().enumerate() {
            for edge in shard.graph.all_edges() {
                let source = GlobalNodeId {
                    shard: ShardId(sid as u16),
                    local: edge.source,
                };
                let target = GlobalNodeId {
                    shard: ShardId(sid as u16),
                    local: edge.target,
                };
                if let (Some(&source), Some(&target)) = (mapping.get(&source), mapping.get(&target))
                {
                    let props = edge
                        .properties
                        .iter()
                        .map(|(tag, value)| {
                            (
                                shard.graph.graph.properties.get_name(*tag).to_owned(),
                                value.clone(),
                            )
                        })
                        .collect();
                    replacement.add_edge_with_properties(source, target, edge.strength, props)?;
                }
            }
            for ghosts in shard.ghost_edges.values() {
                for ghost in ghosts {
                    let source = GlobalNodeId {
                        shard: ShardId(sid as u16),
                        local: ghost.local_source,
                    };
                    if let (Some(&source), Some(&target)) =
                        (mapping.get(&source), mapping.get(&ghost.remote_target))
                    {
                        replacement.add_edge_with_properties(
                            source,
                            target,
                            ghost.strength,
                            ghost.properties.clone(),
                        )?;
                    }
                }
            }
        }
        replacement
            .telemetry
            .record_rebalance(destinations.len() as u64);
        *self = replacement;
        Ok(mapping)
    }
}

/// High-level facade over a sharded graph.
///
/// This is the recommended type for users who want sharding without dealing
/// with `ShardRouter` and `GlobalNodeId` directly.
pub struct ShardedGraph {
    router: ShardRouter,
}

impl std::fmt::Debug for ShardedGraph {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ShardedGraph")
            .field("router", &self.router)
            .finish()
    }
}

impl ShardedGraph {
    /// Read a live node's stored embedding independently of approximate search.
    pub fn read_embedding(&self, node: GlobalNodeId) -> Option<Vec<f32>> {
        let shard = self.router.shards.get(node.shard.0 as usize)?;
        if shard.graph.is_node_deleted(node.local) {
            return None;
        }
        shard.graph.read_embedding(node.local)
    }

    /// Create a new sharded graph with the given number of shards.
    pub fn new(shard_count: u16) -> Self {
        Self {
            router: ShardRouter::new(shard_count),
        }
    }

    /// Replace the telemetry implementation used for observability.
    pub fn set_telemetry(&mut self, telemetry: impl crate::Telemetry + 'static) {
        self.router.set_telemetry(telemetry);
    }

    /// Adds a new node with the given text and optional label, routing it to the appropriate shard.
    pub fn add_node(
        &mut self,
        text: &str,
        label: Option<&str>,
    ) -> Result<GlobalNodeId, crate::GraphError> {
        self.router.add_node(text, label)
    }

    /// Adds a new node (with optional embedding) with the given text and label,
    /// routing it to the appropriate shard using consistent hashing.
    pub fn add_node_with_embedding(
        &mut self,
        text: &str,
        label: Option<&str>,
        embedding: Option<&[f32]>,
    ) -> Result<GlobalNodeId, crate::GraphError> {
        self.router.add_node_with_embedding(text, label, embedding)
    }

    /// Returns the SHA-256 content hash of a node's text (global id).
    pub fn node_content_hash(&self, gid: GlobalNodeId) -> Option<[u8; 32]> {
        self.router.node_content_hash(gid)
    }

    /// Verify that the stored content hash for a node matches its current blob.
    pub fn verify_node(&self, gid: GlobalNodeId) -> crate::graph::NodeIntegrity {
        self.router.verify_node(gid)
    }

    /// Adds a directed edge between two globally-addressed nodes with the given strength and properties.
    pub fn add_edge(
        &mut self,
        source: GlobalNodeId,
        target: GlobalNodeId,
        strength: f64,
        props: Vec<(crate::ids::PropertyTag, crate::properties::PropertyValue)>,
    ) -> Result<(), crate::GraphError> {
        self.router.add_edge(source, target, strength, props)
    }

    /// Removes a node (and its incident edges) across the sharded cluster.
    ///
    /// This marks a tombstone in the owning shard's text index and triggers
    /// (optional) ghost-edge cleanup across other shards.
    pub fn remove_node(&mut self, gid: GlobalNodeId) -> Result<bool, crate::GraphError> {
        self.router.remove_node(gid)
    }

    /// Performs a full-text search across all shards and returns ranked results.
    pub fn search(&self, query: &str, max_results: usize) -> Vec<crate::index::TextSearchResult> {
        self.router.search(query, max_results)
    }

    /// Full text-search + graph traversal: finds seed nodes matching `text_query`, then
    /// traverses outgoing edges up to `max_depth` hops, filtering by `property_names`
    /// and `min_strength`, and returns the top `max_results` combined results.
    pub fn search_and_traverse(
        &self,
        text_query: &str,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> Vec<ShardedQueryResult> {
        self.router.search_and_traverse(
            text_query,
            max_seeds,
            property_names,
            min_strength,
            max_depth,
            max_results,
            crate::index::FusionWeights::default(),
        )
    }

    /// Hybrid text + vector + graph search over the sharded cluster (see [`ShardRouter::search_and_traverse_hybrid`]).
    pub fn search_and_traverse_hybrid(
        &self,
        text_query: &str,
        vector: Option<&[f32]>,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        weights: crate::index::HybridWeights,
    ) -> Vec<ShardedQueryResult> {
        self.router.search_and_traverse_hybrid(
            text_query,
            vector,
            max_seeds,
            property_names,
            min_strength,
            max_depth,
            max_results,
            weights,
        )
    }

    /// Standalone approximate vector similarity search across the entire sharded cluster.
    ///
    /// Returns up to `top_k` global nodes with their cosine similarity scores.
    pub fn vector_search(&self, query: &[f32], top_k: usize) -> Vec<(GlobalNodeId, f64)> {
        self.router.vector_search(query, top_k)
    }

    /// Access the underlying router if you need lower-level control.
    pub fn router(&self) -> &ShardRouter {
        &self.router
    }

    /// Returns a mutable reference to the underlying `ShardRouter`.
    ///
    /// This is an escape hatch for advanced use cases (e.g. registering
    /// properties on all shards before adding nodes, or direct access to
    /// per-shard `IndexedKnowledgeGraph` instances).
    pub fn router_mut(&mut self) -> &mut ShardRouter {
        &mut self.router
    }

    // Re-export some router functionality for convenience

    /// Compute a rebalance plan for changing the number of shards.
    ///
    /// See [`ShardRouter::plan_rebalance`] for details.
    pub fn plan_rebalance(&self, new_shard_count: u16) -> RebalancePlan {
        self.router.plan_rebalance(new_shard_count)
    }

    /// Execute a previously computed rebalance plan.
    ///
    /// Returns an error if the plan references invalid shards or if
    /// migration encounters structural problems.
    pub fn execute_rebalance(&mut self, plan: RebalancePlan) -> Result<(), crate::GraphError> {
        self.router.execute_rebalance(plan)
    }

    /// Aggressively cleans all ghost edges that point to deleted nodes.
    pub fn cleanup_ghost_edges(&mut self) {
        self.router.cleanup_ghost_edges();
    }

    /// Vacuum operation: cleans ghost edges and gives on-disk indexes
    /// a chance to reclaim space from deleted nodes.
    pub fn vacuum_deleted_nodes(&mut self) {
        self.router.vacuum_deleted_nodes();
    }

    /// Returns a serializable snapshot of ghost edges (for persistence / testing).
    pub fn ghost_edges_snapshot(&self) -> Vec<(u16, HashMap<u32, Vec<GhostEdge>>)> {
        self.router.ghost_edges_snapshot()
    }

    /// Restores ghost edges from a snapshot (used during load).
    pub fn restore_ghost_edges(&mut self, data: Vec<(u16, HashMap<u32, Vec<GhostEdge>>)>) {
        self.router.restore_ghost_edges(data);
    }

    /// Run a full maintenance cycle (ghost cleanup + incremental vacuum on on-disk shards).
    pub fn run_maintenance_cycle(&mut self) {
        self.router.run_maintenance_cycle();
    }

    /// Returns per-shard segment distribution (empty for memory-backed shards).
    /// Primary input for future segment-aware rebalancing.
    pub fn segment_distribution(&self) -> Vec<Vec<crate::on_disk_index::SegmentInfo>> {
        self.router.segment_distribution()
    }

    // === Per-shard on-disk persistence (0.2) ===

    /// Save the whole sharded graph, including each shard's on-disk text index
    /// and vector index (if present).
    ///
    /// Each shard will write its vector index under `shards/XXXX/vector_index/`
    /// (manifest.json + vectors.bin) for fast reload.
    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        self.router.save_to_dir(dir)
    }

    /// Load a previously saved sharded graph.
    ///
    /// Vector indexes (if persisted) are loaded per-shard via the fast path
    /// (manifest.json + vectors.bin) when available.
    pub fn load_from_dir(dir: &std::path::Path) -> Result<Self, crate::GraphError> {
        let router = ShardRouter::load_from_dir(dir)?;
        Ok(Self { router })
    }

    /// Load using memory mapping where possible (for the graph part of each shard).
    pub fn load_mmap(dir: &std::path::Path) -> Result<Self, crate::GraphError> {
        let router = ShardRouter::load_mmap(dir)?;
        Ok(Self { router })
    }

    /// Start a background maintenance thread that periodically runs
    /// `vacuum_deleted_nodes()` (ghost cleanup + per-shard compaction)
    /// and any other cheap maintenance tasks.
    ///
    /// For a fully robust implementation in a real deployment, pass an
    /// `Arc<Mutex<ShardedGraph>>` to the worker. The current implementation
    /// demonstrates the pattern and is sufficient for the 0.2 spike.
    /// Start a background maintenance worker.
    ///
    /// **Deprecated:** This variant holds a `&mut self` reference, so the background
    /// thread cannot actually mutate the graph. It only sleeps in a loop and performs
    /// no real work (vacuum, compaction, ghost-edge cleanup).
    ///
    /// Use [`start_background_maintenance_with_shared`] with an `Arc<Mutex<ShardedGraph>>`
    /// for a worker that actually runs maintenance.
    #[deprecated(
        note = "This variant performs no maintenance work. Use start_background_maintenance_with_shared instead."
    )]
    pub fn start_background_maintenance(&mut self, interval: Duration) -> MaintenanceHandle {
        MaintenanceHandle::start(interval, &mut self.router)
    }

    /// Start background maintenance when the caller holds the `ShardedGraph` in an
    /// `Arc<Mutex<...>>`. The background thread will periodically lock and call
    /// `run_maintenance_cycle()` (which runs ghost cleanup + real incremental vacuum).
    pub fn start_background_maintenance_with_shared(
        &self,
        graph: Arc<Mutex<Self>>,
        interval: Duration,
    ) -> MaintenanceHandle {
        MaintenanceHandle::start_shared(interval, graph)
    }

    /// Explicitly trigger a compaction / vacuum pass on all shards that use
    /// the on-disk backend.
    pub fn compact(&mut self) {
        self.router.vacuum_deleted_nodes();
    }

    // === Convenience methods that make the facade pleasant to use ===

    /// Read the text of a node given its global identifier.
    ///
    /// Returns an error if the `GlobalNodeId` refers to a non-existent shard.
    pub fn read_blob(&self, gid: GlobalNodeId) -> Result<&str, crate::GraphError> {
        let shard = self.router.shard(gid.shard)?;
        Ok(shard.graph.read_blob(gid.local))
    }

    /// Get a node label by global id (if present).
    ///
    /// Returns an error if the `GlobalNodeId` refers to a non-existent shard.
    pub fn node_label(&self, gid: GlobalNodeId) -> Result<Option<&str>, crate::GraphError> {
        let shard = self.router.shard(gid.shard)?;
        Ok(shard.graph.graph.node_label(gid.local))
    }

    /// Distributed phrase search across all shards (text only).
    ///
    /// Results are merged and globally ranked by BM25-style score.
    pub fn phrase_search(
        &self,
        phrase: &str,
        max_gap: u32,
        max_results: usize,
    ) -> Vec<crate::index::TextSearchResult> {
        let mut all = Vec::new();
        for shard in &self.router.shards {
            let mut local = shard.graph.phrase_search(phrase, max_gap, max_results);
            all.append(&mut local);
        }
        all.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        all.truncate(max_results);
        all
    }

    /// Distributed phrase search followed by graph traversal.
    ///
    /// Currently reuses the normal `search_and_traverse` path after
    /// performing phrase search on each shard. A dedicated phrase-aware
    /// traversal may be added later.
    pub fn phrase_search_and_traverse(
        &self,
        phrase: &str,
        _max_gap: u32,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> Vec<ShardedQueryResult> {
        // For now we reuse the text search + traverse path.
        // A dedicated phrase-aware version can be added later.
        self.router.search_and_traverse(
            phrase,
            max_seeds,
            property_names,
            min_strength,
            max_depth,
            max_results,
            crate::index::FusionWeights::default(),
        )
    }

    // Background maintenance is implemented by the MaintenanceHandle type
    // defined at the end of the module (after the impl block).

    /// Returns the number of shards in this cluster.
    pub fn shard_count(&self) -> usize {
        self.router.shards.len()
    }

    /// Returns the total number of nodes across all shards.
    pub fn global_node_count(&self) -> usize {
        self.router
            .shards
            .iter()
            .map(|s| s.graph.graph.node_count())
            .sum()
    }

    /// Returns the total number of ghost edges across the entire cluster.
    pub fn ghost_edge_count(&self) -> usize {
        self.router
            .shards
            .iter()
            .map(|s| s.ghost_edges.values().map(|v| v.len()).sum::<usize>())
            .sum()
    }

    /// Returns a vector with the node count of each shard (index = shard id).
    pub fn nodes_per_shard(&self) -> Vec<usize> {
        self.router
            .shards
            .iter()
            .map(|s| s.graph.graph.node_count())
            .collect()
    }

    /// Returns a vector with the ghost edge count of each shard.
    pub fn ghost_edges_per_shard(&self) -> Vec<usize> {
        self.router
            .shards
            .iter()
            .map(|s| s.ghost_edges.values().map(|v| v.len()).sum::<usize>())
            .collect()
    }

    /// Returns a convenient snapshot of the sharded graph's state.
    ///
    /// This is the recommended way to get an overview for monitoring,
    /// dashboards, or logging.
    pub fn stats(&self) -> ShardedGraphStats {
        let remote_cache_per_shard: Vec<usize> = self
            .router
            .shards
            .iter()
            .map(|s| s.remote_cache_size())
            .collect();

        let remote_cache_entries: usize = remote_cache_per_shard.iter().sum();

        ShardedGraphStats {
            shard_count: self.shard_count(),
            total_nodes: self.global_node_count(),
            total_ghost_edges: self.ghost_edge_count(),
            nodes_per_shard: self.nodes_per_shard(),
            ghost_edges_per_shard: self.ghost_edges_per_shard(),
            remote_cache_entries,
            remote_cache_per_shard,
        }
    }
}

// === Background Maintenance (defined at module level so it can be named
// from inside impl ShardedGraph) ===

/// Handle returned by `ShardedGraph::start_background_maintenance`.
/// Drop it or call `shutdown()` to stop the periodic vacuum + compaction worker.
pub struct MaintenanceHandle {
    stop: Arc<AtomicBool>,
    join: Option<thread::JoinHandle<()>>,
}

impl std::fmt::Debug for MaintenanceHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MaintenanceHandle")
            .field(
                "running",
                &!self.stop.load(std::sync::atomic::Ordering::Relaxed),
            )
            .finish_non_exhaustive()
    }
}

impl MaintenanceHandle {
    fn start(interval: Duration, _router: &mut ShardRouter) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let stop2 = stop.clone();

        // Legacy / simple path: the thread is mostly a lifetime holder.
        // Users should call run_maintenance_cycle() themselves on a timer.
        let handle = thread::spawn(move || {
            while !stop2.load(std::sync::atomic::Ordering::SeqCst) {
                thread::sleep(interval);
            }
        });

        MaintenanceHandle {
            stop,
            join: Some(handle),
        }
    }

    fn start_shared(interval: Duration, graph: Arc<Mutex<ShardedGraph>>) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let stop2 = stop.clone();

        let handle = thread::spawn(move || {
            while !stop2.load(std::sync::atomic::Ordering::SeqCst) {
                thread::sleep(interval);

                if let Ok(mut g) = graph.lock() {
                    g.run_maintenance_cycle(); // ghost cleanup + real incremental vacuum()
                }
            }
        });

        MaintenanceHandle {
            stop,
            join: Some(handle),
        }
    }

    /// Signals the background worker to stop and waits for the thread to exit.
    pub fn shutdown(mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::SeqCst);
        if let Some(h) = self.join.take() {
            let _ = h.join();
        }
    }
}

impl Drop for MaintenanceHandle {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::SeqCst);
        if let Some(h) = self.join.take() {
            let _ = h.join();
        }
    }
}
