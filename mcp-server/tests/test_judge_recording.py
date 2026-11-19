"""RUN-02: scripts/judge_recording.py chains export, loom-judge, and import.

The loom-judge binary is a fake this file writes. Its shebang is this
interpreter, and the mode comes from LOOM_JUDGE_MODE: ok, refuse, or crash.
The copied loom_judge fixtures are not modified.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pygrap

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "judge_recording.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "loom_judge"
_RUN_ID = "run-judge"
_UNKNOWN_RUN_ID = "run-missing"
_MODE = "LOOM_JUDGE_MODE"
_REFUSE_LINES = (
    "request refused: unknown key",
    "request refused: missing case",
)
_SPEC_ID = "default@REQ-BRK-001"


def _specification():
    return {
        "title": "Brake lamp",
        "level": "software",
        "cases": [
            {
                "id": "TC-1",
                "verifies": ["REQ-BRK-001"],
                "kind": "positive",
                "checks": [],
            }
        ],
    }


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


def _write_fake(path: Path) -> None:
    """A loom-judge stand-in. The kernel runs it with sys.executable."""
    fixture = FIXTURES / "expected-verdict.json"
    path.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import argparse",
                "import hashlib",
                "import json",
                "import os",
                "import sys",
                "from pathlib import Path",
                "",
                f"_FIXTURE = Path({str(fixture)!r})",
                f"_REFUSE = {list(_REFUSE_LINES)!r}",
                "",
                "def main():",
                "    parser = argparse.ArgumentParser()",
                '    parser.add_argument("--log", required=True)',
                '    parser.add_argument("--dbc", action="append", required=True)',
                '    parser.add_argument("--request", required=True)',
                '    parser.add_argument("--out", required=True)',
                "    args = parser.parse_args()",
                f'    mode = os.environ.get({_MODE!r})',
                '    if mode == "ok":',
                '        verdict = json.loads(_FIXTURE.read_text(encoding="utf-8"))',
                "        digest = hashlib.sha256(Path(args.request).read_bytes()).hexdigest()",
                '        verdict["request_sha256"] = digest',
                '        text = json.dumps(verdict, indent=2, ensure_ascii=False) + "\\n"',
                '        Path(args.out).write_text(text, encoding="utf-8")',
                "        return 0",
                '    if mode == "refuse":',
                "        for line in _REFUSE:",
                "            print(line, file=sys.stderr)",
                "        return 2",
                '    if mode == "crash":',
                "        return 1",
                '    print(f"unknown mode {mode!r}", file=sys.stderr)',
                "    return 1",
                "",
                'if __name__ == "__main__":',
                "    raise SystemExit(main())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _snapshot(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(root))] = path.read_bytes()
    return files


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


def _run(tmp_path, root, mode):
    spec_path = tmp_path / "specification.json"
    spec_path.write_text(
        json.dumps(_specification(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    fake = tmp_path / "fake_loom_judge.py"
    _write_fake(fake)
    out = tmp_path / "out"
    out.mkdir()
    env = dict(os.environ)
    env[_MODE] = mode
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--spec",
            str(spec_path),
            "--spec-id",
            _SPEC_ID,
            "--log",
            str(FIXTURES / "brake.asc"),
            "--dbc",
            str(FIXTURES / "brake.dbc"),
            "--dbc",
            str(FIXTURES / "brake.dbc") + "@1",
            "--loom-judge",
            str(fake),
            "--run-id",
            _RUN_ID,
            "--workspace",
            str(root),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        env=env,
    ), out


def test_ok_writes_four_judge_verdict_events_and_prints_the_counts_line(tmp_path):
    """(a) Four judge_verdict events, and the counts line is the whole print."""
    workspace, root = _closed_run(tmp_path)
    proc, out = _run(tmp_path, root, "ok")
    assert proc.returncode == 0, proc.stderr
    verdict = json.loads((FIXTURES / "expected-verdict.json").read_text(encoding="utf-8"))
    payloads = _payloads(workspace)
    assert [item["case_id"] for item in payloads] == [
        case["case_id"] for case in verdict["cases"]
    ]
    assert [item["verdict"] for item in payloads] == [
        case["verdict"] for case in verdict["cases"]
    ]
    assert len(payloads) == len(verdict["cases"])
    counts = " ".join(f"{key}={value}" for key, value in verdict["counts"].items())
    assert proc.stdout.strip() == counts
    stored = (out / "verdict.json").read_text(encoding="utf-8")
    assert "occurrences" in stored
    printed = proc.stdout + proc.stderr
    assert "occurrences" not in printed
    assert "window_past_end" not in printed


def test_refuse_exits_2_prints_both_lines_and_writes_no_events(tmp_path):
    """(b) Exit 2 reprints both stderr lines. Nothing is imported."""
    workspace, root = _closed_run(tmp_path)
    before = _snapshot(root)
    proc, _out = _run(tmp_path, root, "refuse")
    assert proc.returncode == 2, proc.stderr
    assert proc.stderr.splitlines() == list(_REFUSE_LINES)
    assert proc.stdout == ""
    assert _snapshot(root) == before
    assert _payloads(workspace) == []


def _write_recording_fake(path: Path, marker: Path) -> None:
    """A loom-judge stand-in that records that it was started."""
    path.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "from pathlib import Path",
                f"Path({str(marker)!r}).write_text('ran\\n', encoding='utf-8')",
                "raise SystemExit(0)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_unknown_run_exits_1_without_starting_loom_judge(tmp_path):
    """An unknown run id exits 1 with one line. The binary is not started."""
    root = tmp_path / "ws"
    root.mkdir()
    pygrap.Workspace.open(str(root))
    before = _snapshot(root)
    marker = tmp_path / "loom-judge-ran"
    fake = tmp_path / "fake_loom_judge.py"
    _write_recording_fake(fake, marker)
    spec_path = tmp_path / "specification.json"
    spec_path.write_text(
        json.dumps(_specification(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    out.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--spec",
            str(spec_path),
            "--spec-id",
            _SPEC_ID,
            "--log",
            str(FIXTURES / "brake.asc"),
            "--dbc",
            str(FIXTURES / "brake.dbc"),
            "--loom-judge",
            str(fake),
            "--run-id",
            _UNKNOWN_RUN_ID,
            "--workspace",
            str(root),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr.splitlines() == [
        f"run {_UNKNOWN_RUN_ID!r} is not in the workspace"
    ]
    assert marker.is_file() is False
    assert _snapshot(root) == before


def test_crash_exits_1_and_writes_no_events(tmp_path):
    """(c) Any failure other than a refusal exits 1 and imports nothing."""
    workspace, root = _closed_run(tmp_path)
    before = _snapshot(root)
    proc, _out = _run(tmp_path, root, "crash")
    assert proc.returncode == 1, proc.stderr
    assert proc.stderr.splitlines() == ["loom-judge failed (exit 1)"]
    assert proc.stdout == ""
    assert _snapshot(root) == before
    assert _payloads(workspace) == []
