"""GJ-03: scripts/export_judge_request.py writes loom-judge/1.

§5b of docs/spec-procedure-runtime.md and signal-loom plan §3. The
specification is built in the test. The fixture request is the expected
object. Equality is on the parsed JSON, because the two layouts differ.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "export_judge_request.py"
FIXTURE_REQUEST = (
    Path(__file__).resolve().parent / "fixtures" / "loom_judge" / "request.json"
)
_SPEC_ID = "default@REQ-BRK-001"


def _case(case_id, kind, checks):
    return {
        "id": case_id,
        "verifies": ["REQ-BRK-001"],
        "preconditions": "ignition on",
        "stimulus": "the pedal moves",
        "expected_observable_result": "the lamp follows the pedal",
        "pass_criterion": "the check holds on the recording",
        "variant": "",
        "kind": kind,
        "uses": ["BrakeCmd.BrakePedal", "Lamps.BrakeLamp"],
        "checks": checks,
        "level": "software",
    }


def _brake_check(mode, when_value):
    return {
        "id": "c1",
        "when": {
            "signal": {"message": "BrakeCmd", "signal": "BrakePedal"},
            "op": "==",
            "value": when_value,
        },
        "expect": {
            "signal": {"message": "Lamps", "signal": "BrakeLamp"},
            "op": "==",
            "value": 1,
        },
        "mode": mode,
        "within_ms": [0, 100],
    }


def _brake_specification():
    """A §5 object whose cases are the fixture request, in that order."""
    return {
        "title": "Brake lamp",
        "level": "software",
        "cases": [
            _case("TC-1", "positive", [_brake_check("eventually", 1)]),
            _case("TC-2", "negative", [_brake_check("never", 1)]),
            _case("TC-3", "positive", [_brake_check("eventually", 7)]),
            _case("TC-4", "positive", []),
        ],
    }


def _export(tmp_path, specification, spec_id):
    spec_path = tmp_path / "specification.json"
    out_path = tmp_path / "request.json"
    spec_path.write_text(
        json.dumps(specification, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--spec",
            str(spec_path),
            "--spec-id",
            spec_id,
            "--out",
            str(out_path),
        ],
        check=True,
    )
    text = out_path.read_text(encoding="utf-8")
    parsed = json.loads(text)
    assert text == json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
    return parsed


def _assert_no_null(value):
    if isinstance(value, dict):
        for key, item in value.items():
            assert item is not None, key
            _assert_no_null(item)
    elif isinstance(value, list):
        for item in value:
            assert item is not None
            _assert_no_null(item)


def test_exported_request_parses_equal_to_the_fixture(tmp_path):
    exported = _export(tmp_path, _brake_specification(), _SPEC_ID)
    expected = json.loads(FIXTURE_REQUEST.read_text(encoding="utf-8"))
    assert exported == expected


def test_empty_given_null_channel_and_null_tolerance_are_omitted(tmp_path):
    specification = {
        "title": "Absent keys",
        "level": "software",
        "cases": [
            _case(
                "TC-absent",
                "positive",
                [
                    {
                        "id": "c-null",
                        "given": [],
                        "when": {
                            "signal": {
                                "message": "BrakeCmd",
                                "signal": "BrakePedal",
                                "channel": None,
                            },
                            "op": "==",
                            "value": 1,
                            "tolerance": None,
                        },
                        "expect": {
                            "signal": {
                                "message": "Lamps",
                                "signal": "BrakeLamp",
                                "channel": None,
                            },
                            "op": "!=",
                            "value": 0,
                            "tolerance": None,
                        },
                        "mode": "always",
                        "within_ms": [0, 50],
                    }
                ],
            )
        ],
    }
    exported = _export(tmp_path, specification, "default@REQ-ABSENT")
    check = exported["cases"][0]["checks"][0]
    assert list(check) == ["id", "when", "expect", "mode", "within_ms"]
    assert "given" not in check
    assert "channel" not in check["when"]["signal"]
    assert "channel" not in check["expect"]["signal"]
    assert "tolerance" not in check["when"]
    assert "tolerance" not in check["expect"]
    _assert_no_null(exported)


def test_given_channel_and_tolerance_are_kept_when_present(tmp_path):
    """channel 0 and tolerance 0 are values. They are not absent."""
    specification = {
        "title": "Present keys",
        "level": "software",
        "cases": [
            _case(
                "TC-present",
                "negative",
                [
                    {
                        "id": "c-keep",
                        "given": [
                            {
                                "signal": {
                                    "message": "Ignition",
                                    "signal": "IgnState",
                                    "channel": 0,
                                },
                                "op": "==",
                                "value": "ON",
                                "tolerance": 0,
                            }
                        ],
                        "when": {
                            "signal": {
                                "message": "BrakeCmd",
                                "signal": "BrakePedal",
                                "channel": 2,
                            },
                            "op": "==",
                            "value": 1,
                            "tolerance": 0.5,
                        },
                        "expect": {
                            "signal": {
                                "message": "Lamps",
                                "signal": "BrakeLamp",
                            },
                            "op": "==",
                            "value": 1,
                            "tolerance": 0,
                        },
                        "mode": "eventually",
                        "within_ms": [10, 100],
                    }
                ],
            )
        ],
    }
    exported = _export(tmp_path, specification, "default@REQ-KEEP")
    check = exported["cases"][0]["checks"][0]
    assert list(check) == ["id", "given", "when", "expect", "mode", "within_ms"]
    assert check["given"][0]["signal"]["channel"] == 0
    assert check["given"][0]["tolerance"] == 0
    assert check["when"]["signal"]["channel"] == 2
    assert check["when"]["tolerance"] == 0.5
    assert check["expect"]["tolerance"] == 0
    assert "channel" not in check["expect"]["signal"]
    _assert_no_null(exported)
