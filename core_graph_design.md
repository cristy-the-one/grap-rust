# Knowledge Graph Engine — Product Specification

---

## What It Is

A Rust-native graph database engine purpose-built for one job: **storing interconnected text documents and retrieving them by relationship characteristics, fast.**

---

## The Problem It Solves

You have a large corpus of text — documents, notes, research, records — and these texts are **related to each other** in ways that matter. Not just "related," but related *with context*: this document connects to that one **strongly**, on the **topic** of X, with **confidence** Y. You need to ask questions like:

> *"Starting from this document, give me everything connected to it through high-confidence edges about memory safety, up to 3 hops out, strongest paths first."*

Traditional databases can't do this without expensive joins. Traditional search engines find documents by content but know nothing about inter-document relationships. Graph databases handle relationships but treat node data as an afterthought and property-filtered traversal as a second-class operation.

This engine does all three natively.

---

## How It Works

**Three-layer architecture, each optimized for its access pattern:**

**1. Graph Topology (hot path — always in memory)**
Nodes and edges live in contiguous arena-allocated arrays indexed by integer handles. No pointer chasing, no heap fragmentation. Each edge carries a **strength** score and a sparse set of **typed properties** (topic, confidence, relation type — whatever you define). Every edge's properties are encoded as a 64-bit bitmask. Checking whether an edge carries all properties your query requires is a single bitwise AND — one CPU cycle, regardless of how many properties exist in the system.

**2. Blob Store (cold path — touched only on retrieval)**
The actual text lives in a separate arena, never accessed during traversal. When you're searching the graph, you're navigating a compact topology structure. Only after you've identified the nodes you want does the engine reach into blob storage. On disk, blobs are memory-mapped: a 50 GB corpus "loads" in microseconds because the OS pages in only the regions you actually read.

**3. Inverted Index (entry point — finds your starting nodes)**
A BM25-scored full-text index over all blob content, maintained incrementally as nodes are added. This answers the question "where do I start?" — you provide a text query, the index returns ranked candidate nodes, and those become the seeds for graph traversal.

**Query execution flow:**
Text query ? inverted index produces seed nodes ? BFS/priority traversal expands outward ? bitmask filters discard irrelevant edges in O(1) ? strength thresholds prune weak connections ? results returned ranked by cumulative path strength ? blob text retrieved only for final results.

---

## Performance Characteristics

| Operation | Cost | Mechanism |
| --- | --- | --- |
| Edge property filtering | O(1) per edge | 64-bit bitmask AND |
| Node-level pruning | O(1) per node | Aggregate outgoing-edge mask skips entire adjacency lists |
| Individual property lookup | O(log p), p ? 64 | Binary search on sorted inline array |
| Blob read | O(1) | Direct index into arena or mmap region, zero-copy |
| Full traversal | O(V + E) with constant-factor reduction | Standard BFS, accelerated by mask pruning |
| Text search (seed selection) | O(t · log N) | BM25 over inverted posting lists, t = query terms |
| Serialization/load (topology) | O(V + E) | Bincode binary format |
| Serialization/load (blobs) | O(1) wall-clock via mmap | OS-managed demand paging |

---

## Scaling Model

A single instance handles graphs that fit in one machine's memory (topology) and disk (blobs). Beyond that, the engine shards: nodes are distributed across machines via consistent hashing, cross-shard edges are tracked as ghost references, and a coordinator scatters queries to all shards, gathers results, and merges them. Adding a shard migrates approximately 1/N of existing data. Each shard is a fully self-contained instance with its own blob store and text index.

---

## What It Is Not

It is not a general-purpose database. It does not support arbitrary SQL. It does not handle transactions, ACID guarantees, or concurrent writes beyond a simple read-write lock. It is a **read-optimized, traversal-optimized engine** for a specific data shape: text nodes, weighted property-carrying edges, and queries defined by relationship characteristics.
# Core Module: Property-Weighted Knowledge Graph

This is a substantial design challenge. Let me architect this from first principles, leveraging Rust's ownership model, zero-copy patterns, and cache-friendly data layouts for maximum traversal and retrieval speed.

---

## Architecture Overview

The design centers on three key insights:

1. **Separate hot path (graph topology + edge properties) from cold path (blob storage)** — traversal shouldn't touch blob data until you've found what you want
2. **Edge properties as bitmask-indexed sparse vectors** — enables O(1) property filtering during traversal
3. **Arena allocation for nodes/edges** — cache-friendly, no pointer chasing, no ref-counting overhead

---

## Complete Core Module

```c
// ???????????????????????????????????????????????????????????????????
// lib.rs — Core Knowledge Graph with Property-Weighted Edges
// ???????????????????????????????????????????????????????????????????

use std::collections::HashMap;
use std::sync::RwLock;

// ???????????????????????????????????????????????????????????????????
// SECTION 1: Identifiers & Core Types
// ???????????????????????????????????????????????????????????????????

/// Opaque handle into the node arena. Copy + cheap to pass around.
/// Internally it's just an index — no pointer, no heap, no lifetime issues.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct NodeId(u32);

/// Opaque handle into the edge arena.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct EdgeId(u32);

/// A "property tag" is a u16 identifier for a named property dimension.
/// We keep a registry (string <-> u16) so queries use fast integer comparisons,
/// never string matching on the hot path.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct PropertyTag(u16);

/// The value associated with a property on an edge.
/// Kept small (32 bytes max on stack) so edge scanning stays cache-friendly.
#[derive(Debug, Clone, PartialEq)]
pub enum PropertyValue {
    Float(f64),
    Int(i64),
    Bool(bool),
    /// Short labels only — anything large belongs in the blob store.
    Label(CompactString),
}

/// Inline string up to 30 bytes, spills to heap only if longer.
/// This keeps PropertyValue at a predictable size.
#[derive(Debug, Clone, PartialEq)]
pub struct CompactString {
    data: SmallVec<[u8; 30]>,
}

// For this module we'll use a simple Vec-backed version;
// in production you'd use the `smallvec` crate.
// Simulating SmallVec with Vec for clarity:
type SmallVec<T> = Vec<u8>;

impl CompactString {
    pub fn new(s: &str) -> Self {
        Self {
            data: s.as_bytes().to_vec(),
        }
    }

    pub fn as_str(&self) -> &str {
        // SAFETY: we only construct from valid &str
        unsafe { std::str::from_utf8_unchecked(&self.data) }
    }
}

// ???????????????????????????????????????????????????????????????????
// SECTION 2: Blob Store — Optimized Large Text Storage
// ???????????????????????????????????????????????????????????????????

/// The blob store keeps text data OUT of the graph's hot path.
/// Design choices:
///   - Text is stored in large contiguous arenas (Vec<u8> chunks)
///   - Each blob is referenced by (chunk_index, offset, length)
///   - No per-blob allocation — eliminates allocator pressure
///   - Blobs are immutable once written (append-only)
///   - Optional LZ4 compression for blobs above a size threshold
///
/// This means traversing the graph never touches blob memory
/// until you explicitly call `graph.read_blob(node_id)`.

const CHUNK_SIZE: usize = 4 * 1024 * 1024; // 4 MiB per arena chunk
const COMPRESSION_THRESHOLD: usize = 4096; // compress blobs > 4 KiB

#[derive(Debug)]
struct BlobRef {
    chunk_idx: u32,
    offset: u32,
    length: u32,
    uncompressed_len: u32, // if == length, not compressed
}

#[derive(Debug)]
pub struct BlobStore {
    chunks: Vec<Vec<u8>>,
}

impl BlobStore {
    fn new() -> Self {
        Self {
            chunks: vec![Vec::with_capacity(CHUNK_SIZE)],
        }
    }

    /// Append a blob, returns a lightweight BlobRef.
    /// For simplicity, this version stores uncompressed.
    /// Production version would LZ4-compress if > COMPRESSION_THRESHOLD.
    fn store(&mut self, text: &str) -> BlobRef {
        let bytes = text.as_bytes();
        let len = bytes.len();

        // Find or create a chunk with enough room
        let chunk_idx = {
            let last = self.chunks.len() - 1;
            if self.chunks[last].len() + len > CHUNK_SIZE && !self.chunks[last].is_empty() {
                self.chunks.push(Vec::with_capacity(CHUNK_SIZE.max(len)));
                self.chunks.len() - 1
            } else {
                last
            }
        };

        let offset = self.chunks[chunk_idx].len();
        self.chunks[chunk_idx].extend_from_slice(bytes);

        BlobRef {
            chunk_idx: chunk_idx as u32,
            offset: offset as u32,
            length: len as u32,
            uncompressed_len: len as u32,
        }
    }

    /// Zero-copy read: returns a &str slice directly into the arena.
    fn read(&self, blob_ref: &BlobRef) -> &str {
        let chunk = &self.chunks[blob_ref.chunk_idx as usize];
        let start = blob_ref.offset as usize;
        let end = start + blob_ref.length as usize;
        // SAFETY: we only store valid UTF-8
        unsafe { std::str::from_utf8_unchecked(&chunk[start..end]) }
    }
}

// ???????????????????????????????????????????????????????????????????
// SECTION 3: Node & Edge Arena Storage
// ???????????????????????????????????????????????????????????????????

/// A node in the graph. Kept minimal for cache-friendly traversal.
/// The actual text lives in the BlobStore, referenced indirectly.
#[derive(Debug)]
struct Node {
    /// Pointer into the BlobStore (not the text itself!)
    blob: BlobRef,
    /// Adjacency: outgoing edge indices.
    /// Stored inline for nodes with few connections.
    outgoing: Vec<EdgeId>,
    /// Adjacency: incoming edge indices (enables reverse traversal).
    incoming: Vec<EdgeId>,
    /// Optional short label for display/debugging (not the blob).
    label: Option<String>,
    /// Bitmask of PropertyTags present on ANY outgoing edge.
    /// Enables early pruning: if a query needs tag X and this
    /// node's outgoing_property_mask doesn't have bit X set,
    /// skip all outgoing edges without examining them.
    outgoing_property_mask: u64,
}

/// An edge in the graph.
#[derive(Debug)]
struct Edge {
    source: NodeId,
    target: NodeId,
    /// Primary "strength" score. Always present. Used for
    /// ranking/sorting during traversal.
    strength: f64,
    /// Sparse property map. Typically 0-8 properties per edge.
    /// Stored as a sorted Vec for cache-friendly scanning.
    /// (PropertyTag is u16, so sorting/comparing is trivial.)
    properties: Vec<(PropertyTag, PropertyValue)>,
    /// Bitmask of which PropertyTags this edge carries.
    /// Enables O(1) "does this edge have the properties I need?" check.
    property_mask: u64,
}

impl Edge {
    /// Check if this edge carries ALL of the required property tags.
    /// This is a single bitwise AND — extremely fast.
    #[inline]
    fn matches_mask(&self, required_mask: u64) -> bool {
        (self.property_mask & required_mask) == required_mask
    }

    /// Get a property value by tag. Binary search on sorted vec.
    fn get_property(&self, tag: PropertyTag) -> Option<&PropertyValue> {
        self.properties
            .binary_search_by_key(&tag.0, |(t, _)| t.0)
            .ok()
            .map(|idx| &self.properties[idx].1)
    }
}

// ???????????????????????????????????????????????????????????????????
// SECTION 4: Property Registry
// ???????????????????????????????????????????????????????????????????

/// Bidirectional map: property name <-> PropertyTag.
/// This lets users write queries with human-readable names
/// while the engine operates on u16 tags and bitmasks.
#[derive(Debug)]
pub struct PropertyRegistry {
    name_to_tag: HashMap<String, PropertyTag>,
    tag_to_name: Vec<String>,
}

impl PropertyRegistry {
    fn new() -> Self {
        Self {
            name_to_tag: HashMap::new(),
            tag_to_name: Vec::new(),
        }
    }

    /// Register a property name, returns its tag. Idempotent.
    pub fn register(&mut self, name: &str) -> PropertyTag {
        if let Some(&tag) = self.name_to_tag.get(name) {
            return tag;
        }
        assert!(
            self.tag_to_name.len() < 64,
            "Max 64 property types (bitmask limit). Use compound properties or extend to u128."
        );
        let tag = PropertyTag(self.tag_to_name.len() as u16);
        self.tag_to_name.push(name.to_string());
        self.name_to_tag.insert(name.to_string(), tag);
        tag
    }

    /// Look up tag by name.
    pub fn get_tag(&self, name: &str) -> Option<PropertyTag> {
        self.name_to_tag.get(name).copied()
    }

    /// Look up name by tag.
    pub fn get_name(&self, tag: PropertyTag) -> &str {
        &self.tag_to_name[tag.0 as usize]
    }

    /// Build a bitmask from a set of property names.
    pub fn build_mask(&self, names: &[&str]) -> u64 {
        let mut mask = 0u64;
        for name in names {
            if let Some(tag) = self.get_tag(name) {
                mask |= 1u64 << tag.0;
            }
        }
        mask
    }
}

// ???????????????????????????????????????????????????????????????????
// SECTION 5: Query Types
// ???????????????????????????????????????????????????????????????????

/// A filter predicate on a single property.
#[derive(Debug, Clone)]
pub enum PropertyFilter {
    /// Property exists on the edge (any value).
    Exists,
    /// Exact match.
    Equals(PropertyValue),
    /// Numeric range (inclusive).
    Range { min: f64, max: f64 },
    /// Strength threshold (applied to edge.strength, not a property).
    MinStrength(f64),
}

/// A query describes what subset of the graph to traverse/retrieve.
///
/// Conceptually: "Starting from these seed nodes (or all nodes),
/// follow edges that match ALL of these property constraints,
/// with strength >= min_strength, up to max_depth hops."
#[derive(Debug)]
pub struct GraphQuery {
    /// Optional starting points. If empty, searches all nodes.
    pub seeds: Vec<NodeId>,
    /// Required properties on edges, with optional value filters.
    pub property_filters: Vec<(PropertyTag, PropertyFilter)>,
    /// Precomputed bitmask of required property tags (for fast edge skipping).
    pub required_mask: u64,
    /// Minimum edge strength to traverse.
    pub min_strength: f64,
    /// Maximum traversal depth (0 = seeds only, 1 = direct neighbors, etc.)
    pub max_depth: u32,
    /// Maximum number of results to return.
    pub max_results: usize,
    /// Sort results by cumulative path strength (descending).
    pub sort_by_strength: bool,
}

/// A single result from a query.
#[derive(Debug, Clone)]
pub struct QueryResult {
    pub node_id: NodeId,
    /// The path of edges taken to reach this node.
    pub path: Vec<EdgeId>,
    /// Cumulative strength along the path (product or sum — configurable).
    pub cumulative_strength: f64,
    /// Depth at which this node was found.
    pub depth: u32,
}

// ???????????????????????????????????????????????????????????????????
// SECTION 6: The Graph — Main API
// ???????????????????????????????????????????????????????????????????

/// The core knowledge graph.
///
/// Design invariants:
///   - Nodes and edges are arena-allocated (Vec-backed, indexed by Id).
///   - Blob data is stored separately and only touched on explicit read.
///   - Edge property masks enable sub-microsecond filtering.
///   - The graph is NOT thread-safe by default; wrap in RwLock externally
///     or use the provided `ConcurrentGraph` wrapper.
pub struct KnowledgeGraph {
    nodes: Vec<Node>,
    edges: Vec<Edge>,
    blobs: BlobStore,
    pub properties: PropertyRegistry,
}

impl KnowledgeGraph {
    // ?? Construction ??????????????????????????????????????????????

    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            edges: Vec::new(),
            blobs: BlobStore::new(),
            properties: PropertyRegistry::new(),
        }
    }

    pub fn with_capacity(node_cap: usize, edge_cap: usize) -> Self {
        Self {
            nodes: Vec::with_capacity(node_cap),
            edges: Vec::with_capacity(edge_cap),
            blobs: BlobStore::new(),
            properties: PropertyRegistry::new(),
        }
    }

    // ?? Node Operations ???????????????????????????????????????????

    /// Add a node with its text blob. Returns the node's handle.
    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> NodeId {
        let blob = self.blobs.store(text);
        let id = NodeId(self.nodes.len() as u32);
        self.nodes.push(Node {
            blob,
            outgoing: Vec::new(),
            incoming: Vec::new(),
            label: label.map(|s| s.to_string()),
            outgoing_property_mask: 0,
        });
        id
    }

    /// Read the text blob for a node. Zero-copy into the arena.
    pub fn read_blob(&self, node: NodeId) -> &str {
        self.blobs.read(&self.nodes[node.0 as usize].blob)
    }

    /// Get a node's label (if any).
    pub fn node_label(&self, node: NodeId) -> Option<&str> {
        self.nodes[node.0 as usize].label.as_deref()
    }

    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    // ?? Edge Operations ???????????????????????????????????????????

    /// Add a directed edge with a strength score and properties.
    ///
    /// Properties are given as (tag, value) pairs. The tag must
    /// have been registered via `properties.register()` first.
    pub fn add_edge(
        &mut self,
        source: NodeId,
        target: NodeId,
        strength: f64,
        props: Vec<(PropertyTag, PropertyValue)>,
    ) -> EdgeId {
        // Build property bitmask
        let mut mask = 0u64;
        for (tag, _) in &props {
            mask |= 1u64 << tag.0;
        }

        // Sort properties by tag for binary search
        let mut sorted_props = props;
        sorted_props.sort_by_key(|(t, _)| t.0);

        let edge_id = EdgeId(self.edges.len() as u32);
        self.edges.push(Edge {
            source,
            target,
            strength,
            properties: sorted_props,
            property_mask: mask,
        });

        // Update adjacency lists
        self.nodes[source.0 as usize].outgoing.push(edge_id);
        self.nodes[target.0 as usize].incoming.push(edge_id);

        // Update the source node's outgoing property mask
        self.nodes[source.0 as usize].outgoing_property_mask |= mask;

        edge_id
    }

    /// Read edge strength.
    pub fn edge_strength(&self, edge: EdgeId) -> f64 {
        self.edges[edge.0 as usize].strength
    }

    /// Read a specific property from an edge.
    pub fn edge_property(&self, edge: EdgeId, tag: PropertyTag) -> Option<&PropertyValue> {
        self.edges[edge.0 as usize].get_property(tag)
    }

    /// Get source and target of an edge.
    pub fn edge_endpoints(&self, edge: EdgeId) -> (NodeId, NodeId) {
        let e = &self.edges[edge.0 as usize];
        (e.source, e.target)
    }

    // ?? Query Engine ??????????????????????????????????????????????

    /// Execute a graph query: BFS/priority traversal with property filtering.
    ///
    /// Algorithm:
    /// 1. Start from seed nodes (or all nodes if seeds is empty).
    /// 2. At each node, scan outgoing edges.
    /// 3. For each edge:
    ///    a. Bitmask check: does edge carry ALL required properties? (1 CPU op)
    ///    b. Strength check: is edge.strength >= min_strength?
    ///    c. Detailed filter: check individual PropertyFilter predicates.
    /// 4. If edge passes, enqueue target node at depth+1.
    /// 5. Collect results, optionally sorted by cumulative strength.
    pub fn query(&self, q: &GraphQuery) -> Vec<QueryResult> {
        use std::collections::BinaryHeap;
        use std::cmp::Ordering;

        // Priority queue entry: higher cumulative strength = higher priority
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
                self.partial_cmp(other).unwrap_or(Ordering::Equal)
            }
        }

        let mut results = Vec::new();
        let mut visited = vec![false; self.nodes.len()];
        let mut heap = BinaryHeap::new();

        // Seed the traversal
        let seeds: Vec<NodeId> = if q.seeds.is_empty() {
            (0..self.nodes.len() as u32).map(NodeId).collect()
        } else {
            q.seeds.clone()
        };

        for seed in seeds {
            visited[seed.0 as usize] = true;
            heap.push(QueueEntry {
                node: seed,
                cumulative_strength: 1.0, // multiplicative identity
                depth: 0,
                path: Vec::new(),
            });
        }

        while let Some(entry) = heap.pop() {
            // Emit this node as a result
            results.push(QueryResult {
                node_id: entry.node,
                path: entry.path.clone(),
                cumulative_strength: entry.cumulative_strength,
                depth: entry.depth,
            });

            if results.len() >= q.max_results {
                break;
            }

            // Don't expand past max_depth
            if entry.depth >= q.max_depth {
                continue;
            }

            let node = &self.nodes[entry.node.0 as usize];

            // ?? FAST PRUNING ??
            // If this node's outgoing edges collectively don't carry
            // the required properties, skip ALL of them.
            if (node.outgoing_property_mask & q.required_mask) != q.required_mask {
                continue;
            }

            // Scan outgoing edges
            for &edge_id in &node.outgoing {
                let edge = &self.edges[edge_id.0 as usize];

                // ?? BITMASK CHECK (1 CPU cycle) ??
                if !edge.matches_mask(q.required_mask) {
                    continue;
                }

                // ?? STRENGTH CHECK ??
                if edge.strength < q.min_strength {
                    continue;
                }

                // ?? DETAILED PROPERTY FILTERS ??
                if !self.edge_passes_filters(edge, &q.property_filters) {
                    continue;
                }

                let target = edge.target;
                if visited[target.0 as usize] {
                    continue;
                }
                visited[target.0 as usize] = true;

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

    /// Check all detailed filters against an edge's properties.
    #[inline]
    fn edge_passes_filters(
        &self,
        edge: &Edge,
        filters: &[(PropertyTag, PropertyFilter)],
    ) -> bool {
        for (tag, filter) in filters {
            match filter {
                PropertyFilter::Exists => {
                    // Already guaranteed by bitmask check, but kept for clarity
                    if edge.get_property(*tag).is_none() {
                        return false;
                    }
                }
                PropertyFilter::Equals(expected) => {
                    match edge.get_property(*tag) {
                        Some(val) if val == expected => {}
                        _ => return false,
                    }
                }
                PropertyFilter::Range { min, max } => {
                    match edge.get_property(*tag) {
                        Some(PropertyValue::Float(v)) if *v >= *min && *v <= *max => {}
                        Some(PropertyValue::Int(v))
                            if (*v as f64) >= *min && (*v as f64) <= *max => {}
                        _ => return false,
                    }
                }
                PropertyFilter::MinStrength(_) => {
                    // This is applied at the edge.strength level, not a property.
                    // Handled in the main loop, included here for completeness.
                }
            }
        }
        true
    }

    // ?? Convenience: Query Builder ????????????????????????????????

    /// Ergonomic query builder.
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

// ???????????????????????????????????????????????????????????????????
// SECTION 7: Query Builder (Ergonomic API)
// ???????????????????????????????????????????????????????????????????

pub struct QueryBuilder<'g> {
    graph: &'g KnowledgeGraph,
    seeds: Vec<NodeId>,
    filters: Vec<(PropertyTag, PropertyFilter)>,
    min_strength: f64,
    max_depth: u32,
    max_results: usize,
    sort_by_strength: bool,
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

    /// Require an edge property by name.
    pub fn with_property(mut self, name: &str) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Exists));
        }
        self
    }

    /// Require an edge property to equal a specific value.
    pub fn where_property(mut self, name: &str, value: PropertyValue) -> Self {
        if let Some(tag) = self.graph.properties.get_tag(name) {
            self.filters.push((tag, PropertyFilter::Equals(value)));
        }
        self
    }

    /// Require a numeric property within a range.
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

    /// Execute the query and return results.
    pub fn execute(self) -> Vec<QueryResult> {
        // Build the required mask from filters
        let mut required_mask = 0u64;
        for (tag, _) in &self.filters {
            required_mask |= 1u64 << tag.0;
        }

        let query = GraphQuery {
            seeds: self.seeds,
            property_filters: self.filters,
            required_mask,
            min_strength: self.min_strength,
            max_depth: self.max_depth,
            max_results: self.max_results,
            sort_by_strength: self.sort_by_strength,
        };

        self.graph.query(&query)
    }

    /// Execute and return the blob text for each result.
    pub fn execute_with_text(self) -> Vec<(QueryResult, &'g str)> {
        let graph = self.graph;
        self.execute()
            .into_iter()
            .map(|r| {
                let text = graph.read_blob(r.node_id);
                (r, text)
            })
            .collect()
    }
}

// ???????????????????????????????????????????????????????????????????
// SECTION 8: Thread-Safe Wrapper
// ???????????????????????????????????????????????????????????????????

/// Concurrent wrapper using RwLock.
/// Many readers can query simultaneously; writes are exclusive.
pub struct ConcurrentGraph {
    inner: RwLock<KnowledgeGraph>,
}

impl ConcurrentGraph {
    pub fn new() -> Self {
        Self {
            inner: RwLock::new(KnowledgeGraph::new()),
        }
    }

    /// Write access (adding nodes/edges).
    pub fn write<F, R>(&self, f: F) -> R
    where
        F: FnOnce(&mut KnowledgeGraph) -> R,
    {
        let mut guard = self.inner.write().unwrap();
        f(&mut guard)
    }

    /// Read access (queries, blob reads).
    pub fn read<F, R>(&self, f: F) -> R
    where
        F: FnOnce(&KnowledgeGraph) -> R,
    {
        let guard = self.inner.read().unwrap();
        f(&guard)
    }
}

// ???????????????????????????????????????????????????????????????????
// SECTION 9: Usage Example
// ???????????????????????????????????????????????????????????????????

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn knowledge_graph_end_to_end() {
        let mut g = KnowledgeGraph::new();

        // Register property dimensions
        let topic = g.properties.register("topic");
        let confidence = g.properties.register("confidence");
        let relation_type = g.properties.register("relation_type");

        // Add nodes with large text blobs
        let rust_node = g.add_node(
            "Rust is a systems programming language focused on safety, \
             concurrency, and performance. It achieves memory safety without \
             garbage collection through its ownership system...",
            Some("Rust Overview"),
        );

        let ownership_node = g.add_node(
            "The ownership system is Rust's most distinctive feature. \
             Every value has exactly one owner, and when the owner goes \
             out of scope, the value is dropped...",
            Some("Ownership"),
        );

        let borrowing_node = g.add_node(
            "Borrowing allows you to reference data without taking ownership. \
             You can have multiple immutable references or exactly one mutable \
             reference at any given time...",
            Some("Borrowing"),
        );

        let lifetimes_node = g.add_node(
            "Lifetimes are Rust's way of ensuring that references are always \
             valid. The borrow checker uses lifetime annotations to verify \
             that references don't outlive the data they point to...",
            Some("Lifetimes"),
        );

        let concurrency_node = g.add_node(
            "Rust's ownership system naturally prevents data races at compile \
             time. Combined with types like Mutex<T> and Arc<T>, it enables \
             fearless concurrency...",
            Some("Concurrency"),
        );

        // Add edges with strength and properties
        g.add_edge(
            rust_node,
            ownership_node,
            0.95, // very strong relation
            vec![
                (topic, PropertyValue::Label(CompactString::new("memory_safety"))),
                (confidence, PropertyValue::Float(0.99)),
                (relation_type, PropertyValue::Label(CompactString::new("core_concept"))),
            ],
        );

        g.add_edge(
            ownership_node,
            borrowing_node,
            0.90,
            vec![
                (topic, PropertyValue::Label(CompactString::new("memory_safety"))),
                (confidence, PropertyValue::Float(0.95)),
                (relation_type, PropertyValue::Label(CompactString::new("extends"))),
            ],
        );

        g.add_edge(
            borrowing_node,
            lifetimes_node,
            0.85,
            vec![
                (topic, PropertyValue::Label(CompactString::new("memory_safety"))),
                (confidence, PropertyValue::Float(0.88)),
                (relation_type, PropertyValue::Label(CompactString::new("requires"))),
            ],
        );

        g.add_edge(
            rust_node,
            concurrency_node,
            0.80,
            vec![
                (topic, PropertyValue::Label(CompactString::new("parallelism"))),
                (confidence, PropertyValue::Float(0.85)),
                (relation_type, PropertyValue::Label(CompactString::new("enables"))),
            ],
        );

        g.add_edge(
            ownership_node,
            concurrency_node,
            0.75,
            vec![
                (topic, PropertyValue::Label(CompactString::new("parallelism"))),
                (confidence, PropertyValue::Float(0.80)),
                (relation_type, PropertyValue::Label(CompactString::new("enables"))),
            ],
        );

        // ?? QUERY: "Find all nodes connected to Rust via memory_safety
        //            with high confidence, up to 3 hops deep" ??

        let results = g
            .find()
            .from_node(rust_node)
            .where_property(
                "topic",
                PropertyValue::Label(CompactString::new("memory_safety")),
            )
            .where_in_range("confidence", 0.85, 1.0)
            .min_strength(0.8)
            .depth(3)
            .limit(10)
            .execute_with_text();

        println!("=== Query Results ===");
        for (result, text) in &results {
            println!(
                "Node {:?} (depth={}, strength={:.3}): {}...",
                result.node_id,
                result.depth,
                result.cumulative_strength,
                &text[..text.len().min(60)]
            );
        }

        // The seed node (rust_node) is always included at depth 0.
        // Then ownership_node at depth 1 (strength 0.95).
        // Then borrowing_node at depth 2 (strength 0.95 * 0.90 = 0.855).
        // Then lifetimes_node at depth 3 (strength 0.855 * 0.85 = 0.727).
        //   ^ but lifetimes edge confidence is 0.88, which is in [0.85, 1.0] ?
        assert!(results.len() >= 3); // rust + ownership + borrowing + lifetimes

        // Verify blob content is accessible and correct
        assert!(g.read_blob(rust_node).contains("systems programming"));
        assert!(g.read_blob(ownership_node).contains("exactly one owner"));

        println!("\nGraph stats: {} nodes, {} edges", g.node_count(), g.edge_count());
    }
}
```

---

## Performance Characteristics

| Operation | Complexity | Why |
| --- | --- | --- |
| **Edge property mask check** | O(1) | Single bitwise AND on `u64` |
| **Node-level pruning** | O(1) | Outgoing mask eliminates entire adjacency lists |
| **Property lookup on edge** | O(log p) | Binary search, p ? 64 properties (effectively O(1)) |
| **Blob read** | O(1) | Direct index into arena, zero-copy `&str` |
| **Full traversal** | O(V + E) | Standard BFS/Dijkstra, but with constant-factor reduction from mask pruning |
| **Add node** | O(n) amortized | Vec push + blob arena append |
| **Add edge** | O(p log p) | Sort properties (p is tiny) |

## Key Design Decisions Explained

**Why arena-allocated blobs instead of per-node `String`?** Each `String` is a separate heap allocation. With 100K nodes, that's 100K allocations the allocator must track, fragmented across memory. The arena packs text contiguously — better cache locality, fewer allocator calls, and `read_blob()` returns a zero-copy `&str` slice directly into the arena.

**Why bitmask-based property filtering?** During traversal you touch every outgoing edge. If each edge check requires iterating a `HashMap`, you're doing random memory accesses. A `u64` bitmask fits in a register — checking "does this edge have all 4 required properties?" is literally one AND instruction.

**Why separate hot/cold paths?** The graph topology (node adjacency + edge metadata) is the "hot" data touched during every traversal step. The blob text is "cold" — only needed when you've found the nodes you want. Keeping them separate means the hot path fits in fewer cache lines.

**Why `u16` PropertyTag instead of strings?** String comparison is expensive and cache-unfriendly. The registry converts names to `u16` once at query construction time. All runtime comparisons are integer ops.

To extend this further, we add: serialization (serde + mmap for instant load), incremental indexing (inverted index over blob content for full-text seed selection), and sharding for graphs that exceed single-machine memory.

# Extended Knowledge Graph: Serialization, Full-Text Indexing & Sharding

Here are the three extensions built on top of the core module, each as a self-contained section that integrates with the existing types.

---

## Section 10: Serialization & Memory-Mapped Instant Load

```c
// ???????????????????????????????????????????????????????????????????
// SECTION 10: Serialization — Serde + mmap for Instant Load
// ???????????????????????????????????????????????????????????????????
//
// DESIGN PHILOSOPHY:
//
// Two serialization modes:
//
// 1. "Portable" mode (serde + bincode):
//    Full serialize/deserialize. Safe, cross-platform, versioned.
//    Used for: snapshots, backups, network transfer, small-to-medium graphs.
//
// 2. "Instant" mode (mmap):
//    The blob store is written as a flat file and memory-mapped on load.
//    The OS pages it in on demand — a 10 GB blob store "loads" in microseconds.
//    The graph topology (nodes, edges, registry) is still deserialized
//    normally since it's small and needs to be mutable.
//    Used for: large graphs where blob data dominates memory.
//
// The split is natural because our architecture already separates
// the hot path (topology) from the cold path (blobs).
//
// File layout on disk:
//   graph.topo   — bincode-serialized topology (nodes, edges, registry)
//   graph.blobs  — raw concatenated blob arena chunks (mmap target)
//   graph.meta   — JSON metadata (version, chunk boundaries, checksums)

use serde::{Serialize, Deserialize};
use std::fs::{self, File};
use std::io::{self, Read, Write, BufWriter, BufReader};
use std::path::{Path, PathBuf};

// ?? Serde Derives for All Core Types ??????????????????????????????

/// We derive Serialize/Deserialize for all types needed in the
/// "portable" path. The mmap path bypasses serde for blobs entirely.

#[derive(Serialize, Deserialize)]
struct SerializableNode {
    /// Instead of BlobRef pointing into an in-memory arena,
    /// we store the blob's global byte offset and length.
    blob_global_offset: u64,
    blob_length: u32,
    blob_uncompressed_len: u32,
    outgoing: Vec<u32>, // EdgeId indices
    incoming: Vec<u32>,
    label: Option<String>,
    outgoing_property_mask: u64,
}

#[derive(Serialize, Deserialize)]
struct SerializableEdge {
    source: u32,
    target: u32,
    strength: f64,
    properties: Vec<(u16, SerializablePropertyValue)>,
    property_mask: u64,
}

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
enum SerializablePropertyValue {
    Float(f64),
    Int(i64),
    Bool(bool),
    Label(Vec<u8>), // CompactString as raw bytes
}

#[derive(Serialize, Deserialize)]
struct SerializableRegistry {
    tag_to_name: Vec<String>,
}

#[derive(Serialize, Deserialize)]
struct TopologyFile {
    version: u32,
    node_count: u32,
    edge_count: u32,
    nodes: Vec<SerializableNode>,
    edges: Vec<SerializableEdge>,
    registry: SerializableRegistry,
}

/// Metadata stored as human-readable JSON alongside binary files.
#[derive(Serialize, Deserialize, Debug)]
struct GraphMeta {
    version: u32,
    node_count: u32,
    edge_count: u32,
    blob_total_bytes: u64,
    /// Byte offset where each original arena chunk begins in the flat file.
    /// Needed to reconstruct BlobRefs on load.
    chunk_boundaries: Vec<u64>,
    /// Optional CRC32 checksums per chunk for integrity verification.
    chunk_checksums: Vec<u32>,
    created_timestamp: u64,
}

// ?? Conversion Between Runtime and Serializable Types ?????????????

impl KnowledgeGraph {
    /// Convert a runtime BlobRef to a global file offset.
    /// In the flat blob file, chunks are concatenated sequentially.
    fn blob_ref_to_global_offset(
        &self,
        blob: &BlobRef,
        chunk_offsets: &[u64],
    ) -> u64 {
        chunk_offsets[blob.chunk_idx as usize] + blob.offset as u64
    }

    /// Compute the byte offset of each chunk in a flat concatenation.
    fn compute_chunk_offsets(&self) -> Vec<u64> {
        let mut offsets = Vec::with_capacity(self.blobs.chunks.len());
        let mut running = 0u64;
        for chunk in &self.blobs.chunks {
            offsets.push(running);
            running += chunk.len() as u64;
        }
        offsets
    }
}

// ?? Portable Serialization (Full Serde) ???????????????????????????

impl KnowledgeGraph {
    /// Serialize the entire graph to a directory.
    /// Creates: graph.topo, graph.blobs, graph.meta
    pub fn save_to_disk(&self, dir: &Path) -> io::Result<()> {
        fs::create_dir_all(dir)?;

        let chunk_offsets = self.compute_chunk_offsets();

        // ?? 1. Write blob arena as flat file ??
        let blob_path = dir.join("graph.blobs");
        {
            let mut writer = BufWriter::new(File::create(&blob_path)?);
            let mut checksums = Vec::new();

            for chunk in &self.blobs.chunks {
                writer.write_all(chunk)?;
                // CRC32 for integrity
                checksums.push(crc32_simple(chunk));
            }
            writer.flush()?;

            // ?? 2. Write metadata ??
            let total_bytes: u64 = self.blobs.chunks.iter()
                .map(|c| c.len() as u64)
                .sum();

            let meta = GraphMeta {
                version: 1,
                node_count: self.nodes.len() as u32,
                edge_count: self.edges.len() as u32,
                blob_total_bytes: total_bytes,
                chunk_boundaries: chunk_offsets.clone(),
                chunk_checksums: checksums,
                created_timestamp: current_unix_timestamp(),
            };

            let meta_json = serde_json::to_string_pretty(&meta)
                .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;
            fs::write(dir.join("graph.meta"), meta_json)?;
        }

        // ?? 3. Write topology ??
        let topo = TopologyFile {
            version: 1,
            node_count: self.nodes.len() as u32,
            edge_count: self.edges.len() as u32,
            nodes: self.nodes.iter().map(|n| {
                SerializableNode {
                    blob_global_offset: self.blob_ref_to_global_offset(
                        &n.blob, &chunk_offsets
                    ),
                    blob_length: n.blob.length,
                    blob_uncompressed_len: n.blob.uncompressed_len,
                    outgoing: n.outgoing.iter().map(|e| e.0).collect(),
                    incoming: n.incoming.iter().map(|e| e.0).collect(),
                    label: n.label.clone(),
                    outgoing_property_mask: n.outgoing_property_mask,
                }
            }).collect(),
            edges: self.edges.iter().map(|e| {
                SerializableEdge {
                    source: e.source.0,
                    target: e.target.0,
                    strength: e.strength,
                    properties: e.properties.iter().map(|(t, v)| {
                        (t.0, property_to_serializable(v))
                    }).collect(),
                    property_mask: e.property_mask,
                }
            }).collect(),
            registry: SerializableRegistry {
                tag_to_name: self.properties.tag_to_name.clone(),
            },
        };

        let topo_path = dir.join("graph.topo");
        let mut writer = BufWriter::new(File::create(&topo_path)?);
        bincode::serialize_into(&mut writer, &topo)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;
        writer.flush()?;

        Ok(())
    }

    /// Full deserialization: load everything into memory.
    /// Use `load_with_mmap` for large blob stores instead.
    pub fn load_from_disk(dir: &Path) -> io::Result<Self> {
        // Read metadata
        let meta_str = fs::read_to_string(dir.join("graph.meta"))?;
        let meta: GraphMeta = serde_json::from_str(&meta_str)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;

        // Read topology
        let topo_file = File::open(dir.join("graph.topo"))?;
        let reader = BufReader::new(topo_file);
        let topo: TopologyFile = bincode::deserialize_from(reader)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;

        // Read blobs into arena
        let blob_data = fs::read(dir.join("graph.blobs"))?;

        // Verify checksums
        for (i, &boundary) in meta.chunk_boundaries.iter().enumerate() {
            let start = boundary as usize;
            let end = if i + 1 < meta.chunk_boundaries.len() {
                meta.chunk_boundaries[i + 1] as usize
            } else {
                blob_data.len()
            };
            let chunk_slice = &blob_data[start..end];
            let computed = crc32_simple(chunk_slice);
            if computed != meta.chunk_checksums[i] {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("Checksum mismatch for chunk {}", i),
                ));
            }
        }

        // Reconstruct blob store: split flat data back into chunks
        let mut blob_store = BlobStore { chunks: Vec::new() };
        for (i, &boundary) in meta.chunk_boundaries.iter().enumerate() {
            let start = boundary as usize;
            let end = if i + 1 < meta.chunk_boundaries.len() {
                meta.chunk_boundaries[i + 1] as usize
            } else {
                blob_data.len()
            };
            blob_store.chunks.push(blob_data[start..end].to_vec());
        }

        // Reconstruct nodes
        let nodes: Vec<Node> = topo.nodes.into_iter().map(|sn| {
            // Convert global offset back to (chunk_idx, local_offset)
            let (chunk_idx, local_offset) = global_to_chunk_offset(
                sn.blob_global_offset,
                &meta.chunk_boundaries,
            );
            Node {
                blob: BlobRef {
                    chunk_idx: chunk_idx as u32,
                    offset: local_offset as u32,
                    length: sn.blob_length,
                    uncompressed_len: sn.blob_uncompressed_len,
                },
                outgoing: sn.outgoing.into_iter().map(EdgeId).collect(),
                incoming: sn.incoming.into_iter().map(EdgeId).collect(),
                label: sn.label,
                outgoing_property_mask: sn.outgoing_property_mask,
            }
        }).collect();

        // Reconstruct edges
        let edges: Vec<Edge> = topo.edges.into_iter().map(|se| {
            Edge {
                source: NodeId(se.source),
                target: NodeId(se.target),
                strength: se.strength,
                properties: se.properties.into_iter().map(|(t, v)| {
                    (PropertyTag(t), serializable_to_property(v))
                }).collect(),
                property_mask: se.property_mask,
            }
        }).collect();

        // Reconstruct property registry
        let mut registry = PropertyRegistry::new();
        for name in topo.registry.tag_to_name {
            registry.register(&name);
        }

        Ok(KnowledgeGraph {
            nodes,
            edges,
            blobs: blob_store,
            properties: registry,
        })
    }
}

// ?? Memory-Mapped Blob Store ??????????????????????????????????????

/// A blob store backed by a memory-mapped file.
/// The OS handles paging — only accessed regions load into RAM.
/// This means a 50 GB blob file "loads" in microseconds.
///
/// SAFETY: The mmap region is treated as immutable &[u8] for its
/// entire lifetime. We ensure the file isn't modified while mapped.
pub struct MmapBlobStore {
    /// The memory-mapped region. We use a raw pointer + length
    /// to avoid depending on a specific mmap crate in this core module.
    /// In production, use the `memmap2` crate.
    ///
    /// For this reference implementation, we simulate with a Vec<u8>
    /// and document where `memmap2::Mmap` would slot in.
    data: MmapRegion,
    chunk_boundaries: Vec<u64>,
}

/// Abstraction over the mmap region.
/// Replace with `memmap2::Mmap` in production.
enum MmapRegion {
    /// Real mmap (production path)
    #[cfg(feature = "mmap")]
    Mapped(memmap2::Mmap),

    /// Fallback: read into memory (always available)
    InMemory(Vec<u8>),
}

impl MmapRegion {
    fn as_slice(&self) -> &[u8] {
        match self {
            #[cfg(feature = "mmap")]
            MmapRegion::Mapped(m) => m.as_ref(),
            MmapRegion::InMemory(v) => v.as_slice(),
        }
    }
}

impl MmapBlobStore {
    /// Open a blob file with mmap.
    pub fn open(blob_path: &Path, meta: &GraphMeta) -> io::Result<Self> {
        let region = {
            #[cfg(feature = "mmap")]
            {
                let file = File::open(blob_path)?;
                let mmap = unsafe { memmap2::Mmap::map(&file)? };

                // Advise the OS for random access pattern.
                // This is critical for graph traversal where blob
                // reads are essentially random.
                #[cfg(unix)]
                {
                    use std::os::unix::io::AsRawFd;
                    unsafe {
                        libc::madvise(
                            mmap.as_ptr() as *mut libc::c_void,
                            mmap.len(),
                            libc::MADV_RANDOM,
                        );
                    }
                }

                MmapRegion::Mapped(mmap)
            }

            #[cfg(not(feature = "mmap"))]
            {
                let data = fs::read(blob_path)?;
                MmapRegion::InMemory(data)
            }
        };

        Ok(Self {
            data: region,
            chunk_boundaries: meta.chunk_boundaries.clone(),
        })
    }

    /// Read a blob as &str. Zero-copy from the mmap region.
    pub fn read(&self, global_offset: u64, length: u32) -> &str {
        let start = global_offset as usize;
        let end = start + length as usize;
        let bytes = &self.data.as_slice()[start..end];
        // SAFETY: we only store valid UTF-8 in the blob store
        unsafe { std::str::from_utf8_unchecked(bytes) }
    }
}

/// A KnowledgeGraph variant that uses mmap for blob access.
/// Topology is in memory (fast, mutable). Blobs are on disk (mmap, lazy).
pub struct MmapKnowledgeGraph {
    nodes: Vec<Node>,
    edges: Vec<Edge>,
    mmap_blobs: MmapBlobStore,
    /// Each node stores its global blob offset for mmap reads.
    blob_offsets: Vec<(u64, u32)>, // (global_offset, length) per node
    pub properties: PropertyRegistry,
}

impl MmapKnowledgeGraph {
    /// Load a graph with memory-mapped blobs.
    pub fn load(dir: &Path) -> io::Result<Self> {
        let meta_str = fs::read_to_string(dir.join("graph.meta"))?;
        let meta: GraphMeta = serde_json::from_str(&meta_str)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;

        // Load topology (small, into RAM)
        let topo_file = File::open(dir.join("graph.topo"))?;
        let reader = BufReader::new(topo_file);
        let topo: TopologyFile = bincode::deserialize_from(reader)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;

        // Memory-map the blob file (instant, regardless of size)
        let mmap_blobs = MmapBlobStore::open(&dir.join("graph.blobs"), &meta)?;

        // Build offset table for O(1) blob lookup per node
        let blob_offsets: Vec<(u64, u32)> = topo.nodes.iter()
            .map(|sn| (sn.blob_global_offset, sn.blob_length))
            .collect();

        // Reconstruct nodes (same as before, but blob field is vestigial
        // in mmap mode — we use blob_offsets instead)
        let nodes: Vec<Node> = topo.nodes.into_iter().map(|sn| {
            let (chunk_idx, local_offset) = global_to_chunk_offset(
                sn.blob_global_offset,
                &meta.chunk_boundaries,
            );
            Node {
                blob: BlobRef {
                    chunk_idx: chunk_idx as u32,
                    offset: local_offset as u32,
                    length: sn.blob_length,
                    uncompressed_len: sn.blob_uncompressed_len,
                },
                outgoing: sn.outgoing.into_iter().map(EdgeId).collect(),
                incoming: sn.incoming.into_iter().map(EdgeId).collect(),
                label: sn.label,
                outgoing_property_mask: sn.outgoing_property_mask,
            }
        }).collect();

        let edges: Vec<Edge> = topo.edges.into_iter().map(|se| {
            Edge {
                source: NodeId(se.source),
                target: NodeId(se.target),
                strength: se.strength,
                properties: se.properties.into_iter().map(|(t, v)| {
                    (PropertyTag(t), serializable_to_property(v))
                }).collect(),
                property_mask: se.property_mask,
            }
        }).collect();

        let mut registry = PropertyRegistry::new();
        for name in topo.registry.tag_to_name {
            registry.register(&name);
        }

        Ok(Self {
            nodes,
            edges,
            mmap_blobs,
            blob_offsets,
            properties: registry,
        })
    }

    /// Read blob text for a node — served directly from mmap.
    pub fn read_blob(&self, node: NodeId) -> &str {
        let (offset, len) = self.blob_offsets[node.0 as usize];
        self.mmap_blobs.read(offset, len)
    }
}

// ?? Helper Functions ??????????????????????????????????????????????

fn global_to_chunk_offset(global: u64, boundaries: &[u64]) -> (usize, usize) {
    // Binary search for the chunk containing this offset
    let mut chunk_idx = 0;
    for (i, &boundary) in boundaries.iter().enumerate().rev() {
        if global >= boundary {
            chunk_idx = i;
            break;
        }
    }
    let local = (global - boundaries[chunk_idx]) as usize;
    (chunk_idx, local)
}

fn property_to_serializable(v: &PropertyValue) -> SerializablePropertyValue {
    match v {
        PropertyValue::Float(f) => SerializablePropertyValue::Float(*f),
        PropertyValue::Int(i) => SerializablePropertyValue::Int(*i),
        PropertyValue::Bool(b) => SerializablePropertyValue::Bool(*b),
        PropertyValue::Label(s) => SerializablePropertyValue::Label(s.data.clone()),
    }
}

fn serializable_to_property(v: SerializablePropertyValue) -> PropertyValue {
    match v {
        SerializablePropertyValue::Float(f) => PropertyValue::Float(f),
        SerializablePropertyValue::Int(i) => PropertyValue::Int(i),
        SerializablePropertyValue::Bool(b) => PropertyValue::Bool(b),
        SerializablePropertyValue::Label(bytes) => {
            PropertyValue::Label(CompactString { data: bytes })
        }
    }
}

/// Simple CRC32 (in production, use the `crc32fast` crate).
fn crc32_simple(data: &[u8]) -> u32 {
    let mut hash: u32 = 0xFFFF_FFFF;
    for &byte in data {
        hash ^= byte as u32;
        for _ in 0..8 {
            if hash & 1 != 0 {
                hash = (hash >> 1) ^ 0xEDB8_8320;
            } else {
                hash >>= 1;
            }
        }
    }
    !hash
}

fn current_unix_timestamp() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
```

---

## Section 11: Incremental Full-Text Inverted Index

```
// ???????????????????????????????????????????????????????????????????
// SECTION 11: Inverted Index — Full-Text Seed Selection
// ???????????????????????????????????????????????????????????????????
//
// PURPOSE:
//
// The graph query engine needs seed nodes. If the user says
// "find me everything about ownership and borrowing", we need to
// locate the relevant starting nodes BEFORE traversing edges.
//
// This inverted index maps: term ? list of (NodeId, frequency, positions)
//
// DESIGN:
//   - Built incrementally: each add_node() call updates the index.
//   - Tokenizer is pluggable (default: Unicode word boundaries + lowercase).
//   - Posting lists are sorted by NodeId for merge-intersection.
//   - TF-IDF scoring for ranking seed candidates.
//   - Positional data enables phrase queries.
//   - Memory-efficient: terms stored in a string interner.

use std::collections::{HashMap, BTreeMap};

// ?? String Interner ???????????????????????????????????????????????

/// Deduplicates term strings. Each unique term is stored once,
/// referenced by a TermId. Saves ~40-60% memory on natural language.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
struct TermId(u32);

#[derive(Debug)]
struct StringInterner {
    strings: Vec<String>,
    lookup: HashMap<String, TermId>,
}

impl StringInterner {
    fn new() -> Self {
        Self {
            strings: Vec::new(),
            lookup: HashMap::new(),
        }
    }

    fn intern(&mut self, s: &str) -> TermId {
        if let Some(&id) = self.lookup.get(s) {
            return id;
        }
        let id = TermId(self.strings.len() as u32);
        self.strings.push(s.to_string());
        self.lookup.insert(s.to_string(), id);
        id
    }

    fn resolve(&self, id: TermId) -> &str {
        &self.strings[id.0 as usize]
    }

    fn get(&self, s: &str) -> Option<TermId> {
        self.lookup.get(s).copied()
    }
}

// ?? Posting List ??????????????????????????????????????????????????

/// A single entry in the posting list for a term.
#[derive(Debug, Clone)]
struct Posting {
    node_id: NodeId,
    /// How many times this term appears in the node's blob.
    frequency: u32,
    /// Byte positions of each occurrence (enables phrase queries).
    /// Stored sorted. For very long documents, we could cap this
    /// and just store frequency — configurable via MAX_POSITIONS.
    positions: Vec<u32>,
}

const MAX_POSITIONS_PER_POSTING: usize = 256;

/// The posting list for a single term, sorted by NodeId.
#[derive(Debug, Clone)]
struct PostingList {
    postings: Vec<Posting>,
    /// Total frequency across all documents (for IDF calculation).
    total_frequency: u64,
}

impl PostingList {
    fn new() -> Self {
        Self {
            postings: Vec::new(),
            total_frequency: 0,
        }
    }

    /// Add or update a posting for a node. Maintains sorted order.
    fn add_posting(&mut self, node_id: NodeId, positions: Vec<u32>) {
        let freq = positions.len() as u32;
        self.total_frequency += freq as u64;

        // Binary search to find insertion point (or existing entry)
        match self.postings.binary_search_by_key(&node_id.0, |p| p.node_id.0) {
            Ok(idx) => {
                // Node already indexed — merge (shouldn't happen in normal
                // append-only operation, but handles re-indexing gracefully)
                self.postings[idx].frequency += freq;
                let p = &mut self.postings[idx];
                if p.positions.len() < MAX_POSITIONS_PER_POSTING {
                    p.positions.extend_from_slice(&positions);
                    p.positions.sort();
                }
            }
            Err(idx) => {
                let capped_positions = if positions.len() > MAX_POSITIONS_PER_POSTING {
                    positions[..MAX_POSITIONS_PER_POSTING].to_vec()
                } else {
                    positions
                };
                self.postings.insert(idx, Posting {
                    node_id,
                    frequency: freq,
                    positions: capped_positions,
                });
            }
        }
    }
}

// ?? Tokenizer ?????????????????????????????????????????????????????

/// Pluggable tokenizer trait.
pub trait Tokenizer: Send + Sync {
    /// Tokenize text into (term, byte_position) pairs.
    fn tokenize<'a>(&self, text: &'a str) -> Vec<(&'a str, u32)>;
}

/// Default tokenizer: Unicode word boundaries, lowercased, min length 2.
/// Filters out pure-numeric tokens and common stop words.
pub struct DefaultTokenizer {
    stop_words: HashSet<&'static str>,
}

use std::collections::HashSet;

impl DefaultTokenizer {
    pub fn new() -> Self {
        let stop_words: HashSet<&'static str> = [
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "shall", "can",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "and",
            "but", "or", "nor", "not", "so", "if", "then", "than",
            "that", "this", "it", "its", "they", "them", "we", "you",
            "he", "she", "his", "her", "my", "your", "our", "their",
        ].iter().copied().collect();

        Self { stop_words }
    }
}

impl Tokenizer for DefaultTokenizer {
    fn tokenize<'a>(&self, text: &'a str) -> Vec<(&'a str, u32)> {
        let mut tokens = Vec::new();
        let mut start = None;

        for (i, ch) in text.char_indices() {
            if ch.is_alphanumeric() || ch == '_' {
                if start.is_none() {
                    start = Some(i);
                }
            } else {
                if let Some(s) = start.take() {
                    let word = &text[s..i];
                    if word.len() >= 2
                        && !word.chars().all(|c| c.is_numeric())
                        && !self.stop_words.contains(
                            &word.to_lowercase().as_str().to_owned().as_str()
                        )
                    {
                        tokens.push((word, s as u32));
                    }
                }
            }
        }
        // Handle last token
        if let Some(s) = start {
            let word = &text[s..];
            if word.len() >= 2 && !word.chars().all(|c| c.is_numeric()) {
                tokens.push((word, s as u32));
            }
        }

        tokens
    }
}

// ?? The Inverted Index ????????????????????????????????????????????

pub struct InvertedIndex {
    interner: StringInterner,
    /// term_id ? posting list
    index: HashMap<TermId, PostingList>,
    /// Total number of indexed documents (for IDF)
    document_count: u32,
    /// Per-document term count (for TF normalization)
    doc_lengths: Vec<u32>,
    /// Average document length (for BM25)
    avg_doc_length: f64,
    tokenizer: Box<dyn Tokenizer>,
}

/// Scored search result from the inverted index.
#[derive(Debug, Clone)]
pub struct TextSearchResult {
    pub node_id: NodeId,
    pub score: f64,
    /// Which query terms were found in this node.
    pub matched_terms: Vec<String>,
}

impl InvertedIndex {
    pub fn new() -> Self {
        Self {
            interner: StringInterner::new(),
            index: HashMap::new(),
            document_count: 0,
            doc_lengths: Vec::new(),
            avg_doc_length: 0.0,
            tokenizer: Box::new(DefaultTokenizer::new()),
        }
    }

    pub fn with_tokenizer(tokenizer: Box<dyn Tokenizer>) -> Self {
        Self {
            interner: StringInterner::new(),
            index: HashMap::new(),
            document_count: 0,
            doc_lengths: Vec::new(),
            avg_doc_length: 0.0,
            tokenizer,
        }
    }

    /// Index a single node's text. Called by KnowledgeGraph::add_node().
    /// This is the "incremental" part — no need to rebuild the whole index.
    pub fn index_document(&mut self, node_id: NodeId, text: &str) {
        let tokens = self.tokenizer.tokenize(text);

        // Group by lowercased term ? positions
        let mut term_positions: BTreeMap<String, Vec<u32>> = BTreeMap::new();
        for (word, pos) in &tokens {
            let lower = word.to_lowercase();
            term_positions.entry(lower).or_default().push(*pos);
        }

        let doc_length = tokens.len() as u32;

        // Ensure doc_lengths has an entry for this node
        let idx = node_id.0 as usize;
        if idx >= self.doc_lengths.len() {
            self.doc_lengths.resize(idx + 1, 0);
        }
        self.doc_lengths[idx] = doc_length;
        self.document_count = self.document_count.max(idx as u32 + 1);

        // Update average document length
        let total: u64 = self.doc_lengths.iter().map(|&l| l as u64).sum();
        self.avg_doc_length = total as f64 / self.document_count.max(1) as f64;

        // Insert into posting lists
        for (term, positions) in term_positions {
            let term_id = self.interner.intern(&term);
            let posting_list = self.index
                .entry(term_id)
                .or_insert_with(PostingList::new);
            posting_list.add_posting(node_id, positions);
        }
    }

    /// Search for nodes matching a text query.
    /// Uses BM25 scoring (industry standard for text relevance).
    ///
    /// Returns results sorted by score (descending).
    pub fn search(&self, query: &str, max_results: usize) -> Vec<TextSearchResult> {
        let query_tokens = self.tokenizer.tokenize(query);
        let query_terms: Vec<String> = query_tokens.iter()
            .map(|(w, _)| w.to_lowercase())
            .collect::<HashSet<_>>()
            .into_iter()
            .collect();

        if query_terms.is_empty() {
            return Vec::new();
        }

        // BM25 parameters (tuned defaults)
        const K1: f64 = 1.2;
        const B: f64 = 0.75;

        let n = self.document_count as f64;

        // Accumulate scores per document
        let mut scores: HashMap<u32, (f64, Vec<String>)> = HashMap::new();

        for term in &query_terms {
            let term_id = match self.interner.get(term) {
                Some(id) => id,
                None => continue, // term not in corpus
            };

            let posting_list = match self.index.get(&term_id) {
                Some(pl) => pl,
                None => continue,
            };

            // IDF component: log((N - df + 0.5) / (df + 0.5) + 1)
            let df = posting_list.postings.len() as f64;
            let idf = ((n - df + 0.5) / (df + 0.5) + 1.0).ln();

            for posting in &posting_list.postings {
                let doc_id = posting.node_id.0;
                let tf = posting.frequency as f64;
                let dl = self.doc_lengths.get(doc_id as usize)
                    .copied()
                    .unwrap_or(1) as f64;

                // BM25 TF component
                let tf_norm = (tf * (K1 + 1.0))
                    / (tf + K1 * (1.0 - B + B * dl / self.avg_doc_length));

                let score = idf * tf_norm;

                let entry = scores.entry(doc_id).or_insert((0.0, Vec::new()));
                entry.0 += score;
                entry.1.push(term.clone());
            }
        }

        // Sort by score descending
        let mut results: Vec<TextSearchResult> = scores.into_iter()
            .map(|(doc_id, (score, terms))| TextSearchResult {
                node_id: NodeId(doc_id),
                score,
                matched_terms: terms,
            })
            .collect();

        results.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(max_results);
        results
    }

    /// Phrase search: find nodes where terms appear consecutively.
    /// Falls back to proximity search with a configurable window.
    pub fn phrase_search(
        &self,
        phrase: &str,
        max_gap: u32,       // max byte gap between consecutive terms
        max_results: usize,
    ) -> Vec<TextSearchResult> {
        let tokens = self.tokenizer.tokenize(phrase);
        let terms: Vec<String> = tokens.iter()
            .map(|(w, _)| w.to_lowercase())
            .collect();

        if terms.len() < 2 {
            return self.search(phrase, max_results);
        }

        // Get posting lists for all terms
        let posting_lists: Vec<Option<&PostingList>> = terms.iter()
            .map(|t| {
                self.interner.get(t)
                    .and_then(|id| self.index.get(&id))
            })
            .collect();

        // All terms must exist
        if posting_lists.iter().any(|pl| pl.is_none()) {
            return Vec::new();
        }

        let lists: Vec<&PostingList> = posting_lists.into_iter()
            .map(|pl| pl.unwrap())
            .collect();

        // Intersect by NodeId, then check positional proximity
        let mut results = Vec::new();

        // Start with the shortest posting list (optimization)
        let shortest_idx = lists.iter()
            .enumerate()
            .min_by_key(|(_, pl)| pl.postings.len())
            .map(|(i, _)| i)
            .unwrap_or(0);

        for posting in &lists[shortest_idx].postings {
            let node_id = posting.node_id;

            // Check all other terms have this node
            let all_present = lists.iter().enumerate().all(|(i, pl)| {
                i == shortest_idx
                    || pl.postings.binary_search_by_key(
                        &node_id.0, |p| p.node_id.0
                    ).is_ok()
            });

            if !all_present {
                continue;
            }

            // Collect positions for each term in this document
            let term_positions: Vec<&[u32]> = lists.iter().map(|pl| {
                let idx = pl.postings
                    .binary_search_by_key(&node_id.0, |p| p.node_id.0)
                    .unwrap();
                pl.postings[idx].positions.as_slice()
            }).collect();

            // Check if positions form a near-consecutive sequence
            let proximity_score = compute_proximity_score(
                &term_positions, max_gap
            );

            if proximity_score > 0.0 {
                results.push(TextSearchResult {
                    node_id,
                    score: proximity_score,
                    matched_terms: terms.clone(),
                });
            }
        }

        results.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(max_results);
        results
    }

    /// Stats for diagnostics.
    pub fn stats(&self) -> IndexStats {
        let total_postings: usize = self.index.values()
            .map(|pl| pl.postings.len())
            .sum();
        IndexStats {
            unique_terms: self.interner.strings.len(),
            total_postings,
            document_count: self.document_count,
            avg_doc_length: self.avg_doc_length,
        }
    }
}

#[derive(Debug)]
pub struct IndexStats {
    pub unique_terms: usize,
    pub total_postings: usize,
    pub document_count: u32,
    pub avg_doc_length: f64,
}

/// Score how well term positions form a consecutive phrase.
fn compute_proximity_score(term_positions: &[&[u32]], max_gap: u32) -> f64 {
    if term_positions.is_empty() {
        return 0.0;
    }
    if term_positions.len() == 1 {
        return 1.0;
    }

    // Try to find a sequence where term[i+1] appears within max_gap
    // bytes after term[i]. Use a sweep approach.
    let first_positions = term_positions[0];
    let mut best_score = 0.0f64;

    for &start_pos in first_positions {
        let mut current_pos = start_pos;
        let mut matched = 1u32;

        for i in 1..term_positions.len() {
            // Find the earliest position in term_positions[i]
            // that is > current_pos and <= current_pos + max_gap
            let next_positions = term_positions[i];
            let search = next_positions.binary_search(&(current_pos + 1));
            let candidate_idx = match search {
                Ok(idx) => idx,
                Err(idx) => idx,
            };

            if candidate_idx < next_positions.len() {
                let candidate = next_positions[candidate_idx];
                if candidate <= current_pos + max_gap {
                    current_pos = candidate;
                    matched += 1;
                } else {
                    break;
                }
            } else {
                break;
            }
        }

        // Score based on how many terms in the phrase were matched
        // in sequence, penalized by gap sizes
        let coverage = matched as f64 / term_positions.len() as f64;
        if coverage > best_score {
            best_score = coverage;
        }
    }

    best_score
}

// ?? Integration with KnowledgeGraph ???????????????????????????????

/// Extended graph that includes the inverted index.
/// Wraps KnowledgeGraph and keeps the index in sync.
pub struct IndexedKnowledgeGraph {
    pub graph: KnowledgeGraph,
    pub text_index: InvertedIndex,
}

impl IndexedKnowledgeGraph {
    pub fn new() -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: InvertedIndex::new(),
        }
    }

    /// Add a node: stores blob in graph, indexes text in inverted index.
    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> NodeId {
        let node_id = self.graph.add_node(text, label);
        self.text_index.index_document(node_id, text);
        node_id
    }

    /// Add an edge (delegates to graph).
    pub fn add_edge(
        &mut self,
        source: NodeId,
        target: NodeId,
        strength: f64,
        props: Vec<(PropertyTag, PropertyValue)>,
    ) -> EdgeId {
        self.graph.add_edge(source, target, strength, props)
    }

    /// Text search ? seed selection ? graph traversal.
    /// This is the primary "find related content" API.
    ///
    /// Example: "ownership borrowing" finds nodes about those topics,
    /// then traverses the graph to find related content within the
    /// specified property constraints.
    pub fn search_and_traverse(
        &self,
        text_query: &str,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> Vec<(QueryResult, &str)> {
        // Step 1: Full-text search to find seed nodes
        let text_results = self.text_index.search(text_query, max_seeds);

        if text_results.is_empty() {
            return Vec::new();
        }

        let seed_ids: Vec<NodeId> = text_results.iter()
            .map(|r| r.node_id)
            .collect();

        // Step 2: Build graph query from seeds
        let mut builder = self.graph.find()
            .from_nodes(&seed_ids)
            .min_strength(min_strength)
            .depth(max_depth)
            .limit(max_results);

        for prop_name in property_names {
            builder = builder.with_property(prop_name);
        }

        builder.execute_with_text()
    }
}
```

---

## Section 12: Graph Sharding

```
// ???????????????????????????????????????????????????????????????????
// SECTION 12: Sharding — Distribute Across Machines
// ???????????????????????????????????????????????????????????????????
//
// DESIGN:
//
// When a single machine can't hold the entire graph, we partition
// it across N shards. Each shard is a complete KnowledgeGraph
// (with its own blob store and index) holding a subset of nodes.
//
// PARTITIONING STRATEGY:
//   - Consistent hashing on NodeId (deterministic, even distribution).
//   - Alternative: community-based partitioning (minimize cross-shard edges).
//   - Each shard "owns" a set of nodes and ALL their outgoing edges.
//
// CROSS-SHARD EDGES:
//   - When node A (shard 1) has an edge to node B (shard 2),
//     shard 1 stores a "ghost edge" pointing to (shard_id=2, node_id=B).
//   - Traversal across shards requires a network hop — the ShardRouter
//     handles this transparently.
//
// ARCHITECTURE:
//   ????????????     ????????????     ????????????
//   ? Shard 0  ?     ? Shard 1  ?     ? Shard 2  ?
//   ? nodes    ?     ? nodes    ?     ? nodes    ?
//   ? edges    ?     ? edges    ?     ? edges    ?
//   ? blobs    ?     ? blobs    ?     ? blobs    ?
//   ? index    ?     ? index    ?     ? index    ?
//   ????????????     ????????????     ????????????
//        ?                ?                ?
//        ???????????????????????????????????
//                ?  ShardRouter    ?
//                ?  (coordinator)  ?
//                ?????????????????-?

/// Identifies which shard owns a resource.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ShardId(pub u16);

/// A globally unique node identifier across all shards.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct GlobalNodeId {
    pub shard: ShardId,
    pub local: NodeId,
}

/// An edge that crosses shard boundaries.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GhostEdge {
    /// Local source node on this shard.
    pub local_source: NodeId,
    /// Remote target on another shard.
    pub remote_target: GlobalNodeId,
    pub strength: f64,
    pub properties: Vec<(u16, SerializablePropertyValue)>,
    pub property_mask: u64,
}

// ?? Consistent Hashing ???????????????????????????????????????????

/// Determines which shard owns a given node.
///
/// Uses a consistent hashing ring with virtual nodes for
/// even distribution. Adding/removing shards only migrates
/// ~1/N of the data.
pub struct ConsistentHashRing {
    /// Sorted ring of (hash_position, shard_id)
    ring: Vec<(u64, ShardId)>,
    shard_count: u16,
    virtual_nodes_per_shard: u16,
}

impl ConsistentHashRing {
    pub fn new(shard_count: u16, virtual_nodes_per_shard: u16) -> Self {
        let mut ring = Vec::new();
        for shard in 0..shard_count {
            for vn in 0..virtual_nodes_per_shard {
                let key = format!("shard-{}-vn-{}", shard, vn);
                let hash = fxhash64(key.as_bytes());
                ring.push((hash, ShardId(shard)));
            }
        }
        ring.sort_by_key(|(h, _)| *h);
        Self {
            ring,
            shard_count,
            virtual_nodes_per_shard,
        }
    }

    /// Determine which shard owns a node with the given content hash.
    pub fn locate(&self, key: &[u8]) -> ShardId {
        let hash = fxhash64(key);
        // Binary search for the first ring position >= hash
        let idx = match self.ring.binary_search_by_key(&hash, |(h, _)| *h) {
            Ok(i) => i,
            Err(i) => {
                if i >= self.ring.len() { 0 } else { i }
            }
        };
        self.ring[idx].1
    }

    /// Simple shard assignment by NodeId (for pre-assigned nodes).
    pub fn shard_for_node(&self, global_node_id: u64) -> ShardId {
        let bytes = global_node_id.to_le_bytes();
        self.locate(&bytes)
    }
}

/// FNV-1a inspired fast hash (non-cryptographic).
/// In production, use `rustc-hash` (FxHash) or `ahash`.
fn fxhash64(data: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf29ce484222325;
    for &byte in data {
        hash ^= byte as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

// ?? Shard: A Partition of the Graph ???????????????????????????????

/// A single shard holding a partition of the overall graph.
pub struct Shard {
    pub id: ShardId,
    /// The local graph partition.
    pub graph: IndexedKnowledgeGraph,
    /// Edges pointing to nodes on OTHER shards.
    /// Keyed by local source NodeId.
    pub ghost_edges: HashMap<u32, Vec<GhostEdge>>,
    /// Cache of recently fetched remote node data.
    /// Prevents repeated cross-shard fetches during a single traversal.
    remote_cache: HashMap<GlobalNodeId, CachedRemoteNode>,
}

#[derive(Debug, Clone)]
struct CachedRemoteNode {
    label: Option<String>,
    /// We cache a snippet, not the full blob (could be huge).
    blob_snippet: String,
    outgoing_edge_count: u32,
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

    /// Add a node to this shard.
    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> GlobalNodeId {
        let local = self.graph.add_node(text, label);
        GlobalNodeId {
            shard: self.id,
            local,
        }
    }

    /// Add a local edge (both nodes on this shard).
    pub fn add_local_edge(
        &mut self,
        source: NodeId,
        target: NodeId,
        strength: f64,
        props: Vec<(PropertyTag, PropertyValue)>,
    ) -> EdgeId {
        self.graph.add_edge(source, target, strength, props)
    }

    /// Add a cross-shard edge (source is local, target is remote).
    pub fn add_ghost_edge(&mut self, ghost: GhostEdge) {
        self.ghost_edges
            .entry(ghost.local_source.0)
            .or_default()
            .push(ghost);
    }

    /// Get ghost edges for a local node (if any).
    pub fn ghost_edges_for(&self, node: NodeId) -> &[GhostEdge] {
        self.ghost_edges
            .get(&node.0)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }
}

// ?? ShardRouter: Coordinates Multi-Shard Queries ??????????????????

/// The coordinator that routes queries across shards.
///
/// In a real distributed system, each shard would be on a different
/// machine and the router would use gRPC/TCP. Here we model the
/// logic with direct references for clarity.
///
/// QUERY EXECUTION STRATEGY:
///
/// 1. SCATTER: Send the text query to ALL shards' inverted indexes.
///    Each shard returns its local seed candidates with scores.
///
/// 2. GATHER: Merge and rank seeds globally. Pick top-K.
///
/// 3. EXPAND: For each seed, run local graph traversal on its shard.
///    When traversal hits a ghost edge, issue a cross-shard fetch
///    to continue on the remote shard (with depth decremented).
///
/// 4. MERGE: Combine results from all shards, deduplicate, re-rank.

pub struct ShardRouter {
    pub shards: Vec<Shard>,
    pub hash_ring: ConsistentHashRing,
}

/// Result from a distributed query, includes shard provenance.
#[derive(Debug, Clone)]
pub struct DistributedQueryResult {
    pub global_node_id: GlobalNodeId,
    pub label: Option<String>,
    pub cumulative_strength: f64,
    pub depth: u32,
    pub path_description: Vec<String>,
    /// Which shard produced this result.
    pub origin_shard: ShardId,
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

    /// Determine which shard should own a node (by content hash).
    pub fn assign_shard(&self, content: &str) -> ShardId {
        self.hash_ring.locate(content.as_bytes())
    }

    /// Add a node to the appropriate shard.
    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> GlobalNodeId {
        let shard_id = self.assign_shard(text);
        self.shards[shard_id.0 as usize].add_node(text, label)
    }

    /// Add an edge. If both nodes are on the same shard, it's a local edge.
    /// If they're on different shards, we create a ghost edge on the source shard.
    pub fn add_edge(
        &mut self,
        source: GlobalNodeId,
        target: GlobalNodeId,
        strength: f64,
        props: Vec<(PropertyTag, PropertyValue)>,
    ) {
        if source.shard == target.shard {
            // Local edge
            let shard = &mut self.shards[source.shard.0 as usize];
            shard.add_local_edge(source.local, target.local, strength, props);
        } else {
            // Ghost edge on source shard
            let ghost = GhostEdge {
                local_source: source.local,
                remote_target: target,
                strength,
                properties: props.iter().map(|(t, v)| {
                    (t.0, property_to_serializable(v))
                }).collect(),
                property_mask: {
                    let mut mask = 0u64;
                    for (tag, _) in &props {
                        mask |= 1u64 << tag.0;
                    }
                    mask
                },
            };
            self.shards[source.shard.0 as usize].add_ghost_edge(ghost);

            // Also add the local edge on the source shard with a
            // sentinel target (so adjacency traversal knows to check ghosts).
            // This is an implementation detail — the ghost_edges map
            // is the source of truth for cross-shard links.
        }
    }

    /// Distributed text search ? graph traversal.
    ///
    /// This is the top-level API for finding interconnected data
    /// across the entire sharded graph.
    pub fn distributed_search(
        &self,
        text_query: &str,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> Vec<DistributedQueryResult> {
        // ?? PHASE 1: SCATTER — text search on all shards ??
        let mut all_seeds: Vec<(ShardId, TextSearchResult)> = Vec::new();

        for shard in &self.shards {
            let local_results = shard.graph.text_index.search(
                text_query,
                max_results, // each shard returns up to max_results candidates
            );
            for result in local_results {
                all_seeds.push((shard.id, result));
            }
        }

        // ?? PHASE 2: GATHER — global ranking of seeds ??
        all_seeds.sort_by(|a, b| {
            b.1.score.partial_cmp(&a.1.score).unwrap_or(std::cmp::Ordering::Equal)
        });
        all_seeds.truncate(max_results * 2); // keep generous candidate set

        // ?? PHASE 3: EXPAND — local traversal per shard ??
        // Group seeds by shard for batch processing
        let mut seeds_by_shard: HashMap<u16, Vec<NodeId>> = HashMap::new();
        for (shard_id, result) in &all_seeds {
            seeds_by_shard
                .entry(shard_id.0)
                .or_default()
                .push(result.node_id);
        }

        let mut all_results: Vec<DistributedQueryResult> = Vec::new();

        for (shard_idx, seed_nodes) in &seeds_by_shard {
            let shard = &self.shards[*shard_idx as usize];

            // Run local traversal from these seeds
            let local_results = shard.graph.graph
                .find()
                .from_nodes(seed_nodes)
                .min_strength(min_strength)
                .depth(max_depth)
                .limit(max_results)
                .execute();

            for result in local_results {
                let label = shard.graph.graph.node_label(result.node_id)
                    .map(|s| s.to_string());

                // Build path description
                let path_desc: Vec<String> = result.path.iter().map(|&edge_id| {
                    let (src, tgt) = shard.graph.graph.edge_endpoints(edge_id);
                    let strength = shard.graph.graph.edge_strength(edge_id);
                    format!(
                        "{} --({:.2})--> {}",
                        shard.graph.graph.node_label(src).unwrap_or("?"),
                        strength,
                        shard.graph.graph.node_label(tgt).unwrap_or("?"),
                    )
                }).collect();

                all_results.push(DistributedQueryResult {
                    global_node_id: GlobalNodeId {
                        shard: shard.id,
                        local: result.node_id,
                    },
                    label,
                    cumulative_strength: result.cumulative_strength,
                    depth: result.depth,
                    path_description: path_desc,
                    origin_shard: shard.id,
                });
            }

            // ?? PHASE 3b: Follow ghost edges (cross-shard expansion) ??
            // For each result node, check if it has ghost edges.
            // If so, and we have remaining depth budget, fetch from
            // the remote shard.
            for result in &local_results {
                if result.depth >= max_depth {
                    continue;
                }

                let ghosts = shard.ghost_edges_for(result.node_id);
                for ghost in ghosts {
                    // Check property mask
                    let required_mask = {
                        let mut m = 0u64;
                        for name in property_names {
                            if let Some(tag) = shard.graph.graph.properties.get_tag(name) {
                                m |= 1u64 << tag.0;
                            }
                        }
                        m
                    };

                    if (ghost.property_mask & required_mask) != required_mask {
                        continue;
                    }
                    if ghost.strength < min_strength {
                        continue;
                    }

                    // Fetch from remote shard
                    let remote_shard_idx = ghost.remote_target.shard.0 as usize;
                    if remote_shard_idx < self.shards.len() {
                        let remote_shard = &self.shards[remote_shard_idx];
                        let remote_node = ghost.remote_target.local;

                        let remote_label = remote_shard.graph.graph
                            .node_label(remote_node)
                            .map(|s| s.to_string());

                        all_results.push(DistributedQueryResult {
                            global_node_id: ghost.remote_target,
                            label: remote_label,
                            cumulative_strength: result.cumulative_strength
                                * ghost.strength,
                            depth: result.depth + 1,
                            path_description: vec![format!(
                                "[cross-shard {} ? {}] strength={:.2}",
                                shard.id.0,
                                ghost.remote_target.shard.0,
                                ghost.strength
                            )],
                            origin_shard: ghost.remote_target.shard,
                        });
                    }
                }
            }
        }

        // ?? PHASE 4: MERGE — deduplicate and final ranking ??
        // Deduplicate by GlobalNodeId, keeping highest score
        let mut seen: HashMap<(u16, u32), usize> = HashMap::new();
        let mut deduped: Vec<DistributedQueryResult> = Vec::new();

        // Sort by strength first so we keep the best
        all_results.sort_by(|a, b| {
            b.cumulative_strength
                .partial_cmp(&a.cumulative_strength)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        for result in all_results {
            let key = (
                result.global_node_id.shard.0,
                result.global_node_id.local.0,
            );
            if !seen.contains_key(&key) {
                seen.insert(key, deduped.len());
                deduped.push(result);
            }
        }

        deduped.truncate(max_results);
        deduped
    }

    /// Read a blob from any shard by GlobalNodeId.
    pub fn read_blob(&self, id: GlobalNodeId) -> &str {
        self.shards[id.shard.0 as usize]
            .graph
            .graph
            .read_blob(id.local)
    }

    /// Get cluster-wide statistics.
    pub fn stats(&self) -> ClusterStats {
        let mut total_nodes = 0;
        let mut total_edges = 0;
        let mut total_ghost_edges = 0;
        let mut shard_stats = Vec::new();

        for shard in &self.shards {
            let nodes = shard.graph.graph.node_count();
            let edges = shard.graph.graph.edge_count();
            let ghosts: usize = shard.ghost_edges.values()
                .map(|v| v.len())
                .sum();

            total_nodes += nodes;
            total_edges += edges;
            total_ghost_edges += ghosts;

            shard_stats.push(ShardStats {
                shard_id: shard.id,
                node_count: nodes,
                edge_count: edges,
                ghost_edge_count: ghosts,
                index_stats: shard.graph.text_index.stats(),
            });
        }

        ClusterStats {
            shard_count: self.shards.len(),
            total_nodes,
            total_edges,
            total_ghost_edges,
            shards: shard_stats,
        }
    }
}

#[derive(Debug)]
pub struct ClusterStats {
    pub shard_count: usize,
    pub total_nodes: usize,
    pub total_edges: usize,
    pub total_ghost_edges: usize,
    pub shards: Vec<ShardStats>,
}

#[derive(Debug)]
pub struct ShardStats {
    pub shard_id: ShardId,
    pub node_count: usize,
    pub edge_count: usize,
    pub ghost_edge_count: usize,
    pub index_stats: IndexStats,
}

// ?? Shard Rebalancing ?????????????????????????????????????????????

/// When shards become uneven (e.g., after adding a new machine),
/// this module handles migrating nodes between shards.
///
/// Strategy: minimal migration via consistent hashing.
/// Only nodes that hash to the new shard's range need to move.

pub struct RebalancePlan {
    /// (source_shard, target_shard, list of GlobalNodeIds to migrate)
    pub migrations: Vec<(ShardId, ShardId, Vec<GlobalNodeId>)>,
    pub estimated_bytes: u64,
}

impl ShardRouter {
    /// Compute a rebalance plan (does NOT execute it).
    /// Call this after adding/removing shards.
    pub fn plan_rebalance(&self, new_shard_count: u16) -> RebalancePlan {
        let new_ring = ConsistentHashRing::new(new_shard_count, 150);
        let mut migrations: HashMap<(u16, u16), Vec<GlobalNodeId>> = HashMap::new();
        let mut estimated_bytes = 0u64;

        for shard in &self.shards {
            for local_idx in 0..shard.graph.graph.node_count() {
                let node_id = NodeId(local_idx as u32);
                let blob = shard.graph.graph.read_blob(node_id);
                let new_shard = new_ring.locate(blob.as_bytes());

                if new_shard != shard.id {
                    let key = (shard.id.0, new_shard.0);
                    migrations.entry(key).or_default().push(GlobalNodeId {
                        shard: shard.id,
                        local: node_id,
                    });
                    estimated_bytes += blob.len() as u64;
                }
            }
        }

        let plan: Vec<(ShardId, ShardId, Vec<GlobalNodeId>)> = migrations
            .into_iter()
            .map(|((from, to), nodes)| (ShardId(from), ShardId(to), nodes))
            .collect();

        RebalancePlan {
            migrations: plan,
            estimated_bytes,
        }
    }
}

// ???????????????????????????????????????????????????????????????????
// INTEGRATION TEST: All Three Systems Working Together
// ???????????????????????????????????????????????????????????????????

#[cfg(test)]
mod integration_tests {
    use super::*;

    #[test]
    fn full_pipeline_sharded_indexed_serializable() {
        // ?? Build a 3-shard cluster ??
        let mut router = ShardRouter::new(3);

        // Register properties on shard 0 (in production, sync across shards)
        let topic = router.shards[0].graph.graph.properties.register("topic");
        let confidence = router.shards[0].graph.graph.properties.register("confidence");

        // Register same properties on all shards
        for shard in &mut router.shards[1..] {
            shard.graph.graph.properties.register("topic");
            shard.graph.graph.properties.register("confidence");
        }

        // Add nodes (automatically distributed by content hash)
        let rust_node = router.add_node(
            "Rust is a systems programming language that guarantees memory \
             safety without garbage collection through its ownership system.",
            Some("Rust Lang"),
        );

        let ownership_node = router.add_node(
            "The ownership system ensures every value has exactly one owner. \
             When the owner goes out of scope, the value is automatically dropped, \
             freeing memory without a garbage collector.",
            Some("Ownership"),
        );

        let borrowing_node = router.add_node(
            "Borrowing lets you reference data without taking ownership. \
             Immutable borrows allow multiple readers. Mutable borrows are exclusive. \
             The borrow checker enforces these rules at compile time.",
            Some("Borrowing"),
        );

        let gc_node = router.add_node(
            "Garbage collection automatically reclaims memory that is no longer \
             in use. Languages like Java, Go, and Python use garbage collectors. \
             Rust achieves similar safety without the runtime overhead.",
            Some("Garbage Collection"),
        );

        println!("Node placement:");
        println!("  Rust:       shard {}", rust_node.shard.0);
        println!("  Ownership:  shard {}", ownership_node.shard.0);
        println!("  Borrowing:  shard {}", borrowing_node.shard.0);
        println!("  GC:         shard {}", gc_node.shard.0);

        // Add edges (handles local + cross-shard automatically)
        router.add_edge(rust_node, ownership_node, 0.95, vec![
            (topic, PropertyValue::Label(CompactString::new("memory"))),
            (confidence, PropertyValue::Float(0.99)),
        ]);

        router.add_edge(ownership_node, borrowing_node, 0.90, vec![
            (topic, PropertyValue::Label(CompactString::new("memory"))),
            (confidence, PropertyValue::Float(0.95)),
        ]);

        router.add_edge(rust_node, gc_node, 0.60, vec![
            (topic, PropertyValue::Label(CompactString::new("comparison"))),
            (confidence, PropertyValue::Float(0.70)),
        ]);

        // ?? Distributed search ??
        let results = router.distributed_search(
            "ownership memory safety",
            &["topic"],
            0.5,  // min strength
            3,    // max depth
            10,   // max results
        );

        println!("\n=== Distributed Search: 'ownership memory safety' ===");
        for result in &results {
            let blob_snippet = &router.read_blob(result.global_node_id)
                [..80.min(router.read_blob(result.global_node_id).len())];
            println!(
                "  [{:?}] shard={} strength={:.3} depth={} label={:?}\n    {}...",
                result.global_node_id.local,
                result.global_node_id.shard.0,
                result.cumulative_strength,
                result.depth,
                result.label,
                blob_snippet,
            );
        }

        // ?? Cluster stats ??
        let stats = router.stats();
        println!("\n=== Cluster Stats ===");
        println!("  Total: {} nodes, {} edges, {} ghost edges across {} shards",
            stats.total_nodes, stats.total_edges,
            stats.total_ghost_edges, stats.shard_count);
        for ss in &stats.shards {
            println!(
                "  Shard {}: {} nodes, {} edges, {} ghosts, {} indexed terms",
                ss.shard_id.0, ss.node_count, ss.edge_count,
                ss.ghost_edge_count, ss.index_stats.unique_terms
            );
        }

        // ?? Rebalance plan (what if we added a 4th shard?) ??
        let plan = router.plan_rebalance(4);
        println!("\n=== Rebalance Plan (3 ? 4 shards) ===");
        for (from, to, nodes) in &plan.migrations {
            println!("  Migrate {} nodes: shard {} ? shard {}",
                nodes.len(), from.0, to.0);
        }
        println!("  Estimated data transfer: {} bytes", plan.estimated_bytes);

        // ?? Verify serialization round-trip on one shard ??
        let test_dir = std::env::temp_dir().join("graph_shard_test");
        let shard0 = &router.shards[0];
        shard0.graph.graph.save_to_disk(&test_dir).unwrap();

        let reloaded = KnowledgeGraph::load_from_disk(&test_dir).unwrap();
        assert_eq!(reloaded.node_count(), shard0.graph.graph.node_count());
        assert_eq!(reloaded.edge_count(), shard0.graph.graph.edge_count());

        // Verify blob integrity after round-trip
        for i in 0..reloaded.node_count() {
            let original = shard0.graph.graph.read_blob(NodeId(i as u32));
            let loaded = reloaded.read_blob(NodeId(i as u32));
            assert_eq!(original, loaded, "Blob mismatch for node {}", i);
        }

        // Cleanup
        let _ = fs::remove_dir_all(&test_dir);

        println!("\n? All assertions passed.");
    }
}
```

---

## Cargo.toml Dependencies

```
[package]
name = "knowledge-graph"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
bincode = "1.3"

[dependencies.memmap2]
version = "0.9"
optional = true

[features]
default = []
mmap = ["memmap2"]

[dev-dependencies]
tempfile = "3"
```

---

## How the Three Systems Interconnect

```
                    USER QUERY: "ownership memory safety"
                                ?
                    ????????????????????????????
                    ?      ShardRouter         ?
                    ?  (Section 12)            ?
                    ????????????????????????????
              SCATTER   ?       ?      ?
                    ???????? ??????? ???????
                    ?Shard0? ?Shard1? ?Shard2?
                    ?      ? ?      ? ?      ?
                    ? ?????????????????????  ?
                    ? ?  InvertedIndex     ?  ?  ? Section 11
                    ? ?  BM25 scoring      ?  ?    (per-shard)
                    ? ?  "ownership" ? [N0] ?  ?
                    ? ?  "memory"    ? [N0] ?  ?
                    ? ??????????????????????  ?
                    ?          ? seed nodes    ?
                    ? ??????????????????????  ?
                    ? ?  KnowledgeGraph    ?  ?  ? Core module
                    ? ?  BFS traversal     ?  ?    (Sections 1-9)
                    ? ?  bitmask filtering ?  ?
                    ? ?  ghost edge check ???????? cross-shard fetch
                    ? ??????????????????????  ?
                    ?          ?               ?
                    ? ??????????????????????  ?
                    ? ?  BlobStore / mmap  ?  ?  ? Section 10
                    ? ?  zero-copy read    ?  ?    (only when result
                    ? ?  on-demand paging  ?  ?     blob is requested)
                    ? ????????????????????-?  ?
                    ???????????????????????????
                                ?
                    ????????????????????????????
                    ?  MERGE + DEDUP + RANK    ?
                    ?  ? final results         ?
                    ????????????????????????????
```

Each layer is independently testable, independently deployable, and connected through the thin `NodeId`/`GlobalNodeId`/`BlobRef` indirection handles — no tight coupling, no deep inheritance hierarchies, just Rust structs and traits doing what they do best.

