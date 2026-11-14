"""Statements to rows in ``mentions`` (spec-statements.md §6, first part).

For one item, each statement contributes:

- its ``subject`` span, when it has one;
- every identifier the R-09 rule finds inside any of its spans (the unit,
  the subject, the condition, the required behaviour, each values span, and
  the exception);
- each ``values_and_timing`` span that is itself an identifier.

The same character span is one row. The spec writes "type hint in brackets"
and then names none, so ``type_hint`` is absent. Linking is left to the
entity-resolution stages. A second call with the same rows does not write.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from grap_mcp.kee.resolution.backfill import _sentence
from grap_mcp.kee.resolution.tables import Mention, ResolutionTables
from grap_mcp.kee.statements.tables import Statement

# R-09, the same predicates as ``working_graph`` and the pair-packet finder.
# A whole name with no whitespace is an identifier when it has an underscore,
# a slash, a dot, a digit, or a capital after the first character. Inside a
# phrase, only an underscore, a leading slash, a CamelCase hump, or a
# bracketed id counts. Prose dots and digits do not.
_EDGE_PUNCT = ".,;:!?()\"'`<>"
_BRACKETED_ID = re.compile(r"\[[^\]\s]+\]")
_CAMEL_HUMP = re.compile(r"[a-z][A-Z]")

_PARTS = ("unit", "subject", "condition", "required_behaviour", "exception")


def write_statement_mentions(
    tables: ResolutionTables,
    text: str,
    source_id: str,
    item_id: str,
    statements: Sequence[Statement],
) -> list[int]:
    """Replace one item's mentions with the rows its statements produce.

    Returns the mention ids. When the stored rows are already those rows,
    the ids already stored are returned and nothing is written.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(tables, ResolutionTables):
        raise TypeError("tables must be ResolutionTables")
    desired = _mentions(text, source_id, item_id, statements)
    existing = tables.mentions_for_item(source_id, item_id)
    if [_signature(row) for row in existing] == [_signature(row) for row in desired]:
        return [row.mention_id for row in existing]
    return tables.replace_item_mentions(source_id, item_id, desired)


def _mentions(
    text: str, source_id: str, item_id: str, statements: Sequence[Statement],
) -> list[Mention]:
    rows = _statement_rows(statements)
    found: dict[tuple[int, int], str] = {}
    for statement in rows:
        if statement.source_id != source_id or statement.item_id != item_id:
            raise ValueError(
                f"statement {statement.source_id}|{statement.item_id} does not belong "
                f"to the item being re-extracted ({source_id}|{item_id})"
            )
        extractor = _extractor(statement.extractor)
        if statement.subject is not None:
            _add(found, text, statement.subject, "subject", extractor)
        for field, span in _part_spans(statement):
            start, end = _check_span(span, field, text)
            for ident_start, ident_end in _identifier_spans(text[start:end], start):
                _add(found, text, (ident_start, ident_end), field, extractor)
        for span in statement.values_and_timing:
            start, end = _check_span(span, "values_and_timing", text)
            piece = text[start:end]
            if _span_is_identifier(piece):
                _add(found, text, (start, end), "values_and_timing", extractor)
    mentions = [
        Mention(
            source_id=source_id,
            item_id=item_id,
            span_start=start,
            span_end=end,
            text=text[start:end],
            sentence=_sentence(text, start, end),
            type_hint=None,
            extractor=extractor,
        )
        for (start, end), extractor in sorted(found.items())
    ]
    return mentions


def _statement_rows(statements: Sequence[Statement]) -> list[Statement]:
    if isinstance(statements, (str, bytes)) or not isinstance(statements, Sequence):
        raise TypeError("statements must be a sequence of Statement values")
    rows = list(statements)
    for statement in rows:
        if not isinstance(statement, Statement):
            raise TypeError("statements must be Statement values")
    return rows


def _extractor(value: object) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError("extractor must be a non-empty string")
    return value


def _add(found, text, span, field, extractor) -> None:
    start, end = _check_span(span, field, text)
    if text[start:end].strip() == "":
        raise ValueError(f"{field} span {(start, end)} has no text")
    found.setdefault((start, end), extractor)


def _part_spans(statement: Statement):
    for field in _PARTS:
        span = getattr(statement, field)
        if span is not None:
            yield field, span
    for span in statement.values_and_timing:
        yield "values_and_timing", span


def _check_span(span, field: str, text: str) -> tuple[int, int]:
    if isinstance(span, (str, bytes)) or not isinstance(span, tuple) or len(span) != 2:
        raise TypeError(f"{field} must be a (start, end) span")
    start, end = span
    if (
        isinstance(start, bool) or isinstance(end, bool)
        or not isinstance(start, int) or not isinstance(end, int)
    ):
        raise TypeError(f"{field} must be two integers")
    if start < 0 or end > len(text) or end <= start:
        raise ValueError(f"{field} span {(start, end)} is outside the item text")
    return start, end


def _span_is_identifier(span_text: str) -> bool:
    """The values span as a whole, under the R-09 whole-name rule."""
    if _BRACKETED_ID.fullmatch(span_text):
        return True
    return _name_is_identifier(span_text)


def _name_is_identifier(name: str) -> bool:
    if not name or any(ch.isspace() for ch in name):
        return False
    if any(ch in "_/." or ch.isdigit() for ch in name):
        return True
    return any(ch.isupper() for ch in name[1:])


def _token_is_strong_identifier(token: str) -> bool:
    return "_" in token or token.startswith("/") or _CAMEL_HUMP.search(token) is not None


def _identifier_spans(span_text: str, base: int) -> list[tuple[int, int]]:
    """Identifiers inside one span. A longer span wins when two start together.

    A span with whitespace uses the phrase rule. A one-token span uses the
    whole-name rule. Edge punctuation is not part of the identifier.
    """
    phrase = any(ch.isspace() for ch in span_text)
    found: list[tuple[int, int]] = []
    for match in re.finditer(r"\S+", span_text):
        raw = match.group(0)
        rel = 0
        end = len(raw)
        while rel < end and raw[rel] in _EDGE_PUNCT:
            rel += 1
        while end > rel and raw[end - 1] in _EDGE_PUNCT:
            end -= 1
        core = raw[rel:end]
        if not core:
            continue
        if _BRACKETED_ID.fullmatch(core):
            keep = True
        elif phrase:
            keep = _token_is_strong_identifier(core)
        else:
            keep = _name_is_identifier(core)
        if not keep:
            continue
        abs_start = base + match.start() + rel
        found.append((abs_start, abs_start + len(core)))
    found.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    chosen: list[tuple[int, int]] = []
    cursor = -1
    for start, end in found:
        if start < cursor:
            continue
        chosen.append((start, end))
        cursor = end
    return chosen


def _signature(mention: Mention) -> tuple:
    return (
        mention.source_id,
        mention.item_id,
        mention.span_start,
        mention.span_end,
        mention.text,
        mention.sentence,
        mention.type_hint,
        mention.extractor,
    )
