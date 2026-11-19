"""DRY-01: the P-17 and GJ-06 path on a synthetic brake-lamp corpus.

Nothing in this file is from a customer. The DBC is the committed
loom_judge copy. The requirement sentence names both signals by DBC id.
"""

import json
import os
import stat
import sys
from pathlib import Path

import pygrap

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_automotive_graph  # noqa: E402
import generate_test_spec  # noqa: E402
import judge_recording  # noqa: E402
from export_judge_request import export_request  # noqa: E402

from grap_mcp.kee.working_graph import _relation_filter  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_REQIF = _FIXTURES / "rehearsal" / "requirements.reqif"
_DBC = _FIXTURES / "loom_judge" / "brake.dbc"
_REQUEST = _FIXTURES / "loom_judge" / "request.json"
_VERDICT = _FIXTURES / "loom_judge" / "expected-verdict.json"
_LOG = _FIXTURES / "loom_judge" / "brake.asc"
_SENTENCE = (
    "When BrakeCmd.BrakePedal becomes 1, Lamps.BrakeLamp shall become 1 within 100 ms."
)
_REQUIREMENT_ID = "REQ-BRK-001"
_SPEC_ID = "default@REQ-BRK-001"
_RUN_ID = "rehearsal"
_RELEASE = "R1"
_PEDAL = "BrakeCmd.BrakePedal"
_LAMP = "Lamps.BrakeLamp"
_MODE = "LOOM_JUDGE_MODE"
_STATEMENT_LINKS = ("about", "refers_to")


def _signal_key(projection, native_id: str) -> str:
    found = [
        node["key"]
        for node in projection["nodes"]
        if node.get("type") == "Signal" and node.get("native_id") == native_id
    ]
    assert len(found) == 1, (native_id, len(found), found)
    return found[0]


def _hop(method, node: int, relation: str) -> list[int]:
    name, value = _relation_filter(relation)
    found = []
    seen = set()
    for nxt, _strength in method(node, name, value):
        if nxt in seen:
            continue
        seen.add(nxt)
        found.append(nxt)
    return found


def _statement_signal_keys(kg, projection, requirement_id: str) -> list[str]:
    """Signal keys reached by has_statement, then about or refers_to."""
    by_engine = {node["engine_id"]: node for node in projection["nodes"]}
    requirements = [
        node
        for node in projection["nodes"]
        if node.get("type") == "Requirement" and node.get("native_id") == requirement_id
    ]
    assert len(requirements) == 1, (requirement_id, len(requirements))
    keys = []
    for statement in _hop(
        kg.neighbors_by_relation, requirements[0]["engine_id"], "has_statement",
    ):
        if kg.get_node_label(statement) != "Statement":
            continue
        for relation in _STATEMENT_LINKS:
            for hop_id in _hop(kg.neighbors_by_relation, statement, relation):
                node = by_engine.get(hop_id)
                if node is not None and node.get("type") == "Signal":
                    keys.append(node["key"])
    return keys


def _condition(message: str, signal: str, value: int) -> dict:
    return {
        "signal": {"message": message, "signal": signal, "channel": None},
        "op": "==",
        "value": value,
        "tolerance": None,
    }


def _check(mode: str, pedal_value: int) -> dict:
    return {
        "id": "c1",
        "given": [],
        "when": _condition("BrakeCmd", "BrakePedal", pedal_value),
        "expect": _condition("Lamps", "BrakeLamp", 1),
        "mode": mode,
        "within_ms": [0, 100],
    }


def _case(case_id: str, kind: str, checks: list, uses: list) -> dict:
    return {
        "id": case_id,
        "verifies": [_REQUIREMENT_ID],
        "preconditions": "power is on",
        "stimulus": "the pedal is pressed",
        "expected_observable_result": "the lamp is lit",
        "pass_criterion": "the lamp is lit in the window",
        "variant": "",
        "kind": kind,
        "uses": uses,
        "checks": checks,
    }


def _reply(pedal_key: str, lamp_key: str) -> dict:
    uses = [pedal_key, lamp_key]
    return {
        "title": "Brake lamp",
        "cases": [
            _case("TC-1", "positive", [_check("eventually", 1)], uses),
            _case("TC-2", "negative", [_check("never", 1)], uses),
            _case("TC-3", "positive", [_check("eventually", 7)], uses),
            _case("TC-4", "positive", [], uses),
        ],
    }


def _captured(capsys) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def _assert_silent(combined: str, extra: str = "") -> None:
    assert _SENTENCE not in combined, combined
    if extra != "":
        assert extra not in combined


def _issues(workspace_dir: Path) -> list[tuple]:
    workspace = pygrap.Workspace.open(str(workspace_dir))
    found = []
    for event in workspace.events_for_run(_RUN_ID):
        if event.event_type != "procedure_check_failed":
            continue
        digest = next(
            ident for _relation, kind, ident in event.links if kind == "explanation"
        )
        payload = json.loads(workspace.get_record(digest))
        found.append((
            payload.get("iteration"),
            payload.get("case"),
            payload.get("check"),
            payload.get("value"),
        ))
    return found


def _write_fake(path: Path) -> None:
    """RUN-02's loom-judge stand-in. ok mode copies expected-verdict.json."""
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
                f"_FIXTURE = Path({str(_VERDICT)!r})",
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


def _verdicts(workspace_dir: Path) -> list[str]:
    workspace = pygrap.Workspace.open(str(workspace_dir))
    found = []
    for event in workspace.events_for_run(_RUN_ID):
        if event.event_type != "judge_verdict":
            continue
        digest = next(
            ident for _relation, kind, ident in event.links if kind == "explanation"
        )
        payload = json.loads(workspace.get_record(digest))
        found.append(payload["verdict"])
    return found


def test_rehearsal_exports_the_judge_request_and_imports_four_verdicts(tmp_path, capsys):
    """Walk build, stub spec, export, and the fake loom-judge on synthetic data."""
    graph_out = tmp_path / "graph-out"
    code = build_automotive_graph.main([
        "--reqif", str(_REQIF),
        "--dbc", str(_DBC),
        "--release", _RELEASE,
        "--out", str(graph_out),
    ])
    build_out = _captured(capsys)
    _assert_silent(build_out)
    assert code == 0, build_out

    projection = json.loads((graph_out / "projection.json").read_text(encoding="utf-8"))
    pedal_key = _signal_key(projection, _PEDAL)
    lamp_key = _signal_key(projection, _LAMP)
    kg, loaded = build_automotive_graph.load_graph_and_projection(graph_out / "graph")
    linked = _statement_signal_keys(kg, loaded, _REQUIREMENT_ID)
    mention_lines = [line for line in build_out.splitlines() if "mention" in line]
    assert pedal_key in linked, (linked, mention_lines)
    assert lamp_key in linked, (linked, mention_lines)

    reply = _reply(pedal_key, lamp_key)
    reply_path = tmp_path / "reply.json"
    reply_text = json.dumps(reply)
    reply_path.write_text(reply_text, encoding="utf-8")

    workspace = tmp_path / "ws"
    spec_out = tmp_path / "spec-out"
    code = generate_test_spec.main([
        "--graph", str(graph_out),
        "--workspace", str(workspace),
        "--run-id", _RUN_ID,
        "--scope-ids", _REQUIREMENT_ID,
        "--provider", "stub",
        "--stub-reply", str(reply_path),
        "--out", str(spec_out),
    ])
    generated = _captured(capsys)
    _assert_silent(generated, reply_text)
    assert code == 0, (generated, _issues(workspace))

    spec = json.loads((spec_out / "specification.json").read_text(encoding="utf-8"))
    exported = export_request(spec, _SPEC_ID)
    expected = json.loads(_REQUEST.read_text(encoding="utf-8"))
    assert exported == expected

    fake = tmp_path / "fake_loom_judge.py"
    _write_fake(fake)
    judge_out = tmp_path / "judge-out"
    previous = os.environ.get(_MODE)
    os.environ[_MODE] = "ok"
    try:
        judge_recording.main([
            "--spec", str(spec_out / "specification.json"),
            "--spec-id", _SPEC_ID,
            "--log", str(_LOG),
            "--dbc", str(_DBC),
            "--loom-judge", str(fake),
            "--run-id", _RUN_ID,
            "--workspace", str(workspace),
            "--out", str(judge_out),
        ])
    finally:
        if previous is None:
            os.environ.pop(_MODE, None)
        else:
            os.environ[_MODE] = previous
    judged = _captured(capsys)
    _assert_silent(judged, reply_text)
    verdicts = _verdicts(workspace)
    assert len(verdicts) == 4
    assert verdicts == ["fail", "fail", "not_exercised", "not_judgeable"]
