//! Ergonomic query builder and best-first priority traversal.
//!
//! The main entry point is [`KnowledgeGraph::find`], which returns a
//! [`QueryBuilder`]. The builder lets you express:
//!
//! - Seed nodes (or start from text search via `IndexedKnowledgeGraph`)
//! - Required properties (turned into bitmasks for O(1) pruning)
//! - Minimum strength, max depth, result limit
//!
//! The actual traversal uses a `BinaryHeap` ordered by cumulative path
//! strength and performs aggressive early pruning using node-level
//! `outgoing_property_mask` checks.

use crate::graph::KnowledgeGraph;
use crate::ids::{EdgeId, NodeId, PropertyTag};
use crate::properties::{PropertyFilter, PropertyValue};
use std::cmp::Ordering;
use std::collections::BinaryHeap;

/// Low-level query descriptor (mostly used internally by the traversal engine).
#[derive(Debug, Clone)]
pub struct GraphQuery {
    pub seeds: Vec<NodeId>,
    pub property_filters: Vec<(PropertyTag, PropertyFilter)>,
    pub required_mask: u64,
    pub min_strength: f64,
    pub max_depth: u32,
    pub max_results: usize,
    pub sort_by_strength: bool,
}

/// A single result returned by a graph traversal.
#[derive(Debug, Clone)]
pub struct QueryResult {
    /// The reached node.
    pub node_id: NodeId,
    /// The sequence of edges that formed the best path from a seed to this node.
    pub path: Vec<EdgeId>,
    /// Product of edge strengths along the path (higher is better).
    pub cumulative_strength: f64,
    /// Number of hops from the seed.
    pub depth: u32,
}

/// Ergonomic builder for graph traversals.
///
/// Obtain one via [`KnowledgeGraph::find`].
///
/// The builder is consumed by [`QueryBuilder::execute`], which runs a
/// best-first priority traversal with aggressive bitmask-based pruning.
pub struct QueryBuilder<'g> {
    graph: &'g KnowledgeGraph,
    seeds: Vec<NodeId>,
    filters: Vec<(PropertyTag, PropertyFilter)>,
    min_strength: f64,
    max_depth: u32,
    max_results: usize,
    sort_by_strength: bool,
}

impl KnowledgeGraph {
    /// Starts building a graph traversal query.
    ///
    /// This is the main entry point for the low-level graph query API.
    ///
    /// # Example
    ///
    /// ```rust
    /// use grap::{KnowledgeGraph, NodeId};
    ///
    /// let mut g = KnowledgeGraph::new();
    /// // In real code you would obtain a real NodeId first.
    /// let dummy = g.add_node("example", None);
    /// let results = g.find()
    ///     .from_node(dummy)
    ///     .with_property("topic")
    ///     .min_strength(0.7)
    ///     .depth(3)
    ///     .limit(20)
    ///     .execute();
    /// ```
    pub fn find(&self) -> QueryBuilder<'_> {
        QueryBuilder {
            graph: self,
            seeds: Vec::new(),
            filters: Vec::new(),
            min_strength: 0.0,
            max_depth: 3,
            max_results: 100,
            sort_by_strength: true,
        }
    }
}

impl<'g> QueryBuilder<'g> {
    /// Use a specific node as a traversal seed.
    pub fn from_node(mut self, node: NodeId) -> Self {
        self.seeds.push(node);
        self
    }

    /// Use multiple nodes as traversal seeds.
    pub fn from_nodes(mut self, nodes: &[NodeId]) -> Self {
        self.seeds.extend_from_slice(nodes);
        self
    }

    /// Require that every edge on the path has the named property.
    pub fn with_property(mut self, name: &str) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Exists));
        }
        self
    }

    /// Require that a property equals a specific value.
    pub fn where_property(mut self, name: &str, value: PropertyValue) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Equals(value)));
        }
        self
    }

    /// Require that a numeric property falls inside a range.
    pub fn where_in_range(mut self, name: &str, min: f64, max: f64) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Range { min, max }));
        }
        self
    }

    /// Set the minimum cumulative path strength required to keep expanding.
    pub fn min_strength(mut self, s: f64) -> Self {
        self.min_strength = s;
        self
    }

    /// Maximum number of hops from any seed.
    pub fn depth(mut self, d: u32) -> Self {
        self.max_depth = d;
        self
    }

    /// Maximum number of results to return.
    pub fn limit(mut self, n: usize) -> Self {
        self.max_results = n;
        self
    }

    /// Execute the traversal and return ranked results.
    pub fn execute(self) -> Vec<QueryResult> {
        let mut required_mask = 0u64;
        for (tag, _) in &self.filters {
            required_mask |= 1u64 << tag.0;
        }
        let q = GraphQuery {
            seeds: self.seeds,
            property_filters: self.filters,
            required_mask,
            min_strength: self.min_strength,
            max_depth: self.max_depth,
            max_results: self.max_results,
            sort_by_strength: self.sort_by_strength,
        };
        self.graph.query(&q)
    }

    pub fn execute_with_text(self) -> Vec<(QueryResult, &'g str)> {
        let g = self.graph;
        self.execute()
            .into_iter()
            .map(|r| (r.clone(), g.read_blob(r.node_id)))
            .collect()
    }
}

impl KnowledgeGraph {
    /// The main traversal engine. See md:525 for the algorithm.
    pub fn query(&self, q: &GraphQuery) -> Vec<QueryResult> {
        // PORT NOTE: This is a "greedy best-first" search (priority = cumulative strength product).
        // The visited set means we take the first (highest-strength prefix) path that reaches each node.
        // Not guaranteed to be the globally strongest path to every node (would require decrease-key Dijkstra).
        // This matches the prototype in the design document exactly.

        #[derive(Debug)]
        struct QueueEntry {
            node: NodeId,
            cumulative_strength: f64,
            depth: u32,
            path: Vec<EdgeId>,
        }

        impl PartialEq for QueueEntry {
            fn eq(&self, other: &Self) -> bool {
                self.cumulative_strength == other.cumulative_strength
            }
        }
        impl Eq for QueueEntry {}
        impl PartialOrd for QueueEntry {
            fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
                self.cumulative_strength
                    .partial_cmp(&other.cumulative_strength)
            }
        }
        impl Ord for QueueEntry {
            fn cmp(&self, other: &Self) -> Ordering {
                // Higher strength = "greater" so BinaryHeap pops strongest first.
                self.partial_cmp(other).unwrap_or(Ordering::Equal)
            }
        }

        let mut results = Vec::new();
        let mut visited = vec![false; self.node_count()];
        let mut heap: BinaryHeap<QueueEntry> = BinaryHeap::new();

        let seeds: Vec<NodeId> = if q.seeds.is_empty() {
            (0..self.node_count() as u32).map(NodeId).collect()
        } else {
            q.seeds.clone()
        };

        for seed in seeds {
            if (seed.0 as usize) < visited.len() {
                visited[seed.0 as usize] = true;
            }
            heap.push(QueueEntry {
                node: seed,
                cumulative_strength: 1.0,
                depth: 0,
                path: Vec::new(),
            });
        }

        while let Some(entry) = heap.pop() {
            results.push(QueryResult {
                node_id: entry.node,
                path: entry.path.clone(),
                cumulative_strength: entry.cumulative_strength,
                depth: entry.depth,
            });

            if results.len() >= q.max_results {
                break;
            }
            if entry.depth >= q.max_depth {
                continue;
            }

            let node = &self.nodes.get(entry.node.0 as usize);
            let node = match node {
                Some(n) => n,
                None => continue,
            };

            // Fast node-level pruning via outgoing mask
            if (node.outgoing_property_mask & q.required_mask) != q.required_mask {
                continue;
            }

            for &edge_id in &node.outgoing {
                let edge = &self.edges[edge_id.0 as usize];
                if !edge.matches_mask(q.required_mask) {
                    continue;
                }
                if edge.strength < q.min_strength {
                    continue;
                }
                // Detailed filters (stub for now - full impl in Phase 1 completion)
                if !self.edge_passes_filters(edge, &q.property_filters) {
                    continue;
                }

                let target = edge.target;
                if (target.0 as usize) < visited.len() && visited[target.0 as usize] {
                    continue;
                }
                if (target.0 as usize) < visited.len() {
                    visited[target.0 as usize] = true;
                }

                let mut new_path = entry.path.clone();
                new_path.push(edge_id);

                heap.push(QueueEntry {
                    node: target,
                    cumulative_strength: entry.cumulative_strength * edge.strength,
                    depth: entry.depth + 1,
                    path: new_path,
                });
            }
        }

        if q.sort_by_strength {
            results.sort_by(|a, b| {
                b.cumulative_strength
                    .partial_cmp(&a.cumulative_strength)
                    .unwrap_or(Ordering::Equal)
            });
        }
        results
    }

    #[inline]
    fn edge_passes_filters(
        &self,
        edge: &crate::graph::Edge,
        filters: &[(PropertyTag, PropertyFilter)],
    ) -> bool {
        // Exact port of md:665-698 (with String labels instead of CompactString)
        for (tag, filter) in filters {
            match filter {
                PropertyFilter::Exists => {
                    if edge.properties.iter().find(|(t, _)| t == tag).is_none() {
                        return false;
                    }
                }
                PropertyFilter::Equals(expected) => {
                    match edge.properties.iter().find(|(t, _)| t == tag).map(|(_, v)| v) {
                        Some(val) if val == expected => {}
                        _ => return false,
                    }
                }
                PropertyFilter::Range { min, max } => {
                    match edge.properties.iter().find(|(t, _)| t == tag).map(|(_, v)| v) {
                        Some(PropertyValue::Float(v)) if *v >= *min && *v <= *max => {}
                        Some(PropertyValue::Int(v))
                            if (*v as f64) >= *min && (*v as f64) <= *max => {}
                        _ => return false,
                    }
                }
                PropertyFilter::MinStrength(_) => {
                    // Applied at edge.strength level in the caller.
                }
            }
        }
        true
    }
}

// The internal Edge type is currently private in graph.rs; we need a small bridge.
// For the initial skeleton we accept the stub above. Real impl will move the fn or make Edge pub(crate).
