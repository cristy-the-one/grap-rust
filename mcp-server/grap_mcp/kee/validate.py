"""Phase 6: lightweight consistency checks on a candidate ontology.

Run before a human promotes/loads a proposed ontology. Cheap structural checks
only — not semantic judgement (that's the reviewer's job).
"""

from __future__ import annotations

from typing import Any, Dict, List


def validate_ontology(onto: Dict[str, Any]) -> Dict[str, Any]:
    """Return {ok, errors, warnings} for a candidate ontology dict."""
    errors: List[str] = []
    warnings: List[str] = []

    outcomes = onto.get("process_outcomes", [])
    if not outcomes:
        warnings.append("no outcomes were proposed")

    seen_ids = set()
    for o in outcomes:
        oid = o.get("id", "")
        if not oid:
            errors.append("an outcome has no id")
        elif oid in seen_ids:
            errors.append(f"duplicate outcome id: {oid}")
        seen_ids.add(oid)

        if not str(o.get("name", "")).strip():
            errors.append(f"outcome '{oid}' has no name")
        if not o.get("required_characteristics"):
            warnings.append(f"outcome '{oid}' has no characteristics (will always read as a gap)")

    return {"ok": not errors, "errors": errors, "warnings": warnings}
