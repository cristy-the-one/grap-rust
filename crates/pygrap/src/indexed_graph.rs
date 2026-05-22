//! Python wrapper around `grap::IndexedKnowledgeGraph`.

use std::path::PathBuf;
use std::time::Duration;

use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyType};

use crate::errors::to_py_err;
use grap::{FusionWeights, HybridWeights, IndexedKnowledgeGraph as InnerGraph, NodeId, PropertyValue};

/// Result returned by `IndexedKnowledgeGraph.merge_nodes(keep, absorb)`.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct MergeReport {
    pub absorbed: u32,
    pub edges_redirected: usize,
    pub edges_merged: usize,
    pub self_loops_dropped: usize,
    pub embedding_updated: bool,
}

#[pymethods]
impl MergeReport {
    fn __repr__(&self) -> String {
        format!(
            "MergeReport(absorbed={}, edges_redirected={}, edges_merged={}, \
             self_loops_dropped={}, embedding_updated={})",
            self.absorbed, self.edges_redirected, self.edges_merged,
            self.self_loops_dropped, self.embedding_updated
        )
    }
}

impl From<grap::MergeReport> for MergeReport {
    fn from(r: grap::MergeReport) -> Self {
        MergeReport {
            absorbed: r.absorbed.0,
            edges_redirected: r.edges_redirected,
            edges_merged: r.edges_merged,
            self_loops_dropped: r.self_loops_dropped,
            embedding_updated: r.embedding_updated,
        }
    }
}

/// Single result returned by `IndexedKnowledgeGraph.traverse_by_relation(...)`.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct TraversalResult {
    pub node_id: u32,
    pub depth: u32,
    pub strength: f64,
}

#[pymethods]
impl TraversalResult {
    fn __repr__(&self) -> String {
        format!(
            "TraversalResult(node_id={}, depth={}, strength={:.3})",
            self.node_id, self.depth, self.strength
        )
    }
}

impl From<grap::QueryResult> for TraversalResult {
    fn from(r: grap::QueryResult) -> Self {
        TraversalResult {
            node_id: r.node_id.0,
            depth: r.depth,
            strength: r.cumulative_strength,
        }
    }
}

#[pyclass]
pub struct IndexedKnowledgeGraph {
    inner: InnerGraph,
    /// Directory the graph is persisted to (if any).
    path: Option<PathBuf>,
}

/// Rich result returned by hybrid (and legacy) search methods from Python.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct QueryResult {
    pub node: u32,
    pub text_score: f64,
    pub vector_score: f64,
    pub graph_strength: f64,
    pub fused_score: f64,
    pub depth: u32,
    pub text: String,
}

#[pymethods]
impl QueryResult {
    fn __repr__(&self) -> String {
        format!(
            "QueryResult(node={}, fused={:.3}, text={:.2}, vec={:.2}, graph={:.2}, depth={})",
            self.node, self.fused_score, self.text_score, self.vector_score, self.graph_strength, self.depth
        )
    }
}

/// Convert a Python object to a `PropertyValue`.
///
/// Dispatch order: `str` → `bool` → `int` → `float`.
///
/// IMPORTANT: bool MUST be checked before int — in Python, `bool` is a subtype
/// of `int`, so `extract::<i64>()` would succeed for `True`/`False` as well.
fn py_to_property_value(val: &Bound<'_, PyAny>) -> PyResult<PropertyValue> {
    if let Ok(s) = val.extract::<String>() {
        return Ok(PropertyValue::Label(s));
    }
    if let Ok(b) = val.extract::<bool>() {
        return Ok(PropertyValue::Bool(b));
    }
    if let Ok(i) = val.extract::<i64>() {
        return Ok(PropertyValue::Int(i));
    }
    if let Ok(f) = val.extract::<f64>() {
        return Ok(PropertyValue::Float(f));
    }
    Err(pyo3::exceptions::PyTypeError::new_err(
        "cannot convert to PropertyValue: expected str, int, float, or bool",
    ))
}

#[pymethods]
impl IndexedKnowledgeGraph {
    /// Open an existing on-disk graph or create a new one at the given path.
    ///
    /// If `merge_policy` is provided and the graph is being created (not loaded),
    /// a tiered merge policy will be enabled with the given `segments_per_tier`.
    #[new]
    #[pyo3(signature = (path, *, merge_policy=None))]
    fn new(path: &str, merge_policy: Option<usize>) -> PyResult<Self> {
        let dir = PathBuf::from(path);

        let ig = if dir.exists() {
            // Try to load an existing graph
            match InnerGraph::load_from_dir(&dir) {
                Ok(g) => g,
                Err(_) => {
                    // Directory exists but isn't a valid grap graph → create fresh
                    match merge_policy {
                        Some(n) => InnerGraph::new_on_disk_with_merge_policy(n),
                        None => InnerGraph::new_on_disk(),
                    }
                }
            }
        } else {
            // Fresh graph
            match merge_policy {
                Some(n) => InnerGraph::new_on_disk_with_merge_policy(n),
                None => InnerGraph::new_on_disk(),
            }
        };

        Ok(Self {
            inner: ig,
            path: Some(dir),
        })
    }

    /// Create a graph with a specific merge policy.
    /// If the directory already contains a valid graph, it will be loaded instead.
    #[staticmethod]
    fn with_merge_policy(path: &str, segments_per_tier: usize) -> PyResult<Self> {
        let dir = PathBuf::from(path);

        let ig = if dir.exists() {
            InnerGraph::load_from_dir(&dir)
                .unwrap_or_else(|_| InnerGraph::new_on_disk_with_merge_policy(segments_per_tier))
        } else {
            InnerGraph::new_on_disk_with_merge_policy(segments_per_tier)
        };

        Ok(Self {
            inner: ig,
            path: Some(dir),
        })
    }

    /// Persist the current state of the graph to disk.
    ///
    /// If the graph was created/opened with a path, this will write to that directory.
    /// Returns `True` on success.
    fn save(&mut self) -> PyResult<bool> {
        if let Some(ref dir) = self.path {
            self.inner
                .save_to_dir(dir)
                .map_err(to_py_err)?;
            Ok(true)
        } else {
            Err(pyo3::exceptions::PyValueError::new_err(
                "This graph has no associated directory. Use save_to_dir() instead.",
            ))
        }
    }

    /// Explicitly save the graph to a specific directory.
    fn save_to_dir(&mut self, path: &str) -> PyResult<()> {
        let dir = PathBuf::from(path);
        self.inner.save_to_dir(&dir).map_err(to_py_err)?;
        // Remember the new path for future save() calls
        self.path = Some(dir);
        Ok(())
    }

    /// Add a new node with optional label and optional embedding (list of f32).
    ///
    /// When an embedding is supplied, the node can participate in hybrid
    /// vector + text + graph search via `search_and_traverse_hybrid`.
    #[pyo3(signature = (text, label=None, embedding=None))]
    fn add_node(
        &mut self,
        text: &str,
        label: Option<&str>,
        embedding: Option<Vec<f32>>,
    ) -> PyResult<u32> {
        let emb_ref: Option<&[f32]> = embedding.as_deref();
        let id = self.inner.add_node(text, label, emb_ref);
        Ok(id.0)
    }

    /// Add a directed edge between two nodes with optional property dict.
    ///
    /// `properties` is an optional Python dict `{str: any}` where values must be
    /// `str`, `int`, `float`, or `bool`. Property names are auto-registered.
    ///
    /// Raises `ValueError` if `source` or `target` are out of bounds.
    /// Raises `GraphError` if more than 64 distinct property names have been registered.
    /// Raises `TypeError` if a property value cannot be converted.
    #[pyo3(signature = (source, target, strength, properties=None))]
    fn add_edge(
        &mut self,
        source: u32,
        target: u32,
        strength: f64,
        properties: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<()> {
        let node_count = self.inner.graph.node_count();
        if source as usize >= node_count {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "source node {} is out of bounds", source
            )));
        }
        if target as usize >= node_count {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "target node {} is out of bounds", target
            )));
        }
        let mut props = Vec::new();
        if let Some(dict) = properties {
            for (key, val) in dict.iter() {
                let name: String = key.extract()?;
                let tag = self.inner.graph.properties.try_register(&name).map_err(to_py_err)?;
                let pv = py_to_property_value(&val)?;
                props.push((tag, pv));
            }
        }
        self.inner.add_edge(NodeId(source), NodeId(target), strength, props);
        Ok(())
    }

    /// Return the full stored text for a node.
    ///
    /// Raises `ValueError` if the node ID is out of bounds.
    fn get_node_text(&self, id: u32) -> PyResult<String> {
        self.inner
            .graph
            .try_read_blob(NodeId(id))
            .map(|s| s.to_string())
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "node {} is out of bounds",
                    id
                ))
            })
    }

    /// Return the label of a node, or `None` if it has no label.
    ///
    /// Raises `ValueError` if the node ID is out of bounds.
    fn get_node_label(&self, id: u32) -> PyResult<Option<String>> {
        self.inner
            .graph
            .try_node_label(NodeId(id))
            .map(|opt| opt.map(|s| s.to_string()))
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "node {} is out of bounds",
                    id
                ))
            })
    }

    /// Update the text (and optionally the embedding) of an existing node.
    ///
    /// All edges incident to the node are preserved. Raises `GraphError` on
    /// invalid node ID.
    #[pyo3(signature = (id, text, embedding=None))]
    fn update_node(
        &mut self,
        id: u32,
        text: &str,
        embedding: Option<Vec<f32>>,
    ) -> PyResult<()> {
        let emb_ref: Option<&[f32]> = embedding.as_deref();
        self.inner
            .update_node(NodeId(id), text, emb_ref)
            .map_err(to_py_err)
    }

    /// Merge two nodes: keep one, absorb the other.
    ///
    /// All edges from `absorb` are redirected to `keep`. Returns a `MergeReport`
    /// describing the operation. Raises `GraphError` on validation failure
    /// (e.g., same node twice, or invalid IDs).
    fn merge_nodes(&mut self, keep: u32, absorb: u32) -> PyResult<MergeReport> {
        self.inner
            .merge_nodes(NodeId(keep), NodeId(absorb))
            .map(MergeReport::from)
            .map_err(to_py_err)
    }

    /// Return all outgoing neighbors of `node` whose edges have a property
    /// matching `(property_name, value)`.
    ///
    /// Returns a list of `(node_id, strength)` tuples.
    /// `value` must be `str`, `int`, `float`, or `bool`.
    fn neighbors_by_relation(
        &self,
        node: u32,
        property_name: &str,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<Vec<(u32, f64)>> {
        let pv = py_to_property_value(value)?;
        let results = self.inner.neighbors_by_relation(NodeId(node), property_name, pv);
        Ok(results.into_iter().map(|(id, s)| (id.0, s)).collect())
    }

    /// Return all incoming neighbors of `node` whose edges have a property
    /// matching `(property_name, value)`.
    ///
    /// Returns a list of `(node_id, strength)` tuples.
    /// `value` must be `str`, `int`, `float`, or `bool`.
    fn predecessors_by_relation(
        &self,
        node: u32,
        property_name: &str,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<Vec<(u32, f64)>> {
        let pv = py_to_property_value(value)?;
        let results = self.inner.predecessors_by_relation(NodeId(node), property_name, pv);
        Ok(results.into_iter().map(|(id, s)| (id.0, s)).collect())
    }

    /// BFS traversal from `node` following edges that match `(property_name, value)`.
    ///
    /// Returns a list of `TraversalResult` objects (node_id, depth, strength).
    /// `value` must be `str`, `int`, `float`, or `bool`.
    #[pyo3(signature = (node, property_name, value, depth=3, max_results=50))]
    fn traverse_by_relation(
        &self,
        node: u32,
        property_name: &str,
        value: &Bound<'_, PyAny>,
        depth: u32,
        max_results: usize,
    ) -> PyResult<Vec<TraversalResult>> {
        let pv = py_to_property_value(value)?;
        let results = self.inner.traverse_by_relation(
            NodeId(node),
            property_name,
            pv,
            depth,
            max_results,
        );
        Ok(results.into_iter().map(TraversalResult::from).collect())
    }

    /// Perform hybrid text + graph search.
    #[pyo3(signature = (
        query,
        max_seeds = 10,
        required_properties = vec![],
        min_strength = 0.3,
        max_depth = 4,
        max_results = 50,
        text_weight = 0.5,
        graph_weight = 0.5,
    ))]
    fn search_and_traverse(
        &self,
        query: &str,
        max_seeds: usize,
        required_properties: Vec<String>,
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        text_weight: f64,
        graph_weight: f64,
    ) -> PyResult<Vec<QueryResult>> {
        // Note: Property filtering via string names is simplified here.
        // A better version would resolve property names to tags.
        // For the MVP we pass empty required properties to keep things simple.

        let weights = FusionWeights {
            text: text_weight,
            graph: graph_weight,
        };

        // Convert Vec<String> to Vec<&str> for the Rust API
        let prop_refs: Vec<&str> = required_properties.iter().map(|s| s.as_str()).collect();

        let results = self.inner.search_and_traverse(
            query,
            max_seeds,
            &prop_refs,
            min_strength,
            max_depth,
            max_results,
            weights,
        );

        Ok(results
            .into_iter()
            .map(|r| QueryResult {
                node: r.graph_result.node_id.0,
                text_score: r.text_score,
                vector_score: 0.0,
                graph_strength: r.graph_strength,
                fused_score: r.fused_score,
                depth: r.graph_result.depth,
                text: r.text.to_string(),
            })
            .collect())
    }

    /// Hybrid text + vector + graph search.
    ///
    /// `vector` is an optional list of floats (the query embedding).
    /// When provided, cosine similarity against stored node embeddings is computed
    /// and fused using the supplied weights.
    #[pyo3(signature = (
        query,
        vector = None,
        max_seeds = 10,
        required_properties = vec![],
        min_strength = 0.3,
        max_depth = 4,
        max_results = 50,
        text_weight = 0.4,
        vector_weight = 0.4,
        graph_weight = 0.2,
        traversal_weight_property = None,
    ))]
    fn search_and_traverse_hybrid(
        &self,
        query: &str,
        vector: Option<Vec<f32>>,
        max_seeds: usize,
        required_properties: Vec<String>,
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        text_weight: f64,
        vector_weight: f64,
        graph_weight: f64,
        traversal_weight_property: Option<String>,
    ) -> PyResult<Vec<QueryResult>> {
        let weights = HybridWeights {
            text: text_weight,
            vector: vector_weight,
            graph: graph_weight,
            traversal_weight_property,
        };

        let prop_refs: Vec<&str> = required_properties.iter().map(|s| s.as_str()).collect();

        let vec_ref: Option<&[f32]> = vector.as_deref();

        let results = self.inner.search_and_traverse_hybrid(
            query,
            vec_ref,
            max_seeds,
            &prop_refs,
            min_strength,
            max_depth,
            max_results,
            weights,
        );

        Ok(results
            .into_iter()
            .map(|r| QueryResult {
                node: r.graph_result.node_id.0,
                text_score: r.text_score,
                vector_score: r.vector_score,
                graph_strength: r.graph_strength,
                fused_score: r.fused_score,
                depth: r.graph_result.depth,
                text: r.text.to_string(),
            })
            .collect())
    }

    /// Standalone approximate nearest neighbor search using the vector index (HNSW or brute-force).
    ///
    /// Returns up to `top_k` results as `(node_id, similarity_score)`.
    fn vector_search(&self, embedding: Vec<f32>, top_k: usize) -> PyResult<Vec<(u32, f64)>> {
        let results = self.inner.vector_search(&embedding, top_k);
        Ok(results
            .into_iter()
            .map(|(node_id, score)| (node_id.0, score))
            .collect())
    }

    /// Start background merge/vacuum workers.
    fn start_background_workers(&mut self, interval_ms: u64) -> usize {
        self.inner
            .start_background_workers(Duration::from_millis(interval_ms))
    }

    /// Return the number of nodes in the graph.
    fn node_count(&self) -> usize {
        self.inner.graph.node_count()
    }

    /// Return the number of edges in the graph.
    fn edge_count(&self) -> usize {
        self.inner.graph.edge_count()
    }

    /// String representation for Python (nice for debugging / notebooks).
    fn __repr__(&self) -> String {
        match &self.path {
            Some(p) => format!("IndexedKnowledgeGraph(path={:?}, nodes={})", p, self.node_count()),
            None => format!("IndexedKnowledgeGraph(nodes={})", self.node_count()),
        }
    }

    /// Support for context manager: `with pygrap.IndexedKnowledgeGraph(...) as g:`
    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// On exit, try to save the graph if it has a path.
    fn __exit__(
        &mut self,
        _exc_type: Option<&Bound<'_, PyType>>,
        _exc_value: Option<&Bound<'_, PyAny>>,
        _traceback: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<bool> {
        if self.path.is_some() {
            // Best effort save — ignore errors in __exit__
            let _ = self.save();
        }
        Ok(false) // do not suppress exceptions
    }
}
