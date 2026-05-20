//! pygrap - Python bindings for the grap knowledge graph engine.
//!
//! This is currently an internal / WIP tool. It is not published on PyPI.

use pyo3::prelude::*;

/// Python module exposed as `import pygrap`
#[pymodule]
fn pygrap(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    Ok(())
}

/// Returns the version of the pygrap bindings (and indirectly the grap crate).
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
