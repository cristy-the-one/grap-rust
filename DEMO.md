# grap Holistic Demo

This guide shows you how to run an impressive, self-contained demonstration of the `grap` knowledge graph engine using Python scripts.

The demo is designed to be run live (with pauses for explanation) or in "auto" mode for recordings.

## Prerequisites

- Rust toolchain (the project uses MSRV 1.81)
- Python 3.9+
- (Recommended) A terminal with good Unicode and color support

## One-Time Setup

```bash
# 1. Build the CLI binary (release is noticeably faster)
cargo build --release --bin grap-cli --features "fulltext,sharding"

# 2. Set up Python environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r demo/python/requirements.txt
```

## Running the Full Demo

The recommended way to experience the demo is through the interactive story:

```bash
# Run with pauses (best for live presentations)
python -m demo.python.interactive_demo --graph-dir demo-graph --reset

# Run without pauses (great for screen recordings)
python -m demo.python.interactive_demo --graph-dir demo-graph --reset --auto
```

The demo walks through:

1. **Ingestion** — Real Markdown documents are turned into nodes
2. **Statistics & Observability** — Rich view of on-disk segments, live counts, and delete ratios
3. **Hybrid Search + Traversal** — Text search that also follows graph relationships
4. **Live Modification** — Add a new document while the graph is loaded
5. **Delete + Incremental Vacuum** — The heart of the 0.2 on-disk work (tombstones → space reclamation)
6. **Persistence** — Everything survives process restarts

## Individual Tools

You can also use the tools independently:

```bash
# Beautiful statistics (especially useful after vacuum)
python -m demo.python.stats --graph-dir demo-graph --segments

# Search with optional graph traversal
python -m demo.python.query --graph-dir demo-graph "memory safety" --traverse
```

## What Makes This Demo Special

- It actually exercises **real incremental vacuum** (not just a full compaction).
- It demonstrates **segment-level observability** (`SegmentInfo` with live counts and delete ratios).
- It shows that `grap` is pleasant to drive from Python via a clean CLI.
- All state lives in a normal directory — you can inspect it, copy it, or resume later.

## Resetting the Demo

Add `--reset` to start with a completely fresh graph:

```bash
python -m demo.python.interactive_demo --graph-dir demo-graph --reset
```

## Customizing the Demo

- The sample documents live in `demo/sample-documents/`.
- You can point `--graph-dir` at any location.
- The Python layer is deliberately simple and easy to extend (add visualizations, LLM-based relation extraction, a Streamlit UI, etc.).

## Troubleshooting

**"Could not find the grap-cli binary"**

Make sure you built it:

```bash
cargo build --release --bin grap-cli --features "fulltext,sharding"
```

You can also force a specific binary with the environment variable:

```bash
export GRAP_CLI_PATH=target/release/grap-cli
```

## Future Enhancements (Ideas)

- Full document chunking + relationship extraction in `ingest.py`
- Sharded rebalance live demonstration
- Simple graph visualization
- Pre-built binaries + one-command setup for workshops

---

*This demo exists to make the power of the `grap` engine obvious and delightful.*