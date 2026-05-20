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

    pub fn shard_for_node(&self, global_node_id: u64) -> ShardId {
        let bytes = global_node_id.to_le_bytes();
        self.hash_ring.locate(&bytes)
    }
}
