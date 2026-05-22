# Romanian Legal Acts - grap Use Case (Phase 0)

This folder contains a real-world dataset of ~2,100 Romanian legal acts (Hotărâri, Legi, Ordonanțe, etc.) and a Phase 0 prototype for ingesting them into `grap`.

## Dataset

- Source: legislație.just.ro (scraped)
- Format: One JSON file per legal act
- Size: ~2,092 documents
- Contains rich metadata + full legal text

## Phase 0 Goals

- Ingest all acts as nodes with metadata
- Add embeddings
- Demonstrate basic hybrid search
- Establish a working ingestion pipeline

## Quick Start

```bash
pip install -r requirements.txt

python ingest.py
python query.py
```

## Current Limitations (Phase 0)

- No relationships extracted yet (graph_weight is low)
- One node per act (no chunk nodes yet)
- Basic metadata stored in text prefix

These will be addressed in Phase 1 (relationship extraction + proper edge properties).

## Next Phases (Planned)

- Phase 1: Extract references/amendments between acts
- Phase 2: Rich property modeling + advanced filtering
- Phase 3: Scale & production features

## Files

- `ingest.py` — Phase 0 ingestion script
- `query.py` — Simple hybrid query interface
- `requirements.txt`
