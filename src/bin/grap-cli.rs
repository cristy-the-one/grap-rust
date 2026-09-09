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
use std::path::{Path, PathBuf};

use grap::{ids::NodeId, IndexedKnowledgeGraph, SegmentInfo, ShardedGraph};

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

    /// Add a file as a new node in the knowledge graph.
    AddFile(AddFileArgs),

    /// Delete a node (creates a tombstone; use `vacuum` to reclaim space).
    DeleteNode(DeleteNodeArgs),

    /// Run incremental vacuum to reclaim space from deleted documents.
    Vacuum(VacuumArgs),

    /// Export the graph as JSON (nodes + edges) for visualization tools.
    Export(ExportArgs),

    /// Add an edge between two existing nodes.
    AddEdge(AddEdgeArgs),

    /// Keep background merge/vacuum workers running for a graph (useful for demos).
    StartWorkers(WorkerArgs),
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

/// Arguments for `add-file`.
#[derive(Parser)]
struct AddFileArgs {
    /// Path to the file to ingest as a node.
    #[arg(long, short)]
    path: PathBuf,

    /// Optional human-readable label for the node.
    #[arg(long)]
    label: Option<String>,
}

/// Arguments for `delete-node`.
#[derive(Parser)]
struct DeleteNodeArgs {
    /// The internal NodeId (u32) of the node to delete.
    node_id: u32,
}

/// Arguments for `vacuum`.
#[derive(Parser)]
struct VacuumArgs {
    /// Show segment information before and after vacuuming.
    #[arg(long)]
    show_segments: bool,
}

/// Arguments for `export`.
#[derive(Parser)]
struct ExportArgs {
    /// Output format (currently only 'json' is supported).
    #[arg(long, default_value = "json")]
    format: String,
}

/// Arguments for `add-edge`.
#[derive(Parser)]
struct AddEdgeArgs {
    #[arg(long)]
    source: u32,
    #[arg(long)]
    target: u32,
    #[arg(long, default_value_t = 0.5)]
    strength: f64,
}

/// Arguments for the `start-workers` subcommand (keeps merge/vacuum threads alive).
#[derive(Parser)]
struct WorkerArgs {
    /// How often the background worker wakes up to check for merges/vacuum (in milliseconds).
    #[arg(long, default_value_t = 1500)]
    interval: u64,
}

fn main() {
    let cli = Cli::parse();

    let graph_dir = cli
        .graph_dir
        .expect("--graph-dir is required for this command");

    match cli.command {
        Commands::Stats(args) => run_stats(&graph_dir, cli.json, args),
        Commands::Query(args) => run_query(&graph_dir, cli.json, args),
        Commands::AddFile(args) => run_add_file(&graph_dir, cli.json, args),
        Commands::DeleteNode(args) => run_delete_node(&graph_dir, cli.json, args),
        Commands::Vacuum(args) => run_vacuum(&graph_dir, cli.json, args),
        Commands::Export(args) => run_export(&graph_dir, cli.json, args),
        Commands::AddEdge(args) => run_add_edge(&graph_dir, cli.json, args),
        Commands::StartWorkers(args) => run_start_workers(&graph_dir, args),
    }
}

// ---------------------------------------------------------------------------
// Command implementations
// ---------------------------------------------------------------------------

fn run_stats(graph_dir: &Path, json: bool, args: StatsArgs) {
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

fn run_query(graph_dir: &Path, json: bool, args: QueryArgs) {
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

// ---------------------------------------------------------------------------
// Mutation commands (for live demo scenarios)
// ---------------------------------------------------------------------------

/// Creates a fresh on-disk graph, optionally with a tiered merge policy
/// if the `GRAP_MERGE_POLICY=<n>` environment variable is set (e.g. 3 or 4).
fn create_on_disk_graph() -> IndexedKnowledgeGraph {
    if let Ok(val) = std::env::var("GRAP_MERGE_POLICY") {
        if let Ok(n) = val.parse::<usize>() {
            if n > 0 {
                return IndexedKnowledgeGraph::new_on_disk_with_merge_policy(n);
            }
        }
    }
    IndexedKnowledgeGraph::new_on_disk()
}

fn run_add_file(graph_dir: &Path, json: bool, args: AddFileArgs) {
    // Ensure the target directory exists (harmless if it already does).
    if let Err(e) = std::fs::create_dir_all(graph_dir) {
        eprintln!("Failed to create graph directory {:?}: {}", graph_dir, e);
        std::process::exit(1);
    }

    let mut ig = match IndexedKnowledgeGraph::load_from_dir(graph_dir) {
        Ok(g) => g,
        Err(_) => {
            // Directory is new or empty — create a fresh on-disk graph.
            // Respect GRAP_MERGE_POLICY=<n> to enable tiered background merging (demo / advanced use).
            create_on_disk_graph()
        }
    };

    let content = match std::fs::read_to_string(&args.path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Failed to read file {:?}: {}", args.path, e);
            std::process::exit(1);
        }
    };

    let label = args.label.clone().or_else(|| {
        args.path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
    });

    let node_id = ig.add_node(&content, label.as_deref(), None);

    if let Err(e) = ig.save_to_dir(graph_dir) {
        eprintln!("Failed to save after adding node: {}", e);
        std::process::exit(1);
    }

    if json {
        println!(
            "{}",
            serde_json::json!({
                "status": "ok",
                "node_id": node_id.0,
                "label": label,
                "file": args.path.to_string_lossy(),
            })
        );
    } else {
        println!(
            "Added node {} from {:?} (label: {:?})",
            node_id.0, args.path, label
        );
    }
}

fn run_delete_node(graph_dir: &Path, json: bool, args: DeleteNodeArgs) {
    let mut ig = match IndexedKnowledgeGraph::load_from_dir(graph_dir) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("Failed to load graph: {}", e);
            std::process::exit(1);
        }
    };

    let node_id = grap::NodeId(args.node_id);

    ig.remove_node(node_id);

    if let Err(e) = ig.save_to_dir(graph_dir) {
        eprintln!("Failed to save after delete: {}", e);
        std::process::exit(1);
    }

    if json {
        println!(
            "{}",
            serde_json::json!({ "status": "deleted", "node_id": args.node_id })
        );
    } else {
        println!(
            "Deleted node {} (tombstone created). Run `vacuum` to reclaim space.",
            args.node_id
        );
    }
}

fn run_vacuum(graph_dir: &Path, json: bool, args: VacuumArgs) {
    let mut ig = match IndexedKnowledgeGraph::load_from_dir(graph_dir) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("Failed to load graph: {}", e);
            std::process::exit(1);
        }
    };

    let before = if args.show_segments {
        ig.text_index_segment_info()
    } else {
        vec![]
    };

    ig.vacuum_deleted_nodes();

    let after = if args.show_segments {
        ig.text_index_segment_info()
    } else {
        vec![]
    };

    if let Err(e) = ig.save_to_dir(graph_dir) {
        eprintln!("Failed to save after vacuum: {}", e);
        std::process::exit(1);
    }

    if json {
        println!(
            "{}",
            serde_json::json!({
                "status": "ok",
                "before_segments": before.len(),
                "after_segments": after.len(),
            })
        );
    } else {
        println!("Vacuum complete.");
        if args.show_segments {
            println!("Segments before: {}, after: {}", before.len(), after.len());
        }
    }
}

fn run_export(graph_dir: &Path, json: bool, args: ExportArgs) {
    let ig = match IndexedKnowledgeGraph::load_from_dir(graph_dir) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("Failed to load graph: {}", e);
            std::process::exit(1);
        }
    };

    let node_count = ig.graph.node_count();

    let mut nodes = Vec::new();
    for i in 0..node_count {
        let id = NodeId(i as u32);
        let label = ig.graph.node_label(id).map(|s| s.to_string());
        let text = ig.graph.read_blob(id);
        let snippet: String = text.chars().take(120).collect();

        nodes.push(serde_json::json!({
            "id": i,
            "label": label,
            "snippet": snippet,
        }));
    }

    let mut edges = Vec::new();
    for edge in ig.all_edges().iter() {
        edges.push(serde_json::json!({
            "source": edge.source.0,
            "target": edge.target.0,
            "strength": edge.strength,
        }));
    }

    let output = serde_json::json!({
        "node_count": node_count,
        "edge_count": ig.graph.edge_count(),
        "nodes": nodes,
        "edges": edges,
    });

    if json || args.format == "json" {
        println!("{}", serde_json::to_string_pretty(&output).unwrap());
    } else {
        println!("Only JSON format is supported at the moment.");
    }
}

fn run_add_edge(graph_dir: &Path, json: bool, args: AddEdgeArgs) {
    let mut ig = match IndexedKnowledgeGraph::load_from_dir(graph_dir) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("Failed to load graph: {}", e);
            std::process::exit(1);
        }
    };

    let source = NodeId(args.source);
    let target = NodeId(args.target);

    ig.add_edge(source, target, args.strength, vec![]);

    if let Err(e) = ig.save_to_dir(graph_dir) {
        eprintln!("Failed to save after adding edge: {}", e);
        std::process::exit(1);
    }

    if json {
        println!(
            "{}",
            serde_json::json!({
                "status": "ok",
                "source": args.source,
                "target": args.target,
                "strength": args.strength
            })
        );
    } else {
        println!(
            "Added edge {} -> {} (strength={:.2})",
            args.source, args.target, args.strength
        );
    }
}

/// Long-running command that loads a graph (respecting GRAP_MERGE_POLICY)
/// and keeps background merge/vacuum workers alive.
/// Useful for demos so that tiered merging happens visibly in the background.
fn run_start_workers(graph_dir: &Path, args: WorkerArgs) {
    let mut ig = match IndexedKnowledgeGraph::load_from_dir(graph_dir) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("Failed to load graph: {}", e);
            std::process::exit(1);
        }
    };

    let started = ig.start_background_workers(std::time::Duration::from_millis(args.interval));

    if started == 0 {
        eprintln!(
            "No background workers were started.\n\
             Make sure the graph was created with a merge policy (set GRAP_MERGE_POLICY=<n> or use --merge-policy in the demo)."
        );
        std::process::exit(1);
    }

    println!(
        "Background merge/vacuum workers started ({} worker(s), interval {}ms).\n\
         Press Ctrl+C to stop.",
        started, args.interval
    );

    // Keep the process (and its threads) alive
    loop {
        std::thread::sleep(std::time::Duration::from_secs(3600));
    }
}
