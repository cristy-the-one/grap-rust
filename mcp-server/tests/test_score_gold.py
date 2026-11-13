"""Scorer tests for Sheet 1–3 gold. Fixtures are synthetic and live under tmp_path."""

import csv
import getpass
import json
import math
import random
import subprocess
import sys

import pytest
from collections import Counter, defaultdict
from datetime import datetime
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
            if (row["source_id"] == pred["source_id"]
                    and row["item_id"] == pred["item_id"]
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
    # Default scoring leaves the held row out, so the open match stays exact.
    recall = score_gold.score(folder, json.loads(system_path.read_text(encoding="utf-8")))
    assert recall["statements_recall"] == 1.0
    assert recall["statements_precision"] == 1.0


def _held_sheet_mentions(text):
    """True when the held-sheet directory prefix occurs and the sheet name follows.

    The two pieces are built here so this file does not itself contain that
    sequence. The walker below reads every other Python file as text.
    """
    head = "gol" + "d/"
    tail = "sea" + "led"
    start = 0
    while True:
        found = text.find(head, start)
        if found < 0:
            return False
        if tail in text[found + len(head):]:
            return True
        start = found + 1


# The scorer is the only reader of held sheets. The packet generator is the
# only writer: it creates them and never opens one for reading.
_HELD_SHEET_FILES = {"score_gold.py", "make_gold_packet.py"}


def test_packet_generator_never_reads_a_sheet():
    repo = Path(__file__).resolve().parents[2]
    text = (repo / "scripts" / "make_gold_packet.py").read_text(encoding="utf-8")
    assert "DictReader" not in text and "csv.reader" not in text


def test_only_score_gold_py_mentions_held_sheet_paths():
    """Every .py under mcp-server/ and scripts/ except the reader and the writer."""
    repo = Path(__file__).resolve().parents[2]
    hits = []
    for base in ("mcp-server", "scripts"):
        for path in (repo / base).rglob("*.py"):
            if path.name in _HELD_SHEET_FILES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _held_sheet_mentions(text):
                hits.append(path.relative_to(repo).as_posix())
    assert hits == []


def test_sealed_flag_appends_one_access_log_line(tmp_path, capsys):
    """``--sealed`` appends one aggregate line under a tmp gold root.

    tmp_path stands in for the gold directory. The corpus is a child of that
    root, and the log file is the root's ACCESS.log. A canary that exists
    only on a held row must not appear in the line.
    """
    canary = "SEALEDROWCANARY9f3a7c"
    corpus = tmp_path / "x"
    _write_csv(corpus / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("open-doc", "art-1", "obligation", "the bank"),
    ])
    held = _statement("held-doc", "art-9", "prohibition", canary, passage=canary)
    held["note"] = canary
    _write_csv(corpus / "sealed" / "statements.csv", _STATEMENT_COLUMNS, [held])
    payload = {"statements": [{
        "source_id": "open-doc", "item_id": "art-1", "kind": "obligation", "subject": "the bank",
    }]}
    system_path = tmp_path / "system.json"
    system_path.write_text(json.dumps(payload), encoding="utf-8")
    log = tmp_path / "ACCESS.log"
    repo_gold = Path(__file__).resolve().parents[2] / "gold"

    score_gold.main(["--gold", str(corpus), "--system", str(system_path)])
    assert not log.exists()
    capsys.readouterr()

    score_gold.main(["--gold", str(corpus), "--system", str(system_path), "--sealed"])
    captured = capsys.readouterr()
    assert canary not in captured.out
    assert canary not in captured.err
    text = log.read_text(encoding="utf-8")
    assert canary not in text
    assert text.endswith("\n")
    assert text.count("\n") == 1
    assert log.resolve().is_relative_to(tmp_path.resolve())
    assert not str(log.resolve()).startswith(str(repo_gold.resolve()))
    row = next(csv.reader(text.splitlines()))
    assert len(row) == 4
    stamp, commit, user, summary = row
    datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    expected_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
    ).strip()
    assert commit == expected_commit
    assert user == getpass.getuser()
    assert summary == " ".join(captured.out.splitlines())
    scored = score_gold.score(corpus, payload, sealed=True)
    assert scored["statements_recall"] == 0.0
    # The system's only claim is about a document outside the sealed sheet,
    # so there is nothing to compute precision from.
    assert scored["statements_precision"] == "n/a"
    assert "statements_recall=0.0000000000" in summary
    assert f"pairs_unclear={scored['pairs_unclear']}" in summary

    score_gold.main(["--gold", str(corpus), "--system", str(system_path), "--sealed"])
    text2 = log.read_text(encoding="utf-8")
    assert canary not in text2
    lines = text2.splitlines()
    assert len(lines) == 2
    assert lines[0] == text.splitlines()[0]
    second = next(csv.reader([lines[1]]))
    assert len(second) == 4
    assert canary not in second[3]
    assert "statements_precision=" in second[3]
def test_statement_match_requires_source_and_item(tmp_path):
    """The same article label in two acts is not one statement."""
    folder = tmp_path / "packet"
    _write_csv(folder / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("act-a", "Articolul 1", "obligation", "the ministry"),
        _statement("act-b", "Articolul 1", "obligation", "the bank"),
    ])
    # Matching on the label alone would pair this claim with act-b's row.
    crossed = score_gold.score(folder, {"statements": [{
        "source_id": "act-a", "item_id": "Articolul 1", "kind": "obligation", "subject": "the bank",
    }]})
    assert crossed["statements_precision"] == 0.0
    assert crossed["statements_recall"] == 0.0

    same = score_gold.score(folder, {"statements": [{
        "source_id": "act-b", "item_id": "Articolul 1", "kind": "obligation", "subject": "the bank",
    }]})
    assert same["statements_precision"] == 1.0
    # One of the two gold rows is found; act-a's row has no matching claim.
    assert same["statements_recall"] == 0.5


def test_refuses_blank_kind_bad_label_and_empty_where(tmp_path, capsys):
    canary = "ROWTEXTMUSTNOTLEAK"
    folder = tmp_path / "packet"
    blank = _statement("doc", "a", "", "subject", passage=canary)
    blank["note"] = canary
    whitespace = _statement("doc", "b", "   ", "subject")
    filled = _statement("doc", "c", "obligation", "the bank")
    _write_csv(folder / "open" / "statements.csv", _STATEMENT_COLUMNS, [blank, whitespace, filled])
    _write_csv(folder / "open" / "pairs.csv", _PAIR_COLUMNS, [
        {"packet_id": "p1", "source_id": "d1", "item_id": "a", "passage": canary,
         "other_source_id": "d9", "other_item_id": "b", "other_passage": "right", "label": "sme"},
        {"packet_id": "p2", "source_id": "d1", "item_id": "c", "passage": "left",
         "other_source_id": "d9", "other_item_id": "d", "other_passage": "right", "label": ""},
        {"packet_id": "p3", "source_id": "d1", "item_id": "e", "passage": "left",
         "other_source_id": "d9", "other_item_id": "f", "other_passage": "right", "label": "unclear"},
        {"packet_id": "p4", "source_id": "d1", "item_id": "g", "passage": "left",
         "other_source_id": "d9", "other_item_id": "h", "other_passage": "right", "label": "same"},
    ])
    _write_csv(folder / "open" / "questions.csv", _QUESTION_COLUMNS, [
        {"packet_id": "q1", "source_id": "s1", "item_id": "", "passage": canary,
         "question": "What is due?", "type": "Direct", "answer": "yes", "where": "", "note": ""},
        {"packet_id": "q2", "source_id": "s1", "item_id": "", "passage": "",
         "question": "Which article?", "type": "Direct", "answer": "yes", "where": "a#1", "note": ""},
        {"packet_id": "q3", "source_id": "s2", "item_id": "", "passage": "",
         "question": "Who signed it?", "type": "Not answerable", "answer": "x",
         "where": "", "note": ""},
    ])
    with pytest.raises(Exception) as caught:
        score_gold.score(folder, {})
    message = str(caught.value)
    assert "statements: 2 blank kind" in message
    assert "pairs: 2 label outside the guide list" in message
    assert "questions: 1 answerable with empty where" in message
    assert canary not in message
    system_path = tmp_path / "system.json"
    system_path.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        score_gold.main(["--gold", str(folder), "--system", str(system_path)])
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert canary not in captured.out
    assert canary not in captured.err
    assert "statements: 2 blank kind" in captured.err


def test_metrics_carry_counts_and_empty_is_na(tmp_path, capsys):
    """No gold rows and zero predictions print n/a, never 1.0, and every metric shows n."""
    folder = tmp_path / "packet"
    _write_csv(folder / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("doc", "art-1", "obligation", "the bank"),
    ])
    missed = score_gold.score(folder, {})
    assert missed["statements_precision"] == "n/a"
    assert missed["statements_precision_low"] == "n/a"
    assert missed["statements_precision_high"] == "n/a"
    assert missed["statements_recall"] == 0.0
    assert missed["statements_f1"] == 0.0
    assert missed["statements_n_gold"] == 1
    assert missed["statements_n_pred"] == 0
    assert missed["statements_n_docs"] == 1
    assert missed["pairs_f1"] == "n/a"
    assert missed["pairs_f1_low"] == "n/a"
    assert missed["questions_hit_rate"] == "n/a"
    assert missed["questions_hit_rate_low"] == "n/a"
    assert missed["pairs_n_gold"] == 0
    assert missed["pairs_n_pred"] == 0
    assert missed["pairs_n_docs"] == 0
    assert missed["questions_n_gold"] == 0
    assert missed["questions_n_pred"] == 0
    assert missed["questions_n_docs"] == 0
    assert missed["pairs_unclear"] == 0

    empty = tmp_path / "empty"
    empty.mkdir()
    nothing = score_gold.score(empty, {"statements": [{
        "source_id": "doc", "item_id": "art-1", "kind": "obligation", "subject": "the bank",
    }]})
    assert nothing["statements_precision"] == "n/a"
    assert nothing["statements_recall"] == "n/a"
    assert nothing["statements_f1"] == "n/a"
    assert nothing["statements_n_gold"] == 0
    # The claim is about a document that is in no sheet, so it is left out.
    assert nothing["statements_n_pred"] == 0
    assert nothing["statements_n_docs"] == 0

    system_path = tmp_path / "system.json"
    system_path.write_text("{}", encoding="utf-8")
    score_gold.main(["--gold", str(folder), "--system", str(system_path)])
    out = capsys.readouterr().out
    assert "statements_precision=n/a [n/a, n/a] n_gold=1 n_pred=0 n_docs=1" in out
    assert "pairs_f1=n/a [n/a, n/a] n_gold=0 n_pred=0 n_docs=0" in out
    assert "questions_hit_rate=n/a [n/a, n/a] n_gold=0 n_pred=0 n_docs=0" in out
    assert "pairs_f1=1" not in out
    assert "questions_hit_rate=1" not in out


def test_default_reads_open_only(tmp_path):
    """A sealed row must not move a default score. A flat sheet counts as open."""
    folder = tmp_path / "packet"
    _write_csv(folder / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("open-doc", "art-1", "obligation", "the bank"),
    ])
    _write_csv(folder / "sealed" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("held-doc", "art-9", "prohibition", "held subject"),
    ])
    system = {"statements": [{
        "source_id": "open-doc", "item_id": "art-1", "kind": "obligation", "subject": "the bank",
    }]}
    opened = score_gold.score(folder, system)
    assert opened["statements_recall"] == 1.0
    assert opened["statements_precision"] == 1.0

    flat = tmp_path / "flat"
    _write_csv(flat / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("open-doc", "art-1", "obligation", "the bank"),
    ])
    _write_csv(flat / "sealed" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("held-doc", "art-9", "prohibition", "held subject"),
    ])
    flat_score = score_gold.score(flat, system)
    assert flat_score["statements_recall"] == 1.0
    assert flat_score["statements_precision"] == 1.0


def test_sealed_flag_reads_sealed_only(tmp_path, capsys):
    canary = "SEALEDROWCANARY9f3a7c"
    folder = tmp_path / "packet"
    _write_csv(folder / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("open-doc", "art-1", "obligation", "the bank"),
    ])
    sealed = _statement("held-doc", "art-9", "prohibition", canary, passage=canary)
    sealed["note"] = canary
    _write_csv(folder / "sealed" / "statements.csv", _STATEMENT_COLUMNS, [sealed])
    system = {"statements": [{
        "source_id": "open-doc", "item_id": "art-1", "kind": "obligation", "subject": "the bank",
    }]}
    held = score_gold.score(folder, system, sealed=True)
    # Only the unmatched sealed row is gold. The open-document prediction is
    # outside the sealed sheet, so it is left out rather than counted wrong.
    assert held["statements_recall"] == 0.0
    assert held["statements_precision"] == "n/a"
    system_path = tmp_path / "system.json"
    system_path.write_text(json.dumps(system), encoding="utf-8")
    score_gold.main(["--gold", str(folder), "--system", str(system_path), "--sealed"])
    captured = capsys.readouterr()
    assert canary not in captured.out
    assert canary not in captured.err


def test_score_without_flag_ignores_sealed_directory(tmp_path, capsys):
    """A run without ``--sealed`` is unchanged by the presence of a sealed sheet."""
    canary = "SEALEDROWCANARY9f3a7c"
    payload = {"statements": [{
        "source_id": "open-doc", "item_id": "art-1", "kind": "obligation", "subject": "the bank",
    }]}
    present = tmp_path / "present"
    absent = tmp_path / "absent"
    open_row = _statement("open-doc", "art-1", "obligation", "the bank")
    _write_csv(present / "open" / "statements.csv", _STATEMENT_COLUMNS, [open_row])
    held = _statement("held-doc", "art-9", "prohibition", canary, passage=canary)
    held["note"] = canary
    _write_csv(present / "sealed" / "statements.csv", _STATEMENT_COLUMNS, [held])
    _write_csv(absent / "open" / "statements.csv", _STATEMENT_COLUMNS, [open_row])
    assert score_gold.score(present, payload) == score_gold.score(absent, payload)

    body = json.dumps(payload)
    system_present = tmp_path / "system-present.json"
    system_absent = tmp_path / "system-absent.json"
    system_present.write_text(body, encoding="utf-8")
    system_absent.write_text(body, encoding="utf-8")
    score_gold.main(["--gold", str(present), "--system", str(system_present)])
    out_present = capsys.readouterr().out
    score_gold.main(["--gold", str(absent), "--system", str(system_absent)])
    out_absent = capsys.readouterr().out
    assert out_present == out_absent
    assert canary not in out_present
    assert not (tmp_path / "ACCESS.log").exists()


def test_sealed_log_is_written_before_printing(tmp_path, monkeypatch):
    canary = "SEALEDROWCANARY9f3a7c"
    corpus = tmp_path / "x"
    _write_csv(corpus / "sealed" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("held-doc", "art-9", "prohibition", "the bank"),
    ])
    system_path = tmp_path / "system.json"
    system_path.write_text(json.dumps({"statements": [{
        "source_id": "held-doc", "item_id": "art-9", "kind": "prohibition", "subject": "the bank",
    }]}), encoding="utf-8")
    log = tmp_path / "ACCESS.log"
    seen = {}
    real_write = sys.stdout.write

    def spy(text):
        seen["log"] = log.read_text(encoding="utf-8") if log.is_file() else ""
        return real_write(text)

    monkeypatch.setattr(sys.stdout, "write", spy)
    score_gold.main(["--gold", str(corpus), "--system", str(system_path), "--sealed"])
    assert canary not in seen.get("log", "")
    assert "statements_precision=" in seen.get("log", "")


def test_unwritable_access_log_scores_nothing(tmp_path, capsys):
    corpus = tmp_path / "x"
    _write_csv(corpus / "sealed" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("held-doc", "art-9", "prohibition", "the bank"),
    ])
    system_path = corpus / "system.json"
    system_path.write_text("{}", encoding="utf-8")
    # A directory where the log file should be makes the log unwritable on
    # every platform. Directory permission bits do not do that on Windows.
    (tmp_path / "ACCESS.log").mkdir()
    with pytest.raises(SystemExit) as caught:
        score_gold.main(["--gold", str(corpus), "--system", str(system_path), "--sealed"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (tmp_path / "ACCESS.log").is_dir()


def test_predictions_for_documents_outside_the_scored_sheet_are_ignored(tmp_path):
    """A system normally outputs claims for every document. Claims about a
    document that is not in the sheet being scored (for example a sealed
    document during an open run) cannot be judged and must not count as
    false positives."""
    corpus = tmp_path / "x"
    _write_csv(corpus / "open" / "statements.csv", _STATEMENT_COLUMNS, [
        _statement("open-doc", "art-1", "obligation", "the bank"),
    ])
    system = {"statements": [
        {"source_id": "open-doc", "item_id": "art-1", "kind": "obligation", "subject": "the bank"},
        {"source_id": "held-doc", "item_id": "art-9", "kind": "prohibition", "subject": "the ministry"},
    ]}
    result = score_gold.score(corpus, system)
    assert result["statements_precision"] == 1.0
    assert result["statements_n_pred"] == 1
    assert result["statements_n_docs"] == 1
