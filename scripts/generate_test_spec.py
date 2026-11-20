#!/usr/bin/env python3
"""Generate a test specification from a built automotive graph.

    python scripts/generate_test_spec.py --graph DIR --workspace DIR --run-id ID \
        [--scope-ids ID[,ID...]] --provider stub|local --stub-reply FILE --out DIR

``--graph`` is the ``--out`` directory of ``scripts/build_automotive_graph.py``
(``graph/`` and ``projection.json``). The graph and the projection are loaded
with that script's loader, including its label check. ``stub`` reads one §5
JSON reply from ``--stub-reply``. ``local`` is ``OpenAICompatProvider()``.
Emitted documents go to ``<out>/emitted``. When the recorded status is ``ok``,
the last draft is written to ``<out>/specification.json`` and the testpoints
document to ``<out>/testpoints.json``. Otherwise those files are not written.
Requirement text, the prompt, and the reply are not printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_automotive_graph as graph_cmd  # noqa: E402

import pygrap  # noqa: E402
from grap_mcp.kee.llm import OpenAICompatProvider, StubProvider  # noqa: E402
from grap_mcp.procedure_context import as_testpoints, fetch_context, format_summary  # noqa: E402
from grap_mcp.procedure_model import run_generate_test_spec  # noqa: E402

_POSITIVE_FIELDS = (
    "preconditions",
    "stimulus",
    "expected_observable_result",
    "pass_criterion",
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.provider == "stub" and args.stub_reply is None:
        parser.error("--stub-reply is required with --provider stub")
    try:
        return _execute(args)
    except graph_cmd.ProjectionMismatch as exc:
        print(str(exc))
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a test specification from a built automotive graph.",
    )
    parser.add_argument(
        "--graph",
        required=True,
        type=Path,
        help="Output directory of build_automotive_graph.py (graph/ and projection.json)",
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--scope-ids",
        help="Requirement ids, separated by commas",
    )
    parser.add_argument("--provider", required=True, choices=("stub", "local"))
    parser.add_argument(
        "--stub-reply",
        type=Path,
        help="§5 JSON reply. Required when --provider is stub",
    )
    parser.add_argument("--out", required=True, type=Path)
    return parser


def _execute(args) -> int:
    kg, projection = graph_cmd.load_graph_and_projection(Path(args.graph) / "graph")
    workspace_dir = Path(args.workspace)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    workspace = pygrap.Workspace.open(str(workspace_dir))
    out = Path(args.out)
    try:
        result = run_generate_test_spec(
            kg,
            projection,
            workspace,
            workspace_dir,
            out / "emitted",
            run_id=args.run_id,
            scope=_scope(args.scope_ids),
            provider=_provider(args),
        )
    except RuntimeError as exc:
        # The provider's own text can quote the reply. Print only the class.
        print(f"model_error finish_reason={_model_finish_reason(exc)}")
        return 1
    recorded = result.recorded
    if recorded.status == "ok":
        out.mkdir(parents=True, exist_ok=True)
        spec_path = out / "specification.json"
        draft = result.drafts[-1]
        spec_path.write_text(
            json.dumps(draft, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        document = _testpoints(kg, projection, recorded, draft)
        (out / "testpoints.json").write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"{args.run_id} {spec_path}")
        print(format_summary(document))
        return 0
    print(f"{recorded.status} n_issues={_issue_count(recorded)}")
    return 1


def _testpoints(kg, projection, recorded, draft) -> dict:
    """needs from the run's fetched requirements, plus the last draft's text."""
    requirements = [
        {"key": group["key"]}
        for group in (recorded.evidence or {}).get("requirements") or []
    ]
    document = as_testpoints(projection, fetch_context(kg, projection, requirements))
    cases = draft["cases"]
    for row in document["testpoints"]:
        matched = [case for case in cases if row["item_id"] in case["verifies"]]
        positive = [case for case in matched if case["kind"] == "positive"]
        for field in _POSITIVE_FIELDS:
            row[field] = _distinct(positive, field)
        row["variants"] = _distinct(matched, "variant")
        row["negative_cases"] = _negative_stimuli(matched)
    return document


def _distinct(cases, field: str) -> str:
    seen: list[str] = []
    for case in cases:
        value = case.get(field)
        if not isinstance(value, str) or value == "" or value in seen:
            continue
        seen.append(value)
    return " | ".join(seen)


def _negative_stimuli(cases) -> str:
    found = []
    for case in cases:
        if case.get("kind") != "negative":
            continue
        value = case.get("stimulus")
        if not isinstance(value, str) or value == "":
            continue
        found.append(value)
    return " | ".join(found)


def _model_finish_reason(exc: BaseException) -> str:
    if "finish_reason='length'" in str(exc):
        return "length"
    return "other"


def _provider(args):
    if args.provider == "local":
        return OpenAICompatProvider()
    text = Path(args.stub_reply).read_text(encoding="utf-8")
    return StubProvider(responses=[text])


def _scope(raw: str | None):
    if raw is None:
        return None
    ids = [part.strip() for part in raw.split(",") if part.strip() != ""]
    if not ids:
        return None
    return {"ids": ids}


def _issue_count(recorded) -> int:
    """Issues on the last failed validation. Their text is not used."""
    failed = [
        event
        for event in recorded.events
        if event.get("event_type") == "procedure_check_failed"
    ]
    if not failed:
        return 0
    last = max(event["iteration"] for event in failed)
    return sum(1 for event in failed if event["iteration"] == last)


if __name__ == "__main__":
    sys.exit(main())
