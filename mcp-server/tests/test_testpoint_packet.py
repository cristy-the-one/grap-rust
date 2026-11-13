"""TP-03 Sheet 4 packet generator.

Synthetic fixtures only. The automotive corpus has not been chosen, so these
tests never point the generator at a real corpus.
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import make_testpoint_packet as packet  # noqa: E402


COLUMNS = [
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
]
LABEL_COLUMNS = COLUMNS[4:]
_MAX_PER_SOURCE = 4

_KINDS = {
    "requirement": ("_sot_requirement", "_ad_requirement"),
    "heading": ("_sot_heading", "_ad_heading"),
    "definition": ("_sot_definition", "_ad_definition"),
}

DBC_WITH_SHALL = """\
VERSION "REL-2026.11"

NS_ :

BS_:

BU_: Engine Gateway

BO_ 256 EngineData: 8 Engine
 SG_ EngineSpeed : 0|16@1+ (1,0) [0|8000] "rpm" Gateway

CM_ BO_ 256 "The node shall transmit";
"""


def source_rank(seed, source_id):
    return hashlib.sha256(f"{seed}{source_id}".encode("utf-8")).hexdigest()


def item_rank(seed, source_id, item_id):
    return hashlib.sha256(f"{seed}{source_id}{item_id}".encode("utf-8")).hexdigest()


def select(eligible, seed, items):
    """G-01 selection with the source as the unit: hash order, cap 4, every fourth sealed."""
    ordered = sorted(eligible, key=lambda source_id: (source_rank(seed, source_id), source_id))
    chosen = []
    remaining = items
    for index, source_id in enumerate(ordered):
        if remaining <= 0:
            break
        ranked = sorted(
            eligible[source_id],
            key=lambda item_id: (item_rank(seed, source_id, item_id), item_id),
        )
        take = ranked[: min(_MAX_PER_SOURCE, remaining)]
        chosen.append((source_id, take, (index + 1) % 4 == 0))
        remaining -= len(take)
    return chosen, len(ordered)


def expected_report(chosen, n_sources, items):
    n_rows = sum(len(taken) for _source, taken, _sealed in chosen)
    sealed_rows = sum(len(taken) for _source, taken, sealed in chosen if sealed)
    open_rows = n_rows - sealed_rows
    row_docs = sum(1 for _source, taken, _sealed in chosen if taken)
    sealed_docs = sum(1 for _source, taken, sealed in chosen if sealed and taken)
    open_docs = sum(1 for _source, taken, sealed in chosen if not sealed and taken)
    return [
        f"sources={n_sources} n={n_sources} documents={n_sources}",
        f"rows={n_rows}/{items} n={n_rows} documents={row_docs}",
        f"sealed_rows={sealed_rows} n={sealed_rows} documents={sealed_docs}",
        f"open_rows={open_rows} n={open_rows} documents={open_docs}",
    ]


def _object(item_id, kind, text):
    type_id, attr_id = _KINDS[kind]
    return f"""
        <SPEC-OBJECT IDENTIFIER="{item_id}">
          <TYPE><SPEC-OBJECT-TYPE-REF>{type_id}</SPEC-OBJECT-TYPE-REF></TYPE>
          <VALUES>
            <ATTRIBUTE-VALUE-XHTML>
              <DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>{attr_id}</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>
              <THE-VALUE><div>{text}</div></THE-VALUE>
            </ATTRIBUTE-VALUE-XHTML>
          </VALUES>
        </SPEC-OBJECT>"""


def write_reqif(directory, filename, objects):
    """objects is a list of (item_id, kind, text). The source id is the file stem."""
    body = "\n".join(_object(item_id, kind, text) for item_id, kind, text in objects)
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd">
  <CORE-CONTENT>
    <REQ-IF-CONTENT>
      <SPEC-TYPES>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot_requirement" LONG-NAME="Requirement">
          <SPEC-ATTRIBUTES>
            <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="_ad_requirement" LONG-NAME="ReqIF.Text">
              <TYPE><DATATYPE-DEFINITION-XHTML-REF>_dt</DATATYPE-DEFINITION-XHTML-REF></TYPE>
            </ATTRIBUTE-DEFINITION-XHTML>
          </SPEC-ATTRIBUTES>
        </SPEC-OBJECT-TYPE>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot_heading" LONG-NAME="Heading">
          <SPEC-ATTRIBUTES>
            <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="_ad_heading" LONG-NAME="ReqIF.Text">
              <TYPE><DATATYPE-DEFINITION-XHTML-REF>_dt</DATATYPE-DEFINITION-XHTML-REF></TYPE>
            </ATTRIBUTE-DEFINITION-XHTML>
          </SPEC-ATTRIBUTES>
        </SPEC-OBJECT-TYPE>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot_definition" LONG-NAME="Definition">
          <SPEC-ATTRIBUTES>
            <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="_ad_definition" LONG-NAME="ReqIF.Text">
              <TYPE><DATATYPE-DEFINITION-XHTML-REF>_dt</DATATYPE-DEFINITION-XHTML-REF></TYPE>
            </ATTRIBUTE-DEFINITION-XHTML>
          </SPEC-ATTRIBUTES>
        </SPEC-OBJECT-TYPE>
      </SPEC-TYPES>
      <SPEC-OBJECTS>
        {body}
      </SPEC-OBJECTS>
    </REQ-IF-CONTENT>
  </CORE-CONTENT>
</REQ-IF>
"""
    path = directory / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path


def shall_items(prefix, count):
    return [
        (f"{prefix}{number}", "requirement", f"Item {prefix}{number} shall hold.")
        for number in range(count)
    ]


def write_sources(root, count, per_source):
    eligible = {}
    for index in range(count):
        source_id = f"src-{index:02d}"
        items = shall_items(f"s{index}i", per_source)
        write_reqif(root, f"{source_id}.reqif", items)
        eligible[source_id] = [item_id for item_id, _kind, _text in items]
    return eligible


def run(root, out, seed, items=None, corpus="automotive"):
    argv = [
        "--corpus-root", str(root),
        "--corpus", corpus,
        "--seed", str(seed),
        "--out", str(out),
    ]
    if items is not None:
        argv.extend(["--items", str(items)])
    packet.main(argv)


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == COLUMNS
        return list(reader)


def all_rows(out):
    return read_rows(out / "open" / "testpoints.csv") + read_rows(out / "sealed" / "testpoints.csv")


def assert_report(printed, lines):
    for line in lines:
        assert line in printed


def assert_nothing_written(out):
    assert not (out / "open" / "testpoints.csv").exists()
    assert not (out / "sealed" / "testpoints.csv").exists()
    assert not (out / "manifest.json").exists()


def test_only_shall_or_must_requirements_are_sampled(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "bus.dbc").write_text(DBC_WITH_SHALL, encoding="utf-8")
    decoys = [
        ("shall-1", "requirement", "The pump shall stop."),
        ("shall-2", "requirement", "The motor shall start."),
        ("must-1", "requirement", "The valve must open."),
        ("must-2", "requirement", "The latch must hold."),
        ("plain-1", "requirement", "The pump runs."),
        ("should-1", "requirement", "The pump should stop."),
        ("head-1", "heading", "The chapter shall be ignored."),
        ("def-1", "definition", "A term must be defined."),
    ]
    write_reqif(root, "decoy.reqif", decoys)
    eligible = {"decoy": ["shall-1", "shall-2", "must-1", "must-2"]}
    eligible.update(write_sources(root, 9, 4))
    texts = {
        "shall-1": "The pump shall stop.",
        "shall-2": "The motor shall start.",
        "must-1": "The valve must open.",
        "must-2": "The latch must hold.",
    }
    seed = "11"
    items = 40
    chosen, n_sources = select(eligible, seed, items)
    out = tmp_path / "out"
    run(root, out, seed=seed, items=items)
    rows = all_rows(out)
    got = {(row["source_id"], row["item_id"]) for row in rows}
    expected = {(source_id, item_id) for source_id, taken, _sealed in chosen for item_id in taken}
    assert got == expected
    assert n_sources == 10
    by_id = {row["item_id"]: row for row in rows}
    for item_id, text in texts.items():
        assert by_id[item_id]["passage"] == text
        assert by_id[item_id]["packet_id"] == f"decoy#{item_id}"
        assert all(by_id[item_id][column] == "" for column in LABEL_COLUMNS)
    absent = " ".join(row["passage"] for row in rows)
    assert "The pump runs." not in absent
    assert "The pump should stop." not in absent
    assert "The chapter shall be ignored." not in absent
    assert "A term must be defined." not in absent
    assert "The node shall transmit" not in absent
    assert "EngineData" not in {row["item_id"] for row in rows}
    assert_report(capsys.readouterr().out, expected_report(chosen, n_sources, items))


def test_same_seed_is_byte_identical(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    write_sources(root, 10, 6)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run(root, out_a, seed="20261112", items=40)
    run(root, out_b, seed="20261112", items=40)
    for relative in ("open/testpoints.csv", "sealed/testpoints.csv", "manifest.json"):
        assert (out_a / relative).read_bytes() == (out_b / relative).read_bytes()
    other = tmp_path / "c"
    run(root, other, seed="20261113", items=40)
    assert (out_a / "open" / "testpoints.csv").read_bytes() != (other / "open" / "testpoints.csv").read_bytes()


def test_at_most_four_per_source_follow_hash_order(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    file_a = [f"a{number}" for number in range(3)]
    file_b = [f"b{number}" for number in range(3)]
    ids = file_a + file_b
    seed = expected = None
    for candidate in range(1, 40):
        ranked = sorted(ids, key=lambda item_id: (item_rank(str(candidate), "split", item_id), item_id))
        top = ranked[:4]
        if set(top) & set(file_a) and set(top) & set(file_b) and top != ids[:4]:
            seed, expected = str(candidate), top
            break
    assert expected is not None and expected != ids[:4]
    write_reqif(
        root, "left/split.reqif",
        [(item_id, "requirement", f"Left {item_id} shall hold.") for item_id in file_a],
    )
    write_reqif(
        root, "right/split.reqif",
        [(item_id, "requirement", f"Right {item_id} shall hold.") for item_id in file_b],
    )
    eligible = {"split": ids}
    eligible.update(write_sources(root, 9, 8))
    items = 40
    chosen, n_sources = select(eligible, seed, items)
    out = tmp_path / "out"
    run(root, out, seed=seed, items=items)
    rows = all_rows(out)
    by_source = {}
    for row in rows:
        by_source.setdefault(row["source_id"], []).append(row["item_id"])
    assert set(by_source) == set(eligible)
    for source_id, item_ids in by_source.items():
        assert len(item_ids) <= _MAX_PER_SOURCE
        ranked = sorted(
            eligible[source_id],
            key=lambda item_id: (item_rank(seed, source_id, item_id), item_id),
        )
        assert item_ids == ranked[:_MAX_PER_SOURCE]
    assert by_source["split"] == expected
    sheets = {}
    for row in read_rows(out / "open" / "testpoints.csv"):
        sheets.setdefault(row["source_id"], set()).add("open")
    for row in read_rows(out / "sealed" / "testpoints.csv"):
        sheets.setdefault(row["source_id"], set()).add("sealed")
    assert sheets["split"] in ({"open"}, {"sealed"})
    assert_report(capsys.readouterr().out, expected_report(chosen, n_sources, items))


def test_every_fourth_source_is_sealed(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    seed = "9"
    items = 48
    eligible = write_sources(root, 12, 4)
    chosen, n_sources = select(eligible, seed, items)
    out = tmp_path / "out"
    run(root, out, seed=seed, items=items)
    open_rows = read_rows(out / "open" / "testpoints.csv")
    sealed_rows = read_rows(out / "sealed" / "testpoints.csv")
    open_ids = {row["source_id"] for row in open_rows}
    sealed_ids = {row["source_id"] for row in sealed_rows}
    assert open_ids and sealed_ids
    assert open_ids.isdisjoint(sealed_ids)
    expected_sealed = [source_id for source_id, _taken, sealed in chosen if sealed]
    expected_open = [source_id for source_id, _taken, sealed in chosen if not sealed]
    assert sealed_ids == set(expected_sealed)
    assert open_ids == set(expected_open)
    assert len(sealed_ids) == 3
    assert len(open_ids) == 9
    ordered = sorted(eligible, key=lambda source_id: (source_rank(seed, source_id), source_id))
    assert expected_sealed == [source_id for index, source_id in enumerate(ordered) if (index + 1) % 4 == 0]
    for row in open_rows + sealed_rows:
        assert row["packet_id"] == f"{row['source_id']}#{row['item_id']}"
        assert all(row[column] == "" for column in LABEL_COLUMNS)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == seed
    assert manifest["corpus"] == "automotive"
    assert manifest["sealed_ids"] == expected_sealed
    listed = {entry["file"]: entry for entry in manifest["file_hashes"]}
    assert set(listed) == {f"src-{index:02d}.reqif" for index in range(12)}
    for name, entry in listed.items():
        raw = (root / name).read_bytes()
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
        assert entry["source_id"] in open_ids | sealed_ids
    assert_report(capsys.readouterr().out, expected_report(chosen, n_sources, items))


def test_default_items_is_40(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    eligible = write_sources(root, 10, 4)
    chosen, n_sources = select(eligible, "1", 40)
    out = tmp_path / "out"
    run(root, out, seed="1")
    assert sum(len(taken) for _source, taken, _sealed in chosen) == 40
    assert len(all_rows(out)) == 40
    assert_report(capsys.readouterr().out, expected_report(chosen, n_sources, 40))


def test_fewer_than_10_sources_writes_nothing(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    eligible = write_sources(root, 9, 4)
    items = 36
    chosen, n_sources = select(eligible, "1", items)
    assert n_sources < 10
    assert sum(len(taken) for _source, taken, _sealed in chosen) == items
    assert any(sealed for _source, _taken, sealed in chosen)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(root, out, seed="1", items=items)
    assert exc.value.code not in (0, None)
    assert_nothing_written(out)
    assert_report(capsys.readouterr().out, expected_report(chosen, n_sources, items))


def test_fewer_rows_than_items_writes_nothing(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    eligible = write_sources(root, 10, 1)
    items = 40
    chosen, n_sources = select(eligible, "3", items)
    n_rows = sum(len(taken) for _source, taken, _sealed in chosen)
    assert n_sources >= 10
    assert n_rows < items
    assert any(sealed for _source, _taken, sealed in chosen)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(root, out, seed="3", items=items)
    assert exc.value.code not in (0, None)
    assert_nothing_written(out)
    assert_report(capsys.readouterr().out, expected_report(chosen, n_sources, items))


def test_empty_sealed_sheet_writes_nothing(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    eligible = write_sources(root, 10, 4)
    items = 4
    chosen, n_sources = select(eligible, "4", items)
    n_rows = sum(len(taken) for _source, taken, _sealed in chosen)
    assert n_sources >= 10
    assert n_rows == items
    assert not any(sealed for _source, _taken, sealed in chosen)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(root, out, seed="4", items=items)
    assert exc.value.code not in (0, None)
    assert_nothing_written(out)
    assert_report(capsys.readouterr().out, expected_report(chosen, n_sources, items))
