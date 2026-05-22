//! Python wrapper around `grap::IndexedKnowledgeGraph`.

use std::path::PathBuf;
use std::time::Duration;

use pyo3::prelude::*;
use pyo3::types::{PyAny, PyType};

use crate::errors::to_py_err;
use grap::{FusionWeights, HybridWeights, IndexedKnowledgeGraph as InnerGraph};

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
    ) -> PyResult<Vec<QueryResult>> {
        let weights = HybridWeights {
            text: text_weight,
            vector: vector_weight,
            graph: graph_weight,
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
