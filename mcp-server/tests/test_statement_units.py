"""ST-02: units and certain labels (spec-statements.md §4).

The rule table is data. Each row has one example that must match that row and
one that must not. Splitting reuses the sentence splitter and keeps a number,
a bracketed identifier, and an abbreviation intact.
"""

import json
from pathlib import Path

import pytest

from grap_mcp.kee.statements.schema import statement_kinds
from grap_mcp.readers import SourceItem

_RULES_PATH = (
    Path(__file__).resolve().parents[1]
    / "grap_mcp" / "kee" / "statements" / "rules.json"
)


def _data():
    return json.loads(_RULES_PATH.read_text(encoding="utf-8"))


def _rules():
    return _data()["rules"]


def _item(text, *, kind_hint="requirement", heading_path=()):
    return SourceItem(
        source_id="spec",
        release=None,
        item_id="spec#0",
        kind_hint=kind_hint,
        text=text,
        heading_path=list(heading_path),
        attributes={},
        links=[],
        file="spec.txt",
        sha256="0" * 64,
    )


def _label(text, **kwargs):
    from grap_mcp.kee.statements.units import label_item

    return label_item(_item(text, **kwargs))


def _slice(text, unit):
    return text[unit.start:unit.end]


@pytest.mark.parametrize("rule", _rules(), ids=lambda rule: rule["id"])
def test_each_rule_matches_its_example_and_not_its_counterexample(rule):
    """The row's match example takes that row. The other example does not."""
    data = _data()
    matched = _label(rule["match"])
    assert len(matched) == 1
    unit = matched[0]
    assert unit.rule_id == rule["id"]
    assert unit.kind == rule["kind"]
    if rule["kind"] is None:
        assert rule["normative"] is True
        assert unit.normative is True
    else:
        assert unit.kind in statement_kinds()
        assert unit.normative is False
    assert unit.origin == "rule"
    assert unit.to_model is (rule["kind"] not in set(data["skip_model"]))
    assert _slice(rule["match"], unit) == rule["match"].strip()
    assert (unit.start, unit.end) == (0, len(rule["match"].strip()))

    counter = _label(rule["non_match"])
    assert counter
    assert all(unit.rule_id != rule["id"] for unit in counter)


def test_rule_rows_cover_the_certain_labels_and_name_schema_kinds():
    """Every certain label in §4 has a row. A named kind is a schema kind.

    shall and must name no kind. Amendment 1 sets normative instead.
    """
    data = _data()
    kinds = set(statement_kinds())
    assert [rule["id"] for rule in data["rules"]] == [
        "note-block",
        "rationale-block",
        "example-block",
        "shall",
        "must",
        "defined-as",
        "means",
        "glossary-row",
        "may-only",
        "caption",
        "change-history",
        "heading",
        "no-verb",
    ]
    named = {rule["kind"] for rule in data["rules"] if rule["kind"] is not None}
    assert named <= kinds
    assert [rule["id"] for rule in data["rules"] if rule["kind"] is None] == [
        "shall",
        "must",
    ]
    assert set(data["skip_model"]) == {"other", "informative"}
    for rule in data["rules"]:
        assert rule["match"].strip() and rule["non_match"].strip()
        assert rule["match"] != rule["non_match"]


def test_other_and_informative_do_not_go_to_the_model():
    """A unit labelled other or informative by rule does not go to the model."""
    note = _label("NOTE: The module shall ignore unknown ids.")
    assert note[0].kind == "informative" and note[0].to_model is False

    permission = _label("The caller may retry.")
    assert permission[0].kind == "informative" and permission[0].to_model is False

    fragment = _label("speed, torque")
    assert fragment[0].kind == "other" and fragment[0].to_model is False

    demand = _label("The module shall start.")
    assert demand[0].kind is None and demand[0].normative is True
    assert demand[0].origin == "rule"
    assert demand[0].to_model is True

    defined = _label("ECU is defined as an electronic control unit.")
    assert defined[0].kind == "definition" and defined[0].to_model is True


def test_a_unit_with_no_certain_label_goes_to_the_model_without_a_kind():
    text = "The module starts when the switch is on."
    units = _label(text)
    assert len(units) == 1
    assert units[0].kind is None
    assert units[0].rule_id is None
    assert units[0].origin is None
    assert units[0].to_model is True
    assert _slice(text, units[0]) == text


def test_an_unrecognised_verb_is_not_labelled_other():
    """No-verb is only for a fragment. A sentence whose verb is unknown stays unlabelled."""
    text = "The gateway throttles the burst."
    units = _label(text)
    assert len(units) == 1
    assert units[0].kind is None
    assert units[0].to_model is True


def test_shall_inside_a_marked_block_is_informative_and_the_block_ends_at_a_blank_line():
    text = (
        "NOTE: The module shall ignore unknown ids.\n"
        "It may log them.\n"
        "\n"
        "The module shall start."
    )
    units = _label(text)
    assert [_slice(text, unit) for unit in units] == [
        "NOTE: The module shall ignore unknown ids.",
        "It may log them.",
        "The module shall start.",
    ]
    assert [unit.rule_id for unit in units] == ["note-block", "note-block", "shall"]
    assert [unit.to_model for unit in units] == [False, False, True]


def test_a_heading_marks_the_whole_item_as_that_block():
    text = "The module shall ignore unknown ids."
    noted = _label(text, heading_path=["Specification", "Notes"])
    assert noted[0].rule_id == "note-block"
    assert noted[0].kind == "informative" and noted[0].to_model is False

    reasoned = _label("The timeout is short.", heading_path=["Rationale"])
    assert reasoned[0].rule_id == "rationale-block"

    shown = _label("The caller passes one id.", heading_path=["Example"])
    assert shown[0].rule_id == "example-block"

    plain = _label(text, heading_path=["Specification", "Startup"])
    assert plain[0].rule_id == "shall"


def test_note_marker_covers_a_following_list_entry_until_a_blank_line():
    text = "NOTE:\n- The module shall start.\n\nThe module must stop."
    units = _label(text)
    assert [_slice(text, unit) for unit in units] == [
        "NOTE:",
        "- The module shall start.",
        "The module must stop.",
    ]
    assert [unit.rule_id for unit in units] == ["note-block", "note-block", "must"]


def test_an_explicit_definition_stays_a_definition_when_it_also_says_may():
    """``may`` as the only modal is a permission. It does not override ``means``."""
    assert _label("ECU means a unit the caller may reset.")[0].rule_id == "means"
    assert _label("ECU is defined as a unit the caller may reset.")[0].rule_id == "defined-as"
    assert _label("ECU — a unit the caller may reset")[0].rule_id == "glossary-row"
    assert _label("The caller may retry.")[0].rule_id == "may-only"


def test_a_caption_number_does_not_end_the_caption():
    text = "Figure 2. Startup sequence"
    units = _label(text)
    assert [_slice(text, unit) for unit in units] == [text]
    assert units[0].rule_id == "caption"

    mixed = "Figure 2. Startup sequence. The module shall start."
    parts = _label(mixed)
    assert [_slice(mixed, unit) for unit in parts] == [
        "Figure 2. Startup sequence.",
        "The module shall start.",
    ]
    assert [part.rule_id for part in parts] == ["caption", "shall"]


def test_shall_is_a_word_and_one_unit_can_hold_two_demands():
    """Stage 1 does not split a unit into statements. That is stage 2."""
    assert _label("The marshall starts the bus.")[0].kind is None
    text = "The module shall return E_NOT_OK and shall report the fault."
    units = _label(text)
    assert len(units) == 1
    assert units[0].rule_id == "shall"
    assert _slice(text, units[0]) == text


def test_sentences_list_entries_and_table_rows_are_the_units():
    prose = "The timeout is 1.5 seconds. The module shall start."
    split = _label(prose)
    assert [_slice(prose, unit) for unit in split] == [
        "The timeout is 1.5 seconds.",
        "The module shall start.",
    ]
    assert "1.5" in _slice(prose, split[0])
    assert split[1].rule_id == "shall"

    bracketed = "Call [SWS.Com.001] before use. The module shall start."
    parts = _label(bracketed)
    assert [_slice(bracketed, unit) for unit in parts] == [
        "Call [SWS.Com.001] before use.",
        "The module shall start.",
    ]

    abbreviated = "See Fig. 1 before starting. The module shall start."
    fig = _label(abbreviated)
    assert [_slice(abbreviated, unit) for unit in fig] == [
        "See Fig. 1 before starting.",
        "The module shall start.",
    ]

    for_example = "See e.g. The diagram in section 2. The module shall start."
    example = _label(for_example)
    assert "e.g. The diagram" in _slice(for_example, example[0])
    assert example[-1].rule_id == "shall"

    law_abbrev = "See art. 2 before use. The module shall start."
    article = _label(law_abbrev)
    assert "art. 2" in _slice(law_abbrev, article[0])
    assert len(article) == 2

    listed = "- The module shall start. The module shall stop.\n- The caller may retry."
    entries = _label(listed)
    assert [_slice(listed, unit) for unit in entries] == [
        "- The module shall start. The module shall stop.",
        "- The caller may retry.",
    ]
    assert [unit.rule_id for unit in entries] == ["shall", "may-only"]

    table = "| Term | Meaning |\n| --- | --- |\n| The module shall start. | on reset |\n"
    rows = _label(table)
    assert [_slice(table, unit) for unit in rows] == [
        "| Term | Meaning |",
        "| The module shall start. | on reset |",
    ]
    assert rows[0].rule_id is None
    assert rows[0].kind is None and rows[0].to_model is True
    assert rows[1].rule_id == "shall"
    assert "---" not in "".join(_slice(table, unit) for unit in rows)


def test_a_glossary_table_row_under_a_glossary_heading_is_a_definition():
    text = "| Term | Meaning |\n| --- | --- |\n| ECU | electronic control unit |\n"
    plain = _label(text)
    assert plain[-1].rule_id is None
    assert plain[-1].kind is None and plain[-1].to_model is True

    defined = _label(text, heading_path=["Glossary"])
    assert _slice(text, defined[-1]) == "| ECU | electronic control unit |"
    assert defined[-1].rule_id == "glossary-row"
    assert defined[-1].kind == "definition"
    assert defined[-1].to_model is True


def test_text_items_are_labelled_and_other_kind_hints_are_refused():
    text = "The module shall start."
    assert _label(text, kind_hint="text")[0].rule_id == "shall"
    with pytest.raises(ValueError, match="signal"):
        _label(text, kind_hint="signal")


def test_labelling_the_same_item_twice_is_the_same_units():
    text = "The module shall start. The caller may retry."
    assert _label(text) == _label(text)
