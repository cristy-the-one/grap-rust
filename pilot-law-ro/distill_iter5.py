"""Distillation Iteration 5 — systematic axiom discovery.

Pre-registered in docs/experiments/distillation-journal.md. Discovery-first: score
EVERY candidate over the lifted graph, then look. Deterministic, no LLM.

Families:
  * inverse pairs  — instance-level (R1(x,y) & R2(y,x)) and type-level (A->B vs B->A)
  * subsumption    — R1 subset-of R2 by instance-pair containment (AMIE-style)

The emite / adoptata-de pair is a HELD-BACK validation case: it must be
rediscovered, not assumed. Nothing in this script mentions it.
"""
from __future__ import annotations
import json, math, os, sys, io, time
from collections import Counter, defaultdict
from pathlib import Path
import sqlite3

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
OUT = PILOT / "distillation" / "iter-5"
OUT.mkdir(parents=True, exist_ok=True)
IT1, IT2 = PILOT / "distillation" / "iter-1", PILOT / "distillation" / "iter-2"
SNAP = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    sorted(PILOT.glob("_backup-*"))[-1] / "kee-workdir" / "graph_store.db")

for k, v in {"GRAP_EMBED_BACKEND": "ollama", "GRAP_EMBED_MODEL": "bge-m3",
             "GRAP_EMBED_BASE_URL": "http://127.0.0.1:11434/v1"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(ROOT / "mcp-server"))
sys.path.insert(0, str(PILOT))
from distill_iter1 import classify, cosine        # noqa: E402
sys.stdout = io.TextIOWrapper(open(1, "wb", closefd=False), encoding="utf-8",
                              errors="replace", line_buffering=True)

VERB_SIM = 0.72
MIN_SUPPORT = 8          # a relation must have this many edges to be a candidate
MIN_CONF = 0.50          # confidence floor for a reported axiom


def main():
    t0 = time.time()
    c = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)
    concepts = {i: n for i, n in c.execute("SELECT concept_id, canonical FROM concepts")}
    edges = list(c.execute("SELECT from_id, to_id, type FROM edges"))
    print(f"snapshot {SNAP.parent.parent.name}: {len(concepts)} concepts, {len(edges)} edges")

    # ---- reuse iteration-1 verb clusters so "relations" are canonical ----
    vv = json.loads((IT1 / "verb_embeddings.json").read_text(encoding="utf-8"))
    verbs = [v for v, _ in Counter(t for _, _, t in edges).most_common()]
    clusters = []
    for v in verbs:
        vec = vv[v]; best, bs = None, -1.0
        for cl in clusters:
            s = cosine(vec, cl["vec"])
            if s > bs:
                best, bs = cl, s
        if best is not None and bs >= VERB_SIM:
            best["members"].append(v)
        else:
            clusters.append({"anchor": v, "members": [v], "vec": vec})
    v2c = {m: i for i, cl in enumerate(clusters) for m in cl["members"]}
    rn = {i: cl["anchor"] for i, cl in enumerate(clusters)}

    # ---- relation -> set of instance pairs ----
    pairs = defaultdict(set)
    for f, t, ty in edges:
        i = v2c.get(ty)
        if i is not None:
            pairs[rn[i]].add((f, t))
    rels = sorted([r for r, p in pairs.items() if len(p) >= MIN_SUPPORT],
                  key=lambda r: -len(pairs[r]))
    print(f"candidate relations (support >= {MIN_SUPPORT}): {len(rels)}")
    print(f"ordered pairs to score: {len(rels)*(len(rels)-1)}")

    # ---- class labels (seeded morphology; enough for type-level signal) ----
    lab = {i: classify(n) for i, n in concepts.items()}

    def typesig(r):
        cnt = Counter((lab[f], lab[t]) for f, t in pairs[r]
                      if lab[f] != "Unclassified" and lab[t] != "Unclassified")
        return cnt.most_common(1)[0] if cnt else (None, 0)

    # =========== FAMILY 1: inverse pairs ===========
    inverses = []
    for a in rels:
        pa, ra = pairs[a], {(y, x) for x, y in pairs[a]}
        for b in rels:
            if a >= b:                      # unordered, score once
                continue
            pb = pairs[b]
            inst = len(ra & pb)             # instance-level: R1(x,y) & R2(y,x)
            conf_i = inst / min(len(pa), len(pb))
            (sa, na), (sb, nb) = typesig(a), typesig(b)
            type_inv = bool(sa and sb and sa == (sb[1], sb[0]))
            # endpoint-population overlap gives the type-level signal weight
            subj_a = {x for x, _ in pa}; obj_a = {y for _, y in pa}
            subj_b = {x for x, _ in pb}; obj_b = {y for _, y in pb}
            pop = 0.0
            if type_inv:
                d1 = len(subj_a | obj_b) or 1
                d2 = len(obj_a | subj_b) or 1
                pop = 0.5 * (len(subj_a & obj_b) / d1 + len(obj_a & subj_b) / d2)
            score = max(conf_i, pop if type_inv else 0.0)
            if score >= 0.10:
                inverses.append({"kind": "inverse", "r1": a, "r2": b,
                                 "support_r1": len(pa), "support_r2": len(pb),
                                 "instance_overlap": inst,
                                 "instance_conf": round(conf_i, 3),
                                 "type_inverse": type_inv,
                                 "type_sig_r1": list(sa) if sa else None,
                                 "type_sig_r2": list(sb) if sb else None,
                                 "population_overlap": round(pop, 3),
                                 "score": round(score, 3)})
    inverses.sort(key=lambda d: -d["score"])

    # =========== FAMILY 2: subsumption R1 subset-of R2 ===========
    subs = []
    for a in rels:
        pa = pairs[a]
        for b in rels:
            if a == b:
                continue
            ov = len(pa & pairs[b])
            conf = ov / len(pa)
            if conf >= MIN_CONF and ov >= MIN_SUPPORT:
                subs.append({"kind": "subsumption", "sub": a, "sup": b,
                             "support": ov, "support_sub": len(pa),
                             "confidence": round(conf, 3)})
    subs.sort(key=lambda d: (-d["confidence"], -d["support"]))

    # =========== report ===========
    print(f"\n=== INVERSE CANDIDATES (score >= 0.10): {len(inverses)} ===")
    for d in inverses[:10]:
        sig = (f"  {d['type_sig_r1']}<->{d['type_sig_r2']}" if d["type_inverse"] else "")
        print(f"  [{d['score']:.2f}] {d['r1'][:26]:<26} ~inv~ {d['r2'][:26]:<26} "
              f"instConf={d['instance_conf']:.2f} pop={d['population_overlap']:.2f}{sig}")

    print(f"\n=== SUBSUMPTION CANDIDATES (conf >= {MIN_CONF}): {len(subs)} ===")
    for d in subs[:10]:
        print(f"  [{d['confidence']:.2f}] {d['sub'][:30]:<30} ⊑ {d['sup'][:30]:<30} "
              f"support={d['support']}/{d['support_sub']}")

    high = [d for d in inverses if d["score"] >= MIN_CONF] + subs
    # held-back validation case, checked only now
    VAL = ("emite", "adoptată de")
    rank = next((i + 1 for i, d in enumerate(inverses)
                 if {d["r1"], d["r2"]} == set(VAL)), None)

    res = {"snapshot": str(SNAP), "relations_considered": len(rels),
           "inverse_candidates": inverses, "subsumption_candidates": subs,
           "high_confidence_axioms": len(high),
           "validation_pair_rank": rank}
    (OUT / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False),
                                     encoding="utf-8")

    print("\n=== PRE-REGISTERED CHECKS ===")
    def chk(n, v, ok, t):
        print(f"  {n:<44} {v:>8}  target {t:<12} {'PASS' if ok else 'FAIL'}")
    chk("held-back pair rediscovered (rank in top 5)",
        str(rank) if rank else "ABSENT", rank is not None and rank <= 5, "<= 5")
    chk("high-confidence axioms", len(high), 3 <= len(high) <= 15, "3-15")
    print(f"\n  (plausibility of top-10 judged separately)")
    print(f"\nwrote {OUT}  ({time.time()-t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
