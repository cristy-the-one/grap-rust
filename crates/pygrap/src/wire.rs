//! Python bindings for `grap::wire` — the `#MUNCH/1` columnar wire encoder.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};

use grap::wire::{encode, WireScalar, WireTable, WireValue};

/// Convert a Python scalar value to a `WireValue`.
///
/// Dispatch order matters: bool MUST be checked before int because Python's
/// `bool` is a subtype of `int`.
fn py_to_value(v: &Bound<'_, PyAny>) -> PyResult<WireValue> {
    if v.is_none() {
        return Ok(WireValue::Null);
    }
    if let Ok(b) = v.extract::<bool>() {
        return Ok(WireValue::Bool(b));
    }
    if let Ok(i) = v.extract::<i64>() {
        return Ok(WireValue::Int(i));
    }
    if let Ok(f) = v.extract::<f64>() {
        return Ok(WireValue::Float(f));
    }
    Ok(WireValue::Str(v.str()?.to_string()))
}

/// Encode a tool result into the `#MUNCH/1` columnar wire format.
///
/// Parameters
/// ----------
/// tool : str
///     Tool name, e.g. ``"graph_neighbors"``.
/// enc_id : str
///     Encoding id, e.g. ``"gn1"``.
/// scalars : list[tuple[str, value]]
///     Top-level scalar key/value pairs.  Values may be ``int``, ``float``,
///     ``bool``, ``str``, or ``None``.
/// tables : list[tuple[str, str, list[str], list[int], list[list]]]
///     Each table is ``(tag, key, cols, intern_cols, rows)``:
///
///     * ``tag`` — single character used as the row prefix in the output.
///     * ``key`` — the response key this table came from.
///     * ``cols`` — ordered column names.
///     * ``intern_cols`` — 0-based column indices whose strings are interned.
///     * ``rows`` — list of rows; each row is a list of cell values.
///
/// Returns
/// -------
/// str
///     The encoded ``#MUNCH/1`` payload.
#[pyfunction]
#[pyo3(signature = (tool, enc_id, scalars, tables))]
fn encode_tables(
    tool: &str,
    enc_id: &str,
    scalars: &Bound<'_, PyList>,
    tables: &Bound<'_, PyList>,
) -> PyResult<String> {
    // --- scalars ---
    let mut sc: Vec<WireScalar> = Vec::with_capacity(scalars.len());
    for item in scalars.iter() {
        let t = item.downcast::<PyTuple>().map_err(|_| {
            pyo3::exceptions::PyTypeError::new_err(
                "each scalar must be a (key, value) tuple",
            )
        })?;
        let key: String = t.get_item(0)?.extract()?;
        let val = py_to_value(&t.get_item(1)?)?;
        sc.push(WireScalar { key, value: val });
    }

    // --- tables ---
    let mut tbls: Vec<WireTable> = Vec::with_capacity(tables.len());
    for item in tables.iter() {
        let t = item.downcast::<PyTuple>().map_err(|_| {
            pyo3::exceptions::PyTypeError::new_err(
                "each table must be a (tag, key, cols, intern_cols, rows) tuple",
            )
        })?;

        let tag_str: String = t.get_item(0)?.extract()?;
        // Tags must be a single unique char per document (the row prefix that
        // maps a CSV row back to its table); only the first char is used.
        let tag = tag_str.chars().next().unwrap_or('t');
        let key: String = t.get_item(1)?.extract()?;
        let cols: Vec<String> = t.get_item(2)?.extract()?;
        let intern_cols: Vec<usize> = t.get_item(3)?.extract()?;

        // rows: list[list[...]]  (each row is a Python list of cell values)
        let rows_obj = t.get_item(4)?;
        let rows_list = rows_obj.downcast::<PyList>().map_err(|_| {
            pyo3::exceptions::PyTypeError::new_err("rows must be a list of lists")
        })?;

        let mut rows: Vec<Vec<WireValue>> = Vec::with_capacity(rows_list.len());
        for row_obj in rows_list.iter() {
            let row_list = row_obj.downcast::<PyList>().map_err(|_| {
                pyo3::exceptions::PyTypeError::new_err("each row must be a list of cell values")
            })?;
            let mut cells: Vec<WireValue> = Vec::with_capacity(row_list.len());
            for cell in row_list.iter() {
                cells.push(py_to_value(&cell)?);
            }
            rows.push(cells);
        }

        tbls.push(WireTable { tag, key, cols, intern_cols, rows });
    }

    Ok(encode(tool, enc_id, &sc, &tbls))
}

/// Register the ``pygrap.wire`` submodule onto ``parent``.
pub fn register(parent: &Bound<'_, PyModule>) -> PyResult<()> {
    let m = PyModule::new(parent.py(), "wire")?;
    m.add_function(wrap_pyfunction!(encode_tables, &m)?)?;
    parent.add_submodule(&m)?;
    Ok(())
}
