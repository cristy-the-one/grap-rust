"""Dark-factory governance: the deterministic checks that let routine ontology
adoption happen without a human (Phase 1 of docs/dark-factory-proposals.md).

Pure functions over parsed proposal/ontology dicts — no workspace, no state.
The server exposes them as evaluate_proposal / diff_ontologies and wires the
verdict into auto-adoption. Humans own the *policy*; the policy owns the
routine verdicts; anything the policy cannot pass lands in the exception
queue (list_pending_interventions) instead of blocking the line.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Tuple

from .kee import validate as kee_validate

# Governed defaults. Disabled by default: switching the factory on is a human,
# audited act (set_policy). Old workspace configs lack this key entirely —
# always read through merged_policy().
DEFAULT_ADOPTION_POLICY: Dict[str, Any] = {
    "enabled": False,
    "max_outcomes_added": 5,
    "max_outcomes_removed": 0,
    "max_characteristics_removed": 0,
    "min_source_chunks": 1,
}


def merged_policy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The effective adoption policy: stored values over defaults."""
    stored = cfg.get("adoption_policy") or {}
    return {**DEFAULT_ADOPTION_POLICY, **stored}


def verify_concept_hashes(proposal: dict) -> Tuple[int, list]:
    """Check a proposal's concept_hashes against its own outcome names.

    KEE working-graph concept nodes carry the outcome's canonical name as
    their text, and the core's node content hash is sha256(text) — so each
    recorded hash must equal sha256(outcome name). A mismatch means the yaml
    was edited between extraction and adoption. Returns (checked, mismatches);
    proposals without hashes (legacy stateless path) verify trivially (0, [])."""
    chashes = proposal.get("concept_hashes") or {}
    outcomes_by_id = {o.get("id"): o for o in proposal.get("process_outcomes", [])
                      if isinstance(o, dict)}
    checked, mismatches = 0, []
    for oid, expected in chashes.items():
        oc = outcomes_by_id.get(oid)
        if oc is None:
            mismatches.append({"outcome": oid, "reason": "hash refers to no outcome in proposal"})
            continue
        checked += 1
        actual = hashlib.sha256(str(oc.get("name", "")).encode("utf-8")).hexdigest()
        if actual != expected:
            mismatches.append({"outcome": oid, "expected": expected, "actual": actual})
    return checked, mismatches


def diff_ontology_dicts(base: dict, target: dict) -> Dict[str, Any]:
    """Structural diff between two parsed ontology dicts (base -> target)."""
    b_out = {o.get("id"): o for o in (base or {}).get("process_outcomes", [])
             if isinstance(o, dict)}
    t_out = {o.get("id"): o for o in (target or {}).get("process_outcomes", [])
             if isinstance(o, dict)}
    added = sorted(set(t_out) - set(b_out))
    removed = sorted(set(b_out) - set(t_out))
    changed = []
    chars_added_total = chars_removed_total = 0
    for oid in sorted(set(b_out) & set(t_out)):
        b_chars = set(b_out[oid].get("required_characteristics") or [])
        t_chars = set(t_out[oid].get("required_characteristics") or [])
        c_added = sorted(t_chars - b_chars)
        c_removed = sorted(b_chars - t_chars)
        name_changed = str(b_out[oid].get("name", "")) != str(t_out[oid].get("name", ""))
        chars_added_total += len(c_added)
        chars_removed_total += len(c_removed)
        if c_added or c_removed or name_changed:
            changed.append({"id": oid, "name_changed": name_changed,
                            "characteristics_added": c_added,
                            "characteristics_removed": c_removed})
    # removed outcomes also remove all their characteristics
    for oid in removed:
        chars_removed_total += len(b_out[oid].get("required_characteristics") or [])
    return {
        "outcomes_added": added,
        "outcomes_removed": removed,
        "outcomes_changed": changed,
        "characteristics_added": chars_added_total,
        "characteristics_removed": chars_removed_total,
    }


def evaluate_proposal_dict(proposal: dict, active: dict | None,
                           policy: Dict[str, Any],
                           pg_referenced_ids: Iterable[str] = ()) -> Dict[str, Any]:
    """Run the adoption-policy checks. Returns {pass, checks, diff}.

    Checks (all must pass):
      integrity   — concept_hashes verify against outcome names (tamper-evidence)
      validity    — structural validation has no errors
      churn       — diff vs the active ontology stays inside budget; removing an
                    outcome the active PG references is always a violation;
                    trivially passes when no ontology is active (bootstrap)
      provenance  — every outcome is backed by >= min_source_chunks chunks
                    (source_chunk_counts is emitted by KEE's to_yaml)
    """
    checks: List[Dict[str, Any]] = []

    checked, mismatches = verify_concept_hashes(proposal)
    checks.append({"name": "integrity", "ok": not mismatches,
                   "detail": f"{checked} concept hash(es) verified"
                             if not mismatches else {"mismatches": mismatches}})

    validation = kee_validate.validate_ontology(proposal)
    checks.append({"name": "validity", "ok": validation["ok"],
                   "detail": validation["errors"] or f"{len(validation['warnings'])} warning(s)"})

    diff = None
    if active is None:
        checks.append({"name": "churn", "ok": True,
                       "detail": "no active ontology (bootstrap adoption)"})
    else:
        diff = diff_ontology_dicts(active, proposal)
        violations = []
        if len(diff["outcomes_added"]) > int(policy["max_outcomes_added"]):
            violations.append(f"{len(diff['outcomes_added'])} outcomes added "
                              f"(budget {policy['max_outcomes_added']})")
        if len(diff["outcomes_removed"]) > int(policy["max_outcomes_removed"]):
            violations.append(f"{len(diff['outcomes_removed'])} outcomes removed "
                              f"(budget {policy['max_outcomes_removed']})")
        if diff["characteristics_removed"] > int(policy["max_characteristics_removed"]):
            violations.append(f"{diff['characteristics_removed']} characteristics removed "
                              f"(budget {policy['max_characteristics_removed']})")
        referenced_removed = sorted(set(diff["outcomes_removed"]) & set(pg_referenced_ids))
        if referenced_removed:
            violations.append(f"removes outcomes referenced by the active PG: {referenced_removed}")
        checks.append({"name": "churn", "ok": not violations,
                       "detail": violations or "within budget"})

    min_chunks = int(policy["min_source_chunks"])
    if min_chunks <= 0:
        checks.append({"name": "provenance", "ok": True, "detail": "check disabled (min 0)"})
    else:
        counts = proposal.get("source_chunk_counts") or {}
        thin = []
        if not counts:
            thin.append("proposal carries no source_chunk_counts")
        else:
            for o in proposal.get("process_outcomes", []):
                oid = o.get("id") if isinstance(o, dict) else None
                if oid is not None and int(counts.get(oid, 0)) < min_chunks:
                    thin.append(f"{oid}: {counts.get(oid, 0)} chunk(s) < {min_chunks}")
        checks.append({"name": "provenance", "ok": not thin,
                       "detail": thin or f"all outcomes >= {min_chunks} source chunk(s)"})

    return {"pass": all(c["ok"] for c in checks), "checks": checks, "diff": diff}
