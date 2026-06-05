"""Tests for scripts/curate_ontology.py filters."""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import curate_ontology as cur  # noqa: E402


def test_filter_relations_drops_stop_and_symbol_types():
    rels = [
        {"from": "A", "type": "are", "to": "B"},      # copula -> drop
        {"from": "A", "type": "==", "to": "B"},       # symbol -> drop
        {"from": "A", "type": "e", "to": "B"},        # len<2 -> drop
        {"from": "Ministerul", "type": "emite", "to": "Ordinul"},   # keep
        {"from": "X", "type": "", "to": "Y"},         # empty -> drop
    ]
    kept = cur.filter_relations(rels, top_concepts=0)
    assert len(kept) == 1
    assert kept[0]["type"] == "emite"


def test_filter_relations_top_concepts_keeps_connected_core():
    rels = [
        {"from": "hub", "type": "rel", "to": "a"},
        {"from": "hub", "type": "rel", "to": "b"},
        {"from": "lonely1", "type": "rel", "to": "lonely2"},   # low-degree pair
    ]
    kept = cur.filter_relations(rels, top_concepts=3)   # hub, a, b are top-3
    pairs = {(r["from"], r["to"]) for r in kept}
    assert ("hub", "a") in pairs and ("hub", "b") in pairs
    assert ("lonely1", "lonely2") not in pairs


def test_curate_outcomes_drops_empty_and_caps_by_specificity():
    pos = [
        {"id": "a", "name": "A", "required_characteristics": ["x", "y", "z"]},
        {"id": "b", "name": "B", "required_characteristics": ["x"]},
        {"id": "c", "name": "C", "required_characteristics": []},   # empty -> dropped
    ]
    hints = [{"outcome": "a"}, {"outcome": "b"}, {"outcome": "c"}]
    outs, kept_hints = cur.curate_outcomes(pos, hints, drop_empty=True, max_outcomes=1)
    assert [o["id"] for o in outs] == ["a"]              # most-specified kept
    assert [h["outcome"] for h in kept_hints] == ["a"]   # hints synced
