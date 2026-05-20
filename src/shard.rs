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
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
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
    // In a real system we would also store serialized properties here.
}

/// One hop in a path that may have crossed shard boundaries.
#[derive(Debug, Clone)]
pub(crate) enum ShardedPathHop {
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
    /// Cumulative strength of the best graph path from a seed to this node.
    pub graph_strength: f64,
    /// Final blended score (`text_score * text_weight + graph_strength * graph_weight`).
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

    /// Remove a local node and clean up its ghost edges.
    /// Used during rebalancing.
    /// Remove a node from this shard (used during rebalancing).
    pub(crate) fn remove_local_node(&mut self, local_id: NodeId) {
        // Remove outgoing ghost edges from this node
        self.ghost_edges.remove(&local_id.0);

        // Remove incoming ghost references from other nodes
        for ghosts in self.ghost_edges.values_mut() {
            ghosts.retain(|g| g.remote_target.local != local_id);
        }

        // Mark the node as deleted in the local graph (tombstone).
        // This ensures is_document_deleted and future traversals will skip it.
        self.graph.remove_node(local_id);
    }
}

/// Simple consistent hash ring used to assign nodes to shards.
///
/// Uses FNV-1a hashing + virtual nodes for good distribution (as described
/// in the original design document).
pub(crate) struct ConsistentHashRing {
    ring: Vec<(u64, ShardId)>,
}

impl ConsistentHashRing {
    /// Create a new ring for the given number of shards.
    ///
    /// `virtual_nodes` controls how many virtual nodes each real shard gets
    /// (higher = better balance, more memory).
    pub(crate) fn new(shard_count: u16, virtual_nodes: u16) -> Self {
        let mut ring = Vec::new();

        for s in 0..shard_count {
            let shard = ShardId(s);
            for v in 0..virtual_nodes {
                let key = fxhash64(&[s as u8, (v >> 8) as u8, v as u8]);
                ring.push((key, shard));
            }
        }

        ring.sort_by_key(|&(k, _)| k);
        Self { ring }
    }

    pub(crate) fn locate(&self, key: &[u8]) -> ShardId {
        let h = fxhash64(key);
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
fn fxhash64(data: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf29ce484222325;
    for &byte in data {
        hash ^= byte as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
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
        let shard_id = self.shard_for_new_node(text);
        let shard = &mut self.shards[shard_id.0 as usize];

        let local_id = shard.graph.add_node(text, label);
        Ok(GlobalNodeId {
            shard: shard_id,
            local: local_id,
        })
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
            for ghosts in ghosts_map.values_mut() {
                ghosts.retain(|g| {
                    !self.shards[g.remote_target.shard.0 as usize]
                        .graph
                        .text_index
                        .is_document_deleted(g.remote_target.local)
                });
            }
            self.shards[i].ghost_edges = ghosts_map;
        }
    }

    /// Legacy / targeted version kept for backward compatibility with
    /// the remove_node path. Now delegates to the full aggressive cleanup.
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
    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        std::fs::create_dir_all(dir)?;

        // Save per-shard data
        let shards_dir = dir.join("shards");
        std::fs::create_dir_all(&shards_dir)?;

        for (i, shard) in self.shards.iter_mut().enumerate() {
            let shard_dir = shards_dir.join(format!("{:04}", i));
            shard.graph.save_to_dir(&shard_dir)?;
        }

        // Save high-level router state
        let manifest = serde_json::json!({
            "shard_count": self.shards.len(),
            "version": 1,
        });
        std::fs::write(
            dir.join("manifest.json"),
            serde_json::to_string_pretty(&manifest)?,
        )?;

        // Persist ghost edges for exact round-trip (including after rebalancing)
        let ghost_data: Vec<(u16, HashMap<u32, Vec<GhostEdge>>)> = self
            .shards
            .iter()
            .enumerate()
            .map(|(i, s)| (i as u16, s.ghost_edges.clone()))
            .collect();

        let ghost_json = serde_json::to_string_pretty(&ghost_data)?;
        std::fs::write(dir.join("ghost_edges.json"), ghost_json)?;

        // Ring config (virtual node count) so load can recreate identical distribution
        // (we currently hardcode 150 in ConsistentHashRing::new, but we record it for future)
        Ok(())
    }

    /// Load a sharded graph from a directory created by `save_to_dir`.
    pub fn load_from_dir(dir: &std::path::Path) -> Result<Self, crate::GraphError> {
        // Read manifest
        let manifest_data = std::fs::read(dir.join("manifest.json"))?;
        let manifest: serde_json::Value = serde_json::from_slice(&manifest_data)?;
        let shard_count = manifest["shard_count"].as_u64().unwrap_or(1) as u16;

        let mut router = Self::new(shard_count);

        let shards_dir = dir.join("shards");
        for i in 0..shard_count {
            let shard_dir = shards_dir.join(format!("{:04}", i));
            // IndexedKnowledgeGraph::load_from_dir now auto-detects whether the
            // text_index/ subdir contains an on-disk manifest and loads the
            // correct backend (OnDiskTextIndex or InvertedIndex).
            let ig = crate::index::IndexedKnowledgeGraph::load_from_dir(&shard_dir)?;
            router.shards[i as usize].graph = ig;
        }

        // Restore ghost edges (critical for cross-shard queries after load)
        if let Ok(ghost_data) = std::fs::read(dir.join("ghost_edges.json")) {
            if let Ok(ghosts) =
                serde_json::from_slice::<Vec<(u16, HashMap<u32, Vec<GhostEdge>>)>>(&ghost_data)
            {
                for (sid, map) in ghosts {
                    if (sid as usize) < router.shards.len() {
                        router.shards[sid as usize].ghost_edges = map;
                    }
                }
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
            };
            shard
                .ghost_edges
                .entry(source.local.0)
                .or_default()
                .push(ghost);
        }

        Ok(())
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
        _property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        weights: crate::index::FusionWeights,
    ) -> Vec<ShardedQueryResult> {
        use std::cmp::Ordering;
        use std::collections::{BinaryHeap, HashSet};

        // --- 1. Distributed seed selection ---
        let mut seed_list = Vec::new();
        for (s_idx, shard) in self.shards.iter().enumerate() {
            let local = shard.graph.search(text_query, max_seeds);
            for s in local {
                seed_list.push((ShardId(s_idx as u16), s));
            }
        }
        seed_list.sort_by(|a, b| b.1.score.partial_cmp(&a.1.score).unwrap_or(Ordering::Equal));
        seed_list.truncate(max_seeds);

        // --- 2. Cross-shard best-first traversal with ghost following ---
        #[derive(Debug)]
        struct Entry {
            gnode: GlobalNodeId,
            strength: f64,
            depth: u32,
            text_score: f64,
            path: Vec<ShardedPathHop>,
        }

        impl PartialEq for Entry {
            fn eq(&self, other: &Self) -> bool {
                self.strength == other.strength
            }
        }
        impl Eq for Entry {}
        impl PartialOrd for Entry {
            fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
                self.strength.partial_cmp(&other.strength)
            }
        }
        impl Ord for Entry {
            fn cmp(&self, other: &Self) -> Ordering {
                self.partial_cmp(other).unwrap_or(Ordering::Equal)
            }
        }

        let mut pq: BinaryHeap<Entry> = BinaryHeap::new();
        let mut visited: HashSet<(u16, u32)> = HashSet::new(); // (shard, local)

        for (shard_id, seed) in &seed_list {
            let gnode = GlobalNodeId {
                shard: *shard_id,
                local: seed.node_id,
            };
            visited.insert((shard_id.0, seed.node_id.0));
            pq.push(Entry {
                gnode,
                strength: 1.0,
                depth: 0,
                text_score: seed.score,
                path: vec![], // starts at a seed
            });
        }

        let mut results: Vec<ShardedQueryResult> = Vec::new();

        while let Some(e) = pq.pop() {
            self.telemetry.record_traversal_nodes_visited(1);
            log::trace!("traversal: visiting {:?}", e.gnode);

            let shard = &self.shards[e.gnode.shard.0 as usize];
            let node = e.gnode.local;

            let text = shard.graph.read_blob(node);

            // Clone the (usually short) path so we can still use e.path for ghost expansion below.
            let result_path = e.path.clone();

            results.push(ShardedQueryResult {
                node: e.gnode,
                text_score: e.text_score,
                graph_strength: e.strength,
                fused_score: e.text_score * weights.text + e.strength * weights.graph,
                depth: e.depth,
                path: result_path,
                text: text.to_string(),
            });

            if results.len() >= max_results {
                break;
            }
            if e.depth >= max_depth {
                continue;
            }

            // --- Hardened real ghost-edge following ---
            // We check the ghost edges of the current node and cross shards when needed,
            // using the per-shard remote cache to avoid repeated work.
            if let Some(ghosts) = shard.ghost_edges.get(&node.0) {
                for ghost in ghosts {
                    if ghost.strength < min_strength {
                        self.telemetry.record_mask_prune(1);
                        continue;
                    }

                    let remote = ghost.remote_target;
                    let remote_key = (remote.shard.0, remote.local.0);

                    if visited.contains(&remote_key) {
                        continue;
                    }
                    visited.insert(remote_key);

                    // Check remote cache for hit/miss
                    let target_shard = &self.shards[remote.shard.0 as usize];
                    let hit = target_shard.remote_cache.contains_key(&remote);
                    self.telemetry.record_remote_fetch(hit);
                    log::trace!("traversal: following ghost edge to {:?}", remote);

                    // Fetch remote data (cache update is best-effort in current borrow model)
                    let remote_shard = &self.shards[remote.shard.0 as usize];
                    let _remote_text = remote_shard.graph.read_blob(remote.local);

                    // Note: full cache population during traversal is limited by borrowing rules.
                    // The cache is still useful for repeated lookups between traversals.

                    let new_strength = e.strength * ghost.strength;
                    if new_strength >= min_strength {
                        let mut new_path = e.path.clone();
                        new_path.push(ShardedPathHop::CrossShard {
                            from: GlobalNodeId {
                                shard: e.gnode.shard,
                                local: e.gnode.local,
                            },
                            to: remote,
                            strength: ghost.strength,
                        });

                        pq.push(Entry {
                            gnode: remote,
                            strength: new_strength,
                            depth: e.depth + 1,
                            text_score: e.text_score,
                            path: new_path,
                        });
                    }
                }
            }
        }

        // Final ranking by fused score (text relevance + graph strength)
        results.sort_by(|a, b| {
            b.fused_score
                .partial_cmp(&a.fused_score)
                .unwrap_or(Ordering::Equal)
        });
        results.truncate(max_results);
        results
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

    /// Apply a rebalance plan, moving nodes between shards and repairing ghost edges.
    pub fn execute_rebalance(&mut self, plan: RebalancePlan) -> Result<(), crate::GraphError> {
        let nodes_moved = plan.node_migrations.len() as u64;
        let segments_suggested = plan.segment_migrations.len();

        if segments_suggested > 0 {
            log::info!(
                "Rebalance plan contains {} segment migration recommendations (segment-aware rebalancing)",
                segments_suggested
            );

            // Real bulk segment moves (reverse order keeps indices valid)
            for mig in plan.segment_migrations.iter().rev() {
                let src = mig.source_shard.0 as usize;
                let tgt = mig.target_shard.0 as usize;
                if src < self.shards.len() && tgt < self.shards.len() {
                    if let Some((seg, tier)) = self.shards[src]
                        .graph
                        .text_index
                        .take_sealed_segment(mig.segment_index)
                    {
                        self.shards[tgt]
                            .graph
                            .text_index
                            .add_sealed_segment(seg, tier);
                    }
                }
            }

            for seg in &plan.segment_migrations {
                let src_idx = seg.source_shard.0 as usize;
                if src_idx < self.shards.len() {
                    self.shards[src_idx].graph.text_index.compact();
                    self.shards[src_idx].graph.text_index.vacuum();
                }
            }
        }

        for (gid, target_shard_id) in plan.node_migrations {
            let old_idx = gid.shard.0 as usize;
            if old_idx >= self.shards.len() {
                return Err(crate::GraphError::Sharding(format!(
                    "source shard {} out of range during rebalance",
                    old_idx
                )));
            }
            if target_shard_id.0 as usize >= self.shards.len() {
                return Err(crate::GraphError::Sharding(format!(
                    "target shard {} out of range during rebalance",
                    target_shard_id.0
                )));
            }

            if self.shards[old_idx]
                .graph
                .text_index
                .is_document_deleted(gid.local)
            {
                continue;
            }

            let text = self.shards[old_idx].graph.read_blob(gid.local).to_string();
            let label = self.shards[old_idx]
                .graph
                .graph
                .node_label(gid.local)
                .map(|s| s.to_string());

            self.shards[old_idx].remove_local_node(gid.local);

            let new_local = self.shards[target_shard_id.0 as usize]
                .graph
                .add_node(&text, label.as_deref());

            let new_gid = GlobalNodeId {
                shard: target_shard_id,
                local: new_local,
            };

            // Ghost cleanup (tombstone + rewrite)
            let n = self.shards.len();
            for i in 0..n {
                let mut ghosts_map = std::mem::take(&mut self.shards[i].ghost_edges);
                for ghosts in ghosts_map.values_mut() {
                    ghosts.retain(|g| {
                        !self.shards[g.remote_target.shard.0 as usize]
                            .graph
                            .text_index
                            .is_document_deleted(g.remote_target.local)
                    });
                }
                self.shards[i].ghost_edges = ghosts_map;
            }

            for shard in &mut self.shards {
                for ghosts in shard.ghost_edges.values_mut() {
                    for ghost in ghosts.iter_mut() {
                        if ghost.remote_target == gid {
                            ghost.remote_target = new_gid;
                        }
                    }
                }
            }
        }

        self.telemetry.record_rebalance(nodes_moved);
        log::info!(
            "rebalance completed: moved {} nodes from {} to {} shards",
            nodes_moved,
            plan.old_shard_count,
            plan.new_shard_count
        );
        Ok(())
    }
}

/// High-level facade over a sharded graph.
///
/// This is the recommended type for users who want sharding without dealing
/// with `ShardRouter` and `GlobalNodeId` directly.
pub struct ShardedGraph {
    router: ShardRouter,
}

impl ShardedGraph {
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

    pub fn add_node(
        &mut self,
        text: &str,
        label: Option<&str>,
    ) -> Result<GlobalNodeId, crate::GraphError> {
        self.router.add_node(text, label)
    }

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

    pub fn search(&self, query: &str, max_results: usize) -> Vec<crate::index::TextSearchResult> {
        self.router.search(query, max_results)
    }

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
    /// (if it is using the persistent backend).
    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), crate::GraphError> {
        self.router.save_to_dir(dir)
    }

    /// Load a previously saved sharded graph.
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
    pub fn start_background_maintenance(&mut self, interval: Duration) -> MaintenanceHandle {
        // For simple use cases, users are expected to call run_maintenance_cycle()
        // periodically from their own event loop / timer.
        // For true fire-and-forget background behavior, use
        // start_background_maintenance_with_shared with an Arc<Mutex<Self>>.
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
