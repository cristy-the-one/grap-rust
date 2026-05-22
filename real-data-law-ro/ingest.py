#!/usr/bin/env python3
"""
Phase 1 - Two-pass citation-network ingestion for the Romanian Legal Acts dataset.

Pass 1: Create one node per legal act, build (tip_norm, nr, year) → node_id map.
Pass 2: Extract citation references from text_complet, create edges.
"""

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Dict, Tuple

from sentence_transformers import SentenceTransformer
import pygrap

# ====================== CONFIG ======================
DATA_DIR = Path(__file__).parent
GRAPH_DIR = Path(os.getenv("GRAP_DIR", DATA_DIR / "graph"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
SAVE_INTERVAL = 200
# ====================================================

# Romanian diacritics normalisation map
_DIACRITIC_MAP = str.maketrans(
    "ăâîșțĂÂÎȘȚ",
    "aaistAAIST",
)
# Extended cedilla/comma variants
_DIACRITIC_MAP.update(str.maketrans("şţŞŢ", "stST"))

# Definite-article forms → base form
_DEFINITE_TO_BASE: Dict[str, str] = {
    "LEGEA": "LEGE",
    "HOTARAREA": "HOTARARE",
    "HOTARARILE": "HOTARARE",
    "HOTARARILOR": "HOTARARE",
    "DECIZIA": "DECIZIE",
    "DECIZIEI": "DECIZIE",
}


def _norm_diacritics(s: str) -> str:
    """Remove Romanian diacritics and uppercase."""
    s = s.translate(_DIACRITIC_MAP)
    # Fallback: strip any remaining combining diacritical marks
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper()


def normalize_tip(tip: str) -> str:
    """Normalise act type string for key matching."""
    normed = _norm_diacritics(tip.strip())
    # Collapse whitespace
    normed = re.sub(r"\s+", " ", normed)
    # Resolve definite-article forms
    return _DEFINITE_TO_BASE.get(normed, normed)


def act_key(tip: str, nr: str, year: str) -> Tuple[str, str, str]:
    return (normalize_tip(tip), nr.strip().lstrip("0") or "0", year.strip())


# Regex to find references to other acts in the text
# Matches patterns like: LEGEA nr. 166 din 9 noiembrie 1999
#                        ORDONANȚA DE URGENTA nr. 69 din 29 decembrie 1998
#                        HOTĂRÂREA nr. 671 din 17 august 2000
REF_PATTERN = re.compile(
    r"\b(LEGE[AI]?|ORDONAN[^\s,\.]{0,20}(?:\s+DE\s+URGEN[^\s,\.]{0,10})?|"
    r"HOT[^\s,\.]{0,10}RE[AI]?|DECIZI[AEI]?|NORM[AĂ]?)\s+"
    r"nr\.\s*([\d][\d.]*)\s+din\s+\d{1,2}\s+\w+\s+(\d{4})",
    re.IGNORECASE | re.UNICODE,
)


def build_node_text(act: dict) -> str:
    """Create the main text content for the node, with metadata prefix."""
    meta = []
    if act.get("tip_act"):
        meta.append(f"Tip: {act['tip_act']}")
    if act.get("numar"):
        meta.append(f"Număr: {act['numar']}")
    if act.get("data_vigoare"):
        meta.append(f"Data vigoare: {act['data_vigoare']}")
    if act.get("emitent"):
        meta.append(f"Emitent: {act['emitent']}")
    if act.get("in_vigoare") is not None:
        meta.append(f"În vigoare: {act['in_vigoare']}")

    meta_str = " | ".join(meta)
    title = act.get("titlu", "").strip()
    text = act.get("text_complet", "").strip()

    prefix = f"[METADATA] {meta_str}\n\n"
    full_text = prefix + f"{title}\n\n{text}" if text else prefix + title
    return full_text


def main():
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    print(f"Opening/creating graph at {GRAPH_DIR}...")
    g = pygrap.IndexedKnowledgeGraph.with_merge_policy(str(GRAPH_DIR), 4)

    json_files = sorted(DATA_DIR.glob("*.json"))
    print(f"Found {len(json_files)} legal acts.\n")

    # ------------------------------------------------------------------
    # Pass 1: create nodes, build act_key → node_id map
    # ------------------------------------------------------------------
    print("=== Pass 1: Creating nodes ===")
    key_to_node: Dict[Tuple[str, str, str], int] = {}
    # Also store raw acts for Pass 2 (avoid re-reading files)
    acts = []
    node_ids = []

    processed = 0
    skipped = 0

    for json_path in json_files:
        try:
            with open(json_path, encoding="utf-8") as f:
                act = json.load(f)
        except Exception as e:
            print(f"  Skipping {json_path.name} (JSON error: {e})")
            skipped += 1
            continue

        act_id = str(act.get("id", "")).strip()
        if not act_id:
            skipped += 1
            continue

        tip = act.get("tip_act") or ""
        nr = str(act.get("numar") or "").strip()
        dv = act.get("data_vigoare") or ""
        year = dv[:4] if len(dv) >= 4 else ""

        text = build_node_text(act)
        embedding = model.encode(text[:2000], normalize_embeddings=True).tolist()
        node_id = g.add_node(text=text, label=f"act-{act_id}", embedding=embedding)

        acts.append(act)
        node_ids.append(node_id)

        if tip and nr and year:
            k = act_key(tip, nr, year)
            key_to_node[k] = node_id

        processed += 1
        if processed % SAVE_INTERVAL == 0:
            print(f"  Pass 1: {processed} nodes created...")
            g.save_to_dir(str(GRAPH_DIR))

    print(f"Pass 1 complete: {processed} nodes, {skipped} skipped, {len(key_to_node)} keyed acts.")
    g.save_to_dir(str(GRAPH_DIR))

    # ------------------------------------------------------------------
    # Pass 2: extract citations, create edges
    # ------------------------------------------------------------------
    print("\n=== Pass 2: Extracting citation edges ===")
    edges_created = 0
    unresolved = 0

    for act, source_id in zip(acts, node_ids):
        text_complet = act.get("text_complet") or ""
        seen = set()

        for m in REF_PATTERN.finditer(text_complet):
            ref_tip = m.group(1)
            ref_nr = m.group(2).replace(".", "").lstrip("0") or "0"
            ref_year = m.group(3)

            k = act_key(ref_tip, ref_nr, ref_year)
            if k in seen:
                continue
            seen.add(k)

            target_id = key_to_node.get(k)
            if target_id is None:
                unresolved += 1
                continue

            if target_id == source_id:
                continue  # skip self-references

            g.add_edge(source_id, target_id, 0.8,
                       {"relation_type": "cites", "confidence": 0.8})
            edges_created += 1

    print(f"Pass 2 complete: {edges_created} edges created, {unresolved} unresolved references.")

    print("Saving final graph...")
    g.save_to_dir(str(GRAPH_DIR))
    print(f"Graph saved to {GRAPH_DIR}")
    print(f"\nSummary: {processed} nodes | {edges_created} citation edges | {unresolved} unresolved refs")


if __name__ == "__main__":
    main()
