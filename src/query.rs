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
use std::collections::{BinaryHeap, HashMap};

/// Low-level query descriptor (mostly used internally by the traversal engine).
#[derive(Debug, Clone)]
pub struct GraphQuery {
    /// Nodes to start the traversal from.
    pub seeds: Vec<NodeId>,
    /// Per-edge property filters applied during traversal.
    pub property_filters: Vec<(PropertyTag, PropertyFilter)>,
    /// Bitmask of required property tags (derived from `property_filters`).
    pub required_mask: u64,
    /// Edges with strength below this threshold are skipped.
    pub min_strength: f64,
    /// Maximum number of hops from any seed node.
    pub max_depth: u32,
    /// Maximum number of results to return.
    pub max_results: usize,
    /// If `true`, results are sorted by cumulative path strength (highest first).
    pub sort_by_strength: bool,
    /// If `Some(tag)`, use the named edge property's numeric value as the
    /// traversal priority weight instead of `edge.strength`.
    /// Falls back to `edge.strength` when the property is absent on a given edge.
    /// `None` (default) preserves existing behaviour exactly.
    pub traversal_weight_property: Option<PropertyTag>,
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
    traversal_weight_property: Option<PropertyTag>,
    unsatisfiable: bool,
}

impl std::fmt::Debug for QueryBuilder<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("QueryBuilder")
            .field("seeds", &self.seeds)
            .field("min_strength", &self.min_strength)
            .field("max_depth", &self.max_depth)
            .field("max_results", &self.max_results)
            .finish_non_exhaustive()
    }
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
    /// let dummy = g.add_node("example", None, None);
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
            traversal_weight_property: None,
            unsatisfiable: false,
        }
    }

    /// Returns all direct outgoing neighbors reached via an edge whose
    /// `property == value`. Returns `(target_node, edge_strength)` pairs.
    ///
    /// Returns an empty `Vec` (never `Err`) when:
    /// - `node` is out of bounds or deleted
    /// - `property` is not registered in the graph
    pub fn neighbors_by_relation(
        &self,
        node: NodeId,
        property: &str,
        value: PropertyValue,
    ) -> Vec<(NodeId, f64)> {
        let idx = node.0 as usize;
        if idx >= self.nodes.len() || self.deleted.get(idx).copied().unwrap_or(true) {
            return Vec::new();
        }
        let tag = match self.properties.get_tag(property) {
            Some(t) => t,
            None => return Vec::new(),
        };
        self.nodes[idx]
            .outgoing
            .iter()
            .filter_map(|&eid| {
                let edge = &self.edges[eid.0 as usize];
                let matches = edge
                    .properties
                    .iter()
                    .any(|(t, v)| *t == tag && *v == value);
                if matches {
                    Some((edge.target, edge.strength))
                } else {
                    None
                }
            })
            .collect()
    }

    /// Returns all direct incoming predecessors connected via an edge whose
    /// `property == value`. Returns `(source_node, edge_strength)` pairs.
    ///
    /// Returns an empty `Vec` (never `Err`) when:
    /// - `node` is out of bounds or deleted
    /// - `property` is not registered in the graph
    pub fn predecessors_by_relation(
        &self,
        node: NodeId,
        property: &str,
        value: PropertyValue,
    ) -> Vec<(NodeId, f64)> {
        let idx = node.0 as usize;
        if idx >= self.nodes.len() || self.deleted.get(idx).copied().unwrap_or(true) {
            return Vec::new();
        }
        let tag = match self.properties.get_tag(property) {
            Some(t) => t,
            None => return Vec::new(),
        };
        self.nodes[idx]
            .incoming
            .iter()
            .filter_map(|&eid| {
                let edge = &self.edges[eid.0 as usize];
                let matches = edge
                    .properties
                    .iter()
                    .any(|(t, v)| *t == tag && *v == value);
                if matches {
                    Some((edge.source, edge.strength))
                } else {
                    None
                }
            })
            .collect()
    }

    /// Best-first traversal up to `depth` hops, following only edges where
    /// `property == value`. Returns full [`QueryResult`] (path, depth, strength).
    ///
    /// This is a thin wrapper over [`KnowledgeGraph::find`] with
    /// [`QueryBuilder::where_property`] — no new traversal logic.
    ///
    /// Returns an empty `Vec` (never `Err`) for an invalid node or unregistered
    /// property.
    pub fn traverse_by_relation(
        &self,
        node: NodeId,
        property: &str,
        value: PropertyValue,
        depth: u32,
        max_results: usize,
    ) -> Vec<QueryResult> {
        self.find()
            .from_node(node)
            .where_property(property, value)
            .depth(depth)
            .limit(max_results)
            .execute()
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
    /// An unregistered name makes the query return no results.
    pub fn with_property(mut self, name: &str) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Exists));
        } else {
            self.unsatisfiable = true;
        }
        self
    }

    /// Require that a property equals a specific value.
    /// An unregistered name makes the query return no results.
    pub fn where_property(mut self, name: &str, value: PropertyValue) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Equals(value)));
        } else {
            self.unsatisfiable = true;
        }
        self
    }

    /// Require that a numeric property falls inside a range.
    /// An unregistered name makes the query return no results.
    pub fn where_in_range(mut self, name: &str, min: f64, max: f64) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Range { min, max }));
        } else {
            self.unsatisfiable = true;
        }
        self
    }

    /// Use a named edge property's numeric value as the traversal priority weight.
    ///
    /// The property must have been registered via
    /// `KnowledgeGraph::properties.register` before building edges. Unregistered
    /// names are silently ignored (behaviour is identical to calling without this
    /// method).
    ///
    /// Falls back to `edge.strength` for any edge that lacks the property.
    /// `min_strength` still gates on `edge.strength`, not on the property value.
    ///
    /// **Value range:** effective weights must be finite and in `[0.0, 1.0]`.
    /// Edges with invalid effective weights are skipped, so extending a path
    /// cannot increase its score and best-first ordering remains correct.
    pub fn weight_by_property(mut self, name: &str) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.traversal_weight_property = Some(tag);
        }
        self
    }

    /// Skip edges whose stored strength is below this threshold.
    /// This threshold is applied per edge, not to the cumulative path score.
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
        if self.unsatisfiable {
            return Vec::new();
        }
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
            traversal_weight_property: self.traversal_weight_property,
        };
        self.graph.query(&q)
    }

    /// Like [`Self::execute`] but also returns the text content of each result node.
    pub fn execute_with_text(self) -> Vec<(QueryResult, &'g str)> {
        let g = self.graph;
        self.execute()
            .into_iter()
            .map(|r| {
                let text = g.read_blob(r.node_id);
                (r, text)
            })
            .collect()
    }
}

impl KnowledgeGraph {
    /// Return distinct live nodes in descending path-score order, including seeds.
    /// Empty seeds mean all live nodes; invalid and duplicate seeds are ignored.
    /// A zero result limit returns nothing.
    ///
    /// Effective edge weights must be finite and in `[0, 1]`; other weights are
    /// skipped. Each result carries its strongest path within `max_depth` hops.
    /// A shorter, weaker route may still expand to reach further descendants.
    /// Exploration state is sparse and queued routes share predecessor records;
    /// full edge paths are materialized only for returned results.
    pub fn query(&self, q: &GraphQuery) -> Vec<QueryResult> {
        if q.max_results == 0 {
            return Vec::new();
        }

        #[derive(Debug)]
        struct QueueEntry {
            node: NodeId,
            cumulative_strength: f64,
            depth: u32,
            parent: Option<usize>,
            via: Option<EdgeId>,
        }

        struct PathLink {
            parent: Option<usize>,
            edge: EdgeId,
        }

        impl PartialEq for QueueEntry {
            fn eq(&self, other: &Self) -> bool {
                self.cmp(other) == Ordering::Equal
            }
        }
        impl Eq for QueueEntry {}
        impl PartialOrd for QueueEntry {
            fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
                Some(self.cmp(other))
            }
        }
        impl Ord for QueueEntry {
            fn cmp(&self, other: &Self) -> Ordering {
                self.cumulative_strength
                    .total_cmp(&other.cumulative_strength)
                    .then_with(|| other.depth.cmp(&self.depth))
                    .then_with(|| other.node.0.cmp(&self.node.0))
                    .then_with(|| other.parent.cmp(&self.parent))
                    .then_with(|| other.via.cmp(&self.via))
            }
        }

        // A small bounded reservation avoids repeated growth for common queries
        // without allocating in proportion to the arena or an untrusted limit.
        let capacity = q.max_results.min(self.node_count()).min(64);
        let mut results = Vec::with_capacity(capacity);
        // Popped scores never increase. A prior route dominates this route iff
        // it used no more hops. Record the smallest expanded depth per node,
        // allowing later shorter routes to expand without emitting duplicates.
        let mut expanded_depth: HashMap<NodeId, u32> = HashMap::with_capacity(capacity);
        let mut paths: Vec<PathLink> = Vec::with_capacity(capacity);
        let mut heap: BinaryHeap<QueueEntry> = BinaryHeap::new();

        let mut push_seed = |seed| {
            if self.is_node_deleted(seed) {
                return;
            }
            heap.push(QueueEntry {
                node: seed,
                cumulative_strength: 1.0,
                depth: 0,
                parent: None,
                via: None,
            });
        };
        if q.seeds.is_empty() {
            for i in 0..self.node_count() {
                push_seed(NodeId(i as u32));
            }
        } else {
            for &seed in &q.seeds {
                push_seed(seed);
            }
        }

        while let Some(entry) = heap.pop() {
            let previous = expanded_depth.get(&entry.node).copied();
            if previous.is_some_and(|depth| depth <= entry.depth) {
                continue;
            }
            expanded_depth.insert(entry.node, entry.depth);
            let tail = entry.via.map(|edge| {
                let index = paths.len();
                paths.push(PathLink {
                    parent: entry.parent,
                    edge,
                });
                index
            });
            if previous.is_none() {
                let mut path = Vec::with_capacity(entry.depth as usize);
                let mut cursor = tail;
                while let Some(index) = cursor {
                    path.push(paths[index].edge);
                    cursor = paths[index].parent;
                }
                path.reverse();
                results.push(QueryResult {
                    node_id: entry.node,
                    path,
                    cumulative_strength: entry.cumulative_strength,
                    depth: entry.depth,
                });
                if results.len() >= q.max_results {
                    break;
                }
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
                if !edge.strength.is_finite() || edge.strength < q.min_strength {
                    continue;
                }
                // Detailed property values are checked only after mask pruning.
                if !self.edge_passes_filters(edge, &q.property_filters) {
                    continue;
                }

                let target = edge.target;
                if self.is_node_deleted(target)
                    || expanded_depth
                        .get(&target)
                        .is_some_and(|&depth| depth <= entry.depth + 1)
                {
                    continue;
                }

                let w = match q.traversal_weight_property {
                    Some(tag) => edge
                        .properties
                        .iter()
                        .find(|(t, _)| *t == tag)
                        .and_then(|(_, v)| match v {
                            PropertyValue::Float(f) => Some(*f),
                            PropertyValue::Int(i) => Some(*i as f64),
                            _ => None,
                        })
                        .unwrap_or(edge.strength),
                    None => edge.strength,
                };
                if !w.is_finite() || !(0.0..=1.0).contains(&w) {
                    continue;
                }
                heap.push(QueueEntry {
                    node: target,
                    cumulative_strength: entry.cumulative_strength * w,
                    depth: entry.depth + 1,
                    parent: tail,
                    via: Some(edge_id),
                });
            }
        }

        // The heap already emits nonincreasing strengths, satisfying both
        // sort_by_strength=true and the unspecified ordering when it is false.
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
                    match edge
                        .properties
                        .iter()
                        .find(|(t, _)| t == tag)
                        .map(|(_, v)| v)
                    {
                        Some(val) if val == expected => {}
                        _ => return false,
                    }
                }
                PropertyFilter::Range { min, max } => {
                    match edge
                        .properties
                        .iter()
                        .find(|(t, _)| t == tag)
                        .map(|(_, v)| v)
                    {
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

#[cfg(test)]
mod weight_property_tests {
    use crate::graph::KnowledgeGraph;
    use crate::properties::PropertyValue;

    #[test]
    fn weight_by_property_selects_higher_confidence_path() {
        let mut g = KnowledgeGraph::new();
        let tag = g.properties.register("confidence");

        let start = g.add_node("start", None, None);
        let hi = g.add_node("hi", None, None); // high confidence path
        let lo = g.add_node("lo", None, None); // low confidence path

        // Both edges have equal `strength = 0.5`, but different `confidence` properties.
        g.add_edge(start, hi, 0.5, vec![(tag, PropertyValue::Float(0.9))]);
        g.add_edge(start, lo, 0.5, vec![(tag, PropertyValue::Float(0.1))]);

        let results = g
            .find()
            .from_node(start)
            .weight_by_property("confidence")
            .depth(1)
            .limit(10)
            .execute();

        // `hi` must appear before `lo` because its confidence weight is higher.
        let hi_pos = results.iter().position(|r| r.node_id == hi).unwrap();
        let lo_pos = results.iter().position(|r| r.node_id == lo).unwrap();
        assert!(
            hi_pos < lo_pos,
            "hi-confidence node must rank before lo-confidence node"
        );
    }

    #[test]
    fn weight_by_property_falls_back_when_absent() {
        let mut g = KnowledgeGraph::new();
        let _tag = g.properties.register("confidence");

        let start = g.add_node("start", None, None);
        let dest = g.add_node("dest", None, None);
        // Edge has NO confidence property — fallback to edge.strength
        g.add_edge(start, dest, 0.7, vec![]);

        let results = g
            .find()
            .from_node(start)
            .weight_by_property("confidence")
            .depth(1)
            .limit(5)
            .execute();

        // `dest` must still be reachable
        assert!(results.iter().any(|r| r.node_id == dest));
        let dest_result = results.iter().find(|r| r.node_id == dest).unwrap();
        // cumulative_strength should equal edge.strength (the fallback)
        assert!((dest_result.cumulative_strength - 0.7).abs() < 1e-10);
    }

    #[test]
    fn weight_by_property_unknown_name_is_noop() {
        let mut g = KnowledgeGraph::new();
        let start = g.add_node("start", None, None);
        let dest = g.add_node("dest", None, None);
        g.add_edge(start, dest, 0.6, vec![]);

        // "nonexistent" is not in the registry — should silently ignore
        let results = g
            .find()
            .from_node(start)
            .weight_by_property("nonexistent")
            .depth(1)
            .limit(5)
            .execute();

        assert!(results.iter().any(|r| r.node_id == dest));
    }
}

#[cfg(test)]
mod relation_neighbor_tests {
    use crate::graph::KnowledgeGraph;
    use crate::properties::PropertyValue;

    #[test]
    fn neighbors_by_relation_returns_matching_edges_only() {
        let mut g = KnowledgeGraph::new();
        let tag = g.properties.register("rel");

        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        let c = g.add_node("c", None, None);

        g.add_edge(
            a,
            b,
            0.8,
            vec![(tag, PropertyValue::Label("supports".into()))],
        );
        g.add_edge(
            a,
            c,
            0.7,
            vec![(tag, PropertyValue::Label("opposes".into()))],
        );

        let results = g.neighbors_by_relation(a, "rel", PropertyValue::Label("supports".into()));

        assert_eq!(results.len(), 1);
        assert_eq!(results[0].0, b);
        assert!((results[0].1 - 0.8).abs() < 1e-10);
    }

    #[test]
    fn predecessors_by_relation_returns_incoming_only() {
        let mut g = KnowledgeGraph::new();
        let tag = g.properties.register("kind");

        let src = g.add_node("src", None, None);
        let dst = g.add_node("dst", None, None);
        let other = g.add_node("other", None, None);

        g.add_edge(src, dst, 0.9, vec![(tag, PropertyValue::Label("x".into()))]);
        g.add_edge(
            other,
            dst,
            0.5,
            vec![(tag, PropertyValue::Label("y".into()))],
        );

        let preds = g.predecessors_by_relation(dst, "kind", PropertyValue::Label("x".into()));

        assert_eq!(preds.len(), 1);
        assert_eq!(preds[0].0, src);
    }

    #[test]
    fn neighbors_by_relation_empty_on_unknown_property() {
        let mut g = KnowledgeGraph::new();
        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        g.add_edge(a, b, 1.0, vec![]);

        let results = g.neighbors_by_relation(a, "nonexistent", PropertyValue::Bool(true));
        assert!(results.is_empty());
    }

    #[test]
    fn neighbors_by_relation_empty_on_deleted_node() {
        let mut g = KnowledgeGraph::new();
        let tag = g.properties.register("r");
        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        g.add_edge(a, b, 0.5, vec![(tag, PropertyValue::Bool(true))]);
        g.remove_node(a);

        let results = g.neighbors_by_relation(a, "r", PropertyValue::Bool(true));
        assert!(results.is_empty());
    }

    #[test]
    fn traverse_by_relation_follows_matching_chain() {
        let mut g = KnowledgeGraph::new();
        let tag = g.properties.register("rel_type");

        // Chain: A → B → C → D (all "supports")
        // Branch: A → E ("opposes") — must be excluded
        let a = g.add_node("A", None, None);
        let b = g.add_node("B", None, None);
        let c = g.add_node("C", None, None);
        let d = g.add_node("D", None, None);
        let e = g.add_node("E", None, None);

        let supports = PropertyValue::Label("supports".into());
        let opposes = PropertyValue::Label("opposes".into());

        g.add_edge(a, b, 0.9, vec![(tag, supports.clone())]);
        g.add_edge(b, c, 0.9, vec![(tag, supports.clone())]);
        g.add_edge(c, d, 0.9, vec![(tag, supports.clone())]);
        g.add_edge(a, e, 0.9, vec![(tag, opposes)]);

        let results = g.traverse_by_relation(a, "rel_type", supports, 4, 10);

        let ids: Vec<_> = results.iter().map(|r| r.node_id).collect();
        assert!(ids.contains(&b), "B must be reachable");
        assert!(ids.contains(&c), "C must be reachable");
        assert!(ids.contains(&d), "D must be reachable");
        assert!(!ids.contains(&e), "E must be excluded (different rel_type)");
    }

    #[test]
    fn traverse_by_relation_respects_depth_limit() {
        let mut g = KnowledgeGraph::new();
        let tag = g.properties.register("t");
        let val = PropertyValue::Bool(true);

        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        let c = g.add_node("c", None, None);
        let d = g.add_node("d", None, None);

        g.add_edge(a, b, 0.9, vec![(tag, val.clone())]);
        g.add_edge(b, c, 0.9, vec![(tag, val.clone())]);
        g.add_edge(c, d, 0.9, vec![(tag, val.clone())]);

        // depth = 1 → only B should be reachable, not C or D
        let results = g.traverse_by_relation(a, "t", val, 1, 10);
        let ids: Vec<_> = results.iter().map(|r| r.node_id).collect();

        assert!(ids.contains(&b));
        assert!(
            !ids.contains(&c),
            "C is 2 hops away, must be excluded with depth=1"
        );
        assert!(
            !ids.contains(&d),
            "D is 3 hops away, must be excluded with depth=1"
        );
    }
}
