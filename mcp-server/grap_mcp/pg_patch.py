"""Deterministic PG reference maintenance; publication policy lives in the application."""
from __future__ import annotations

from typing import List, Set
import re
import yaml

REF_MARKER = "process_outcome."


def patch_references(pg, uncovered: List[dict], orphaned: List[str]) -> str:
    """Preserve existing step semantics and edit only outcome coverage.

    Removed references leave their step intact for review. Added steps are
    explicitly gap checks, with deterministic collision-free ids.
    """
    data = pg.to_dict()
    data.pop("source_path", None)
    removed = set(orphaned)
    for step in data["steps"]:
        step["references"] = [ref for ref in step["references"]
                              if REF_MARKER not in str(ref)
                              or str(ref).split(REF_MARKER, 1)[1] not in removed]
    used = {step["id"] for step in data["steps"]}
    for outcome in sorted(uncovered, key=lambda o: o["id"]):
        oid = outcome["id"]
        base = "cover_" + re.sub(r"[^a-zA-Z0-9_-]", "_", oid)
        sid, suffix = base, 2
        while sid in used:
            sid = f"{base}_{suffix}"
            suffix += 1
        used.add(sid)
        data["steps"].append({"id": sid, "operation": "gap_check",
                              "references": [f"kg:aspice.process_outcome.{oid}"]})
    version = str(data["version"])
    match = re.fullmatch(r"(\d+\.\d+\.)(\d+)", version)
    data["version"] = match[1] + str(int(match[2]) + 1) if match else version + ".1"
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


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
    """Coverage gate on a reference patch: it must add every uncovered id,
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
