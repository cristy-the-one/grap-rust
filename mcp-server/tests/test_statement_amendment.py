"""ST-08: Amendment 1 (spec-statements.md §4).

A shall or must marks the unit normative and does not choose the kind. The
model's normative kind is stored. Any other kind is stored as requirement and
counted. Three certain rules stop matching the cases the amendment names.
"""

import json
from pathlib import Path

from grap_mcp.kee.llm import StubProvider
from grap_mcp.readers import SourceItem

_MARKING = (
    Path(__file__).resolve().parents[1]
    / "grap_mcp" / "kee" / "statements" / "marking.json"
)

# The labelling guide's error-handling example, as Amendment 1 quotes it.
_GUIDE_ERROR = (
    "If the module is not initialized, Xyz_Transmit shall return E_NOT_OK"
)


def _item(text, *, item_id="spec#0", kind_hint="requirement"):
    return SourceItem(
        source_id="spec",
        release=None,
        item_id=item_id,
        kind_hint=kind_hint,
        text=text,
        heading_path=[],
        attributes={},
        links=[],
        file="spec.txt",
        sha256="0" * 64,
    )


def _label(text):
    from grap_mcp.kee.statements.units import label_item

    return label_item(_item(text))


def _run(tmp_path, items, provider):
    from grap_mcp.kee.graph_store import GraphStore
    from grap_mcp.kee.statements.marking import mark_items
    from grap_mcp.kee.statements.tables import StatementTables

    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        result = mark_items(list(items), provider, StatementTables(graph))
        stored = [
            StatementTables(graph).statements_for_item(item.source_id, item.item_id)
            for item in items
        ]
    return result, stored


def _reply(units):
    return json.dumps(
        {"units": {str(number): rows for number, rows in units.items()}},
        ensure_ascii=False,
    )


def _marked(text, *, kind, subject, behaviour, condition=""):
    row = {
        "kind": kind,
        "subject": subject,
        "required_behaviour": behaviour,
    }
    if condition:
        row["condition"] = condition
    return row


def test_normative_kinds_are_the_required_behaviour_kinds():
    """normative_kinds is the same four as required_behaviour_required.

    A model kind outside that list is stored as normative_fallback_kind.
    """
    data = json.loads(_MARKING.read_text(encoding="utf-8"))
    four = [
        "requirement",
        "error_handling",
        "state_or_mode_rule",
        "configuration_constraint",
    ]
    assert data["normative_kinds"] == four
    assert data["normative_kinds"] == data["required_behaviour_required"]
    assert data["normative_fallback_kind"] == "requirement"
    assert data["normative_fallback_kind"] in data["normative_kinds"]


def test_shall_and_must_set_normative_and_give_no_kind():
    """Those rows set the flag. They do not choose a kind."""
    shall = _label("The module shall start.")
    must = _label("The module must start.")
    assert shall[0].rule_id == "shall"
    assert must[0].rule_id == "must"
    for unit in (shall[0], must[0]):
        assert unit.kind is None
        assert unit.normative is True
        assert unit.origin == "rule"
        assert unit.to_model is True


def test_the_guides_error_handling_example_is_stored_as_error_handling(tmp_path):
    """The guide's shall example is error_handling when the model says so."""
    raw = _reply({0: [_marked(
        _GUIDE_ERROR,
        kind="error_handling",
        subject="Xyz_Transmit",
        behaviour="shall return E_NOT_OK",
        condition="If the module is not initialized",
    )]})
    result, stored = _run(
        tmp_path, [_item(_GUIDE_ERROR)], StubProvider(responses=[raw]),
    )
    assert len(stored[0]) == 1
    assert stored[0][0].kind == "error_handling"
    assert stored[0][0].origin == "model"
    assert result.items[0].kind_disagreements == 0
    assert result.items[0].review == ()
    text = _GUIDE_ERROR
    row = stored[0][0]
    assert text[row.subject[0]:row.subject[1]] == "Xyz_Transmit"
    assert text[row.required_behaviour[0]:row.required_behaviour[1]] == (
        "shall return E_NOT_OK"
    )


def test_a_normative_unit_the_model_calls_informative_is_stored_as_requirement(tmp_path):
    """Outside the normative kinds, the statement is requirement and counted."""
    text = "The module shall start."
    unit = _label(text)[0]
    assert unit.normative is True
    assert unit.kind is None
    raw = _reply({0: [_marked(
        text, kind="informative", subject="The module", behaviour="shall start",
    )]})
    result, stored = _run(tmp_path, [_item(text)], StubProvider(responses=[raw]))
    assert len(stored[0]) == 1
    assert stored[0][0].kind == "requirement"
    assert stored[0][0].origin == "model"
    assert result.items[0].kind_disagreements == 1
    assert result.items[0].review == ()


def test_means_does_not_match_by_means_of():
    """``means`` is a definition. ``by means of`` is not."""
    text = "Errors are reported by means of the DET."
    units = _label(text)
    assert units
    assert all(unit.rule_id != "means" for unit in units)
    assert all(unit.kind != "definition" for unit in units)


def test_no_verb_does_not_apply_to_a_table_row():
    """A table row with no other label goes to the model."""
    text = "| Timeout | 100 ms |"
    units = _label(text)
    assert len(units) == 1
    assert "Timeout" in text[units[0].start:units[0].end]
    assert "100 ms" in text[units[0].start:units[0].end]
    assert units[0].rule_id != "no-verb"
    assert units[0].kind is None
    assert units[0].to_model is True


def test_may_only_does_not_match_may_followed_by_a_year():
    """``May`` before a four-digit year is the month."""
    text = "Released in May 2024"
    units = _label(text)
    assert units
    assert all(unit.rule_id != "may-only" for unit in units)
