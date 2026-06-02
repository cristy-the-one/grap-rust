"""Phase 5: the assisted-extraction pipeline + YAML emission.

Ties phases 1-4 together: read docs -> chunk -> LLM extract fragments ->
embedding-based reconcile -> candidate ontology in the tool's schema. The
caller (the MCP tool) versions the result as a *proposed* artifact; a human
loads/promotes it. The pipeline itself is provider/embedder-injected so it runs
deterministically in tests (StubProvider + HashingEmbedder).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..ingest import _EXTS, chunk_text
from . import extract, reconcile


def read_chunks(root: str) -> List[Tuple[str, str]]:
    """Walk .md/.txt under `root` and return [(source_label, chunk_text), ...]."""
    p = Path(root)
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in _EXTS)
    else:
        raise FileNotFoundError(f"extract path not found: {root}")
    chunks: List[Tuple[str, str]] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, c in enumerate(chunk_text(text)):
            chunks.append((f"{f.stem}#{i}", c))
    return chunks


def extract_ontology(docs_path: str, provider, embedder, sim: float = 0.86,
                     version: str = "kee-candidate") -> Dict[str, Any]:
    """Run the full pipeline and return the candidate ontology + run stats."""
    chunks = read_chunks(docs_path)
    fragments = extract.extract_fragments(chunks, provider)
    ontology = reconcile.reconcile(fragments, embedder, sim=sim, version=version)
    return {
        "ontology": ontology,
        "chunks": len(chunks),
        "fragments": len(fragments),
    }


def to_yaml(ontology: Dict[str, Any]) -> str:
    """Serialize only the loadable ontology fields (drops provenance)."""
    import yaml

    loadable = {
        "version": ontology.get("version", "kee-candidate"),
        "process_outcomes": ontology.get("process_outcomes", []),
        "mapping_hints": ontology.get("mapping_hints", []),
    }
    header = ("# Candidate ontology proposed by the Knowledge Extraction Engine.\n"
              "# Review before promoting — LLM extraction is a proposal, not ground truth.\n\n")
    return header + yaml.safe_dump(loadable, sort_keys=False, allow_unicode=True,
                                   default_flow_style=False)
