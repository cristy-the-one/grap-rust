"""GJ-04: import a loom-verdict/1 file onto an existing closed run.

§5b of docs/spec-procedure-runtime.md and signal-loom plan §5. The
fixture is the copied contract under tests/fixtures/loom_judge/. These
tests do not modify those files.
"""

import json
import subprocess
import sys
from pathlib import Path

import pygrap

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "import_verdicts.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "loom_judge"
_RUN_ID = "run-judge"
_CHECK_KEYS = ("id", "verdict", "anchors", "skipped", "pass", "fail", "inconclusive")
_FORBIDDEN = ("occurrences", "t_us", "value")


def _closed_run(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    workspace = pygrap.Workspace.open(str(root))
    workspace.start_run(_RUN_ID, kind="experiment", scope="procedure")
    workspace.complete_run(
        _RUN_ID,
        status="completed",
        gaps_found=0,
        explanation_hashes=[],
        summary={"procedure_status": "ok"},
    )
    return workspace, root


def _import(root, verdict, request):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verdict",
            str(verdict),
            "--request",
            str(request),
            "--run-id",
            _RUN_ID,
            "--workspace",
            str(root),
        ],
        capture_output=True,
        text=True,
    )


def _snapshot(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(root))] = path.read_bytes()
    return files


def _reason(proc):
    lines = [line for line in (proc.stdout + proc.stderr).splitlines() if line != ""]
    assert len(lines) == 1
    return lines[0]


def _payloads(workspace):
    found = []
    for event in workspace.events_for_run(_RUN_ID):
        if event.event_type != "judge_verdict":
            continue
        digest = next(
            ident for relation, kind, ident in event.links if kind == "explanation"
        )
        found.append(json.loads(workspace.get_record(digest)))
    return found


def _expected(verdict):
    log_sha256 = verdict["log"]["sha256"]
    dbc_sha256 = [item["sha256"] for item in verdict["dbc"]]
    payloads = []
    for case in verdict["cases"]:
        payloads.append(
            {
                "event_type": "judge_verdict",
                "case_id": case["case_id"],
                "verdict": case["verdict"],
                "log_sha256": log_sha256,
                "dbc_sha256": dbc_sha256,
                "checks": [
                    {key: check[key] for key in _CHECK_KEYS} for check in case["checks"]
                ],
            }
        )
    return payloads


def test_import_writes_one_judge_verdict_event_per_case(tmp_path):
    """(a) Four events, in fixture order."""
    workspace, root = _closed_run(tmp_path)
    stored = workspace.get_run(_RUN_ID)
    hashes_before = list(stored.explanation_hashes)
    verdict = json.loads((FIXTURES / "expected-verdict.json").read_text(encoding="utf-8"))
    proc = _import(root, FIXTURES / "expected-verdict.json", FIXTURES / "request.json")
    assert proc.returncode == 0, proc.stderr
    payloads = _payloads(workspace)
    assert [item["verdict"] for item in payloads] == [
        "fail",
        "fail",
        "not_exercised",
        "not_judgeable",
    ]
    assert payloads == _expected(verdict)
    counts = " ".join(f"{key}={value}" for key, value in verdict["counts"].items())
    assert proc.stdout.strip() == counts
    assert workspace.get_run(_RUN_ID).status == "completed"
    assert list(workspace.get_run(_RUN_ID).explanation_hashes) == hashes_before
    completed = [
        event.event_type
        for event in workspace.events_for_run(_RUN_ID)
        if event.event_type == "run_completed"
    ]
    assert completed == ["run_completed"]


def test_a_changed_request_byte_is_refused_and_writes_nothing(tmp_path):
    """(b) The request hash is the file bytes. One changed byte writes no event."""
    workspace, root = _closed_run(tmp_path)
    before = _snapshot(root)
    changed = tmp_path / "request.json"
    data = bytearray((FIXTURES / "request.json").read_bytes())
    data[0] ^= 0x01
    changed.write_bytes(data)
    proc = _import(root, FIXTURES / "expected-verdict.json", changed)
    assert proc.returncode != 0
    assert "request_sha256" in _reason(proc)
    assert _snapshot(root) == before
    assert _payloads(workspace) == []


def test_a_wrong_format_is_refused_and_writes_nothing(tmp_path):
    """(c) loom-verdict/2 is not this importer."""
    workspace, root = _closed_run(tmp_path)
    before = _snapshot(root)
    verdict = json.loads((FIXTURES / "expected-verdict.json").read_text(encoding="utf-8"))
    verdict["format"] = "loom-verdict/2"
    changed = tmp_path / "verdict.json"
    changed.write_text(json.dumps(verdict) + "\n", encoding="utf-8")
    proc = _import(root, changed, FIXTURES / "request.json")
    assert proc.returncode != 0
    reason = _reason(proc)
    assert "format" in reason
    assert "loom-verdict/1" in reason
    assert _snapshot(root) == before
    assert _payloads(workspace) == []


def test_judge_verdict_events_and_audit_lines_omit_evidence(tmp_path):
    """(d) Only judge_verdict events and their audit lines.

    Other events, such as procedure_check_failed, may carry a value key.
    This test does not read those.
    """
    _workspace, root = _closed_run(tmp_path)
    proc = _import(root, FIXTURES / "expected-verdict.json", FIXTURES / "request.json")
    assert proc.returncode == 0, proc.stderr
    audit = (root / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    judge_lines = []
    texts = []
    for line in audit:
        event = json.loads(line)
        if event.get("event_type") != "judge_verdict":
            continue
        judge_lines.append(line)
        digest = next(
            link["id"]
            for link in event["links"]
            if link["kind"] == "explanation"
        )
        # The explanation body is the event text. Read it back from the line's hash.
        raw = _workspace.get_record(digest)
        texts.append(raw)
    assert judge_lines
    assert len(texts) == len(judge_lines)
    for text in texts + judge_lines:
        for word in _FORBIDDEN:
            assert word not in text
