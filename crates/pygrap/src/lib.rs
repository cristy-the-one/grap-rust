//! pygrap - Python bindings for the grap knowledge graph engine.
//!
//! This is currently an internal / WIP tool. It is not published on PyPI.

use pyo3::prelude::*;

mod errors;
mod indexed_graph;
mod lifecycle;
mod sharded_graph;
mod wire;

pub use errors::GraphError;

/// Python module exposed as `import pygrap`
#[pymodule]
fn pygrap(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    // Register custom exception
    m.add("GraphError", m.py().get_type::<GraphError>())?;

    // Register classes
    m.add_class::<indexed_graph::IndexedKnowledgeGraph>()?;
    m.add_class::<indexed_graph::QueryResult>()?;
    m.add_class::<indexed_graph::MergeReport>()?;
    m.add_class::<indexed_graph::TraversalResult>()?;
    m.add_class::<sharded_graph::ShardedGraph>()?;
    m.add_class::<sharded_graph::RebalancePlanPy>()?;
    m.add_class::<sharded_graph::ShardedQueryResult>()?;
    m.add_class::<lifecycle::Workspace>()?;
    m.add_class::<lifecycle::Version>()?;
    m.add_class::<lifecycle::Activation>()?;
    m.add_class::<lifecycle::AuditEvent>()?;
    m.add_class::<lifecycle::RunRecord>()?;
    m.add_class::<lifecycle::ReconstructionReport>()?;
    m.add_class::<lifecycle::RetentionReport>()?;

    m.add_function(wrap_pyfunction!(version, m)?)?;

    // Register wire submodule
    wire::register(m)?;

    Ok(())
}

/// Returns the version of the pygrap bindings.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
