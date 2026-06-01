"""Lightweight hybrid ASPICE gap-detection reasoner.

Runs over the pygrap KG surface. For each in-scope process outcome, every
required characteristic is checked for satisfaction using:
  - a structural signal (best-effort: KG exposes has_characteristic), and
  - a lexical signal (mapping-hint keyword search via search_and_traverse).
An unsatisfied characteristic is a gap.
"""

from typing import Any, Dict, List, Optional

from .ontology import Ontology, ProcessOutcome

# Minimum fused score for a lexical hit to count as satisfying a characteristic.
LEXICAL_THRESHOLD = 0.2

_OUTCOME_REF_PREFIX = "kg:aspice.process_outcome."


def scoped_outcomes(ontology: Ontology, pg=None) -> List[ProcessOutcome]:
    """Outcomes referenced by the PG, or all ontology outcomes if none referenced."""
    if pg is not None:
        referenced = []
        for step in pg.steps:
            for ref in step.references:
                if ref.startswith(_OUTCOME_REF_PREFIX):
                    oc = ontology.outcome(ref[len(_OUTCOME_REF_PREFIX):])
                    if oc and oc not in referenced:
                        referenced.append(oc)
        if referenced:
            return referenced
    return list(ontology.process_outcomes)


def _structural_signals(kg, outcome_id: str, characteristic: str) -> List[Dict[str, Any]]:
    if hasattr(kg, "has_characteristic"):
        try:
            if kg.has_characteristic(outcome_id, characteristic):
                return [{"kind": "structural", "detail": f"{outcome_id}:{characteristic} present"}]
        except Exception:
            return []
    return []


def _lexical_signals(kg, ontology: Ontology, outcome_id: str, characteristic: str) -> List[Dict[str, Any]]:
    hint = ontology.hint_for(outcome_id)
    keywords = " ".join(hint.keywords) if hint else ""
    # Keep the raw characteristic token (e.g. "verification_method_specified") plus
    # its readable form and the mapping-hint keywords, so both exact and natural
    # text searches have terms to match.
    query = f"{characteristic} {characteristic.replace('_', ' ')} {keywords}".strip()
    try:
        hits = kg.search_and_traverse(
            query, max_seeds=5, required_properties=[], min_strength=0.0,
            max_depth=1, max_results=5, text_weight=0.7, graph_weight=0.3,
        )
    except Exception:
        hits = []
    signals = []
    for h in hits:
        signals.append({
            "kind": "lexical",
            "node": getattr(h, "node", None),
            "score": getattr(h, "fused_score", 0.0),
            "snippet": (getattr(h, "text", "") or "")[:160],
            "query": query,
        })
    return signals


def _best_score(signals: List[Dict[str, Any]]) -> float:
    best = 0.0
    for s in signals:
        if s["kind"] == "structural":
            return 1.0
        best = max(best, float(s.get("score", 0.0)))
    return best


def detect_gaps(kg, ontology: Ontology, pg=None) -> List[Dict[str, Any]]:
    """Return a list of gap dicts for unsatisfied required characteristics."""
    gaps: List[Dict[str, Any]] = []
    for outcome in scoped_outcomes(ontology, pg):
        for characteristic in outcome.required_characteristics:
            structural = _structural_signals(kg, outcome.id, characteristic)
            lexical = _lexical_signals(kg, ontology, outcome.id, characteristic)
            satisfaction = _best_score(structural + lexical)
            satisfied = bool(structural) or satisfaction >= LEXICAL_THRESHOLD
            if not satisfied:
                gaps.append({
                    "outcome_id": outcome.id,
                    "outcome_name": outcome.name,
                    "missing_characteristic": characteristic,
                    "signals": {"structural": structural, "lexical": lexical},
                    "confidence": round(1.0 - satisfaction, 3),
                })
    return gaps


def explain_gap(gap: Dict[str, Any], ontology_version_hash: Optional[str] = None,
                expected: str = "") -> Dict[str, Any]:
    """Produce a sectioned, reproducible explanation for a single gap."""
    outcome_id = gap["outcome_id"]
    characteristic = gap["missing_characteristic"]
    lexical = gap["signals"]["lexical"]
    queries = sorted({s["query"] for s in lexical if "query" in s})
    top_hits = [{"node": s.get("node"), "score": s.get("score"), "snippet": s.get("snippet")}
                for s in lexical]

    rendered = (
        f"Process outcome {outcome_id} ({gap.get('outcome_name', '')}) requires "
        f"'{characteristic}', but it was not found in the knowledge graph. "
        f"Expected: {expected or 'see ontology'}. "
        f"{'No supporting evidence above threshold.' if not top_hits else 'Closest matches were below the satisfaction threshold.'}"
    )

    return {
        "target": f"{outcome_id}:{characteristic}",
        "sections": {
            "structured": {
                "outcome": outcome_id,
                "characteristic": characteristic,
                "expected": expected,
            },
            "statistical": {
                "queries": queries,
                "top_hits": top_hits,
                "threshold": LEXICAL_THRESHOLD,
            },
            "rendered": rendered,
        },
        "raw_signals": {
            "ontology_version": ontology_version_hash,
            "outcome_id": outcome_id,
            "characteristic": characteristic,
            "signals": gap["signals"],
        },
    }
