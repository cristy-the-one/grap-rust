"""Phase 4 (P-10): LLM-drafted Procedure Graph patches.

The LLM only *drafts*; every draft passes deterministic machine checks
(coverage rules below + publish validation) before being published at
level=draft for a human's one-click promotion. Pure helpers here — the
orchestration lives in server.draft_pg_patch.
"""
from __future__ import annotations

import re
from typing import List, Set

REF_MARKER = "process_outcome."

SYSTEM_PROMPT = (
    "You maintain Procedure Graph (PG) YAML files for a process-governance "
    "system. You will receive the current PG YAML, a list of ontology process "
    "outcomes the PG must newly cover, and a list of outcome ids whose "
    "references must be removed. Return ONLY the complete updated PG YAML - "
    "no prose, no code fences. Rules: keep the PG name; bump the patch "
    "version; keep every existing step that is still valid; add one step per "
    "newly covered outcome with a reference of the form "
    "'kg:aspice.process_outcome.<outcome_id>'; remove steps or references "
    "pointing at the removed outcome ids; every step needs an id and an "
    "operation."
)


def build_user_prompt(pg_yaml: str, uncovered: List[dict], orphaned: List[str]) -> str:
    lines = ["Current PG YAML:", "---", pg_yaml.rstrip(), "---", ""]
    if uncovered:
        lines.append("Outcomes the PG must newly cover (id: characteristics):")
        for o in uncovered:
            chars = ", ".join(o.get("required_characteristics") or []) or "(none)"
            lines.append(f"- {o.get('id')}: {chars}")
        # Small local models follow examples far better than prose rules:
        # without this template they tend to drop the references field.
        lines.append("")
        lines.append("Add EXACTLY ONE step per outcome above, in this form "
                     "(the references line is mandatory):")
        first = uncovered[0].get("id")
        lines.append(f"  - id: cover_{first}")
        lines.append(f"    operation: ensure_{first}")
        lines.append(f'    references: ["kg:aspice.process_outcome.{first}"]')
    if orphaned:
        lines.append("Outcome ids to remove references to (no longer in the ontology):")
        for oid in orphaned:
            lines.append(f"- {oid}")
    lines.append("")
    lines.append("Return only the updated YAML.")
    return "\n".join(lines)


def strip_fences(text: str) -> str:
    """Models often wrap YAML in ``` fences despite instructions — unwrap."""
    t = (text or "").strip()
    m = re.match(r"^```[\w-]*\s*\n(.*?)\n?```\s*$", t, re.DOTALL)
    return m.group(1).strip() if m else t


def referenced_outcome_ids(pg) -> Set[str]:
    """Outcome ids referenced by a ProcedureGraph (kg:...process_outcome.<id>)."""
    ids: Set[str] = set()
    for step in getattr(pg, "steps", []) or []:
        for ref in getattr(step, "references", []) or []:
            if REF_MARKER in str(ref):
                ids.add(str(ref).split(REF_MARKER, 1)[1])
    return ids


def coverage_violations(draft_pg, previous_ids: Set[str], uncovered_ids: Set[str],
                        orphaned_ids: Set[str]) -> List[str]:
    """Deterministic gate on an LLM draft: it must add every uncovered id,
    drop every orphaned id, and keep every still-valid previous reference.
    Returns human-readable violations (empty = pass)."""
    new_ids = referenced_outcome_ids(draft_pg)
    violations: List[str] = []
    missing = sorted(uncovered_ids - new_ids)
    if missing:
        violations.append(f"draft does not cover required outcomes: {missing}")
    kept = sorted(orphaned_ids & new_ids)
    if kept:
        violations.append(f"draft still references removed outcomes: {kept}")
    regressed = sorted((previous_ids - orphaned_ids) - new_ids)
    if regressed:
        violations.append(f"draft drops previously covered outcomes: {regressed}")
    return violations
