"""E-11: the four entity-resolution tables (spec §3) and their accessors."""

import sqlite3

import pytest

from grap_mcp.kee.graph_store import GraphState, GraphStore

WIDGET = "Signal | demo | Widget"
GADGET = "Signal | demo | Gadget"


def test_later_decision_wins_and_mentions_cannot_be_updated_in_place(tmp_path):
    """A decision appended later wins, and a mention row cannot be edited.

    The second decision carries an earlier recorded time and a non-human
    decider, so only append order selects it. Re-extraction may replace an
    item's mention rows; an UPDATE of one row may not.
    """
    from grap_mcp.kee.resolution import LinkDecision, Mention, ResolutionTables

    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as graph:
        tables = ResolutionTables(graph)
        mention_id = tables.add_mentions([Mention(
            source_id="demo",
            item_id="demo#0",
            span_start=4,
            span_end=10,
            text="Widget",
            sentence="The Widget sends.",
            type_hint="signal",
            extractor="fixture",
        )])[0]
        tables.append_decision(
            mention_id, WIDGET, "not_same", method="review", score=None,
            decider="human", decided_at="2026-11-13T12:00:00Z",
        )
        later = tables.append_decision(
            mention_id, WIDGET, "same", method="identifier", score=None,
            decider="rule", decided_at="2026-11-13T09:00:00Z",
        )
        winner = tables.decision_for(mention_id, WIDGET)
        history = tables.decisions_for(mention_id, WIDGET)
        assert isinstance(winner, LinkDecision)
        assert winner.decision_id == later.decision_id
        assert winner.verdict == "same"
        assert winner.decider == "rule"
        assert [row.verdict for row in history] == ["not_same", "same"]

        with pytest.raises(sqlite3.IntegrityError, match="mentions cannot be updated in place"):
            graph.conn.execute(
                "UPDATE mentions SET text = ? WHERE mention_id = ?",
                ("Changed", mention_id),
            )
        graph.conn.rollback()
        assert tables.mention(mention_id).text == "Widget"

        with pytest.raises(sqlite3.IntegrityError, match="mentions are replaced only when their item is re-extracted"):
            graph.conn.execute("DELETE FROM mentions WHERE mention_id = ?", (mention_id,))
        graph.conn.rollback()
        assert tables.mention(mention_id).text == "Widget"

        with pytest.raises(sqlite3.IntegrityError, match="link_decisions are append-only"):
            graph.conn.execute(
                "DELETE FROM link_decisions WHERE decision_id = ?",
                (later.decision_id,),
            )
        graph.conn.rollback()
        assert tables.decision_for(mention_id, WIDGET).verdict == "same"
        assert len(tables.decisions_for(mention_id, WIDGET)) == 2

        replaced = tables.replace_item_mentions("demo", "demo#0", [Mention(
            source_id="demo",
            item_id="demo#0",
            span_start=4,
            span_end=12,
            text="Widgetry",
            sentence="The Widgetry sends.",
            type_hint="signal",
            extractor="fixture",
        )])
        assert tables.mention(mention_id) is None
        assert len(replaced) == 1 and replaced[0] != mention_id
        assert tables.mention(replaced[0]).text == "Widgetry"
        with pytest.raises(sqlite3.IntegrityError, match="mentions cannot be updated in place"):
            graph.conn.execute(
                "UPDATE mentions SET text = ? WHERE mention_id = ?",
                ("Changed", replaced[0]),
            )
        graph.conn.rollback()
        assert tables.mention(replaced[0]).text == "Widgetry"
        # Decisions stay appended; saving the graph does not touch these tables.
        assert tables.decision_for(mention_id, WIDGET).verdict == "same"
        graph.save(graph.load())
        assert tables.decision_for(mention_id, WIDGET).verdict == "same"
        assert sorted(graph.load().edges) == []


def test_constraints_can_be_removed_and_earlier_grouping_runs_are_kept(tmp_path):
    """Constraints are removable. A later grouping run does not rewrite the earlier one."""
    from grap_mcp.kee.resolution import Mention, ResolutionTables

    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as graph:
        tables = ResolutionTables(graph)
        mention_id = tables.add_mentions([Mention(
            source_id="demo",
            item_id="demo#0",
            span_start=0,
            span_end=6,
            text="Widget",
            sentence="Widget",
            extractor="fixture",
        )])[0]
        constraint = tables.add_constraint(GADGET, WIDGET, "cannot_link", origin="review", reason="distinct signals")
        assert tables.constraints() == [constraint]
        with pytest.raises(sqlite3.IntegrityError, match="constraints are added or removed, not edited"):
            graph.conn.execute(
                "UPDATE constraints SET reason = ? WHERE constraint_id = ?",
                ("edited", constraint.constraint_id),
            )
        graph.conn.rollback()
        assert tables.constraints() == [constraint]
        tables.remove_constraint(constraint.constraint_id)
        assert tables.constraints() == []

        tables.record_grouping("run-1", [(mention_id, WIDGET)])
        tables.record_grouping("run-2", [(mention_id, GADGET)])
        assert tables.grouping("run-1") == {mention_id: WIDGET}
        assert tables.latest_grouping() == ("run-2", {mention_id: GADGET})
        with pytest.raises(ValueError, match="already stored"):
            tables.record_grouping("run-1", [(mention_id, GADGET)])
        with pytest.raises(sqlite3.IntegrityError, match="groupings are kept per run"):
            graph.conn.execute(
                "UPDATE groupings SET entity_key = ? WHERE run_id = ?",
                (GADGET, "run-1"),
            )
        graph.conn.rollback()
        assert tables.grouping("run-1") == {mention_id: WIDGET}


def test_e10_store_migrates_to_resolution_tables_and_keeps_both_edges(tmp_path):
    """Resolution tables are the migration after E-10. Both relations on a pair remain."""
    path = str(tmp_path / "graph_store.db")
    state = GraphState(edges=[(1, 0, "uses"), (1, 0, "cites")])
    with GraphStore(path) as store:
        store.save(state)
        store.conn.execute("PRAGMA user_version = 5")
        for table in ("mentions", "link_decisions", "constraints", "groupings"):
            store.conn.execute(f"DROP TABLE IF EXISTS {table}")
        store.conn.commit()

    with GraphStore(path) as store:
        version = store.conn.execute("PRAGMA user_version").fetchone()[0]
        names = {
            row[0]
            for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        loaded = store.load()
    assert version == 6
    assert {"mentions", "link_decisions", "constraints", "groupings"} <= names
    assert sorted(loaded.edges) == [(1, 0, "cites"), (1, 0, "uses")]
