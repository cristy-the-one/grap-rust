//! Error types exposed to Python.

use pyo3::prelude::*;

// `GraphError` is a real Python exception (subclass of `Exception`), so it can
// be raised and caught in Python code.
pyo3::create_exception!(pygrap, GraphError, pyo3::exceptions::PyException);

/// Convert any `grap::GraphError` into a raisable Python `GraphError`.
pub fn to_py_err(err: grap::GraphError) -> PyErr {
    GraphError::new_err(err.to_string())
}
