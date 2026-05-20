//! grap-cli — Command-line interface for the grap knowledge graph engine.
//!
//! This binary provides a practical way to interact with grap graphs from scripts
//! (especially Python) and from the terminal during demos.
//!
//! Design goals for the CLI:
//! - Operate exclusively on the directory format produced by `save_to_dir`/`load_from_dir`.
//! - Be script-friendly: structured output (JSON) + human-friendly colored output.
//! - Support both single-graph (`IndexedKnowledgeGraph`) and sharded (`ShardedGraph`) use cases.
//! - Minimal new dependencies (only clap for argument parsing).

use clap::{Parser, Subcommand};
use std::path::PathBuf;

use grap::{IndexedKnowledgeGraph, SegmentInfo, ShardedGraph};

#[derive(Parser)]
#[command(
    name = "grap-cli",
    version,
    about = "CLI for the grap knowledge graph engine (demo & scripting interface)",
    long_about = "Use this tool from Python scripts or the terminal to ingest, query, inspect, \
                  and maintain grap knowledge graphs. All state lives in a directory created by \
                  save_to_dir / load_from_dir."
)]
struct Cli {
    /// Path to the graph directory.
    ///
    /// This directory is created and managed by grap's save_to_dir/load_from_dir APIs.
    /// The CLI will auto-detect whether it contains a single graph or a sharded cluster.
    #[arg(long, global = true, value_name = "DIR")]
    graph_dir: Option<PathBuf>,

    /// Output results as JSON (ideal for Python scripts).
    #[arg(long, global = true)]
    json: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Show statistics about the graph (node/edge counts, segment info, etc.)
    Stats(StatsArgs),

    /// Run a text search (and optionally a graph traversal) against the knowledge graph.
    Query(QueryArgs),
    // Future commands (Phase 2+):
    // Ingest(IngestArgs),
    // DeleteNode(DeleteArgs),
    // Vacuum(VacuumArgs),
    // Rebalance(RebalanceArgs),
    // AddFile(AddFileArgs),
}

/// Arguments for the `stats` subcommand.
#[derive(Parser)]
struct StatsArgs {
    /// Show detailed per-segment information (live counts, sizes, delete ratios).
    #[arg(long)]
    segments: bool,
}

/// Arguments for the `query` subcommand.
#[derive(Parser)]
struct QueryArgs {
    /// The search query (supports phrase search when quoted).
    query: String,

    /// Also perform a graph traversal from the text seeds.
    #[arg(long)]
    traverse: bool,

    /// Maximum number of text search results to consider.
    #[arg(long, default_value_t = 10)]
    max_results: usize,
}

fn main() {
    let cli = Cli::parse();

    let graph_dir = cli
        .graph_dir
        .expect("--graph-dir is required for this command");

    match cli.command {
        Commands::Stats(args) => run_stats(&graph_dir, cli.json, args),
        Commands::Query(args) => run_query(&graph_dir, cli.json, args),
    }
}

// ---------------------------------------------------------------------------
// Command implementations
// ---------------------------------------------------------------------------

fn run_stats(graph_dir: &PathBuf, json: bool, args: StatsArgs) {
    // Try to load as a single IndexedKnowledgeGraph first (most common case for demos).
    match IndexedKnowledgeGraph::load_from_dir(graph_dir) {
        Ok(ig) => {
            let node_count = ig.graph.node_count();
            let edge_count = ig.graph.edge_count();

            let segment_infos: Vec<SegmentInfo> = ig.text_index_segment_info();

            if json {
                // Structured output for Python consumption
                let output = serde_json::json!({
                    "type": "single",
                    "node_count": node_count,
                    "edge_count": edge_count,
                    "segment_count": segment_infos.len(),
                    "segments": if args.segments {
                        Some(segment_infos.iter().map(|s| {
                            serde_json::json!({
                                "doc_count": s.doc_count,
                                "live_doc_count": s.live_doc_count,
                                "tier": s.tier,
                                "size_bytes": s.size_bytes,
                            })
                        }).collect::<Vec<_>>())
                    } else {
                        None
                    }
                });
                println!("{}", serde_json::to_string_pretty(&output).unwrap());
            } else {
                // Human-friendly output
                println!("=== grap Graph Statistics ===");
                println!("Graph directory : {:?}", graph_dir);
                println!("Type            : Single IndexedKnowledgeGraph (on-disk capable)");
                println!("Nodes           : {}", node_count);
                println!("Edges           : {}", edge_count);
                println!("Text segments   : {}", segment_infos.len());

                if args.segments && !segment_infos.is_empty() {
                    println!("\nSegment details:");
                    for (i, seg) in segment_infos.iter().enumerate() {
                        let delete_ratio = if seg.doc_count > 0 {
                            (seg.doc_count - seg.live_doc_count) as f32 / seg.doc_count as f32
                        } else {
                            0.0
                        };
                        println!(
                            "  [{:02}] tier={}  docs={} (live={})  size≈{:.1} KB  delete_ratio={:.1}%",
                            i,
                            seg.tier,
                            seg.doc_count,
                            seg.live_doc_count,
                            seg.size_bytes as f64 / 1024.0,
                            delete_ratio * 100.0
                        );
                    }
                }
            }
        }
        Err(_) => {
            // Fall back to trying as a ShardedGraph
            match ShardedGraph::load_from_dir(graph_dir) {
                Ok(_sg) => {
                    // For now we give a high-level view; richer sharded stats can come later.
                    if json {
                        let output = serde_json::json!({
                            "type": "sharded",
                            "message": "ShardedGraph stats not fully implemented in CLI yet",
                        });
                        println!("{}", serde_json::to_string_pretty(&output).unwrap());
                    } else {
                        println!("Loaded as ShardedGraph (sharded stats coming soon)");
                        println!("Directory: {:?}", graph_dir);
                    }
                }
                Err(e) => {
                    eprintln!("Failed to load graph from {:?}: {}", graph_dir, e);
                    std::process::exit(1);
                }
            }
        }
    }
}

fn run_query(graph_dir: &PathBuf, json: bool, args: QueryArgs) {
    // Load the graph (single-graph path for the first version of the demo)
    let ig = match IndexedKnowledgeGraph::load_from_dir(graph_dir) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("Could not load graph: {}", e);
            std::process::exit(1);
        }
    };

    if args.traverse {
        // Hybrid text + graph traversal path
        let results = ig.search_and_traverse(
            &args.query,
            args.max_results,
            &[], // no required properties for the basic CLI
            0.0, // min strength
            3,   // depth
            20,  // final max results
            grap::FusionWeights::default(),
        );

        if json {
            let output: Vec<_> = results
                .iter()
                .map(|r| {
                    serde_json::json!({
                        "node_id": r.graph_result.node_id.0,
                        "text_score": r.text_score,
                        "graph_strength": r.graph_strength,
                        "fused_score": r.fused_score,
                        "text_snippet": r.text.chars().take(200).collect::<String>(),
                        "path": r.graph_result.path.iter().map(|e| e.0).collect::<Vec<_>>(),
                    })
                })
                .collect();
            println!("{}", serde_json::to_string_pretty(&output).unwrap());
        } else {
            println!("Query (with traversal): \"{}\"", args.query);
            println!("Results ({}):", results.len());
            for (i, r) in results.iter().enumerate() {
                let snippet: String = r.text.chars().take(160).collect();
                println!(
                    "  [{:02}] fused={:.3}  node={}  {}",
                    i,
                    r.fused_score,
                    r.graph_result.node_id.0,
                    snippet.replace('\n', " ")
                );
            }
        }
    } else {
        // Plain text search
        let results = ig.search(&args.query, args.max_results);

        if json {
            let output: Vec<_> = results
                .iter()
                .map(|r| {
                    let text = ig.graph.read_blob(r.node_id);
                    serde_json::json!({
                        "node_id": r.node_id.0,
                        "score": r.score,
                        "text_snippet": text.chars().take(200).collect::<String>(),
                    })
                })
                .collect();
            println!("{}", serde_json::to_string_pretty(&output).unwrap());
        } else {
            println!("Query: \"{}\"", args.query);
            println!("Results ({}):", results.len());
            for (i, r) in results.iter().enumerate() {
                let text = ig.graph.read_blob(r.node_id);
                let snippet: String = text.chars().take(160).collect();
                println!(
                    "  [{:02}] score={:.3}  node={}  {}",
                    i,
                    r.score,
                    r.node_id.0,
                    snippet.replace('\n', " ")
                );
            }
        }
    }
}
