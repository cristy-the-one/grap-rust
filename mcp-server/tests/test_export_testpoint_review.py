"""TP-05 side-by-side export for Sheet 4.

Fixtures are synthetic. The automotive corpus has not been chosen and no
Sheet 4 gold exists, so these tests never point the tool at a real corpus.
"""
import csv
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import export_testpoint_review as review  # noqa: E402


_GOLD_COLUMNS = (
    "packet_id",
    "source_id",
    "item_id",
    "passage",
    "testable_as_written",
    "preconditions",
    "stimulus",
    "expected_observable_result",
    "pass_criterion",
    "variants",
    "negative_cases",
    "needs_from_elsewhere",
    "note",
)

# Labeller column, then the system's generated value, for each of the six
# free-text columns. The verdict cell is left empty.
_REVIEW_COLUMNS = (
    "packet_id",
    "source_id",
    "item_id",
    "passage",
    "preconditions",
    "system_preconditions",
    "stimulus",
    "system_stimulus",
    "expected_observable_result",
    "system_expected_observable_result",
    "pass_criterion",
    "system_pass_criterion",
    "variants",
    "system_variants",
    "negative_cases",
    "system_negative_cases",
    "verdict",
)

_HELD_PACKET = "HELD-PKT-9f3a7c"
_HELD_PASSAGE = "HELD-PASSAGE-9f3a7c"
_HELD_SYSTEM = "HELD-SYSTEM-9f3a7c"


def _write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, lineterminator="\n", extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _gold_row(packet_id, source_id, item_id, passage, **labels):
    row = {column: "" for column in _GOLD_COLUMNS}
    row["packet_id"] = packet_id
    row["source_id"] = source_id
    row["item_id"] = item_id
    row["passage"] = passage
    row.update(labels)
    return row


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames), list(reader)


def test_one_row_per_open_requirement_and_no_sealed_packet_id(tmp_path, capsys):
    """One output row per open requirement, and no held packet_id in the output.

    The six free-text columns sit beside the system's generated values.
    The verdict cell is empty. A requirement that is only in the held sheet,
    or only in the system JSON, does not grow a row.
    """
    folder = tmp_path / "packet"
    open_rows = [
        _gold_row(
            "open-1", "src-a", "req-1", "The unit shall start.",
            testable_as_written="yes",
            preconditions="ignition on",
            stimulus="press start",
            expected_observable_result="engine running",
            pass_criterion="within 2 s",
            variants="cold",
            negative_cases="no crank",
            needs_from_elsewhere="SWS_Com_00123",
            note="open note",
        ),
        _gold_row(
            "open-2", "src-b", "req-2", "The unit must stop.",
            testable_as_written="partly",
            preconditions="ignition off",
            stimulus="release",
            expected_observable_result="engine stopped",
            pass_criterion="within 1 s",
            variants="hot",
            negative_cases="restart",
            needs_from_elsewhere="none",
            note="second note",
        ),
    ]
    held = _gold_row(
        _HELD_PACKET, "held-doc", "req-9", _HELD_PASSAGE,
        preconditions=_HELD_PASSAGE,
        stimulus=_HELD_PASSAGE,
        expected_observable_result=_HELD_PASSAGE,
        pass_criterion=_HELD_PASSAGE,
        variants=_HELD_PASSAGE,
        negative_cases=_HELD_PASSAGE,
        needs_from_elsewhere=_HELD_PASSAGE,
        note=_HELD_PASSAGE,
    )
    _write_csv(folder / "open" / "testpoints.csv", _GOLD_COLUMNS, open_rows)
    _write_csv(folder / "sealed" / "testpoints.csv", _GOLD_COLUMNS, [held])
    system = {
        "testpoints": [
            {
                "source_id": "src-a",
                "item_id": "req-1",
                "needs": ["SWS_Com_00123"],
                "testable": "yes",
                "preconditions": "KL15 high",
                "stimulus": "send start frame",
                "expected_observable_result": "rpm above 0",
                "pass_criterion": "before 2000 ms",
                "variants": "minus 20 C",
                "negative_cases": "no bus traffic",
            },
            {
                "source_id": "src-b",
                "item_id": "req-2",
                "stimulus": "cut power",
            },
            {
                "source_id": "held-doc",
                "item_id": "req-9",
                "preconditions": _HELD_SYSTEM,
                "stimulus": _HELD_SYSTEM,
            },
            {
                "source_id": "extra-doc",
                "item_id": "req-x",
                "preconditions": "NOT-IN-OPEN-SHEET",
            },
        ],
    }
    system_path = tmp_path / "system.json"
    system_path.write_text(json.dumps(system), encoding="utf-8")
    out = tmp_path / "review.csv"
    review.main([
        "--gold", str(folder), "--system", str(system_path), "--out", str(out),
    ])
    captured = capsys.readouterr()
    text = out.read_text(encoding="utf-8")
    for secret in (_HELD_PACKET, _HELD_PASSAGE, _HELD_SYSTEM, "NOT-IN-OPEN-SHEET"):
        assert secret not in text
        assert secret not in captured.out
        assert secret not in captured.err
    header, rows = _read_csv(out)
    assert tuple(header) == _REVIEW_COLUMNS
    assert len(rows) == len(open_rows) == 2
    assert [row["packet_id"] for row in rows] == ["open-1", "open-2"]
    assert captured.out == "rows=2 n=2 documents=2\n"
    assert captured.err == ""

    first, second = rows
    assert first["source_id"] == "src-a"
    assert first["item_id"] == "req-1"
    assert first["passage"] == "The unit shall start."
    assert first["preconditions"] == "ignition on"
    assert first["system_preconditions"] == "KL15 high"
    assert first["stimulus"] == "press start"
    assert first["system_stimulus"] == "send start frame"
    assert first["expected_observable_result"] == "engine running"
    assert first["system_expected_observable_result"] == "rpm above 0"
    assert first["pass_criterion"] == "within 2 s"
    assert first["system_pass_criterion"] == "before 2000 ms"
    assert first["variants"] == "cold"
    assert first["system_variants"] == "minus 20 C"
    assert first["negative_cases"] == "no crank"
    assert first["system_negative_cases"] == "no bus traffic"
    assert first["verdict"] == ""

    assert second["passage"] == "The unit must stop."
    assert second["preconditions"] == "ignition off"
    assert second["system_preconditions"] == ""
    assert second["stimulus"] == "release"
    assert second["system_stimulus"] == "cut power"
    assert second["expected_observable_result"] == "engine stopped"
    assert second["system_expected_observable_result"] == ""
    assert second["pass_criterion"] == "within 1 s"
    assert second["system_pass_criterion"] == ""
    assert second["variants"] == "hot"
    assert second["system_variants"] == ""
    assert second["negative_cases"] == "restart"
    assert second["system_negative_cases"] == ""
    assert second["verdict"] == ""


def test_flat_sheet_is_open_and_held_sheet_is_unread(tmp_path, capsys):
    """A sheet sitting directly in the directory counts as open. The held sheet does not."""
    folder = tmp_path / "flat"
    _write_csv(folder / "testpoints.csv", _GOLD_COLUMNS, [
        _gold_row(
            "flat-1", "src-a", "req-1", "The lamp shall light.",
            preconditions="night",
            stimulus="toggle",
            expected_observable_result="lamp on",
            pass_criterion="steady",
        ),
    ])
    _write_csv(folder / "sealed" / "testpoints.csv", _GOLD_COLUMNS, [
        _gold_row(_HELD_PACKET, "held-doc", "req-9", _HELD_PASSAGE),
    ])
    system_path = tmp_path / "system.json"
    system_path.write_text(json.dumps({"testpoints": [
        {"source_id": "src-a", "item_id": "req-1", "preconditions": "dark"},
    ]}), encoding="utf-8")
    out = tmp_path / "review.csv"
    review.main([
        "--gold", str(folder), "--system", str(system_path), "--out", str(out),
    ])
    captured = capsys.readouterr()
    text = out.read_text(encoding="utf-8")
    assert _HELD_PACKET not in text
    assert _HELD_PASSAGE not in text
    assert _HELD_PACKET not in captured.out
    assert _HELD_PASSAGE not in captured.err
    header, rows = _read_csv(out)
    assert tuple(header) == _REVIEW_COLUMNS
    assert len(rows) == 1
    assert rows[0]["packet_id"] == "flat-1"
    assert rows[0]["preconditions"] == "night"
    assert rows[0]["system_preconditions"] == "dark"
    assert rows[0]["system_stimulus"] == ""
    assert rows[0]["verdict"] == ""
    assert captured.out == "rows=1 n=1 documents=1\n"


def test_held_directory_itself_is_not_read(tmp_path, capsys):
    """Pointing the tool at the held directory exports nothing from it."""
    held = tmp_path / "sealed"
    _write_csv(held / "testpoints.csv", _GOLD_COLUMNS, [
        _gold_row(_HELD_PACKET, "held-doc", "req-9", _HELD_PASSAGE),
    ])
    system_path = tmp_path / "system.json"
    system_path.write_text("{}", encoding="utf-8")
    out = tmp_path / "review.csv"
    review.main(["--gold", str(held), "--system", str(system_path), "--out", str(out)])
    captured = capsys.readouterr()
    text = out.read_text(encoding="utf-8")
    assert _HELD_PACKET not in text
    assert _HELD_PASSAGE not in text
    assert _HELD_PACKET not in captured.out
    header, rows = _read_csv(out)
    assert tuple(header) == _REVIEW_COLUMNS
    assert rows == []
    assert captured.out == "rows=0 n=0 documents=0\n"
