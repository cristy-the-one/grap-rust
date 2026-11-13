#!/usr/bin/env python3
"""Write an empty Sheet 2 pair packet from the files a statement packet samples.

Mentions come from an extraction receipt beside a sampled file when that file
exists (``<file>.receipt.json``). Otherwise they are capitalised noun phrases
and identifiers found by regex. Mention names are embedded with the configured
embedder. The packet is 200 pairs in four disjoint bands:

- 80 with cosine >= 0.86, and names that are not the same string
- 60 with cosine in [0.70, 0.86)
- 30 with cosine below 0.70 (the seeded stand-in for a random draw)
- 30 whose names are string-identical and whose documents differ

Each band is drawn over distinct name pairs: one occurrence pair per name
pair, the occurrence ``sha256(seed + mention keys)`` ranks first. No name is
written on more than 3 rows. A name that occurs in more than 20% of the
sampled documents is page furniture and is dropped; the report prints how
many names were dropped out of how many distinct names. A pair is written to
the sealed sheet when either document is sealed in the statement sample, so
a sealed sentence never lands in the open sheet. Label columns are left blank.

Stops, without writing, when any band is short. The shortfall is printed and
nothing is borrowed from another band. Every count is printed with its
denominator. ``n/a`` is printed for sheet sizes when nothing was written.

    python scripts/make_pair_packet.py --corpus-root <dir> --corpus law-ro \\
        --seed 20261112 --items 150 --out packet-out
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "mcp-server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import make_gold_packet as statements  # noqa: E402
from grap_mcp.kee.embeddings import get_embedder  # noqa: E402
from grap_mcp.kee.legal import _REF_PATTERN  # noqa: E402
from grap_mcp.kee.legal_rules import _ARTICLE  # noqa: E402

# 80 / 60 / 30 / 30. Identical strings are their own band even when cosine is 1.
# A name may occupy at most this many rows in the whole packet.
_NAME_CAP = 3
_BANDS = (
    ("cosine_high", 80),
    ("cosine_mid", 60),
    ("random", 30),
    ("identical", 30),
)
_HIGH = 0.86
_MID = 0.70

_WORD = r"[A-ZĂÂÎȘŞȚŢ][A-Za-zĂÂÎȘŞȚŢăâîșşțţ0-9-]*"
_NP = re.compile(rf"\b{_WORD}(?:\s+{_WORD})+\b")
_CODE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")
_ABBREV = re.compile(r"\b[A-ZĂÂÎȘŞȚŢ]{2,}\b")

_COLUMNS = (
    "packet_id",
    "source_id",
    "item_id",
    "passage",
    "name",
    "other_source_id",
    "other_item_id",
    "other_passage",
    "other_name",
    "label",
    "note",
)


@dataclass(frozen=True)
class Mention:
    source_id: str
    item_id: str
    name: str
    sentence: str
    start: int
    sealed: bool


def band_for_cosine(value: float) -> str:
    """Band of a pair whose names are not the same string."""
    if value >= _HIGH:
        return "cosine_high"
    if value >= _MID:
        return "cosine_mid"
    return "random"


def regex_mention_names(text: str) -> list[str]:
    """Capitalised phrases and identifiers, in order, without overlaps."""
    return [name for name, _start, _end in _regex_spans(text)]


def _regex_spans(text: str) -> list[tuple[str, int, int]]:
    spans = []
    for pattern in (_NP, _REF_PATTERN, _CODE, _ABBREV):
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), match.group(0)))
    spans.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    chosen = []
    cursor = -1
    for start, end, name in spans:
        if start < cursor:
            continue
        chosen.append((name.strip(), start, end))
        cursor = end
    return chosen


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _key(mention: Mention) -> str:
    return f"{mention.source_id}\t{mention.item_id}\t{mention.start}\t{mention.name}"


def _canonical(left: Mention, right: Mention) -> tuple[Mention, Mention]:
    if _key(left) <= _key(right):
        return left, right
    return right, left


def _rank(seed: str, left: Mention, right: Mention) -> str:
    first, second = sorted((_key(left), _key(right)))
    return hashlib.sha256(f"{seed}{first}\n{second}".encode("utf-8")).hexdigest()


def _sentence(text: str, start: int, end: int) -> str:
    begin = 0
    for mark in ".!?\n":
        pos = text.rfind(mark, 0, start)
        if pos + 1 > begin:
            begin = pos + 1
    stop = len(text)
    for mark in ".!?":
        pos = text.find(mark, end)
        if pos >= 0:
            stop = min(stop, pos + 1)
    return text[begin:stop].strip()


def _item_id(text: str, start: int, source_id: str) -> str:
    matches = list(_ARTICLE.finditer(text))
    seen: dict[str, int] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        passage = text[match.start():end].strip()
        kept = statements._MIN_CHARS <= len(passage) <= statements._MAX_CHARS
        label = statements._article_label(match) or f"{source_id}#{index}"
        if match.start() <= start < end:
            if not kept:
                return f"{source_id}#{index}"
            occurrence = seen.get(label, 0)
            return label if occurrence == 0 else f"{label}#{occurrence}"
        if kept:
            seen[label] = seen.get(label, 0) + 1
    return f"{source_id}#0"


def _body(path: Path) -> str:
    raw = path.read_bytes()
    relative = path.name
    if path.suffix.lower() == ".json":
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot parse {relative}: {exc}") from exc
        if not isinstance(obj, dict):
            raise RuntimeError(f"cannot parse {relative}: document must be an object")
        return str(
            obj.get("text_complet") or obj.get("text") or obj.get("content") or obj.get("body") or ""
        )
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise RuntimeError(f"cannot parse {relative}: {exc}") from exc


def _locate(text: str, outcome: dict) -> tuple[str, int, int] | None:
    raw_name = outcome.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    name = raw_name.strip()
    evidence = outcome.get("evidence") if isinstance(outcome.get("evidence"), dict) else {}
    quote = evidence.get("quote") if isinstance(evidence.get("quote"), str) else ""
    start = evidence.get("start")
    end = evidence.get("end")
    if (type(start) is int and type(end) is int and 0 <= start <= end <= len(text)
            and text[start:end] == quote and name in quote):
        rel = quote.find(name)
        return name, start + rel, start + rel + len(name)
    if quote and name in quote:
        at = text.find(quote)
        if at >= 0:
            rel = quote.find(name)
            return name, at + rel, at + rel + len(name)
    at = text.find(name)
    if at >= 0:
        return name, at, at + len(name)
    return None


def _receipt_records(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    evidence = payload.get("extraction_evidence")
    if isinstance(evidence, dict):
        return [record for record in evidence.values() if isinstance(record, dict)]
    if isinstance(payload.get("outcomes"), list):
        return [payload]
    return []


def _mentions_from_receipt(text: str, path: Path, source_id: str, sealed: bool) -> list[Mention]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot parse {path.name}: {exc}") from exc
    found = []
    for record in _receipt_records(payload):
        for outcome in record.get("outcomes") or []:
            if isinstance(outcome, dict):
                located = _locate(text, outcome)
                if located is not None:
                    found.append(located)
    return _pack(text, source_id, sealed, found)


def _body_start(text: str) -> int:
    """Where the first article begins, or 0 when the text has no article marker.

    Everything before it is the document header: issuer, publication line,
    title. Capitalised runs there are page furniture, not names, and each
    document words its header a little differently, so a frequency filter
    over whole names does not catch them.
    """
    match = _ARTICLE.search(text)
    return match.start() if match else 0


def _is_name(name: str) -> bool:
    """At least four letters. Roman numerals and list markers are not names."""
    return sum(1 for ch in name if ch.isalpha()) >= 4


def _mentions_from_regex(text: str, source_id: str, sealed: bool) -> list[Mention]:
    start_at = _body_start(text)
    found = [(name, start, end) for name, start, end in _regex_spans(text)
             if start >= start_at and _is_name(name)]
    return _pack(text, source_id, sealed, found)


def _pack(text: str, source_id: str, sealed: bool, found: list[tuple[str, int, int]]) -> list[Mention]:
    mentions = []
    seen = set()
    for name, start, end in found:
        token = (start, end, name)
        if token in seen:
            continue
        seen.add(token)
        sentence = _sentence(text, start, end)
        if name not in sentence:
            continue
        mentions.append(Mention(
            source_id=source_id,
            item_id=_item_id(text, start, source_id),
            name=name,
            sentence=sentence,
            start=start,
            sealed=sealed,
        ))
    return mentions


def _collect(chosen, root: Path) -> list[Mention]:
    mentions = []
    for group, _taken, sealed in chosen:
        path = root / group.file
        text = _body(path)
        receipt = path.with_name(path.name + ".receipt.json")
        if receipt.is_file():
            mentions.extend(_mentions_from_receipt(text, receipt, group.source_id, sealed))
        else:
            mentions.extend(_mentions_from_regex(text, group.source_id, sealed))
    return mentions


def _drop_common(mentions: list[Mention], n_docs: int):
    """Drop names that occur in more than 20% of the sampled documents.

    ``len(docs) * 5 > n_docs`` is the integer form of ``len(docs) / n_docs > 1/5``.
    Returns the kept mentions and ``(dropped, distinct names)``, or ``None``
    when there are no documents or no names to measure.
    """
    by_docs: dict[str, set[str]] = {}
    for mention in mentions:
        by_docs.setdefault(mention.name, set()).add(mention.source_id)
    total = len(by_docs)
    if n_docs <= 0 or total == 0:
        return mentions, None
    dropped = {name for name, docs in by_docs.items() if len(docs) * 5 > n_docs}
    kept = [mention for mention in mentions if mention.name not in dropped]
    return kept, (len(dropped), total)


def _pair_sort_key(seed: str, left: Mention, right: Mention):
    return (_rank(seed, left, right), _key(left), _key(right))


def _pools(mentions: list[Mention], vectors: dict[str, list[float]], seed: str):
    """One occurrence pair per distinct name pair: the seeded-hash minimum."""
    by_name: dict[str, list[Mention]] = {}
    for mention in mentions:
        by_name.setdefault(mention.name, []).append(mention)
    grouped: dict[tuple[str, str], tuple[str, list[tuple[Mention, Mention]]]] = {}
    for name, group in by_name.items():
        occurrences = []
        for left, right in itertools.combinations(group, 2):
            if left.source_id == right.source_id:
                continue
            occurrences.append(_canonical(left, right))
        if occurrences:
            grouped[(name, name)] = ("identical", occurrences)
    names = sorted(by_name)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1:]:
            kind = band_for_cosine(_cosine(vectors[left_name], vectors[right_name]))
            occurrences = [
                _canonical(left, right)
                for left in by_name[left_name]
                for right in by_name[right_name]
            ]
            grouped[(left_name, right_name)] = (kind, occurrences)
    pools = {band: [] for band, _target in _BANDS}
    for band, occurrences in grouped.values():
        best = min(occurrences, key=lambda pair: _pair_sort_key(seed, pair[0], pair[1]))
        pools[band].append(best)
    return pools


def _take(pool: list[tuple[Mention, Mention]], target: int, seed: str, used: dict[str, int]):
    """Walk name pairs in hash order, skipping any name already on 3 rows."""
    taken = []
    for left, right in sorted(pool, key=lambda pair: _pair_sort_key(seed, pair[0], pair[1])):
        names = {left.name, right.name}
        if any(used.get(name, 0) >= _NAME_CAP for name in names):
            continue
        taken.append((left, right))
        for name in names:
            used[name] = used.get(name, 0) + 1
        if len(taken) == target:
            break
    return taken, len(pool)


def _report(taken, pool_sizes, n_mentions, n_docs, n_files, n_open, n_sealed,
            names_dropped, wrote: bool) -> None:
    parts = []
    filled_sum = 0
    target_sum = 0
    for band, target in _BANDS:
        filled = len(taken[band])
        filled_sum += filled
        target_sum += target
        parts.append(f"{band}={filled}/{target}")
        short = target - filled
        if short:
            parts.append(f"{band}_shortfall={short}")
        parts.append(f"{band}_pool={pool_sizes[band]}")
    parts.append(f"n_mentions={n_mentions}")
    parts.append(f"n_docs={n_docs}")
    parts.append(f"n_files_reached={n_files}")
    if names_dropped is None:
        parts.append("names_dropped=n/a")
    else:
        dropped, total = names_dropped
        parts.append(f"names_dropped={dropped}/{total}")
    parts.append(f"n_pairs={filled_sum}/{target_sum}")
    if wrote:
        parts.append(f"n_open={n_open}")
        parts.append(f"n_sealed={n_sealed}")
    else:
        parts.append("n_open=n/a")
        parts.append("n_sealed=n/a")
    print(" ".join(parts))


def _row(number: int, left: Mention, right: Mention) -> dict[str, str]:
    return {
        "packet_id": f"pair-{number:04d}",
        "source_id": left.source_id,
        "item_id": left.item_id,
        "passage": left.sentence,
        "name": left.name,
        "other_source_id": right.source_id,
        "other_item_id": right.item_id,
        "other_passage": right.sentence,
        "other_name": right.name,
        "label": "",
        "note": "",
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build_packet(corpus_root: Path, corpus: str, seed: str, items: int, out: Path,
                 embedder=None) -> None:
    if corpus == "automotive":
        statements.build_packet(corpus_root, corpus, str(seed), items, out)
        return
    if corpus != "law-ro":
        raise RuntimeError(f"unknown corpus: {corpus}")
    root = Path(corpus_root)
    chosen, n_files = statements._select(statements.read_law_files(root), str(seed), items)
    mentions, names_dropped = _drop_common(_collect(chosen, root), len(chosen))
    if embedder is None:
        embedder = get_embedder()
    names = sorted({mention.name for mention in mentions})
    vectors = {}
    if names:
        embedded = embedder.embed(names)
        if len(embedded) != len(names):
            raise RuntimeError("embedder returned a different number of vectors than names")
        vectors = dict(zip(names, embedded))
    pools = _pools(mentions, vectors, str(seed))
    used: dict[str, int] = {}
    taken = {}
    pool_sizes = {}
    for band, target in _BANDS:
        taken[band], pool_sizes[band] = _take(pools[band], target, str(seed), used)
    short = any(len(taken[band]) < target for band, target in _BANDS)
    if short:
        _report(
            taken, pool_sizes, len(mentions), len(chosen), n_files, 0, 0, names_dropped, wrote=False,
        )
        raise SystemExit(1)

    open_rows = []
    sealed_rows = []
    bands = {band: [] for band, _target in _BANDS}
    number = 0
    for band, _target in _BANDS:
        for left, right in taken[band]:
            row = _row(number, left, right)
            number += 1
            bands[band].append(row["packet_id"])
            if left.sealed or right.sealed:
                sealed_rows.append(row)
            else:
                open_rows.append(row)
    _report(
        taken, pool_sizes, len(mentions), len(chosen), n_files,
        len(open_rows), len(sealed_rows), names_dropped, wrote=True,
    )
    destination = Path(out)
    _write_csv(destination / "open" / "pairs.csv", open_rows)
    _write_csv(destination / "sealed" / "pairs.csv", sealed_rows)
    manifest = {
        "bands": bands,
        "corpus": corpus,
        "file_hashes": [
            {"file": group.file, "sha256": group.sha256, "source_id": group.source_id}
            for group, _taken, _sealed in chosen
        ],
        "items": items,
        "sealed_ids": [group.source_id for group, _taken, sealed in chosen if sealed],
        "seed": str(seed),
    }
    manifest_path = destination / "pair_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write an empty pair gold packet (Sheet 2).")
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
