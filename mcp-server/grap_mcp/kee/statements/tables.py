"""Accessors for the statements table (spec-statements.md §3).

The table lives in the extraction store. Saving the graph does not insert,
update, or delete these rows.

A statement stores spans, not the item's text. ``subject`` is a span, or
empty for the kinds the marking data file allows to omit one.
``condition``, ``required_behaviour`` and ``exception`` are a span or empty.
``values_and_timing`` is a list of spans. ``statement_id`` is
``{source_id}|{item_id}|{n}`` with ``n`` counting from 0 in text order of the
unit span.

Rows are not edited. The only write is to replace every row of one item, which
is the re-extraction path. ``kind`` is accepted only when it is one of the
kinds in the automotive schema file.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from grap_mcp.kee.graph_store import GraphStore

from .schema import marking_rules, statement_kinds

_ORIGINS = ("rule", "model")


@dataclass(frozen=True)
class Statement:
    source_id: str
    item_id: str
    unit: tuple[int, int]
    kind: str
    subject: tuple[int, int] | None
    condition: tuple[int, int] | None
    required_behaviour: tuple[int, int] | None
    values_and_timing: tuple[tuple[int, int], ...]
    exception: tuple[int, int] | None
    origin: str
    extractor: str
    statement_id: str | None = None
    n: int | None = None


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _span(value: object, field: str) -> tuple[int, int]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise TypeError(f"{field} must be a (start, end) span")
    start, end = value
    if (
        isinstance(start, bool) or isinstance(end, bool)
        or not isinstance(start, int) or not isinstance(end, int)
    ):
        raise TypeError(f"{field} must be two integers")
    if start < 0 or end <= start:
        raise ValueError(f"{field} must be a non-empty range")
    return start, end


def _required_span(value: object, field: str) -> tuple[int, int]:
    if value is None:
        raise ValueError(f"{field} must be a non-empty span")
    return _span(value, field)


def _optional_span(value: object, field: str) -> tuple[int, int] | None:
    if value is None:
        return None
    return _span(value, field)


def _span_list(value: object, field: str) -> tuple[tuple[int, int], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of spans")
    return tuple(_span(item, field) for item in value)


def _one_of(value: object, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)}")
    return str(value)


def _rows(value: object) -> Sequence:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("statements must be a sequence of rows")
    return value


def _encode_spans(spans: Sequence[tuple[int, int]]) -> str:
    return json.dumps([[start, end] for start, end in spans], separators=(",", ":"))


def _read_subject(start: object, end: object) -> tuple[int, int] | None:
    span = _stored_span(start, end)
    if span is None:
        return None
    return _span(span, "subject")


def _stored_span(start: object, end: object) -> tuple[int, int] | None:
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise ValueError("a stored span is missing one end")
    if isinstance(start, bool) or isinstance(end, bool):
        raise ValueError("a stored span must be two integers")
    return (int(start), int(end))


def _decode_spans(raw: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(raw, str):
        raise ValueError("values_and_timing must be a list of spans")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("values_and_timing must be a list of spans") from exc
    return _span_list(parsed, "values_and_timing")


class StatementTables:
    """Read and replace statement rows on an open extraction store."""

    def __init__(self, store: GraphStore):
        self.conn = store.conn
        self._kinds = statement_kinds()
        self._subject_optional = frozenset(marking_rules()["subject_optional"])
        self._replacing = False

        def replace_ok() -> int:
            return 1 if self._replacing else 0

        self.conn.create_function("grap_statement_replace_ok", 0, replace_ok)

    def replace_item_statements(
        self, source_id: str, item_id: str, statements: Sequence[Statement],
    ) -> list[str]:
        """Replace every statement of one item. This is the re-extraction path.

        ``n`` is assigned from 0 in text order: unit start, then unit end, and
        the order given by the caller when two statements share a unit. The
        returned ids are in that order.
        """
        source_id = _required_text(source_id, "source_id")
        item_id = _required_text(item_id, "item_id")
        prepared = [
            self._prepare(statement, source_id, item_id)
            for statement in _rows(statements)
        ]
        ordered = [
            statement
            for _, statement in sorted(
                enumerate(prepared),
                key=lambda pair: (pair[1].unit[0], pair[1].unit[1], pair[0]),
            )
        ]
        return self._write(source_id, item_id, ordered)

    def statement(self, statement_id: str) -> Statement | None:
        row = self.conn.execute(
            "SELECT * FROM statements WHERE statement_id = ?",
            (_required_text(statement_id, "statement_id"),),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def statements_for_item(self, source_id: str, item_id: str) -> list[Statement]:
        rows = self.conn.execute(
            """
            SELECT * FROM statements
            WHERE source_id = ? AND item_id = ?
            ORDER BY n, statement_id
            """,
            (_required_text(source_id, "source_id"), _required_text(item_id, "item_id")),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def _prepare(
        self, statement: Statement, source_id: str, item_id: str,
    ) -> Statement:
        if not isinstance(statement, Statement):
            raise TypeError("statements must be Statement values")
        source = _required_text(statement.source_id, "source_id")
        item = _required_text(statement.item_id, "item_id")
        if source != source_id or item != item_id:
            raise ValueError(
                "replacement statements must belong to the item being re-extracted"
            )
        kind = statement.kind
        if kind not in self._kinds:
            raise ValueError(
                "kind must be one of the kinds in the schema file: "
                + ", ".join(self._kinds)
            )
        if statement.subject is None:
            if kind not in self._subject_optional:
                raise ValueError("subject must be a non-empty span")
            subject = None
        else:
            subject = _required_span(statement.subject, "subject")
        return Statement(
            source_id=source,
            item_id=item,
            unit=_required_span(statement.unit, "unit"),
            kind=kind,
            subject=subject,
            condition=_optional_span(statement.condition, "condition"),
            required_behaviour=_optional_span(
                statement.required_behaviour, "required_behaviour",
            ),
            values_and_timing=_span_list(statement.values_and_timing, "values_and_timing"),
            exception=_optional_span(statement.exception, "exception"),
            origin=_one_of(statement.origin, _ORIGINS, "origin"),
            extractor=_required_text(statement.extractor, "extractor"),
        )

    def _write(self, source_id: str, item_id: str, statements: Sequence[Statement]) -> list[str]:
        c = self.conn
        try:
            c.execute("BEGIN IMMEDIATE")
            self._replacing = True
            c.execute(
                "DELETE FROM statements WHERE source_id = ? AND item_id = ?",
                (source_id, item_id),
            )
            self._replacing = False
            ids: list[str] = []
            for n, statement in enumerate(statements):
                statement_id = f"{source_id}|{item_id}|{n}"
                c.execute(
                    """
                    INSERT INTO statements (
                        statement_id, source_id, item_id, n,
                        unit_start, unit_end, kind,
                        subject_start, subject_end,
                        condition_start, condition_end,
                        required_behaviour_start, required_behaviour_end,
                        values_and_timing,
                        exception_start, exception_end,
                        origin, extractor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        statement_id, source_id, item_id, n,
                        statement.unit[0], statement.unit[1], statement.kind,
                        *_ends(statement.subject),
                        *_ends(statement.condition),
                        *_ends(statement.required_behaviour),
                        _encode_spans(statement.values_and_timing),
                        *_ends(statement.exception),
                        statement.origin, statement.extractor,
                    ),
                )
                ids.append(statement_id)
            c.commit()
        except Exception:
            self._replacing = False
            c.rollback()
            raise
        return ids

    @staticmethod
    def _from_row(row) -> Statement:
        return Statement(
            source_id=row["source_id"],
            item_id=row["item_id"],
            unit=_span((row["unit_start"], row["unit_end"]), "unit"),
            kind=row["kind"],
            subject=_read_subject(row["subject_start"], row["subject_end"]),
            condition=_stored_span(row["condition_start"], row["condition_end"]),
            required_behaviour=_stored_span(
                row["required_behaviour_start"], row["required_behaviour_end"],
            ),
            values_and_timing=_decode_spans(row["values_and_timing"]),
            exception=_stored_span(row["exception_start"], row["exception_end"]),
            origin=row["origin"],
            extractor=row["extractor"],
            statement_id=row["statement_id"],
            n=row["n"],
        )


def _ends(span: tuple[int, int] | None) -> tuple[int | None, int | None]:
    if span is None:
        return None, None
    return span
