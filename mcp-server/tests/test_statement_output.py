"""ST-06: system output for the scorer (spec-statements.md).

The scorer reads ``{"statements": [{source_id, item_id, kind, subject}]}``.
``subject`` is the quoted text of the stored span, read back from the item.
A statement with no subject span is written with an empty subject. Offsets
are not written.
"""

import pytest

from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.statements.output import scorer_output
from grap_mcp.kee.statements.tables import Statement, StatementTables
from grap_mcp.readers import SourceItem


def _item(text, *, source_id="spec", item_id="spec#0"):
    return SourceItem(
        source_id=source_id,
        release=None,
        item_id=item_id,
        kind_hint="requirement",
        text=text,
        heading_path=[],
        attributes={},
        links=[],
        file=f"{source_id}.txt",
        sha256="0" * 64,
    )


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
        origin="model",
        extractor="stub",
    )
    fields.update(overrides)
    return Statement(**fields)


def _span(text, quote):
    at = text.index(quote)
    return (at, at + len(quote))


def test_scorer_output_quotes_the_subject(tmp_path):
    """subject is the item text inside the span, not the offsets.

    Rows follow stored text order. A second source with the same item id
    stays separate. An informative statement with no subject is written
    with subject "". An item with no stored statement adds no row. Only
    the four scorer fields are present, and a doubled space in the quote
    is kept.
    """
    first = "If the module is not initialized, Xyz_Transmit shall return E_NOT_OK."
    second = "The  module shall start when the switch is on."
    untouched = "This item was not extracted."
    note = "See the note."
    later = _statement(
        first,
        kind="informative",
        unit=(len(first) + 1, len(first) + 1 + len(note)),
        subject=None,
    )
    earlier = _statement(
        first,
        kind="error_handling",
        unit=(0, len(first)),
        subject=_span(first, "Xyz_Transmit"),
    )
    other = _statement(
        second,
        source_id="other",
        item_id="spec#0",
        kind="requirement",
        subject=_span(second, "The  module"),
    )
    items = [
        _item(first + " " + note),
        _item(untouched, item_id="spec#1"),
        _item(second, source_id="other", item_id="spec#0"),
    ]
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = StatementTables(graph)
        tables.replace_item_statements("spec", "spec#0", [later, earlier])
        tables.replace_item_statements("other", "spec#0", [other])
        written = scorer_output(items, tables)

    assert written == {
        "statements": [
            {
                "source_id": "spec",
                "item_id": "spec#0",
                "kind": "error_handling",
                "subject": "Xyz_Transmit",
            },
            {
                "source_id": "spec",
                "item_id": "spec#0",
                "kind": "informative",
                "subject": "",
            },
            {
                "source_id": "other",
                "item_id": "spec#0",
                "kind": "requirement",
                "subject": "The  module",
            },
        ]
    }
    assert list(written) == ["statements"]
    assert [list(row) for row in written["statements"]] == [
        ["source_id", "item_id", "kind", "subject"]
    ] * 3


def test_subject_span_outside_the_item_is_refused(tmp_path):
    """A span that is not inside the item text is not clipped into a quote."""
    text = "The module shall start."
    statement = _statement(text, subject=(0, len(text) + 5))
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = StatementTables(graph)
        tables.replace_item_statements("spec", "spec#0", [statement])
        with pytest.raises(ValueError, match="outside the item text"):
            scorer_output([_item(text)], tables)
