"""Format-level validation for Procedure Graphs.

Structural checks always run. Cross-KG checks run only when a reference
resolver is provided. Validation never raises and never blocks — it returns a
structured report (per the publish pipeline's "inform, don't block" rule).
"""

from typing import Any, Callable, Dict, List, Optional

from .procedure_graph import ProcedureGraph

# A resolver maps a kg reference id (e.g. "kg:aspice.ontology.v1") to a node id
# or None when it cannot be resolved.
Resolver = Callable[[str], Optional[int]]


def _issue(code: str, message: str, step_id: Optional[str] = None) -> Dict[str, Any]:
    return {"code": code, "message": message, "step_id": step_id}


def validate_pg(pg: ProcedureGraph, resolver: Optional[Resolver] = None) -> Dict[str, Any]:
    """Validate a ProcedureGraph. Returns {ok, errors, warnings}.

    `resolver`, when supplied, is used to check that every `kg:` reference
    resolves; unresolved references become warnings (never errors).
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    # --- Structural: unique, non-empty step ids ---
    seen = set()
    for step in pg.steps:
        if not step.id:
            errors.append(_issue("empty_step_id", "Step has an empty id"))
            continue
        if step.id in seen:
            errors.append(_issue("duplicate_step_id", f"Duplicate step id '{step.id}'", step.id))
        seen.add(step.id)

    # Index declared outputs per step for data-flow checks.
    outputs_by_step = {s.id: set(s.outputs.keys()) for s in pg.steps if s.id}

    # --- Structural: data-flow references ---
    for step in pg.steps:
        if not step.operation:
            warnings.append(_issue("empty_operation", f"Step '{step.id}' has no operation", step.id))
        for input_name, ref in step.inputs.items():
            if not (isinstance(ref, str) and ".outputs." in ref):
                continue  # literal value, not a data-flow reference
            source_id, _, output_name = ref.partition(".outputs.")
            if source_id not in outputs_by_step:
                errors.append(_issue(
                    "unknown_dataflow_step",
                    f"Step '{step.id}' input '{input_name}' references unknown step '{source_id}'",
                    step.id,
                ))
            elif output_name not in outputs_by_step[source_id]:
                errors.append(_issue(
                    "unknown_dataflow_output",
                    f"Step '{step.id}' input '{input_name}' references undeclared output "
                    f"'{source_id}.outputs.{output_name}'",
                    step.id,
                ))

    # --- Cross-KG: reference resolvability (warnings only) ---
    if resolver is not None:
        for step in pg.steps:
            for ref in step.references:
                if resolver(ref) is None:
                    warnings.append(_issue(
                        "unresolved_kg_reference",
                        f"Step '{step.id}' reference '{ref}' did not resolve in the KG",
                        step.id,
                    ))

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
