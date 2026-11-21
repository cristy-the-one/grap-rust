"""RUN-14: a verdict counts only from a check a person approved.

Synthetic run directories only. No corpus, no network. The names are made
up for these tests.
"""

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "review_checks.py"
_SENTINEL = "SENTINEL-REQUIREMENT-TEXT"

_DBC = """\
VERSION "SYN-14"

NS_ :

BS_:

BU_: NodeA

BO_ 100 Box: 8 NodeA
 SG_ Side : 0|8@1+ (1,0) [0|7] "" NodeA

VAL_ 100 Side 3 "none" ;
"""


def _check(check_id, *, expect_value=3, expect_op="!=", when_value=1):
    return {
        "id": check_id,
        "given": [
            {
                "signal": {"message": "Box", "signal": "Side"},
                "op": "==",
                "value": 0,
            }
        ],
        "when": {
            "signal": {"message": "Box", "signal": "Side"},
            "op": "==",
            "value": when_value,
        },
        "expect": {
            "signal": {"message": "Box", "signal": "Side"},
            "op": expect_op,
            "value": expect_value,
        },
        "mode": "eventually",
        "within_ms": [0, 200],
    }


def _case(case_id, checks, requirement="REQ-SYN-1"):
    return {
        "id": case_id,
        "verifies": [requirement],
        "preconditions": "",
        "stimulus": _SENTINEL,
        "expected_observable_result": "",
        "pass_criterion": "",
        "variant": "",
        "kind": "positive",
        "uses": ["Box.Side"],
        "checks": checks,
    }


def _verdict(rows):
    counts = {
        "pass": sum(1 for _, verdict in rows if verdict == "pass"),
        "fail": sum(1 for _, verdict in rows if verdict == "fail"),
        "cases": len(rows),
        "not_exercised": sum(1 for _, verdict in rows if verdict == "not_exercised"),
        "not_judgeable": sum(1 for _, verdict in rows if verdict == "not_judgeable"),
        "inconclusive": sum(1 for _, verdict in rows if verdict == "inconclusive"),
    }
    return {
        "format": "loom-verdict/1",
        "counts": counts,
        "cases": [
            {"case_id": case_id, "verdict": verdict, "checks": []}
            for case_id, verdict in rows
        ],
    }


def _reverse_keys(value):
    if isinstance(value, dict):
        return {key: _reverse_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reverse_keys(item) for item in value]
    return value


def _write_run(tmp_path, cases, verdict=None, dbc=None, reorder=False):
    run = tmp_path / "run-syn"
    (run / "spec-out").mkdir(parents=True)
    (run / "in").mkdir()
    payload = {"title": "Synthetic", "cases": cases}
    if reorder:
        payload = _reverse_keys(payload)
    (run / "spec-out" / "specification.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if verdict is not None:
        (run / "judge-out").mkdir()
        (run / "judge-out" / "verdict.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if dbc is not None:
        (run / "in" / "synth.dbc").write_text(dbc, encoding="utf-8")
    (run / "report.md").write_text("kept line\n", encoding="utf-8")
    return run


def _sha(check):
    payload = json.dumps(check, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _invoke(run, *args, approvals=None):
    command = [sys.executable, str(SCRIPT), "--run", str(run)]
    if approvals is not None:
        command.extend(["--approvals", str(approvals)])
    command.extend(args)
    return subprocess.run(command, capture_output=True, text=True)


def test_a_approve_then_list_is_approved_until_the_check_value_changes(tmp_path):
    """Approving a check marks that hash approved. A changed value is a new hash."""
    check = _check("chk-1", expect_value=3)
    run = _write_run(tmp_path, [_case("CASE-A", [check])])
    approvals = tmp_path / "approved-checks.json"
    listed = _invoke(run, "--approve", "CASE-A:chk-1", "--list", approvals=approvals)
    assert listed.returncode == 0, listed.stderr
    digest = _sha(check)
    assert f"CASE-A chk-1 sha256={digest[:12]}  [approved]" in listed.stdout
    assert _SENTINEL not in listed.stdout
    stored = json.loads(approvals.read_text(encoding="utf-8"))
    assert stored == {
        "format": "grap-check-review/1",
        "checks": [{
            "requirement": "REQ-SYN-1",
            "case": "CASE-A",
            "check": "chk-1",
            "sha256": digest,
            "decision": "approved",
            "at": date.today().isoformat(),
        }],
    }
    assert _SENTINEL not in approvals.read_text(encoding="utf-8")

    spec_path = run / "spec-out" / "specification.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["cases"][0]["checks"][0]["expect"]["value"] = 4
    spec_path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    again = _invoke(run, "--list", approvals=approvals)
    assert again.returncode == 0, again.stderr
    changed = spec["cases"][0]["checks"][0]
    assert _sha(changed) != digest
    assert f"sha256={_sha(changed)[:12]}  [unreviewed]" in again.stdout
    assert "[approved]" not in again.stdout


def test_b_hash_is_stable_under_key_order(tmp_path):
    """sha256 uses sorted keys, so object key order does not change it."""
    check = _check("chk-1")
    run_a = _write_run(tmp_path / "a", [_case("CASE-A", [check])], reorder=False)
    run_b = _write_run(tmp_path / "b", [_case("CASE-A", [check])], reorder=True)
    raw_a = (run_a / "spec-out" / "specification.json").read_text(encoding="utf-8")
    raw_b = (run_b / "spec-out" / "specification.json").read_text(encoding="utf-8")
    assert raw_a != raw_b
    list_a = _invoke(run_a, "--list")
    list_b = _invoke(run_b, "--list")
    assert list_a.returncode == 0, list_a.stderr
    assert list_b.returncode == 0, list_b.stderr
    prefix = _sha(check)[:12]
    assert f"sha256={prefix}  [unreviewed]" in list_a.stdout
    assert f"sha256={prefix}  [unreviewed]" in list_b.stdout


def test_c_value_table_name_is_printed_beside_the_raw_value(tmp_path):
    """A made-up DBC VAL_ name is printed with that raw value."""
    check = _check("chk-1", expect_value=3, expect_op="!=")
    run = _write_run(tmp_path, [_case("CASE-A", [check])], dbc=_DBC)
    listed = _invoke(run, "--list")
    assert listed.returncode == 0, listed.stderr
    assert "!= 3 (none)" in listed.stdout
    assert "== 0 (" not in listed.stdout
    assert "== 1 (" not in listed.stdout
    assert "within 0–200 ms" in listed.stdout


def test_d_verdicts_count_only_the_fully_approved_case(tmp_path):
    """One approved case is the headline. The other stays unreviewed."""
    cases = [
        _case("CASE-A", [_check("chk-a", expect_value=1, expect_op="==")]),
        _case("CASE-B", [_check("chk-b", expect_value=2, expect_op="==")]),
    ]
    run = _write_run(
        tmp_path,
        cases,
        verdict=_verdict([("CASE-A", "pass"), ("CASE-B", "fail")]),
    )
    done = _invoke(run, "--approve", "CASE-A:chk-a", approvals=tmp_path / "approved-checks.json")
    assert done.returncode == 0, done.stderr
    report = (run / "report.md").read_text(encoding="utf-8")
    assert report.startswith("kept line\n")
    assert "### scorecard (after review)" in report
    assert "verdicts=1 of 2 cases (pass=1 fail=0), reviewed checks only" in report
    assert "unreviewed_verdicts=1 (pass=0 fail=1)" in report
    assert _SENTINEL not in report


def test_e_a_case_with_one_unreviewed_check_is_unreviewed(tmp_path):
    """Every check must be approved. One unreviewed check keeps the case out."""
    checks = [
        _check("chk-1", expect_value=1, expect_op="=="),
        _check("chk-2", expect_value=2, expect_op="=="),
    ]
    run = _write_run(
        tmp_path,
        [_case("CASE-A", checks)],
        verdict=_verdict([("CASE-A", "fail")]),
    )
    done = _invoke(run, "--approve", "CASE-A:chk-1", approvals=tmp_path / "approved-checks.json")
    assert done.returncode == 0, done.stderr
    report = (run / "report.md").read_text(encoding="utf-8")
    assert "verdicts=0 of 1 cases (pass=0 fail=0), reviewed checks only" in report
    assert "unreviewed_verdicts=1 (pass=0 fail=1)" in report


def test_f_missing_approvals_file_reviews_nothing(tmp_path):
    """No approvals file means the headline reviews nothing."""
    cases = [
        _case("CASE-A", [_check("chk-a")]),
        _case("CASE-B", [_check("chk-b", expect_value=4)]),
    ]
    run = _write_run(
        tmp_path,
        cases,
        verdict=_verdict([("CASE-A", "pass"), ("CASE-B", "fail")]),
    )
    default = run.parent / "approved-checks.json"
    assert not default.exists()
    shown = _invoke(run, "--scorecard")
    assert shown.returncode == 0, shown.stderr
    assert shown.stdout.splitlines() == [
        "verdicts=0 of 2 cases (pass=0 fail=0), reviewed checks only",
        "unreviewed_verdicts=2 (pass=1 fail=1)",
    ]
    assert not default.exists()
    report = (run / "report.md").read_text(encoding="utf-8")
    assert "scorecard (after review)" not in report


def test_later_decision_on_the_same_hash_replaces_the_entry(tmp_path):
    """One entry per hash. A later decision on that hash replaces it."""
    check = _check("chk-1")
    run = _write_run(tmp_path, [_case("CASE-A", [check])])
    approvals = tmp_path / "approved-checks.json"
    done = _invoke(
        run,
        "--approve",
        "CASE-A:chk-1",
        "--reject",
        "CASE-A:chk-1",
        "--list",
        approvals=approvals,
    )
    assert done.returncode == 0, done.stderr
    assert f"sha256={_sha(check)[:12]}  [rejected]" in done.stdout
    assert "[approved]" not in done.stdout
    stored = json.loads(approvals.read_text(encoding="utf-8"))
    assert len(stored["checks"]) == 1
    assert stored["checks"][0]["decision"] == "rejected"
    assert stored["checks"][0]["sha256"] == _sha(check)
