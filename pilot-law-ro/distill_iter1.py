"""Distillation experiment, Iteration 1 — does this corpus have a clean grammar?

Pre-registered in docs/experiments/distillation-journal.md. Read-only against a
FROZEN snapshot (never the live working graph). Deterministic analytics propose
structure; the LLM only names clusters. All intermediates checkpointed to
distillation/iter-1/ so the run is resumable and re-inspectable.

Usage: python distill_iter1.py [snapshot_db]
"""
from __future__ import annotations
import json, math, os, sys, io, time, hashlib
from collections import Counter, defaultdict
from pathlib import Path
import sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
OUT = PILOT / "distillation" / "iter-1"
OUT.mkdir(parents=True, exist_ok=True)

SNAP = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    sorted(PILOT.glob("_backup-*"))[-1] / "kee-workdir" / "graph_store.db")

os.environ.setdefault("GRAP_EMBED_BACKEND", "ollama")
os.environ.setdefault("GRAP_EMBED_MODEL", "bge-m3")
os.environ.setdefault("GRAP_EMBED_BASE_URL", "http://127.0.0.1:11434/v1")
sys.path.insert(0, str(ROOT / "mcp-server"))
from grap_mcp.kee.reconcile import fold                     # noqa: E402
from grap_mcp.kee.embeddings import get_embedder            # noqa: E402

VERB_SIM = 0.72          # cosine threshold for verb clustering (recorded)
TOP_HUBS = 250           # concepts to coarse-type
SEED = 0

# ---- class rules: Romanian legal morphology (diacritic-folded matching) -----
CLASS_RULES = [
    ("NormativeAct", ("ordonanta", "ordonanța", "lege", "legea", "legii", "hotarare",
                      "hotărâre", "decizie", "norme", "norma", "regulament",
                      "ordin", "decret", "instructiuni", "instrucţiuni", "protocol",
                      "acord", "convenţie", "conventie", "statut")),
    ("AuthoritativeBody", ("guvern", "minister", "ministerul", "banca nationala",
                           "casa ", "casei", "curtea", "consiliu", "comisia",
                           "comitet", "agentia", "agenţia", "institut", "parlament",
                           "senat", "camera", "oficiul", "autoritatea", "directia",
                           "inspectoratul", "colegiul", "regia")),
    ("Official", ("prim-ministru", "ministru", "preşedinte", "presedinte", "secretar",
                  "guvernator", "director", "sef ", "şef ")),
    ("OfficialGazette", ("monitorul oficial",)),
    ("StructuralPart", ("articolul", "art.", "anexa", "anexă", "alineat", "capitol",
                        "litera", "punctul", "paragraf")),
    ("LegalChallenge", ("excepţia de neconstituţionalitate", "exceptia de neconstitutionalitate",
                        "recurs", "contestatie", "contestaţie", "plangere", "plângere")),
    ("Budget", ("buget", "bugetul", "fond", "fondul", "cota", "credite", "cheltuieli",
                "venituri", "finantare", "finanţare", "taxa", "impozit")),
]


def classify(name: str) -> str:
    f = fold(name).lower()
    for cls, pats in CLASS_RULES:
        for p in pats:
            if fold(p).lower() in f:
                return cls
    return "Unclassified"


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))     # embedder L2-normalizes


def main():
    t_start = time.time()
    print(f"snapshot: {SNAP}")
    if not SNAP.exists():
        print("  SNAPSHOT MISSING"); return 2
    snap_hash = hashlib.sha256(SNAP.read_bytes()[:1_000_000]).hexdigest()[:16]
    c = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)

    # ---------- load ----------
    concepts = {cid: nm for cid, nm in c.execute("SELECT concept_id, canonical FROM concepts")}
    edges = list(c.execute("SELECT from_id, to_id, type FROM edges"))
    print(f"  concepts={len(concepts)} edges={len(edges)} distinct_verbs="
          f"{len({t for _,_,t in edges})}")

    # ---------- Stage 1: verb clustering ----------
    verb_freq = Counter(t for _, _, t in edges)
    verbs = [v for v, _ in verb_freq.most_common()]      # frequent first = cluster anchors
    cache = OUT / "verb_embeddings.json"
    if cache.exists():
        vecs = {k: v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
        print(f"  reusing cached embeddings ({len(vecs)})")
    else:
        emb = get_embedder()
        t0 = time.time()
        # fold + de-underscore so morphological variants start closer
        texts = [fold(v).replace("_", " ") for v in verbs]
        allv = []
        B = 128
        for i in range(0, len(texts), B):
            allv.extend(emb.embed(texts[i:i + B]))
        vecs = {v: allv[i] for i, v in enumerate(verbs)}
        cache.write_text(json.dumps(vecs), encoding="utf-8")
        print(f"  embedded {len(verbs)} verbs in {time.time()-t0:.1f}s (cached)")

    clusters = []            # [{"anchor":verb, "members":[...], "vec":[...]}]
    for v in verbs:
        vec = vecs[v]
        best, bestsim = None, -1.0
        for cl in clusters:
            sim = cosine(vec, cl["vec"])
            if sim > bestsim:
                best, bestsim = cl, sim
        if best is not None and bestsim >= VERB_SIM:
            best["members"].append(v)
        else:
            clusters.append({"anchor": v, "members": [v], "vec": vec})
    clusters.sort(key=lambda cl: -sum(verb_freq[m] for m in cl["members"]))
    for cl in clusters:
        cl["support"] = sum(verb_freq[m] for m in cl["members"])
    verb2cluster = {m: i for i, cl in enumerate(clusters) for m in cl["members"]}

    total_edges = len(edges)
    cum, n_for_80 = 0, 0
    for i, cl in enumerate(clusters, 1):
        cum += cl["support"]
        if cum >= 0.8 * total_edges:
            n_for_80 = i
            break
    print(f"\nSTAGE 1: {len(verbs)} verbs -> {len(clusters)} clusters; "
          f"{n_for_80} clusters cover 80% of edges")
    for cl in clusters[:8]:
        print(f"   [{cl['support']:>4}] {cl['anchor'][:34]:<34} + {len(cl['members'])-1} variants: "
              f"{', '.join(m[:22] for m in cl['members'][1:5])}")

    # ---------- Stage 2: coarse class typing ----------
    deg = Counter()
    for f, t, _ in edges:
        deg[f] += 1; deg[t] += 1
    hubs = [cid for cid, _ in deg.most_common(TOP_HUBS)]
    cls_of = {cid: classify(concepts.get(cid, "")) for cid in concepts}
    hub_classes = Counter(cls_of[h] for h in hubs)
    print(f"\nSTAGE 2: classes over top-{TOP_HUBS} hubs: {dict(hub_classes)}")
    unclassified_hubs = [concepts[h] for h in hubs if cls_of[h] == "Unclassified"][:6]
    print(f"   sample Unclassified hubs: {[u[:34] for u in unclassified_hubs]}")

    # ---------- Stage 3: lift + support/purity ----------
    lifted = Counter()
    per_rel = defaultdict(Counter)
    for f, t, verb in edges:
        ci = verb2cluster.get(verb)
        if ci is None:
            continue
        rel = clusters[ci]["anchor"]
        a, b = cls_of.get(f, "Unclassified"), cls_of.get(t, "Unclassified")
        lifted[(a, rel, b)] += 1
        per_rel[rel][(a, b)] += 1
    purity = {}
    for rel, pairs in per_rel.items():
        tot = sum(pairs.values())
        purity[rel] = max(pairs.values()) / tot if tot else 0.0

    typed = [(k, v) for k, v in lifted.items()
             if k[0] != "Unclassified" and k[2] != "Unclassified"]
    typed.sort(key=lambda kv: -kv[1])
    print(f"\nSTAGE 3: top abstract triples (both endpoints typed):")
    for (a, rel, b), sup in typed[:12]:
        print(f"   [{sup:>4}  purity {purity[rel]:.2f}]  {a} --{rel[:26]}--> {b}")

    covered = sum(v for k, v in lifted.items()
                  if k[0] != "Unclassified" and k[2] != "Unclassified")
    top_rels = [cl["anchor"] for cl in clusters[:10]]
    mean_purity = sum(purity[r] for r in top_rels if r in purity) / max(
        1, len([r for r in top_rels if r in purity]))

    # ---------- pre-registered checks ----------
    gov = [cid for cid, nm in concepts.items()
           if fold(nm).lower().strip() in ("guvernul", "guvern", "guvernul romaniei")]
    gov_classes = {cls_of[g] for g in gov}
    part_verbs = [v for v in verbs
                  if "parte integranta" in fold(v).lower() or fold(v).lower() == "is part of"]
    part_clusters = {verb2cluster[v] for v in part_verbs if v in verb2cluster}

    checks = {
        "snapshot": str(SNAP), "snapshot_fingerprint": snap_hash,
        "params": {"verb_sim": VERB_SIM, "top_hubs": TOP_HUBS, "seed": SEED},
        "verbs": len(verbs), "clusters": len(clusters),
        "clusters_for_80pct_edges": n_for_80,
        "edge_coverage_typed": round(covered / total_edges, 3),
        "mean_purity_top10": round(mean_purity, 3),
        "gov_variants_found": [concepts[g] for g in gov],
        "gov_single_class": len(gov_classes) == 1, "gov_classes": sorted(gov_classes),
        "part_verbs_found": part_verbs,
        "part_single_cluster": len(part_clusters) == 1,
        "top_triples": [{"from": a, "rel": r, "to": b, "support": s,
                         "purity": round(purity[r], 3)} for (a, r, b), s in typed[:10]],
    }
    (OUT / "result.json").write_text(json.dumps(checks, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    (OUT / "clusters.json").write_text(json.dumps(
        [{"anchor": cl["anchor"], "support": cl["support"], "members": cl["members"]}
         for cl in clusters], indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== PRE-REGISTERED CHECKS ===")
    print(f"  verbs {len(verbs)} -> {len(clusters)} clusters   (threshold: <=25 for 80% edges)")
    print(f"  clusters covering 80% of edges: {n_for_80}   {'PASS' if n_for_80 <= 25 else 'FAIL'}")
    print(f"  typed edge coverage: {checks['edge_coverage_typed']:.1%}")
    print(f"  mean purity (top-10 rels): {mean_purity:.2f}   {'PASS' if mean_purity >= 0.80 else 'FAIL'}")
    print(f"  GUVERNUL variants {checks['gov_variants_found']} -> one class? "
          f"{checks['gov_single_class']} ({checks['gov_classes']})")
    print(f"  'part of' variants {part_verbs} -> one cluster? {checks['part_single_cluster']}")
    print(f"\nwrote {OUT}  ({time.time()-t_start:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
