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
import math
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
    # Scale-free anomaly budget: outcomes added per NEW supporting chunk. The
    # absolute budgets above answer "is this change too big to adopt without a
    # human?" — a blast-radius question, and one that inherently compounds: a
    # rejected proposal never becomes the baseline, so the next proposal's diff
    # includes the rejected delta and the gate stays shut (observed: 11
    # consecutive rejections at 600 docs, 3 more at 650). This budget answers a
    # different question — "is this proposal anomalous *relative to the evidence
    # that justifies it*?" — which is scale-free and therefore cascade-proof:
    # when batches accumulate, additions and supporting chunks grow together and
    # the rate is unchanged. 0 disables it (default, so existing configs are
    # unaffected).
    "max_outcomes_added_per_chunk": 0.0,
    # Canary (Phase 3): after auto-adoption, run gap detection as a
    # quarantined experiment and roll back if it introduces more than the
    # budgeted new gaps vs the production baseline.
    "canary_enabled": False,
    "canary_max_new_gaps": 0,
    # Phase 4: after a kept adoption, LLM-draft a PG patch covering the new
    # ontology (machine-checked, published at level=draft, human promotes).
    "pg_draft_enabled": False,
    # Phase 5: trivial (purely additive) machine-checked drafts self-promote
    # through the validation-gated promote path; anything else stays queued.
    "pg_autopromote_enabled": False,
    # Review fix 4: sync auto-promoted drafts back to the PG source file so
    # the working copy tracks the promoted head (opt-in — writes user files).
    "pg_write_back": False,
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

    # Rename detection: a removed and an added outcome with the *identical*,
    # non-empty characteristic set — and unique on both sides — is the same
    # concept under a new id/name, not churn. Ambiguous fingerprints (several
    # candidates sharing a set) are conservatively NOT paired.
    def _fp(o):
        return tuple(sorted(o.get("required_characteristics") or []))

    removed_by_fp: Dict[tuple, List[str]] = {}
    for oid in removed:
        fp = _fp(b_out[oid])
        if fp:
            removed_by_fp.setdefault(fp, []).append(oid)
    added_by_fp: Dict[tuple, List[str]] = {}
    for oid in added:
        fp = _fp(t_out[oid])
        if fp:
            added_by_fp.setdefault(fp, []).append(oid)
    renamed = [[olds[0], news[0]]
               for fp, olds in sorted(removed_by_fp.items())
               for news in [added_by_fp.get(fp, [])]
               if len(olds) == 1 and len(news) == 1]

    return {
        "outcomes_added": added,
        "outcomes_removed": removed,
        "outcomes_changed": changed,
        "outcomes_renamed": renamed,
        "characteristics_added": chars_added_total,
        "characteristics_removed": chars_removed_total,
    }


def _supporting_chunks(proposal: dict, outcome_ids: Iterable[str]) -> int:
    """How much evidence backs `outcome_ids` in `proposal`.

    Prefers `provenance` (outcome id -> [chunk ids]) and counts DISTINCT chunks,
    since several outcomes usually come from the same chunk and double-counting
    would understate the rate. Falls back to summing `source_chunk_counts`, then
    to 0 (which disables the rate check rather than guessing)."""
    ids = list(outcome_ids)
    if not ids:
        return 0
    prov = proposal.get("provenance")
    if isinstance(prov, dict):
        chunks = set()
        for oid in ids:
            v = prov.get(oid)
            if isinstance(v, (list, tuple, set)):
                chunks.update(str(x) for x in v)
            elif v:
                chunks.add(str(v))
        if chunks:
            return len(chunks)
    counts = proposal.get("source_chunk_counts")
    if isinstance(counts, dict):
        total = 0
        for oid in ids:
            try:
                total += int(counts.get(oid, 0) or 0)
            except (TypeError, ValueError):
                pass
        if total:
            return total
    return 0


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
        # Renames (same concept, new id) are not churn: budget the *effective*
        # adds/removals with rename pairs subtracted. A renamed outcome that
        # the active PG references still violates — the PG reference would
        # dangle until a PG patch lands.
        renamed = diff.get("outcomes_renamed") or []
        ren_old = {old for old, _ in renamed}
        ren_new = {new for _, new in renamed}
        base_out = {o.get("id"): o for o in (active or {}).get("process_outcomes", [])
                    if isinstance(o, dict)}
        eff_added = [o for o in diff["outcomes_added"] if o not in ren_new]
        eff_removed = [o for o in diff["outcomes_removed"] if o not in ren_old]
        eff_chars_removed = diff["characteristics_removed"] - sum(
            len(base_out.get(o, {}).get("required_characteristics") or [])
            for o in ren_old)
        violations = []
        # --- scale-free anomaly check: additions per supporting chunk ---------
        # Reported separately from the absolute budgets so an *anomalous* proposal
        # (many outcomes from little evidence) is distinguishable from a merely
        # *large* one (proportionate growth that needs a human to re-baseline).
        rate_budget = float(policy.get("max_outcomes_added_per_chunk") or 0.0)
        added_chunks = _supporting_chunks(proposal, eff_added)
        rate = (len(eff_added) / added_chunks) if added_chunks else None
        if rate_budget > 0 and rate is not None and rate > rate_budget:
            violations.append(
                f"anomalous: {len(eff_added)} outcomes added from only "
                f"{added_chunks} supporting chunk(s) = {rate:.1f}/chunk "
                f"(budget {rate_budget}/chunk)")

        if len(eff_added) > int(policy["max_outcomes_added"]):
            extra = ""
            if rate_budget > 0 and rate is not None and rate <= rate_budget:
                # Proportionate to its evidence — this is a blast-radius stop, not
                # a quality failure. Say so, and say what clears it, because the
                # condition does not clear itself.
                extra = (f"; proportionate to evidence ({rate:.1f}/chunk, within "
                         f"{rate_budget}/chunk) — this is a blast-radius stop, not "
                         f"an anomaly. It will NOT clear on its own: adopt to "
                         f"re-baseline, or raise the budget with rationale")
            violations.append(f"{len(eff_added)} outcomes added "
                              f"(budget {policy['max_outcomes_added']}){extra}")
        if len(eff_removed) > int(policy["max_outcomes_removed"]):
            violations.append(f"{len(eff_removed)} outcomes removed "
                              f"(budget {policy['max_outcomes_removed']})")
        if eff_chars_removed > int(policy["max_characteristics_removed"]):
            violations.append(f"{eff_chars_removed} characteristics removed "
                              f"(budget {policy['max_characteristics_removed']})")
        referenced_removed = sorted(set(diff["outcomes_removed"]) & set(pg_referenced_ids))
        if referenced_removed:
            violations.append(f"removes outcomes referenced by the active PG: {referenced_removed}")
        detail = violations or "within budget"
        if not violations and renamed:
            detail = f"within budget (renames not counted: {renamed})"
        checks.append({"name": "churn", "ok": not violations, "detail": detail})

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


def pg_draft_is_trivial(pg_diff: dict) -> bool:
    """A PG draft is 'trivial' — safe to auto-promote through the
    validation-gated promote path — when it is purely additive: at least one
    step added, nothing removed, nothing changed, and at most a version bump
    in the metadata. Anything touching existing process logic stays a human
    decision."""
    if pg_diff.get("steps_removed") or pg_diff.get("steps_changed"):
        return False
    meta = set((pg_diff.get("metadata_changed") or {}).keys())
    return bool(pg_diff.get("steps_added")) and meta <= {"version"}


# ---- n-version extraction (Phase 3, P-5) -----------------------------------

def _norm_name(name) -> str:
    return " ".join(str(name or "").lower().split())


def _cos(u, v) -> float:
    num = sum(x * y for x, y in zip(u, v))
    du = math.sqrt(sum(x * x for x in u))
    dv = math.sqrt(sum(y * y for y in v))
    return num / (du * dv) if du and dv else 0.0


def _chunk_counts(proposal: dict) -> Dict[str, int]:
    counts = proposal.get("source_chunk_counts")
    if counts:
        return {k: int(v) for k, v in counts.items()}
    return {k: len(v) for k, v in (proposal.get("provenance") or {}).items()}


def agreement_merge(proposal_a: dict, proposal_b: dict, embedder=None,
                    sim: float = 0.86) -> Tuple[dict, dict]:
    """N-version merge: outcomes both models proposed (matched by normalized
    name, then optionally by embedding similarity >= sim) form the
    auto-adoptable agreement proposal; everything else is the disagreement
    residue for human review. Per-outcome characteristics are intersected —
    disputed ones are reported, never silently dropped. concept_hashes are
    recomputed as sha256(name) (that is their definition) and
    source_chunk_counts take the conservative minimum of the two runs."""
    a_out = [o for o in (proposal_a or {}).get("process_outcomes", [])
             if isinstance(o, dict)]
    b_out = [o for o in (proposal_b or {}).get("process_outcomes", [])
             if isinstance(o, dict)]
    b_by_norm = {_norm_name(o.get("name")): o for o in b_out}
    matched: List[Tuple[dict, dict]] = []
    unmatched_a: List[dict] = []
    used_b: set = set()
    for o in a_out:
        key = _norm_name(o.get("name"))
        if key in b_by_norm and key not in used_b:
            matched.append((o, b_by_norm[key]))
            used_b.add(key)
        else:
            unmatched_a.append(o)
    if embedder is not None and unmatched_a:
        remaining = [(k, o) for k, o in b_by_norm.items() if k not in used_b]
        if remaining:
            try:
                va = embedder.embed([o.get("name", "") for o in unmatched_a])
                vb = embedder.embed([o.get("name", "") for _, o in remaining])
                still: List[dict] = []
                for i, o in enumerate(unmatched_a):
                    best_j, best_s = -1, 0.0
                    for j in range(len(remaining)):
                        if remaining[j][0] in used_b:
                            continue
                        s = _cos(va[i], vb[j])
                        if s > best_s:
                            best_j, best_s = j, s
                    if best_j >= 0 and best_s >= sim:
                        matched.append((o, remaining[best_j][1]))
                        used_b.add(remaining[best_j][0])
                    else:
                        still.append(o)
                unmatched_a = still
            except Exception:
                pass
    unmatched_b = [o for k, o in b_by_norm.items() if k not in used_b]

    counts_a, counts_b = _chunk_counts(proposal_a), _chunk_counts(proposal_b)
    merged_outcomes: List[dict] = []
    hashes: Dict[str, str] = {}
    counts: Dict[str, int] = {}
    disputed_chars: List[dict] = []
    for oa, ob in matched:
        ca = set(oa.get("required_characteristics") or [])
        cb = set(ob.get("required_characteristics") or [])
        if ca ^ cb:
            disputed_chars.append({"outcome": oa.get("id"),
                                   "characteristics": sorted(ca ^ cb)})
        oid = oa.get("id")
        merged_outcomes.append({
            "id": oid,
            "name": oa.get("name"),
            "description": oa.get("description") or str(oa.get("name")),
            "required_characteristics": sorted(ca & cb),
        })
        hashes[oid] = hashlib.sha256(str(oa.get("name", "")).encode("utf-8")).hexdigest()
        counts[oid] = min(counts_a.get(oid, 0), counts_b.get(ob.get("id"), 0))

    merged_ids = {o["id"] for o in merged_outcomes}
    merged = {
        "version": proposal_a.get("version", "kee-candidate"),
        "process_outcomes": merged_outcomes,
        "mapping_hints": [h for h in (proposal_a.get("mapping_hints") or [])
                          if isinstance(h, dict) and h.get("outcome") in merged_ids],
        "concept_hashes": hashes,
        "source_chunk_counts": counts,
    }
    disagreement = {
        "agreed_outcomes": len(merged_outcomes),
        "outcomes_only_in_a": [o.get("id") for o in unmatched_a],
        "outcomes_only_in_b": [o.get("id") for o in unmatched_b],
        "disputed_characteristics": disputed_chars,
    }
    return merged, disagreement
