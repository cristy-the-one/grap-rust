# Token-Budgeted Columnar Wire Encoding (`#MUNCH`-style) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the token cost of every grap result that crosses to an agent by emitting a compact, self-describing, string-interned columnar text format — with a per-call `tokens_saved` measure — while keeping plain JSON as the default until a decoder ships.

**Architecture (core codec + skin schemas).** The *generic, hot* part — interning repeated strings into a legend and packing rows into tag-prefixed CSV — lives in the **Rust core** as `src/wire.rs` (`grap::wire`), because it is pure string/array work that benefits from Rust speed on large graph result sets (thousands of edges/events) and is reused by **three** consumers: the `grap-cli` (`--format compact`), `pygrap`, and `grap_mcp`. The *response-shape-specific* part — which tools have which tables/columns, and format negotiation per request — is **skin** and lives in `grap_mcp/encoding/` (Python). A round-trip decoder lives in core (for tests + CLI) with a thin Python mirror for the MCP/client side.

**Tech Stack:** Rust (`grap::wire`, no new deps — uses `std`), PyO3 (`pygrap.wire`), Python (`grap_mcp/encoding/`), `cargo test` + `pytest`.

**Allocation rationale (core vs skin):** Per your "favor the core for anything embeddable" rule, the codec primitive is embeddable and perf-sensitive → core. The per-tool schema map is not engine logic and is coupled to MCP response shapes → skin. This keeps the engine free of any MCP knowledge while still making the expensive transform blazing fast.

---

## Format (the contract both sides implement)

```
#MUNCH/1 tool=<tool> enc=<schema_id>
@1=<interned string>            ← legend: a string used ≥2× that saves ≥1 char
@2=<interned string>
<scalar k=v ...> __stypes=k:type|...  __tables=tag:key:col|col|...
<tag>,<c1>,<c2>,...             ← one CSV block per table; interned cells written as @N
```
- Header line names the tool and the schema id (so the decoder is version-pinned).
- Legend lines map `@N` → string; only emitted when interning nets a saving.
- One scalar line carries primitive top-level values, a `__stypes` type map (non-string scalars), and `__tables` (the table schema), making decode **self-sufficient**.
- Each table block is CSV rows prefixed by a 1-char tag; cells in interned columns are written as `@N` references.

---

## File Structure

- **Create** `src/wire.rs` — `WireValue`, `WireTable`, `encode(...) -> String`, `decode(&str) -> DecodedDoc`; `pub mod wire;` in `src/lib.rs`.
- **Create** `crates/pygrap/src/wire.rs` — PyO3 wrappers `encode_tables(...)` / `decode(...)`; registered in `crates/pygrap/src/lib.rs`.
- **Create** `mcp-server/grap_mcp/encoding/__init__.py` — `encode_response(tool, response, requested_format, repo)`, `default_format`, format aliases.
- **Create** `mcp-server/grap_mcp/encoding/schemas.py` — per-tool table/scalar/intern schemas (start: `graph_neighbors`, `query_events`, `legal_graph_from_relations`).
- **Modify** `mcp-server/grap_mcp/server.py` — call `encode_response` at the tool-result boundary; thread a `format` arg + `tokens_saved` into `_meta`.
- **Modify** `src/bin/grap-cli.rs` — add `--format json|compact` to `run_query`/`run_export` to dogfood the codec.
- **Test** `tests/wire_roundtrip.rs`, `mcp-server/tests/test_encoding.py`.

---

## Task 1: Core codec — `encode`

**Files:**
- Create: `src/wire.rs`
- Modify: `src/lib.rs` (`pub mod wire;`)
- Test: `src/wire.rs` unit tests

- [ ] **Step 1: Write the failing test**

Create `src/wire.rs` with the test first:

```rust
//! Generic columnar, string-interned wire encoding (`#MUNCH`-style).

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_header_legend_and_rows() {
        let table = WireTable {
            tag: 'n',
            key: "neighbors",
            cols: vec!["node_id", "relation"],
            intern_cols: vec![1], // intern the "relation" column
            rows: vec![
                vec![WireValue::Int(7), WireValue::Str("cites".into())],
                vec![WireValue::Int(9), WireValue::Str("cites".into())],
            ],
        };
        let out = encode("graph_neighbors", "gn1", &[], &[table]);
        assert!(out.starts_with("#MUNCH/1 tool=graph_neighbors enc=gn1"));
        assert!(out.contains("@1=cites"));     // "cites" interned (used 2×)
        assert!(out.contains("n,7,@1"));        // row references the legend
        assert!(out.contains("__tables=n:neighbors:node_id|relation"));
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib wire::tests::encodes_header_legend_and_rows`
Expected: FAIL — types/functions undefined.

- [ ] **Step 3: Write minimal implementation**

Add to `src/wire.rs`:

```rust
use std::collections::HashMap;

/// A primitive cell value.
#[derive(Debug, Clone, PartialEq)]
pub enum WireValue {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    Null,
}

impl WireValue {
    fn render(&self) -> String {
        match self {
            WireValue::Str(s) => csv_escape(s),
            WireValue::Int(i) => i.to_string(),
            WireValue::Float(f) => format!("{}", f),
            WireValue::Bool(b) => if *b { "T".into() } else { "F".into() },
            WireValue::Null => String::new(),
        }
    }
}

/// One table: a tag, the response key it came from, ordered columns, the
/// indices of columns to string-intern, and the rows.
pub struct WireTable {
    pub tag: char,
    pub key: &'static str,
    pub cols: Vec<&'static str>,
    pub intern_cols: Vec<usize>,
    pub rows: Vec<Vec<WireValue>>,
}

/// A top-level scalar (k, value).
pub struct WireScalar {
    pub key: &'static str,
    pub value: WireValue,
}

fn csv_escape(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

/// Build the legend: strings appearing in interned columns ≥2× and saving
/// ≥1 char when replaced by `@N`.
fn build_legend(tables: &[WireTable]) -> (HashMap<String, String>, Vec<String>) {
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for t in tables {
        for r in &t.rows {
            for &ci in &t.intern_cols {
                if let Some(WireValue::Str(s)) = r.get(ci) {
                    *counts.entry(s).or_insert(0) += 1;
                }
            }
        }
    }
    let mut chosen: Vec<&str> = counts
        .iter()
        .filter(|(s, &c)| c >= 2 && s.len() > 2) // ">2" ⇒ saves ≥1 char vs "@N"
        .map(|(s, _)| *s)
        .collect();
    chosen.sort(); // deterministic legend
    let mut map = HashMap::new();
    let mut lines = Vec::new();
    for (i, s) in chosen.iter().enumerate() {
        let tok = format!("@{}", i + 1);
        lines.push(format!("{}={}", tok, s));
        map.insert((*s).to_string(), tok);
    }
    (map, lines)
}

/// Encode a response into the `#MUNCH/1` text format.
pub fn encode(tool: &str, enc_id: &str, scalars: &[WireScalar], tables: &[WireTable]) -> String {
    let (legend_map, legend_lines) = build_legend(tables);
    let mut out = String::new();
    out.push_str(&format!("#MUNCH/1 tool={} enc={}\n", tool, enc_id));
    for line in &legend_lines {
        out.push_str(line);
        out.push('\n');
    }

    // Scalar line: values + __stypes + __tables.
    let mut scalar_parts: Vec<String> = Vec::new();
    let mut stypes: Vec<String> = Vec::new();
    for s in scalars {
        scalar_parts.push(format!("{}={}", s.key, s.value.render()));
        let ty = match s.value {
            WireValue::Int(_) => Some("int"),
            WireValue::Float(_) => Some("float"),
            WireValue::Bool(_) => Some("bool"),
            _ => None,
        };
        if let Some(t) = ty { stypes.push(format!("{}:{}", s.key, t)); }
    }
    if !stypes.is_empty() {
        scalar_parts.push(format!("__stypes={}", stypes.join("|")));
    }
    let tables_schema: Vec<String> = tables
        .iter()
        .map(|t| format!("{}:{}:{}", t.tag, t.key, t.cols.join("|")))
        .collect();
    scalar_parts.push(format!("__tables={}", tables_schema.join(",")));
    out.push_str(&scalar_parts.join(" "));
    out.push('\n');

    // Table blocks.
    for t in tables {
        let intern: std::collections::HashSet<usize> = t.intern_cols.iter().copied().collect();
        for r in &t.rows {
            let mut cells = vec![t.tag.to_string()];
            for (ci, cell) in r.iter().enumerate() {
                if intern.contains(&ci) {
                    if let WireValue::Str(s) = cell {
                        if let Some(tok) = legend_map.get(s) {
                            cells.push(tok.clone());
                            continue;
                        }
                    }
                }
                cells.push(cell.render());
            }
            out.push_str(&cells.join(","));
            out.push('\n');
        }
    }
    out.trim_end().to_string()
}
```

Add `pub mod wire;` to `src/lib.rs`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib wire::tests::encodes_header_legend_and_rows`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire.rs src/lib.rs
git commit -m "feat(core): generic columnar wire encoder (grap::wire)"
```

---

## Task 2: Core codec — `decode` (round-trip)

**Files:**
- Modify: `src/wire.rs`
- Test: `tests/wire_roundtrip.rs` (new) + `src/wire.rs` unit test

- [ ] **Step 1: Write the failing test**

Create `tests/wire_roundtrip.rs`:

```rust
use grap::wire::{decode, encode, WireScalar, WireTable, WireValue};

#[test]
fn round_trip_preserves_rows_and_scalars() {
    let table = WireTable {
        tag: 'e', key: "events", cols: vec!["id", "type"],
        intern_cols: vec![1],
        rows: vec![
            vec![WireValue::Int(1), WireValue::Str("promote".into())],
            vec![WireValue::Int(2), WireValue::Str("promote".into())],
        ],
    };
    let scalars = vec![WireScalar { key: "count", value: WireValue::Int(2) }];
    let wire = encode("query_events", "qe1", &scalars, &[table]);
    let doc = decode(&wire);

    assert_eq!(doc.tool, "query_events");
    assert_eq!(doc.enc_id, "qe1");
    let events = &doc.tables["events"];
    assert_eq!(events.len(), 2);
    assert_eq!(events[1]["type"], "promote"); // legend expanded
    assert_eq!(doc.scalars["count"], "2");
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --test wire_roundtrip`
Expected: FAIL — `decode`/`DecodedDoc` undefined.

- [ ] **Step 3: Write minimal implementation**

Add to `src/wire.rs`:

```rust
use std::collections::BTreeMap;

/// Decoded document. Rows are returned as column→string maps (the decoder
/// does not re-type cells; callers apply `__stypes` if they need numerics).
pub struct DecodedDoc {
    pub tool: String,
    pub enc_id: String,
    pub scalars: BTreeMap<String, String>,
    pub tables: BTreeMap<String, Vec<BTreeMap<String, String>>>,
}

pub fn decode(payload: &str) -> DecodedDoc {
    let mut lines = payload.lines();
    let header = lines.next().unwrap_or_default();
    let tool = field(header, "tool=");
    let enc_id = field(header, "enc=");

    let mut legend: BTreeMap<String, String> = BTreeMap::new();
    let mut scalar_line = String::new();
    let mut table_lines: Vec<&str> = Vec::new();
    let mut schema: Vec<(char, String, Vec<String>)> = Vec::new();

    for line in lines {
        if let Some(rest) = line.strip_prefix('@') {
            // legend "N=value"
            if let Some((n, v)) = rest.split_once('=') {
                legend.insert(format!("@{}", n), v.to_string());
                continue;
            }
        }
        if line.contains("__tables=") && scalar_line.is_empty() {
            scalar_line = line.to_string();
            schema = parse_tables_schema(line);
            continue;
        }
        table_lines.push(line);
    }

    let mut scalars = BTreeMap::new();
    for tok in scalar_line.split(' ') {
        if tok.starts_with("__") { continue; }
        if let Some((k, v)) = tok.split_once('=') { scalars.insert(k.to_string(), v.to_string()); }
    }

    let tag_to_schema: BTreeMap<char, (String, Vec<String>)> =
        schema.into_iter().map(|(t, k, c)| (t, (k, c))).collect();
    let mut tables: BTreeMap<String, Vec<BTreeMap<String, String>>> = BTreeMap::new();
    for line in table_lines {
        let mut it = split_csv(line).into_iter();
        let tag = it.next().and_then(|s| s.chars().next());
        let tag = match tag { Some(t) => t, None => continue };
        if let Some((key, cols)) = tag_to_schema.get(&tag) {
            let mut row = BTreeMap::new();
            for (i, col) in cols.iter().enumerate() {
                let raw = it.next().unwrap_or_default();
                let val = legend.get(&raw).cloned().unwrap_or(raw);
                row.insert(col.clone(), val);
            }
            tables.entry(key.clone()).or_default().push(row);
        }
    }
    DecodedDoc { tool, enc_id, scalars, tables }
}

fn field(header: &str, key: &str) -> String {
    header.split_whitespace()
        .find_map(|t| t.strip_prefix(key))
        .unwrap_or_default().to_string()
}
fn parse_tables_schema(line: &str) -> Vec<(char, String, Vec<String>)> {
    let spec = line.split("__tables=").nth(1).unwrap_or("").split(' ').next().unwrap_or("");
    spec.split(',').filter(|s| !s.is_empty()).filter_map(|t| {
        let mut p = t.splitn(3, ':');
        let tag = p.next()?.chars().next()?;
        let key = p.next()?.to_string();
        let cols = p.next()?.split('|').map(|c| c.to_string()).collect();
        Some((tag, key, cols))
    }).collect()
}
fn split_csv(line: &str) -> Vec<String> {
    // Minimal CSV split honoring double-quoted fields produced by csv_escape.
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut in_q = false;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '"' if in_q && chars.peek() == Some(&'"') => { cur.push('"'); chars.next(); }
            '"' => in_q = !in_q,
            ',' if !in_q => { out.push(std::mem::take(&mut cur)); }
            _ => cur.push(c),
        }
    }
    out.push(cur);
    out
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --test wire_roundtrip`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire.rs tests/wire_roundtrip.rs
git commit -m "feat(core): wire decoder + round-trip test"
```

---

## Task 3: `tokens_saved` estimate helper

**Files:**
- Modify: `src/wire.rs`
- Test: `src/wire.rs` unit test

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn savings_is_positive_when_compact() {
    let raw_json_bytes = 1000;
    let saved = estimate_tokens_saved(raw_json_bytes, 250);
    assert_eq!(saved, (1000 - 250) / BYTES_PER_TOKEN); // ~ (750)/4
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib wire::tests::savings_is_positive_when_compact`
Expected: FAIL — undefined.

- [ ] **Step 3: Write minimal implementation**

```rust
/// Heuristic bytes-per-token used for savings estimates (matches the common
/// ~4 chars/token rule of thumb).
pub const BYTES_PER_TOKEN: usize = 4;

/// Estimate tokens saved by sending `compact_bytes` instead of `raw_bytes`.
pub fn estimate_tokens_saved(raw_bytes: usize, compact_bytes: usize) -> usize {
    raw_bytes.saturating_sub(compact_bytes) / BYTES_PER_TOKEN
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib wire::tests::savings_is_positive_when_compact`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wire.rs
git commit -m "feat(core): estimate_tokens_saved helper for wire encoding"
```

---

## Task 4: pygrap binding for the codec

**Files:**
- Create: `crates/pygrap/src/wire.rs`
- Modify: `crates/pygrap/src/lib.rs` (register a `wire` submodule)
- Test: `crates/pygrap/smoke_test.py`

- [ ] **Step 1: Write the failing test**

In `crates/pygrap/smoke_test.py`:

```python
import pygrap
wire = pygrap.wire.encode_tables(
    tool="graph_neighbors", enc_id="gn1",
    scalars=[("count", 2)],
    tables=[("n", "neighbors", ["node_id", "relation"], [1],
             [[7, "cites"], [9, "cites"]])],
)
assert wire.startswith("#MUNCH/1 tool=graph_neighbors enc=gn1"), wire
assert "@1=cites" in wire
print(wire)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `maturin develop --manifest-path crates/pygrap/Cargo.toml --release && python crates/pygrap/smoke_test.py`
Expected: FAIL — no `pygrap.wire`.

- [ ] **Step 3: Write minimal implementation**

`crates/pygrap/src/wire.rs` (map Python lists/tuples → `grap::wire` types):

```rust
use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};
use grap::wire::{encode, WireScalar, WireTable, WireValue};

fn py_to_value(v: &Bound<'_, PyAny>) -> PyResult<WireValue> {
    if v.is_none() { return Ok(WireValue::Null); }
    if let Ok(b) = v.extract::<bool>() { return Ok(WireValue::Bool(b)); }
    if let Ok(i) = v.extract::<i64>() { return Ok(WireValue::Int(i)); }
    if let Ok(f) = v.extract::<f64>() { return Ok(WireValue::Float(f)); }
    Ok(WireValue::Str(v.str()?.to_string()))
}

#[pyfunction]
#[pyo3(signature = (tool, enc_id, scalars, tables))]
fn encode_tables(
    tool: &str, enc_id: &str,
    scalars: &Bound<'_, PyList>, tables: &Bound<'_, PyList>,
) -> PyResult<String> {
    // scalars: list[(key:str, value)]
    let mut sc = Vec::new();
    for item in scalars.iter() {
        let t = item.downcast::<PyTuple>()?;
        let key: String = t.get_item(0)?.extract()?;
        let val = py_to_value(&t.get_item(1)?)?;
        sc.push(WireScalar { key: Box::leak(key.into_boxed_str()), value: val });
    }
    // tables: list[(tag:str, key:str, cols:[str], intern:[int], rows:[[..]])]
    let mut tbls = Vec::new();
    for item in tables.iter() {
        let t = item.downcast::<PyTuple>()?;
        let tag: String = t.get_item(0)?.extract()?;
        let key: String = t.get_item(1)?.extract()?;
        let cols: Vec<String> = t.get_item(2)?.extract()?;
        let intern: Vec<usize> = t.get_item(3)?.extract()?;
        let rows_py = t.get_item(4)?;
        let mut rows = Vec::new();
        for row in rows_py.downcast::<PyList>()?.iter() {
            let mut cells = Vec::new();
            for c in row.downcast::<PyList>()?.iter() { cells.push(py_to_value(&c)?); }
            rows.push(cells);
        }
        tbls.push(WireTable {
            tag: tag.chars().next().unwrap_or('t'),
            key: Box::leak(key.into_boxed_str()),
            cols: cols.into_iter().map(|c| &*Box::leak(c.into_boxed_str())).collect(),
            intern_cols: intern,
            rows,
        });
    }
    Ok(encode(tool, enc_id, &sc, &tbls))
}

pub fn register(parent: &Bound<'_, PyModule>) -> PyResult<()> {
    let m = PyModule::new(parent.py(), "wire")?;
    m.add_function(wrap_pyfunction!(encode_tables, &m)?)?;
    parent.add_submodule(&m)?;
    Ok(())
}
```

> **Note on `Box::leak`:** `grap::wire` types take `&'static str` for column/key names. Leaking the small, bounded set of schema strings at the FFI boundary is acceptable (schemas are fixed per tool). If you prefer zero leaks, change `WireTable.cols`/`key` and `WireScalar.key` to `String` in Task 1 — a one-line type change with no behavioural effect. **Recommended: switch to `String`** to keep the FFI clean; update Task 1's structs accordingly before doing Task 4.

Register in `crates/pygrap/src/lib.rs` `pygrap(...)`: `crate::wire::register(m)?;` and `mod wire;`.

- [ ] **Step 4: Run test to verify it passes**

Run: `maturin develop --manifest-path crates/pygrap/Cargo.toml --release && python crates/pygrap/smoke_test.py`
Expected: PASS, prints the encoded block.

- [ ] **Step 5: Commit**

```bash
git add crates/pygrap/src/wire.rs crates/pygrap/src/lib.rs crates/pygrap/smoke_test.py
git commit -m "feat(pygrap): expose pygrap.wire.encode_tables"
```

---

## Task 5: MCP schemas + `encode_response` (skin)

**Files:**
- Create: `mcp-server/grap_mcp/encoding/__init__.py`, `mcp-server/grap_mcp/encoding/schemas.py`
- Test: `mcp-server/tests/test_encoding.py` (new)

- [ ] **Step 1: Write the failing test**

Create `mcp-server/tests/test_encoding.py`:

```python
from grap_mcp.encoding import encode_response, default_format

def test_graph_neighbors_compact_interns_relation():
    response = {
        "neighbors": [
            {"node_id": 7, "relation": "cites"},
            {"node_id": 9, "relation": "cites"},
        ],
        "count": 2,
    }
    payload, meta = encode_response("graph_neighbors", response, requested_format="compact")
    assert isinstance(payload, str)
    assert payload.startswith("#MUNCH/1 tool=graph_neighbors")
    assert "@1=cites" in payload
    assert meta["tokens_saved"] >= 0

def test_json_is_default_passthrough():
    response = {"neighbors": [], "count": 0}
    payload, meta = encode_response("graph_neighbors", response, requested_format=None)
    assert payload == response  # JSON default until decoder ships
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server && python -m pytest tests/test_encoding.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

`mcp-server/grap_mcp/encoding/schemas.py`:

```python
"""Per-tool wire schemas. tag, response-key, columns, intern-column indices."""
SCHEMAS = {
    "graph_neighbors": {
        "enc_id": "gn1",
        "scalars": ["count"],
        "tables": [("n", "neighbors", ["node_id", "relation"], [1])],
    },
    "query_events": {
        "enc_id": "qe1",
        "scalars": ["count"],
        "tables": [("e", "events", ["id", "event_type", "subject", "ts"], [1])],
    },
    "legal_graph_from_relations": {
        "enc_id": "lg1",
        "scalars": ["node_count", "edge_count"],
        "tables": [("g", "edges", ["source", "target", "relation", "strength"], [2])],
    },
}
```

`mcp-server/grap_mcp/encoding/__init__.py`:

```python
import json
import pygrap  # provides pygrap.wire.encode_tables
from .schemas import SCHEMAS

_FORMATS = ("auto", "compact", "json")

def default_format(repo=None) -> str:
    return "json"  # flip to "auto" once a client decoder is deployed

def _normalize(fmt, fallback):
    if fmt is None:
        return fallback
    fmt = fmt.lower()
    return fmt if fmt in _FORMATS else fallback

def encode_response(tool_name, response, requested_format=None, repo=None):
    """Return (payload, meta). payload is the original dict for json, or the
    #MUNCH string for compact. meta always carries tokens_saved."""
    fmt = _normalize(requested_format, default_format(repo))
    schema = SCHEMAS.get(tool_name)
    if fmt == "json" or schema is None:
        return response, {"format": "json", "tokens_saved": 0}

    raw_bytes = len(json.dumps(response, separators=(",", ":")))
    scalars = [(k, response[k]) for k in schema["scalars"] if k in response]
    tables = []
    for tag, key, cols, intern in schema["tables"]:
        rows = [[row.get(c) for c in cols] for row in response.get(key, []) or []]
        tables.append((tag, key, cols, intern, rows))

    payload = pygrap.wire.encode_tables(
        tool=tool_name, enc_id=schema["enc_id"], scalars=scalars, tables=tables,
    )
    compact_bytes = len(payload)
    tokens_saved = max(0, (raw_bytes - compact_bytes) // 4)
    return payload, {"format": "compact", "tokens_saved": tokens_saved}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp-server && python -m pytest tests/test_encoding.py -q`
Expected: PASS (requires `maturin develop` from Task 4 first so `pygrap.wire` exists).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/grap_mcp/encoding mcp-server/tests/test_encoding.py
git commit -m "feat(mcp): per-tool wire schemas + encode_response (json default)"
```

---

## Task 6: Wire it into the MCP server boundary

**Files:**
- Modify: `mcp-server/grap_mcp/server.py` (the tool-dispatch/result path)
- Test: extend `mcp-server/tests/test_encoding.py`

- [ ] **Step 1: Write the failing test**

Add a test that calls the server's tool handler for `graph_neighbors` with `format="compact"` and asserts the result is a `#MUNCH` string with a `tokens_saved` field in `_meta`. (Use the existing server test harness/fixtures; mirror how other `server.py` tools are tested.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server && python -m pytest tests/test_encoding.py -q -k server`
Expected: FAIL — server ignores `format`.

- [ ] **Step 3: Write minimal implementation**

At the point where `server.py` returns a tool result dict, route it through `encode_response`, threading an optional `format` argument (default `None` → json):

```python
from grap_mcp.encoding import encode_response

# ...after computing `result` (a dict) for `tool_name`:
payload, enc_meta = encode_response(tool_name, result, requested_format=arguments.get("format"))
if isinstance(payload, dict):
    payload.setdefault("_meta", {}).update(enc_meta)
    return payload
# compact: return the string; attach savings via the transport's meta channel
return payload
```

Add an optional `"format"` parameter (`enum: auto|compact|json`) to the input schema of the three piloted tools only. Leave all other tools untouched (json).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp-server && python -m pytest tests/test_encoding.py -q`
Run: `cd mcp-server && python -m pytest -q` (full MCP suite — nothing else should change since json is default)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_encoding.py
git commit -m "feat(mcp): opt-in compact encoding for graph_neighbors/query_events/legal_graph"
```

---

## Task 7: Dogfood in the CLI (`--format compact`)

**Files:**
- Modify: `src/bin/grap-cli.rs` (`run_query` ~line 254, `run_export` ~line 488)
- Test: manual + a small `tests/` assertion if a CLI test harness exists

- [ ] **Step 1: Write the failing test**

If `src/bin/grap-cli.rs` has integration coverage, add a test asserting `grap-cli query --format compact` output starts with `#MUNCH/1`. Otherwise, add a `--format` arg and verify manually in Step 4.

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo run --bin grap-cli -- --graph-dir <dir> query "x" --format compact`
Expected: error — unknown flag `--format`.

- [ ] **Step 3: Write minimal implementation**

Add a `--format json|compact` arg to `QueryArgs`/`ExportArgs`. When `compact`, build `WireTable`s from the query results (one table tag `r`, columns `node_id|label|score`, intern `label`) and print `grap::wire::encode(...)`; else keep the current JSON/text output.

- [ ] **Step 4: Run to verify**

Run: `cargo run --bin grap-cli -- --graph-dir <dir> query "memory" --format compact`
Expected: prints a `#MUNCH/1 tool=query ...` block.

- [ ] **Step 5: Commit**

```bash
git add src/bin/grap-cli.rs
git commit -m "feat(cli): grap-cli query/export --format compact (dogfood grap::wire)"
```

---

## Self-Review

- **Spec coverage:** core encode (T1), decode/round-trip (T2), savings (T3), pygrap binding (T4), MCP schemas+encode_response (T5), server boundary (T6), CLI dogfood (T7). Covered.
- **Type consistency:** `WireValue`/`WireTable`/`WireScalar` identical across T1–T4; `encode(tool, enc_id, scalars, tables)` signature stable; `decode → DecodedDoc{tool,enc_id,scalars,tables}` matches T2 test; Python `encode_tables(tool, enc_id, scalars, tables)` mirrors it. **Action item flagged in T4:** prefer `String` over `&'static str` in `WireTable`/`WireScalar` to avoid FFI leaks — apply to T1 before T4.
- **Safety / reversibility:** JSON is the default everywhere (`default_format → "json"`); only three tools opt in; nothing changes for existing clients until they request `compact` and ship the decoder. The bytes-per-token constant (4) is shared core↔skin so savings numbers are consistent.
- **Mission fit:** the perf-sensitive generic codec is in the core and reused by CLI + pygrap + MCP; only schema definitions and negotiation live in the skin — exactly the core/skin split you asked for.
