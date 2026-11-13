"""Accessors for the four entity-resolution tables (spec §3).

The tables live in the extraction store. They are not part of ``GraphState``:
saving the graph does not insert, update, or delete these rows.

A mention is stored as seen. The only rewrite is replacing every mention of
one item when that item is re-extracted. A link decision is appended; the
decision appended later for the same mention and entity is the one that wins.
``decided_at`` is the time the decider recorded, and it does not choose the
winner. A constraint can be removed. A grouping run is inserted whole, and
earlier runs stay.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from grap_mcp.kee.graph_store import GraphStore

_KEY_SEP = " | "
_VERDICTS = ("same", "not_same")
_CONSTRAINT_KINDS = ("must_link", "cannot_link")


@dataclass(frozen=True)
class Mention:
    source_id: str
    item_id: str
    span_start: int
    span_end: int
    text: str
    sentence: str
    extractor: str
    type_hint: str | None = None
    mention_id: int | None = None


@dataclass(frozen=True)
class LinkDecision:
    """One appended decision. ``decider`` is who or what decided.

    The spec's origin ``rule`` and origin ``human`` are stored here. ``decided_at``
    is the time that decider recorded; it does not choose which row wins.
    """

    mention_id: int
    entity_key: str
    verdict: str
    method: str
    score: float | None
    decider: str
    decided_at: str
    proposal: bool = False
    decision_id: int | None = None


@dataclass(frozen=True)
class Constraint:
    entity_a: str
    entity_b: str
    kind: str
    origin: str
    reason: str
    constraint_id: int | None = None


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string or absent")
    return value


def _entity_key(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError("entity key must be a string")
    parts = key.split(_KEY_SEP)
    if len(parts) != 3 or any(part == "" for part in parts):
        raise ValueError(
            "entity key must be 'type | scope | native_id' "
            "(a discovered entity is 'Term | source | normalised name')"
        )
    return key


def _pair(entity_a: object, entity_b: object) -> tuple[str, str]:
    left, right = _entity_key(entity_a), _entity_key(entity_b)
    if left == right:
        raise ValueError("a constraint is between two entities")
    return (left, right) if left < right else (right, left)


def _one_of(value: object, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)}")
    return str(value)


def _score(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("score must be a real number or absent")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("score must be a finite number")
    return number


def _span(start: object, end: object, text: str) -> tuple[int, int]:
    if (
        isinstance(start, bool) or isinstance(end, bool)
        or not isinstance(start, int) or not isinstance(end, int)
    ):
        raise TypeError("character span must be two integers")
    if start < 0 or end <= start:
        raise ValueError("character span must be a non-empty range")
    if end - start != len(text):
        raise ValueError("character span must cover the text as written")
    return start, end


def _mention_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("mention_id must be an int")
    return value


def _prepare_mention(
    mention: Mention, source_id: str | None = None, item_id: str | None = None,
) -> Mention:
    if not isinstance(mention, Mention):
        raise TypeError("mention must be a Mention")
    source = _required_text(mention.source_id, "source_id")
    item = _required_text(mention.item_id, "item_id")
    if source_id is not None and source != source_id:
        raise ValueError("replacement mentions must belong to the item being re-extracted")
    if item_id is not None and item != item_id:
        raise ValueError("replacement mentions must belong to the item being re-extracted")
    text = _required_text(mention.text, "text")
    start, end = _span(mention.span_start, mention.span_end, text)
    return Mention(
        source_id=source,
        item_id=item,
        span_start=start,
        span_end=end,
        text=text,
        sentence=_required_text(mention.sentence, "sentence"),
        extractor=_required_text(mention.extractor, "extractor"),
        type_hint=_optional_text(mention.type_hint, "type_hint"),
    )


def _rows(value: object, field: str) -> Sequence:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of rows")
    return value


class ResolutionTables:
    """Read and write the four §3 tables on an open extraction store."""

    def __init__(self, store: GraphStore):
        self.conn = store.conn
        # Session flag read by the mentions DELETE trigger. Item replacement
        # sets it; every other delete sees 0 and is refused.
        self._replacing_mentions = False

        def mention_replace_ok() -> int:
            return 1 if self._replacing_mentions else 0

        self.conn.create_function("grap_mention_replace_ok", 0, mention_replace_ok)

    def add_mentions(self, mentions: Sequence[Mention]) -> list[int]:
        """Insert occurrences. An existing occurrence is not overwritten."""
        prepared = [_prepare_mention(mention) for mention in _rows(mentions, "mentions")]
        return self._write_mentions(prepared, delete_item=None)

    def replace_item_mentions(
        self, source_id: str, item_id: str, mentions: Sequence[Mention],
    ) -> list[int]:
        """Replace every mention of one item. This is the re-extraction path."""
        source_id = _required_text(source_id, "source_id")
        item_id = _required_text(item_id, "item_id")
        prepared = [
            _prepare_mention(mention, source_id, item_id)
            for mention in _rows(mentions, "mentions")
        ]
        return self._write_mentions(prepared, delete_item=(source_id, item_id))

    def mention(self, mention_id: int) -> Mention | None:
        row = self.conn.execute(
            "SELECT * FROM mentions WHERE mention_id = ?", (_mention_id(mention_id),),
        ).fetchone()
        if row is None:
            return None
        return Mention(
            source_id=row["source_id"],
            item_id=row["item_id"],
            span_start=row["span_start"],
            span_end=row["span_end"],
            text=row["text"],
            sentence=row["sentence"],
            extractor=row["extractor"],
            type_hint=row["type_hint"],
            mention_id=row["mention_id"],
        )

    def mentions_for_item(self, source_id: str, item_id: str) -> list[Mention]:
        rows = self.conn.execute(
            """
            SELECT * FROM mentions
            WHERE source_id = ? AND item_id = ?
            ORDER BY span_start, span_end, mention_id
            """,
            (_required_text(source_id, "source_id"), _required_text(item_id, "item_id")),
        ).fetchall()
        return [
            Mention(
                source_id=row["source_id"],
                item_id=row["item_id"],
                span_start=row["span_start"],
                span_end=row["span_end"],
                text=row["text"],
                sentence=row["sentence"],
                extractor=row["extractor"],
                type_hint=row["type_hint"],
                mention_id=row["mention_id"],
            )
            for row in rows
        ]

    def append_decision(
        self,
        mention_id: int,
        entity_key: str,
        verdict: str,
        method: str,
        score: float | None,
        decider: str,
        decided_at: str,
        proposal: bool = False,
    ) -> LinkDecision:
        """Append one decision. Nothing already stored is changed."""
        mention_id = _mention_id(mention_id)
        entity_key = _entity_key(entity_key)
        verdict = _one_of(verdict, _VERDICTS, "verdict")
        method = _required_text(method, "method")
        number = _score(score)
        decider = _required_text(decider, "decider")
        decided_at = _required_text(decided_at, "decided_at")
        if not isinstance(proposal, bool):
            raise TypeError("proposal must be a bool")
        if self.mention(mention_id) is None:
            raise ValueError(f"no mention {mention_id}")
        cursor = self._change(
            """
            INSERT INTO link_decisions (
                mention_id, entity_key, verdict, method, score, decider, decided_at, proposal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mention_id, entity_key, verdict, method, number, decider, decided_at,
                int(proposal),
            ),
        )
        return self._decision(int(cursor.lastrowid))

    def decision_for(self, mention_id: int, entity_key: str) -> LinkDecision | None:
        """The decision appended later for this mention and entity."""
        row = self.conn.execute(
            """
            SELECT * FROM link_decisions
            WHERE mention_id = ? AND entity_key = ?
            ORDER BY decision_id DESC
            LIMIT 1
            """,
            (_mention_id(mention_id), _entity_key(entity_key)),
        ).fetchone()
        return None if row is None else self._decision_from_row(row)

    def decisions_for(self, mention_id: int, entity_key: str) -> list[LinkDecision]:
        """Every decision for the pair, in the order they were appended."""
        rows = self.conn.execute(
            """
            SELECT * FROM link_decisions
            WHERE mention_id = ? AND entity_key = ?
            ORDER BY decision_id ASC
            """,
            (_mention_id(mention_id), _entity_key(entity_key)),
        ).fetchall()
        return [self._decision_from_row(row) for row in rows]

    def add_constraint(
        self, entity_a: str, entity_b: str, kind: str, origin: str, reason: str,
    ) -> Constraint:
        left, right = _pair(entity_a, entity_b)
        kind = _one_of(kind, _CONSTRAINT_KINDS, "kind")
        origin = _required_text(origin, "origin")
        reason = _required_text(reason, "reason")
        cursor = self._change(
            """
            INSERT INTO constraints (entity_a, entity_b, kind, origin, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (left, right, kind, origin, reason),
        )
        return self._constraint(int(cursor.lastrowid))

    def remove_constraint(self, constraint_id: int) -> None:
        if isinstance(constraint_id, bool) or not isinstance(constraint_id, int):
            raise TypeError("constraint_id must be an int")
        cursor = self._change(
            "DELETE FROM constraints WHERE constraint_id = ?", (constraint_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"no constraint {constraint_id}")

    def constraints(self) -> list[Constraint]:
        rows = self.conn.execute(
            "SELECT * FROM constraints ORDER BY constraint_id"
        ).fetchall()
        return [self._constraint_from_row(row) for row in rows]

    def record_grouping(self, run_id: str, links: Sequence[tuple[int, str]]) -> None:
        """Insert one run. An empty run is refused: with no rows it would not
        become the latest run, and earlier runs would stay in force.
        """
        run_id = _required_text(run_id, "run_id")
        rows = _rows(links, "links")
        if not rows:
            raise ValueError(
                "a grouping run with no mentions is not stored; "
                "it would not supersede the previous run"
            )
        prepared: list[tuple[str, int, str]] = []
        seen: set[int] = set()
        for mention_id, entity_key in rows:
            mention_id = _mention_id(mention_id)
            entity_key = _entity_key(entity_key)
            if mention_id in seen:
                raise ValueError(f"grouping run {run_id!r} repeats mention {mention_id}")
            seen.add(mention_id)
            if self.mention(mention_id) is None:
                raise ValueError(f"no mention {mention_id}")
            prepared.append((run_id, mention_id, entity_key))
        existing = self.conn.execute(
            "SELECT 1 FROM groupings WHERE run_id = ? LIMIT 1", (run_id,),
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"grouping run {run_id!r} is already stored; a recompute appends a new run"
            )
        self._change_many(
            "INSERT INTO groupings (run_id, mention_id, entity_key) VALUES (?, ?, ?)",
            prepared,
        )

    def grouping(self, run_id: str) -> dict[int, str]:
        run_id = _required_text(run_id, "run_id")
        rows = self.conn.execute(
            """
            SELECT mention_id, entity_key FROM groupings
            WHERE run_id = ?
            ORDER BY mention_id
            """,
            (run_id,),
        ).fetchall()
        if not rows:
            raise ValueError(f"no grouping run {run_id!r}")
        return {int(row["mention_id"]): row["entity_key"] for row in rows}

    def latest_grouping(self) -> tuple[str, dict[int, str]] | None:
        """The run appended last, and its mention-to-entity map."""
        row = self.conn.execute(
            "SELECT run_id FROM groupings ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return row["run_id"], self.grouping(row["run_id"])

    def _write_mentions(
        self, mentions: Sequence[Mention], delete_item: tuple[str, str] | None,
    ) -> list[int]:
        c = self.conn
        try:
            c.execute("BEGIN IMMEDIATE")
            if delete_item is not None:
                self._replacing_mentions = True
                c.execute(
                    "DELETE FROM mentions WHERE source_id = ? AND item_id = ?",
                    delete_item,
                )
                self._replacing_mentions = False
            ids: list[int] = []
            for mention in mentions:
                cursor = c.execute(
                    """
                    INSERT INTO mentions (
                        source_id, item_id, span_start, span_end,
                        text, sentence, type_hint, extractor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mention.source_id, mention.item_id, mention.span_start, mention.span_end,
                        mention.text, mention.sentence, mention.type_hint, mention.extractor,
                    ),
                )
                ids.append(int(cursor.lastrowid))
            c.commit()
        except Exception:
            self._replacing_mentions = False
            c.rollback()
            raise
        return ids

    def _change(self, sql: str, params: tuple):
        c = self.conn
        try:
            c.execute("BEGIN IMMEDIATE")
            cursor = c.execute(sql, params)
            c.commit()
        except Exception:
            c.rollback()
            raise
        return cursor

    def _change_many(self, sql: str, params: Sequence[tuple]) -> None:
        c = self.conn
        try:
            c.execute("BEGIN IMMEDIATE")
            c.executemany(sql, params)
            c.commit()
        except Exception:
            c.rollback()
            raise

    def _decision(self, decision_id: int) -> LinkDecision:
        row = self.conn.execute(
            "SELECT * FROM link_decisions WHERE decision_id = ?", (decision_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no link decision {decision_id}")
        return self._decision_from_row(row)

    @staticmethod
    def _decision_from_row(row) -> LinkDecision:
        score = row["score"]
        return LinkDecision(
            mention_id=row["mention_id"],
            entity_key=row["entity_key"],
            verdict=row["verdict"],
            method=row["method"],
            score=None if score is None else float(score),
            decider=row["decider"],
            decided_at=row["decided_at"],
            proposal=bool(row["proposal"]),
            decision_id=row["decision_id"],
        )

    def _constraint(self, constraint_id: int) -> Constraint:
        row = self.conn.execute(
            "SELECT * FROM constraints WHERE constraint_id = ?", (constraint_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no constraint {constraint_id}")
        return self._constraint_from_row(row)

    @staticmethod
    def _constraint_from_row(row) -> Constraint:
        return Constraint(
            entity_a=row["entity_a"],
            entity_b=row["entity_b"],
            kind=row["kind"],
            origin=row["origin"],
            reason=row["reason"],
            constraint_id=row["constraint_id"],
        )
