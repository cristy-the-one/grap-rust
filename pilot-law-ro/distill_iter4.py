"""Distillation Iteration 4 — is a small schema good extraction context?

THE PITCH-BLACK CLAIM, tested as non-inferiority. Pre-registered in
docs/experiments/distillation-journal.md.

Two arms over the SAME held-out chunks, same system prompt, parser, model:
  A  concept-sea  : wg.context_for(text) over the 3,739-concept graph (today)
  B  distilled    : 7 classes + 3 typed axioms, fixed size forever

Writes every prompt and raw response to distillation/iter-4/ so the human
spot-check is auditable and the run is resumable.
"""
from __future__ import annotations
import json, os, sys, io, time, re
from collections import Counter
from pathlib import Path

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
OUT = PILOT / "distillation" / "iter-4"
OUT.mkdir(parents=True, exist_ok=True)

N_CHUNKS = int(os.environ.get("ITER4_CHUNKS", "10"))

os.environ.setdefault("GRAP_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
os.environ.setdefault("GRAP_LLM_MODEL", "ternary-bonsai-27b")
os.environ.setdefault("GRAP_LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
os.environ.setdefault("GRAP_LLM_TIMEOUT", "420")
os.environ.setdefault("GRAP_LLM_MAX_TOKENS", "2048")
os.environ.setdefault("GRAP_LLM_RETRIES", "2")
os.environ.setdefault("GRAP_EMBED_BACKEND", "ollama")
os.environ.setdefault("GRAP_EMBED_MODEL", "bge-m3")
os.environ.setdefault("GRAP_EMBED_BASE_URL", "http://127.0.0.1:11434/v1")
sys.path.insert(0, str(ROOT / "mcp-server"))
sys.path.insert(0, str(PILOT))

from grap_mcp.ingest import _text_and_label_from_json, chunk_text   # noqa: E402
from grap_mcp.kee import extract as ex                              # noqa: E402
from grap_mcp.kee.llm import get_provider                           # noqa: E402
from grap_mcp.kee.embeddings import get_embedder                    # noqa: E402
from grap_mcp.kee.working_graph import WorkingGraph                 # noqa: E402
from distill_iter1 import classify                                  # noqa: E402
sys.stdout = io.TextIOWrapper(open(1, "wb", closefd=False), encoding="utf-8",
                              errors="replace", line_buffering=True)

# ---- arm B: the distilled schema, verbatim from iteration 3b ----------------
SCHEMA_CONTEXT = """You also have a DISTILLED SCHEMA for this corpus: the classes of thing it
contains and the typed relations that hold between them. Use these class names and
relation directions when the same kinds of thing appear here. Only assert relations
grounded in THIS text.

CLASSES: NormativeAct, AuthoritativeBody, Official, OfficialGazette,
StructuralPart, LegalChallenge, Budget

TYPED RELATIONS (use exactly this direction):
- AuthoritativeBody --emite--> NormativeAct
- Official --contrasemnează--> NormativeAct
- NormativeAct --adoptată de--> AuthoritativeBody

CROSS-REFERENCE: "se referă la" may link any kinds of thing (it is deliberately untyped)."""

CANON = {"emite", "EMITENT", "emit"}


def approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def held_out_chunks(n):
    """Chunks from documents never ingested into the pilot corpus."""
    present = {p.name for p in (PILOT / "docs").glob("*.json")}
    pool = sorted((p for p in (ROOT / "real-data-law-ro").glob("*.json")
                   if p.name not in present), key=lambda p: p.stat().st_size)
    # mid-sized docs: skip the tiny tail, take a deterministic stride
    pool = pool[len(pool) // 2:]
    out = []
    for f in pool:
        try:
            t, _ = _text_and_label_from_json(json.loads(f.read_text(encoding="utf-8")), f.stem)
        except Exception:
            continue
        ch = chunk_text(t, 2000)
        if ch:
            out.append((f"{f.stem}#0", ch[0]))
        if len(out) >= n:
            break
    return out


def canon_stats(frag):
    """How many emite-family relations point AuthoritativeBody -> NormativeAct?"""
    ok = bad = 0
    for r in frag.get("relations", []):
        if str(r.get("type", "")).strip() in CANON:
            a, b = classify(str(r.get("from", ""))), classify(str(r.get("to", "")))
            if a == "AuthoritativeBody" and b == "NormativeAct":
                ok += 1
            elif a == "NormativeAct" and b == "AuthoritativeBody":
                bad += 1
    return ok, bad


def main():
    prov = get_provider()
    chunks = held_out_chunks(N_CHUNKS)
    print(f"held-out chunks: {len(chunks)}  (docs never ingested)")

    wg = WorkingGraph.open(str(PILOT / "kee-workdir"), get_embedder(), merge_threshold=0.86)
    print(f"arm A context source: live graph, {len(wg.canonical)} concepts")

    resume = OUT / "runs.json"
    runs = json.loads(resume.read_text(encoding="utf-8")) if resume.exists() else []
    done = {(r["chunk"], r["arm"]) for r in runs}

    for src, text in chunks:
        for arm in ("A", "B"):
            if (src, arm) in done:
                continue
            if arm == "A":
                user = ex.build_user_prompt_with_context(text, wg.context_for(text))
            else:
                user = f"{SCHEMA_CONTEXT}\n\n{ex.build_user_prompt(text)}"
            t0 = time.time()
            try:
                raw = prov.complete(ex.SYSTEM_PROMPT, user)
                err = None
            except Exception as e:
                raw, err = "", str(e)[:120]
            dt = time.time() - t0
            obj = ex.extract_json(raw) if raw else None
            frag = ex.normalize_fragment(obj, src) if obj else {"outcomes": [], "relations": []}
            ok, bad = canon_stats(frag)
            rec = {"chunk": src, "arm": arm, "prompt_tokens": approx_tokens(user),
                   "prompt_chars": len(user), "secs": round(dt, 1), "error": err,
                   "parsed": obj is not None,
                   "outcomes": len(frag["outcomes"]),
                   "characteristics": sum(len(o.get("characteristics") or []) for o in frag["outcomes"]),
                   "relations": len(frag["relations"]),
                   "emite_canonical": ok, "emite_inverted": bad}
            runs.append(rec)
            resume.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")
            (OUT / f"{src.replace('#','_')}_{arm}.txt").write_text(
                f"=== PROMPT ({rec['prompt_tokens']} tok) ===\n{user}\n\n=== RESPONSE ===\n{raw}",
                encoding="utf-8")
            print(f"  {src:<14} arm {arm}: {rec['prompt_tokens']:>5} tok  "
                  f"outc={rec['outcomes']:>2} chars={rec['characteristics']:>3} "
                  f"rel={rec['relations']:>2} emite ok/inv={ok}/{bad} "
                  f"{'PARSE-FAIL' if not rec['parsed'] else ''} {rec['secs']}s", flush=True)
    wg.close()

    def agg(arm):
        rs = [r for r in runs if r["arm"] == arm]
        n = len(rs) or 1
        return {"n": len(rs),
                "prompt_tokens": sum(r["prompt_tokens"] for r in rs) / n,
                "outcomes": sum(r["outcomes"] for r in rs) / n,
                "characteristics": sum(r["characteristics"] for r in rs) / n,
                "relations": sum(r["relations"] for r in rs) / n,
                "yield": sum(r["outcomes"] + r["relations"] for r in rs) / n,
                "parse_fails": sum(0 if r["parsed"] else 1 for r in rs),
                "emite_ok": sum(r["emite_canonical"] for r in rs),
                "emite_inv": sum(r["emite_inverted"] for r in rs)}
    A, B = agg("A"), agg("B")
    tok_ratio = B["prompt_tokens"] / A["prompt_tokens"] if A["prompt_tokens"] else 0
    yield_ratio = B["yield"] / A["yield"] if A["yield"] else 0
    bt = B["emite_ok"] + B["emite_inv"]
    b_canon = B["emite_ok"] / bt if bt else None
    at = A["emite_ok"] + A["emite_inv"]
    a_canon = A["emite_ok"] / at if at else None

    print("\n=== AGGREGATE ===")
    for name, d in (("A concept-sea", A), ("B distilled  ", B)):
        print(f"  {name}: prompt={d['prompt_tokens']:>6.0f} tok  outc={d['outcomes']:.1f} "
              f"chars={d['characteristics']:.1f} rel={d['relations']:.1f} "
              f"yield={d['yield']:.1f}  parse_fails={d['parse_fails']}  "
              f"emite ok/inv={d['emite_ok']}/{d['emite_inv']}")

    res = {"A": A, "B": B, "token_ratio": round(tok_ratio, 3),
           "yield_ratio": round(yield_ratio, 3),
           "B_canonical_rate": b_canon, "A_canonical_rate": a_canon, "runs": runs}
    (OUT / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== PRE-REGISTERED CHECKS ===")
    def chk(n, v, ok, t):
        print(f"  {n:<40} {v:>9}  target {t:<14} {'PASS' if ok else 'FAIL'}")
    chk("prompt tokens B vs A", f"{tok_ratio:.2f}x", tok_ratio <= 0.334, "<= 0.33x")
    chk("yield B vs A (non-inferiority)", f"{yield_ratio:.2f}x", yield_ratio >= 0.75, ">= 0.75x")
    chk("B emite canonical rate", f"{b_canon:.0%}" if b_canon is not None else "n/a",
        b_canon is not None and b_canon >= 0.90, ">= 90%")
    chk("B parse failures", B["parse_fails"], B["parse_fails"] <= A["parse_fails"] + 1,
        f"<= {A['parse_fails']+1}")
    print(f"\nprompts+responses written to {OUT} (for the human spot-check)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
