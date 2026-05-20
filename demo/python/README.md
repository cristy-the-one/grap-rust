# grap Python Demo Layer

This folder contains Python scripts that drive the `grap` Rust engine through the `grap-cli` binary.

It is designed to make the power of grap (especially the on-disk segmented index, sharding, vacuum, statistics, and hybrid search) easy to experience and demonstrate.

## Quick Start

1. Build the CLI (from the repository root):

```bash
cargo build --release --bin grap-cli --features "fulltext,sharding"
```

2. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r demo/python/requirements.txt
```

3. Use the scripts (they will try to auto-discover the `grap-cli` binary):

```bash
# View rich statistics
python -m demo.python.stats --graph-dir ./my-kg

# Run a query
python -m demo.python.query --graph-dir ./my-kg "memory safety" --traverse

# Ingest documents (basic version)
python -m demo.python.ingest --input ./sample-docs --graph-dir ./my-kg --shards 3
```

## Environment Variables

- `GRAP_CLI_PATH` — Full path to the `grap-cli` (or `grap-cli.exe`) binary. Overrides auto-discovery.

## Structure

- `grap_cli.py` — Core helper that finds and invokes the Rust binary.
- `stats.py`   — Pretty statistics (especially segment info and delete ratios).
- `query.py`   — Search + optional graph traversal.
- `ingest.py`  — Turn files into a knowledge graph (simple but extensible).

## Design Philosophy

- The Rust crate (`grap`) is the high-performance engine.
- Python is the user-friendly orchestration and presentation layer.
- Everything is scriptable and easy to customize for workshops, demos, or RAG experiments.

This layer is intentionally lightweight — it does **not** replace a future proper PyO3 binding (`pygrap`).
