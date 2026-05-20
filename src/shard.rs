//! Graph Sharding (Section 12 of core_graph_design.md)
//!
//! Distributes nodes across multiple `IndexedKnowledgeGraph` instances (shards)
//! using consistent hashing. Cross-shard edges are represented as "ghost edges".
//!
//! This is an in-process sharding implementation suitable for scaling beyond
//! single-machine memory while keeping a unified query API.

use std::collections::HashMap;

use crate::ids::NodeId;
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

/// Cached information about a remote node fetched from another shard.
#[derive(Debug, Clone)]
pub struct CachedRemoteNode {
    pub label: Option<String>,
    pub blob_snippet: String, // we usually don't need the full text
    pub outgoing_edge_count: u32,
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

    /// Try to resolve a ghost edge by fetching the remote node (with caching).
    pub fn resolve_remote_node(&mut self, target: GlobalNodeId) -> Option<&CachedRemoteNode> {
        if self.remote_cache.contains_key(&target) {
            return self.remote_cache.get(&target);
        }

        // In-process access: we can reach the other shard directly via the router later.
        // For now we just record that we would need to fetch it.
        // Real fetching will be orchestrated by ShardRouter.
        None
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

            if out.len() >= max_results { break; }
            if e.depth >= max_depth { continue; }

            // --- Follow real GhostEdges ---
            if let Some(ghosts) = shard.ghost_edges.get(&node.0) {
                for g in ghosts {
                    if g.strength < min_strength { continue; }

                    let r = g.remote_target;
                    let key = (r.shard.0, r.local.0);
                    if visited.contains(&key) { continue; }
                    visited.insert(key);

                    // Fetch remote node (in-process direct access)
                    let remote_shard = &self.shards[r.shard.0 as usize];
                    let remote_text = remote_shard.graph.read_blob(r.local);

                    // Simple cache
                    remote_shard.remote_cache.insert(r, CachedRemoteNode {
                        label: remote_shard.graph.graph.node_label(r.local).map(|s| s.to_string()),
                        blob_snippet: remote_text.chars().take(100).collect(),
                        outgoing_edge_count: 0,
                    });

                    let new_strength = e.strength * g.strength;
                    if new_strength >= min_strength {
                        pq.push(Entry {
                            gnode: r,
                            strength: new_strength,
                            depth: e.depth + 1,
                            text_score: e.text_score,
                        });
                    }
                }
            }
        }

        out.sort_by(|a, b| b.fused_score.partial_cmp(&a.fused_score).unwrap_or(Ordering::Equal));
        out.truncate(max_results);
        out
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
    ) -> Vec<crate::index::CombinedResult> {
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

    pub fn router_mut(&mut self) -> &mut ShardRouter {
        &mut self.router
    }
}
