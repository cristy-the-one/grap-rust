//! Python wrapper around `grap::ShardedGraph`.

use std::path::PathBuf;
use std::time::Duration;

use pyo3::prelude::*;
use pyo3::types::{PyAny, PyType};

use crate::errors::to_py_err;
use grap::{
    FusionMode, GlobalNodeId, HybridWeights, NodeId, ShardId, ShardedGraph as InnerShardedGraph,
};

/// Represents a sharded knowledge graph.
///
/// Global node IDs are returned/passed as tuples of the form `(shard_id, local_id)`.
#[pyclass]
pub struct ShardedGraph {
    inner: InnerShardedGraph,
    path: Option<PathBuf>,
}

#[pymethods]
impl ShardedGraph {
    /// Open an existing sharded graph or create a new one.
    ///
    /// If the directory exists and contains a valid sharded graph, it will be loaded.
    /// Otherwise a fresh `ShardedGraph` with the given number of shards is created.
    #[new]
    #[pyo3(signature = (path, shard_count))]
    fn new(path: &str, shard_count: u16) -> PyResult<Self> {
        let dir = PathBuf::from(path);

        let sg = if dir.exists() {
            match InnerShardedGraph::load_from_dir(&dir) {
                Ok(g) => g,
                Err(_) => InnerShardedGraph::new(shard_count),
            }
        } else {
            InnerShardedGraph::new(shard_count)
        };

        Ok(Self {
            inner: sg,
            path: Some(dir),
        })
    }

    /// Persist the graph to its associated directory.
    fn save(&mut self) -> PyResult<bool> {
        if let Some(ref dir) = self.path {
            self.inner.save_to_dir(dir).map_err(to_py_err)?;
            Ok(true)
        } else {
            Err(pyo3::exceptions::PyValueError::new_err(
                "This sharded graph has no associated directory. Use save_to_dir() instead.",
            ))
        }
    }

    /// Save the graph to a specific directory.
    fn save_to_dir(&mut self, path: &str) -> PyResult<()> {
        let dir = PathBuf::from(path);
        self.inner.save_to_dir(&dir).map_err(to_py_err)?;
        self.path = Some(dir);
        Ok(())
    }

    /// Add a node. Returns the global node ID as a tuple `(shard_id, local_id)`.
    ///
    /// Optionally accepts an embedding (list of floats) for hybrid vector search.
    #[pyo3(signature = (text, label=None, embedding=None))]
    fn add_node(
        &mut self,
        text: &str,
        label: Option<&str>,
        embedding: Option<Vec<f32>>,
    ) -> PyResult<(u16, u32)> {
        let emb_ref: Option<&[f32]> = embedding.as_deref();
        let gid = self
            .inner
            .add_node_with_embedding(text, label, emb_ref)
            .map_err(to_py_err)?;
        Ok((gid.shard.0, gid.local.0))
    }

    /// Add an edge between two global node IDs (passed as tuples).
    fn add_edge(&mut self, source: (u16, u32), target: (u16, u32), strength: f64) -> PyResult<()> {
        let src = GlobalNodeId {
            shard: ShardId(source.0),
            local: NodeId(source.1),
        };
        let tgt = GlobalNodeId {
            shard: ShardId(target.0),
            local: NodeId(target.1),
        };

        self.inner
            .add_edge(src, tgt, strength, vec![])
            .map_err(to_py_err)?;
        Ok(())
    }

    /// Remove a node (and its incident edges) across the sharded cluster.
    /// Accepts a global node ID as tuple `(shard_id, local_id)`.
    fn remove_node(&mut self, gid: (u16, u32)) -> PyResult<bool> {
        let global_id = GlobalNodeId {
            shard: ShardId(gid.0),
            local: NodeId(gid.1),
        };
        let removed = self.inner.remove_node(global_id).map_err(to_py_err)?;
        Ok(removed)
    }

    /// Hex SHA-256 content hash of a node's text (or None).
    fn get_node_content_hash(&self, gid: (u16, u32)) -> Option<String> {
        let global_id = GlobalNodeId {
            shard: ShardId(gid.0),
            local: NodeId(gid.1),
        };
        self.inner.node_content_hash(global_id).map(|bytes| {
            bytes.iter().map(|b| format!("{:02x}", b)).collect()
        })
    }

    /// "match" | "mismatch" | "unknown" for the node's content integrity.
    fn verify_node(&self, gid: (u16, u32)) -> String {
        let global_id = GlobalNodeId {
            shard: ShardId(gid.0),
            local: NodeId(gid.1),
        };
        match self.inner.verify_node(global_id) {
            grap::NodeIntegrity::Match => "match",
            grap::NodeIntegrity::Mismatch => "mismatch",
            grap::NodeIntegrity::Unknown => "unknown",
        }
        .to_string()
    }

    /// Aggressively clean up ghost edges that point to deleted nodes.
    fn cleanup_ghost_edges(&mut self) {
        self.inner.cleanup_ghost_edges();
    }

    /// Hybrid search + traversal.
    /// Global node IDs in results are returned as tuples `(shard_id, local_id)`.
    #[pyo3(signature = (
        query,
        max_seeds = 10,
        required_properties = vec![],
        min_strength = 0.3,
        max_depth = 4u32,
        max_results = 50,
    ))]
    fn search_and_traverse(
        &self,
        query: &str,
        max_seeds: usize,
        required_properties: Vec<String>,
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> PyResult<Vec<ShardedQueryResult>> {
        // Convert Vec<String> to Vec<&str>
        let prop_refs: Vec<&str> = required_properties.iter().map(|s| s.as_str()).collect();

        let results = self.inner.search_and_traverse(
            query,
            max_seeds,
            &prop_refs,
            min_strength,
            max_depth,
            max_results,
        );

        Ok(results
            .into_iter()
            .map(|r| ShardedQueryResult {
                node: (r.node.shard.0, r.node.local.0),
                text_score: r.text_score,
                vector_score: r.vector_score,
                graph_strength: r.graph_strength,
                fused_score: r.fused_score,
                depth: r.depth,
                text: r.text.clone(),
            })
            .collect())
    }

    /// Hybrid text + vector + graph search across the sharded cluster.
    ///
    /// `fusion_mode` is `"rrf"` (default) or `"linear"`.
    /// `rrf_k` is the RRF rank-smoothing constant (default 60.0).
    /// `centrality_weight` scales the optional PageRank contribution (default 0.0).
    /// `traversal_weight_property` optionally prioritises edges that carry the named property
    /// during BFS expansion (mirrors the non-sharded `search_and_traverse_hybrid` parameter).
    ///
    /// Note: RRF ranks and PageRank centrality are computed in each shard's own local space;
    /// the sharded method merges results by raw fused score across shards — so centrality is
    /// per-shard, not global.
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
        centrality_weight = 0.0,
        traversal_weight_property = None,
        fusion_mode = String::from("rrf"),
        rrf_k = 60.0,
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
        centrality_weight: f64,
        traversal_weight_property: Option<String>,
        fusion_mode: String,
        rrf_k: f64,
    ) -> PyResult<Vec<ShardedQueryResult>> {
        let mode = match fusion_mode.as_str() {
            "linear" => FusionMode::Linear,
            _ => FusionMode::Rrf { k: rrf_k },
        };
        let weights = HybridWeights {
            text: text_weight,
            vector: vector_weight,
            graph: graph_weight,
            centrality: centrality_weight,
            traversal_weight_property,
            mode,
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
            .map(|r| ShardedQueryResult {
                node: (r.node.shard.0, r.node.local.0),
                text_score: r.text_score,
                vector_score: r.vector_score,
                graph_strength: r.graph_strength,
                fused_score: r.fused_score,
                depth: r.depth,
                text: r.text.clone(),
            })
            .collect())
    }

    /// Standalone vector similarity search across the sharded cluster.
    ///
    /// Returns up to `top_k` results as `((shard_id, local_id), similarity_score)`.
    fn vector_search(&self, embedding: Vec<f32>, top_k: usize) -> PyResult<Vec<((u16, u32), f64)>> {
        let results = self.inner.vector_search(&embedding, top_k);
        Ok(results
            .into_iter()
            .map(|(gid, score)| ((gid.shard.0, gid.local.0), score))
            .collect())
    }

    /// Compute a rebalance plan (mainly for inspection / dry-run).
    fn plan_rebalance(&self, target_shards: u16) -> PyResult<RebalancePlanPy> {
        let plan = self.inner.plan_rebalance(target_shards);
        Ok(RebalancePlanPy {
            target_shards,
            node_migrations: plan.node_migrations.len() as u64,
        })
    }

    /// Execute a rebalance to the given number of shards.
    /// This is the most common operation users want.
    fn execute_rebalance_to(&mut self, target_shards: u16) -> PyResult<()> {
        let plan = self.inner.plan_rebalance(target_shards);
        self.inner.execute_rebalance(plan).map_err(to_py_err)?;
        Ok(())
    }

    /// Start background maintenance (merge + vacuum + ghost cleanup) on the sharded graph.
    #[allow(deprecated)]
    fn start_background_maintenance(&mut self, interval_ms: u64) -> bool {
        let _handle = self
            .inner
            .start_background_maintenance(Duration::from_millis(interval_ms));
        true
    }

    fn node_count(&self) -> usize {
        self.inner.global_node_count()
    }

    fn shard_count(&self) -> usize {
        self.inner.shard_count()
    }

    fn ghost_edge_count(&self) -> usize {
        self.inner.ghost_edge_count()
    }

    /// Return basic cluster statistics as a dictionary.
    fn stats(&self) -> PyResult<Py<PyAny>> {
        let stats = self.inner.stats();
        Python::with_gil(|py| {
            let dict = pyo3::types::PyDict::new_bound(py);
            dict.set_item("shard_count", stats.shard_count)?;
            dict.set_item("total_nodes", stats.total_nodes)?;
            dict.set_item("total_ghost_edges", stats.total_ghost_edges)?;
            dict.set_item("nodes_per_shard", stats.nodes_per_shard.clone())?;
            dict.set_item("ghost_edges_per_shard", stats.ghost_edges_per_shard.clone())?;
            dict.set_item("remote_cache_entries", stats.remote_cache_entries)?;
            // You can extend this later with more fields
            Ok(dict.into())
        })
    }

    fn __repr__(&self) -> String {
        match &self.path {
            Some(p) => format!(
                "ShardedGraph(path={:?}, shards={}, nodes={})",
                p,
                self.shard_count(),
                self.node_count()
            ),
            None => format!(
                "ShardedGraph(shards={}, nodes={})",
                self.shard_count(),
                self.node_count()
            ),
        }
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &mut self,
        _exc_type: Option<&Bound<'_, PyType>>,
        _exc_value: Option<&Bound<'_, PyAny>>,
        _traceback: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<bool> {
        if self.path.is_some() {
            let _ = self.save();
        }
        Ok(false)
    }
}

/// Lightweight Python representation of a rebalance plan.
#[pyclass]
#[derive(Clone)]
pub struct RebalancePlanPy {
    pub target_shards: u16,
    pub node_migrations: u64,
}

#[pymethods]
impl RebalancePlanPy {
    fn __repr__(&self) -> String {
        format!(
            "RebalancePlan(target_shards={}, migrations={})",
            self.target_shards, self.node_migrations
        )
    }
}

/// Rich result from `ShardedGraph.search_and_traverse` and hybrid variants.
#[pyclass]
#[derive(Clone)]
pub struct ShardedQueryResult {
    /// Global node ID as (shard_id, local_id)
    #[pyo3(get)]
    pub node: (u16, u32),
    #[pyo3(get)]
    pub text_score: f64,
    #[pyo3(get)]
    pub vector_score: f64,
    #[pyo3(get)]
    pub graph_strength: f64,
    #[pyo3(get)]
    pub fused_score: f64,
    #[pyo3(get)]
    pub depth: u32,
    #[pyo3(get)]
    pub text: String,
}

#[pymethods]
impl ShardedQueryResult {
    fn __repr__(&self) -> String {
        format!(
            "ShardedQueryResult(node=({}, {}), fused={:.3}, vec={:.2}, depth={})",
            self.node.0, self.node.1, self.fused_score, self.vector_score, self.depth
        )
    }
}
