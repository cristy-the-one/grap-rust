"""Phase 5: the assisted-extraction pipeline + YAML emission.

Ties phases 1-4 together: read docs -> chunk -> LLM extract fragments ->
embedding-based reconcile -> candidate ontology in the tool's schema. The
caller (the MCP tool) versions the result as a *proposed* artifact; a human
loads/promotes it. The pipeline itself is provider/embedder-injected so it runs
deterministically in tests (StubProvider + HashingEmbedder).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# _text_and_label_from_json lives in ..ingest so evidence ingestion and KEE
# extraction read the SAME text for the same file (law-ro pilot finding 13).
from ..ingest import _EXTS, _text_and_label_from_json, chunk_text
from . import extract, reconcile, tabular

logger = logging.getLogger("grap-mcp.kee")


def read_chunks(root: str, max_files: Optional[int] = None,
                chunk_chars: int = 800,
                stats: Optional[dict] = None) -> List[Tuple[str, str]]:
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
    skipped_ids: List[str] = []
    source_files: Dict[str, Path] = {}
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
        if label in source_files:
            raise ValueError(f"Duplicate extraction source label {label!r}: "
                             f"{source_files[label]} and {f}; use unique file stems or JSON ids")
        source_files[label] = f
        # Enumerate FIRST, then filter. Chunk ids embed the index, so filtering
        # before numbering would renumber every chunk after the first skip and
        # make ~1,500 already-processed documents look changed — a full
        # re-extract of the entire corpus. Skipped chunks simply leave gaps.
        for i, c in enumerate(chunk_text(text, max_chars=chunk_chars)):
            if tabular.is_tabular(c):
                skipped_ids.append(f"{label}#{i}")
                continue
            chunks.append((f"{label}#{i}", c))
    if skipped_ids:
        logger.info("read_chunks: skipped %d tabular chunk(s) (score >= %.2f); "
                    "they are still ingested as evidence, just not extracted",
                    len(skipped_ids), tabular._threshold())
    if stats is not None:
        stats["tabular_skipped"] = len(skipped_ids)
        # The IDS matter, not just the count. A caller diffing this list against
        # its manifest must be able to tell "skipped" from "removed" — see
        # extend_graph_ontology.
        stats["tabular_skipped_ids"] = skipped_ids
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
    """Serialize the loadable fields and reviewable relation/evidence proposals."""
    import yaml

    loadable = {
        "version": ontology.get("version", "kee-candidate"),
        "process_outcomes": ontology.get("process_outcomes", []),
        "mapping_hints": ontology.get("mapping_hints", []),
    }
    if "concept_hashes" in ontology:
        loadable["concept_hashes"] = ontology["concept_hashes"]
    for key in ("relations", "extraction_evidence"):
        if ontology.get(key):
            loadable[key] = ontology[key]
    # Per-outcome source-chunk counts: the adoption policy's provenance-floor
    # check reads these (full chunk lists stay out of the loadable file).
    if "provenance" in ontology:
        loadable["source_chunk_counts"] = {
            oid: len(chunks) for oid, chunks in ontology["provenance"].items()}
    header = ("# Candidate ontology proposed by the Knowledge Extraction Engine.\n"
              "# Review before promoting — LLM extraction is a proposal, not ground truth.\n"
              "# concept_hashes (if present) are core node content hashes for verification.\n\n")
    return header + yaml.safe_dump(loadable, sort_keys=False, allow_unicode=True,
                                   default_flow_style=False)
