"""Structural diff between two parsed Procedure Graphs.

Steps are matched by id. A step is "changed" when any of its content fields
differ. Top-level metadata differences (name/description/version) are reported
separately.
"""

from typing import Any, Dict, List

from .procedure_graph import ProcedureGraph, ProcedureStep

_STEP_FIELDS = ["operation", "inputs", "outputs", "references", "guards", "notes"]
_META_FIELDS = ["name", "description", "version"]


def _changed_fields(a: ProcedureStep, b: ProcedureStep) -> List[str]:
    return [f for f in _STEP_FIELDS if getattr(a, f) != getattr(b, f)]


def diff_pg(pg_a: ProcedureGraph, pg_b: ProcedureGraph) -> Dict[str, Any]:
    """Compute the structural diff from `pg_a` to `pg_b`."""
    a_steps = {s.id: s for s in pg_a.steps}
    b_steps = {s.id: s for s in pg_b.steps}

    steps_added = sorted(b_steps.keys() - a_steps.keys())
    steps_removed = sorted(a_steps.keys() - b_steps.keys())

    steps_changed: List[Dict[str, Any]] = []
    for step_id in sorted(a_steps.keys() & b_steps.keys()):
        fields = _changed_fields(a_steps[step_id], b_steps[step_id])
        if fields:
            steps_changed.append({"step_id": step_id, "fields_changed": fields})

    metadata_changed: Dict[str, List[Any]] = {}
    for f in _META_FIELDS:
        av, bv = getattr(pg_a, f), getattr(pg_b, f)
        if av != bv:
            metadata_changed[f] = [av, bv]

    return {
        "steps_added": steps_added,
        "steps_removed": steps_removed,
        "steps_changed": steps_changed,
        "metadata_changed": metadata_changed,
    }
