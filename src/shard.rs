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

/// A single shard holding a partition of the overall graph.
pub struct Shard {
    pub id: ShardId,
    pub graph: IndexedKnowledgeGraph,
    /// Edges that point to nodes on other shards.
    pub ghost_edges: HashMap<u32, Vec<GhostEdge>>,
}

impl Shard {
    pub fn new(id: ShardId) -> Self {
        Self {
            id,
            graph: IndexedKnowledgeGraph::new(),
            ghost_edges: HashMap::new(),
        }
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

    /// High-level distributed "search + traverse" across shards.
    ///
    /// This version:
    /// - Runs text search on every shard
    /// - Collects the globally best seeds
    /// - Runs local traversals on the owning shard for each seed
    ///
    /// Ghost-edge following across shards is not yet implemented (future work).
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
        // 1. Scatter text search to all shards
        let mut all_seeds = Vec::new();
        for shard in &self.shards {
            let mut local = shard.graph.search(text_query, max_seeds);
            all_seeds.append(&mut local);
        }

        // 2. Global ranking of seeds
        all_seeds.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        all_seeds.truncate(max_seeds);

        let mut results = Vec::new();

        // 3. Run local traversal on the correct shard for each seed
        for seed in &all_seeds {
            // Find which shard this seed actually belongs to by scanning
            // (in a real system we would maintain a GlobalNodeId → ShardId map)
            let mut handled = false;
            for shard in &self.shards {
                // Cheap check: try to run a tiny traversal; if the node exists locally it will work
                // For demo purposes we run on every shard and dedup later.
                let local = shard.graph.search_and_traverse_default(
                    text_query,
                    1,
                    property_names,
                    min_strength,
                    max_depth,
                    max_results,
                );

                for mut cr in local {
                    if cr.graph_result.node_id == seed.node_id {
                        cr.text_score = seed.score;
                        cr.fused_score = cr.text_score * weights.text + cr.graph_strength * weights.graph;
                        results.push(cr);
                        handled = true;
                        break;
                    }
                }
                if handled {
                    break;
                }
            }
        }

        // 4. Final global ranking by fused score
        results.sort_by(|a, b| {
            b.fused_score
                .partial_cmp(&a.fused_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        results.truncate(max_results);
        results
    }
}
