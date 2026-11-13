"""G-02 pair packet generator.

Synthetic fixtures only. The generator must not be pointed at a real corpus here.
Band membership is recomputed from the embedder vectors, not from a label the
tool writes on the row. Selection order is sha256(seed + mention key), the same
rule the statement packet uses for items.
"""
import csv
import hashlib
import itertools
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import make_gold_packet as statements  # noqa: E402
import make_pair_packet as pairs  # noqa: E402


COLUMNS = [
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
]
BANDS = ("cosine_high", "cosine_mid", "random", "identical")
TARGETS = {"cosine_high": 80, "cosine_mid": 60, "random": 30, "identical": 30}


def cosine(left, right):
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm)


def mention_key(source_id, item_id, start, name):
    """Identity of one mention. The pair rank hashes the two keys."""
    return f"{source_id}\t{item_id}\t{start}\t{name}"


def pair_rank(seed, left_key, right_key):
    first, second = sorted((left_key, right_key))
    return hashlib.sha256(f"{seed}{first}\n{second}".encode("utf-8")).hexdigest()


def band_of(name, other, vectors):
    if name == other:
        return "identical"
    score = cosine(vectors[name], vectors[other])
    if score >= 0.86:
        return "cosine_high"
    if score >= 0.70:
        return "cosine_mid"
    return "random"


def _endpoint(row, side):
    if side == "left":
        return (row["source_id"], row["item_id"], row["name"], row["passage"])
    return (row["other_source_id"], row["other_item_id"], row["other_name"], row["other_passage"])


def _article(name, sentences):
    body = "+ Articolul 1\n" + " ".join(sentences)
    # Kept articles are 80 to 1,500 characters, so the file is sampled.
    return body + " " + ("a" * 80)


def _write_act(directory, source_id, body, outcomes):
    payload = {"id": source_id, "titlu": f"Titlu {source_id}", "text_complet": body}
    path = directory / f"{source_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    receipt = {
        "outcomes": [
            {
                "name": outcome["name"],
                "evidence": {
                    "status": "quoted",
                    "quote": outcome["quote"],
                    "start": outcome["start"],
                    "end": outcome["end"],
                },
            }
            for outcome in outcomes
        ]
    }
    path.with_name(path.name + ".receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return path


def _geometry(names):
    """Vectors whose within-group cosines fall in the bands the card names.

    High names share one axis. Mid names are equiangular at 0.8 in their own
    subspace. Identical, random, and every cross-group pair sit on other axes,
    so their cosine is 0.
    """
    total = 1 + 1 + (len(names["mid"]) + 1) + len(names["random"])
    vectors = {}
    for name in names["high"]:
        vector = [0.0] * total
        vector[0] = 1.0
        vectors[name] = vector
    for name in names["identical"]:
        vector = [0.0] * total
        vector[1] = 1.0
        vectors[name] = vector
    offset = 2
    for index, name in enumerate(names["mid"]):
        vector = [0.0] * total
        vector[offset] = math.sqrt(0.8)
        vector[offset + 1 + index] = math.sqrt(0.2)
        vectors[name] = vector
    rand_at = offset + len(names["mid"]) + 1
    for index, name in enumerate(names["random"]):
        vector = [0.0] * total
        vector[rand_at + index] = 1.0
        vectors[name] = vector
    return vectors


def _assert_geometry(names, vectors):
    assert cosine(vectors[names["high"][0]], vectors[names["high"][1]]) >= 0.86
    mid = cosine(vectors[names["mid"][0]], vectors[names["mid"][1]])
    assert 0.70 <= mid < 0.86
    assert cosine(vectors[names["high"][0]], vectors[names["mid"][0]]) < 0.70
    assert cosine(vectors[names["random"][0]], vectors[names["random"][1]]) < 0.70
    assert cosine(vectors[names["identical"][0]], vectors[names["high"][0]]) < 0.70


def build_corpus(root):
    """One mention per file, plus a second identical mention and a regex decoy.

    Receipts name the mentions. The decoy is a capitalised phrase in the file
    text and must not be emitted, because a receipt is present.
    """
    root.mkdir()
    names = {
        "high": [f"High{index:02d}" for index in range(14)],
        "mid": [f"Mid{index:02d}" for index in range(12)],
        "random": [f"Rand{index:02d}" for index in range(9)],
        "identical": ["Ident Token"],
    }
    vectors = _geometry(names)
    _assert_geometry(names, vectors)
    mentions = []
    specs = (
        [(name, f"src-h{index:02d}", "high") for index, name in enumerate(names["high"])]
        + [(name, f"src-m{index:02d}", "mid") for index, name in enumerate(names["mid"])]
        + [(name, f"src-r{index:02d}", "random") for index, name in enumerate(names["random"])]
        + [("Ident Token", f"src-i{index:02d}", "identical") for index in range(9)]
    )
    for index, (name, source_id, _group) in enumerate(specs):
        first = f"{name} is named in this sentence."
        sentences = [first]
        outcomes = []
        if index == 0:
            sentences.append("Decoy Phrase appears.")
        if source_id == "src-i00":
            sentences.append("Ident Token is named again here.")
        body = _article(name, sentences)
        for sentence in sentences:
            if sentence == "Decoy Phrase appears.":
                continue
            start = body.index(sentence)
            outcomes.append({
                "name": name,
                "quote": sentence,
                "start": start,
                "end": start + len(sentence),
            })
            mentions.append({
                "source_id": source_id,
                "item_id": "Articolul 1",
                "name": name,
                "start": body.index(name, start),
                "passage": sentence,
            })
        _write_act(root, source_id, body, outcomes)
    return mentions, vectors


class MapEmbedder:
    def __init__(self, vectors):
        self.vectors = vectors

    def embed(self, texts):
        return [list(self.vectors[text]) for text in texts]


def run(monkeypatch, root, out, seed, vectors, items=150):
    monkeypatch.setattr(pairs, "get_embedder", lambda: MapEmbedder(vectors))
    pairs.main([
        "--corpus-root", str(root),
        "--corpus", "law-ro",
        "--seed", str(seed),
        "--items", str(items),
        "--out", str(out),
    ])


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == COLUMNS
        return list(reader)


def all_rows(out):
    rows = []
    for part in ("open", "sealed"):
        path = out / part / "pairs.csv"
        if path.exists():
            rows.extend(read_rows(path))
    return rows


def _canonical(mention, other):
    left = mention_key(mention["source_id"], mention["item_id"], mention["start"], mention["name"])
    right = mention_key(other["source_id"], other["item_id"], other["start"], other["name"])
    if left <= right:
        return mention, other
    return other, mention


def _pools(mentions, vectors):
    pools = {band: [] for band in BANDS}
    for left, right in itertools.combinations(mentions, 2):
        if (left["source_id"], left["item_id"], left["start"], left["name"]) == (
            right["source_id"], right["item_id"], right["start"], right["name"]
        ):
            continue
        kind = band_of(left["name"], right["name"], vectors)
        if kind == "identical" and left["source_id"] == right["source_id"]:
            continue
        first, second = _canonical(left, right)
        pools[kind].append((first, second))
    return pools


def _select(pools, seed):
    chosen = {}
    for band in BANDS:
        def rank(pair, seed=seed):
            left, right = pair
            left_key = mention_key(left["source_id"], left["item_id"], left["start"], left["name"])
            right_key = mention_key(right["source_id"], right["item_id"], right["start"], right["name"])
            return pair_rank(seed, left_key, right_key), left_key, right_key

        ordered = sorted(pools[band], key=rank)
        chosen[band] = ordered[: TARGETS[band]]
    return chosen


def _file_order(pools):
    chosen = {}
    for band in BANDS:
        ordered = sorted(
            pools[band],
            key=lambda pair: (
                pair[0]["source_id"], pair[0]["start"], pair[0]["name"],
                pair[1]["source_id"], pair[1]["start"], pair[1]["name"],
            ),
        )
        chosen[band] = ordered[: TARGETS[band]]
    return chosen


def _identity(mention):
    return (mention["source_id"], mention["item_id"], mention["name"], mention["passage"])


def _row_identity(row):
    left = _endpoint(row, "left")
    right = _endpoint(row, "right")
    return (left, right) if left <= right else (right, left)


def _pick_seed(mentions, vectors):
    pools = _pools(mentions, vectors)
    by_file = _file_order(pools)
    for number in range(1, 40):
        seed = str(number)
        chosen = _select(pools, seed)
        high = [_identity(pair[0]) + _identity(pair[1]) for pair in chosen["cosine_high"]]
        filed = [_identity(pair[0]) + _identity(pair[1]) for pair in by_file["cosine_high"]]
        if high != filed and all(len(chosen[band]) == TARGETS[band] for band in BANDS):
            return seed
    raise AssertionError("no seed separated hash order from file order")


def test_band_counts_are_exact_and_hash_seeded(tmp_path, monkeypatch, capsys):
    root = tmp_path / "corpus"
    mentions, vectors = build_corpus(root)
    seed = _pick_seed(mentions, vectors)
    out = tmp_path / "out"
    run(monkeypatch, root, out, seed, vectors)
    printed = capsys.readouterr().out
    rows = all_rows(out)
    counts = Counter(band_of(row["name"], row["other_name"], vectors) for row in rows)
    assert dict(counts) == TARGETS
    assert f"cosine_high=80/80" in printed
    assert f"cosine_mid=60/60" in printed
    assert f"random=30/30" in printed
    assert f"identical=30/30" in printed
    assert "n_mentions=45" in printed
    assert "n_docs=44" in printed
    assert "n_files_reached=44" in printed
    assert "n_pairs=200/200" in printed

    seen = set()
    for row in rows:
        left = _endpoint(row, "left")
        right = _endpoint(row, "right")
        assert left != right
        assert left[2] in left[3] and right[2] in right[3]
        assert row["source_id"] and row["other_source_id"]
        assert row["label"] == "" and row["note"] == ""
        key = frozenset((left, right))
        assert key not in seen
        seen.add(key)
        if row["name"] == row["other_name"]:
            assert row["source_id"] != row["other_source_id"]
    assert "Decoy Phrase" not in {row["name"] for row in rows}
    assert "Decoy Phrase" not in {row["other_name"] for row in rows}

    pools = _pools(mentions, vectors)
    expected = _select(pools, seed)
    got = {band: [] for band in BANDS}
    for row in sorted(rows, key=lambda item: item["packet_id"]):
        got[band_of(row["name"], row["other_name"], vectors)].append(
            (_endpoint(row, "left"), _endpoint(row, "right"))
        )
    for band in BANDS:
        want = [(_identity(pair[0]), _identity(pair[1])) for pair in expected[band]]
        assert got[band] == want
    filed = _file_order(pools)
    filed_high = [(_identity(pair[0]), _identity(pair[1])) for pair in filed["cosine_high"]]
    assert got["cosine_high"] != filed_high

    open_rows = read_rows(out / "open" / "pairs.csv")
    sealed_rows = read_rows(out / "sealed" / "pairs.csv")
    assert len(open_rows) + len(sealed_rows) == 200
    assert open_rows and sealed_rows
    assert f"n_open={len(open_rows)}" in printed
    assert f"n_sealed={len(sealed_rows)}" in printed
    chosen, reached = statements._select(statements.read_law_files(root), seed, 150)
    assert reached == 44
    sealed_ids = {group.source_id for group, _taken, sealed in chosen if sealed}
    sampled_ids = {group.source_id for group, _taken, _sealed in chosen}
    assert sealed_ids
    for row in open_rows:
        assert row["source_id"] in sampled_ids and row["other_source_id"] in sampled_ids
        assert row["source_id"] not in sealed_ids and row["other_source_id"] not in sealed_ids
    for row in sealed_rows:
        assert row["source_id"] in sealed_ids or row["other_source_id"] in sealed_ids

    manifest = json.loads((out / "pair_manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == seed
    assert manifest["corpus"] == "law-ro"
    by_id = {row["packet_id"]: row for row in rows}
    for band in BANDS:
        assert len(manifest["bands"][band]) == TARGETS[band]
        for packet_id in manifest["bands"][band]:
            row = by_id[packet_id]
            assert band_of(row["name"], row["other_name"], vectors) == band
    assert set(manifest["sealed_ids"]) == sealed_ids


def test_same_seed_is_byte_identical(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    _mentions, vectors = build_corpus(root)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run(monkeypatch, root, out_a, "4", vectors)
    run(monkeypatch, root, out_b, "4", vectors)
    for relative in ("open/pairs.csv", "sealed/pairs.csv", "pair_manifest.json"):
        assert (out_a / relative).read_bytes() == (out_b / relative).read_bytes()


def test_different_seed_selects_different_pairs(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    _mentions, vectors = build_corpus(root)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run(monkeypatch, root, out_a, "1", vectors)
    run(monkeypatch, root, out_b, "2", vectors)
    left = {_row_identity(row) for row in all_rows(out_a)}
    right = {_row_identity(row) for row in all_rows(out_b)}
    assert left != right


def test_shortfall_exits_nonzero_and_does_not_top_up(tmp_path, monkeypatch, capsys):
    """High pairs are plentiful. Nothing may be moved across to fill the other bands."""
    root = tmp_path / "corpus"
    root.mkdir()
    names = [f"High{index:02d}" for index in range(14)]
    vector = [1.0, 0.0]
    vectors = {name: vector for name in names}
    for name in names:
        sentence = f"{name} is named in this sentence."
        body = _article(name, [sentence])
        start = body.index(sentence)
        _write_act(root, f"src-{name}", body, [{
            "name": name, "quote": sentence, "start": start, "end": start + len(sentence),
        }])
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as caught:
        run(monkeypatch, root, out, "1", vectors)
    assert caught.value.code not in (0, None)
    printed = capsys.readouterr().out
    assert "cosine_high=80/80" in printed
    assert "cosine_high_pool=91" in printed
    assert "cosine_mid=0/60" in printed
    assert "cosine_mid_shortfall=60" in printed
    assert "cosine_mid_pool=0" in printed
    assert "random=0/30" in printed
    assert "random_shortfall=30" in printed
    assert "identical=0/30" in printed
    assert "identical_shortfall=30" in printed
    assert "n_mentions=14" in printed
    assert "n_docs=14" in printed
    assert "n_pairs=80/200" in printed
    assert "n_open=n/a" in printed
    assert "n_sealed=n/a" in printed
    assert not (out / "open" / "pairs.csv").exists()
    assert not (out / "sealed" / "pairs.csv").exists()
    assert not (out / "pair_manifest.json").exists()


def test_regex_finds_capitalised_phrases_and_identifiers():
    text = (
        "+ Articolul 1 Ministerul Finantelor applies Legea nr. 58/1998 "
        "and SWS_Xyz_00042 today. Guvernul României meets BNR."
    )
    assert pairs.regex_mention_names(text) == [
        "Ministerul Finantelor",
        "Legea nr. 58/1998",
        "SWS_Xyz_00042",
        "Guvernul României",
        "BNR",
    ]


def test_cosine_band_boundaries():
    assert pairs.band_for_cosine(0.86) == "cosine_high"
    assert pairs.band_for_cosine(0.859) == "cosine_mid"
    assert pairs.band_for_cosine(0.70) == "cosine_mid"
    assert pairs.band_for_cosine(0.699) == "random"


def test_automotive_names_the_missing_readers(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    with pytest.raises(RuntimeError) as caught:
        pairs.main([
            "--corpus-root", str(root),
            "--corpus", "automotive",
            "--seed", "1",
            "--items", "4",
            "--out", str(tmp_path / "out"),
        ])
    message = str(caught.value).lower()
    assert "dbc" in message
    assert "pdf" in message
    assert "reqif" in message
    assert "arxml" in message


def test_generator_does_not_call_source_units():
    script = Path(__file__).resolve().parents[2] / "scripts" / "make_pair_packet.py"
    assert "source_units" not in script.read_text(encoding="utf-8")
