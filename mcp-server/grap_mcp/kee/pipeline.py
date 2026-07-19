"""Phase 5: the assisted-extraction pipeline + YAML emission.

Ties phases 1-4 together: read docs -> chunk -> LLM extract fragments ->
embedding-based reconcile -> candidate ontology in the tool's schema. The
caller (the MCP tool) versions the result as a *proposed* artifact; a human
loads/promotes it. The pipeline itself is provider/embedder-injected so it runs
deterministically in tests (StubProvider + HashingEmbedder).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..ingest import _EXTS, chunk_text
from . import extract, reconcile


def _text_and_label_from_json(obj: Any, stem: str) -> Tuple[str, str]:
    """Extract (text, label) from a parsed JSON document.

    Law-aware (titlu + text_complet, the Romanian-legal-acts schema), with a
    generic fallback (common text fields, else all string values joined)."""
    if isinstance(obj, dict):
        label = (str(obj.get("id")).strip() if obj.get("id") not in (None, "") else stem)
        title = str(obj.get("titlu") or "").strip()
        body = str(obj.get("text_complet") or obj.get("text")
                   or obj.get("content") or obj.get("body") or "").strip()
        if title or body:
            return ((title + "\n\n" + body).strip(), label)
        parts = [v.strip() for v in obj.values() if isinstance(v, str) and v.strip()]
        return ("\n\n".join(parts), label)
    if isinstance(obj, list):
        return ("\n\n".join(_text_and_label_from_json(x, stem)[0] for x in obj), stem)
    return (str(obj or ""), stem)


def read_chunks(root: str, max_files: Optional[int] = None,
                chunk_chars: int = 800) -> List[Tuple[str, str]]:
    """Walk .md/.txt/.json under `root` → [(source_label, chunk_text), ...].

    JSON files are read via the law-aware/generic extractor. `max_files` caps how
    many files are read (a safety guard against pointing at a huge corpus — an
    LLM call per chunk gets expensive fast). `chunk_chars` sets the chunk size
    (bigger = fewer LLM calls; keep modest for small models)."""
    p = Path(root)
    exts = set(_EXTS) | {".json"}
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in exts)
    else:
        raise FileNotFoundError(f"extract path not found: {root}")
    if max_files is not None and max_files > 0:
        files = files[:max_files]
    chunks: List[Tuple[str, str]] = []
    for f in files:
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if f.suffix.lower() == ".json":
            try:
                text, label = _text_and_label_from_json(json.loads(raw), f.stem)
            except ValueError:
                continue
        else:
            text, label = raw, f.stem
        for i, c in enumerate(chunk_text(text, max_chars=chunk_chars)):
            chunks.append((f"{label}#{i}", c))
    return chunks


def extract_ontology(docs_path: str, provider, embedder, sim: float = 0.86,
                     version: str = "kee-candidate",
                     max_files: Optional[int] = None, chunk_chars: int = 800,
                     graph_augmented: bool = True) -> Dict[str, Any]:
    """Run the extraction pipeline and return the candidate ontology + run stats.

    `graph_augmented=True` (default) uses the graph-in-the-loop pipeline
    (live KG context + entity resolution + relation grounding + bounded
    re-looks). `graph_augmented=False` runs the legacy stateless path
    (per-chunk extract -> embedding reconcile)."""
    chunks = read_chunks(docs_path, max_files=max_files, chunk_chars=chunk_chars)
    if graph_augmented:
        from .graph_extract import graph_extract_ontology
        return graph_extract_ontology(chunks, provider, embedder,
                                      sim=sim, version=version)
    fragments = extract.extract_fragments(chunks, provider)
    ontology = reconcile.reconcile(fragments, embedder, sim=sim, version=version)
    return {
        "ontology": ontology,
        "chunks": len(chunks),
        "fragments": len(fragments),
    }


def to_yaml(ontology: Dict[str, Any]) -> str:
    """Serialize only the loadable ontology fields (drops provenance, keeps concept_hashes for verification)."""
    import yaml

    loadable = {
        "version": ontology.get("version", "kee-candidate"),
        "process_outcomes": ontology.get("process_outcomes", []),
        "mapping_hints": ontology.get("mapping_hints", []),
    }
    if "concept_hashes" in ontology:
        loadable["concept_hashes"] = ontology["concept_hashes"]
    header = ("# Candidate ontology proposed by the Knowledge Extraction Engine.\n"
              "# Review before promoting — LLM extraction is a proposal, not ground truth.\n"
              "# concept_hashes (if present) are core node content hashes for verification.\n\n")
    return header + yaml.safe_dump(loadable, sort_keys=False, allow_unicode=True,
                                   default_flow_style=False)
