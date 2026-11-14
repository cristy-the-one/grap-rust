#!/usr/bin/env python3
"""Write an empty Sheet 1 statement packet from a corpus folder.

One row per kept passage. Label columns are left blank for the labeller.
Law passages are whole articles, split on the ``_ARTICLE`` markers in
``grap_mcp.kee.legal_rules``. Automotive passages come from ``ingest._READERS``.
Sheet 1 keeps ``requirement`` and ``text`` items of 80 to 1,500 characters.
Sealing follows ``source_id``, so one source is not split across the sheets.
No model is called.

    python scripts/make_gold_packet.py --corpus-root <dir> --corpus law-ro \\
        --seed 20261112 --items 150 --out gold/law-ro
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))
from grap_mcp.kee.legal_rules import _ARTICLE  # noqa: E402

_DOC_SUFFIXES = {".json", ".txt", ".md", ".markdown"}
_BODY_KEYS = {"text_complet", "text", "content", "body"}
_MIN_CHARS = 80
_MAX_CHARS = 1500
_MAX_PER_FILE = 4
_MIN_SOURCE_FILES = 40

# Sheet 1 columns from the labelling guides: lower-case, spaces and
# punctuation runs turned into a single underscore.
_LAW_LABELS = (
    "kind",
    "subject",
    "conditions",
    "exceptions",
    "dates_and_amounts",
    "acts_referred_to",
    "sure",
    "note",
)
_AUTOMOTIVE_LABELS = (
    "kind",
    "subject",
    "trigger_condition",
    "required_behaviour",
    "values_and_timing",
    "exception",
    "refers_to",
    "traces_up_to",
    "configuration_dependency",
    "sure",
    "note",
)
_STATEMENT_KINDS = ("requirement", "text")


@dataclass(frozen=True)
class SourceItem:
    source_id: str
    release: str | None
    item_id: str
    kind_hint: str
    text: str
    heading_path: tuple[str, ...]
    attributes: dict[str, str]
    links: tuple[dict, ...]
    file: str
    sha256: str


@dataclass(frozen=True)
class _FileItems:
    file: str
    sha256: str
    source_id: str
    items: tuple[SourceItem, ...]


def _rank(seed: str, relative: str) -> str:
    return hashlib.sha256(f"{seed}{relative}".encode("utf-8")).hexdigest()


def _item_rank(seed: str, relative: str, item_id: str) -> str:
    return hashlib.sha256(f"{seed}{relative}{item_id}".encode("utf-8")).hexdigest()


def _article_label(match: re.Match) -> str:
    label = re.sub(r"\s+", " ", match.group(0)).strip()
    if label.startswith("+"):
        label = label[1:].strip()
    return label


def _split_articles(text, source_id, release, heading_path, attributes, relative, digest):
    matches = list(_ARTICLE.finditer(text))
    items = []
    seen = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        passage = text[match.start():end].strip()
        if not _MIN_CHARS <= len(passage) <= _MAX_CHARS:
            continue
        label = _article_label(match) or f"{source_id}#{index}"
        occurrence = seen.get(label, 0)
        seen[label] = occurrence + 1
        item_id = label if occurrence == 0 else f"{label}#{occurrence}"
        items.append(SourceItem(
            source_id=source_id,
            release=release,
            item_id=item_id,
            kind_hint="text",
            text=passage,
            heading_path=heading_path,
            attributes=attributes,
            links=(),
            file=relative,
            sha256=digest,
        ))
    return tuple(items)


def _attributes(obj: dict) -> dict[str, str]:
    attributes = {}
    for key, value in obj.items():
        if key in _BODY_KEYS or value is None or isinstance(value, (dict, list)):
            continue
        attributes[str(key)] = value if isinstance(value, str) else str(value)
    return attributes


def _release_of(obj: dict) -> str | None:
    release = obj.get("release")
    if isinstance(release, str) and release.strip():
        return release.strip()
    return None


def _items_from_file(path: Path, root: Path) -> _FileItems:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot parse {relative}: {exc}") from exc
        if not isinstance(obj, dict):
            raise RuntimeError(f"cannot parse {relative}: law JSON must be an object")
        source_id = str(obj["id"]).strip() if obj.get("id") not in (None, "") else path.stem
        title = str(obj.get("titlu") or "").strip()
        body = str(obj.get("text_complet") or obj.get("text") or obj.get("content") or obj.get("body") or "")
        heading = (title,) if title else ()
        items = _split_articles(
            body, source_id, _release_of(obj), heading, _attributes(obj), relative, digest)
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise RuntimeError(f"cannot parse {relative}: {exc}") from exc
        items = _split_articles(text, path.stem, None, (), {}, relative, digest)
        source_id = path.stem
    return _FileItems(relative, digest, source_id, items)


def read_law_files(corpus_root: Path) -> list[_FileItems]:
    """One group per document. A file that cannot be parsed raises."""
    root = Path(corpus_root)
    if not root.is_dir():
        raise RuntimeError(f"corpus root is not a directory: {root}")
    paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _DOC_SUFFIXES
    )
    return [_items_from_file(path, root) for path in paths]


def _select(files: list[_FileItems], seed: str, items: int):
    """Hash order, at most 4 items per file. Every fourth source is sealed.

    The seal follows ``source_id``, not the file. A source that spans files
    stays in one sheet. A corpus whose files are already one source each
    seals the same files as a per-file count.
    """
    ordered = sorted((group for group in files if group.items), key=lambda group: _rank(seed, group.file))
    chosen = []
    remaining = items
    source_ordinal = {}
    for group in ordered:
        if remaining <= 0:
            break
        ordinal = source_ordinal.get(group.source_id)
        if ordinal is None:
            ordinal = len(source_ordinal)
            source_ordinal[group.source_id] = ordinal
        ranked = tuple(sorted(
            group.items,
            key=lambda item: (_item_rank(seed, group.file, item.item_id), item.item_id),
        ))
        take = ranked[: min(_MAX_PER_FILE, remaining)]
        chosen.append((group, take, (ordinal + 1) % 4 == 0))
        remaining -= len(take)
    return chosen, len(ordered)


def _counts_line(source_files: int, rows: int, sealed_rows: int) -> str:
    return f"source_files={source_files} rows={rows} sealed_rows={sealed_rows}"


def _header(labels: tuple[str, ...]) -> tuple[str, ...]:
    return ("packet_id", "source_id", "item_id", "passage", *labels)


def _from_reader(item, relative: str, digest: str) -> SourceItem:
    return SourceItem(
        source_id=item.source_id,
        release=item.release,
        item_id=item.item_id,
        kind_hint=item.kind_hint,
        text=item.text,
        heading_path=tuple(item.heading_path),
        attributes=dict(item.attributes),
        links=tuple(item.links),
        file=relative,
        sha256=digest,
    )


def read_automotive_files(corpus_root: Path) -> list[_FileItems]:
    """One group per file the ingestion readers know. A bad file raises."""
    from grap_mcp.ingest import _READERS

    root = Path(corpus_root)
    if not root.is_dir():
        raise RuntimeError(f"corpus root is not a directory: {root}")
    paths = sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in _READERS
        ),
        key=lambda path: path.as_posix(),
    )
    groups = []
    for path in paths:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            read_items = list(_READERS[path.suffix.lower()](path, corpus_root=root))
        except Exception as exc:
            raise RuntimeError(f"cannot parse {relative}: {exc}") from exc
        items = tuple(
            _from_reader(item, relative, digest)
            for item in read_items
            if item.kind_hint in _STATEMENT_KINDS
            and _MIN_CHARS <= len(item.text) <= _MAX_CHARS
        )
        source_id = items[0].source_id if items else path.stem
        groups.append(_FileItems(relative, digest, source_id, items))
    return groups


def _rows(taken: tuple[SourceItem, ...], labels: tuple[str, ...]) -> list[dict[str, str]]:
    rows = []
    for item in taken:
        row = {
            "packet_id": f"{item.source_id}#{item.item_id}",
            "source_id": item.source_id,
            "item_id": item.item_id,
            "passage": item.text,
        }
        for column in labels:
            row[column] = ""
        rows.append(row)
    return rows


def _write_csv(path: Path, rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build_packet(corpus_root: Path, corpus: str, seed: str, items: int, out: Path) -> None:
    if corpus == "automotive":
        labels = _AUTOMOTIVE_LABELS
        read = read_automotive_files
    elif corpus == "law-ro":
        labels = _LAW_LABELS
        read = read_law_files
    else:
        raise RuntimeError(f"unknown corpus: {corpus}")
    if items < 0:
        raise RuntimeError("--items must be zero or greater")
    chosen, source_files = _select(read(corpus_root), str(seed), items)
    open_rows = []
    sealed_rows = []
    for group, taken, sealed in chosen:
        target = sealed_rows if sealed else open_rows
        target.extend(_rows(taken, labels))
    rows = len(open_rows) + len(sealed_rows)
    print(_counts_line(source_files, rows, len(sealed_rows)))
    if source_files < _MIN_SOURCE_FILES or rows < items or not sealed_rows:
        raise SystemExit(1)
    columns = _header(labels)
    destination = Path(out)
    _write_csv(destination / "open" / "statements.csv", open_rows, columns)
    _write_csv(destination / "sealed" / "statements.csv", sealed_rows, columns)
    manifest = {
        "corpus": corpus,
        "file_hashes": [
            {"file": group.file, "sha256": group.sha256, "source_id": group.source_id}
            for group, _taken, _sealed in chosen
        ],
        "sealed_ids": [group.source_id for group, _taken, sealed in chosen if sealed],
        "seed": str(seed),
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write an empty statement gold packet (Sheet 1).")
    parser.add_argument("--corpus-root", required=True, type=Path)
    parser.add_argument("--corpus", required=True, choices=("law-ro", "automotive"))
    parser.add_argument("--seed", required=True)
    parser.add_argument("--items", type=int, default=150)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    build_packet(args.corpus_root, args.corpus, args.seed, args.items, args.out)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        sys.stderr.write(str(exc) + "\n")
        sys.exit(1)
