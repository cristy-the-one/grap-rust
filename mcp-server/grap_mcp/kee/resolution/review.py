"""Export and import the review spreadsheet (spec §5).

The sheet has the same columns as Sheet 2 of the labelling guides. The score
sits in front of the empty decision column, which is ``label``. ``mention_id``
and ``entity_key`` are carried on the row so the filled decision is stored
against that pair. Matching on the displayed name would attach it to the wrong
occurrence.

A filled label becomes a ``link_decision`` with decider ``human`` (the spec's
origin ``human``) and method ``review``. Guide labels collapse the way Part B
already fixed for scoring. ``same`` holds generally, so it also stores
``must_link``. ``different`` holds generally, so it stores ``cannot_link``.
``same only here`` is that decision and nothing wider. ``part of``,
``realises`` and ``same, other release`` are not identity constraints.
``unclear`` and a blank cell are not decisions.

Human rows override every other decider, including a rule appended later.
They are never deleted. Importing the same standing rule twice does not store
it twice.
"""

from __future__ import annotations

import csv
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from grap_mcp.kee.resolution.normalise import normalise
from grap_mcp.kee.resolution.tables import ResolutionTables, _entity_key

# Sheet 2, then the score, then the decision column, then the pair's identity.
COLUMNS = (
    "packet_id",
    "source_id",
    "item_id",
    "passage",
    "name",
    "other_source_id",
    "other_item_id",
    "other_passage",
    "other_name",
    "score",
    "label",
    "note",
    "mention_id",
    "entity_key",
)

# Part B. ``same`` and ``same only here`` are the same thing. The other guide
# labels are not. ``unclear`` is excluded.
_SAME = {"same", "same only here"}
_NOT_SAME = {"same, other release", "part of", "realises", "different"}
_LABELS = _SAME | _NOT_SAME | {"unclear"}
# A standing identity, as opposed to a decision that holds only on this row.
_GENERAL = {"same": "must_link", "different": "cannot_link"}
_NA = "n/a"
_KEY_SEP = " | "


class ReviewRefusal(Exception):
    """The sheet was not imported. The message names the count, not the row."""


@dataclass(frozen=True)
class ReviewCandidate:
    """One undecided pair whose entity side is not a stored mention.

    The mention side is read from the store. The entity side is the sentence
    and source the caller already has. Nothing is looked up by name.
    """

    mention_id: int
    entity_key: str
    other_name: str
    other_sentence: str
    other_source_id: str
    score: float | None = None
    other_item_id: str = ""


def decision_in_force(tables: ResolutionTables, mention_id: int, entity_key: str):
    """The decision a re-run must keep.

    A human decision wins over every other decider, whichever was appended
    later. With no human decision, the latest decision that is not a proposal
    wins. A proposal is shadow mode and is not in force.
    """
    rows = tables.decisions_for(mention_id, entity_key)
    humans = [row for row in rows if row.decider == "human" and not row.proposal]
    if humans:
        return humans[-1]
    firm = [row for row in rows if not row.proposal]
    if firm:
        return firm[-1]
    return None


def export_review_list(tables: ResolutionTables, path, candidates: Sequence[ReviewCandidate] | None = None) -> str:
    """Write the spreadsheet. The decision column is empty.

    With no candidates, the rows are the stored proposals that a human has
    not answered. A proposal whose mention has been re-extracted is counted
    and left off: the sentence is gone, and a new one is not invented.
    """
    if candidates is None:
        built, proposals, orphan = _rows_from_proposals(tables)
        proposal_count: int | None = proposals
        orphan_count: int | None = orphan
    else:
        built = _rows_from_candidates(tables, candidates)
        proposal_count = None
        orphan_count = None
    _write_csv(path, built)
    mentions = _count(tables, "SELECT COUNT(*) FROM mentions")
    documents = _documents(built)
    missing_sentence = sum(1 for row in built if row["other_passage"] == "")
    line = _export_line(
        rows=len(built),
        proposals=proposal_count,
        mentions=mentions,
        documents=documents,
        missing_sentence=None if not built else missing_sentence,
        orphan=orphan_count,
    )
    print(line)
    return line


def import_review_list(tables: ResolutionTables, path, decided_at: str) -> str:
    """Append one human decision per filled row. Write nothing if a row is bad.

    ``decided_at`` is the time this import is recorded. It does not choose
    which decision wins.
    """
    if not isinstance(decided_at, str) or decided_at.strip() == "":
        raise ValueError("decided_at must be a non-empty string")
    decided_at = decided_at.strip()
    rows = _read_csv(path)
    documents = _documents(rows)
    problems, actions = _classify(tables, rows)
    if problems:
        detail = " ".join(problems)
        raise ReviewRefusal(f"{detail} n_rows={len(rows)} n_documents={documents}")

    decisions = 0
    constraints = 0
    self_pairs = 0
    for action in actions:
        if action.verdict is None:
            continue
        tables.append_decision(
            action.mention_id,
            action.entity_key,
            action.verdict,
            method="review",
            score=action.score,
            decider="human",
            decided_at=decided_at,
            proposal=False,
        )
        decisions += 1
        if action.kind is None:
            continue
        if action.discovered == action.entity_key:
            self_pairs += 1
            continue
        if _constraint_exists(tables, action.discovered, action.entity_key, action.kind):
            continue
        tables.add_constraint(
            action.discovered,
            action.entity_key,
            action.kind,
            origin="human",
            reason=action.label,
        )
        constraints += 1

    decided = sum(1 for action in actions if action.verdict is not None)
    undecided = sum(1 for action in actions if action.label == "")
    unclear = sum(1 for action in actions if action.label == "unclear")
    line = _import_line(
        rows=len(rows),
        decided=decided,
        undecided=undecided,
        unclear=unclear,
        decisions=decisions,
        constraints=constraints,
        documents=documents,
        self_pairs=self_pairs,
        mentions=_count(tables, "SELECT COUNT(*) FROM mentions"),
    )
    print(line)
    return line


def main(argv: list[str] | None = None) -> int:
    """Export or import one store. The path must already exist."""
    from pathlib import Path

    from grap_mcp.kee.graph_store import GraphStore

    args = list(sys.argv[1:] if argv is None else argv)
    usage = "usage: python -m grap_mcp.kee.resolution.review {export|import} STORE CSV"
    if len(args) == 1 and args[0] in ("-h", "--help"):
        print(usage)
        return 0
    if len(args) != 3 or args[0] not in ("export", "import"):
        print(usage, file=sys.stderr)
        return 2
    command, store_path, csv_path = args
    if not Path(store_path).is_file():
        print(f"no store at {store_path}", file=sys.stderr)
        return 2
    try:
        with GraphStore(store_path) as store:
            tables = ResolutionTables(store)
            if command == "export":
                export_review_list(tables, csv_path)
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                import_review_list(tables, csv_path, decided_at=stamp)
    except ReviewRefusal as refused:
        print(str(refused), file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


@dataclass(frozen=True)
class _Action:
    mention_id: int
    entity_key: str
    label: str
    verdict: str | None
    kind: str | None
    score: float | None
    discovered: str | None


def _rows_from_proposals(tables: ResolutionTables):
    fetched = tables.conn.execute(
        """
        SELECT mention_id, entity_key, score, decision_id
        FROM link_decisions
        WHERE proposal = 1
        ORDER BY decision_id
        """
    ).fetchall()
    latest: dict[tuple[int, str], tuple] = {}
    for row in fetched:
        latest[(int(row["mention_id"]), row["entity_key"])] = row
    built = []
    orphan = 0
    for mention_id, entity_key in sorted(latest):
        if _human_answered(tables, mention_id, entity_key):
            continue
        mention = tables.mention(mention_id)
        if mention is None:
            orphan += 1
            continue
        score = latest[(mention_id, entity_key)]["score"]
        _type, scope, native_id = entity_key.split(_KEY_SEP)
        built.append(_sheet_row(
            len(built) + 1,
            mention,
            mention_id,
            entity_key,
            other_name=native_id,
            other_sentence="",
            other_source_id=scope,
            other_item_id="",
            score=None if score is None else float(score),
        ))
    return built, len(fetched), orphan


def _rows_from_candidates(tables: ResolutionTables, candidates: Sequence[ReviewCandidate]):
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise TypeError("candidates must be a sequence of review rows")
    built = []
    missing = 0
    for candidate in candidates:
        if not isinstance(candidate, ReviewCandidate):
            raise TypeError("candidates must be a sequence of review rows")
        entity_key = _checked_key(candidate.entity_key)
        if entity_key is None:
            raise ReviewRefusal(
                "pairs: 1 entity key that is not type | scope | native_id "
                f"n_rows={len(candidates)} n_documents=n/a"
            )
        mention = tables.mention(candidate.mention_id)
        if mention is None:
            missing += 1
            continue
        other_name = candidate.other_name.strip()
        if other_name == "":
            other_name = entity_key.split(_KEY_SEP)[2]
        built.append(_sheet_row(
            len(built) + 1,
            mention,
            candidate.mention_id,
            entity_key,
            other_name=other_name,
            other_sentence=candidate.other_sentence,
            other_source_id=candidate.other_source_id,
            other_item_id=candidate.other_item_id,
            score=candidate.score,
        ))
    if missing:
        raise ReviewRefusal(
            f"pairs: {missing} unknown mention n_rows={len(candidates)} n_documents=n/a"
        )
    return built


def _sheet_row(number, mention, mention_id, entity_key, other_name, other_sentence,
               other_source_id, other_item_id, score) -> dict[str, str]:
    return {
        "packet_id": f"review-{number:04d}",
        "source_id": mention.source_id,
        "item_id": mention.item_id,
        "passage": mention.sentence,
        "name": mention.text,
        "other_source_id": other_source_id,
        "other_item_id": other_item_id,
        "other_passage": other_sentence,
        "other_name": other_name,
        "score": _score_cell(score),
        "label": "",
        "note": "",
        "mention_id": str(mention_id),
        "entity_key": entity_key,
    }


def _classify(tables: ResolutionTables, rows: list[dict]):
    bad_label = 0
    mismatch = 0
    unknown = 0
    bad_id = 0
    bad_key = 0
    bad_score = 0
    bad_discovered = 0
    actions: list[_Action] = []
    for row in rows:
        label = _norm(row.get("label") or "")
        if label != "" and label not in _LABELS:
            bad_label += 1
            label_ok = False
        else:
            label_ok = True
        mention_id = _mention_cell(row.get("mention_id") or "")
        if mention_id is None:
            bad_id += 1
        entity_key = _checked_key(row.get("entity_key") or "")
        if entity_key is None:
            bad_key += 1
        score, score_ok = _score_cell_in(row.get("score") or "")
        if not score_ok:
            bad_score += 1
        mention = None if mention_id is None else tables.mention(mention_id)
        if mention_id is not None and mention is None:
            unknown += 1
        if mention is not None and not _same_mention(row, mention):
            mismatch += 1
        kind = _GENERAL.get(label) if label_ok else None
        discovered = None
        if kind is not None and mention is not None:
            discovered = _discovered_key(mention)
            if discovered is None:
                bad_discovered += 1
        row_bad = (
            not label_ok
            or mention_id is None
            or entity_key is None
            or not score_ok
            or mention is None
            or not _same_mention(row, mention)
            or (kind is not None and discovered is None)
        )
        if row_bad:
            continue
        verdict = None
        if label in _SAME:
            verdict = "same"
        elif label in _NOT_SAME:
            verdict = "not_same"
        actions.append(_Action(
            mention_id=mention_id,
            entity_key=entity_key,
            label=label,
            verdict=verdict,
            kind=kind,
            score=score,
            discovered=discovered,
        ))
    problems = []
    if bad_label:
        problems.append(f"pairs: {bad_label} label outside the guide list")
    if mismatch:
        problems.append(f"pairs: {mismatch} row whose mention does not match")
    if unknown:
        problems.append(f"pairs: {unknown} unknown mention")
    if bad_id:
        problems.append(f"pairs: {bad_id} row with a mention_id that is not an integer")
    if bad_key:
        problems.append(f"pairs: {bad_key} entity key that is not type | scope | native_id")
    if bad_score:
        problems.append(f"pairs: {bad_score} score that is not a finite number")
    if bad_discovered:
        problems.append(f"pairs: {bad_discovered} mention whose entity key cannot be formed")
    return problems, actions


def _same_mention(row: dict, mention) -> bool:
    return (
        (row.get("source_id") or "") == mention.source_id
        and (row.get("item_id") or "") == mention.item_id
        and (row.get("name") or "") == mention.text
        and (row.get("passage") or "") == mention.sentence
    )


def _human_answered(tables: ResolutionTables, mention_id: int, entity_key: str) -> bool:
    return any(
        row.decider == "human" and not row.proposal
        for row in tables.decisions_for(mention_id, entity_key)
    )


def _discovered_key(mention) -> str | None:
    """``Term | source | normalised name``. Empty or unkeyable names do not invent one."""
    name = normalise(mention.text)
    source = mention.source_id
    if name == "" or _KEY_SEP in name or _KEY_SEP in source:
        return None
    key = f"Term | {source} | {name}"
    if _checked_key(key) is None:
        return None
    return key


def _constraint_exists(tables: ResolutionTables, left: str, right: str, kind: str) -> bool:
    first, second = (left, right) if left < right else (right, left)
    return any(
        row.entity_a == first and row.entity_b == second and row.kind == kind
        for row in tables.constraints()
    )


def _checked_key(value: str) -> str | None:
    try:
        return _entity_key(value)
    except (TypeError, ValueError):
        return None


def _mention_cell(value: str) -> int | None:
    text = value.strip()
    if text == "" or text[0] == "+" or (text.startswith("-") and not text[1:].isdigit()):
        return None
    if not text.lstrip("-").isdigit():
        return None
    return int(text)


def _score_cell(score: float | None) -> str:
    if score is None:
        return ""
    return format(float(score), ".12g")


def _score_cell_in(value: str) -> tuple[float | None, bool]:
    text = value.strip()
    if text == "":
        return None, True
    try:
        number = float(text)
    except ValueError:
        return None, False
    if not math.isfinite(number):
        return None, False
    return number, True


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


def _documents(rows: Sequence[dict]) -> int:
    found = set()
    for row in rows:
        for key in ("source_id", "other_source_id"):
            value = (row.get(key) or "").strip()
            if value:
                found.add(value)
    return len(found)


def _count(tables: ResolutionTables, sql: str) -> int:
    return int(tables.conn.execute(sql).fetchone()[0])


def _write_csv(path, rows: list[dict[str, str]]) -> None:
    from pathlib import Path

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path) -> list[dict]:
    from pathlib import Path

    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in COLUMNS if reader.fieldnames is None or name not in reader.fieldnames]
        if missing:
            raise ReviewRefusal(
                "pairs: header is missing " + ", ".join(missing) + " n_rows=0 n_documents=0"
            )
        return list(reader)


def _cell(value: int | None) -> str:
    return _NA if value is None else str(value)


def _frac(part: int, whole: int) -> str:
    if whole == 0:
        return _NA
    return f"{part}/{whole}"


def _export_line(rows, proposals, mentions, documents, missing_sentence, orphan) -> str:
    if missing_sentence is None:
        missing = _NA
    else:
        missing = f"{missing_sentence}/{rows}"
    return (
        f"n_rows={rows} n_proposals={_cell(proposals)} n_mentions={mentions} "
        f"n_documents={documents} n_without_sentence={missing} n_orphan={_cell(orphan)}"
    )


def _import_line(rows, decided, undecided, unclear, decisions, constraints, documents,
                 self_pairs, mentions) -> str:
    return (
        f"n_rows={rows} n_decided={_frac(decided, rows)} n_undecided={_frac(undecided, rows)} "
        f"n_unclear={_frac(unclear, rows)} n_decisions={decisions} "
        f"n_constraints={constraints} n_documents={documents} "
        f"n_self={self_pairs} n_mentions={mentions}"
    )


if __name__ == "__main__":
    sys.exit(main())
