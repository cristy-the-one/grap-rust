//! Generic columnar, string-interned wire encoding (`#MUNCH`-style).
//!
//! A tool result is re-encoded from a row/scalar shape into a compact,
//! self-describing text payload: a header naming the tool + schema id, a
//! legend interning repeated strings (`@1`, `@2`, …), one scalar line carrying
//! `__stypes` (type hints) and `__tables` (the table schema, so a decoder needs
//! no out-of-band schema), then tag-prefixed CSV rows. The types own their
//! strings so the PyO3 binding stays allocation-clean (no `Box::leak`).

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
            WireValue::Bool(b) => {
                if *b {
                    "T".into()
                } else {
                    "F".into()
                }
            }
            WireValue::Null => String::new(),
        }
    }
}

/// One table: a 1-char tag, the response key it came from, ordered column
/// names, the indices of columns to string-intern, and the rows.
#[derive(Debug, Clone)]
pub struct WireTable {
    pub tag: char,
    pub key: String,
    pub cols: Vec<String>,
    pub intern_cols: Vec<usize>,
    pub rows: Vec<Vec<WireValue>>,
}

/// A top-level scalar (key, value).
#[derive(Debug, Clone)]
pub struct WireScalar {
    pub key: String,
    pub value: WireValue,
}

fn csv_escape(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

/// Build the legend: strings appearing in interned columns >=2x and saving
/// >=1 char when replaced by `@N` (so len > 2, since "@N" is 2 chars).
fn build_legend(tables: &[WireTable]) -> (HashMap<String, String>, Vec<String>) {
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for t in tables {
        for r in &t.rows {
            for &ci in &t.intern_cols {
                if let Some(WireValue::Str(s)) = r.get(ci) {
                    *counts.entry(s.as_str()).or_insert(0) += 1;
                }
            }
        }
    }
    let mut chosen: Vec<&str> = counts
        .iter()
        .filter(|(s, &c)| c >= 2 && s.len() > 2)
        .map(|(s, _)| *s)
        .collect();
    chosen.sort_unstable(); // deterministic legend numbering
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

    // Scalar line: values + __stypes + __tables (so decode is self-sufficient).
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
        if let Some(t) = ty {
            stypes.push(format!("{}:{}", s.key, t));
        }
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

    // Table blocks: CSV rows prefixed by the 1-char tag; interned cells as @N.
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

use std::collections::BTreeMap;

/// Decoded document. Rows are column→string maps; cells are not re-typed
/// (callers may consult `scalars`/`__stypes` if they need numerics).
#[derive(Debug, Clone)]
pub struct DecodedDoc {
    /// Tool name from the `#MUNCH/1` header.
    pub tool: String,
    /// Encoding id from the `#MUNCH/1` header.
    pub enc_id: String,
    /// Top-level scalar key→value pairs (values as strings).
    pub scalars: BTreeMap<String, String>,
    /// Table rows keyed by the table's response key; each row is a column→value map.
    pub tables: BTreeMap<String, Vec<BTreeMap<String, String>>>,
}

/// Reverse `encode`: parse a `#MUNCH/1` payload back into a [`DecodedDoc`].
pub fn decode(payload: &str) -> DecodedDoc {
    let mut lines = payload.lines();
    let header = lines.next().unwrap_or_default();
    let tool = header_field(header, "tool=");
    let enc_id = header_field(header, "enc=");

    let mut legend: BTreeMap<String, String> = BTreeMap::new();
    let mut scalar_line = String::new();
    let mut table_lines: Vec<&str> = Vec::new();
    let mut schema: Vec<(char, String, Vec<String>)> = Vec::new();

    for line in lines {
        // legend line "@N=value"
        if let Some(rest) = line.strip_prefix('@') {
            if let Some((n, v)) = rest.split_once('=') {
                legend.insert(format!("@{}", n), v.to_string());
                continue;
            }
        }
        // the single scalar line carries __tables=
        if scalar_line.is_empty() && line.contains("__tables=") {
            scalar_line = line.to_string();
            schema = parse_tables_schema(line);
            continue;
        }
        table_lines.push(line);
    }

    let mut scalars = BTreeMap::new();
    for tok in scalar_line.split(' ') {
        if tok.starts_with("__") {
            continue;
        }
        if let Some((k, v)) = tok.split_once('=') {
            scalars.insert(k.to_string(), v.to_string());
        }
    }

    let tag_to_schema: BTreeMap<char, (String, Vec<String>)> =
        schema.into_iter().map(|(t, k, c)| (t, (k, c))).collect();
    let mut tables: BTreeMap<String, Vec<BTreeMap<String, String>>> = BTreeMap::new();
    for line in table_lines {
        if line.is_empty() {
            continue;
        }
        let mut it = split_csv(line).into_iter();
        let tag = match it.next().and_then(|s| s.chars().next()) {
            Some(t) => t,
            None => continue,
        };
        if let Some((key, cols)) = tag_to_schema.get(&tag) {
            let mut row = BTreeMap::new();
            for col in cols {
                let raw = it.next().unwrap_or_default();
                let val = legend.get(&raw).cloned().unwrap_or(raw);
                row.insert(col.clone(), val);
            }
            tables.entry(key.clone()).or_default().push(row);
        }
    }
    DecodedDoc { tool, enc_id, scalars, tables }
}

fn header_field(header: &str, key: &str) -> String {
    header
        .split_whitespace()
        .find_map(|t| t.strip_prefix(key))
        .unwrap_or_default()
        .to_string()
}

fn parse_tables_schema(line: &str) -> Vec<(char, String, Vec<String>)> {
    let spec = line
        .split("__tables=")
        .nth(1)
        .unwrap_or("")
        .split(' ')
        .next()
        .unwrap_or("");
    spec.split(',')
        .filter(|s| !s.is_empty())
        .filter_map(|t| {
            let mut p = t.splitn(3, ':');
            let tag = p.next()?.chars().next()?;
            let key = p.next()?.to_string();
            let cols = p.next()?.split('|').map(|c| c.to_string()).collect();
            Some((tag, key, cols))
        })
        .collect()
}

/// Minimal CSV split honoring the double-quoted fields `csv_escape` produces.
fn split_csv(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut in_q = false;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '"' if in_q && chars.peek() == Some(&'"') => {
                cur.push('"');
                chars.next();
            }
            '"' => in_q = !in_q,
            ',' if !in_q => out.push(std::mem::take(&mut cur)),
            _ => cur.push(c),
        }
    }
    out.push(cur);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_header_legend_and_rows() {
        let table = WireTable {
            tag: 'n',
            key: "neighbors".to_string(),
            cols: vec!["node_id".to_string(), "relation".to_string()],
            intern_cols: vec![1], // intern the "relation" column
            rows: vec![
                vec![WireValue::Int(7), WireValue::Str("cites".into())],
                vec![WireValue::Int(9), WireValue::Str("cites".into())],
            ],
        };
        let out = encode("graph_neighbors", "gn1", &[], &[table]);
        assert!(out.starts_with("#MUNCH/1 tool=graph_neighbors enc=gn1"));
        assert!(out.contains("@1=cites")); // "cites" interned (used 2x)
        assert!(out.contains("n,7,@1")); // row references the legend
        assert!(out.contains("__tables=n:neighbors:node_id|relation"));
    }
}
