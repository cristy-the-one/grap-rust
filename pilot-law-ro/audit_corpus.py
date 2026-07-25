"""Corpus hygiene audit — find junk BEFORE paying an LLM to read it.

Read-only. Uses the same extraction the pipeline uses
(`grap_mcp.ingest._text_and_label_from_json`) so it measures exactly the text
Bonsai would see, not the raw JSON.

Flags, each a separate signal so a file can be judged rather than guessed at:
  dead          — no extractable text at all
  tiny          — text below a floor (no room for an ontology fragment)
  char_run      — a long run of one repeated character (download artifact)
  low_variety   — few unique words relative to length (boilerplate / repetition)
  ngram_loop    — one 5-gram dominates (a repeating block)
  markup        — HTML/XML tags where legal prose is expected
  http_error    — 404/403/error-page text saved instead of the document
  duplicate     — byte-identical extracted text to another file
  mojibake      — classic UTF-8-read-as-latin1 sequences
"""
from __future__ import annotations
import hashlib, json, re, sys, io, os
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "real-data-law-ro"
DOCS = ROOT / "pilot-law-ro" / "docs"
OUT = ROOT / "pilot-law-ro" / "corpus-audit.json"
sys.path.insert(0, str(ROOT / "mcp-server"))
from grap_mcp.ingest import _text_and_label_from_json  # noqa: E402
sys.stdout = io.TextIOWrapper(open(1, "wb", closefd=False), encoding="utf-8",
                              errors="replace", line_buffering=True)

TINY_CHARS = 200
CHAR_RUN = 40           # e.g. "............" or "aaaaaaaa"
LOW_VARIETY = 0.08      # unique words / total words
NGRAM_SHARE = 0.15      # share of the most common 5-gram
MARKUP = re.compile(r"<\s*(html|body|div|span|table|script|head)\b", re.I)
HTTP_ERR = re.compile(r"\b(404|403|500)\b.{0,40}(not found|forbidden|error)"
                      r"|not\s+found|access denied|page not found"
                      r"|internal server error", re.I)
MOJIBAKE = re.compile(r"Ã[\x80-\xbf]|â€\x9c|â€\x9d|Å£|Å\x9f|Ã®")
RUN = re.compile(r"(.)\1{%d,}" % (CHAR_RUN - 1), re.S)


def analyse(text: str) -> dict:
    flags = []
    n = len(text)
    words = re.findall(r"\w+", text.lower(), re.UNICODE)
    variety = (len(set(words)) / len(words)) if words else 0.0
    top5 = 0.0
    if len(words) >= 50:
        grams = Counter(tuple(words[i:i + 5]) for i in range(len(words) - 4))
        if grams:
            top5 = grams.most_common(1)[0][1] / max(1, len(words) - 4)
    m = RUN.search(text)
    longest_run = len(m.group(0)) if m else 0
    if n < TINY_CHARS:
        flags.append("tiny")
    if longest_run >= CHAR_RUN:
        flags.append("char_run")
    if words and len(words) >= 80 and variety < LOW_VARIETY:
        flags.append("low_variety")
    if top5 >= NGRAM_SHARE:
        flags.append("ngram_loop")
    if MARKUP.search(text):
        flags.append("markup")
    if HTTP_ERR.search(text[:4000]):
        flags.append("http_error")
    if MOJIBAKE.search(text):
        flags.append("mojibake")
    return {"chars": n, "words": len(words), "variety": round(variety, 4),
            "top5gram_share": round(top5, 4), "longest_char_run": longest_run,
            "flags": flags}


def main():
    staged = {p.name for p in DOCS.glob("*.json")}
    files = sorted(POOL.glob("*.json"))
    rows, byhash = [], defaultdict(list)
    for f in files:
        rec = {"file": f.name, "raw_bytes": f.stat().st_size,
               "processed": f.name in staged}
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
            text, _ = _text_and_label_from_json(obj, f.stem)
        except Exception as e:
            rec.update({"chars": 0, "words": 0, "flags": ["unreadable"],
                        "error": str(e)[:80]})
            rows.append(rec)
            continue
        if not text or not text.strip():
            rec.update({"chars": 0, "words": 0, "flags": ["dead"]})
            rows.append(rec)
            continue
        rec.update(analyse(text))
        h = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
        rec["text_sha"] = h[:16]
        byhash[h].append(f.name)
        rows.append(rec)

    for h, names in byhash.items():
        if len(names) > 1:
            for r in rows:
                if r.get("text_sha") == h[:16]:
                    r.setdefault("flags", []).append("duplicate")
                    r["duplicate_group"] = names

    def report(subset, label):
        n = len(subset)
        flagged = [r for r in subset if r.get("flags")]
        print(f"\n=== {label}: {n} files, {len(flagged)} flagged "
              f"({len(flagged)/max(1,n):.1%}) ===")
        c = Counter(f for r in subset for f in (r.get("flags") or []))
        for k, v in c.most_common():
            print(f"   {v:>5}  {k}")
        if not c:
            print("   (clean)")
        return flagged

    all_flagged = report(rows, "WHOLE POOL")
    unproc = [r for r in rows if not r["processed"]]
    proc = [r for r in rows if r["processed"]]
    uf = report(unproc, "UNPROCESSED (what Bonsai has not seen)")
    report(proc, "ALREADY PROCESSED (for comparison)")

    print("\n=== worst unprocessed offenders ===")
    for r in sorted(uf, key=lambda r: -(len(r.get("flags") or [])))[:12]:
        print(f"   {r['file']:<16} {r['raw_bytes']//1024:>4}KB chars={r.get('chars',0):>7} "
              f"variety={r.get('variety',0):.3f} top5={r.get('top5gram_share',0):.3f} "
              f"run={r.get('longest_char_run',0):>4}  {r['flags']}")

    est = sum(max(1, (r.get("chars", 0) + 1999) // 2000) for r in unproc)
    est_clean = sum(max(1, (r.get("chars", 0) + 1999) // 2000)
                    for r in unproc if not r.get("flags"))
    print(f"\n=== cost implication (chunk_chars=2000) ===")
    print(f"   unprocessed chunks, as-is        : ~{est}")
    print(f"   unprocessed chunks, flagged excluded: ~{est_clean}  "
          f"(saves ~{est-est_clean} chunks ≈ {(est-est_clean)/60:.1f} h at 1 chunk/min)")

    OUT.write_text(json.dumps({"rows": rows}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nfull per-file report -> {OUT}")


if __name__ == "__main__":
    main()
