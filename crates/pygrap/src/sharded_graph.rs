//! Python wrapper around `grap::ShardedGraph`.

use std::path::PathBuf;
use std::time::Duration;

use pyo3::prelude::*;
use pyo3::types::{PyAny, PyType};

use crate::errors::to_py_err;
use grap::{FusionWeights, GlobalNodeId, NodeId, ShardId, ShardedGraph as InnerShardedGraph};

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
    fn add_node(&mut self, text: &str, label: Option<&str>) -> PyResult<(u16, u32)> {
        let gid = self.inner.add_node(text, label).map_err(to_py_err)?;
        Ok((gid.shard.0, gid.local.0))
    }

    /// Add an edge between two global node IDs (passed as tuples).
    fn add_edge(
        &mut self,
        source: (u16, u32),
        target: (u16, u32),
        strength: f64,
    ) -> PyResult<()> {
        let src = GlobalNodeId {
            shard: ShardId(source.0),
            local: NodeId(source.1),
        };
        let tgt = GlobalNodeId {
            shard: ShardId(target.0),
            local: NodeId(target.1),
        };

        self.inner.add_edge(src, tgt, strength, vec![]).map_err(to_py_err)?;
        Ok(())
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
    ) -> PyResult<Vec<((u16, u32), f64)>> {
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
            .map(|r| {
                let gid = r.node;
                ((gid.shard.0, gid.local.0), r.fused_score)
            })
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
    fn start_background_maintenance(&mut self, interval_ms: u64) -> bool {
        let _handle = self.inner
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
