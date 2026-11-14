#!/usr/bin/env python3
"""Write the Sheet 4 side-by-side review sheet.

One row per requirement on the open sheet. The labeller's six free-text
columns sit next to the system's generated values for that requirement.
The verdict column is left empty; the owner fills same, acceptable, wrong,
or missing. Nothing is computed, and no row is printed.

Reads the open sheet only: ``open/testpoints.csv``, and ``testpoints.csv``
when that file sits directly in the directory (a flat directory counts as
open). If both files exist, the tool refuses and writes nothing: reading
both would emit each row twice. A directory named sealed is not a sheet
and is not read.

The system JSON is the object the scorer already reads. Generated text is
taken from each testpoint under the six column names, matched on
``source_id`` and ``item_id``. ``needs`` and ``testable`` are left out of
this sheet; those two are scored elsewhere.

    python scripts/export_testpoint_review.py --gold <dir> --system <json> --out <csv>

Prints ``rows=<n> n=<n> documents=<n> system_missing=<k>`` for the rows
written. ``k`` is how many gold rows have no system testpoint, and ``n``
is the gold-row denominator. When there are no gold rows, ``system_missing``
prints ``n/a``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_FREE = (
    "preconditions",
    "stimulus",
    "expected_observable_result",
    "pass_criterion",
    "variants",
    "negative_cases",
)
_IDENTITY = ("packet_id", "source_id", "item_id", "passage")
_COLUMNS = (
    *_IDENTITY,
    *(name for column in _FREE for name in (column, f"system_{column}")),
    "verdict",
)
_HELD_DIR = "sealed"


def _open_paths(gold_dir: Path) -> list[Path]:
    """Open sheet only. A flat file counts as open. The held directory does not.

    Both the flat file and ``open/testpoints.csv`` together are a refusal:
    the two sheets are not merged, because that would write each row twice.
    """
    folder = Path(gold_dir)
    if folder.name == _HELD_DIR:
        return []
    direct = folder / "testpoints.csv"
    opened = folder / "open" / "testpoints.csv"
    if direct.is_file() and opened.is_file():
        sys.stderr.write(
            "refusing: both testpoints.csv and open/testpoints.csv exist\n"
        )
        raise SystemExit(1)
    paths = []
    if direct.is_file():
        paths.append(direct)
    if opened.is_file():
        paths.append(opened)
    return paths


def _read_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append({
                    key: value or ""
                    for key, value in row.items()
                    if key is not None
                })
    return rows


def _cell(row: dict, name: str) -> str:
    if name not in row or row[name] is None:
        return ""
    value = row[name]
    if isinstance(value, str):
        return value
    return str(value)


def _system_index(system: dict) -> dict[tuple[str, str], dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for entry in list(system.get("testpoints") or []):
        key = (str(entry.get("source_id") or ""), str(entry.get("item_id") or ""))
        by_key[key] = entry
    return by_key


def _review_row(gold: dict, system_row: dict) -> dict[str, str]:
    row = {
        "packet_id": gold.get("packet_id") or "",
        "source_id": str(gold.get("source_id") or ""),
        "item_id": str(gold.get("item_id") or ""),
        "passage": gold.get("passage") or "",
        "verdict": "",
    }
    for name in _FREE:
        row[name] = gold.get(name) or ""
        row[f"system_{name}"] = _cell(system_row, name)
    return row


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def export(gold_dir, system: dict, out) -> None:
    """Write one review row per open requirement. Prints counts only."""
    gold_rows = _read_rows(_open_paths(Path(gold_dir)))
    by_key = _system_index(system or {})
    written = []
    missing = 0
    for gold in gold_rows:
        key = (str(gold.get("source_id") or ""), str(gold.get("item_id") or ""))
        if key not in by_key:
            missing += 1
        written.append(_review_row(gold, by_key.get(key, {})))
    _write_csv(Path(out), written)
    n = len(written)
    documents = len({row["source_id"] for row in written})
    # No gold rows means there is nothing to measure. A zero here would be a
    # number with an empty denominator.
    missing_field = "n/a" if n == 0 else str(missing)
    print(f"rows={n} n={n} documents={documents} system_missing={missing_field}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Write the Sheet 4 side-by-side review sheet. Nothing is computed.",
    )
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--system", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    system = json.loads(args.system.read_text(encoding="utf-8"))
    export(args.gold, system, args.out)


if __name__ == "__main__":
    main()
