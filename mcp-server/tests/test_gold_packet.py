"""G-01 statement packet generator.

Synthetic fixtures only. The generator must not be pointed at a real corpus here.
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

import make_gold_packet as packet  # noqa: E402


COLUMNS = [
    "packet_id",
    "source_id",
    "item_id",
    "passage",
    "kind",
    "subject",
    "conditions",
    "exceptions",
    "dates_and_amounts",
    "acts_referred_to",
    "sure",
    "note",
]
LABEL_COLUMNS = COLUMNS[4:]


def article(number, body):
    return f"+ Articolul {number} {body}"


def valid_body(token):
    return token + " " + ("a" * 120)


def article_of_length(number, length):
    prefix = f"+ Articolul {number} "
    assert length >= len(prefix)
    return prefix + ("b" * (length - len(prefix)))


def write_act(directory, filename, source_id, articles):
    payload = {
        "id": source_id,
        "titlu": f"Titlu {source_id}",
        "text_complet": "\n".join(articles),
    }
    (directory / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def run(root, out, seed, items, corpus="law-ro"):
    packet.main([
        "--corpus-root", str(root),
        "--corpus", corpus,
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
    return read_rows(out / "open" / "statements.csv") + read_rows(out / "sealed" / "statements.csv")


def file_rank(seed, relative):
    return hashlib.sha256(f"{seed}{relative}".encode("utf-8")).hexdigest()


def test_same_seed_is_byte_identical(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    for index in range(40):
        articles = [article(number, valid_body(f"f{index}n{number}")) for number in range(1, 7)]
        write_act(root, f"act-{index}.json", f"src-{index}", articles)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run(root, out_a, seed="20261112", items=160)
    run(root, out_b, seed="20261112", items=160)
    for relative in ("open/statements.csv", "sealed/statements.csv", "manifest.json"):
        assert (out_a / relative).read_bytes() == (out_b / relative).read_bytes()


def test_different_seed_selects_different_items(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    for index in range(48):
        write_act(root, f"act-{index}.json", f"src-{index}", [article(1, valid_body(f"only-{index}"))])
    selected = []
    for seed in ("1", "2", "3", "4", "5", "6", "7", "8"):
        out = tmp_path / f"out-{seed}"
        run(root, out, seed=seed, items=4)
        rows = all_rows(out)
        assert len(rows) == 4
        selected.append(tuple(row["source_id"] for row in rows))
    assert len(set(selected)) > 1


def test_no_file_contributes_more_than_four_items(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    seed = "7"
    for index in range(40):
        articles = [article(number, valid_body(f"f{index}n{number}")) for number in range(1, 9)]
        write_act(root, f"act-{index}.json", f"src-{index}", articles)
    out = tmp_path / "out"
    run(root, out, seed=seed, items=160)
    rows = all_rows(out)
    by_source = {}
    for row in rows:
        by_source.setdefault(row["source_id"], []).append(row["item_id"])
    assert set(by_source) == {f"src-{index}" for index in range(40)}
    eligible = [f"Articolul {number}" for number in range(1, 9)]
    for source_id, item_ids in by_source.items():
        relative = f"act-{source_id.removeprefix('src-')}.json"
        expected = sorted(eligible, key=lambda item_id: item_rank(seed, relative, item_id))[:4]
        assert item_ids == expected
    for row in rows:
        assert row["packet_id"] == f"{row['source_id']}#{row['item_id']}"
        assert all(row[column] == "" for column in LABEL_COLUMNS)


def test_cap_keeps_the_first_four_items_that_pass_the_length_window(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    seed = "1"
    relative = "act-0.json"
    eligible = [f"Articolul {number}" for number in range(2, 8)]
    write_act(
        root, relative, "only",
        [article(1, "short")] + [article(number, valid_body(f"keep{number}")) for number in range(2, 8)],
    )
    for index in range(1, 40):
        write_act(
            root, f"act-{index}.json", f"src-{index}",
            [article(number, valid_body(f"f{index}n{number}")) for number in range(1, 5)],
        )
    out = tmp_path / "out"
    run(root, out, seed=seed, items=160)
    rows = [row for row in all_rows(out) if row["source_id"] == "only"]
    expected = sorted(eligible, key=lambda item_id: item_rank(seed, relative, item_id))[:4]
    assert [row["item_id"] for row in rows] == expected
    assert "short" not in "".join(row["passage"] for row in all_rows(out))


def test_sealed_and_open_share_no_source_id(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    seed = "9"
    for index in range(40):
        articles = [article(number, valid_body(f"s{index}a{number}")) for number in range(1, 3)]
        write_act(root, f"act-{index}.json", f"src-{index}", articles)
    out = tmp_path / "out"
    run(root, out, seed=seed, items=80)
    open_rows = read_rows(out / "open" / "statements.csv")
    sealed_rows = read_rows(out / "sealed" / "statements.csv")
    open_ids = {row["source_id"] for row in open_rows}
    sealed_ids = {row["source_id"] for row in sealed_rows}
    assert open_ids and sealed_ids
    assert open_ids.isdisjoint(sealed_ids)

    ordered = sorted(
        (path.relative_to(root).as_posix() for path in root.glob("*.json")),
        key=lambda relative: file_rank(seed, relative),
    )
    expected_sealed = []
    for index, relative in enumerate(ordered):
        if (index + 1) % 4 == 0:
            document = json.loads((root / relative).read_text(encoding="utf-8"))
            expected_sealed.append(str(document["id"]))
    assert sealed_ids == set(expected_sealed)
    assert len(sealed_ids) == 10
    assert len(open_ids) == 30

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == seed
    assert manifest["corpus"] == "law-ro"
    assert set(manifest["sealed_ids"]) == sealed_ids
    listed = {entry["file"]: entry for entry in manifest["file_hashes"]}
    assert set(listed) == {f"act-{index}.json" for index in range(40)}
    for name, entry in listed.items():
        raw = (root / name).read_bytes()
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
        assert entry["source_id"] in open_ids | sealed_ids


def test_keeps_text_from_80_to_1500_characters(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    kept = {
        "low": article_of_length(1, 80),
        "high": article_of_length(1, 1500),
    }
    write_act(root, "too-short.json", "short", [article_of_length(1, 79)])
    write_act(root, "low.json", "low", [kept["low"]])
    write_act(root, "high.json", "high", [kept["high"]])
    write_act(root, "too-long.json", "long", [article_of_length(1, 1501)])
    for index in range(40):
        write_act(root, f"fill-{index}.json", f"fill-{index}", [article(1, valid_body(f"fill{index}"))])
    out = tmp_path / "out"
    run(root, out, seed="1", items=42)
    rows = all_rows(out)
    got = {row["source_id"]: row["passage"] for row in rows}
    assert got["low"] == kept["low"]
    assert got["high"] == kept["high"]
    assert "short" not in got and "long" not in got
    for row in rows:
        assert row["item_id"] == "Articolul 1"
        assert row["packet_id"] == f"{row['source_id']}#Articolul 1"
        assert all(row[column] == "" for column in LABEL_COLUMNS)


def test_automotive_names_the_missing_readers(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    with pytest.raises(RuntimeError) as exc:
        run(root, tmp_path / "out", seed="1", items=4, corpus="automotive")
    message = str(exc.value).lower()
    assert "dbc" in message
    assert "pdf" in message
    assert "reqif" in message
    assert "arxml" in message


def test_generator_does_not_call_source_units():
    script = Path(__file__).resolve().parents[2] / "scripts" / "make_gold_packet.py"
    assert "source_units" not in script.read_text(encoding="utf-8")


def item_rank(seed, relative, item_id):
    return hashlib.sha256(f"{seed}{relative}{item_id}".encode("utf-8")).hexdigest()


def test_f5_within_file_items_follow_seed_hash_not_file_order(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    relative = "act-0.json"
    eligible = [f"Articolul {number}" for number in range(1, 9)]
    seed = expected = None
    for candidate in range(1, 40):
        ranked = sorted(eligible, key=lambda item_id: item_rank(str(candidate), relative, item_id))
        if ranked[:4] != eligible[:4]:
            seed, expected = str(candidate), ranked[:4]
            break
    assert expected != eligible[:4]
    write_act(root, relative, "src-0", [article(number, valid_body(f"n{number}")) for number in range(1, 9)])
    for index in range(1, 40):
        write_act(
            root, f"act-{index}.json", f"src-{index}",
            [article(number, valid_body(f"f{index}n{number}")) for number in range(1, 5)],
        )
    out = tmp_path / "out"
    run(root, out, seed=seed, items=160)
    chosen = [row["item_id"] for row in all_rows(out) if row["source_id"] == "src-0"]
    assert chosen == expected
    printed = capsys.readouterr().out
    assert "source_files=40" in printed
    assert "rows=160" in printed
    assert "sealed_rows=40" in printed


def test_f6_fewer_than_40_source_files_exits_nonzero(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    write_act(root, "only.json", "only", [article(number, valid_body(f"a{number}")) for number in range(1, 6)])
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(root, out, seed="1", items=150)
    assert exc.value.code not in (0, None)
    printed = capsys.readouterr().out
    assert "source_files=1" in printed
    assert "rows=4" in printed
    assert "sealed_rows=0" in printed
    assert not (out / "open" / "statements.csv").exists()


def test_f6_fewer_rows_than_items_exits_nonzero(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    for index in range(40):
        write_act(root, f"act-{index}.json", f"src-{index}", [article(1, valid_body(f"f{index}"))])
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(root, out, seed="3", items=150)
    assert exc.value.code not in (0, None)
    printed = capsys.readouterr().out
    assert "source_files=40" in printed
    assert "rows=40" in printed
    assert "sealed_rows=10" in printed
    assert not (out / "open" / "statements.csv").exists()


def test_f6_empty_sealed_sheet_exits_nonzero(tmp_path, capsys):
    root = tmp_path / "corpus"
    root.mkdir()
    for index in range(40):
        write_act(
            root, f"act-{index}.json", f"src-{index}",
            [article(number, valid_body(f"f{index}n{number}")) for number in range(1, 5)],
        )
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        run(root, out, seed="4", items=4)
    assert exc.value.code not in (0, None)
    printed = capsys.readouterr().out
    assert "source_files=40" in printed
    assert "rows=4" in printed
    assert "sealed_rows=0" in printed
    assert not (out / "open" / "statements.csv").exists()
