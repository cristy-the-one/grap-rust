#!/usr/bin/env python3
"""Split three filled reviewer workbooks into open and sealed CSVs.

Reads the statements, pairs, and questions workbooks plus ``manifest.json``
and ``pair_manifest.json``. Source ids are the union of ``sealed_ids`` and
``file_hashes`` from both files. A row whose ``source_id`` is sealed is
written under ``sealed/``. A row whose ``source_id`` is a known source and
is not sealed is written under ``open/``. A question follows that same
``source_id`` rule. A ``source_id`` in neither list refuses the run: the
counts are printed and no CSV is written.

A row whose only non-empty cell is ``packet_id`` is an unused prepared row.
It is skipped. Each sheet prints ``unused_rows`` with the rows seen on that
sheet (every non-blank data row) as the denominator. A row with any other
cell filled and no ``source_id`` refuses the run. On refusal the sheet row
numbers of the refused rows are printed, numbers only.

Every count is printed with ``n`` and the number of documents behind it.
A figure with nothing to measure is printed as ``n/a``. Cell text is never
printed or logged.

    python scripts/split_reviewer_packet.py --statements s.xlsx --pairs p.xlsx \\
        --questions q.xlsx --manifest manifest.json \\
        --pair-manifest pair_manifest.json --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import openpyxl

_COLUMN = re.compile(r"[^a-z0-9]+")


def _column_name(value: str) -> str:
    """Guide header to the CSV name: lower case, punctuation runs to one underscore."""
    return _COLUMN.sub("_", value.strip().lower()).strip("_")


def _cell_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _n_docs(rows: list[dict[str, str]]) -> int:
    return len({row["source_id"].strip() for row in rows})


def _only_packet_id(row: dict[str, str]) -> bool:
    """True when packet_id is the only non-empty cell."""
    filled = [name for name, value in row.items() if value != ""]
    return filled == ["packet_id"]


def _load_ids(path: Path, label: str) -> tuple[set[str], set[str]]:
    """Return ``(sealed_ids, known_ids)`` for one manifest. Known includes sealed."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label} ({type(exc).__name__})") from None
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}: expected an object")
    sealed_raw = payload.get("sealed_ids")
    hashes = payload.get("file_hashes")
    if not isinstance(sealed_raw, list) or not isinstance(hashes, list):
        raise RuntimeError(f"{label}: sealed_ids and file_hashes are required")
    sealed: set[str] = set()
    for item in sealed_raw:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(f"{label}: sealed_ids must be strings")
        sealed.add(item.strip())
    known = set(sealed)
    for entry in hashes:
        if not isinstance(entry, dict):
            raise RuntimeError(f"{label}: file_hashes entries must be objects")
        source_id = entry.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise RuntimeError(f"{label}: file_hashes source_id must be a string")
        known.add(source_id.strip())
    return sealed, known


def _read_workbook(
    path: Path, label: str,
) -> tuple[list[str], list[dict[str, str]], list[int], int, int]:
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    except Exception as exc:
        raise RuntimeError(f"cannot read {label} workbook ({type(exc).__name__})") from None
    try:
        try:
            if not workbook.worksheets:
                raise RuntimeError(f"{label}: workbook has no sheet")
            sheet = workbook.worksheets[0]
            table = []
            origin = []
            for raw in sheet.iter_rows():
                if not raw:
                    continue
                origin.append(raw[0].row)
                table.append([_cell_text(cell.value) for cell in raw])
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"cannot read {label} workbook ({type(exc).__name__})"
            ) from None
    finally:
        workbook.close()
    if not table:
        raise RuntimeError(f"{label}: workbook has no header")
    width = len(table[0])
    while width > 0 and table[0][width - 1] == "":
        width -= 1
    header = table[0][:width]
    if not header or any(cell == "" for cell in header):
        raise RuntimeError(f"{label}: blank column header")
    columns = [_column_name(cell) for cell in header]
    if any(not name for name in columns):
        raise RuntimeError(f"{label}: blank column header")
    if len(set(columns)) != len(columns):
        raise RuntimeError(f"{label}: duplicate column")
    if "source_id" not in columns:
        raise RuntimeError(f"{label}: no source_id column")
    rows = []
    kept_origin = []
    unused = 0
    seen = 0
    for sheet_row, raw in zip(origin[1:], table[1:]):
        cells = list(raw)
        extra = cells[width:]
        if any(cell != "" for cell in extra):
            raise RuntimeError(f"{label}: row wider than header")
        cells = cells[:width]
        if len(cells) < width:
            cells.extend("" for _ in range(width - len(cells)))
        if all(cell == "" for cell in cells):
            continue
        seen += 1
        record = dict(zip(columns, cells))
        if _only_packet_id(record):
            unused += 1
            continue
        rows.append(record)
        kept_origin.append(sheet_row)
    return columns, rows, kept_origin, unused, seen


def _side(source_id: str, sealed_ids: set[str], known_ids: set[str]) -> str:
    key = source_id.strip()
    if key in sealed_ids:
        return "sealed"
    if key in known_ids:
        return "open"
    return "neither"


def _group(rows: list[dict[str, str]], sealed_ids: set[str], known_ids: set[str]):
    grouped = {"open": [], "sealed": [], "neither": []}
    for row in rows:
        grouped[_side(row["source_id"], sealed_ids, known_ids)].append(row)
    return grouped


def _fraction(name: str, count: int, total: int, docs: int, measured: bool) -> str:
    if not measured:
        return f"{name}=n/a"
    return f"{name}={count}/{total} n={count} documents={docs}"


def _refused_sheet_rows(rows, numbers, sealed_ids, known_ids) -> list[int]:
    """Sheet row numbers of rows whose source_id is in neither list."""
    found = []
    for row, number in zip(rows, numbers):
        if _side(row["source_id"], sealed_ids, known_ids) == "neither":
            found.append(number)
    return found


def _sheet_line(name: str, rows: list[dict[str, str]], grouped, wrote: bool,
                unused: int, seen: int, refused_numbers: list[int]) -> str:
    total = len(rows)
    refused = grouped["neither"]
    # Open and sealed counts describe CSVs that were written. A refusal, or a
    # sheet with no rows, did not produce those counts.
    produced = wrote and total > 0
    parts = [
        name,
        _fraction("open_rows", len(grouped["open"]), total, _n_docs(grouped["open"]), produced),
        _fraction("sealed_rows", len(grouped["sealed"]), total, _n_docs(grouped["sealed"]), produced),
        f"rows={total} n={total} documents={_n_docs(rows)}",
        _fraction("refused", len(refused), total, _n_docs(refused), total > 0),
    ]
    if refused_numbers:
        parts.append("refused_sheet_rows=" + ",".join(str(number) for number in refused_numbers))
    # An unused row has no source_id, so no document is behind the count.
    # The denominator is the non-blank data rows seen on the sheet.
    parts.append(_fraction("unused_rows", unused, seen, 0, seen > 0))
    return " ".join(parts)


def _id_line(label: str, sealed_ids: set[str], known_ids: set[str]) -> str:
    n_sealed = len(sealed_ids)
    n_known = len(known_ids)
    if n_known == 0:
        return f"{label} sealed_ids=n/a n=n/a documents=n/a"
    return f"{label} sealed_ids={n_sealed}/{n_known} n={n_sealed} documents={n_known}"


def _report(loaded, sealed_ids, known_ids, manifest_ids, pair_ids, wrote: bool) -> str:
    lines = [
        _sheet_line(
            label, rows, grouped, wrote, unused, seen,
            _refused_sheet_rows(rows, numbers, sealed_ids, known_ids),
        )
        for label, _columns, rows, grouped, unused, seen, numbers in loaded
    ]
    all_rows = [
        row
        for _label, _columns, rows, _grouped, _unused, _seen, _numbers in loaded
        for row in rows
    ]
    combined = _group(all_rows, sealed_ids, known_ids)
    # Sheet row numbers are per sheet. The total line does not repeat them.
    lines.append(_sheet_line(
        "total", all_rows, combined, wrote,
        sum(unused for _l, _c, _r, _g, unused, _s, _n in loaded),
        sum(seen for _l, _c, _r, _g, _u, seen, _n in loaded),
        [],
    ))
    lines.append(_id_line("manifest", manifest_ids[0], manifest_ids[1]))
    lines.append(_id_line("pair_manifest", pair_ids[0], pair_ids[1]))
    lines.append(_id_line("routing", sealed_ids, known_ids))
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    except Exception as exc:
        raise RuntimeError(f"cannot write {path.name} ({type(exc).__name__})") from None


def split(statements: Path, pairs: Path, questions: Path, manifest: Path,
          pair_manifest: Path, out: Path) -> None:
    manifest_ids = _load_ids(manifest, "manifest")
    pair_ids = _load_ids(pair_manifest, "pair_manifest")
    sealed_ids = manifest_ids[0] | pair_ids[0]
    known_ids = manifest_ids[1] | pair_ids[1]
    loaded = []
    for label, path in (
        ("statements", statements),
        ("pairs", pairs),
        ("questions", questions),
    ):
        columns, rows, numbers, unused, seen = _read_workbook(path, label)
        loaded.append((
            label, columns, rows, _group(rows, sealed_ids, known_ids),
            unused, seen, numbers,
        ))
    refused = any(
        grouped["neither"]
        for _label, _columns, _rows, grouped, _unused, _seen, _numbers in loaded
    )
    if refused:
        sys.stdout.write(_report(loaded, sealed_ids, known_ids, manifest_ids, pair_ids, wrote=False))
        raise SystemExit(1)
    destination = Path(out)
    for label, columns, _rows, grouped, _unused, _seen, _numbers in loaded:
        _write_csv(destination / "open" / f"{label}.csv", columns, grouped["open"])
        _write_csv(destination / "sealed" / f"{label}.csv", columns, grouped["sealed"])
    sys.stdout.write(_report(loaded, sealed_ids, known_ids, manifest_ids, pair_ids, wrote=True))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Split reviewer workbooks into open and sealed CSVs. Prints counts only."
    )
    parser.add_argument("--statements", required=True, type=Path)
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--pair-manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        split(
            args.statements,
            args.pairs,
            args.questions,
            args.manifest,
            args.pair_manifest,
            args.out,
        )
    except RuntimeError as exc:
        sys.stderr.write(str(exc) + "\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
