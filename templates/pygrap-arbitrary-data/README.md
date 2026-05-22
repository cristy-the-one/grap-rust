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
