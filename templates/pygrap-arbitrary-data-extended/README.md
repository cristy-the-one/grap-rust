# PyGrap Arbitrary Data Template — Extended

This is the **extended version** of the basic template.

## New Features

- Parses YAML frontmatter from Markdown files
- Attaches document metadata (`topic`, `source`, `confidence`, etc.) to edges between chunks
- Supports property-based filtering during hybrid queries using `--require`

## Usage

```bash
pip install -r requirements.txt

python ingest.py
python query.py --require "topic=rag,confidence>0.7"
```

## Frontmatter Example

```markdown
---
topic: rag
source: internal-docs
confidence: 0.92
---

# My Document

Content here...
```

The metadata will be attached to the document's internal edges and can be used for filtering.
