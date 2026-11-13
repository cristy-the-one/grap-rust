#!/usr/bin/env python3
"""Write an empty Sheet 4 test-point packet from a corpus folder.

Eligible items are requirements whose text contains ``shall`` or ``must``.
Sources are ordered by ``sha256(seed + source_id)``. At most 4 items are
taken from a source, in ``sha256(seed + source_id + item_id)`` order. Every
fourth source in that order is sealed. Label columns are left blank.

Stops, without writing, when fewer than 10 sources were reached, fewer than
``--items`` rows were produced, or the sealed sheet is empty. Every count is
printed with ``n`` and the number of documents behind it.

    python scripts/make_testpoint_packet.py --corpus-root <dir> --corpus automotive \\
        --seed 20261112 --items 40 --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "mcp-server"))

from grap_mcp.ingest import _READERS  # noqa: E402
from grap_mcp.readers.reqif import read_reqif  # noqa: E402

_MAX_PER_SOURCE = 4
_MIN_SOURCES = 10
_LABELS = (
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
_COLUMNS = ("packet_id", "source_id", "item_id", "passage", *_LABELS)


def _rank(seed: str, source_id: str) -> str:
    return hashlib.sha256(f"{seed}{source_id}".encode("utf-8")).hexdigest()


def _item_rank(seed: str, source_id: str, item_id: str) -> str:
    return hashlib.sha256(f"{seed}{source_id}{item_id}".encode("utf-8")).hexdigest()


def _eligible(item) -> bool:
    if item.kind_hint != "requirement":
        return False
    text = item.text or ""
    return "shall" in text or "must" in text


def _readers() -> dict:
    """Extension dispatch shared with ingestion, plus the merged ReqIF reader.

    ReqIF is not in ``ingest._READERS`` until R-06. Sheet 4 samples its
    requirement items, and a reader added to ``_READERS`` is picked up here
    without a second list.
    """
    readers = dict(_READERS)
    readers.setdefault(".reqif", read_reqif)
    return readers


def read_items(corpus_root: Path) -> list:
    """One SourceItem per item the readers return. A file that cannot be parsed raises."""
    root = Path(corpus_root)
    if not root.is_dir():
        raise RuntimeError(f"corpus root is not a directory: {root}")
    readers = _readers()
    suffixes = set(readers)
    paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes
    )
    items = []
    for path in paths:
        reader = readers[path.suffix.lower()]
        items.extend(reader(path, corpus_root=root))
    return items


def _select(items, seed: str, limit: int):
    grouped: dict = {}
    for item in items:
        if not _eligible(item):
            continue
        grouped.setdefault(item.source_id, []).append(item)
    ordered = sorted(grouped, key=lambda source_id: (_rank(seed, source_id), source_id))
    chosen = []
    remaining = limit
    for index, source_id in enumerate(ordered):
        if remaining <= 0:
            break
        ranked = sorted(
            grouped[source_id],
            key=lambda item: (_item_rank(seed, source_id, item.item_id), item.item_id, item.file),
        )
        take = tuple(ranked[: min(_MAX_PER_SOURCE, remaining)])
        chosen.append((source_id, take, (index + 1) % 4 == 0))
        remaining -= len(take)
    return chosen, len(ordered)


def _report(chosen, n_sources: int, items: int) -> tuple[int, int]:
    n_rows = sum(len(taken) for _source, taken, _sealed in chosen)
    sealed_rows = sum(len(taken) for _source, taken, sealed in chosen if sealed)
    open_rows = n_rows - sealed_rows
    row_docs = sum(1 for _source, taken, _sealed in chosen if taken)
    sealed_docs = sum(1 for _source, taken, sealed in chosen if sealed and taken)
    open_docs = sum(1 for _source, taken, sealed in chosen if not sealed and taken)
    print(f"sources={n_sources} n={n_sources} documents={n_sources}")
    print(f"rows={n_rows}/{items} n={n_rows} documents={row_docs}")
    print(f"sealed_rows={sealed_rows} n={sealed_rows} documents={sealed_docs}")
    print(f"open_rows={open_rows} n={open_rows} documents={open_docs}")
    return n_rows, sealed_rows


def _sheet_rows(taken) -> list[dict[str, str]]:
    rows = []
    for item in taken:
        row = {
            "packet_id": f"{item.source_id}#{item.item_id}",
            "source_id": item.source_id,
            "item_id": item.item_id,
            "passage": item.text,
        }
        for column in _LABELS:
            row[column] = ""
        rows.append(row)
    return rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _file_hashes(chosen) -> list[dict[str, str]]:
    seen = set()
    hashes = []
    for source_id, taken, _sealed in chosen:
        for item in taken:
            if item.file in seen:
                continue
            seen.add(item.file)
            hashes.append({"file": item.file, "sha256": item.sha256, "source_id": source_id})
    return hashes


def build_packet(corpus_root: Path, corpus: str, seed: str, items: int, out: Path) -> None:
    if corpus not in ("law-ro", "automotive"):
        raise RuntimeError(f"unknown corpus: {corpus}")
    if items < 0:
        raise RuntimeError("--items must be zero or greater")
    seed = str(seed)
    chosen, n_sources = _select(read_items(corpus_root), seed, items)
    n_rows, sealed_rows = _report(chosen, n_sources, items)
    if n_sources < _MIN_SOURCES or n_rows < items or sealed_rows == 0:
        raise SystemExit(1)
    open_rows = []
    sealed = []
    for _source, taken, is_sealed in chosen:
        target = sealed if is_sealed else open_rows
        target.extend(_sheet_rows(taken))
    destination = Path(out)
    _write_csv(destination / "open" / "testpoints.csv", open_rows)
    _write_csv(destination / "sealed" / "testpoints.csv", sealed)
    manifest = {
        "corpus": corpus,
        "file_hashes": _file_hashes(chosen),
        "sealed_ids": [source_id for source_id, _taken, is_sealed in chosen if is_sealed],
        "seed": seed,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write an empty test-point packet (Sheet 4).")
    parser.add_argument("--corpus-root", required=True, type=Path)
    parser.add_argument("--corpus", required=True, choices=("law-ro", "automotive"))
    parser.add_argument("--seed", required=True)
    parser.add_argument("--items", type=int, default=40)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    build_packet(args.corpus_root, args.corpus, args.seed, args.items, args.out)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        sys.stderr.write(str(exc) + "\n")
        sys.exit(1)
