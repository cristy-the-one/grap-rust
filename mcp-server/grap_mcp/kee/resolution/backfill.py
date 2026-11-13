"""Rebuild mentions from the extraction receipts already stored per chunk.

Spec §6: an existing store is filled without a model call. Each receipt holds
the names that were seen and the quote they were seen in. One mention is one
occurrence. The same character span is stored once, so a relation endpoint that
repeats an outcome inside the same quote adds nothing.

A second run on the same receipts inserts nothing. Opening a store does not
run this; E-11's migration stays a schema change.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterator, Mapping

from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.resolution.tables import Mention, ResolutionTables

_NA = "n/a"


def backfill_mentions(store: GraphStore) -> dict:
    """Insert one mention per name occurrence in the stored receipts.

    The returned counts use ``None`` where there is nothing to measure. The
    same line is printed. ``names`` is the number of distinct occurrences.
    ``name_entries`` counts every outcome name and relation endpoint on the
    receipts. ``repeated`` counts entries that land on a span already kept.
    ``unlocatable`` counts a quoted name that is not in its quote, and a name
    with no quote that does not occur in the source text. No span is invented.
    """
    rows = store.conn.execute(
        "SELECT chunk_id, record FROM extractions ORDER BY chunk_id"
    ).fetchall()
    if not rows:
        report = _empty_report()
        print(format_report(report))
        return report

    tables = ResolutionTables(store)
    documents: set[str] = set()
    name_entries = 0
    repeated = 0
    unlocatable = 0
    occurrences = 0
    added = 0
    replaced_items = 0

    for row in rows:
        chunk_id = row["chunk_id"]
        record = _record(chunk_id, row["record"])
        source_id, item_id = _source_and_item(chunk_id)
        documents.add(source_id)
        source_text = record.get("source_text")
        if not isinstance(source_text, str):
            source_text = ""
        extractor = _extractor(record)
        desired: list[Mention] = []
        taken: set[tuple[int, int]] = set()
        for name, evidence in _names(record):
            name_entries += 1
            span = _locate(source_text, name, evidence) if source_text else None
            if span is None:
                unlocatable += 1
                continue
            if span in taken:
                repeated += 1
                continue
            taken.add(span)
            start, end = span
            desired.append(Mention(
                source_id=source_id,
                item_id=item_id,
                span_start=start,
                span_end=end,
                text=source_text[start:end],
                sentence=_sentence(source_text, start, end),
                type_hint=None,
                extractor=extractor,
            ))
        desired.sort(key=lambda mention: (mention.span_start, mention.span_end, mention.text))
        occurrences += len(desired)
        existing = tables.mentions_for_item(source_id, item_id)
        if _same(existing, desired):
            continue
        if existing:
            tables.replace_item_mentions(source_id, item_id, desired)
            replaced_items += 1
        else:
            tables.add_mentions(desired)
        added += len(desired)

    mentions = store.conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
    report = {
        "mentions": int(mentions),
        "names": occurrences,
        "name_entries": name_entries,
        "repeated": repeated,
        "unlocatable": unlocatable,
        "receipts": len(rows),
        "documents": len(documents),
        "added": added,
        "replaced_items": replaced_items,
    }
    print(format_report(report))
    return report


def format_report(report: Mapping) -> str:
    """One line. A figure with nothing behind it is ``n/a``."""
    return (
        f"mentions={_cell(report['mentions'])} n_names={_cell(report['names'])} "
        f"n_name_entries={_cell(report['name_entries'])} n_repeated={_cell(report['repeated'])} "
        f"n_unlocatable={_cell(report['unlocatable'])} "
        f"n_receipts={report['receipts']} n_documents={report['documents']} "
        f"added={_cell(report['added'])}"
    )


def main(argv: list[str] | None = None) -> int:
    """Backfill one extraction store. The store path must already exist."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(
            "usage: python -m grap_mcp.kee.resolution.backfill GRAPH_STORE_DB",
            file=sys.stderr,
        )
        return 0 if args and args[0] in ("-h", "--help") else 2
    path = args[0]
    if not os.path.isfile(path):
        print(f"no store at {path}", file=sys.stderr)
        return 2
    with GraphStore(path) as store:
        backfill_mentions(store)
    return 0


def _empty_report() -> dict:
    return {
        "mentions": None,
        "names": None,
        "name_entries": None,
        "repeated": None,
        "unlocatable": None,
        "receipts": 0,
        "documents": 0,
        "added": None,
        "replaced_items": 0,
    }


def _cell(value: object) -> str:
    return _NA if value is None else str(value)


def _record(chunk_id: str, raw: str) -> dict:
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"extraction receipt {chunk_id!r} is not JSON") from exc
    if not isinstance(record, dict):
        raise ValueError(f"extraction receipt {chunk_id!r} is not an object")
    return record


def _source_and_item(chunk_id: str) -> tuple[str, str]:
    """Chunk ids are ``{source_id}#{n}``. The chunk is the item."""
    label, sep, index = chunk_id.rpartition("#")
    if sep and label and index.isdigit():
        return label, chunk_id
    return chunk_id, chunk_id


def _extractor(record: Mapping) -> str:
    found = _named_extractor(record)
    if found is not None:
        return found
    nested = record.get("extraction")
    if isinstance(nested, Mapping):
        found = _named_extractor(nested)
        if found is not None:
            return found
    return "receipt"


def _named_extractor(record: Mapping) -> str | None:
    for key in ("provider", "model"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _names(record: Mapping) -> Iterator[tuple[str, object]]:
    """Outcome names first, then relation endpoints, in receipt order."""
    outcomes = record.get("outcomes")
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            name = outcome.get("name")
            if isinstance(name, str) and name.strip():
                yield name.strip(), outcome.get("evidence")
    relations = record.get("relations")
    if isinstance(relations, list):
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            evidence = relation.get("evidence")
            for key in ("from", "to"):
                name = relation.get(key)
                if isinstance(name, str) and name.strip():
                    yield name.strip(), evidence


def _locate(source: str, name: str, evidence: object) -> tuple[int, int] | None:
    """The name's span inside its quote, or in the source when no quote was stored.

    A quote that does not contain the name is unlocatable. The span is not
    taken from a different occurrence.
    """
    span = _quote_span(source, evidence)
    if span is not None:
        start, end = span
        at = _find_in(source[start:end], name)
        if at is None:
            return None
        found = start + at
        return found, found + len(name)
    at = _find_in(source, name)
    if at is None:
        return None
    return at, at + len(name)


def _quote_span(source: str, evidence: object) -> tuple[int, int] | None:
    if isinstance(evidence, str) and evidence.strip():
        quote = evidence.strip()
        at = source.find(quote)
        if at < 0:
            return None
        return at, at + len(quote)
    if not isinstance(evidence, dict):
        return None
    quote = evidence.get("quote")
    start = evidence.get("start")
    end = evidence.get("end")
    if (
        isinstance(quote, str)
        and isinstance(start, int) and not isinstance(start, bool)
        and isinstance(end, int) and not isinstance(end, bool)
        and 0 <= start <= end <= len(source)
        and source[start:end] == quote
    ):
        return start, end
    if isinstance(quote, str) and quote:
        at = source.find(quote)
        if at >= 0:
            return at, at + len(quote)
    return None


def _find_in(haystack: str, name: str) -> int | None:
    match = re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", haystack)
    if match is None:
        return None
    return match.start()


def _sentence(source: str, start: int, end: int) -> str:
    """The sentence that contains the span. ``nr.`` does not end it."""
    left = 0
    i = start
    while i > 0:
        i -= 1
        if source[i] == "\n" or _ends_sentence(source, i):
            left = i + 1
            break
    while left < start and source[left].isspace():
        left += 1
    right = len(source)
    i = end
    while i < len(source):
        if source[i] == "\n":
            right = i
            break
        if _ends_sentence(source, i):
            right = i + 1
            break
        i += 1
    sentence = source[left:right].strip()
    if not sentence:
        return source[start:end]
    return sentence


def _ends_sentence(source: str, i: int) -> bool:
    if source[i] not in ".!?":
        return False
    j = i + 1
    while j < len(source) and source[j] in " \t":
        j += 1
    if j >= len(source) or source[j] == "\n":
        return True
    return source[j].isupper() or source[j] in "\"“«"


def _same(existing: list[Mention], desired: list[Mention]) -> bool:
    return [_signature(mention) for mention in existing] == [_signature(mention) for mention in desired]


def _signature(mention: Mention) -> tuple:
    return (
        mention.span_start,
        mention.span_end,
        mention.text,
        mention.sentence,
        mention.type_hint,
        mention.extractor,
    )


if __name__ == "__main__":
    raise SystemExit(main())
