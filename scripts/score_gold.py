#!/usr/bin/env python3
"""Score a gold folder against a system-output JSON. Prints aggregates only.

The folder is either a corpus directory with ``open/`` and ``sealed/`` sheets,
or a directory that holds the CSVs itself. Sheets are UTF-8 with a header.
Statement rows match on item_id, kind, and subject (B3). Pair labels collapse
to same / not-same, and ``unclear`` rows are excluded from the pair metric.
Question hits require every ``where`` entry, except unanswerable questions,
which hit only when the system declines.

System JSON::

    {
      "statements": [{"source_id", "item_id", "kind", "subject"}],
      "pairs": [{"packet_id", "label"}],
      "questions": [{"packet_id", "cited": ["..."], "declined": false}]
    }

Every figure includes a 95% bootstrap interval, resampling source documents
with replacement (2,000 resamples, seed 20261112). No row is printed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

BOOTSTRAP_SEED = 20261112
BOOTSTRAP_RESAMPLES = 2000

_SAME_LABELS = {"same", "same only here"}
_SHEETS = ("open", "sealed")


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


def _sheet_paths(gold_dir: Path, name: str) -> list[Path]:
    paths = []
    direct = gold_dir / name
    if direct.is_file():
        paths.append(direct)
    for part in _SHEETS:
        candidate = gold_dir / part / name
        if candidate.is_file():
            paths.append(candidate)
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
            if (row["item_id"] == pred["item_id"]
                    and _norm(row["kind"]) == _norm(pred["kind"])
                    and _subjects_agree(row["subject"], pred["subject"])):
                used.add(index)
                tp += 1
                break
    return tp, len(predicted) - tp, len(gold) - tp


def _score_statements(rows: list[dict], predicted: list[dict]) -> dict:
    gold = [{
        "source_id": row.get("source_id") or "",
        "item_id": str(row.get("item_id") or ""),
        "kind": row.get("kind") or "",
        "subject": row.get("subject") or "",
    } for row in rows]
    preds = [{
        "source_id": str(row.get("source_id") or ""),
        "item_id": str(row.get("item_id") or ""),
        "kind": row.get("kind") or "",
        "subject": row.get("subject") or "",
    } for row in predicted]
    precision, recall, f1 = _prf(*_match_statements(gold, preds))
    gold_by = defaultdict(list)
    pred_by = defaultdict(list)
    for row in gold:
        gold_by[row["source_id"]].append(row)
    for row in preds:
        pred_by[row["source_id"]].append(row)

    def evaluate(drawn):
        return _prf(*_match_statements(_expand(drawn, gold_by), _expand(drawn, pred_by)))

    intervals = _bootstrap(set(gold_by) | set(pred_by), evaluate)
    precision_low, precision_high = _bounds(precision, None if intervals is None else intervals[0])
    recall_low, recall_high = _bounds(recall, None if intervals is None else intervals[1])
    f1_low, f1_high = _bounds(f1, None if intervals is None else intervals[2])
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
    f1 = _prf(*_pair_counts(scored, labels))[2]

    def evaluate(drawn):
        return (_prf(*_pair_counts(_expand(drawn, scored_by), labels))[2],)

    intervals = _bootstrap(scored_by, evaluate)
    low, high = _bounds(f1, None if intervals is None else intervals[0])
    return {
        "pairs_f1": f1,
        "pairs_f1_low": low,
        "pairs_f1_high": high,
        "pairs_unclear": unclear,
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
    rate = _question_rate(gold, by_id)

    def evaluate(drawn):
        return (_question_rate(_expand(drawn, grouped), by_id),)

    intervals = _bootstrap(grouped, evaluate)
    low, high = _bounds(rate, None if intervals is None else intervals[0])
    return {
        "questions_hit_rate": rate,
        "questions_hit_rate_low": low,
        "questions_hit_rate_high": high,
    }


def score(gold_dir, system: dict) -> dict:
    """Return statement, pair, and question aggregates for one gold folder."""
    folder = Path(gold_dir)
    system = system or {}
    result = {}
    result.update(_score_statements(
        _read_rows(_sheet_paths(folder, "statements.csv")),
        list(system.get("statements") or []),
    ))
    result.update(_score_pairs(
        _read_rows(_sheet_paths(folder, "pairs.csv")),
        list(system.get("pairs") or []),
    ))
    result.update(_score_questions(
        _read_rows(_sheet_paths(folder, "questions.csv")),
        list(system.get("questions") or []),
    ))
    return result


def format_report(result: dict) -> str:
    lines = []
    for key in (
        "statements_precision",
        "statements_recall",
        "statements_f1",
        "pairs_f1",
        "questions_hit_rate",
    ):
        lines.append(
            f"{key}={result[key]:.10f} [{result[key + '_low']:.10f}, {result[key + '_high']:.10f}]"
        )
    lines.append(f"pairs_unclear={result['pairs_unclear']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Score a gold folder against system output. Prints aggregates only."
    )
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--system", required=True, type=Path)
    args = parser.parse_args(argv)
    system = json.loads(args.system.read_text(encoding="utf-8"))
    sys.stdout.write(format_report(score(args.gold, system)))


if __name__ == "__main__":
    main()
