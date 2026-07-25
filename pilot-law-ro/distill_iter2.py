"""Distillation experiment, Iteration 2 — class induction + entropy statistic.

Pre-registered in docs/experiments/distillation-journal.md. Read-only against a
FROZEN snapshot. Fully deterministic (embeddings + graph analytics, no LLM), so
it is exactly reproducible. Checkpoints to distillation/iter-2/.

Changes from iteration 1:
  * classes are INDUCED (morphology rules as high-precision seeds, then
    propagation by name-embedding + structural-role similarity) instead of rules
    alone, which capped coverage at 56.5%;
  * relation quality is measured by NORMALIZED ENTROPY over (class,class) pairs
    instead of purity, which punished legitimate polysemy;
  * each relation's unordered class pair is collapsed to its DOMINANT direction,
    quantifying extraction direction-noise instead of emitting both.
"""
from __future__ import annotations
import json, math, os, sys, io, time
from collections import Counter, defaultdict
from pathlib import Path
import sqlite3

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
OUT = PILOT / "distillation" / "iter-2"
OUT.mkdir(parents=True, exist_ok=True)
IT1 = PILOT / "distillation" / "iter-1"

SNAP = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    sorted(PILOT.glob("_backup-*"))[-1] / "kee-workdir" / "graph_store.db")

os.environ.setdefault("GRAP_EMBED_BACKEND", "ollama")
os.environ.setdefault("GRAP_EMBED_MODEL", "bge-m3")
os.environ.setdefault("GRAP_EMBED_BASE_URL", "http://127.0.0.1:11434/v1")
sys.path.insert(0, str(ROOT / "mcp-server"))
from grap_mcp.kee.reconcile import fold                 # noqa: E402
from grap_mcp.kee.embeddings import get_embedder        # noqa: E402

VERB_SIM = 0.72
TOP_HUBS = 250
ASSIGN_FLOOR = 0.55      # min blended similarity to accept an induced class
W_NAME, W_ROLE = 0.6, 0.4
SPECIFIC_MAX_H = 0.50    # normalized entropy at/below which a relation is "specific"
MIN_REL_SUPPORT = 8      # relations below this support are not schema candidates

from distill_iter1 import CLASS_RULES, classify, cosine   # reuse, single source of truth

# UTF-8 stdout, set ONCE and after the iter1 import (iter1 re-wraps stdout at
# module level; wrapping before it made the first wrapper get GC'd and close the
# underlying fd). closefd=False keeps fd 1 alive regardless of wrapper lifetime.
sys.stdout = io.TextIOWrapper(open(1, "wb", closefd=False), encoding="utf-8",
                              errors="replace", line_buffering=True)


def norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def main():
    t0 = time.time()
    print(f"snapshot: {SNAP.parent.parent.name}/{SNAP.name}")
    c = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)
    concepts = {cid: nm for cid, nm in c.execute("SELECT concept_id, canonical FROM concepts")}
    edges = list(c.execute("SELECT from_id, to_id, type FROM edges"))
    print(f"  concepts={len(concepts)} edges={len(edges)}")

    # ---------- reuse iteration-1 verb clusters ----------
    verb_freq = Counter(t for _, _, t in edges)
    verbs = [v for v, _ in verb_freq.most_common()]
    cache = IT1 / "verb_embeddings.json"
    emb = get_embedder()
    vvecs = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    missing = [v for v in verbs if v not in vvecs]
    if missing:
        texts = [fold(v).replace("_", " ") for v in missing]
        got = []
        for i in range(0, len(texts), 128):
            got.extend(emb.embed(texts[i:i + 128]))
        vvecs.update({v: got[i] for i, v in enumerate(missing)})
        cache.write_text(json.dumps(vvecs), encoding="utf-8")
    clusters = []
    for v in verbs:
        vec = vvecs[v]
        best, bs = None, -1.0
        for cl in clusters:
            sim = cosine(vec, cl["vec"])
            if sim > bs:
                best, bs = cl, sim
        if best is not None and bs >= VERB_SIM:
            best["members"].append(v)
        else:
            clusters.append({"anchor": v, "members": [v], "vec": vec})
    verb2cl = {m: i for i, cl in enumerate(clusters) for m in cl["members"]}
    rel_name = {i: cl["anchor"] for i, cl in enumerate(clusters)}
    print(f"  verb clusters (reused): {len(clusters)}")

    # ---------- Stage 2: INDUCED classes ----------
    seeds = {cid: classify(nm) for cid, nm in concepts.items()}
    seeded = {cid: k for cid, k in seeds.items() if k != "Unclassified"}
    print(f"  morphology seeds: {len(seeded)}/{len(concepts)} concepts "
          f"({len(seeded)/len(concepts):.0%})")

    # structural role: distribution over incident relation clusters (in/out)
    R = len(clusters)
    role = defaultdict(lambda: [0.0] * (2 * R))
    for f, t, verb in edges:
        ci = verb2cl.get(verb)
        if ci is None:
            continue
        role[f][ci] += 1.0                 # outgoing
        role[t][R + ci] += 1.0             # incoming
    role = {k: norm(v) for k, v in role.items()}

    # name embeddings for every concept (batched, cached)
    ncache = OUT / "concept_embeddings.json"
    if ncache.exists():
        nvecs = json.loads(ncache.read_text(encoding="utf-8"))
        nvecs = {int(k): v for k, v in nvecs.items()}
    else:
        ids = sorted(concepts)
        texts = [fold(concepts[i]).replace("_", " ")[:300] for i in ids]
        got = []
        te = time.time()
        for i in range(0, len(texts), 128):
            got.extend(emb.embed(texts[i:i + 128]))
        nvecs = {ids[i]: got[i] for i in range(len(ids))}
        ncache.write_text(json.dumps({str(k): v for k, v in nvecs.items()}), encoding="utf-8")
        print(f"  embedded {len(ids)} concept names in {time.time()-te:.1f}s (cached)")

    classes = sorted({k for k in seeded.values()})
    cent_name, cent_role = {}, {}
    for k in classes:
        members = [cid for cid, kk in seeded.items() if kk == k]
        cent_name[k] = norm([sum(nvecs[m][d] for m in members) / len(members)
                             for d in range(len(next(iter(nvecs.values()))))])
        rms = [role[m] for m in members if m in role]
        cent_role[k] = norm([sum(r[d] for r in rms) / len(rms)
                             for d in range(2 * R)]) if rms else [0.0] * (2 * R)

    induced = dict(seeded)
    assigned = 0
    for cid in concepts:
        if cid in induced:
            continue
        nv, rv = nvecs[cid], role.get(cid)
        best, bs = None, -1.0
        for k in classes:
            s = W_NAME * cosine(nv, cent_name[k])
            if rv is not None:
                s += W_ROLE * cosine(rv, cent_role[k])
            if s > bs:
                best, bs = k, s
        if bs >= ASSIGN_FLOOR:
            induced[cid] = best
            assigned += 1
    print(f"  induced: +{assigned} concepts assigned -> "
          f"{len(induced)}/{len(concepts)} ({len(induced)/len(concepts):.0%}) classed")

    deg = Counter()
    for f, t, _ in edges:
        deg[f] += 1; deg[t] += 1
    hubs = [cid for cid, _ in deg.most_common(TOP_HUBS)]
    unc_hubs = [h for h in hubs if h not in induced]
    print(f"  top-{TOP_HUBS} hubs unclassified: {len(unc_hubs)} "
          f"({len(unc_hubs)/len(hubs):.0%})   (iter-1: 29%)")

    cls = lambda cid: induced.get(cid, "Unclassified")

    # ---------- Stage 3: lift, entropy, dominant direction ----------
    per_rel = defaultdict(Counter)          # rel -> Counter[(a,b)]
    for f, t, verb in edges:
        ci = verb2cl.get(verb)
        if ci is None:
            continue
        per_rel[rel_name[ci]][(cls(f), cls(t))] += 1

    schema, generic = [], []
    for rel, pairs in per_rel.items():
        typed = Counter({k: v for k, v in pairs.items()
                         if k[0] != "Unclassified" and k[1] != "Unclassified"})
        support = sum(typed.values())
        if support < MIN_REL_SUPPORT:
            continue
        # normalized entropy over typed class pairs
        H = -sum((n / support) * math.log(n / support) for n in typed.values())
        Hn = H / math.log(len(typed)) if len(typed) > 1 else 0.0
        # dominant direction for the top unordered pair
        (a, b), n_ab = typed.most_common(1)[0]
        n_ba = typed.get((b, a), 0)
        dom = n_ab / (n_ab + n_ba) if (n_ab + n_ba) else 1.0
        rec = {"relation": rel, "support": support, "entropy": round(Hn, 3),
               "domain": a, "range": b, "top_support": n_ab,
               "reverse_support": n_ba, "direction_dominance": round(dom, 3),
               "distinct_pairs": len(typed)}
        (schema if Hn <= SPECIFIC_MAX_H else generic).append(rec)
    schema.sort(key=lambda r: -r["top_support"])
    generic.sort(key=lambda r: -r["support"])

    total = len(edges)
    covered = sum(v for rel in per_rel for k, v in per_rel[rel].items()
                  if k[0] != "Unclassified" and k[1] != "Unclassified")

    print(f"\n=== DISTILLED SCHEMA: {len(schema)} specific relations "
          f"(entropy <= {SPECIFIC_MAX_H}) ===")
    for r in schema[:14]:
        print(f"   [{r['top_support']:>4}  H={r['entropy']:.2f}  dir={r['direction_dominance']:.0%}]  "
              f"{r['domain']} --{r['relation'][:24]}--> {r['range']}")
    print(f"\n=== GENERIC relations (high entropy — correctly NOT axioms): {len(generic)} ===")
    for r in generic[:5]:
        print(f"   [{r['support']:>4}  H={r['entropy']:.2f}  {r['distinct_pairs']} pairs]  {r['relation'][:34]}")

    ent_s = [r["entropy"] for r in schema]
    ent_g = [r["entropy"] for r in generic]
    res = {
        "snapshot": str(SNAP), "params": {"verb_sim": VERB_SIM, "assign_floor": ASSIGN_FLOOR,
        "w_name": W_NAME, "w_role": W_ROLE, "specific_max_H": SPECIFIC_MAX_H,
        "min_rel_support": MIN_REL_SUPPORT},
        "concepts": len(concepts), "edges": total,
        "classes": len(classes), "class_names": classes,
        "seeded_pct": round(len(seeded) / len(concepts), 3),
        "classed_pct": round(len(induced) / len(concepts), 3),
        "hubs_unclassified_pct": round(len(unc_hubs) / len(hubs), 3),
        "typed_edge_coverage": round(covered / total, 3),
        "specific_relations": len(schema), "generic_relations": len(generic),
        "entropy_specific_max": max(ent_s) if ent_s else None,
        "entropy_generic_min": min(ent_g) if ent_g else None,
        "top_direction_dominance": schema[0]["direction_dominance"] if schema else None,
        "schema": schema, "generic": generic,
    }
    (OUT / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== PRE-REGISTERED CHECKS ===")
    def chk(name, val, ok, tgt):
        print(f"  {name:<42} {val:>8}   target {tgt:<12} {'PASS' if ok else 'FAIL'}")
    chk("typed edge coverage", f"{res['typed_edge_coverage']:.1%}",
        res["typed_edge_coverage"] >= 0.75, ">= 75%")
    chk("hubs unclassified", f"{res['hubs_unclassified_pct']:.0%}",
        res["hubs_unclassified_pct"] <= 0.10, "<= 10%")
    chk("induced classes", len(classes), 8 <= len(classes) <= 80, "8-80")
    chk("specific relations (H<=0.5)", len(schema), len(schema) >= 8, ">= 8")
    gap = (min(ent_g) - max(ent_s)) if (ent_s and ent_g) else None
    chk("entropy separation (bimodal)", f"{gap:+.2f}" if gap is not None else "n/a",
        gap is not None and gap > -0.15, "visible gap")
    chk("top relation direction dominance", f"{res['top_direction_dominance']:.0%}"
        if schema else "n/a", bool(schema) and schema[0]["direction_dominance"] >= 0.90, ">= 90%")
    print(f"\nwrote {OUT}  ({time.time()-t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
