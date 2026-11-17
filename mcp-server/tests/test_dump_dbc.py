"""GJ-05: scripts/dump_dbc.py prints loom-dbc-dump/1 from the DBC reader.

§5b of docs/spec-procedure-runtime.md and signal-loom plan SL-17. The
dump is read through grap_mcp.readers.dbc. Equality is on the parsed
JSON. Numbers compare as numbers, so 1 == 1.0. A field that differs is
a finding about one of the two readers.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dump_dbc.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "loom_dbc_dump"


def _dump(dbc: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dbc", str(dbc)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _equal(got, expected) -> bool:
    if isinstance(got, bool) or isinstance(expected, bool):
        return isinstance(got, bool) and isinstance(expected, bool) and got is expected
    if isinstance(got, (int, float)) and isinstance(expected, (int, float)):
        return got == expected
    if isinstance(got, list) and isinstance(expected, list):
        return len(got) == len(expected) and all(
            _equal(left, right) for left, right in zip(got, expected)
        )
    if isinstance(got, dict) and isinstance(expected, dict):
        return got.keys() == expected.keys() and all(
            _equal(got[key], expected[key]) for key in expected
        )
    return got == expected


def _assert_dump_equals(got: dict, expected: dict) -> None:
    assert _equal(got.get("format"), expected.get("format")), (
        f"format: dump {got.get('format')!r} expected {expected.get('format')!r}"
    )
    got_signals = got["signals"]
    expected_signals = expected["signals"]
    assert len(got_signals) == len(expected_signals), (
        f"signal count: dump {len(got_signals)} expected {len(expected_signals)}"
    )
    for index, (left, right) in enumerate(zip(got_signals, expected_signals)):
        where = (
            f"{right.get('message')}.{right.get('signal')} "
            f"id={right.get('id')} index={index}"
        )
        assert left.keys() == right.keys(), f"{where} keys differ"
        for key in right:
            assert _equal(left[key], right[key]), (
                f"{where} field {key}: dump {left[key]!r} expected {right[key]!r}"
            )


def _expected(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_one_equals_one_point_zero():
    assert _equal(1, 1.0)
    assert not _equal(True, 1)


def test_hypercar_lap_dump_matches_loom_json():
    _assert_dump_equals(_dump(FIXTURES / "hypercar_lap.dbc"), _expected("hypercar_lap.json"))


def test_parity_dbc_dump_matches_loom_json():
    _assert_dump_equals(_dump(FIXTURES / "parity.dbc"), _expected("parity.json"))
