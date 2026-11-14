"""X-02: a second check that a quote supports its claim.

The check runs beside quote validation, in shadow mode. It reports how many
quoted claims the checker agrees with, disagrees with, or cannot decide.
Each of those counts carries the number of quoted claims it was taken from.
Nothing the checker says changes what is accepted, stored, or returned.
A score is not a verdict: no threshold is applied.
"""

import copy
import json

import pytest

from grap_mcp.kee import extract
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.quote_support import check_quote_support, main


TEXT = "The Council issues Order 17."
RAW = json.dumps({
    "outcomes": [{"name": "Council", "requirements": [], "evidence": TEXT}],
    "relations": [{"from": "Council", "type": "issues", "to": "Order 17", "evidence": TEXT}],
})


def _extract(provider=None, checker=None):
    return extract.extract_chunk(
        provider or StubProvider(default=RAW), "act#0", TEXT, support_checker=checker,
    )


def test_shadow_check_counts_agree_disagree_and_undecided(capsys):
    """Three quoted claims, three verdicts, one denominator."""
    frag, reason = _extract()
    assert reason is None
    quoted = [claim for claim in frag["outcomes"] + frag["relations"]
              if claim.get("evidence", {}).get("status") == "quoted"]
    assert len(quoted) == 3
    answers = iter(("agree", "disagree", "undecided"))

    def reply(system, user):
        assert system != extract.SYSTEM_PROMPT
        assert system != extract.LEGAL_SYSTEM_PROMPT
        assert TEXT in user
        return json.dumps({"verdict": next(answers)})

    checker = StubProvider(handler=reply)
    before = copy.deepcopy(frag)
    report = check_quote_support(frag, checker, shadow=True)
    assert frag == before
    assert report["agree"] == 1
    assert report["disagree"] == 1
    assert report["undecided"] == 1
    assert report["n_claims"] == 3
    assert report["n_documents"] == 1
    printed = capsys.readouterr().out.strip()
    assert printed == (
        "shadow=1 agree=1/3 disagree=1/3 undecided=1/3 "
        "n_claims=3 n_provider_errors=0 n_documents=1"
    )
    assert len(checker.calls) == 3


def test_shadow_mode_changes_no_extraction_output(capsys):
    """Disagreeing with every claim leaves the fragment and its receipt unchanged.

    Refusing shadow mode does not ask the checker and does not edit the fragment.
    """
    alone_provider = StubProvider(default=RAW)
    alone, reason = extract.extract_chunk(alone_provider, "act#0", TEXT)
    assert reason is None and len(alone_provider.calls) == 1
    stored = extract.extraction_record(alone)

    checker = StubProvider(default='{"verdict":"disagree"}')
    extraction = StubProvider(default=RAW)
    checked, reason2 = extract.extract_chunk(
        extraction, "act#0", TEXT, support_checker=checker,
    )
    assert reason2 is None
    assert len(extraction.calls) == 1
    assert checked == alone
    assert extract.extraction_record(checked) == stored
    assert {item["name"] for item in checked["outcomes"]} == {"Council", "Order 17"}
    assert checked["relations"] == alone["relations"]
    printed = capsys.readouterr().out.strip()
    assert printed == (
        "shadow=1 agree=0/3 disagree=3/3 undecided=0/3 "
        "n_claims=3 n_provider_errors=0 n_documents=1"
    )

    with pytest.raises(RuntimeError, match="shadow"):
        check_quote_support(checked, checker, shadow=False)
    assert checked == alone
    assert len(checker.calls) == 3


def test_unlocated_quotes_are_not_rows_and_a_score_is_not_a_verdict(capsys):
    """Location failures stay out of the denominator. A bare score is undecided."""
    text = "The Test Plan records coverage."
    legacy, reason = extract.extract_chunk(StubProvider(default=json.dumps({
        "outcomes": [{"name": "Test Plan", "characteristics": ["coverage"]}],
        "relations": [],
    })), "plan#0", text)
    assert reason is None
    assert legacy["outcomes"][0]["evidence"]["status"] == "missing"
    invalid = {
        "source": "other#0",
        "outcomes": [{
            "name": "Test Plan",
            "characteristics": [],
            "evidence": {"status": "invalid", "quote": "made up"},
        }],
        "relations": [],
    }
    checker = StubProvider(default='{"verdict":"agree"}')
    report = check_quote_support([legacy, invalid], checker, shadow=True)
    assert checker.calls == []
    assert report["agree"] is None
    assert report["disagree"] is None
    assert report["undecided"] is None
    assert report["n_claims"] == 0
    assert report["n_documents"] == 2
    assert capsys.readouterr().out.strip() == (
        "shadow=1 agree=n/a disagree=n/a undecided=n/a "
        "n_claims=0 n_provider_errors=0 n_documents=2"
    )

    quoted, _ = _extract()
    scored = StubProvider(default='{"score": 0.99}')
    report = check_quote_support(quoted, scored, shadow=True)
    assert report["agree"] == 0
    assert report["disagree"] == 0
    assert report["undecided"] == 3
    assert report["n_claims"] == 3
    assert report["n_provider_errors"] == 0
    assert "undecided=3/3" in capsys.readouterr().out

    def down(_system, _user):
        raise RuntimeError("provider down")

    before = copy.deepcopy(quoted)
    report = check_quote_support(quoted, StubProvider(handler=down), shadow=True)
    assert quoted == before
    assert report["agree"] is None
    assert report["disagree"] is None
    assert report["undecided"] is None
    assert report["n_claims"] == 3
    assert report["n_provider_errors"] == 3
    assert capsys.readouterr().out.strip() == (
        "shadow=1 agree=n/a disagree=n/a undecided=n/a "
        "n_claims=3 n_provider_errors=3 n_documents=1"
    )


def test_a_provider_error_stays_out_of_the_verdict_denominator(capsys):
    """One failed call is not undecided, and it is not part of the fraction."""
    quoted, reason = _extract()
    assert reason is None
    seen = {"n": 0}

    def mixed(_system, _user):
        seen["n"] += 1
        if seen["n"] == 2:
            raise RuntimeError("provider down")
        return '{"verdict":"agree"}'

    before = copy.deepcopy(quoted)
    report = check_quote_support(quoted, StubProvider(handler=mixed), shadow=True)
    assert quoted == before
    assert report["agree"] == 2
    assert report["disagree"] == 0
    assert report["undecided"] == 0
    assert report["n_claims"] == 3
    assert report["n_provider_errors"] == 1
    assert capsys.readouterr().out.strip() == (
        "shadow=1 agree=2/2 disagree=0/2 undecided=0/2 "
        "n_claims=3 n_provider_errors=1 n_documents=1"
    )


def test_a_rejected_quote_never_reaches_the_checker():
    """Quote validation still drops a fabricated quote. The checker is not asked."""
    text = "The Test Plan records coverage."
    checker = StubProvider(default='{"verdict":"agree"}')
    bad, reason = extract.extract_chunk(StubProvider(default=json.dumps({
        "outcomes": [{"name": "Test Plan", "requirements": ["coverage"], "evidence": "made up"}],
        "relations": [],
    })), "plan#0", text, support_checker=checker)
    assert bad is None and reason == "filtered"
    assert checker.calls == []


def test_chunks_of_one_document_share_a_denominator(capsys):
    """Two chunks of one document are one document. The claim count is the denominator."""
    first, _ = _extract()
    second_text = "The Council records the minutes."
    second, reason = extract.extract_chunk(StubProvider(default=json.dumps({
        "outcomes": [{"name": "Council", "requirements": [], "evidence": second_text}],
        "relations": [],
    })), "act#1", second_text)
    assert reason is None
    report = check_quote_support(
        [first, second], StubProvider(default='{"verdict":"agree"}'), shadow=True,
    )
    assert report["n_claims"] == 4
    assert report["n_documents"] == 1
    assert report["agree"] == 4
    assert capsys.readouterr().out.strip() == (
        "shadow=1 agree=4/4 disagree=0/4 undecided=0/4 "
        "n_claims=4 n_provider_errors=0 n_documents=1"
    )


def test_cli_checks_a_case_file_and_writes_nothing(tmp_path, capsys):
    """The fixture runner validates quotes, prints counts, and does not edit the file."""
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{
        "id": "act",
        "text": TEXT,
        "relations": [["Council", "issues", "Order 17"]],
    }]), encoding="utf-8")
    before = path.read_bytes()
    checker = StubProvider(default='{"verdict":"disagree"}')
    code = main([str(path)], provider=checker)
    assert code == 0
    assert path.read_bytes() == before
    assert capsys.readouterr().out.strip() == (
        "shadow=1 agree=0/3 disagree=3/3 undecided=0/3 "
        "n_claims=3 n_provider_errors=0 n_documents=1"
    )
    assert main(["/tmp/x02-no-such-cases.json"], provider=checker) == 2
