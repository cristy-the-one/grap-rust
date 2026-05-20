# pygrap – Python Bindings for grap (Internal / WIP)

This crate provides Python bindings for the `grap` Rust knowledge graph engine using PyO3.

**Status**: Early development / internal tool. Not published on PyPI.

## Local Development

### Prerequisites

- Rust (stable, MSRV 1.81+)
- Python 3.9+
- [maturin](https://github.com/PyO3/maturin) (`pip install maturin`)

### Build & Install in Development Mode

From the repository root:

```bash
# Build and install the extension in editable mode
cd crates/pygrap
maturin develop --release --features "fulltext,sharding"
```

Then in Python:

```python
import pygrap
print(pygrap.version())
```

### Running with specific features

```bash
maturin develop --release --features "fulltext,sharding,mmap"
```

### Rebuilding after changes

Just run `maturin develop` again. It will rebuild only what changed.

## Current Exposed API (very early)

- `pygrap.version()` → returns the version string
- (More types will be added gradually: `IndexedKnowledgeGraph`, `ShardedGraph`, etc.)

## Notes

- This crate lives under `crates/pygrap` and is part of the workspace.
- Because it is internal, we currently use `maturin develop` for local use.
- We do **not** publish wheels to PyPI at this time.
