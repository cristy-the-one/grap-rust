"""Do LARGE documents yield extraction output at the same rate as small ones?

Half the remaining decode (5,245 of 10,291 chunks, ~9 days) sits in the 92
documents larger than the 25-chunk batch budget. They are budget annexes and
tariff schedules — the same class that produced the mid-round empty-chunk
clusters, and `50956.json` is one of the documents already measured as
table-heavy in the layout-cleaning experiment. Before spending nine days on
them, measure what they actually return.

PRE-REGISTERED. Written before the numbers exist, thresholds included.

Design
------
Two arms, same model, same prompt, same context mode, same session:

  LARGE  — chunks sampled from unprocessed documents of >25 chunks
  SMALL  — chunks sampled from unprocessed documents of <=24 chunks

Both arms are drawn from UNPROCESSED documents, so neither has been seen before
and neither is contaminated by having already contributed to the graph. Each arm
runs in its own throwaway working graph, so (a) the live pilot state is never
touched, and (b) the arms cannot seed each other's context.

Sampling is seeded, so the same chunks come back on a re-run.

Primary metric: CHARACTERISTICS PER CHUNK — the same metric the layout-cleaning
A/B was judged on, and the one closest to "knowledge extracted".

Decision rule (fixed in advance)
--------------------------------
  ratio = LARGE characteristics/chunk  ÷  SMALL characteristics/chunk

  ratio >= 0.80  -> the tail is worth processing as-is; just run it.
  ratio <  0.50  -> the tail is poor value. Nine days of decode for half the
                    output rate justifies revisiting table-preserving reduction
                    (docs/table-preserving-reduction.md), this time motivated by
                    the documents that dominate the remaining cost rather than
                    by five sample chunks.
  0.50-0.80      -> marginal; report both numbers and decide with the operator.

Also recorded, because a mean can hide the shape: no-fragment rate per arm (the
empty-chunk hypothesis), relations/chunk, distinct relation types, and input
characters per chunk (a large document's chunks are the same 2000-char cap, so
this should NOT differ — if it does, the sampling is wrong).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
DOCS = PILOT / "docs"
POOL = ROOT / "real-data-law-ro"
OUT = PILOT / "yield-probe.json"

CHUNKS_PER_ARM = 30          # ~30 x ~110s ~= 55 min per arm
BUDGET = 25                  # the batch chunk budget that defines "large"
CHUNK_CHARS = 2000
SEED = 20260729

os.environ.setdefault("GRAP_WORKSPACE", str(PILOT / "ws"))
os.environ["GRAP_ROLE"] = "admin"
os.environ["GRAP_LLM_BASE_URL"] = "http://127.0.0.1:8080/v1"
os.environ["GRAP_LLM_MODEL"] = "ternary-bonsai-27b"
os.environ["GRAP_LLM_EXTRA_BODY"] = '{"chat_template_kwargs": {"enable_thinking": false}}'
os.environ["GRAP_LLM_TIMEOUT"] = "420"
os.environ["GRAP_LLM_RETRIES"] = "2"
os.environ["GRAP_LLM_MAX_TOKENS"] = "2048"
os.environ["GRAP_KEE_CONTEXT_MODE"] = "names"
os.environ["GRAP_KEE_CLASS_VOCAB"] = ("NormativeAct, AuthoritativeBody, Official, "
                                      "OfficialGazette, StructuralPart, "
                                      "LegalChallenge, Budget")
os.environ["GRAP_EMBED_BACKEND"] = "ollama"
os.environ["GRAP_EMBED_MODEL"] = "bge-m3"
os.environ["GRAP_EMBED_BASE_URL"] = "http://127.0.0.1:11434/v1"
os.environ.pop("GRAP_LLM_STUB", None)

sys.path.insert(0, str(ROOT / "mcp-server"))
from grap_mcp.ingest import chunk_text                      # noqa: E402
from grap_mcp.kee import extract, graph_extract             # noqa: E402
from grap_mcp.kee.embeddings import get_embedder            # noqa: E402
from grap_mcp.kee.llm import get_provider                   # noqa: E402
from grap_mcp.kee.pipeline import _text_and_label_from_json  # noqa: E402
from grap_mcp.kee.working_graph import WorkingGraph         # noqa: E402


def doc_chunks(p):
    try:
        text, _ = _text_and_label_from_json(json.loads(p.read_text(encoding="utf-8")), p.stem)
    except Exception:
        return []
    return chunk_text(text, max_chars=CHUNK_CHARS)


def sample_arm(docs, n, rng):
    """Take chunks spread ACROSS documents, not the first n of one document.

    A document's opening chunks are its title and preamble, which are unusually
    entity-rich; sampling only those would flatter any arm containing big
    documents. Round-robin across documents, at a random offset in each."""
    per_doc = {}
    for p in docs:
        cs = doc_chunks(p)
        if cs:
            per_doc[p] = cs
    picked, order = [], list(per_doc)
    rng.shuffle(order)
    i = 0
    while len(picked) < n and order:
        p = order[i % len(order)]
        cs = per_doc[p]
        if cs:
            j = rng.randrange(len(cs))
            picked.append((f"{p.stem}#{j}", cs.pop(j)))
        else:
            order.remove(p)
            continue
        i += 1
    return picked


def run_arm(name, chunks, provider, embedder):
    workdir = tempfile.mkdtemp(prefix=f"yield-{name}-")
    stats = {"arm": name, "chunks": len(chunks), "outcomes": 0, "characteristics": 0,
             "relations": 0, "rel_types": set(), "no_fragment": 0,
             "unparsed": 0, "empty": 0, "provider": 0, "input_chars": 0,
             "per_chunk_chars": []}
    try:
        wg = WorkingGraph.open(workdir, embedder, merge_threshold=0.86, context_cap=8)
        try:
            for k, (source, text) in enumerate(chunks, 1):
                stats["input_chars"] += len(text)
                frag, reason = graph_extract._extract_with_context(
                    provider, source, text, wg.context_for(text))
                if frag is None:
                    stats["no_fragment"] += 1
                    stats[reason or "unparsed"] += 1
                    stats["per_chunk_chars"].append(0)
                    print(f"  [{name} {k}/{len(chunks)}] {source}: no fragment ({reason})",
                          flush=True)
                    continue
                nchar = sum(len(o.get("characteristics") or []) for o in frag["outcomes"])
                stats["outcomes"] += len(frag["outcomes"])
                stats["characteristics"] += nchar
                stats["relations"] += len(frag["relations"])
                stats["rel_types"].update(r["type"] for r in frag["relations"])
                stats["per_chunk_chars"].append(nchar)
                wg.upsert_fragment(frag, source)
                print(f"  [{name} {k}/{len(chunks)}] {source}: "
                      f"{len(frag['outcomes'])} outcomes, {nchar} chars, "
                      f"{len(frag['relations'])} rels", flush=True)
        finally:
            wg.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    stats["rel_types"] = len(stats["rel_types"])
    return stats


def main():
    rng = random.Random(SEED)
    present = {p.name for p in DOCS.glob("*.json")}
    rem = [p for p in POOL.glob("*.json") if p.name not in present]
    large = [p for p in rem if len(doc_chunks(p)) > BUDGET]
    small = [p for p in rem if 0 < len(doc_chunks(p)) <= BUDGET]
    print(f"[probe] unprocessed: {len(rem)} docs -> {len(large)} large (>{BUDGET} chunks), "
          f"{len(small)} small", flush=True)

    arms = {"LARGE": sample_arm(large, CHUNKS_PER_ARM, rng),
            "SMALL": sample_arm(small, CHUNKS_PER_ARM, rng)}
    for a, cs in arms.items():
        docs = len({s.split('#')[0] for s, _ in cs})
        print(f"[probe] {a}: {len(cs)} chunks from {docs} documents", flush=True)

    provider, embedder = get_provider(), get_embedder()
    t0 = time.time()
    results = {a: run_arm(a, cs, provider, embedder) for a, cs in arms.items()}
    elapsed = time.time() - t0

    def per_chunk(s, key):
        return s[key] / max(1, s["chunks"])

    L, S = results["LARGE"], results["SMALL"]
    ratio = per_chunk(L, "characteristics") / max(1e-9, per_chunk(S, "characteristics"))
    verdict = ("PROCESS AS-IS (>=0.80)" if ratio >= 0.80 else
               "POOR VALUE (<0.50) — revisit table-preserving reduction" if ratio < 0.50 else
               "MARGINAL (0.50-0.80) — decide with the operator")
    summary = {"seed": SEED, "chunks_per_arm": CHUNKS_PER_ARM, "elapsed_s": round(elapsed),
               "ratio_characteristics_per_chunk": round(ratio, 3), "verdict": verdict,
               "arms": {a: {k: (v if not isinstance(v, list) else v) for k, v in s.items()}
                        for a, s in results.items()}}
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== YIELD PROBE ===", flush=True)
    print(f"{'metric':28} {'LARGE':>10} {'SMALL':>10}")
    for label, key in (("outcomes / chunk", "outcomes"),
                       ("characteristics / chunk", "characteristics"),
                       ("relations / chunk", "relations"),
                       ("input chars / chunk", "input_chars")):
        print(f"{label:28} {per_chunk(L, key):10.2f} {per_chunk(S, key):10.2f}")
    print(f"{'no-fragment rate':28} {L['no_fragment']/max(1,L['chunks']):10.3f} "
          f"{S['no_fragment']/max(1,S['chunks']):10.3f}")
    print(f"{'distinct relation types':28} {L['rel_types']:10d} {S['rel_types']:10d}")
    print(f"\nratio (characteristics/chunk, LARGE/SMALL) = {ratio:.3f}")
    print(f"VERDICT: {verdict}")
    print(f"\nelapsed {elapsed/60:.1f} min; results -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
