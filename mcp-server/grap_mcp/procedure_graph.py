"""
Procedure Graph loader and model for the POC.

This module parses the human-friendly flat YAML format defined in
docs/procedure-graph-format.md and provides a simple in-memory model.

It is intentionally minimal for the narrow-but-vertical ASPICE POC.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


class ProcedureStep:
    def __init__(
        self,
        id: str,
        operation: str,
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        references: Optional[List[str]] = None,
        guards: Optional[List[str]] = None,
        notes: Optional[str] = None,
    ):
        self.id = id
        self.operation = operation
        self.inputs = inputs or {}
        self.outputs = outputs or {}
        self.references = references or []
        self.guards = guards or []
        self.notes = notes or ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "operation": self.operation,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "references": self.references,
            "guards": self.guards,
            "notes": self.notes,
        }


class ProcedureGraph:
    def __init__(
        self,
        name: str,
        description: str,
        version: str,
        steps: List[ProcedureStep],
        source_path: Optional[str] = None,
    ):
        self.name = name
        self.description = description
        self.version = version
        self.steps = steps
        self.source_path = source_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "steps": [s.to_dict() for s in self.steps],
            "source_path": self.source_path,
        }

    def get_step(self, step_id: str) -> Optional[ProcedureStep]:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None


def load_from_file(path: str | Path) -> ProcedureGraph:
    """
    Load a Procedure Graph from a .pg.yaml file using the POC format.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Procedure Graph file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    steps_data = data.get("steps", [])
    steps: List[ProcedureStep] = []

    for step_data in steps_data:
        step = ProcedureStep(
            id=step_data["id"],
            operation=step_data.get("operation", ""),
            inputs=step_data.get("inputs"),
            outputs=step_data.get("outputs"),
            references=step_data.get("references"),
            guards=step_data.get("guards"),
            notes=step_data.get("notes"),
        )
        steps.append(step)

    return ProcedureGraph(
        name=data.get("name", "Unnamed Procedure"),
        description=data.get("description", ""),
        version=data.get("version", "0.0.0"),
        steps=steps,
        source_path=str(path),
    )


def load_from_directory(directory: str | Path) -> List[ProcedureGraph]:
    """
    Load all .pg.yaml files from a directory.
    Useful when a Procedure Graph is split across multiple files (future extension).
    """
    directory = Path(directory)
    graphs = []
    for file in sorted(directory.glob("*.pg.yaml")):
        graphs.append(load_from_file(file))
    return graphs


def _default_resolver(kg):
    """Build a best-effort kg-reference resolver from a KG object.

    Resolution order:
    1. If the KG exposes `resolve_reference(ref_id)`, use it.
    2. Otherwise fall back to a best-effort hybrid text search; any failure
       resolves to None (POC: references are advisory, never fatal).
    """
    def resolve(ref_id):
        if hasattr(kg, "resolve_reference"):
            try:
                return kg.resolve_reference(ref_id)
            except Exception:
                return None
        try:
            results = kg.search_and_traverse_hybrid(
                ref_id, vector=None, max_seeds=1,
                property_names=[], min_strength=0.0, max_depth=0, max_results=1,
            )
            return results[0].node_id if results else None
        except Exception:
            return None

    return resolve


def project_to_grap(pg: ProcedureGraph, kg, resolver=None) -> dict:
    """POC-level projection of a Procedure Graph into grap structures.

    - Creates a node per step.
    - Creates data-flow edges from explicit `step.outputs.field` references.
    - Resolves `kg:` references into `references` edges (best-effort) and
      reports any that could not be resolved.
    """
    if not kg:
        return {"projected": False, "reason": "No KG available for projection"}

    resolve = resolver or _default_resolver(kg)

    created_nodes = 0
    created_edges = 0
    references_resolved = 0
    unresolved_references = []
    node_map = {}  # step_id -> grap node id

    # Step 1: Create nodes for every step
    for step in pg.steps:
        try:
            text = f"[PG Step] {step.operation}"
            node_id = kg.add_node(text=text, label=f"pg-step-{step.id}")
            node_map[step.id] = node_id
            created_nodes += 1
        except Exception:
            pass

    # Step 2: Data-flow edges from explicit references
    for step in pg.steps:
        if step.id not in node_map:
            continue
        target_node = node_map[step.id]
        for input_name, ref in step.inputs.items():
            if isinstance(ref, str) and ".outputs." in ref:
                source_step_id = ref.split(".outputs.")[0]
                if source_step_id in node_map:
                    try:
                        kg.add_edge(
                            node_map[source_step_id],
                            target_node,
                            strength=1.0,
                            props={
                                "type": "data_flow",
                                "from_step": source_step_id,
                                "to_step": step.id,
                                "input": input_name,
                            },
                        )
                        created_edges += 1
                    except Exception:
                        pass

    # Step 3: Resolve kg: references into reference edges (best-effort)
    for step in pg.steps:
        if step.id not in node_map:
            continue
        step_node = node_map[step.id]
        for ref in step.references:
            target = resolve(ref)
            if target is None:
                unresolved_references.append(ref)
                continue
            try:
                kg.add_edge(
                    step_node,
                    target,
                    strength=1.0,
                    props={"type": "references", "ref": ref},
                )
                references_resolved += 1
            except Exception:
                unresolved_references.append(ref)

    return {
        "projected": True,
        "steps_projected": created_nodes,
        "data_flow_edges": created_edges,
        "references_resolved": references_resolved,
        "unresolved_references": unresolved_references,
        "message": "Procedure Graph projected into KG with steps, data flow, and references (POC)",
    }
