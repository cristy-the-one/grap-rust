"""Citation-network helpers for Romanian legal acts.

Ports the act-reference parsing from real-data-law-ro/ingest.py: parse acts and
resolve inter-act "cites" links by (type, number, year) key. Used to merge the
corpus's citation network with KEE's extracted relations into one KG.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple

# Romanian diacritics → ASCII (uppercased) for robust key matching.
_DIACRITIC_MAP = str.maketrans("ăâîșțĂÂÎȘȚ", "aaistAAIST")
_DIACRITIC_MAP.update(str.maketrans("şţŞŢ", "stST"))

_DEFINITE_TO_BASE: Dict[str, str] = {
    "LEGEA": "LEGE", "HOTARAREA": "HOTARARE", "HOTARARILE": "HOTARARE",
    "HOTARARILOR": "HOTARARE", "DECIZIA": "DECIZIE", "DECIZIEI": "DECIZIE",
}

# Matches references like: LEGEA nr. 166 din 9 noiembrie 1999
_REF_PATTERN = re.compile(
    r"\b(LEGE[AI]?|ORDONAN[^\s,\.]{0,20}(?:\s+DE\s+URGEN[^\s,\.]{0,10})?|"
    r"HOT[^\s,\.]{0,10}RE[AI]?|DECIZI[AEI]?|NORM[AĂ]?)\s+"
    r"nr\.\s*([\d][\d.]*)\s+din\s+\d{1,2}\s+\w+\s+(\d{4})",
    re.IGNORECASE | re.UNICODE,
)


def _norm_diacritics(s: str) -> str:
    s = s.translate(_DIACRITIC_MAP)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper()


def normalize_tip(tip: str) -> str:
    normed = re.sub(r"\s+", " ", _norm_diacritics(tip.strip()))
    return _DEFINITE_TO_BASE.get(normed, normed)


def act_key(tip: str, nr: str, year: str) -> Tuple[str, str, str]:
    return (normalize_tip(tip), nr.strip().lstrip("0") or "0", year.strip())


def parse_act(d: dict) -> dict:
    dv = d.get("data_vigoare") or ""
    return {
        "id": str(d.get("id", "")).strip(),
        "tip": d.get("tip_act") or "",
        "nr": str(d.get("numar") or "").strip(),
        "year": dv[:4] if len(dv) >= 4 else "",
        "title": (d.get("titlu") or "").strip(),
        "text": d.get("text_complet") or "",
    }


def load_acts(path: str, max_files: int = None) -> List[dict]:
    """Parse up to `max_files` legal-act JSON files under `path`."""
    p = Path(path)
    files = [p] if p.is_file() else sorted(f for f in p.rglob("*.json"))
    if max_files:
        files = files[:max_files]
    acts: List[dict] = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and str(d.get("id", "")).strip():
            acts.append(parse_act(d))
    return acts


def citation_pairs(acts: List[dict]) -> List[Tuple[str, str]]:
    """Return [(source_act_id, target_act_id)] for references resolvable within
    `acts` (references to acts outside the set are dropped)."""
    key_to_id: Dict[Tuple[str, str, str], str] = {}
    for a in acts:
        if a["tip"] and a["nr"] and a["year"]:
            key_to_id[act_key(a["tip"], a["nr"], a["year"])] = a["id"]
    pairs: List[Tuple[str, str]] = []
    for a in acts:
        seen = set()
        for m in _REF_PATTERN.finditer(a["text"]):
            k = act_key(m.group(1), m.group(2).replace(".", "").lstrip("0") or "0",
                        m.group(3))
            if k in seen:
                continue
            seen.add(k)
            tgt = key_to_id.get(k)
            if tgt and tgt != a["id"]:
                pairs.append((a["id"], tgt))
    return pairs
