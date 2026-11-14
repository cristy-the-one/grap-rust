"""GS-01 split a filled reviewer packet into open and sealed CSVs.

GS-02: a row whose only filled cell is packet_id is an unused prepared row.
Synthetic workbooks only. Cell text is invented. Nothing is written under a
held directory, and the real reviewer sheets are not read here.
"""
import csv
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import split_reviewer_packet as splitter  # noqa: E402

# Invented marker. It is the cell text the tool must never print.
CANARY = "ZZCANARY7q-cell"
UNKNOWN_SOURCE = "src-missing"

_STATEMENT_HEADERS = [
    "packet_id", "source_id", "item_id", "passage",
    "kind", "subject", "conditions", "exceptions",
    "dates and amounts", "acts referred to", "sure?", "note",
]
_STATEMENT_COLUMNS = [
    "packet_id", "source_id", "item_id", "passage",
    "kind", "subject", "conditions", "exceptions",
    "dates_and_amounts", "acts_referred_to", "sure", "note",
]
_PAIR_COLUMNS = [
    "packet_id", "source_id", "item_id", "passage",
    "name", "other_source_id", "other_item_id", "other_passage",
    "other_name", "label", "note",
]
_QUESTION_HEADERS = [
    "packet_id", "source_id", "item_id", "passage",
    "question", "type", "answer", "Where", "note",
]
_QUESTION_COLUMNS = [
    "packet_id", "source_id", "item_id", "passage",
    "question", "type", "answer", "where", "note",
]


def _column_letters(index):
    letters = ""
    number = index + 1
    while number:
        number, rem = divmod(number - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def write_xlsx(path, rows):
    """Minimal workbook the splitter can open. First row is the header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for index, value in enumerate(row):
            ref = f"{_column_letters(index)}{row_number}"
            text = escape(value)
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def write_manifest(path, file_hashes, sealed_ids, extra=None):
    payload = {
        "corpus": "law-ro",
        "file_hashes": file_hashes,
        "sealed_ids": sealed_ids,
        "seed": "1",
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _hash(source_id):
    return {"file": f"{source_id}.txt", "sha256": "abc", "source_id": source_id}


def write_packet_manifests(directory):
    """Statement manifest plus pair manifest. src-pair is known only to the pair file."""
    write_manifest(
        directory / "manifest.json",
        [_hash("src-open"), _hash("src-held")],
        ["src-held"],
    )
    write_manifest(
        directory / "pair_manifest.json",
        [_hash("src-open"), _hash("src-held"), _hash("src-pair")],
        ["src-held"],
        extra={"bands": {}, "items": 2},
    )


def _statement(source_id, item_id, kind):
    return [
        f"{source_id}#{item_id}", source_id, item_id, CANARY,
        kind, "invented-subject", "", "", "", "", "yes", CANARY,
    ]


def _pair(packet_id, source_id, other_source_id):
    return [
        packet_id, source_id, "item-1", CANARY,
        "invented-name", other_source_id, "item-2", CANARY,
        "invented-other", "same", CANARY,
    ]


def _question(packet_id, source_id, where):
    return [
        packet_id, source_id, "item-1", CANARY,
        CANARY, "Direct", "invented-answer", where, CANARY,
    ]


def _question_cells(packet_id, source_id="", note=""):
    """One question row. Empty cells stay empty. source_id defaults to blank."""
    row = [""] * len(_QUESTION_HEADERS)
    row[0] = packet_id
    row[1] = source_id
    row[-1] = note
    return row


def write_workbooks(directory, statements, pairs, questions):
    write_xlsx(directory / "statements.xlsx", [_STATEMENT_HEADERS, *statements])
    write_xlsx(directory / "pairs.xlsx", [_PAIR_COLUMNS, *pairs])
    write_xlsx(directory / "questions.xlsx", [_QUESTION_HEADERS, *questions])
    return (
        directory / "statements.xlsx",
        directory / "pairs.xlsx",
        directory / "questions.xlsx",
    )


def argv_for(directory, out):
    statements, pairs, questions = (
        directory / "statements.xlsx",
        directory / "pairs.xlsx",
        directory / "questions.xlsx",
    )
    return [
        "--statements", str(statements),
        "--pairs", str(pairs),
        "--questions", str(questions),
        "--manifest", str(directory / "manifest.json"),
        "--pair-manifest", str(directory / "pair_manifest.json"),
        "--out", str(out),
    ]


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def streams_of(capsys):
    captured = capsys.readouterr()
    return captured.out, captured.err, captured.out + captured.err


def test_routes_open_and_sealed_by_source_id(tmp_path, capsys):
    """Open and sealed follow source_id. A sealed source_id seals its question.

    The question's where text names an open source. Routing still follows
    source_id. src-pair is known only from pair_manifest.json, so that file
    is part of the membership lists.
    """
    packet = tmp_path / "packet"
    packet.mkdir()
    write_packet_manifests(packet)
    write_workbooks(
        packet,
        [
            _statement("src-open", "item-1", "obligation"),
            _statement("src-open", "item-2", "fact"),
            _statement("src-held", "item-9", "prohibition"),
        ],
        [
            _pair("pair-0000", "src-open", "src-pair"),
            _pair("pair-0001", "src-held", "src-open"),
        ],
        [
            _question("q-held", "src-held", "src-open#item-1"),
            _question("q-pair", "src-pair", "src-pair#item-1"),
        ],
    )
    out = tmp_path / "split-out"
    splitter.main(argv_for(packet, out))
    stdout, stderr, combined = streams_of(capsys)
    assert CANARY not in combined
    assert "invented-subject" not in combined
    assert "invented-answer" not in combined

    open_statements, open_statement_rows = read_csv(out / "open" / "statements.csv")
    sealed_statements, sealed_statement_rows = read_csv(out / "sealed" / "statements.csv")
    assert open_statements == _STATEMENT_COLUMNS
    assert sealed_statements == _STATEMENT_COLUMNS
    assert [row["source_id"] for row in open_statement_rows] == ["src-open", "src-open"]
    assert [row["source_id"] for row in sealed_statement_rows] == ["src-held"]
    assert open_statement_rows[0]["kind"] == "obligation"
    assert open_statement_rows[0]["passage"] == CANARY
    assert open_statement_rows[0]["dates_and_amounts"] == ""
    assert open_statement_rows[0]["sure"] == "yes"

    _pair_header, open_pairs = read_csv(out / "open" / "pairs.csv")
    _pair_header, sealed_pairs = read_csv(out / "sealed" / "pairs.csv")
    assert [row["source_id"] for row in open_pairs] == ["src-open"]
    assert [row["source_id"] for row in sealed_pairs] == ["src-held"]
    assert open_pairs[0]["label"] == "same"

    question_header, open_questions = read_csv(out / "open" / "questions.csv")
    _question_header, sealed_questions = read_csv(out / "sealed" / "questions.csv")
    assert question_header == _QUESTION_COLUMNS
    assert [row["source_id"] for row in open_questions] == ["src-pair"]
    assert [row["packet_id"] for row in sealed_questions] == ["q-held"]
    assert sealed_questions[0]["where"] == "src-open#item-1"
    assert sealed_questions[0]["question"] == CANARY

    expected = "\n".join([
        "statements open_rows=2/3 n=2 documents=1 sealed_rows=1/3 n=1 documents=1 "
        "rows=3 n=3 documents=2 refused=0/3 n=0 documents=0 "
        "unused_rows=0/3 n=0 documents=0",
        "pairs open_rows=1/2 n=1 documents=1 sealed_rows=1/2 n=1 documents=1 "
        "rows=2 n=2 documents=2 refused=0/2 n=0 documents=0 "
        "unused_rows=0/2 n=0 documents=0",
        "questions open_rows=1/2 n=1 documents=1 sealed_rows=1/2 n=1 documents=1 "
        "rows=2 n=2 documents=2 refused=0/2 n=0 documents=0 "
        "unused_rows=0/2 n=0 documents=0",
        "total open_rows=4/7 n=4 documents=2 sealed_rows=3/7 n=3 documents=1 "
        "rows=7 n=7 documents=3 refused=0/7 n=0 documents=0 "
        "unused_rows=0/7 n=0 documents=0",
        "manifest sealed_ids=1/2 n=1 documents=2",
        "pair_manifest sealed_ids=1/3 n=1 documents=3",
        "routing sealed_ids=1/3 n=1 documents=3",
    ]) + "\n"
    assert stdout == expected
    assert stderr == ""


def test_question_with_sealed_source_id_goes_to_sealed(tmp_path, capsys):
    """A question is sealed exactly when its own source_id is sealed."""
    packet = tmp_path / "packet"
    packet.mkdir()
    write_packet_manifests(packet)
    write_workbooks(
        packet,
        [_statement("src-open", "item-1", "obligation")],
        [_pair("pair-0000", "src-open", "src-open")],
        [_question("q-held", "src-held", "src-open#item-1")],
    )
    out = tmp_path / "split-out"
    splitter.main(argv_for(packet, out))
    _stdout, _stderr, combined = streams_of(capsys)
    assert CANARY not in combined
    _header, open_questions = read_csv(out / "open" / "questions.csv")
    _header, sealed_questions = read_csv(out / "sealed" / "questions.csv")
    assert open_questions == []
    assert [row["source_id"] for row in sealed_questions] == ["src-held"]
    assert "questions open_rows=0/1 n=0 documents=0 sealed_rows=1/1 n=1 documents=1" in combined


def test_source_id_in_neither_list_writes_nothing(tmp_path, capsys):
    """One unknown source_id refuses the run. No CSV is written, and no cell is printed."""
    packet = tmp_path / "packet"
    packet.mkdir()
    write_packet_manifests(packet)
    write_workbooks(
        packet,
        [
            _statement("src-open", "item-1", "obligation"),
            _statement(UNKNOWN_SOURCE, "item-9", "other"),
        ],
        [_pair("pair-0000", "src-open", "src-open")],
        [_question("q-held", "src-held", "src-open#item-1")],
    )
    out = tmp_path / "split-out"
    with pytest.raises(SystemExit) as caught:
        splitter.main(argv_for(packet, out))
    assert caught.value.code == 1
    stdout, stderr, combined = streams_of(capsys)
    assert CANARY not in combined
    assert UNKNOWN_SOURCE not in combined
    assert "invented-subject" not in combined
    assert "Traceback" not in combined
    assert not out.exists()
    expected = "\n".join([
        "statements open_rows=n/a sealed_rows=n/a rows=2 n=2 documents=2 "
        "refused=1/2 n=1 documents=1 refused_sheet_rows=3 "
        "unused_rows=0/2 n=0 documents=0",
        "pairs open_rows=n/a sealed_rows=n/a rows=1 n=1 documents=1 "
        "refused=0/1 n=0 documents=0 unused_rows=0/1 n=0 documents=0",
        "questions open_rows=n/a sealed_rows=n/a rows=1 n=1 documents=1 "
        "refused=0/1 n=0 documents=0 unused_rows=0/1 n=0 documents=0",
        "total open_rows=n/a sealed_rows=n/a rows=4 n=4 documents=3 "
        "refused=1/4 n=1 documents=1 unused_rows=0/4 n=0 documents=0",
        "manifest sealed_ids=1/2 n=1 documents=2",
        "pair_manifest sealed_ids=1/3 n=1 documents=3",
        "routing sealed_ids=1/3 n=1 documents=3",
    ]) + "\n"
    assert stdout == expected
    assert stderr == ""

    completed = subprocess.run(
        [sys.executable, str(_SCRIPTS / "split_reviewer_packet.py"), *argv_for(packet, out)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    process_text = completed.stdout + completed.stderr
    assert CANARY not in process_text
    assert UNKNOWN_SOURCE not in process_text
    assert "invented-answer" not in process_text
    assert "Traceback" not in process_text
    assert not out.exists()


def test_unused_question_rows_are_skipped(tmp_path, capsys):
    """Three filled question rows and two unused ones. The split succeeds.

    An unused row has packet_id and nothing else. It is not written.
    unused_rows carries the rows seen on that sheet as its denominator.
    """
    unused_a = "pkt-unused-aa"
    unused_b = "pkt-unused-bb"
    packet = tmp_path / "packet"
    packet.mkdir()
    write_packet_manifests(packet)
    write_workbooks(
        packet,
        [_statement("src-open", "item-1", "obligation")],
        [_pair("pair-0000", "src-open", "src-open")],
        [
            _question("q-fill-1", "src-open", "src-open#item-1"),
            _question_cells(unused_a),
            _question("q-fill-2", "src-open", "src-open#item-2"),
            _question_cells(unused_b),
            _question("q-fill-3", "src-open", "src-open#item-3"),
        ],
    )
    out = tmp_path / "split-out"
    splitter.main(argv_for(packet, out))
    stdout, stderr, combined = streams_of(capsys)
    assert unused_a not in combined
    assert unused_b not in combined
    assert CANARY not in combined
    assert "invented-answer" not in combined
    assert stderr == ""
    expected = "\n".join([
        "statements open_rows=1/1 n=1 documents=1 sealed_rows=0/1 n=0 documents=0 "
        "rows=1 n=1 documents=1 refused=0/1 n=0 documents=0 "
        "unused_rows=0/1 n=0 documents=0",
        "pairs open_rows=1/1 n=1 documents=1 sealed_rows=0/1 n=0 documents=0 "
        "rows=1 n=1 documents=1 refused=0/1 n=0 documents=0 "
        "unused_rows=0/1 n=0 documents=0",
        "questions open_rows=3/3 n=3 documents=1 sealed_rows=0/3 n=0 documents=0 "
        "rows=3 n=3 documents=1 refused=0/3 n=0 documents=0 "
        "unused_rows=2/5 n=2 documents=0",
        "total open_rows=5/5 n=5 documents=1 sealed_rows=0/5 n=0 documents=0 "
        "rows=5 n=5 documents=1 refused=0/5 n=0 documents=0 "
        "unused_rows=2/7 n=2 documents=0",
        "manifest sealed_ids=1/2 n=1 documents=2",
        "pair_manifest sealed_ids=1/3 n=1 documents=3",
        "routing sealed_ids=1/3 n=1 documents=3",
    ]) + "\n"
    assert stdout == expected
    _header, open_questions = read_csv(out / "open" / "questions.csv")
    _header, sealed_questions = read_csv(out / "sealed" / "questions.csv")
    assert [row["packet_id"] for row in open_questions] == [
        "q-fill-1", "q-fill-2", "q-fill-3",
    ]
    assert sealed_questions == []
    written = "\n".join(row["packet_id"] for row in open_questions)
    assert unused_a not in written
    assert unused_b not in written


def test_other_cell_without_source_id_refuses_with_sheet_row(tmp_path, capsys):
    """packet_id plus one other filled cell, and no source_id, still refuses.

    The refusal prints that row's sheet row number and no cell text.
    The unused neighbour is skipped, with the rows seen as the denominator.
    """
    bare_packet = "pkt-nosrc-qq"
    bare_note = "NOTETEXT9q"
    unused_packet = "pkt-unused-cc"
    packet = tmp_path / "packet"
    packet.mkdir()
    write_packet_manifests(packet)
    write_workbooks(
        packet,
        [_statement("src-open", "item-1", "obligation")],
        [_pair("pair-0000", "src-open", "src-open")],
        [
            _question("q-ok-1", "src-open", "src-open#item-1"),
            _question_cells(unused_packet),
            _question("q-ok-2", "src-open", "src-open#item-2"),
            _question_cells(bare_packet, note=bare_note),
        ],
    )
    out = tmp_path / "split-out"
    with pytest.raises(SystemExit) as caught:
        splitter.main(argv_for(packet, out))
    assert caught.value.code == 1
    stdout, stderr, combined = streams_of(capsys)
    assert bare_packet not in combined
    assert bare_note not in combined
    assert unused_packet not in combined
    assert CANARY not in combined
    assert "invented-answer" not in combined
    assert "Traceback" not in combined
    assert not out.exists()
    expected = "\n".join([
        "statements open_rows=n/a sealed_rows=n/a rows=1 n=1 documents=1 "
        "refused=0/1 n=0 documents=0 unused_rows=0/1 n=0 documents=0",
        "pairs open_rows=n/a sealed_rows=n/a rows=1 n=1 documents=1 "
        "refused=0/1 n=0 documents=0 unused_rows=0/1 n=0 documents=0",
        "questions open_rows=n/a sealed_rows=n/a rows=3 n=3 documents=2 "
        "refused=1/3 n=1 documents=1 refused_sheet_rows=5 "
        "unused_rows=1/4 n=1 documents=0",
        "total open_rows=n/a sealed_rows=n/a rows=5 n=5 documents=2 "
        "refused=1/5 n=1 documents=1 unused_rows=1/6 n=1 documents=0",
        "manifest sealed_ids=1/2 n=1 documents=2",
        "pair_manifest sealed_ids=1/3 n=1 documents=3",
        "routing sealed_ids=1/3 n=1 documents=3",
    ]) + "\n"
    assert stdout == expected
    assert stderr == ""

    completed = subprocess.run(
        [sys.executable, str(_SCRIPTS / "split_reviewer_packet.py"), *argv_for(packet, out)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    process_text = completed.stdout + completed.stderr
    assert bare_packet not in process_text
    assert bare_note not in process_text
    assert unused_packet not in process_text
    assert CANARY not in process_text
    assert "invented-answer" not in process_text
    assert "Traceback" not in process_text
    assert "refused_sheet_rows=5" in completed.stdout
    assert not out.exists()


def test_only_the_splitter_imports_the_workbook_library():
    """The workbook library is named in the splitter and nowhere else under the two trees."""
    repo = Path(__file__).resolve().parents[2]
    needle = "open" + "pyxl"
    hits = []
    for base in ("mcp-server", "scripts"):
        for path in (repo / base).rglob("*.py"):
            if path.name == "split_reviewer_packet.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if needle in text:
                hits.append(path.relative_to(repo).as_posix())
    assert hits == []
