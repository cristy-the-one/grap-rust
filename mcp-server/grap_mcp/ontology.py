"""ASPICE ontology model, loader, and projection into the grap KG.

The ontology is small and human-edited (see docs/examples/minimal-aspice-ontology.yaml):
a set of process outcomes, each with required characteristics, plus mapping hints
(keywords/properties) used as lightweight inference indicators.
"""

from pathlib import Path
from typing import Dict, List, Optional

import yaml


class ProcessOutcome:
    def __init__(self, id: str, name: str, description: str, required_characteristics: List[str]):
        self.id = id
        self.name = name
        self.description = description
        self.required_characteristics = required_characteristics


class MappingHint:
    def __init__(self, outcome: str, keywords: List[str], properties: List[str]):
        self.outcome = outcome
        self.keywords = keywords
        self.properties = properties


class Ontology:
    def __init__(self, version: str, process_outcomes: List[ProcessOutcome],
                 mapping_hints: List[MappingHint]):
        self.version = version
        self.process_outcomes = process_outcomes
        self.mapping_hints = mapping_hints
        self._outcomes = {o.id: o for o in process_outcomes}
        self._hints = {h.outcome: h for h in mapping_hints}

    def outcome(self, outcome_id: str) -> Optional[ProcessOutcome]:
        return self._outcomes.get(outcome_id)

    def hint_for(self, outcome_id: str) -> Optional[MappingHint]:
        return self._hints.get(outcome_id)


def load_ontology_from_file(path: str) -> Ontology:
    """Parse an ASPICE ontology YAML file into an Ontology model."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Ontology file not found: {path}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    outcomes: List[ProcessOutcome] = []
    for o in data.get("process_outcomes", []):
        outcomes.append(ProcessOutcome(
            id=o["id"],
            name=o.get("name", ""),
            description=o.get("description", ""),
            required_characteristics=list(o.get("required_characteristics", [])),
        ))

    hints: List[MappingHint] = []
    for h in data.get("mapping_hints", []):
        keywords: List[str] = []
        properties: List[str] = []
        for indicator in h.get("indicators", []):
            keywords.extend(indicator.get("keywords", []))
            properties.extend(indicator.get("properties", []))
        hints.append(MappingHint(outcome=h["outcome"], keywords=keywords, properties=properties))

    return Ontology(
        version=str(data.get("version", "0.1.0")),
        process_outcomes=outcomes,
        mapping_hints=hints,
    )


def project_ontology_to_grap(ontology: Ontology, kg) -> Dict:
    """Project the ontology into the grap KG.

    - One node per process outcome, labelled `kg:aspice.process_outcome.<id>`
      (the exact form PG `references` use, so they resolve to these nodes).
    - One node per required characteristic, labelled
      `kg:aspice.characteristic.<outcome>.<char>`, linked from its outcome by a
      `requires` edge.
    """
    outcomes_projected = 0
    characteristics_projected = 0
    outcome_node_map: Dict[str, int] = {}

    for o in ontology.process_outcomes:
        onode = kg.add_node(
            text=f"[ASPICE outcome] {o.name}: {o.description}",
            label=f"kg:aspice.process_outcome.{o.id}",
        )
        outcome_node_map[o.id] = onode
        outcomes_projected += 1

        for char in o.required_characteristics:
            cnode = kg.add_node(
                text=f"[characteristic] {char}",
                label=f"kg:aspice.characteristic.{o.id}.{char}",
            )
            kg.add_edge(onode, cnode, strength=1.0,
                        props={"type": "requires", "characteristic": char})
            characteristics_projected += 1

    return {
        "outcomes_projected": outcomes_projected,
        "characteristics_projected": characteristics_projected,
        "outcome_node_map": outcome_node_map,
    }
