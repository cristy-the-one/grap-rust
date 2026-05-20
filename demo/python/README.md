# grap Python Demo Layer

This folder contains a complete Python-driven demonstration layer for the `grap` Rust knowledge graph engine.

It exists to make the advanced capabilities of grap (on-disk segmented text index, incremental vacuum, segment statistics, hybrid search + traversal, sharding, persistence, and live mutation) **tangible and impressive** without requiring the audience to read Rust code.

## Quick Start (Recommended)

```bash
# 1. Build the CLI (from repo root)
cargo build --release --bin grap-cli --features "fulltext,sharding"

# 2. Set up Python environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r demo/python/requirements.txt

# 3. Run the full holistic story (with pauses)
python -m demo.python.interactive_demo --graph-dir demo-graph --reset

# Or run without pauses (great for recording)
python -m demo.python.interactive_demo --graph-dir demo-graph --reset --auto
```

## Available Commands

### High-level Demo

- `python -m demo.python.interactive_demo`  
  The main narrative experience. Walks through ingestion → statistics → search → live add/delete/vacuum → persistence.

- `python -m demo.python.demo`  
  Lightweight entrypoint (currently maps to `stats` and `query`).

### Individual Tools

- `python -m demo.python.stats --graph-dir <dir> [--segments]`  
  Beautiful statistics view, especially useful for showing segment health and vacuum effects.

- `python -m demo.python.query --graph-dir <dir> "your query" [--traverse]`  
  Run text search or hybrid search+traversal with rich output.

- `python -m demo.python.ingest --input <dir> --graph-dir <dir>`  
  Basic document ingestion (currently a skeleton — will be expanded).

## The `grap-cli` Binary

All Python scripts ultimately drive a single Rust binary: `grap-cli`.

Key subcommands available today:

| Command          | Purpose                                      |
|------------------|----------------------------------------------|
| `stats`          | Node/edge counts + detailed segment info     |
| `query`          | Text search (+ optional graph traversal)     |
| `add-file`       | Add a document as a new node                 |
| `delete-node`    | Soft-delete a node (creates tombstone)       |
| `vacuum`         | Incremental space reclamation                |

Example direct usage:
```bash
cargo run --bin grap-cli --features "fulltext,sharding" -- \
  --graph-dir ./my-kg stats --segments
```

## Environment Variable

- `GRAP_CLI_PATH` — Override the automatic discovery of the `grap-cli` binary.

## Project Structure

```
demo/
├── python/
│   ├── grap_cli.py          # Binary discovery + subprocess helper
│   ├── interactive_demo.py  # The full story / main demo
│   ├── stats.py / query.py
│   ├── ingest.py
│   └── requirements.txt
└── sample-documents/        # Curated documents for the demo
```

## Design Goals

- **Showcase, not production** — This is a demonstration tool.
- **Python first** — Most users will interact via these scripts.
- **Real operations** — The demo actually exercises `remove_node`, tombstones, and incremental `vacuum`.
- **Extensible** — Easy to add LLM-based relation extraction, visualizations, or a Streamlit UI later.

This Python layer is intentionally separate from the core library. A future proper PyO3 binding (`pygrap`) can be built independently if needed.

## Next Steps / Ideas

- Make `ingest.py` fully functional with chunking and optional LLM relation extraction.
- Add sharded rebalance demonstration.
- Add visualization (graphviz / networkx).
- Record a terminal cast of `interactive_demo.py --auto`.
