"""Scorer tests for Sheet 1–3 gold. Fixtures are synthetic and live under tmp_path."""

import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import score_gold  # noqa: E402

_SEED = 20261112
_RESAMPLES = 2000
_SAME = {"same", "same only here"}

_STATEMENT_COLUMNS = (
    "packet_id", "source_id", "item_id", "passage",
    "kind", "subject", "conditions", "exceptions",
    "dates_and_amounts", "acts_referred_to", "sure", "note",
)
_PAIR_COLUMNS = (
    "packet_id", "source_id", "item_id", "passage",
    "other_source_id", "other_item_id", "other_passage", "label",
)
_QUESTION_COLUMNS = (
    "packet_id", "source_id", "item_id", "passage",
    "question", "type", "answer", "where", "note",
)


def _norm(value):
    return " ".join(str(value).lower().split())


def _token_f1(left, right):
    """Token F1 after the caller has lower-cased and collapsed whitespace."""
    a = left.split()
    b = right.split()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    overlap = sum((Counter(a) & Counter(b)).values())
    if overlap == 0:
        return 0.0
    return 2 * overlap / (len(a) + len(b))


def _subjects_agree(left, right):
    a = _norm(left)
    b = _norm(right)
    return a == b or _token_f1(a, b) >= 0.8


def _write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _statement(source_id, item_id, kind, subject, passage="synthetic passage"):
    return {
        "packet_id": f"{source_id}#{item_id}",
        "source_id": source_id,
        "item_id": item_id,
        "passage": passage,
        "kind": kind,
        "subject": subject,
        "conditions": "",
        "exceptions": "",
        "dates_and_amounts": "",
        "acts_referred_to": "",
        "sure": "yes",
        "note": "",
    }


def _prf(tp, fp, fn):
    """Positive-class precision, recall, and F1. Vacuous sides are 1."""
    precision = 1.0 if tp + fp == 0 else tp / (tp + fp)
    recall = 1.0 if tp + fn == 0 else tp / (tp + fn)
    denom = 2 * tp + fp + fn
    f1 = 1.0 if denom == 0 else (2 * tp) / denom
    return precision, recall, f1


def _match_statements(gold, predicted):
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


def _pair_positive(label):
    text = _norm(label)
    if text == "unclear":
        return None
    return text in _SAME


def _where_entries(text):
    entries = []
    for chunk in str(text).replace("\n", ";").split(";"):
        piece = chunk.strip()
        if piece:
            entries.append(piece)
    return entries


def _unanswerable(row):
    return _norm(row.get("type", "")) == "not answerable" or _norm(row.get("answer", "")) == "not in the documents"


def _question_hit(row, system):
    if _unanswerable(row):
        return bool(system.get("declined", False))
    cited = {str(item).strip() for item in system.get("cited") or []}
    return all(entry in cited for entry in _where_entries(row.get("where", "")))


def _percentile(samples, p):
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


def _interval(doc_ids, evaluate):
    docs = sorted(set(doc_ids))
    rng = random.Random(_SEED)
    count = len(docs)
    samples = []
    for _ in range(_RESAMPLES):
        drawn = [docs[rng.randrange(count)] for _ in range(count)]
        samples.append(float(evaluate(drawn)))
    return _percentile(samples, 0.025), _percentile(samples, 0.975)


def _expand(drawn, grouped):
    rows = []
    for doc in drawn:
        rows.extend(grouped[doc])
    return rows


def _long_subjects():
    gold = " ".join(f"t{i}" for i in range(121))
    pred = " ".join(f"t{i}" for i in range(79))
    return gold, pred


def test_subject_fixtures_are_the_stated_f1():
    gold, pred = _long_subjects()
    assert _token_f1(gold, pred) == 0.79
    assert _token_f1("alpha beta gamma", "alpha beta") == 0.8
    assert _subjects_agree(gold, pred) is False
    assert _subjects_agree("alpha beta gamma", "alpha beta") is True
    assert _subjects_agree("The  Bank", "the bank") is True


def test_subject_f1_079_is_miss_and_080_is_hit(tmp_path):
    gold_long, pred_long = _long_subjects()
    miss = tmp_path / "miss"
    _write_csv(miss / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("doc", "item", "obligation", gold_long),
    ])
    missed = score_gold.score(miss, {"statements": [{
        "source_id": "doc", "item_id": "item", "kind": "obligation", "subject": pred_long,
    }]})
    assert missed["statements_precision"] == 0.0
    assert missed["statements_recall"] == 0.0

    hit = tmp_path / "hit"
    _write_csv(hit / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("doc", "item", "obligation", "alpha beta gamma"),
    ])
    matched = score_gold.score(hit, {"statements": [{
        "source_id": "doc", "item_id": "item", "kind": "obligation", "subject": "alpha beta",
    }]})
    assert matched["statements_precision"] == 1.0
    assert matched["statements_recall"] == 1.0


def test_six_row_gold_precision_and_recall(tmp_path):
    """6 gold statements. TP=3, FP=2, FN=3 → precision 3/5, recall 1/2, F1 6/11.

    Row 1 matches after case and whitespace collapse. Row 2 fails on kind.
    Row 3 is a subject F1 of 0.80 (hit). Row 4 is a subject F1 of 0.79 (miss).
    Row 5 has no prediction. Row 6 matches exactly.
    """
    gold_long, pred_long = _long_subjects()
    rows = [
        _statement("doc1", "art-1", "obligation", "The  Bank"),
        _statement("doc1", "art-2", "prohibition", "the shareholders"),
        _statement("doc2", "art-3", "permission", "alpha beta gamma"),
        _statement("doc2", "art-4", "right", gold_long),
        _statement("doc3", "art-5", "definition", "payments"),
        _statement("doc3", "art-6", "fact", "the registry"),
    ]
    assert len(rows) == 6
    predicted = [
        {"source_id": "doc1", "item_id": "art-1", "kind": "obligation", "subject": "the bank"},
        {"source_id": "doc1", "item_id": "art-2", "kind": "obligation", "subject": "the shareholders"},
        {"source_id": "doc2", "item_id": "art-3", "kind": "permission", "subject": "alpha beta"},
        {"source_id": "doc2", "item_id": "art-4", "kind": "right", "subject": pred_long},
        {"source_id": "doc3", "item_id": "art-6", "kind": "fact", "subject": "the registry"},
    ]
    folder = tmp_path / "packet"
    _write_csv(folder / "open" / "statements.csv", _STATEMENT_COLUMNS, rows)
    result = score_gold.score(folder, {"statements": predicted})

    assert result["statements_precision"] == 3 / 5
    assert result["statements_recall"] == 1 / 2
    assert result["statements_f1"] == 6 / 11
    assert score_gold.BOOTSTRAP_SEED == _SEED
    assert score_gold.BOOTSTRAP_RESAMPLES == _RESAMPLES

    gold_by = defaultdict(list)
    pred_by = defaultdict(list)
    for row in rows:
        gold_by[row["source_id"]].append(row)
    for row in predicted:
        pred_by[row["source_id"]].append(row)
    docs = set(gold_by) | set(pred_by)

    def _metrics(drawn):
        tp, fp, fn = _match_statements(_expand(drawn, gold_by), _expand(drawn, pred_by))
        return _prf(tp, fp, fn)

    precision_low, precision_high = _interval(docs, lambda drawn: _metrics(drawn)[0])
    recall_low, recall_high = _interval(docs, lambda drawn: _metrics(drawn)[1])
    f1_low, f1_high = _interval(docs, lambda drawn: _metrics(drawn)[2])
    assert result["statements_precision_low"] == precision_low
    assert result["statements_precision_high"] == precision_high
    assert result["statements_recall_low"] == recall_low
    assert result["statements_recall_high"] == recall_high
    assert result["statements_f1_low"] == f1_low
    assert result["statements_f1_high"] == f1_high
    assert result == score_gold.score(folder, {"statements": predicted})


def test_unclear_pairs_excluded_and_counted(tmp_path):
    """Same and 'same only here' are the positive class.

    'same, other release', 'part of', 'realises', and 'different' are negative.
    Two 'unclear' rows are dropped. TP=2, FP=2, FN=0 → pair F1 = 2/3.
    """
    gold_rows = [
        {"packet_id": "p1", "source_id": "d1", "item_id": "a", "passage": "left",
         "other_source_id": "d9", "other_item_id": "b", "other_passage": "right", "label": "same"},
        {"packet_id": "p2", "source_id": "d1", "item_id": "c", "passage": "left",
         "other_source_id": "d9", "other_item_id": "d", "other_passage": "right", "label": "same only here"},
        {"packet_id": "p3", "source_id": "d2", "item_id": "e", "passage": "left",
         "other_source_id": "d9", "other_item_id": "f", "other_passage": "right", "label": "different"},
        {"packet_id": "p4", "source_id": "d2", "item_id": "g", "passage": "left",
         "other_source_id": "d9", "other_item_id": "h", "other_passage": "right", "label": "same, other release"},
        {"packet_id": "p5", "source_id": "d3", "item_id": "i", "passage": "left",
         "other_source_id": "d9", "other_item_id": "j", "other_passage": "right", "label": "part of"},
        {"packet_id": "p6", "source_id": "d3", "item_id": "k", "passage": "left",
         "other_source_id": "d9", "other_item_id": "l", "other_passage": "right", "label": "realises"},
        {"packet_id": "p7", "source_id": "d4", "item_id": "m", "passage": "left",
         "other_source_id": "d9", "other_item_id": "n", "other_passage": "right", "label": "unclear"},
        {"packet_id": "p8", "source_id": "d4", "item_id": "o", "passage": "left",
         "other_source_id": "d9", "other_item_id": "p", "other_passage": "right", "label": "unclear"},
    ]
    system_pairs = [
        {"packet_id": "p1", "label": "same"},
        {"packet_id": "p2", "label": "same only here"},
        {"packet_id": "p3", "label": "same"},
        {"packet_id": "p4", "label": "different"},
        {"packet_id": "p5", "label": "part of"},
        {"packet_id": "p6", "label": "same"},
        {"packet_id": "p7", "label": "same"},
        {"packet_id": "p8", "label": "different"},
    ]
    folder = tmp_path / "packet"
    _write_csv(folder / "open" / "pairs.csv", _PAIR_COLUMNS, gold_rows)
    result = score_gold.score(folder, {"pairs": system_pairs})
    assert result["pairs_unclear"] == 2
    assert result["pairs_f1"] == 2 / 3

    by_id = {row["packet_id"]: row["label"] for row in system_pairs}
    scored_by = defaultdict(list)
    for row in gold_rows:
        if _pair_positive(row["label"]) is None:
            continue
        scored_by[row["source_id"]].append(row)

    def _f1(drawn):
        tp = fp = fn = 0
        for row in _expand(drawn, scored_by):
            gold_pos = _pair_positive(row["label"])
            system_pos = _pair_positive(by_id.get(row["packet_id"], ""))
            if system_pos is None:
                system_pos = False
            if gold_pos and system_pos:
                tp += 1
            elif system_pos:
                fp += 1
            elif gold_pos:
                fn += 1
        return _prf(tp, fp, fn)[2]

    low, high = _interval(scored_by, _f1)
    assert result["pairs_f1_low"] == low
    assert result["pairs_f1_high"] == high


def test_question_hit_rate(tmp_path):
    """A hit cites every where entry. Unanswerable questions hit only on a decline.

    Four hits out of six: q1 (extra citation still a hit), q3, q5, q6.
    """
    rows = [
        {"packet_id": "q1", "source_id": "s1", "item_id": "", "passage": "",
         "question": "What is due?", "type": "Direct", "answer": "yes",
         "where": "a#1; a#2", "note": ""},
        {"packet_id": "q2", "source_id": "s2", "item_id": "", "passage": "",
         "question": "Which article?", "type": "Direct", "answer": "yes",
         "where": "b#1; b#2", "note": ""},
        {"packet_id": "q3", "source_id": "s3", "item_id": "", "passage": "",
         "question": "Who signed it?", "type": "Not answerable", "answer": "x",
         "where": "c#1", "note": ""},
        {"packet_id": "q4", "source_id": "s4", "item_id": "", "passage": "",
         "question": "When was it published?", "type": "Not answerable",
         "answer": "not in the documents", "where": "d#1", "note": ""},
        {"packet_id": "q5", "source_id": "s5", "item_id": "", "passage": "",
         "question": "Is it allowed?", "type": "Conditional", "answer": "yes",
         "where": "e#1", "note": ""},
        {"packet_id": "q6", "source_id": "s6", "item_id": "", "passage": "",
         "question": "What is the fee?", "type": "Direct", "answer": "not in the documents",
         "where": "f#1", "note": ""},
    ]
    system_questions = [
        {"packet_id": "q1", "cited": ["a#1", "a#2", "a#extra"], "declined": False},
        {"packet_id": "q2", "cited": ["b#1"], "declined": False},
        {"packet_id": "q3", "cited": [], "declined": True},
        {"packet_id": "q4", "cited": ["d#1"], "declined": False},
        {"packet_id": "q5", "cited": ["e#1"], "declined": False},
        {"packet_id": "q6", "cited": [], "declined": True},
    ]
    folder = tmp_path / "packet"
    _write_csv(folder / "open" / "questions.csv", _QUESTION_COLUMNS, rows)
    result = score_gold.score(folder, {"questions": system_questions})
    assert result["questions_hit_rate"] == 4 / 6

    by_id = {row["packet_id"]: row for row in system_questions}
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["source_id"]].append(row)

    def _rate(drawn):
        taken = _expand(drawn, grouped)
        if not taken:
            return 1.0
        hits = sum(1 for row in taken if _question_hit(row, by_id.get(row["packet_id"], {})))
        return hits / len(taken)

    low, high = _interval(grouped, _rate)
    assert result["questions_hit_rate_low"] == low
    assert result["questions_hit_rate_high"] == high


def test_sealed_rows_are_scored_and_not_emitted(tmp_path, capsys, caplog):
    canary = "SEALEDROWCANARY9f3a7c"
    folder = tmp_path / "packet"
    _write_csv(folder / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("open-doc", "art-1", "obligation", "the bank"),
    ])
    sealed = _statement("held-doc", "art-9", "prohibition", canary, passage=canary)
    sealed["note"] = canary
    _write_csv(folder / "sealed" / "statements.csv", _STATEMENT_COLUMNS, [sealed])
    system_path = tmp_path / "system.json"
    system_path.write_text(json.dumps({"statements": [{
        "source_id": "open-doc", "item_id": "art-1", "kind": "obligation", "subject": "the bank",
    }]}), encoding="utf-8")
    before = sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))
    with caplog.at_level("DEBUG"):
        score_gold.main(["--gold", str(folder), "--system", str(system_path)])
    captured = capsys.readouterr()
    after = sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))
    assert before == after
    assert canary not in captured.out
    assert canary not in captured.err
    assert canary not in caplog.text
    assert "statements_precision=" in captured.out
    assert "statements_recall=" in captured.out
    assert "statements_f1=" in captured.out
    assert "pairs_f1=" in captured.out
    assert "pairs_unclear=" in captured.out
    assert "questions_hit_rate=" in captured.out
    # The held row is a miss, so recall is 1/2. Ignoring it would leave recall at 1.
    recall = score_gold.score(folder, json.loads(system_path.read_text(encoding="utf-8")))
    assert recall["statements_recall"] == 1 / 2
    assert recall["statements_precision"] == 1.0
