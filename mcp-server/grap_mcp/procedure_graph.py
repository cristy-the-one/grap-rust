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
