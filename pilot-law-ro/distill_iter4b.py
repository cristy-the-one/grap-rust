"""Distillation Iteration 4b — schema as CONSTRAINT, not template.

Pre-registered in docs/experiments/distillation-journal.md. Three arms over the
same MID-DOCUMENT held-out chunks:
  A   concept-sea (baseline, unchanged)
  B2  schema as a closed vocabulary + "assert only if this text states it"
  C   hybrid: class vocabulary + bounded instance NAMES only (nothing to instantiate)

Adds the qualitative checks iteration 4 lacked: per-chunk relation-count variance
(a template-filler has ~zero), class-name-as-instance rate, notation leakage, and
schema-violating relations as an automatable fabrication proxy.
"""
from __future__ import annotations
import json, os, sys, io, time, statistics as st
from collections import Counter
from pathlib import Path

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
OUT = PILOT / "distillation" / "iter-4b"
OUT.mkdir(parents=True, exist_ok=True)
N_CHUNKS = int(os.environ.get("ITER4B_CHUNKS", "10"))

for k, v in {"GRAP_LLM_BASE_URL": "http://127.0.0.1:8080/v1",
             "GRAP_LLM_MODEL": "ternary-bonsai-27b",
             "GRAP_LLM_EXTRA_BODY": '{"chat_template_kwargs": {"enable_thinking": false}}',
             "GRAP_LLM_TIMEOUT": "420", "GRAP_LLM_MAX_TOKENS": "2048",
             "GRAP_LLM_RETRIES": "2", "GRAP_EMBED_BACKEND": "ollama",
             "GRAP_EMBED_MODEL": "bge-m3",
             "GRAP_EMBED_BASE_URL": "http://127.0.0.1:11434/v1"}.items():
    os.environ.setdefault(k, v)
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

CLASS_NAMES = ["NormativeAct", "AuthoritativeBody", "Official", "OfficialGazette",
               "StructuralPart", "LegalChallenge", "Budget"]

# B2: closed vocabulary, no arrows, explicit permission-not-obligation.
B2_CONTEXT = (
    "VOCABULARY for this corpus. Prefer these names for kinds of thing when they "
    "apply: " + ", ".join(CLASS_NAMES) + ".\n"
    "Permitted relation types: emite, contrasemnează, adoptată de, se referă la, "
    "fac parte integrantă din, se face public, modificarea şi completarea.\n"
    "IMPORTANT: these are permitted, NOT required. Assert a relation ONLY if THIS "
    "text states it. Omit any type this text does not support. Never invent a "
    "relation to use up a type. Name concepts as the text names them (the actual "
    "act, body or person), never with a kind-name."
)

# schema domain/range for the fabrication proxy (from iteration 3b)
SCHEMA_DR = {"emite": ("AuthoritativeBody", "NormativeAct"),
             "contrasemnează": ("Official", "NormativeAct"),
             "adoptată de": ("NormativeAct", "AuthoritativeBody")}
CLASS_LOWER = {c.lower() for c in CLASS_NAMES}


def approx_tokens(s): return max(1, len(s) // 4)


def midchunks(n):
    """Mid-document chunks (index >= 1) from never-ingested documents."""
    present = {p.name for p in (PILOT / "docs").glob("*.json")}
    pool = sorted((p for p in (ROOT / "real-data-law-ro").glob("*.json")
                   if p.name not in present), key=lambda p: p.stat().st_size)
    pool = pool[int(len(pool) * 0.6):]      # bigger docs -> have mid chunks
    out = []
    for f in pool:
        try:
            t, _ = _text_and_label_from_json(json.loads(f.read_text(encoding="utf-8")), f.stem)
        except Exception:
            continue
        ch = chunk_text(t, 2000)
        if len(ch) >= 3:
            out.append((f"{f.stem}#1", ch[1]))
            if len(out) < n and len(ch) >= 4:
                out.append((f"{f.stem}#2", ch[2]))
        if len(out) >= n:
            break
    return out[:n]


def instance_names_context(wg, text, cap=24):
    ctx = wg.context_for(text)
    names = [n for n in (getattr(ctx, "entities", None) or [])][:cap]
    if not names:
        return None
    return ("VOCABULARY for this corpus. Prefer these names for kinds of thing when "
            "they apply: " + ", ".join(CLASS_NAMES) + ".\n"
            "KNOWN CONCEPT NAMES (reuse the exact name if the same concept appears "
            "here; this list is for naming consistency only and asserts nothing):\n"
            + "\n".join(f"- {n}" for n in names) +
            "\nAssert a relation ONLY if THIS text states it. Name concepts as the "
            "text names them, never with a kind-name.")


def metrics(frag):
    cls_named = notation = viol = schema_typed = 0
    for o in frag.get("outcomes", []):
        if str(o.get("name", "")).strip().lower().replace(" ", "") in CLASS_LOWER:
            cls_named += 1
    for r in frag.get("relations", []):
        t = str(r.get("type", "")).strip()
        if "-->" in t or "--" in t:
            notation += 1
        if t in SCHEMA_DR:
            schema_typed += 1
            d, rg = SCHEMA_DR[t]
            if (classify(str(r.get("from", ""))), classify(str(r.get("to", "")))) != (d, rg):
                viol += 1
    return cls_named, notation, viol, schema_typed


def main():
    prov = get_provider()
    chunks = midchunks(N_CHUNKS)
    print(f"mid-document held-out chunks: {len(chunks)}  {[c for c,_ in chunks][:4]}...")
    wg = WorkingGraph.open(str(PILOT / "kee-workdir"), get_embedder(), merge_threshold=0.86)

    resume = OUT / "runs.json"
    runs = json.loads(resume.read_text(encoding="utf-8")) if resume.exists() else []
    done = {(r["chunk"], r["arm"]) for r in runs}

    for src, text in chunks:
        for arm in ("A", "B2", "C"):
            if (src, arm) in done:
                continue
            if arm == "A":
                user = ex.build_user_prompt_with_context(text, wg.context_for(text))
            elif arm == "B2":
                user = f"{B2_CONTEXT}\n\n{ex.build_user_prompt(text)}"
            else:
                ic = instance_names_context(wg, text)
                user = f"{ic}\n\n{ex.build_user_prompt(text)}" if ic else \
                       f"{B2_CONTEXT}\n\n{ex.build_user_prompt(text)}"
            t0 = time.time()
            try:
                raw = prov.complete(ex.SYSTEM_PROMPT, user); err = None
            except Exception as e:
                raw, err = "", str(e)[:100]
            obj = ex.extract_json(raw) if raw else None
            frag = ex.normalize_fragment(obj, src) if obj else {"outcomes": [], "relations": []}
            cn, nt, vi, sch = metrics(frag)
            rec = {"chunk": src, "arm": arm, "prompt_tokens": approx_tokens(user),
                   "secs": round(time.time() - t0, 1), "error": err, "parsed": obj is not None,
                   "outcomes": len(frag["outcomes"]), "relations": len(frag["relations"]),
                   "rel_types": sorted({str(r.get("type", "")).strip() for r in frag["relations"]}),
                   "class_named": cn, "notation_leak": nt,
                   "schema_typed": sch, "schema_violations": vi}
            runs.append(rec)
            resume.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")
            (OUT / f"{src.replace('#','_')}_{arm}.txt").write_text(
                f"=== PROMPT ({rec['prompt_tokens']} tok) ===\n{user}\n\n=== RESPONSE ===\n{raw}",
                encoding="utf-8")
            print(f"  {src:<12} {arm:<2}: {rec['prompt_tokens']:>5}tok outc={rec['outcomes']:>2} "
                  f"rel={rec['relations']:>2} clsname={cn} notation={nt} viol={vi}/{sch} "
                  f"{'PARSEFAIL' if not rec['parsed'] else ''} {rec['secs']}s", flush=True)
    wg.close()

    print("\n=== AGGREGATE ===")
    agg = {}
    for arm in ("A", "B2", "C"):
        rs = [r for r in runs if r["arm"] == arm]
        if not rs:
            continue
        rel = [r["relations"] for r in rs]
        n = len(rs)
        yields = [r["outcomes"] + r["relations"] for r in rs]
        cv = (st.pstdev(rel) / (sum(rel) / n)) if sum(rel) else 0.0
        sch = sum(r["schema_typed"] for r in rs)
        agg[arm] = {"n": n, "tok": sum(r["prompt_tokens"] for r in rs) / n,
                    "yield": sum(yields) / n, "rel_cv": cv,
                    "class_named": sum(r["class_named"] for r in rs),
                    "notation": sum(r["notation_leak"] for r in rs),
                    "viol": sum(r["schema_violations"] for r in rs), "schema_typed": sch,
                    "viol_rate": (sum(r["schema_violations"] for r in rs) / sch) if sch else 0.0,
                    "parse_fails": sum(0 if r["parsed"] else 1 for r in rs),
                    "distinct_types": len({t for r in rs for t in r["rel_types"]})}
        d = agg[arm]
        print(f"  {arm:<2}: tok={d['tok']:>6.0f} yield={d['yield']:>4.1f} relCV={d['rel_cv']:.2f} "
              f"types={d['distinct_types']:>2} clsname={d['class_named']} notation={d['notation']} "
              f"viol={d['viol']}/{d['schema_typed']} ({d['viol_rate']:.0%}) pf={d['parse_fails']}")
    (OUT / "result.json").write_text(json.dumps({"agg": agg, "runs": runs}, indent=2,
                                                ensure_ascii=False), encoding="utf-8")
    A = agg["A"]
    print("\n=== PRE-REGISTERED CHECKS ===")
    for arm in ("B2", "C"):
        d = agg.get(arm)
        if not d:
            continue
        tr, yr = d["tok"] / A["tok"], d["yield"] / A["yield"]
        print(f"  -- arm {arm} --")
        def chk(n, v, ok, t): print(f"    {n:<34} {v:>8}  target {t:<14} {'PASS' if ok else 'FAIL'}")
        chk("prompt tokens vs A", f"{tr:.2f}x", tr <= 0.5, "<= 0.5x")
        chk("yield vs A (two-sided)", f"{yr:.2f}x", 0.75 <= yr <= 1.5, "0.75-1.5x")
        chk("relation-count CV (template det.)", f"{d['rel_cv']:.2f}", d["rel_cv"] >= 0.25, ">= 0.25")
        chk("class-name-as-instance", d["class_named"], d["class_named"] == 0, "0")
        chk("notation leakage", d["notation"], d["notation"] == 0, "0")
        chk("schema violations", f"{d['viol_rate']:.0%}", d["viol_rate"] <= 0.10, "<= 10%")
    print(f"\nartifacts: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
