"""Lightweight hybrid ASPICE gap-detection reasoner.

Runs over the pygrap KG surface. For each in-scope process outcome, every
required characteristic is checked for satisfaction using:
  - a structural signal (best-effort: KG exposes has_characteristic), and
  - a lexical signal (mapping-hint keyword search via search_and_traverse).
An unsatisfied characteristic is a gap.
"""

import re
from typing import Any, Dict, List, Optional

from .ontology import Ontology, ProcessOutcome

# Session integration for same-session searchability (high-leverage for
# in-session knowledge added during a run).
try:
    from . import session as _session
except Exception:
    _session = None

# Default reasoner weights. `lexical_threshold` is the satisfaction cutoff,
# interpreted as the minimum fraction of a characteristic's distinguishing
# tokens that must appear in real evidence (0..1). text_weight/graph_weight
# feed the search fusion used to retrieve candidate evidence.
#
# Why coverage and not a raw BM25 cutoff: pygrap returns unbounded BM25 scores
# (typically 2-9 for any token overlap), so a fixed magnitude cutoff marks every
# characteristic "satisfied" as soon as any document is ingested -- and a strong
# match on a shared word (e.g. "definition") wrongly satisfies a different
# characteristic (process_definition vs interface_definition). Requiring that
# the evidence actually contains the characteristic's own tokens is what makes
# gap detection discriminating.
DEFAULT_WEIGHTS = {"text_weight": 0.7, "graph_weight": 0.3, "lexical_threshold": 0.6}
LEXICAL_THRESHOLD = DEFAULT_WEIGHTS["lexical_threshold"]

# Generic connective words that carry no discriminating meaning; excluded from
# token-coverage scoring.
_STOPWORDS = {"a", "an", "the", "of", "for", "and", "or", "to", "in", "is",
              "are", "be", "with", "on", "by"}


def _content_tokens(characteristic: str) -> List[str]:
    """Distinguishing tokens of a characteristic id (snake_case -> words),
    diacritic-folded so both sides of the coverage match normalize alike."""
    from .kee.reconcile import fold
    return [t for t in fold(characteristic).replace("_", " ").split()
            if t not in _STOPWORDS]


def _covers(text: str, characteristic: str) -> float:
    """Fraction (0..1) of a characteristic's distinguishing tokens present in `text`.

    Light 4-char prefix-stemming lets 'verify' satisfy 'verification' and
    'requirement' satisfy 'requirements' without matching unrelated words.
    Short tokens (<4 chars) require an exact word match.
    """
    tokens = _content_tokens(characteristic)
    if not tokens:
        return 0.0
    from .kee.reconcile import fold
    words = re.findall(r"[a-z]+", fold(text))
    present = 0
    for tok in tokens:
        if len(tok) < 4:
            ok = tok in words
        else:
            stem = tok[:4]
            ok = any(w.startswith(stem) for w in words)
        if ok:
            present += 1
    return present / len(tokens)

_OUTCOME_REF_PREFIX = "kg:aspice.process_outcome."

# Text prefixes of system-projected nodes. These are scaffolding/procedure nodes,
# NOT project knowledge, so they must never be counted as evidence that a
# characteristic is satisfied (otherwise the ontology or the procedure graph
# trivially "satisfies" the very characteristics we are checking).
_PROJECTED_PREFIXES = ("[ASPICE outcome]", "[characteristic]", "[PG Step]")

# Label prefixes of system-projected nodes (timing-robust secondary guard).
_PROJECTED_LABEL_PREFIXES = ("kg:aspice.", "pg-step-")


def _is_projected(kg, node, text: str, exclude_nodes) -> bool:
    """True if a hit is a system-projected node (ontology scaffolding or PG step),
    so it must not count as evidence.

    Authoritative check is the explicit node-id set captured at projection time
    (timing- and thread-independent). Label and text prefixes are secondary guards.
    """
    if exclude_nodes and node in exclude_nodes:
        return True
    if text.startswith(_PROJECTED_PREFIXES):
        return True
    if node is not None and hasattr(kg, "get_node_label"):
        try:
            label = kg.get_node_label(node)
            if label and label.startswith(_PROJECTED_LABEL_PREFIXES):
                return True
        except Exception:
            return False
    return False


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


def _lexical_signals(kg, ontology: Ontology, outcome_id: str, characteristic: str,
                     weights: Dict[str, float], exclude_nodes=None) -> List[Dict[str, Any]]:
    hint = ontology.hint_for(outcome_id)
    keywords = " ".join(hint.keywords) if hint else ""
    # Keep the raw characteristic token (e.g. "verification_method_specified") plus
    # its readable form and the mapping-hint keywords, so both exact and natural
    # text searches have terms to match.
    query = f"{characteristic} {characteristic.replace('_', ' ')} {keywords}".strip()
    try:
        # Deliberately the classic (deterministic) fusion path. The RRF-hybrid
        # variant produced per-process tie-break ordering (hash-seed dependent),
        # which is unacceptable here: gap explanations are persisted, versioned
        # records and must be reproducible. Revisit only with deterministic
        # tie-breaking in the core.
        hits = kg.search_and_traverse(
            query, max_seeds=5, required_properties=[], min_strength=0.0,
            max_depth=1, max_results=5,
            text_weight=weights.get("text_weight", 0.7), graph_weight=weights.get("graph_weight", 0.3),
        )
    except Exception:
        hits = []

    # Journal the query (zero hits = negative evidence) and touched nodes.
    # Observability only — session state must never influence scores here:
    # gap explanations are persisted, versioned records and have to be
    # reproducible regardless of what else happened in this process.
    if _session:
        _session.record_query(query, len(hits))

    signals = []
    for h in hits:
        text = getattr(h, "text", "") or ""
        node = getattr(h, "node", None)
        if _is_projected(kg, node, text, exclude_nodes):
            continue  # ignore system-projected nodes; only real evidence counts
        if _session and node is not None:
            _session.record_node_access(node, "reasoner_lexical")
        signals.append({
            "kind": "lexical",
            "node": node,
            "score": getattr(h, "fused_score", 0.0),
            "coverage": round(_covers(text, characteristic), 3),
            "snippet": text[:160],
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


def detect_gaps(kg, ontology: Ontology, pg=None, weights=None,
                exclude_nodes=None) -> List[Dict[str, Any]]:
    """Return gap dicts for unsatisfied required characteristics, ranked by confidence.

    `exclude_nodes` is an optional set of system-projected node ids (ontology
    scaffolding + PG steps) to never count as evidence, independent of any
    label/text read-back or index timing.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    gaps: List[Dict[str, Any]] = []
    for outcome in scoped_outcomes(ontology, pg):
        for characteristic in outcome.required_characteristics:
            structural = _structural_signals(kg, outcome.id, characteristic)
            lexical = _lexical_signals(kg, ontology, outcome.id, characteristic, w, exclude_nodes)
            # Satisfaction is token coverage (0..1), not raw BM25 magnitude.
            coverage = 1.0 if structural else max(
                (s.get("coverage", 0.0) for s in lexical), default=0.0)
            satisfied = bool(structural) or coverage >= w["lexical_threshold"]
            if not satisfied:
                gaps.append({
                    "outcome_id": outcome.id,
                    "outcome_name": outcome.name,
                    "missing_characteristic": characteristic,
                    "signals": {"structural": structural, "lexical": lexical},
                    "confidence": round(1.0 - coverage, 3),
                })
    gaps.sort(key=lambda g: g["confidence"], reverse=True)
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
