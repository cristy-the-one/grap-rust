"""ST-04: statements become rows in mentions (spec-statements.md §6, first part).

For each statement the subject span is a mention. Every identifier the R-09
rule finds inside any of that statement's spans is a mention. A
values_and_timing span that is itself an identifier is a mention. The same
character span is one row. Linking and edges are not this card.
"""

import pytest

from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.resolution.tables import ResolutionTables
from grap_mcp.kee.statements.tables import Statement


def _span(text, quote, start=0):
    at = text.index(quote, start)
    return (at, at + len(quote))


def _statement(text, **overrides):
    fields = dict(
        source_id="spec",
        item_id="spec#0",
        unit=(0, len(text)),
        kind="requirement",
        subject=None,
        condition=None,
        required_behaviour=None,
        values_and_timing=(),
        exception=None,
        origin="rule",
        extractor="ruleset 1",
    )
    fields.update(overrides)
    return Statement(**fields)


def _open(tmp_path, name="graph_store.db"):
    return GraphStore(str(tmp_path / name))


def _write(graph, text, source_id, item_id, statements):
    from grap_mcp.kee.statements.mentions import write_statement_mentions

    return write_statement_mentions(
        ResolutionTables(graph), text, source_id, item_id, statements,
    )


def _rows(graph, source_id, item_id):
    return ResolutionTables(graph).mentions_for_item(source_id, item_id)


def test_subject_span_becomes_one_mention(tmp_path):
    """The subject span is a mention. Its sentence is the sentence around it.

    The spec names no type hint, so the row has none. The extractor is the
    statement's.
    """
    text = "The module shall start. Another sentence follows."
    statement = _statement(text, subject=_span(text, "The module"))
    with _open(tmp_path) as graph:
        ids = _write(graph, text, "spec", "spec#0", [statement])
        rows = _rows(graph, "spec", "spec#0")
    assert len(rows) == 1
    assert ids == [rows[0].mention_id]
    row = rows[0]
    assert (row.span_start, row.span_end) == _span(text, "The module")
    assert row.text == "The module"
    assert row.sentence == "The module shall start."
    assert row.text in row.sentence
    assert row.type_hint is None
    assert row.extractor == "ruleset 1"
    assert row.source_id == "spec"
    assert row.item_id == "spec#0"


def test_identifiers_inside_every_span(tmp_path):
    """R-09 identifiers inside any span become mentions, including the unit.

    Com_SendSignal sits only in the unit. The others sit in condition,
    required behaviour, and exception. Ordinary words in those spans do not.
    """
    text = (
        "Before Com_SendSignal, the module shall call /ISignal/SigA "
        "when VehSpdFL is on unless [SWS_Com_00123] says otherwise."
    )
    statement = _statement(
        text,
        subject=_span(text, "the module"),
        condition=_span(text, "when VehSpdFL is on"),
        required_behaviour=_span(text, "shall call /ISignal/SigA"),
        exception=_span(text, "unless [SWS_Com_00123] says otherwise"),
    )
    with _open(tmp_path) as graph:
        _write(graph, text, "spec", "spec#0", [statement])
        rows = _rows(graph, "spec", "spec#0")
    assert [row.text for row in rows] == [
        "Com_SendSignal",
        "the module",
        "/ISignal/SigA",
        "VehSpdFL",
        "[SWS_Com_00123]",
    ]
    by_text = {row.text: row for row in rows}
    assert (by_text["Com_SendSignal"].span_start, by_text["Com_SendSignal"].span_end) == _span(
        text, "Com_SendSignal",
    )
    assert (by_text["[SWS_Com_00123]"].span_start, by_text["[SWS_Com_00123]"].span_end) == _span(
        text, "[SWS_Com_00123]",
    )
    assert all(row.type_hint is None for row in rows)
    assert all(row.extractor == "ruleset 1" for row in rows)


def test_prose_dots_digits_and_plain_tokens_are_not_identifiers(tmp_path):
    """Inside a phrase, a dot, a digit, or a plain word is not an identifier.

    That is the R-09 rule: nr. 58, art. 5, file.txt, 2/1999 and BNR stay prose.
    """
    text = "The module shall read nr. 58 and art. 5 and file.txt and 2/1999 and BNR."
    behaviour = "shall read nr. 58 and art. 5 and file.txt and 2/1999 and BNR."
    statement = _statement(
        text,
        subject=_span(text, "The module"),
        required_behaviour=_span(text, behaviour),
    )
    with _open(tmp_path) as graph:
        _write(graph, text, "spec", "spec#0", [statement])
        rows = _rows(graph, "spec", "spec#0")
    assert [row.text for row in rows] == ["The module"]


def test_values_and_timing_span_that_is_an_identifier(tmp_path):
    """A values span that is an identifier is a mention, as that span.

    BNR is an identifier as a whole name and is not a strong token inside
    the phrase, so only the values span puts it in. 100 has no letter, so
    it is not a mention. 100 ms is not an identifier. E_NOT_OK is one row,
    though the phrase contains it too.
    """
    text = "The module shall wait 100 ms and return E_NOT_OK or BNR."
    statement = _statement(
        text,
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall wait 100 ms and return E_NOT_OK or BNR."),
        values_and_timing=(
            _span(text, "100"),
            _span(text, "100 ms"),
            _span(text, "E_NOT_OK"),
            _span(text, "BNR"),
        ),
    )
    with _open(tmp_path) as graph:
        _write(graph, text, "spec", "spec#0", [statement])
        rows = _rows(graph, "spec", "spec#0")
    assert [row.text for row in rows] == ["The module", "E_NOT_OK", "BNR"]
    assert "100" not in [row.text for row in rows]
    assert "100 ms" not in [row.text for row in rows]


def test_a_values_span_with_no_letter_is_not_a_mention(tmp_path):
    """A token with no letter is never a mention.

    A values span ``100`` has no letter, so it is not a mention. ``5.0``
    is the same. ``0x01`` stays a mention because it has a letter.
    ``E_NOT_OK`` is one mention.
    """
    text = "The module shall return 100 or 5.0 or 0x01 or E_NOT_OK."
    statement = _statement(
        text,
        values_and_timing=(
            _span(text, "100"),
            _span(text, "5.0"),
            _span(text, "0x01"),
            _span(text, "E_NOT_OK"),
        ),
    )
    with _open(tmp_path) as graph:
        _write(graph, text, "spec", "spec#0", [statement])
        rows = _rows(graph, "spec", "spec#0")
    assert [row.text for row in rows] == ["0x01", "E_NOT_OK"]


def test_the_same_character_span_is_one_row(tmp_path):
    """A subject that is itself an identifier is one mention, not two."""
    text = "Com_SendSignal shall start."
    statement = _statement(
        text,
        subject=_span(text, "Com_SendSignal"),
        required_behaviour=_span(text, "shall start"),
    )
    with _open(tmp_path) as graph:
        _write(graph, text, "spec", "spec#0", [statement])
        rows = _rows(graph, "spec", "spec#0")
    assert len(rows) == 1
    assert rows[0].text == "Com_SendSignal"
    assert (rows[0].span_start, rows[0].span_end) == _span(text, "Com_SendSignal")


def test_rows_are_replaced_per_item_and_a_second_run_changes_nothing(tmp_path):
    """Re-extraction replaces that item's mentions. An unchanged item is left.

    A second call with the same statements does not write new rows.
    """
    text = "The module shall start. Another sentence follows."
    other = "The controller shall stop."
    first = _statement(text, subject=_span(text, "The module"))
    second = _statement(
        other,
        item_id="spec#1",
        subject=_span(other, "The controller"),
        extractor="ruleset 2",
    )
    with _open(tmp_path) as graph:
        first_ids = _write(graph, text, "spec", "spec#0", [first])
        other_ids = _write(graph, other, "spec", "spec#1", [second])
        again = _write(graph, text, "spec", "spec#0", [first])
        assert again == first_ids
        assert [row.mention_id for row in _rows(graph, "spec", "spec#0")] == first_ids
        assert [row.text for row in _rows(graph, "spec", "spec#0")] == ["The module"]

        changed = _statement(text, subject=_span(text, "shall start"), extractor="ruleset 9")
        replaced = _write(graph, text, "spec", "spec#0", [changed])
        assert replaced != first_ids
        rows = _rows(graph, "spec", "spec#0")
        assert [row.text for row in rows] == ["shall start"]
        assert rows[0].extractor == "ruleset 9"
        assert rows[0].mention_id not in first_ids
        assert [row.mention_id for row in _rows(graph, "spec", "spec#1")] == other_ids
        assert _rows(graph, "spec", "spec#1")[0].text == "The controller"

        cleared = _write(graph, text, "spec", "spec#0", [])
        assert cleared == []
        assert _rows(graph, "spec", "spec#0") == []
        assert [row.mention_id for row in _rows(graph, "spec", "spec#1")] == other_ids


def test_a_span_outside_the_text_is_refused(tmp_path):
    """A span that is not inside the item text is not stored and not clipped."""
    text = "The module shall start."
    statement = _statement(text, subject=(0, 500), unit=(0, len(text)))
    with _open(tmp_path) as graph:
        with pytest.raises(ValueError, match="subject"):
            _write(graph, text, "spec", "spec#0", [statement])
        assert _rows(graph, "spec", "spec#0") == []


def test_a_statement_for_another_item_is_refused(tmp_path):
    """Rows are for the item being written. A foreign statement is not stored."""
    text = "The module shall start."
    statement = _statement(text, item_id="spec#9", subject=_span(text, "The module"))
    with _open(tmp_path) as graph:
        with pytest.raises(ValueError, match="spec#9"):
            _write(graph, text, "spec", "spec#0", [statement])
        assert _rows(graph, "spec", "spec#0") == []
        assert _rows(graph, "spec", "spec#9") == []
