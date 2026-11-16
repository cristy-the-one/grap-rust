"""System output for the gold scorer (spec-statements.md, card ST-06).

The object is ``{"statements": [{source_id, item_id, kind, subject}]}``.
``subject`` is the item text inside the stored span. A statement with no
subject span has ``subject`` equal to ``""``. Offsets are not written.
"""

from __future__ import annotations

from collections.abc import Sequence

from grap_mcp.readers import SourceItem

from .tables import StatementTables


def scorer_output(items: Sequence[SourceItem], tables: StatementTables) -> dict:
    """The statements object ``score_gold`` reads.

    Items are emitted in the order given. Within an item, rows follow the
    stored text order. An item with no stored statement adds nothing.
    """
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise TypeError("items must be SourceItem values")
    if not isinstance(tables, StatementTables):
        raise TypeError("tables must be StatementTables")
    rows = []
    for item in items:
        if not isinstance(item, SourceItem):
            raise TypeError("items must be SourceItem values")
        if not isinstance(item.text, str):
            raise TypeError("item text must be a string")
        for statement in tables.statements_for_item(item.source_id, item.item_id):
            rows.append({
                "source_id": statement.source_id,
                "item_id": statement.item_id,
                "kind": statement.kind,
                "subject": _subject(item.text, statement.subject),
            })
    return {"statements": rows}


def _subject(text: str, span) -> str:
    if span is None:
        return ""
    if isinstance(span, (str, bytes)) or not isinstance(span, tuple) or len(span) != 2:
        raise TypeError("subject must be a (start, end) span")
    start, end = span
    if (
        isinstance(start, bool) or isinstance(end, bool)
        or not isinstance(start, int) or not isinstance(end, int)
    ):
        raise TypeError("subject must be two integers")
    if start < 0 or end > len(text) or end <= start:
        raise ValueError(f"subject span {(start, end)} is outside the item text")
    return text[start:end]
