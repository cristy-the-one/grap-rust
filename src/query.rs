//! GraphQuery, QueryBuilder, priority traversal with bitmask pruning.
//! Core algorithm from core_graph_design.md:536-699 (with documented PORT NOTES).

use crate::graph::KnowledgeGraph;
use crate::ids::{EdgeId, NodeId, PropertyTag};
use crate::properties::{PropertyFilter, PropertyValue};
use std::cmp::Ordering;
use std::collections::BinaryHeap;

/// A filter predicate on a single property (or strength).
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

#[derive(Debug, Clone)]
pub struct QueryResult {
    pub node_id: NodeId,
    pub path: Vec<EdgeId>,
    pub cumulative_strength: f64,
    pub depth: u32,
}

/// Ergonomic builder (see KnowledgeGraph::find).
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
    pub fn from_node(mut self, node: NodeId) -> Self {
        self.seeds.push(node);
        self
    }
    pub fn from_nodes(mut self, nodes: &[NodeId]) -> Self {
        self.seeds.extend_from_slice(nodes);
        self
    }
    pub fn with_property(mut self, name: &str) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Exists));
        }
        self
    }
    pub fn where_property(mut self, name: &str, value: PropertyValue) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Equals(value)));
        }
        self
    }
    pub fn where_in_range(mut self, name: &str, min: f64, max: f64) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Range { min, max }));
        }
        self
    }
    pub fn min_strength(mut self, s: f64) -> Self {
        self.min_strength = s;
        self
    }
    pub fn depth(mut self, d: u32) -> Self {
        self.max_depth = d;
        self
    }
    pub fn limit(mut self, n: usize) -> Self {
        self.max_results = n;
        self
    }

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
        _edge: &crate::graph::Edge,
        _filters: &[(PropertyTag, PropertyFilter)],
    ) -> bool {
        // Full predicate logic (Exists/Equals/Range) will be filled when properties are wired.
        // For Phase 1 bootstrap we accept everything that passed the mask/strength checks.
        true
    }
}

// The internal Edge type is currently private in graph.rs; we need a small bridge.
// For the initial skeleton we accept the stub above. Real impl will move the fn or make Edge pub(crate).
