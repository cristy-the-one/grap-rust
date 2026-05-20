//! Error types exposed to Python.

use pyo3::prelude::*;
use pyo3::PyErr;

/// The main exception type for grap errors.
#[pyclass]
#[derive(Debug)]
pub struct GraphError {
    message: String,
}

#[pymethods]
impl GraphError {
    fn __str__(&self) -> &str {
        &self.message
    }

    fn __repr__(&self) -> String {
        format!("GraphError({})", self.message)
    }
}

impl GraphError {
    pub fn new(msg: impl Into<String>) -> Self {
        GraphError {
            message: msg.into(),
        }
    }
}

impl From<grap::GraphError> for GraphError {
    fn from(err: grap::GraphError) -> Self {
        GraphError::new(err.to_string())
    }
}

impl From<GraphError> for PyErr {
    fn from(err: GraphError) -> PyErr {
        PyErr::new::<GraphError, _>(err.message)
    }
}

/// Helper to convert any `grap::GraphError` into a Python exception.
pub fn to_py_err(err: grap::GraphError) -> PyErr {
    GraphError::from(err).into()
}
