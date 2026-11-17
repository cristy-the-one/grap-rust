#!/usr/bin/env python3
"""Import a loom-verdict/1 file onto an existing closed run.

    python scripts/import_verdicts.py --verdict FILE --request FILE --run-id ID --workspace DIR

``--request`` is the exported loom-judge/1 file that was judged. Its
sha256 is the hash of those file bytes. The file is not parsed and
written back out before hashing.

Two refusals each exit non-zero, write no event, and print one line
naming the reason: the format is not ``loom-verdict/1``, or
``request_sha256`` does not match the request file.

Each case in the verdict becomes one ``judge_verdict`` event. The run
is not completed again. ``occurrences``, ``t_us``, and sample values
stay in the verdict file. The last line is the verdict's ``counts``
object as ``key=value`` pairs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MCP = _ROOT / "mcp-server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

import pygrap  # noqa: E402

from grap_mcp.procedure_events import keep_event  # noqa: E402

_FORMAT = "loom-verdict/1"
_CHECK_KEYS = ("id", "verdict", "anchors", "skipped", "pass", "fail", "inconclusive")


class Refusal(Exception):
    """The verdict file was not imported. Nothing was written."""


def import_verdicts(verdict_path, request_path, run_id: str, workspace_dir) -> str:
    """Write one judge_verdict event per case. Return the counts line."""
    verdict = json.loads(Path(verdict_path).read_text(encoding="utf-8"))
    if not isinstance(verdict, dict) or verdict.get("format") != _FORMAT:
        found = verdict.get("format") if isinstance(verdict, dict) else None
        raise Refusal(f"format is {found!r}, not {_FORMAT}")
    request_hash = hashlib.sha256(Path(request_path).read_bytes()).hexdigest()
    if verdict.get("request_sha256") != request_hash:
        raise Refusal("request_sha256 does not match the request file")
    payloads = [_event(verdict, case) for case in verdict["cases"]]
    workspace = pygrap.Workspace.open(str(workspace_dir))
    if workspace.get_run(run_id) is None:
        raise Refusal(f"run {run_id!r} is not in the workspace")
    for payload in payloads:
        keep_event(workspace, workspace_dir, run_id, payload)
    counts = verdict["counts"]
    return " ".join(f"{key}={counts[key]}" for key in counts)


def _event(verdict: dict, case: dict) -> dict:
    return {
        "event_type": "judge_verdict",
        "case_id": case["case_id"],
        "verdict": case["verdict"],
        "log_sha256": verdict["log"]["sha256"],
        "dbc_sha256": [item["sha256"] for item in verdict["dbc"]],
        "checks": [
            {key: check[key] for key in _CHECK_KEYS} for check in case["checks"]
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Import a loom-verdict/1 file onto an existing closed run.",
    )
    parser.add_argument("--verdict", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        line = import_verdicts(args.verdict, args.request, args.run_id, args.workspace)
    except Refusal as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(line)


if __name__ == "__main__":
    main()
