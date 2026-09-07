from evaluate_legal_semantics import score


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
