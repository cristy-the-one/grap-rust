"""Iteration 5b — TYPE-LEVEL axiom discovery.

Iteration 5 failed because both its scoring families rested on instance-pair
co-occurrence, which is structurally zero in a per-document extraction graph (a
document states a fact one way only). 5b scores type-level signals instead:
  * inverse      — dominant type signatures are exact reverses, weighted by the
                   support of both relations and by signature concentration
  * domain/range — a relation's dominant signature, confidence = concentration
Instance overlap is kept as a BONUS, never a gate. Deterministic, no LLM.
"""
from __future__ import annotations
import json, math, os, sys, io, time
from collections import Counter, defaultdict
from pathlib import Path
import sqlite3

P = Path(__file__).resolve().parent
ROOT = P.parent
OUT = P / "distillation" / "iter-5b"; OUT.mkdir(parents=True, exist_ok=True)
SNAP = sorted(P.glob("_backup-*"))[-1] / "kee-workdir" / "graph_store.db"
for k, v in {"GRAP_EMBED_BACKEND": "ollama", "GRAP_EMBED_MODEL": "bge-m3",
             "GRAP_EMBED_BASE_URL": "http://127.0.0.1:11434/v1"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(ROOT / "mcp-server")); sys.path.insert(0, str(P))
from distill_iter1 import classify, cosine
sys.stdout = io.TextIOWrapper(open(1, "wb", closefd=False), encoding="utf-8",
                              errors="replace", line_buffering=True)

VERB_SIM, MIN_SUP, MIN_CONC = 0.72, 8, 0.60

c = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)
con = {i: n for i, n in c.execute("SELECT concept_id, canonical FROM concepts")}
edges = list(c.execute("SELECT from_id, to_id, type FROM edges"))
vv = json.loads((P / "distillation" / "iter-1" / "verb_embeddings.json").read_text(encoding="utf-8"))
verbs = [v for v, _ in Counter(t for _, _, t in edges).most_common()]
cl = []
for v in verbs:
    vec = vv[v]; b, bs = None, -1.0
    for x in cl:
        s = cosine(vec, x["vec"])
        if s > bs: b, bs = x, s
    (b["members"].append(v) if b and bs >= VERB_SIM else cl.append({"anchor": v, "members": [v], "vec": vec}))
v2c = {m: i for i, x in enumerate(cl) for m in x["members"]}
rn = {i: x["anchor"] for i, x in enumerate(cl)}
pairs = defaultdict(set)
for f, t, ty in edges:
    i = v2c.get(ty)
    if i is not None: pairs[rn[i]].add((f, t))
lab = {i: classify(n) for i, n in con.items()}
rels = sorted([r for r, p in pairs.items() if len(p) >= MIN_SUP], key=lambda r: -len(pairs[r]))

def sig(r):
    cnt = Counter((lab[f], lab[t]) for f, t in pairs[r]
                  if lab[f] != "Unclassified" and lab[t] != "Unclassified")
    tot = sum(cnt.values())
    if not tot: return None, 0.0, 0
    (pair, n) = cnt.most_common(1)[0]
    return pair, n / tot, tot

print(f"relations: {len(rels)}   (support >= {MIN_SUP})")

# ---- domain/range axioms (type-level by nature) ----
dr = []
for r in rels:
    s, conc, tot = sig(r)
    if s and tot >= MIN_SUP and conc >= MIN_CONC:
        dr.append({"relation": r, "domain": s[0], "range": s[1],
                   "concentration": round(conc, 3), "typed_support": tot})
dr.sort(key=lambda d: -d["typed_support"])

# ---- inverse candidates, type-level ----
inv = []
for i, a in enumerate(rels):
    sa, ca, ta = sig(a)
    if not sa or ta < MIN_SUP: continue
    for b in rels[i+1:]:
        sb, cb, tb = sig(b)
        if not sb or tb < MIN_SUP: continue
        if sa != (sb[1], sb[0]): continue
        if sa[0] == sa[1]: continue          # symmetric signature: uninformative
        rev = {(y, x) for x, y in pairs[a]}
        bonus = len(rev & pairs[b]) / min(len(pairs[a]), len(pairs[b]))
        score = math.sqrt(ca * cb) * min(1.0, math.log10(min(ta, tb)) / 2.0) + bonus
        inv.append({"r1": a, "r2": b, "sig_r1": list(sa), "sig_r2": list(sb),
                    "conc_r1": round(ca, 3), "conc_r2": round(cb, 3),
                    "typed_r1": ta, "typed_r2": tb,
                    "instance_bonus": round(bonus, 3), "score": round(score, 3)})
inv.sort(key=lambda d: -d["score"])

print(f"\n=== INVERSE CANDIDATES (type-level): {len(inv)} ===")
for d in inv[:8]:
    print(f"  [{d['score']:.2f}] {d['r1'][:24]:<24} ~inv~ {d['r2'][:24]:<24} "
          f"{d['sig_r1']}<->{d['sig_r2']} conc={d['conc_r1']:.2f}/{d['conc_r2']:.2f} "
          f"sup={d['typed_r1']}/{d['typed_r2']}")
print(f"\n=== DOMAIN/RANGE AXIOMS (conc >= {MIN_CONC}): {len(dr)} ===")
for d in dr[:12]:
    print(f"  [{d['typed_support']:>4} conc={d['concentration']:.2f}]  "
          f"{d['domain']} --{d['relation'][:26]}--> {d['range']}")

VAL = {"emite", "adoptată de"}
rank = next((i+1 for i, d in enumerate(inv) if {d["r1"], d["r2"]} == VAL), None)
json.dump({"inverse": inv, "domain_range": dr, "validation_rank": rank},
          open(OUT/"result.json","w",encoding="utf-8"), indent=2, ensure_ascii=False)
print("\n=== PRE-REGISTERED CHECKS ===")
def chk(n,v,ok,t): print(f"  {n:<44} {v:>8}  target {t:<12} {'PASS' if ok else 'FAIL'}")
chk("held-back pair rank (weak evidence now)", str(rank) if rank else "ABSENT",
    rank is not None and rank <= 3, "<= 3")
chk("domain/range axioms", len(dr), 3 <= len(dr) <= 40, "3-40")
print(f"\nwrote {OUT}")
