"""E-12: rebuild mentions from stored extraction receipts, with no model call.

A mention is one occurrence of a name. The same character span is one
occurrence, so a relation endpoint that repeats an outcome inside the same
quote does not add a row. The mention count equals those occurrences. A second
run inserts nothing.
"""

from grap_mcp.kee.graph_store import GraphState, GraphStore
from grap_mcp.kee.resolution import ResolutionTables

EXTRACTOR = "stub-extractor"

ACT = "The Council issues Order 17. The Council records the minutes."
ACT_FIRST = "The Council issues Order 17."
ACT_SECOND = "The Council records the minutes."
BUS = "Widget sends Gadget."
LAW = "Comisia emite Hotărârea nr. 5. Hotărârea se publică."
LAW_FIRST = "Comisia emite Hotărârea nr. 5."
LAW_SECOND = "Hotărârea se publică."
EMPTY = "Nothing named here."

# One row per distinct occurrence, in chunk-id order then character order.
EXPECTED = (
    ("act", "act#0", "Council", ACT_FIRST),
    ("act", "act#0", "Order 17", ACT_FIRST),
    ("act", "act#0", "Council", ACT_SECOND),
    ("act", "act#0", "minutes", ACT_SECOND),
    ("bus", "bus#0", "Widget", BUS),
    ("bus", "bus#0", "Gadget", BUS),
    ("law", "law#0", "Comisia", LAW_FIRST),
    ("law", "law#0", "Hotărârea", LAW_FIRST),
    ("law", "law#0", "Hotărârea", LAW_SECOND),
)

# Every name string written on the receipts: outcome names, then relation
# endpoints. Repeated spans are in the receipts and are not second occurrences.
NAME_ENTRIES = (
    "Council", "Order 17", "Council",
    "Council", "Order 17",
    "Council", "minutes",
    "Widget", "Gadget",
    "Widget", "Gadget",
    "Comisia", "Hotărârea", "Hotărârea",
)
REPEATED = (
    "Council", "Order 17",
    "Council",
    "Widget", "Gadget",
)

SOURCES = {
    "act#0": ACT,
    "bus#0": BUS,
    "empty#0": EMPTY,
    "law#0": LAW,
}


def _quoted(source: str, quote: str) -> dict:
    start = source.index(quote)
    return {"status": "quoted", "quote": quote, "start": start, "end": start + len(quote)}


def _receipt(source: str, outcomes: list, relations: list) -> dict:
    return {
        "source_text": source,
        "provider": EXTRACTOR,
        "outcomes": outcomes,
        "relations": relations,
    }


def _receipts() -> dict:
    return {
        "act#0": _receipt(ACT, [
            {"name": "Council", "characteristics": [], "evidence": _quoted(ACT, ACT_FIRST)},
            {"name": "Order 17", "characteristics": [], "evidence": _quoted(ACT, ACT_FIRST)},
            {"name": "Council", "characteristics": [], "evidence": _quoted(ACT, ACT_SECOND)},
        ], [
            {"from": "Council", "type": "issues", "to": "Order 17",
             "evidence": _quoted(ACT, ACT_FIRST)},
            {"from": "Council", "type": "records", "to": "minutes",
             "evidence": _quoted(ACT, ACT_SECOND)},
        ]),
        "bus#0": _receipt(BUS, [
            {"name": "Widget", "characteristics": [], "evidence": _quoted(BUS, BUS)},
            {"name": "Gadget", "characteristics": [], "evidence": {"status": "missing"}},
        ], [
            {"from": "Widget", "type": "sends", "to": "Gadget",
             "evidence": _quoted(BUS, BUS)},
        ]),
        "empty#0": _receipt(EMPTY, [], []),
        "law#0": _receipt(LAW, [
            {"name": "Comisia", "characteristics": [], "evidence": _quoted(LAW, LAW_FIRST)},
            {"name": "Hotărârea", "characteristics": [], "evidence": _quoted(LAW, LAW_FIRST)},
            {"name": "Hotărârea", "characteristics": [], "evidence": _quoted(LAW, LAW_SECOND)},
        ], []),
    }


def _line(*, mentions, names, entries, repeated, unlocatable, receipts, documents, added) -> str:
    return (
        f"mentions={mentions} n_names={names} n_name_entries={entries} "
        f"n_repeated={repeated} n_unlocatable={unlocatable} "
        f"n_receipts={receipts} n_documents={documents} added={added}"
    )


def _stored(store) -> list:
    tables = ResolutionTables(store)
    rows = []
    for item_id in ("act#0", "bus#0", "empty#0", "law#0"):
        source_id = item_id.split("#", 1)[0]
        rows.extend(tables.mentions_for_item(source_id, item_id))
    return rows


def test_backfill_counts_equal_receipt_names_and_second_run_adds_nothing(tmp_path, capsys):
    """Mention rows equal the names in the receipts. Running again inserts nothing."""
    from grap_mcp.kee.resolution.backfill import backfill_mentions

    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        store.save(GraphState(extractions=_receipts()))
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert store.conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 0

        first = backfill_mentions(store)
        out = capsys.readouterr().out.strip()
        rows = _stored(store)
        assert out == _line(
            mentions=len(EXPECTED), names=len(EXPECTED), entries=len(NAME_ENTRIES),
            repeated=len(REPEATED), unlocatable=0, receipts=len(SOURCES),
            documents=len(SOURCES), added=len(EXPECTED),
        )
        assert first["mentions"] == len(EXPECTED)
        assert first["names"] == len(EXPECTED)
        assert first["name_entries"] == len(NAME_ENTRIES)
        assert first["repeated"] == len(REPEATED)
        assert first["unlocatable"] == 0
        assert first["receipts"] == len(SOURCES)
        assert first["documents"] == len(SOURCES)
        assert first["added"] == len(EXPECTED)
        assert first["names"] + first["repeated"] + first["unlocatable"] == first["name_entries"]
        assert [(m.source_id, m.item_id, m.text, m.sentence) for m in rows] == list(EXPECTED)
        for mention in rows:
            source = SOURCES[mention.item_id]
            assert source[mention.span_start:mention.span_end] == mention.text
            assert mention.text in mention.sentence
            assert mention.extractor == EXTRACTOR
            assert mention.type_hint is None
        assert len({(m.item_id, m.span_start, m.span_end) for m in rows}) == len(rows)
        ids = [m.mention_id for m in rows]
        assert store.conn.execute("SELECT COUNT(*) FROM link_decisions").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM constraints").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM groupings").fetchone()[0] == 0

        store.save(store.load())
        second = backfill_mentions(store)
        out2 = capsys.readouterr().out.strip()
        again = _stored(store)
        assert out2 == _line(
            mentions=len(EXPECTED), names=len(EXPECTED), entries=len(NAME_ENTRIES),
            repeated=len(REPEATED), unlocatable=0, receipts=len(SOURCES),
            documents=len(SOURCES), added=0,
        )
        assert second["added"] == 0
        assert second["mentions"] == len(EXPECTED)
        assert [m.mention_id for m in again] == ids
        assert [(m.text, m.span_start, m.span_end, m.sentence) for m in again] == [
            (m.text, m.span_start, m.span_end, m.sentence) for m in rows
        ]

    with GraphStore(path) as reopened:
        third = backfill_mentions(reopened)
        capsys.readouterr()
        assert third["added"] == 0
        assert third["mentions"] == len(EXPECTED)
        assert [m.mention_id for m in _stored(reopened)] == ids
        assert reopened.conn.execute("PRAGMA user_version").fetchone()[0] == 6


def test_backfill_does_not_invent_a_span_for_a_name_absent_from_its_quote(tmp_path, capsys):
    """A name that is not in its quote is counted as unlocatable and stored nowhere."""
    from grap_mcp.kee.resolution.backfill import backfill_mentions

    source = "The Council issues Order 17. The Council records the minutes."
    first = "The Council issues Order 17."
    # The second Council's quote does not contain the name, so it must not
    # reuse the first Council's span.
    receipts = {
        "act#0": _receipt(source, [
            {"name": "Council", "characteristics": [], "evidence": _quoted(source, first)},
            {"name": "Council", "characteristics": [],
             "evidence": _quoted(source, "records the minutes.")},
            {"name": "Ministry", "characteristics": [], "evidence": {"status": "missing"}},
        ], []),
    }
    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        store.save(GraphState(extractions=receipts))
        report = backfill_mentions(store)
        printed = capsys.readouterr().out.strip()
        rows = ResolutionTables(store).mentions_for_item("act", "act#0")
        again = backfill_mentions(store)
        capsys.readouterr()
    assert report["mentions"] == 1
    assert report["names"] == 1
    assert report["name_entries"] == 3
    assert report["unlocatable"] == 2
    assert report["repeated"] == 0
    assert report["added"] == 1
    assert "n_unlocatable=2" in printed
    assert [row.text for row in rows] == ["Council"]
    assert source[rows[0].span_start:rows[0].span_end] == "Council"
    assert rows[0].span_start == source.index("Council")
    assert again["added"] == 0
    assert again["mentions"] == 1


def test_backfill_cli_refuses_a_missing_store(tmp_path, capsys):
    """A missing path is not created and is not reported as an empty store."""
    from grap_mcp.kee.resolution.backfill import main

    missing = tmp_path / "absent.db"
    assert main([str(missing)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no store" in captured.err
    assert not missing.exists()


def test_backfill_with_no_receipts_prints_na_and_a_second_run_adds_nothing(tmp_path, capsys):
    """No receipts is nothing to measure: the count is n/a, and the table stays empty."""
    from grap_mcp.kee.resolution.backfill import backfill_mentions

    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        first = backfill_mentions(store)
        out = capsys.readouterr().out.strip()
        assert out == _line(
            mentions="n/a", names="n/a", entries="n/a", repeated="n/a",
            unlocatable="n/a", receipts=0, documents=0, added="n/a",
        )
        assert first["mentions"] is None
        assert first["names"] is None
        assert first["name_entries"] is None
        assert first["repeated"] is None
        assert first["unlocatable"] is None
        assert first["added"] is None
        assert first["receipts"] == 0
        assert first["documents"] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 0

        second = backfill_mentions(store)
        out2 = capsys.readouterr().out.strip()
        assert out2 == out
        assert second["added"] is None
        assert store.conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 0
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 6
