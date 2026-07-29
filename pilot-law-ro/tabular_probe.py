"""How much of the corpus is table/list content, and can we detect it safely?

Goal: keep row-lists ("familia Alexe Valentin ... 10.000 lei") out of the
ontology and out of the LLM budget, WITHOUT losing normative text. Whole-file
exclusion is not an option — the corpus audit's "exclude 142 files" advice was
wrong and would have deleted 4,280 chunks of real legislation. These documents
are typically a normative preamble followed by annexes, so the granularity has
to be the CHUNK.

This script only measures. It classifies chunks, reports what would be dropped
and what that saves, and prints samples from both sides of the line plus the
borderline band, so the operator can see the failure modes before anything is
wired into extraction.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
DOCS = PILOT / "docs"
POOL = ROOT / "real-data-law-ro"
sys.path.insert(0, str(ROOT / "mcp-server"))
from grap_mcp.ingest import chunk_text                       # noqa: E402
from grap_mcp.kee.pipeline import _text_and_label_from_json  # noqa: E402

CHUNK_CHARS = 2000

# NOT layout. The law-aware JSON extractor flattens tables into running text
# before we ever see them, so the whitespace/box-drawing delimiters of
# docs/table-preserving-reduction.md are already gone. A budget annex arrives as
# "...85 01 Rambursari de credite externe 13.630.000 13.630.000..." — visually
# prose, structurally a table. The signal that survives flattening is the ratio
# of tokens that are NUMBERS or CODES rather than words.
_NUMERIC_TOKEN = re.compile(r"^[\d.,;:()\[\]%/\-–—]+$")
_CODEY_TOKEN = re.compile(r"^[\d][\d.,()\-/]*[\d)]$")
_SENT_END = re.compile(r"[.;:]\s")


def chunk_signals(text: str) -> dict:
    toks = text.split()
    if len(toks) < 20:
        return {"numeric": 0.0, "digits": 0.0, "sent": 1.0, "tokens": len(toks)}
    numeric = sum(1 for t in toks if _NUMERIC_TOKEN.match(t) or _CODEY_TOKEN.match(t))
    digits = sum(ch.isdigit() for ch in text) / max(1, len(text))
    # sentence enders per 100 tokens: prose punctuates, a row list does not
    sents = 100.0 * len(_SENT_END.findall(text)) / max(1, len(toks))
    return {"numeric": numeric / len(toks), "digits": digits, "sent": sents,
            "tokens": len(toks)}


def tabular_score(text: str) -> float:
    """0..1. High = a flattened table / code list; low = prose."""
    s = chunk_signals(text)
    if s["tokens"] < 20:
        return 0.0                       # too small to judge; keep it
    score = 0.0
    score += 0.60 * min(1.0, s["numeric"] / 0.35)     # a third of tokens are figures
    score += 0.25 * min(1.0, s["digits"] / 0.20)      # ...and they are long ones
    score += 0.15 * (1.0 - min(1.0, s["sent"] / 4.0))  # prose punctuates; lists do not
    return round(min(1.0, score), 3)


def main():
    threshold = float(sys.argv[1]) if len(sys.argv) > 1 else 0.75
    present = {p.name for p in DOCS.glob("*.json")}
    rem = [p for p in POOL.glob("*.json") if p.name not in present]
    rows = []
    for p in rem:
        try:
            text, _ = _text_and_label_from_json(json.loads(p.read_text(encoding="utf-8")), p.stem)
        except Exception:
            continue
        cs = chunk_text(text, max_chars=CHUNK_CHARS)
        for i, c in enumerate(cs):
            rows.append((p.stem, i, len(cs), tabular_score(c), c))

    total = len(rows)
    drop = [r for r in rows if r[3] >= threshold]
    band = [r for r in rows if threshold - 0.15 <= r[3] < threshold]
    print(f"remaining chunks: {total}")
    print(f"threshold {threshold}: would EXCLUDE {len(drop)} ({100*len(drop)/total:.1f}%), "
          f"saving ~{len(drop)*146/3600:.0f} h of decode")
    print(f"borderline band [{threshold-0.15:.2f},{threshold}): {len(band)} chunks")
    print()
    for lo, hi in ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 0.9), (0.9, 1.01)):
        n = sum(1 for r in rows if lo <= r[3] < hi)
        print(f"  score {lo:.2f}-{hi:.2f}: {n:5d} chunks ({100*n/total:4.1f}%)")

    # Per-document: does exclusion ever take a WHOLE document? That would mean
    # we are silently doing the file-level exclusion that was already refuted.
    bydoc = {}
    for stem, i, n, sc, _ in rows:
        d = bydoc.setdefault(stem, [0, n])
        if sc >= threshold:
            d[0] += 1
    whole = [(s, v[0], v[1]) for s, v in bydoc.items() if v[1] and v[0] == v[1]]
    print(f"\ndocuments that would lose EVERY chunk: {len(whole)}")
    for s, a, b in whole[:10]:
        print(f"   {s}: {a}/{b}")

    def show(label, sel, k=3):
        print(f"\n--- {label} ---")
        for stem, i, n, sc, c in sel[:k]:
            head = " / ".join(c.strip().splitlines()[:3])[:170]
            print(f"  [{sc:.2f}] {stem}#{i}: {head}")

    drop.sort(key=lambda r: -r[3])
    keep = [r for r in rows if r[3] < threshold]
    keep.sort(key=lambda r: r[3])
    show("HIGHEST-scoring (would be excluded)", drop)
    show("BORDERLINE (just above the line)", sorted(band, key=lambda r: -r[3]))
    show("LOWEST-scoring (clearly kept)", keep)
    Path(PILOT / "tabular-probe.json").write_text(json.dumps(
        {"threshold": threshold, "total": total, "excluded": len(drop),
         "band": len(band), "whole_doc_losses": len(whole)}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
