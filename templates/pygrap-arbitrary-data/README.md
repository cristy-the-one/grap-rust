# PyGrap Arbitrary Data Template

A clean, production-oriented drop-in for ingesting and querying your own documents with `pygrap`.

## Features

- Recursive ingestion of `.md`, `.txt`, `.rst` files
- Paragraph-aware chunking with overlap
- Idempotent ingestion (safe to re-run)
- Vector embeddings + hybrid (text + vector + graph) search
- On-disk persistence with merge policy
- Easy to extend

## Quick Start

```bash
pip install -r requirements.txt

# Put your documents here
mkdir -p documents

python ingest.py
python query.py
```

## Environment Variables

| Variable           | Default             | Description                      |
|--------------------|---------------------|----------------------------------|
| `GRAP_DIR`         | `./graph`           | Location of the grap database    |
| `DOCS_DIR`         | `./documents`       | Location of your source files    |
| `EMBEDDING_MODEL`  | `all-MiniLM-L6-v2`  | SentenceTransformer model name   |

## Next Steps

See the **extended version** of this template that adds support for **properties and property-based filtering** during queries.

---

## Using Docker (Recommended)

This template includes a `Dockerfile` and `docker-compose.yml` so you don't need to set up Rust + maturin locally.

### Quick Start with Docker

```bash
# 1. Put your documents in the ./documents folder
mkdir -p documents

# 2. Build the image and run ingestion
docker compose run --rm pygrap python ingest.py

# 3. Run queries interactively
docker compose run --rm pygrap python query.py
```

### Common Docker Commands

```bash
# Ingest documents
docker compose run --rm pygrap python ingest.py

# Query the graph
docker compose run --rm pygrap python query.py

# Open a shell in the container (useful for debugging)
docker compose run --rm pygrap bash

# Force rebuild the image (after changing requirements.txt etc.)
docker compose build --no-cache
```

### Notes

- Your documents and graph data live on your host machine (via Docker volumes).
- The first build takes a while because it compiles the Rust extension.
- You can change the embedding model by editing the `EMBEDDING_MODEL` variable in `docker-compose.yml`.
- The graph directory (`./graph`) persists between runs.
