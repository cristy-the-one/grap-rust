//! Search Crab Cage evidence without joining its compilation/scoring hot path.
//! cargo run --example crab_memory -- /path/to/crab-out "L0113 comparison"

use std::collections::HashMap;
use std::error::Error;
use std::fs;
use std::path::Path;

use grap::{FusionWeights, IndexedKnowledgeGraph, NodeId, PropertyValue};
use serde::Deserialize;

#[derive(Deserialize)]
struct Record {
    hash: String,
    status: String,
    #[serde(default)]
    gate: String,
    #[serde(default)]
    proposer: String,
    #[serde(default)]
    note: String,
    #[serde(default)]
    score: Option<f64>,
    #[serde(default)]
    diagnostics: Vec<Diagnostic>,
}

#[derive(Deserialize)]
struct Diagnostic {
    code: String,
    message: String,
}

fn import(out: &Path) -> Result<IndexedKnowledgeGraph, Box<dyn Error>> {
    let mut graph = IndexedKnowledgeGraph::new();
    let evidence = graph.graph.properties.register("evidence");
    let mut codes: HashMap<String, NodeId> = HashMap::new();
    let text = fs::read_to_string(out.join("journal.jsonl"))?;
    let lines: Vec<_> = text.split('\n').collect();
    for (index, line) in lines.iter().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let record: Record = match serde_json::from_str(line) {
            Ok(record) => record,
            // Crab Cage permits an incomplete final record after a crash.
            Err(_) if index + 1 == lines.len() && !text.ends_with('\n') => break,
            Err(error) => return Err(format!("journal line {}: {error}", index + 1).into()),
        };
        // Hashes are filenames supplied by an external journal. Never let
        // a malformed record select a file outside this run's work directory.
        if record.hash.len() != 16 || !record.hash.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(format!("invalid candidate hash on line {}", index + 1).into());
        }
        let source_path = out.join("work").join(format!("{}.rs", record.hash));
        let source = match fs::read_to_string(&source_path) {
            Ok(source) => source,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(error) => return Err(error.into()),
        };
        let diagnostic_text = record
            .diagnostics
            .iter()
            .map(|d| format!("{} {}", d.code, d.message))
            .collect::<Vec<_>>()
            .join("\n");
        let candidate = graph.add_node(
            &format!("run: {}\njournal line: {}\nhash: {}\njournal status: {}\nscore: {:?}\ngate: {}\nproposer: {}\nnote: {}\nsource: {}\n{}\n{}",
                out.display(), index + 1, record.hash, record.status, record.score,
                record.gate, record.proposer, record.note,
                source_path.display(), source, diagnostic_text),
            Some(&record.hash), None,
        );
        for diagnostic in record.diagnostics {
            let code = *codes.entry(diagnostic.code.clone()).or_insert_with(|| {
                graph.add_node(
                    &format!("diagnostic {}", diagnostic.code),
                    Some(&diagnostic.code),
                    None,
                )
            });
            let props = || vec![(evidence, PropertyValue::Label("shares_diagnostic".into()))];
            graph.add_edge(code, candidate, 1.0, props());
            graph.add_edge(candidate, code, 1.0, props());
        }
    }
    Ok(graph)
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut args = std::env::args().skip(1);
    let out = args
        .next()
        .ok_or("usage: crab_memory RUN_DIRECTORY QUERY...")?;
    let query = args.collect::<Vec<_>>().join(" ");
    if query.is_empty() {
        return Err("supply a search query".into());
    }
    let graph = import(Path::new(&out))?;
    for result in graph.search_and_traverse(
        &query,
        5,
        &["evidence"],
        0.0,
        2,
        10,
        FusionWeights::default(),
    ) {
        println!("relevance: {:.4}\n{}\n", result.fused_score, result.text);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retrieves_real_source_and_related_diagnostic_evidence() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("work")).unwrap();
        fs::write(
            dir.path().join("work/0123456789abcdef.rs"),
            "fn compare_unique_marker() {}",
        )
        .unwrap();
        fs::write(dir.path().join("journal.jsonl"), concat!(
            "{\"hash\":\"0123456789abcdef\",\"status\":\"rejected\",\"diagnostics\":[{\"code\":\"L0113\",\"message\":\"comparison needs dereference\"}]}\n",
            "{\"hash\":\"fedcba9876543210\",\"status\":\"proven\",\"score\":0}\n{\"hash\":"
        )).unwrap();
        let graph = import(dir.path()).unwrap();
        let hits = graph.search("compare_unique_marker", 5);
        assert_eq!(hits.len(), 1);
        assert_eq!(
            graph.graph.node_label(hits[0].node_id),
            Some("0123456789abcdef")
        );
        let related = graph.search_and_traverse(
            "L0113",
            5,
            &["evidence"],
            0.0,
            2,
            10,
            FusionWeights::default(),
        );
        assert!(related
            .iter()
            .any(|r| r.text.contains("comparison needs dereference")));
        assert!(!related.iter().any(|r| r.text.contains("fedcba9876543210")));
    }

    #[test]
    fn refuses_hashes_that_could_escape_the_work_directory() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("journal.jsonl"),
            "{\"hash\":\"../../secret\",\"status\":\"proven\"}\n",
        )
        .unwrap();
        assert!(import(dir.path()).is_err());
    }
}
