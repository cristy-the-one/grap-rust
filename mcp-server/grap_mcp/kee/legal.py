"""Citation-network helpers for Romanian legal acts.

Ports the act-reference parsing from real-data-law-ro/ingest.py: parse acts and
resolve inter-act "cites" links by (type, number, year) key. Used to merge the
corpus's citation network with KEE's extracted relations into one KG.
"""

from __future__ import annotations

import json
import hashlib
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
    "LEGII": "LEGE", "HOTARARII": "HOTARARE", "ORDONANTEI": "ORDONANTA",
    "ORDONANTEI DE URGENTA": "ORDONANTA DE URGENTA",
    "NORME": "NORMA", "NORMELE": "NORMA", "NORMELOR": "NORMA",
    "ORDINUL": "ORDIN", "ORDINULUI": "ORDIN",
    "REGULAMENTUL": "REGULAMENT", "REGULAMENTULUI": "REGULAMENT",
}

# Matches references like: LEGEA nr. 166 din 9 noiembrie 1999
_KIND = (r"LEG(?:EA|II|E)|HOT[ĂA]R[ÂA]R(?:EA|II|E|ILOR|ILE)|DECIZI(?:A|EI|E)|"
         r"ORDONAN[ȚŢT](?:A|Ă|EI)(?:\s+DE\s+URGEN[ȚŢT][ĂA])?|"
         r"NORM(?:A|Ă|ELE|ELOR|E)|REGULAMENT(?:ULUI|UL)?|ORDIN(?:ULUI|UL)?")
_REF_PATTERN = re.compile(
    rf"\b(?P<kind>{_KIND})\s+(?P<qualifier>(?:[^\W\d_][\w.-]*\s+){{0,8}}?)"
    r"nr\.?\s*(?P<number>\d[\d.]*)\s*(?:/\s*(?P<short_year>\d{4})\b|"
    r"din\s+\d{1,2}\s+\w+\s+(?P<date_year>\d{4})\b)", re.IGNORECASE)
_HEADING_DATE = re.compile(r"\bdin\s+\d{1,2}\s+\w+\s+(\d{4})\b", re.IGNORECASE)
_HEADING_NUMBER = re.compile(rf"(?:{_KIND})\s+nr\.?\s*(\d[\w./-]*)", re.IGNORECASE)


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
    title = (d.get("titlu") or "").strip()
    # Effective date can be in a later year. It is not the act's identifier year.
    date = _HEADING_DATE.search(title)
    heading = _REF_PATTERN.match(title.lstrip("\ufeff \t\r\n"))
    number = _HEADING_NUMBER.match(title.lstrip("\ufeff \t\r\n"))
    nr = number.group(1).rstrip(".") if number else str(d.get("numar") or "").strip()
    if re.fullmatch(r"\d+(?:\.\d{3})+", nr):
        nr = nr.replace(".", "")
    return {
        "id": str(d.get("id", "")).strip(),
        "tip": d.get("tip_act") or "",
        "nr": nr,
        "year": ((heading.group("short_year") or heading.group("date_year")) if heading
                 else date.group(1) if date else ""),
        "title": title,
        "text": d.get("text_complet") or "",
        "effective_date": d.get("data_vigoare"), "in_force": d.get("in_vigoare"),
        "issuer": d.get("emitent"), "source_url": d.get("link"),
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
            raw = f.read_bytes()
            d = json.loads(raw.decode("utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and str(d.get("id", "")).strip():
            acts.append({**parse_act(d), "source_file": str(f.resolve()),
                         "source_sha256": hashlib.sha256(raw).hexdigest()})
    return acts


def citation_report(acts: List[dict]) -> dict:
    """Resolve unique in-corpus targets, preserving missing/ambiguous references.

    A citation is only a citation: this parser does not infer repeal or amendment.
    Type/number/year may collide across issuers, so a collision is left unresolved.
    """
    key_to_ids = {}
    for a in acts:
        if a["tip"] and a["nr"] not in ("", "0") and a["year"]:
            key_to_ids.setdefault(act_key(a["tip"], a["nr"], a["year"]), set()).add(a["id"])
    pairs: List[Tuple[str, str]] = []
    references = []
    for a in acts:
        seen = set()
        for m in _REF_PATTERN.finditer(a["text"]):
            # Do not let an unmatched outer act name consume a nested reference.
            if re.search(rf"\b(?:{_KIND})\b", m.group("qualifier"), re.IGNORECASE):
                continue
            k = act_key(m.group("kind"), m.group("number").replace(".", ""),
                        m.group("short_year") or m.group("date_year"))
            if (not a["text"][:m.start()].strip("\ufeff \t\r\n") and
                    k == act_key(a["tip"], a["nr"], a["year"])):
                continue  # the document's own heading is known even if its key collides
            targets = key_to_ids.get(k, set())
            tgt = next(iter(targets)) if len(targets) == 1 else None
            status = "resolved" if tgt else "ambiguous" if targets else "missing"
            if tgt == a["id"]:
                continue  # own heading / self-reference is not an inter-act edge
            references.append({"source":a["id"], "target":tgt, "key":list(k), "status":status,
                               "start":m.start(), "end":m.end(), "quote":m.group(0)})
            if tgt and tgt not in seen:
                seen.add(tgt)
                pairs.append((a["id"], tgt))
    return {"pairs": pairs, "references": references,
            "unidentified_acts": [a["id"] for a in acts if not a["year"] or a["nr"] in ("", "0")],
            "ambiguous_keys": [{"key":list(k), "targets":sorted(v)} for k,v in key_to_ids.items() if len(v)>1]}


def citation_pairs(acts: List[dict]) -> List[Tuple[str, str]]:
    return citation_report(acts)["pairs"]
