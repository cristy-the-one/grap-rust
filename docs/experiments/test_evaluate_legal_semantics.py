from evaluate_legal_semantics import score
import json
from pathlib import Path

import pytest

from grap_mcp.kee.legal_rules import extract_rules
from grap_mcp.kee.llm import StubProvider


DATA = Path(__file__).parent
CASES = json.loads((DATA / "real-law-semantic-cases.json").read_text(encoding="utf-8"))["cases"]
CASES += json.loads((DATA / "real-law-semantic-transfer-cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_recorded_gemma_subject_and_kind_regressions(case):
    """Replay the actual v7 failures and successes, independent of model availability."""
    name = "semantic-gemma-dev-v7-final.json" if case["split"] == "dev" else "semantic-gemma-holdout-v7.json"
    if case["id"].startswith("transfer-"):
        name = "semantic-gemma-transfer-v7.json"
    report = json.loads((DATA / name).read_text(encoding="utf-8"))
    row = next(r for r in report["cases"] if r["id"] == case["id"])
    replies = iter(call["reply"] for call in row["calls"])
    receipt = extract_rules(StubProvider(handler=lambda *_: next(replies)), case["text"])
    result = score(case, receipt["claims"])
    assert result["grounded"] and result["joint_correct"], receipt["claims"]


def example():
    text = "Banca poate solicita date numai după aprobare."
    case = {"text": text, "expected_kind": "permission", "accepted_subjects": ["Banca"],
            "required_text": ["numai după aprobare"]}
    claim = {"kind": "permission", "subject": "Banca", "statement": text,
             "evidence": [{"quote": text, "start": 0, "end": len(text)}]}
    return case, claim


def test_grounding_does_not_count_as_correct_semantics():
    case, claim = example()
    claim.update(kind="obligation", subject="date")
    result = score(case, [claim])
    assert result["grounded"] and result["qualifications_retained"]
    assert not result["kind_correct"] and not result["subject_correct"] and not result["joint_correct"]


def test_empty_duplicate_and_missing_evidence_are_not_successes():
    case, claim = example()
    assert score(case, [claim])["joint_correct"]
    assert score(case, [])["missing"] and not score(case, [])["grounded"]
    assert not score(case, [claim, claim])["joint_correct"]
    claim["evidence"] = []
    assert not score(case, [claim])["grounded"]


def test_qualification_must_belong_to_same_statement():
    case, claim = example()
    claim["statement"] = "Banca poate solicita date."
    result = score(case, [claim])
    assert result["kind_correct"] and result["subject_correct"]
    assert not result["qualifications_retained"] and not result["joint_correct"]
