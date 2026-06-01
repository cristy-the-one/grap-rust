"""Publish-time validation: PG validity + cross-reference checks.

Composes the format-level validate_pg with ontology process-outcome reference
validity. Structural problems and invalid ontology references are errors;
unresolved generic kg: references stay warnings. Never raises.
"""

from typing import Any, Dict

from .procedure_graph import ProcedureGraph, _default_resolver
from .validation import validate_pg

_OUTCOME_PREFIX = "kg:aspice.process_outcome."


def validate_for_publish(pg: ProcedureGraph, kg=None, ontology=None) -> Dict[str, Any]:
    """Return {ok, errors, warnings} for publish-time validation."""
    resolver = _default_resolver(kg) if kg else None
    base = validate_pg(pg, resolver=resolver)
    errors = list(base["errors"])
    warnings = list(base["warnings"])

    if ontology is not None:
        valid = {o.id for o in ontology.process_outcomes}
        for step in pg.steps:
            for ref in step.references:
                if ref.startswith(_OUTCOME_PREFIX):
                    oid = ref[len(_OUTCOME_PREFIX):]
                    if oid not in valid:
                        errors.append({
                            "code": "unknown_process_outcome",
                            "message": f"Step '{step.id}' references unknown process outcome '{oid}'",
                            "step_id": step.id,
                        })
    else:
        warnings.append({
            "code": "ontology_not_loaded",
            "message": "Ontology not loaded; process-outcome reference validity unchecked",
            "step_id": None,
        })

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
