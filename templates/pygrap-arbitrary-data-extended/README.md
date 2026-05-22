# PyGrap Arbitrary Data Template — Extended

This is the **extended version** of the basic template.

## New Features

- Parses YAML frontmatter from Markdown files
- Attaches document metadata (`topic`, `source`, `confidence`, etc.) to edges between chunks
- Supports property-based filtering during hybrid queries using `--require`

## Usage

```bash
pip install -r requirements.txt

# Optional: copy the example document
cp -r documents/ your-documents/

python ingest.py
python query.py --require topic
python query.py --require "topic,confidence"
```

> **Note:** `--require` filters by **property presence** (does the edge carry this property?),
> not by value or range. `confidence>0.7` is parsed as just `confidence`.
> Value/range predicates are planned for a future release.

## Example Document

An example Markdown file with frontmatter is included at:

```
documents/example-rag-document.md
```

It demonstrates the following metadata fields:

- `topic`
- `source`
- `confidence`
- `author`
- `date`
- `tags`

During ingestion, this metadata is attached to the edges between chunks of the document, enabling powerful property-based filtering at query time.

## Frontmatter Example

```markdown
---
topic: rag
source: internal-docs
confidence: 0.92
author: Jane Doe
date: 2026-03-15
---

# My Document

Content here...
```

The metadata is attached to internal document edges and can be used for filtering with the `--require` flag.
