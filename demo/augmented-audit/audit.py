"""Interaction surface: run an authored PG/ontology against the KG and return
a per-requirement compliance report with provenance.

This is the deterministic core the agent tool calls. It wraps the reasoner so
that every verdict — satisfied OR gap — carries the evidence that produced it:
the queries run, the best matching corpus snippet, its score and token
coverage. That provenance is exactly what a bare LLM cannot produce, and what
makes a "gap" a provable claim rather than a guess.

    from audit import load_audit, run_audit
    kg, on, pg = load_audit("pilot-law-ro/kg", "requirement-ontology.yaml", "audit.pg.yaml")
    report = run_audit(kg, on, pg)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "mcp-server"))

import pygrap                                             # noqa: E402
from grap_mcp import ontology as omod                     # noqa: E402
from grap_mcp import procedure_graph as pgmod             # noqa: E402
from grap_mcp import reasoner                             # noqa: E402


def load_audit(kg_path: str, onto_path: str, pg_path: str):
    kg = pygrap.IndexedKnowledgeGraph(kg_path)
    on = omod.load_ontology_from_file(onto_path)
    pg = pgmod.load_from_file(pg_path)
    return kg, on, pg


def run_audit(kg, ontology, pg, weights=None) -> Dict[str, Any]:
    """Return {requirements: [...], summary: {...}}. Each requirement lists its
    characteristics with satisfied/gap + the best supporting evidence."""
    w = {**reasoner.DEFAULT_WEIGHTS, **(weights or {})}
    threshold = w["lexical_threshold"]
    requirements: List[Dict[str, Any]] = []
    n_sat = n_gap = 0

    for oc in reasoner.scoped_outcomes(ontology, pg):
        chars = []
        for ch in oc.required_characteristics:
            structural = reasoner._structural_signals(kg, oc.id, ch)
            lexical = reasoner._lexical_signals(kg, ontology, oc.id, ch, w, None)
            best = max(lexical, key=lambda s: s.get("coverage", 0.0), default=None)
            coverage = 1.0 if structural else (best.get("coverage", 0.0) if best else 0.0)
            satisfied = bool(structural) or coverage >= threshold
            n_sat += satisfied
            n_gap += (not satisfied)
            chars.append({
                "characteristic": ch,
                "satisfied": satisfied,
                "coverage": round(coverage, 3),
                "evidence_snippet": (best.get("snippet") if best else "") or "",
                "evidence_score": round(best.get("score", 0.0), 3) if best else 0.0,
                "queries": sorted({s.get("query", "") for s in lexical if s.get("query")}),
            })
        requirements.append({
            "outcome_id": oc.id,
            "name": oc.name,
            "description": oc.description,
            "satisfied": all(c["satisfied"] for c in chars),
            "characteristics": chars,
        })

    return {
        "requirements": requirements,
        "summary": {
            "requirements_total": len(requirements),
            "requirements_satisfied": sum(r["satisfied"] for r in requirements),
            "characteristics_total": n_sat + n_gap,
            "characteristics_satisfied": n_sat,
            "characteristics_gap": n_gap,
            "threshold": threshold,
        },
    }


def format_report(report: Dict[str, Any]) -> str:
    out = []
    s = report["summary"]
    out.append(f"Compliance audit — {s['requirements_satisfied']}/{s['requirements_total']} "
               f"requirements satisfied, {s['characteristics_gap']} gap(s) "
               f"(coverage threshold {s['threshold']})")
    for r in report["requirements"]:
        mark = "PASS" if r["satisfied"] else "GAP "
        out.append(f"\n[{mark}] {r['name']}  ({r['outcome_id']})")
        for c in r["characteristics"]:
            cm = "ok " if c["satisfied"] else "MISS"
            out.append(f"   {cm} {c['characteristic']:<24} cov={c['coverage']:.2f}")
            if c["evidence_snippet"]:
                out.append(f"        evidence: \"{c['evidence_snippet'][:90]}\"")
            elif not c["satisfied"]:
                out.append(f"        no evidence above threshold; queries={c['queries']}")
    return "\n".join(out)


if __name__ == "__main__":
    import os
    # Romanian evidence contains characters (ţ, ş, ă) the Windows console's
    # cp1252 codepage cannot encode — force UTF-8 so the report always prints.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.environ.setdefault("GRAP_EMBED_BACKEND", "ollama")
    os.environ.setdefault("GRAP_EMBED_MODEL", "bge-m3")
    os.environ.setdefault("GRAP_EMBED_BASE_URL", "http://localhost:11434/v1")
    kg_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "pilot-law-ro" / "kg")
    kg, on, pg = load_audit(kg_path, str(HERE / "requirement-ontology.yaml"),
                            str(HERE / "audit.pg.yaml"))
    print(format_report(run_audit(kg, on, pg)))
