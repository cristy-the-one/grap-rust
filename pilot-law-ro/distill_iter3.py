"""Distillation Iteration 3 — is high entropy genuine polysemy or label noise?

Pre-registered in docs/experiments/distillation-journal.md. Compares each
relation's entropy over (class,class) pairs computed two ways:
  * INDUCED labels  (morphology seeds + propagated assignments) — iteration 2
  * SEEDED labels only (high-precision morphology rules, propagation dropped)
If entropy falls materially under seeded-only labels, the generic relations were
generic because of label noise; if it does not, they are genuinely polysemous.

Deterministic, no LLM, reuses iteration 1/2 caches.
"""
from __future__ import annotations
import json, math, os, sys, io, time
from collections import Counter, defaultdict
from pathlib import Path
import sqlite3

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
OUT = PILOT / "distillation" / "iter-3"
OUT.mkdir(parents=True, exist_ok=True)
IT1, IT2 = PILOT / "distillation" / "iter-1", PILOT / "distillation" / "iter-2"
SNAP = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    sorted(PILOT.glob("_backup-*"))[-1] / "kee-workdir" / "graph_store.db")

os.environ.setdefault("GRAP_EMBED_BACKEND", "ollama")
os.environ.setdefault("GRAP_EMBED_MODEL", "bge-m3")
os.environ.setdefault("GRAP_EMBED_BASE_URL", "http://127.0.0.1:11434/v1")
sys.path.insert(0, str(ROOT / "mcp-server"))
from grap_mcp.kee.reconcile import fold                   # noqa: E402
from distill_iter1 import classify, cosine                # noqa: E402
sys.stdout = io.TextIOWrapper(open(1, "wb", closefd=False), encoding="utf-8",
                              errors="replace", line_buffering=True)

VERB_SIM, ASSIGN_FLOOR, W_NAME, W_ROLE = 0.72, 0.55, 0.6, 0.4
SPECIFIC_MAX_H, MIN_REL_SUPPORT = 0.50, 8


def norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def entropy(counter):
    tot = sum(counter.values())
    if tot == 0 or len(counter) <= 1:
        return 0.0, tot
    H = -sum((n / tot) * math.log(n / tot) for n in counter.values())
    return H / math.log(len(counter)), tot


def main():
    t0 = time.time()
    c = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)
    concepts = {cid: nm for cid, nm in c.execute("SELECT concept_id, canonical FROM concepts")}
    edges = list(c.execute("SELECT from_id, to_id, type FROM edges"))

    # verb clusters (cached from iter-1)
    vvecs = json.loads((IT1 / "verb_embeddings.json").read_text(encoding="utf-8"))
    verbs = [v for v, _ in Counter(t for _, _, t in edges).most_common()]
    clusters = []
    for v in verbs:
        vec = vvecs[v]; best, bs = None, -1.0
        for cl in clusters:
            s = cosine(vec, cl["vec"])
            if s > bs:
                best, bs = cl, s
        if best is not None and bs >= VERB_SIM:
            best["members"].append(v)
        else:
            clusters.append({"anchor": v, "members": [v], "vec": vec})
    verb2cl = {m: i for i, cl in enumerate(clusters) for m in cl["members"]}
    rel_name = {i: cl["anchor"] for i, cl in enumerate(clusters)}
    R = len(clusters)

    # labels: seeded vs induced (reproduce iter-2 exactly)
    seeded = {cid: k for cid, k in ((c_, classify(n)) for c_, n in concepts.items())
              if k != "Unclassified"}
    nvecs = {int(k): v for k, v in json.loads(
        (IT2 / "concept_embeddings.json").read_text(encoding="utf-8")).items()}
    role = defaultdict(lambda: [0.0] * (2 * R))
    for f, t, verb in edges:
        ci = verb2cl.get(verb)
        if ci is None:
            continue
        role[f][ci] += 1.0; role[t][R + ci] += 1.0
    role = {k: norm(v) for k, v in role.items()}
    classes = sorted(set(seeded.values()))
    dim = len(next(iter(nvecs.values())))
    cent_n, cent_r = {}, {}
    for k in classes:
        mem = [cid for cid, kk in seeded.items() if kk == k]
        cent_n[k] = norm([sum(nvecs[m][d] for m in mem) / len(mem) for d in range(dim)])
        rms = [role[m] for m in mem if m in role]
        cent_r[k] = norm([sum(r[d] for r in rms) / len(rms) for d in range(2 * R)]) if rms else [0.0] * (2 * R)
    induced = dict(seeded)
    for cid in concepts:
        if cid in induced:
            continue
        nv, rv = nvecs[cid], role.get(cid)
        best, bs = None, -1.0
        for k in classes:
            s = W_NAME * cosine(nv, cent_n[k]) + (W_ROLE * cosine(rv, cent_r[k]) if rv else 0.0)
            if s > bs:
                best, bs = k, s
        if bs >= ASSIGN_FLOOR:
            induced[cid] = best

    def pairs_for(labels):
        per = defaultdict(Counter)
        for f, t, verb in edges:
            ci = verb2cl.get(verb)
            if ci is None:
                continue
            a, b = labels.get(f), labels.get(t)
            if a and b:                     # only fully-labelled edges
                per[rel_name[ci]][(a, b)] += 1
        return per

    per_ind, per_seed = pairs_for(induced), pairs_for(seeded)

    rows = []
    for rel, pi in per_ind.items():
        Hi, ni = entropy(pi)
        if ni < MIN_REL_SUPPORT:
            continue
        Hs, ns = entropy(per_seed.get(rel, Counter()))
        rows.append({"relation": rel, "support_induced": ni, "H_induced": round(Hi, 3),
                     "support_seeded": ns, "H_seeded": round(Hs, 3),
                     "delta_H": round(Hi - Hs, 3),
                     "pairs_induced": len(pi), "pairs_seeded": len(per_seed.get(rel, {})),
                     "was_generic": Hi > SPECIFIC_MAX_H,
                     "now_specific": ns >= MIN_REL_SUPPORT and Hs <= SPECIFIC_MAX_H})
    rows.sort(key=lambda r: -r["support_induced"])

    generic = [r for r in rows if r["was_generic"]]
    moved = [r for r in generic if r["now_specific"]]
    drops = [r["delta_H"] for r in generic if r["support_seeded"] >= MIN_REL_SUPPORT]
    mean_drop = sum(drops) / len(drops) if drops else 0.0
    aproba = next((r for r in rows if r["relation"] == "aproba"), None)

    print(f"snapshot: {SNAP.parent.parent.name}   relations analysed: {len(rows)}")
    print(f"\n{'relation':<32} {'sup(i)':>6} {'H(ind)':>7} {'sup(s)':>6} {'H(seed)':>7} {'ΔH':>6}  moved")
    print("-" * 78)
    for r in rows[:14]:
        print(f"{r['relation'][:32]:<32} {r['support_induced']:>6} {r['H_induced']:>7.2f} "
              f"{r['support_seeded']:>6} {r['H_seeded']:>7.2f} {r['delta_H']:>+6.2f}  "
              f"{'YES' if r['now_specific'] and r['was_generic'] else ''}")

    res = {"snapshot": str(SNAP), "relations": rows,
           "generic_count": len(generic), "moved_to_specific": len(moved),
           "moved_names": [r["relation"] for r in moved],
           "mean_entropy_drop_on_generic": round(mean_drop, 3),
           "aproba_H_seeded": aproba["H_seeded"] if aproba else None}
    (OUT / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== PRE-REGISTERED CHECKS ===")
    def chk(n, v, ok, t):
        print(f"  {n:<44} {v:>7}   target {t:<10} {'PASS' if ok else 'FAIL'}")
    chk("generic relations that became specific", len(moved), len(moved) >= 3, ">= 3")
    chk("mean entropy drop on generic relations", f"{mean_drop:+.3f}", mean_drop >= 0.10, ">= 0.10")
    chk("aproba entropy (seeded-only)", f"{aproba['H_seeded']:.2f}" if aproba else "n/a",
        bool(aproba) and aproba["H_seeded"] <= 0.6, "<= 0.60")
    print(f"\nwrote {OUT}  ({time.time()-t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
