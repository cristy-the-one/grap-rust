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


_DOCS = 10
_FURNITURE = "Page Header"
_BORDER = "Border Line"


def _quads(prefix, groups):
    return [[f"{prefix}{group:02d}{member}" for member in range(4)] for group in range(groups)]


def build_corpus(root):
    """Ten documents, disjoint within-band groups, and one furniture name.

    High groups share a private axis (cosine 1). Mid groups are equiangular at
    0.8 inside a private subspace. Random names are mutually orthogonal.
    Identical names each occur in two documents, which is exactly a fifth of
    ten and so is kept. Page Header occurs in three documents and must be
    dropped. A decoy phrase sits in the text of a file that has a receipt.
    """
    root.mkdir()
    high_groups = _quads("H", 14)
    mid_groups = _quads("M", 10)
    rand_groups = _quads("R", 5)
    identical = [f"I{index:02d}" for index in range(50)]
    high_dims = len(high_groups)
    mid_dims = len(mid_groups) * 5
    rand_dims = len(rand_groups) * 4
    total = high_dims + mid_dims + rand_dims + len(identical) + 1
    vectors = {}
    for index, group in enumerate(high_groups):
        vector = [0.0] * total
        vector[index] = 1.0
        for name in group:
            vectors[name] = list(vector)
    for index, group in enumerate(mid_groups):
        offset = high_dims + index * 5
        for member, name in enumerate(group):
            vector = [0.0] * total
            vector[offset] = math.sqrt(0.8)
            vector[offset + 1 + member] = math.sqrt(0.2)
            vectors[name] = vector
    rand_at = high_dims + mid_dims
    for index, group in enumerate(rand_groups):
        for member, name in enumerate(group):
            vector = [0.0] * total
            vector[rand_at + index * 4 + member] = 1.0
            vectors[name] = vector
    ident_at = rand_at + rand_dims
    for index, name in enumerate(identical):
        vector = [0.0] * total
        vector[ident_at + index] = 1.0
        vectors[name] = vector
    border = [0.0] * total
    border[-1] = 1.0
    vectors[_BORDER] = border
    assert cosine(vectors[high_groups[0][0]], vectors[high_groups[0][1]]) >= 0.86
    assert cosine(vectors[high_groups[0][0]], vectors[high_groups[1][0]]) < 0.70
    mid = cosine(vectors[mid_groups[0][0]], vectors[mid_groups[0][1]])
    assert 0.70 <= mid < 0.86
    assert cosine(vectors[rand_groups[0][0]], vectors[rand_groups[0][1]]) < 0.70

    placements = []
    singles = [name for group in high_groups + mid_groups + rand_groups for name in group]
    for index, name in enumerate(singles):
        placements.append((index % _DOCS, name, f"{name} is named in this sentence."))
    for index, name in enumerate(identical):
        placements.append((index % _DOCS, name, f"{name} is named in this sentence."))
        placements.append(((index + 1) % _DOCS, name, f"{name} is named again here."))
    placements.append((0, _BORDER, f"{_BORDER} is named in this sentence."))
    placements.append((1, _BORDER, f"{_BORDER} is named again here."))
    for doc in (0, 1, 2):
        placements.append((doc, _FURNITURE, f"{_FURNITURE} is named in this sentence."))

    by_doc = {doc: [] for doc in range(_DOCS)}
    for doc, name, sentence in placements:
        by_doc[doc].append((name, sentence))
    mentions = []
    for doc in range(_DOCS):
        source_id = f"src-{doc:02d}"
        named = list(by_doc[doc])
        sentences = [sentence for _name, sentence in named]
        if doc == 0:
            sentences.append("Decoy Phrase appears.")
        body = "+ Articolul 1\n" + " ".join(sentences)
        if len(body) < 80:
            body += " " + ("a" * 80)
        assert 80 <= len(body.strip()) <= 1500
        outcomes = []
        for name, sentence in named:
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


def _dropped_names(mentions, n_docs):
    docs = {}
    for mention in mentions:
        docs.setdefault(mention["name"], set()).add(mention["source_id"])
    if n_docs <= 0 or not docs:
        return set(), len(docs)
    dropped = {name for name, sources in docs.items() if len(sources) * 5 > n_docs}
    return dropped, len(docs)


def _kept(mentions, n_docs):
    dropped, total = _dropped_names(mentions, n_docs)
    return [mention for mention in mentions if mention["name"] not in dropped], dropped, total


def _hash_key(pair, seed):
    left_key = mention_key(pair[0]["source_id"], pair[0]["item_id"], pair[0]["start"], pair[0]["name"])
    right_key = mention_key(pair[1]["source_id"], pair[1]["item_id"], pair[1]["start"], pair[1]["name"])
    return pair_rank(seed, left_key, right_key), left_key, right_key


def _file_key(pair):
    left, right = pair
    return (
        left["source_id"], left["start"], left["name"],
        right["source_id"], right["start"], right["name"],
    )


def _grouped(mentions, vectors):
    by_name = {}
    for mention in mentions:
        by_name.setdefault(mention["name"], []).append(mention)
    groups = {}
    for name, group in by_name.items():
        occurrences = []
        for left, right in itertools.combinations(group, 2):
            if left["source_id"] == right["source_id"]:
                continue
            occurrences.append(_canonical(left, right))
        if occurrences:
            groups[(name, name)] = ("identical", occurrences)
    names = sorted(by_name)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1:]:
            occurrences = [
                _canonical(left, right)
                for left in by_name[left_name]
                for right in by_name[right_name]
            ]
            groups[(left_name, right_name)] = (
                band_of(left_name, right_name, vectors), occurrences,
            )
    return groups


def _representatives(groups, choose):
    pools = {band: [] for band in BANDS}
    for _key, (band, occurrences) in groups.items():
        pools[band].append(choose(occurrences))
    return pools


def _cap(pools, order_key):
    used = Counter()
    chosen = {}
    for band in BANDS:
        taken = []
        for pair in sorted(pools[band], key=order_key):
            names = {pair[0]["name"], pair[1]["name"]}
            if any(used[name] >= 3 for name in names):
                continue
            taken.append(pair)
            for name in names:
                used[name] += 1
            if len(taken) == TARGETS[band]:
                break
        chosen[band] = taken
    return chosen


def _select(mentions, vectors, seed, n_docs):
    kept, dropped, total = _kept(mentions, n_docs)
    groups = _grouped(kept, vectors)
    reps = _representatives(
        groups, lambda occurrences: min(occurrences, key=lambda pair: _hash_key(pair, seed)),
    )
    return _cap(reps, lambda pair: _hash_key(pair, seed)), dropped, total, len(kept)


def _file_select(mentions, vectors, n_docs):
    kept, _dropped, _total = _kept(mentions, n_docs)
    groups = _grouped(kept, vectors)
    reps = _representatives(groups, lambda occurrences: min(occurrences, key=_file_key))
    return _cap(reps, _file_key)


def _identity(mention):
    return (mention["source_id"], mention["item_id"], mention["name"], mention["passage"])


def _row_identity(row):
    left = _endpoint(row, "left")
    right = _endpoint(row, "right")
    return (left, right) if left <= right else (right, left)


def _sampled(root, seed, items=150):
    chosen, reached = statements._select(statements.read_law_files(root), str(seed), items)
    return chosen, reached


def _pick_seed(mentions, vectors, n_docs):
    for number in range(1, 40):
        seed = str(number)
        chosen, _dropped, _total, _kept_count = _select(mentions, vectors, seed, n_docs)
        filed = _file_select(mentions, vectors, n_docs)
        if not all(len(chosen[band]) == TARGETS[band] for band in BANDS):
            continue
        high = [(_identity(pair[0]), _identity(pair[1])) for pair in chosen["cosine_high"]]
        filed_high = [(_identity(pair[0]), _identity(pair[1])) for pair in filed["cosine_high"]]
        if high != filed_high:
            return seed
    raise AssertionError("no seed separated hash order from file order")


def test_band_counts_are_exact_and_hash_seeded(tmp_path, monkeypatch, capsys):
    root = tmp_path / "corpus"
    mentions, vectors = build_corpus(root)
    sampled, reached = _sampled(root, "1")
    n_docs = len(sampled)
    seed = _pick_seed(mentions, vectors, n_docs)
    out = tmp_path / "out"
    run(monkeypatch, root, out, seed, vectors)
    printed = capsys.readouterr().out
    rows = all_rows(out)
    counts = Counter(band_of(row["name"], row["other_name"], vectors) for row in rows)
    assert dict(counts) == TARGETS
    expected, dropped, total_names, n_kept = _select(mentions, vectors, seed, n_docs)
    assert f"cosine_high=80/80" in printed
    assert f"cosine_mid=60/60" in printed
    assert f"random=30/30" in printed
    assert f"identical=30/30" in printed
    assert f"names_dropped={len(dropped)}/{total_names}" in printed
    assert f"n_mentions={n_kept}" in printed
    assert f"n_docs={n_docs}" in printed
    assert f"n_files_reached={reached}" in printed
    assert "n_pairs=200/200" in printed
    assert _FURNITURE in dropped and _BORDER not in dropped

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

    use_counts, name_pairs = _name_use(rows)
    assert len(name_pairs) == len(set(name_pairs)) == 200
    assert max(use_counts.values()) <= 3
    got = {band: [] for band in BANDS}
    for row in sorted(rows, key=lambda item: item["packet_id"]):
        got[band_of(row["name"], row["other_name"], vectors)].append(
            (_endpoint(row, "left"), _endpoint(row, "right"))
        )
    for band in BANDS:
        want = [(_identity(pair[0]), _identity(pair[1])) for pair in expected[band]]
        assert got[band] == want
    filed = _file_select(mentions, vectors, n_docs)
    filed_high = [(_identity(pair[0]), _identity(pair[1])) for pair in filed["cosine_high"]]
    assert got["cosine_high"] != filed_high

    open_rows = read_rows(out / "open" / "pairs.csv")
    sealed_rows = read_rows(out / "sealed" / "pairs.csv")
    assert len(open_rows) + len(sealed_rows) == 200
    assert open_rows and sealed_rows
    assert f"n_open={len(open_rows)}" in printed
    assert f"n_sealed={len(sealed_rows)}" in printed
    chosen, reached_again = statements._select(statements.read_law_files(root), seed, 150)
    assert reached_again == reached
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
    mentions = []
    for name in names:
        sentence = f"{name} is named in this sentence."
        body = _article(name, [sentence])
        start = body.index(sentence)
        mentions.append({
            "source_id": f"src-{name}",
            "item_id": "Articolul 1",
            "name": name,
            "start": body.index(name, start),
            "passage": sentence,
        })
    sampled, _reached = _sampled(root, "1")
    n_docs = len(sampled)
    expected, dropped, total_names, n_kept = _select(mentions, vectors, "1", n_docs)
    filled = len(expected["cosine_high"])
    assert filled < TARGETS["cosine_high"]
    assert len(expected["cosine_mid"]) == 0
    assert len(dropped) == 0
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as caught:
        run(monkeypatch, root, out, "1", vectors)
    assert caught.value.code not in (0, None)
    printed = capsys.readouterr().out
    assert f"cosine_high={filled}/80" in printed
    assert "cosine_high_shortfall=" in printed
    assert "cosine_high_pool=91" in printed
    assert "cosine_mid=0/60" in printed
    assert "cosine_mid_shortfall=60" in printed
    assert "cosine_mid_pool=0" in printed
    assert "random=0/30" in printed
    assert "random_shortfall=30" in printed
    assert "identical=0/30" in printed
    assert "identical_shortfall=30" in printed
    assert f"names_dropped=0/{total_names}" in printed
    assert f"n_mentions={n_kept}" in printed
    assert f"n_docs={n_docs}" in printed
    assert f"n_pairs={filled}/200" in printed
    assert "n_open=n/a" in printed
    assert "n_sealed=n/a" in printed
    assert not (out / "open" / "pairs.csv").exists()
    assert not (out / "sealed" / "pairs.csv").exists()
    assert not (out / "pair_manifest.json").exists()


def _name_use(rows):
    counts = Counter()
    name_pairs = []
    for row in rows:
        name_pairs.append(tuple(sorted((row["name"], row["other_name"]))))
        counts[row["name"]] += 1
        if row["other_name"] != row["name"]:
            counts[row["other_name"]] += 1
    return counts, name_pairs


def test_f12_one_row_per_name_pair_and_name_cap(tmp_path, monkeypatch):
    """Repeated occurrences of one name pair must not fill the sheet."""
    root = tmp_path / "corpus"
    _mentions, vectors = build_corpus(root)
    out = tmp_path / "out"
    run(monkeypatch, root, out, "1", vectors)
    counts, name_pairs = _name_use(all_rows(out))
    assert len(name_pairs) == len(set(name_pairs))
    assert max(counts.values()) <= 3


def test_f13_drops_names_in_more_than_a_fifth_of_documents(tmp_path, monkeypatch, capsys):
    """Page Header is in 3 of the sampled documents, which is more than a fifth."""
    root = tmp_path / "corpus"
    mentions, vectors = build_corpus(root)
    sampled, _reached = _sampled(root, "1")
    n_docs = len(sampled)
    dropped, total_names = _dropped_names(mentions, n_docs)
    furniture_docs = {mention["source_id"] for mention in mentions if mention["name"] == _FURNITURE}
    border_docs = {mention["source_id"] for mention in mentions if mention["name"] == _BORDER}
    assert len(furniture_docs) * 5 > n_docs
    assert len(border_docs) * 5 <= n_docs
    assert _FURNITURE in dropped and _BORDER not in dropped
    out = tmp_path / "out"
    run(monkeypatch, root, out, "1", vectors)
    printed = capsys.readouterr().out
    rows = all_rows(out)
    assert f"names_dropped={len(dropped)}/{total_names}" in printed
    assert all(row["name"] not in dropped and row["other_name"] not in dropped for row in rows)


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
