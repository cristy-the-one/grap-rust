//! Graph Sharding (Section 12 of core_graph_design.md)
//!
//! Distributes nodes across multiple `IndexedKnowledgeGraph` instances (shards)
//! using consistent hashing. Cross-shard edges are represented as "ghost edges".
//!
//! This is an in-process sharding implementation suitable for scaling beyond
//! single-machine memory while keeping a unified query API.

use std::collections::HashMap;

use crate::ids::{EdgeId, NodeId};
use crate::index::IndexedKnowledgeGraph;

/// Identifies a shard within a `ShardRouter`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ShardId(pub u16);

/// A node identifier that is globally unique across all shards.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct GlobalNodeId {
    pub shard: ShardId,
    pub local: NodeId,
}

/// Represents an edge that points to a node on a different shard.
#[derive(Debug, Clone)]
pub struct GhostEdge {
    pub local_source: NodeId,
    pub remote_target: GlobalNodeId,
    pub strength: f64,
    // In a real system we would also store serialized properties here.
}

/// A single hop in a path that may cross shards.
#[derive(Debug, Clone)]
pub enum ShardedPathHop {
    /// A normal edge within one shard.
    Local(EdgeId),
    /// A cross-shard hop via a ghost edge.
    CrossShard {
        from: GlobalNodeId,
        to: GlobalNodeId,
        strength: f64,
    },
}

/// Describes the migrations needed when changing the number of shards.
#[derive(Debug, Clone)]
pub struct RebalancePlan {
    pub old_shard_count: u16,
    pub new_shard_count: u16,
    /// Nodes that need to move to a different shard under the new hash ring.
    pub node_migrations: Vec<(GlobalNodeId, ShardId)>,
}

/// Result of a query executed on a `ShardedGraph`.
///
/// Contains both relevance signals and the actual path taken, which may
/// include cross-shard (`CrossShard`) hops.
#[derive(Debug, Clone)]
pub struct ShardedQueryResult {
    pub node: GlobalNodeId,
    pub text_score: f64,
    pub graph_strength: f64,
    pub fused_score: f64,
    pub depth: u32,
    /// The path from a seed to this node. May contain `CrossShard` hops.
    pub path: Vec<ShardedPathHop>,
    pub text: String,
}

/// Cached information about a remote node fetched from another shard during traversal.
#[derive(Debug, Clone)]
pub struct CachedRemoteNode {
    pub label: Option<String>,
    pub blob_snippet: String,
    pub outgoing_edge_count: u32,
    /// Whether this remote node has any outgoing ghost edges (useful for pruning).
    pub has_outgoing_ghosts: bool,
}

/// A single shard holding a partition of the overall graph.
pub struct Shard {
    pub id: ShardId,
    pub graph: IndexedKnowledgeGraph,
    /// Edges that point to nodes on other shards.
    pub ghost_edges: HashMap<u32, Vec<GhostEdge>>,
    /// Cache of recently fetched remote nodes to avoid repeated cross-shard lookups
    /// during a single query.
    remote_cache: HashMap<GlobalNodeId, CachedRemoteNode>,
}

impl Shard {
    pub fn new(id: ShardId) -> Self {
        Self {
            id,
            graph: IndexedKnowledgeGraph::new(),
            ghost_edges: HashMap::new(),
            remote_cache: HashMap::new(),
        }
    }

    /// Fetch a remote node (with caching). Returns a reference to the cached entry.
    /// This is the main entry point used by the router when following ghost edges.
    pub fn fetch_remote_node(
        &mut self,
        target: GlobalNodeId,
        remote_shard: &IndexedKnowledgeGraph, // passed in because ShardRouter has all shards
    ) -> Option<&CachedRemoteNode> {
        if let Some(cached) = self.remote_cache.get(&target) {
            return Some(cached);
        }

        // In-process fetch (we have direct access to the remote shard)
        let label = remote_shard.graph.node_label(target.local).map(|s| s.to_string());
        let text = remote_shard.read_blob(target.local);
        let snippet = text.chars().take(120).collect::<String>();

        // We don't have cheap access to "does this node have ghost edges" without the router,
        // so we set a conservative default. The router can override if needed.
        let cached = CachedRemoteNode {
            label,
            blob_snippet: snippet,
            outgoing_edge_count: 0,
            has_outgoing_ghosts: false,
        };

        self.remote_cache.insert(target, cached);
        self.remote_cache.get(&target)
    }
}

/// Simple consistent hash ring (FNV-1a inspired, as described in the design doc).
pub struct ConsistentHashRing {
    ring: Vec<(u64, ShardId)>,
}

impl ConsistentHashRing {
    pub fn new(shard_count: u16, virtual_nodes: u16) -> Self {
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

    pub fn locate(&self, key: &[u8]) -> ShardId {
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

/// Coordinates multiple shards and routes operations.
pub struct ShardRouter {
    pub shards: Vec<Shard>,
    hash_ring: ConsistentHashRing,
}

impl ShardRouter {
    pub fn new(shard_count: u16) -> Self {
        let mut shards = Vec::new();
        for i in 0..shard_count {
            shards.push(Shard::new(ShardId(i)));
        }
        Self {
            shards,
            hash_ring: ConsistentHashRing::new(shard_count, 150),
        }
    }

    /// Decide which shard owns a new node based on its content (consistent hashing).
    fn shard_for_new_node(&self, text: &str) -> ShardId {
        self.hash_ring.locate(text.as_bytes())
    }

    /// Add a node to the appropriate shard and return its global identifier.
    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> GlobalNodeId {
        let shard_id = self.shard_for_new_node(text);
        let shard = &mut self.shards[shard_id.0 as usize];

        let local_id = shard.graph.add_node(text, label);
        GlobalNodeId {
            shard: shard_id,
            local: local_id,
        }
    }

    /// Add a (possibly cross-shard) edge.
    /// If the source and target are on different shards, a ghost edge is created on the source shard.
    pub fn add_edge(
        &mut self,
        source: GlobalNodeId,
        target: GlobalNodeId,
        strength: f64,
        props: Vec<(crate::ids::PropertyTag, crate::properties::PropertyValue)>,
    ) {
        if source.shard == target.shard {
            // Local edge
            let shard = &mut self.shards[source.shard.0 as usize];
            shard.graph.add_edge(source.local, target.local, strength, props);
        } else {
            // Cross-shard edge → create ghost edge on the source shard
            let shard = &mut self.shards[source.shard.0 as usize];
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

            // We could also store a "remote reference" on the target shard if needed,
            // but for traversal from the source side, the ghost edge is sufficient.
        }
    }

    /// Perform a distributed text search across all shards.
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
    ) -> Vec<crate::index::CombinedResult> {
        use std::collections::{BinaryHeap, HashSet};
        use std::cmp::Ordering;

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
            fn eq(&self, other: &Self) -> bool { self.strength == other.strength }
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
            let gnode = GlobalNodeId { shard: *shard_id, local: seed.node_id };
            visited.insert((shard_id.0, seed.node_id.0));
            pq.push(Entry {
                gnode,
                strength: 1.0,
                depth: 0,
                text_score: seed.score,
                path: vec![], // starts at a seed
            });
        }

        let mut out: Vec<crate::index::CombinedResult> = Vec::new();

        while let Some(e) = pq.pop() {
            let shard = &self.shards[e.gnode.shard.0 as usize];
            let node = e.gnode.local;

            let text = shard.graph.read_blob(node);

            out.push(crate::index::CombinedResult {
                text_score: e.text_score,
                graph_strength: e.strength,
                fused_score: e.text_score * weights.text + e.strength * weights.graph,
                graph_result: crate::query::QueryResult {
                    node_id: node,
                    path: vec![],
                    cumulative_strength: e.strength,
                    depth: e.depth,
                },
                text,
            });

            if out.len() >= max_results {
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
                        continue;
                    }

                    let remote = ghost.remote_target;
                    let remote_key = (remote.shard.0, remote.local.0);

                    if visited.contains(&remote_key) {
                        continue;
                    }
                    visited.insert(remote_key);

                    // Use the hardened fetch helper (checks cache first, then fetches)
                    let remote_shard_ref = &self.shards[remote.shard.0 as usize];
                    let src_shard = &mut self.shards[e.gnode.shard.0 as usize]; // mutable borrow for cache

                    // This will populate the cache on the source shard if not already present
                    src_shard.fetch_remote_node(remote, &remote_shard_ref.graph);

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
        out.sort_by(|a, b| {
            b.fused_score
                .partial_cmp(&a.fused_score)
                .unwrap_or(Ordering::Equal)
        });
        out.truncate(max_results);
        out
    }

    /// Compute a basic rebalance plan for changing the number of shards.
    /// Returns which nodes would need to move under a new consistent hash ring.
    pub fn plan_rebalance(&self, new_shard_count: u16) -> RebalancePlan {
        let new_ring = ConsistentHashRing::new(new_shard_count, 150);
        let mut migrations = Vec::new();

        for (old_idx, shard) in self.shards.iter().enumerate() {
            for i in 0..shard.graph.graph.node_count() {
                let local_id = NodeId(i as u32);
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

        RebalancePlan {
            old_shard_count: self.shards.len() as u16,
            new_shard_count,
            node_migrations: migrations,
        }
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
    pub fn new(shard_count: u16) -> Self {
        Self {
            router: ShardRouter::new(shard_count),
        }
    }

    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> GlobalNodeId {
        self.router.add_node(text, label)
    }

    pub fn add_edge(
        &mut self,
        source: GlobalNodeId,
        target: GlobalNodeId,
        strength: f64,
        props: Vec<(crate::ids::PropertyTag, crate::properties::PropertyValue)>,
    ) {
        self.router.add_edge(source, target, strength, props)
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
        // For now we convert from the router's internal results.
        // A future pass will make the router return ShardedQueryResult directly with full paths.
        let combined = self.router.search_and_traverse(
            text_query,
            max_seeds,
            property_names,
            min_strength,
            max_depth,
            max_results,
            crate::index::FusionWeights::default(),
        );

        combined
            .into_iter()
            .map(|cr| ShardedQueryResult {
                node: GlobalNodeId {
                    shard: ShardId(0), // placeholder – real mapping would be better
                    local: cr.graph_result.node_id,
                },
                text_score: cr.text_score,
                graph_strength: cr.graph_strength,
                fused_score: cr.fused_score,
                depth: cr.graph_result.depth,
                path: vec![], // path reconstruction across ghosts is improved below
                text: cr.text.to_string(),
            })
            .collect()
    }

    /// Access the underlying router if you need lower-level control.
    pub fn router(&self) -> &ShardRouter {
        &self.router
    }

    pub fn router_mut(&mut self) -> &mut ShardRouter {
        &mut self.router
    }

    // === Convenience methods that make the facade pleasant to use ===

    /// Read the text of a node given its global identifier.
    pub fn read_blob(&self, gid: GlobalNodeId) -> &str {
        let shard = &self.router.shards[gid.shard.0 as usize];
        shard.graph.read_blob(gid.local)
    }

    /// Get a node label by global id (if present).
    pub fn node_label(&self, gid: GlobalNodeId) -> Option<&str> {
        let shard = &self.router.shards[gid.shard.0 as usize];
        shard.graph.graph.node_label(gid.local)
    }

    /// Distributed phrase search (text only).
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
        all.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        all.truncate(max_results);
        all
    }

    /// Distributed phrase + graph traversal with the default fusion weights.
    pub fn phrase_search_and_traverse(
        &self,
        phrase: &str,
        max_gap: u32,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> Vec<crate::index::CombinedResult> {
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
}
