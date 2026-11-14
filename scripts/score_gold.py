#!/usr/bin/env python3
"""Score a gold folder against a system-output JSON. Prints aggregates only.

Default reads ``open/`` plus a flat folder of CSVs. ``--sealed`` reads ``sealed/``
only. The two are never combined. Sheets are UTF-8 with a header.
Statement rows match on source_id, item_id, kind, and subject (B3). A gold
statement whose kind is ``unclear`` or ``broken`` is excluded from the
statement metrics and counted (``statements_unclear``, ``statements_broken``).
A prediction whose ``source_id`` and ``item_id`` equal one of those rows is
dropped before matching and counted (``statements_pred_on_excluded``).
Pair labels collapse to same / not-same, and ``unclear`` rows are excluded
from the pair metric. Question hits require every ``where`` entry, except
unanswerable questions, which hit only when the system declines.

A metric with no gold rows prints ``n/a``. Precision with no predictions prints
``n/a``. Each figure is printed with gold rows, predicted rows, and documents.

Sheet 4 (``testpoints.csv``) is read under those same rules. ``needs_from_elsewhere``
is scored against the IDs the system fetched for that requirement, after
``normalise()``. Recall is found ÷ needed and precision is found ÷ fetched,
both micro. A gold cell of ``none`` adds nothing to recall and adds to
precision only when the system fetched something. A blank cell is refused.
``testable_as_written`` is exact agreement on yes / partly / no. When the
system output has no ``testable`` field, that figure is ``n/a``.

System JSON::

    {
      "statements": [{"source_id", "item_id", "kind", "subject"}],
      "pairs": [{"packet_id", "label"}],
      "questions": [{"packet_id", "cited": ["..."], "declined": false}],
      "testpoints": [{"source_id", "item_id", "needs": ["..."], "testable": "yes"}]
    }

Every figure includes a 95% bootstrap interval, resampling source documents
with replacement (2,000 resamples, seed 20261112). No row is printed.
``--sealed`` is the only way sealed sheets are read. It appends one line to
the gold-root access log before printing: timestamp, git commit, user, and
that same aggregate summary. If the log cannot be written, nothing is scored
and nothing is printed. The line never contains a row.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import math
import random
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_MCP = Path(__file__).resolve().parents[1] / "mcp-server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from grap_mcp.kee.resolution.normalise import normalise  # noqa: E402

BOOTSTRAP_SEED = 20261112
BOOTSTRAP_RESAMPLES = 2000

_SAME_LABELS = {"same", "same only here"}
_PAIR_LABELS = _SAME_LABELS | {
    "same, other release",
    "part of",
    "realises",
    "different",
    "unclear",
}
_NA = "n/a"
_TESTABLE = {"yes", "partly", "no"}


class ScoreRefusal(Exception):
    """Gold rows that cannot be scored. The message names the sheet and the count."""


def _norm(value) -> str:
    return " ".join(str(value).lower().split())


def _subjects_agree(left, right) -> bool:
    a = _norm(left)
    b = _norm(right)
    if a == b:
        return True
    # Integer form of token F1 >= 0.8, so 0.80 hits and 0.79 misses.
    a_tokens = a.split()
    b_tokens = b.split()
    if not a_tokens or not b_tokens:
        return False
    counts_a: dict[str, int] = {}
    counts_b: dict[str, int] = {}
    for token in a_tokens:
        counts_a[token] = counts_a.get(token, 0) + 1
    for token in b_tokens:
        counts_b[token] = counts_b.get(token, 0) + 1
    overlap = sum(min(counts_a[token], counts_b.get(token, 0)) for token in counts_a)
    return 5 * overlap >= 2 * (len(a_tokens) + len(b_tokens))


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = 1.0 if tp + fp == 0 else tp / (tp + fp)
    recall = 1.0 if tp + fn == 0 else tp / (tp + fn)
    denom = 2 * tp + fp + fn
    f1 = 1.0 if denom == 0 else (2 * tp) / denom
    return precision, recall, f1


def _percentile(samples: list[float], p: float) -> float:
    """Hyndman-Fan type 7 percentile of an already collected sample."""
    ordered = sorted(samples)
    count = len(ordered)
    if count == 0:
        return 0.0
    if count == 1:
        return float(ordered[0])
    rank = (count - 1) * p
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(ordered[lo])
    weight = rank - lo
    return float(ordered[lo]) * (1.0 - weight) + float(ordered[hi]) * weight


def _expand(drawn: list[str], grouped: dict[str, list]) -> list:
    rows = []
    for doc in drawn:
        rows.extend(grouped[doc])
    return rows


def _bootstrap(doc_ids, evaluate) -> list[tuple[float, float]] | None:
    docs = sorted(set(doc_ids))
    if not docs:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    count = len(docs)
    buckets: list[list[float]] | None = None
    for _ in range(BOOTSTRAP_RESAMPLES):
        drawn = [docs[rng.randrange(count)] for _ in range(count)]
        values = tuple(float(value) for value in evaluate(drawn))
        if buckets is None:
            buckets = [[] for _ in values]
        for bucket, value in zip(buckets, values):
            bucket.append(value)
    assert buckets is not None
    return [(_percentile(bucket, 0.025), _percentile(bucket, 0.975)) for bucket in buckets]


def _bounds(point: float, interval: tuple[float, float] | None) -> tuple[float, float]:
    if interval is None:
        return point, point
    return interval


def _sheet_paths(gold_dir: Path, name: str, sealed: bool) -> list[Path]:
    """Default is open sheets plus a flat folder. ``sealed`` reads sealed sheets only."""
    if sealed:
        candidate = gold_dir / "sealed" / name
        return [candidate] if candidate.is_file() else []
    paths = []
    direct = gold_dir / name
    if direct.is_file():
        paths.append(direct)
    opened = gold_dir / "open" / name
    if opened.is_file():
        paths.append(opened)
    return paths


def _read_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append({key: value or "" for key, value in row.items() if key is not None})
    return rows


def _match_statements(gold: list[dict], predicted: list[dict]) -> tuple[int, int, int]:
    used = set()
    tp = 0
    for row in gold:
        for index, pred in enumerate(predicted):
            if index in used:
                continue
            if (row["source_id"] == pred["source_id"]
                    and row["item_id"] == pred["item_id"]
                    and _norm(row["kind"]) == _norm(pred["kind"])
                    and _subjects_agree(row["subject"], pred["subject"])):
                used.add(index)
                tp += 1
                break
    return tp, len(predicted) - tp, len(gold) - tp


def _score_statements(rows: list[dict], predicted: list[dict]) -> dict:
    # ``unclear`` and ``broken`` are not misses. Drop them before matching,
    # and count them, the same way an unclear pair is left out of pair F1.
    # A prediction on one of those rows is not a false positive. Drop it
    # before matching too, and count it on its own.
    unclear = 0
    broken = 0
    kept = []
    excluded = set()
    for row in rows:
        kind = _norm(row.get("kind") or "")
        if kind == "unclear":
            unclear += 1
        elif kind == "broken":
            broken += 1
        else:
            kept.append(row)
            continue
        excluded.add((str(row.get("source_id") or ""), str(row.get("item_id") or "")))
    gold = [{
        "source_id": row.get("source_id") or "",
        "item_id": str(row.get("item_id") or ""),
        "kind": row.get("kind") or "",
        "subject": row.get("subject") or "",
    } for row in kept]
    preds = [{
        "source_id": str(row.get("source_id") or ""),
        "item_id": str(row.get("item_id") or ""),
        "kind": row.get("kind") or "",
        "subject": row.get("subject") or "",
    } for row in predicted]
    pred_on_excluded = 0
    judged = []
    for row in preds:
        if (row["source_id"], row["item_id"]) in excluded:
            pred_on_excluded += 1
            continue
        judged.append(row)
    preds = judged
    gold_by = defaultdict(list)
    pred_by = defaultdict(list)
    for row in gold:
        gold_by[row["source_id"]].append(row)
    # A system normally emits claims for every document. Claims about a
    # document that is not in the sheet being scored (a sealed document during
    # an open run, or the reverse) cannot be judged, so they are left out
    # rather than counted as false positives.
    preds = [row for row in preds if row["source_id"] in gold_by]
    precision, recall, f1 = _prf(*_match_statements(gold, preds))
    for row in preds:
        pred_by[row["source_id"]].append(row)
    n_gold = len(gold)
    n_pred = len(preds)
    n_docs = len(set(gold_by) | set(pred_by))

    def evaluate(drawn):
        return _prf(*_match_statements(_expand(drawn, gold_by), _expand(drawn, pred_by)))

    intervals = _bootstrap(set(gold_by) | set(pred_by), evaluate)
    precision_low, precision_high = _bounds(precision, None if intervals is None else intervals[0])
    recall_low, recall_high = _bounds(recall, None if intervals is None else intervals[1])
    f1_low, f1_high = _bounds(f1, None if intervals is None else intervals[2])
    if n_gold == 0:
        precision = recall = f1 = _NA
        precision_low = precision_high = recall_low = recall_high = f1_low = f1_high = _NA
    elif n_pred == 0:
        precision = precision_low = precision_high = _NA
    return {
        "statements_precision": precision,
        "statements_precision_low": precision_low,
        "statements_precision_high": precision_high,
        "statements_recall": recall,
        "statements_recall_low": recall_low,
        "statements_recall_high": recall_high,
        "statements_f1": f1,
        "statements_f1_low": f1_low,
        "statements_f1_high": f1_high,
        "statements_n_gold": n_gold,
        "statements_n_pred": n_pred,
        "statements_n_docs": n_docs,
        "statements_unclear": unclear,
        "statements_broken": broken,
        "statements_pred_on_excluded": pred_on_excluded,
    }


def _pair_positive(label) -> bool | None:
    text = _norm(label)
    if text == "unclear":
        return None
    return text in _SAME_LABELS


def _pair_counts(rows: list[dict], labels: dict[str, str]) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for row in rows:
        gold_pos = _pair_positive(row["label"])
        system_pos = _pair_positive(labels.get(row["packet_id"], ""))
        if system_pos is None:
            system_pos = False
        if gold_pos and system_pos:
            tp += 1
        elif system_pos:
            fp += 1
        elif gold_pos:
            fn += 1
    return tp, fp, fn


def _score_pairs(rows: list[dict], system_pairs: list[dict]) -> dict:
    labels = {str(row.get("packet_id")): row.get("label") or "" for row in system_pairs}
    scored = []
    scored_by = defaultdict(list)
    unclear = 0
    for row in rows:
        if _pair_positive(row.get("label") or "") is None:
            unclear += 1
            continue
        item = {
            "packet_id": str(row.get("packet_id") or ""),
            "source_id": row.get("source_id") or "",
            "label": row.get("label") or "",
        }
        scored.append(item)
        scored_by[item["source_id"]].append(item)
    tp, fp, fn = _pair_counts(scored, labels)
    n_gold = len(rows)
    n_pred = len(system_pairs)
    n_docs = len(scored_by)
    if not scored or (tp == 0 and fp == 0 and fn == 0):
        f1 = low = high = _NA
    else:
        f1 = _prf(tp, fp, fn)[2]

        def evaluate(drawn):
            return (_prf(*_pair_counts(_expand(drawn, scored_by), labels))[2],)

        intervals = _bootstrap(scored_by, evaluate)
        low, high = _bounds(f1, None if intervals is None else intervals[0])
    return {
        "pairs_f1": f1,
        "pairs_f1_low": low,
        "pairs_f1_high": high,
        "pairs_unclear": unclear,
        "pairs_n_gold": n_gold,
        "pairs_n_pred": n_pred,
        "pairs_n_docs": n_docs,
    }


def _where_entries(text) -> list[str]:
    entries = []
    for chunk in str(text).replace("\n", ";").split(";"):
        piece = chunk.strip()
        if piece:
            entries.append(piece)
    return entries


def _unanswerable(row: dict) -> bool:
    return (_norm(row.get("type", "")) == "not answerable"
            or _norm(row.get("answer", "")) == "not in the documents")


def _question_hit(row: dict, system: dict) -> bool:
    if _unanswerable(row):
        return bool(system.get("declined", False))
    cited = {str(item).strip() for item in system.get("cited") or []}
    return all(entry in cited for entry in _where_entries(row.get("where", "")))


def _question_rate(rows: list[dict], by_id: dict[str, dict]) -> float:
    if not rows:
        return 1.0
    hits = sum(1 for row in rows if _question_hit(row, by_id.get(row["packet_id"], {})))
    return hits / len(rows)


def _score_questions(rows: list[dict], system_questions: list[dict]) -> dict:
    by_id = {str(row.get("packet_id")): row for row in system_questions}
    gold = []
    grouped = defaultdict(list)
    for row in rows:
        item = {
            "packet_id": str(row.get("packet_id") or ""),
            "source_id": row.get("source_id") or "",
            "type": row.get("type") or "",
            "answer": row.get("answer") or "",
            "where": row.get("where") or "",
        }
        gold.append(item)
        grouped[item["source_id"]].append(item)
    n_gold = len(gold)
    n_pred = len(system_questions)
    n_docs = len(grouped)
    if n_gold == 0:
        rate = low = high = _NA
    else:
        rate = _question_rate(gold, by_id)

        def evaluate(drawn):
            return (_question_rate(_expand(drawn, grouped), by_id),)

        intervals = _bootstrap(grouped, evaluate)
        low, high = _bounds(rate, None if intervals is None else intervals[0])
    return {
        "questions_hit_rate": rate,
        "questions_hit_rate_low": low,
        "questions_hit_rate_high": high,
        "questions_n_gold": n_gold,
        "questions_n_pred": n_pred,
        "questions_n_docs": n_docs,
    }


def _split_ids(text) -> list[str]:
    """Identifiers separated by ``;`` or a newline. Empty pieces are dropped."""
    parts = []
    normalised = str(text).replace("\r\n", "\n").replace("\r", "\n")
    for chunk in normalised.replace("\n", ";").split(";"):
        piece = chunk.strip()
        if piece:
            parts.append(piece)
    return parts


def _is_none_needs(text) -> bool:
    """The literal ``none``: this requirement needs nothing else."""
    return _norm(text) == "none"


def _needs_unlabelled(text) -> bool:
    """A blank cell is unlabelled. ``none`` is a label, not a blank."""
    if _is_none_needs(text):
        return False
    return not _split_ids(text)


def _needed_ids(text) -> set[str]:
    if _is_none_needs(text):
        return set()
    return {normalise(part) for part in _split_ids(text)}


def _fetched_ids(needs) -> set[str]:
    """System IDs for one requirement. A missing list fetches nothing."""
    if needs is None:
        return set()
    if isinstance(needs, str):
        parts = _split_ids(needs)
    else:
        parts = [str(item).strip() for item in needs if str(item).strip()]
    return {normalise(part) for part in parts}


def _requirement_key(row: dict) -> tuple[str, str]:
    return (str(row.get("source_id") or ""), str(row.get("item_id") or ""))


def _testable_label(value) -> str | None:
    if value is None:
        return None
    text = _norm(value)
    if text in _TESTABLE:
        return text
    return None


def _micro_needs(rows: list[dict]) -> tuple[float, float]:
    """Micro recall and precision. A side with nothing to divide by is 1.0.

    That 1.0 is only for a bootstrap draw. The printed figure is ``n/a`` when
    the whole sheet has nothing on that side.
    """
    found = needed = fetched = 0
    for row in rows:
        found += len(row["needed"] & row["fetched"])
        needed += len(row["needed"])
        fetched += len(row["fetched"])
    recall = 1.0 if needed == 0 else found / needed
    precision = 1.0 if fetched == 0 else found / fetched
    return recall, precision


def _score_testpoints(rows: list[dict], system_rows: list[dict]) -> dict:
    """Sheet 4. Needs are micro recall and precision; testable is agreement.

    A requirement is judged only against the system row with the same
    source_id and item_id. Rows for any other requirement are left out.
    """
    by_key: dict[tuple[str, str], dict] = {}
    for row in system_rows:
        by_key[_requirement_key(row)] = row

    scored = []
    grouped = defaultdict(list)
    for row in rows:
        key = _requirement_key(row)
        system = by_key.get(key, {})
        item = {
            "source_id": key[0],
            "needed": _needed_ids(row.get("needs_from_elsewhere") or ""),
            "fetched": _fetched_ids(system.get("needs")),
        }
        scored.append(item)
        grouped[item["source_id"]].append(item)

    found, needed, fetched = 0, 0, 0
    for item in scored:
        found += len(item["needed"] & item["fetched"])
        needed += len(item["needed"])
        fetched += len(item["fetched"])
    n_requirements = len(scored)
    n_docs = len(grouped)
    if n_requirements == 0 or needed == 0:
        recall = recall_low = recall_high = _NA
    else:
        recall = found / needed
    if n_requirements == 0 or fetched == 0:
        precision = precision_low = precision_high = _NA
    else:
        precision = found / fetched
    if recall != _NA or precision != _NA:
        def evaluate(drawn):
            values = _micro_needs(_expand(drawn, grouped))
            if recall == _NA:
                return (values[1],)
            if precision == _NA:
                return (values[0],)
            return values

        intervals = _bootstrap(grouped, evaluate)
        if recall != _NA and precision != _NA:
            recall_low, recall_high = _bounds(recall, None if intervals is None else intervals[0])
            precision_low, precision_high = _bounds(precision, None if intervals is None else intervals[1])
        elif recall != _NA:
            recall_low, recall_high = _bounds(recall, None if intervals is None else intervals[0])
        else:
            precision_low, precision_high = _bounds(precision, None if intervals is None else intervals[0])

    labelled = []
    labelled_by = defaultdict(list)
    n_pred = 0
    matches = 0
    for row in rows:
        label = _testable_label(row.get("testable_as_written"))
        if label is None:
            continue
        system = by_key.get(_requirement_key(row))
        present = system is not None and "testable" in system
        predicted = _testable_label(system.get("testable")) if present else None
        hit = predicted == label
        if present:
            n_pred += 1
        if hit:
            matches += 1
        item = {"source_id": str(row.get("source_id") or ""), "hit": hit}
        labelled.append(item)
        labelled_by[item["source_id"]].append(item)
    n_gold = len(labelled)
    testable_docs = len(labelled_by)
    if n_gold == 0 or n_pred == 0:
        agreement = agreement_low = agreement_high = _NA
    else:
        agreement = matches / n_gold

        def evaluate_agreement(drawn):
            taken = _expand(drawn, labelled_by)
            if not taken:
                return (1.0,)
            return (sum(1 for item in taken if item["hit"]) / len(taken),)

        intervals = _bootstrap(labelled_by, evaluate_agreement)
        agreement_low, agreement_high = _bounds(agreement, None if intervals is None else intervals[0])
    return {
        "testpoints_needs_recall": recall,
        "testpoints_needs_recall_low": recall_low,
        "testpoints_needs_recall_high": recall_high,
        "testpoints_needs_precision": precision,
        "testpoints_needs_precision_low": precision_low,
        "testpoints_needs_precision_high": precision_high,
        "testpoints_n_requirements": n_requirements,
        "testpoints_n_needed": needed,
        "testpoints_n_fetched": fetched,
        "testpoints_n_docs": n_docs,
        "testpoints_testable_agreement": agreement,
        "testpoints_testable_agreement_low": agreement_low,
        "testpoints_testable_agreement_high": agreement_high,
        "testpoints_testable_n_gold": n_gold,
        "testpoints_testable_n_pred": n_pred,
        "testpoints_testable_n_docs": testable_docs,
    }


def _refuse_unscorable(statements: list[dict], pairs: list[dict], questions: list[dict],
                       testpoints: list[dict]) -> None:
    blank_kind = sum(1 for row in statements if not str(row.get("kind") or "").strip())
    bad_labels = sum(1 for row in pairs if _norm(row.get("label") or "") not in _PAIR_LABELS)
    empty_where = sum(
        1 for row in questions
        if not _unanswerable(row) and not _where_entries(row.get("where") or "")
    )
    blank_needs = sum(
        1 for row in testpoints if _needs_unlabelled(row.get("needs_from_elsewhere") or "")
    )
    problems = []
    if blank_kind:
        problems.append(f"statements: {blank_kind} blank kind")
    if bad_labels:
        problems.append(f"pairs: {bad_labels} label outside the guide list")
    if empty_where:
        problems.append(f"questions: {empty_where} answerable with empty where")
    if blank_needs:
        problems.append(f"testpoints: {blank_needs} blank needs_from_elsewhere")
    if problems:
        raise ScoreRefusal("; ".join(problems))


def score(gold_dir, system: dict, sealed: bool = False) -> dict:
    """Return statement, pair, question, and test-point aggregates for one gold folder.

    ``sealed=False`` reads open sheets and a flat folder. ``sealed=True`` reads
    sealed sheets only. The two are never combined.
    """
    folder = Path(gold_dir)
    system = system or {}
    statements = _read_rows(_sheet_paths(folder, "statements.csv", sealed))
    pairs = _read_rows(_sheet_paths(folder, "pairs.csv", sealed))
    questions = _read_rows(_sheet_paths(folder, "questions.csv", sealed))
    testpoints = _read_rows(_sheet_paths(folder, "testpoints.csv", sealed))
    _refuse_unscorable(statements, pairs, questions, testpoints)
    result = {}
    result.update(_score_statements(statements, list(system.get("statements") or [])))
    result.update(_score_pairs(pairs, list(system.get("pairs") or [])))
    result.update(_score_questions(questions, list(system.get("questions") or [])))
    result.update(_score_testpoints(testpoints, list(system.get("testpoints") or [])))
    return result


def _fmt(value) -> str:
    if value == _NA:
        return _NA
    return f"{float(value):.10f}"


def format_report(result: dict) -> str:
    lines = []
    for key, group in (
        ("statements_precision", "statements"),
        ("statements_recall", "statements"),
        ("statements_f1", "statements"),
        ("pairs_f1", "pairs"),
        ("questions_hit_rate", "questions"),
    ):
        lines.append(
            f"{key}={_fmt(result[key])} [{_fmt(result[key + '_low'])}, {_fmt(result[key + '_high'])}] "
            f"n_gold={result[group + '_n_gold']} n_pred={result[group + '_n_pred']} "
            f"n_docs={result[group + '_n_docs']}"
        )
    lines.append(f"pairs_unclear={result['pairs_unclear']}")
    lines.append(f"statements_unclear={result['statements_unclear']}")
    lines.append(f"statements_broken={result['statements_broken']}")
    lines.append(f"statements_pred_on_excluded={result['statements_pred_on_excluded']}")
    needs_counts = (
        f"n_requirements={result['testpoints_n_requirements']} "
        f"n_needed={result['testpoints_n_needed']} "
        f"n_fetched={result['testpoints_n_fetched']} "
        f"n_docs={result['testpoints_n_docs']}"
    )
    for key in ("testpoints_needs_recall", "testpoints_needs_precision"):
        lines.append(
            f"{key}={_fmt(result[key])} [{_fmt(result[key + '_low'])}, {_fmt(result[key + '_high'])}] "
            f"{needs_counts}"
        )
    lines.append(
        f"testpoints_testable_agreement={_fmt(result['testpoints_testable_agreement'])} "
        f"[{_fmt(result['testpoints_testable_agreement_low'])}, "
        f"{_fmt(result['testpoints_testable_agreement_high'])}] "
        f"n_gold={result['testpoints_testable_n_gold']} "
        f"n_pred={result['testpoints_testable_n_pred']} "
        f"n_docs={result['testpoints_testable_n_docs']}"
    )
    return "\n".join(lines) + "\n"


_SHEET_FILES = ("statements.csv", "pairs.csv", "questions.csv", "testpoints.csv")


def _gold_root(gold_dir: Path) -> Path:
    """Directory that holds the access log.

    ``--gold`` is a corpus directory: ``open/`` and ``sealed/`` inside it, or
    the CSV sheets themselves. The log is one level above that directory.
    A directory with neither is already the root.
    """
    folder = Path(gold_dir)
    nested = (folder / "open").is_dir() or (folder / "sealed").is_dir()
    flat = any((folder / name).is_file() for name in _SHEET_FILES)
    if nested or flat:
        return folder.parent
    return folder


def _git_commit() -> str:
    repo = Path(__file__).resolve().parents[1]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _access_log_path(gold_dir: Path) -> Path:
    return _gold_root(gold_dir) / "ACCESS.log"


def _prepare_access_log(gold_dir: Path) -> Path:
    """Prove the access log can be written before any sealed sheet is read."""
    path = _access_log_path(gold_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline=""):
        pass
    return path


def _append_access_log(gold_dir: Path, report: str) -> None:
    """Append timestamp, git commit, user, and the metric summary.

    The summary is the aggregate report on one line. Sheet rows are not written.
    """
    path = _access_log_path(gold_dir)
    summary = " ".join(report.splitlines())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = [stamp, _git_commit(), getpass.getuser(), summary]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerow(row)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Score a gold folder against system output. Prints aggregates only."
    )
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--system", required=True, type=Path)
    parser.add_argument(
        "--sealed",
        action="store_true",
        help="Score sealed sheets only. Append timestamp, git commit, user, and metric summary to gold/ACCESS.log before printing.",
    )
    args = parser.parse_args(argv)
    system = json.loads(args.system.read_text(encoding="utf-8"))
    if args.sealed:
        try:
            _prepare_access_log(args.gold)
        except OSError as exc:
            sys.stderr.write(f"cannot write access log: {exc}\n")
            raise SystemExit(1) from exc
    try:
        report = format_report(score(args.gold, system, sealed=args.sealed))
    except ScoreRefusal as exc:
        sys.stderr.write(str(exc) + "\n")
        raise SystemExit(1) from exc
    if args.sealed:
        try:
            _append_access_log(args.gold, report)
        except OSError as exc:
            sys.stderr.write(f"cannot write access log: {exc}\n")
            raise SystemExit(1) from exc
    sys.stdout.write(report)


if __name__ == "__main__":
    main()
