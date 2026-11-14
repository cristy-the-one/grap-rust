"""ST-01: the statements table (spec-statements.md §3) and its accessors."""

import sqlite3
from pathlib import Path

import pytest

from grap_mcp.kee.graph_store import SCHEMA_VERSION, GraphState, GraphStore
from grap_mcp.schema import load_automotive_schema


def _kinds():
    return load_automotive_schema().statements["kinds"]


def _statement(kind, **overrides):
    from grap_mcp.kee.statements import Statement

    fields = dict(
        source_id="spec",
        item_id="spec#0",
        unit=(0, 40),
        kind=kind,
        subject=(4, 10),
        condition=None,
        required_behaviour=(11, 25),
        values_and_timing=(),
        exception=None,
        origin="rule",
        extractor="ruleset 1",
    )
    fields.update(overrides)
    return Statement(**fields)


def test_statements_cannot_be_updated_in_place_and_are_replaced_per_item(tmp_path):
    """An item's rows are replaced together. One row cannot be edited or deleted.

    ``n`` follows text order of the unit span. A second item is left alone.
    Saving the graph does not write this table. The quoted text is not stored.
    """
    from grap_mcp.kee.statements import StatementTables

    kind = _kinds()[0]
    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as graph:
        tables = StatementTables(graph)
        later = _statement(kind, unit=(30, 48), subject=(30, 36), required_behaviour=(37, 48))
        earlier = _statement(
            kind,
            unit=(0, 20),
            subject=(0, 6),
            condition=(7, 12),
            required_behaviour=None,
            values_and_timing=((13, 16), (17, 20)),
            exception=(0, 4),
            origin="model",
            extractor="model prompt 1",
            statement_id="caller-supplied",
        )
        same_unit = _statement(kind, unit=(0, 20), subject=(2, 8), required_behaviour=(9, 18))
        other_item = _statement(kind, item_id="spec#1", unit=(0, 8), subject=(0, 4))

        ids = tables.replace_item_statements("spec", "spec#0", [later, earlier, same_unit])
        other_ids = tables.replace_item_statements("spec", "spec#1", [other_item])

        assert ids == ["spec|spec#0|0", "spec|spec#0|1", "spec|spec#0|2"]
        assert ids[0] != "caller-supplied"
        assert other_ids == ["spec|spec#1|0"]

        first, second, third = tables.statements_for_item("spec", "spec#0")
        assert [row.statement_id for row in (first, second, third)] == ids
        assert [row.n for row in (first, second, third)] == [0, 1, 2]
        assert first.unit == (0, 20) and first.subject == (0, 6)
        assert first.condition == (7, 12)
        assert first.required_behaviour is None
        assert first.values_and_timing == ((13, 16), (17, 20))
        assert first.exception == (0, 4)
        assert first.origin == "model" and first.extractor == "model prompt 1"
        assert second.unit == (0, 20) and second.subject == (2, 8)
        assert third.unit == (30, 48)
        assert tables.statement(ids[0]) == first
        assert tables.statement("spec|spec#0|9") is None

        columns = {
            row["name"] for row in graph.conn.execute("PRAGMA table_info(statements)")
        }
        assert "text" not in columns

        with pytest.raises(sqlite3.IntegrityError, match="statements cannot be updated in place"):
            graph.conn.execute(
                "UPDATE statements SET extractor = ? WHERE statement_id = ?",
                ("edited", ids[0]),
            )
        graph.conn.rollback()
        assert tables.statement(ids[0]).extractor == "model prompt 1"

        with pytest.raises(
            sqlite3.IntegrityError,
            match="statements are replaced only when their item is re-extracted",
        ):
            graph.conn.execute(
                "DELETE FROM statements WHERE statement_id = ?", (ids[0],)
            )
        graph.conn.rollback()
        assert tables.statement(ids[0]).extractor == "model prompt 1"

        replaced = tables.replace_item_statements("spec", "spec#0", [
            _statement(kind, unit=(4, 12), subject=(4, 10), required_behaviour=(10, 12)),
        ])
        assert replaced == ["spec|spec#0|0"]
        assert tables.statement(ids[0]).unit == (4, 12)
        assert tables.statement(ids[1]) is None
        assert tables.statement(ids[2]) is None
        assert tables.statements_for_item("spec", "spec#1")[0] == tables.statement(other_ids[0])
        assert tables.statement(other_ids[0]).item_id == "spec#1"

        cleared = tables.replace_item_statements("spec", "spec#0", [])
        assert cleared == []
        assert tables.statements_for_item("spec", "spec#0") == []
        assert tables.statement(other_ids[0]).subject == (0, 4)

        graph.save(graph.load())
        assert tables.statement(other_ids[0]).subject == (0, 4)
        assert sorted(graph.load().edges) == []


def test_statement_kind_comes_from_the_schema_file_and_rows_are_checked(tmp_path):
    """Kinds are the schema file's list. Spans, origin and item identity are checked."""
    from grap_mcp.kee.statements import StatementTables

    kinds = _kinds()
    package = Path(__file__).resolve().parents[1] / "grap_mcp" / "kee" / "statements"
    sources = [path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))]
    for kind in kinds:
        for source in sources:
            assert f"'{kind}'" not in source
            assert f'"{kind}"' not in source

    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as graph:
        tables = StatementTables(graph)
        for kind in kinds:
            tables.replace_item_statements("spec", "spec#0", [_statement(kind)])
            assert tables.statements_for_item("spec", "spec#0")[0].kind == kind

        with pytest.raises(ValueError, match="schema file"):
            tables.replace_item_statements("spec", "spec#0", [_statement("not-a-kind")])
        assert tables.statements_for_item("spec", "spec#0")[0].kind == kinds[-1]

        with pytest.raises(ValueError, match="origin"):
            tables.replace_item_statements(
                "spec", "spec#0", [_statement(kinds[0], origin="human")],
            )
        with pytest.raises(ValueError, match="subject"):
            tables.replace_item_statements(
                "spec", "spec#0", [_statement(kinds[0], subject=None)],
            )
        with pytest.raises(ValueError, match="unit"):
            tables.replace_item_statements(
                "spec", "spec#0", [_statement(kinds[0], unit=(5, 5))],
            )
        with pytest.raises(TypeError, match="values_and_timing"):
            tables.replace_item_statements(
                "spec", "spec#0", [_statement(kinds[0], values_and_timing="13,16")],
            )
        with pytest.raises(ValueError, match="re-extracted"):
            tables.replace_item_statements(
                "spec", "spec#0", [_statement(kinds[0], item_id="spec#1")],
            )
        assert tables.statements_for_item("spec", "spec#0")[0].kind == kinds[-1]
        assert tables.statements_for_item("spec", "spec#1") == []


def test_existing_store_migrates_to_the_statements_table(tmp_path):
    """The statements table is the migration after the current one. Edges stay."""
    path = str(tmp_path / "graph_store.db")
    state = GraphState(edges=[(1, 0, "uses"), (1, 0, "cites")])
    with GraphStore(path) as store:
        store.save(state)
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        store.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        store.conn.execute("DROP TABLE statements")
        store.conn.commit()

    with GraphStore(path) as store:
        version = store.conn.execute("PRAGMA user_version").fetchone()[0]
        names = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        loaded = store.load()

        # Repeating the migration after the table exists must not fail.
        store.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        store.conn.commit()
    with GraphStore(path) as store:
        version_again = store.conn.execute("PRAGMA user_version").fetchone()[0]
        names_again = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert version == SCHEMA_VERSION
    assert version_again == SCHEMA_VERSION
    assert "statements" in names
    assert "statements" in names_again
    assert sorted(loaded.edges) == [(1, 0, "cites"), (1, 0, "uses")]


# The statements table as ST-01 created it: subject columns are NOT NULL.
_OLD_STATEMENTS = """
CREATE TABLE statements (
    statement_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    unit_start INTEGER NOT NULL,
    unit_end INTEGER NOT NULL,
    kind TEXT NOT NULL,
    subject_start INTEGER NOT NULL,
    subject_end INTEGER NOT NULL,
    condition_start INTEGER,
    condition_end INTEGER,
    required_behaviour_start INTEGER,
    required_behaviour_end INTEGER,
    values_and_timing TEXT NOT NULL,
    exception_start INTEGER,
    exception_end INTEGER,
    origin TEXT NOT NULL,
    extractor TEXT NOT NULL,
    CHECK (n >= 0),
    CHECK (statement_id = source_id || '|' || item_id || '|' || n),
    CHECK (unit_start >= 0 AND unit_end > unit_start),
    CHECK (subject_start >= 0 AND subject_end > subject_start),
    CHECK (origin IN ('rule', 'model')),
    CHECK (json_valid(values_and_timing) AND json_type(values_and_timing) = 'array'),
    UNIQUE (source_id, item_id, n)
);
"""


def test_other_and_informative_store_an_empty_subject(tmp_path):
    """§5: subject is required for every kind except other and informative.

    Those two kinds have no subject. This card is the first that stores them.
    """
    from grap_mcp.kee.statements import StatementTables

    kinds = _kinds()
    optional = ("informative", "other")
    assert [kind for kind in kinds if kind in optional] == list(optional)

    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as graph:
        tables = StatementTables(graph)
        for kind in optional:
            tables.replace_item_statements("spec", kind, [
                _statement(kind, item_id=kind, subject=None, required_behaviour=None),
            ])
            row = tables.statements_for_item("spec", kind)[0]
            assert row.kind == kind
            assert row.subject is None
            assert row.statement_id == f"spec|{kind}|0"

        for kind in kinds:
            if kind in optional:
                continue
            with pytest.raises(ValueError, match="subject"):
                tables.replace_item_statements("spec", "kept", [
                    _statement(kind, item_id="kept", subject=None),
                ])
        assert tables.statements_for_item("spec", "kept") == []

        with pytest.raises(sqlite3.IntegrityError):
            graph.conn.execute(
                """
                INSERT INTO statements (
                    statement_id, source_id, item_id, n,
                    unit_start, unit_end, kind,
                    subject_start, subject_end,
                    condition_start, condition_end,
                    required_behaviour_start, required_behaviour_end,
                    values_and_timing,
                    exception_start, exception_end,
                    origin, extractor
                ) VALUES (
                    'spec|raw-req|0', 'spec', 'raw-req', 0,
                    0, 8, ?,
                    NULL, NULL,
                    NULL, NULL,
                    NULL, NULL,
                    '[]',
                    NULL, NULL,
                    'rule', 'ruleset 1'
                )
                """,
                (kinds[0],),
            )
        graph.conn.rollback()

        graph.conn.execute(
            """
            INSERT INTO statements (
                statement_id, source_id, item_id, n,
                unit_start, unit_end, kind,
                subject_start, subject_end,
                condition_start, condition_end,
                required_behaviour_start, required_behaviour_end,
                values_and_timing,
                exception_start, exception_end,
                origin, extractor
            ) VALUES (
                'spec|raw-other|0', 'spec', 'raw-other', 0,
                0, 8, 'other',
                NULL, NULL,
                NULL, NULL,
                NULL, NULL,
                '[]',
                NULL, NULL,
                'rule', 'ruleset 1'
            )
            """
        )
        graph.conn.commit()
        assert tables.statement("spec|raw-other|0").subject is None
        assert tables.statement("spec|raw-other|0").kind == "other"


def test_an_old_statements_table_gains_an_empty_subject(tmp_path):
    """A store created before empty subjects still accepts one after it opens."""
    from grap_mcp.kee.graph_store import _MIGRATIONS, _migrate_statements_empty_subject
    from grap_mcp.kee.statements import StatementTables

    path = str(tmp_path / "graph_store.db")
    state = GraphState(edges=[(1, 0, "uses")])
    with GraphStore(path) as store:
        store.save(state)
        store.conn.execute("DROP TABLE statements")
        store.conn.executescript(_OLD_STATEMENTS)
        store.conn.execute(
            """
            INSERT INTO statements (
                statement_id, source_id, item_id, n,
                unit_start, unit_end, kind,
                subject_start, subject_end,
                condition_start, condition_end,
                required_behaviour_start, required_behaviour_end,
                values_and_timing,
                exception_start, exception_end,
                origin, extractor
            ) VALUES (
                'spec|spec#0|0', 'spec', 'spec#0', 0,
                0, 8, 'requirement',
                0, 4,
                NULL, NULL,
                NULL, NULL,
                '[]',
                NULL, NULL,
                'rule', 'ruleset 1'
            )
            """
        )
        store.conn.execute(
            "PRAGMA user_version = "
            + str(_MIGRATIONS.index(_migrate_statements_empty_subject))
        )
        store.conn.commit()

    with GraphStore(path) as store:
        tables = StatementTables(store)
        kept = tables.statement("spec|spec#0|0")
        nullable = {
            row["name"]: row["notnull"]
            for row in store.conn.execute("PRAGMA table_info(statements)")
        }
        tables.replace_item_statements("spec", "spec#1", [
            _statement("informative", item_id="spec#1", subject=None, required_behaviour=None),
        ])
        empty = tables.statements_for_item("spec", "spec#1")[0]
        with pytest.raises(sqlite3.IntegrityError, match="cannot be updated"):
            store.conn.execute(
                "UPDATE statements SET extractor = ? WHERE statement_id = ?",
                ("edited", "spec|spec#0|0"),
            )
        store.conn.rollback()
        loaded = store.load()

    assert kept is not None and kept.subject == (0, 4) and kept.kind == "requirement"
    assert nullable["subject_start"] == 0 and nullable["subject_end"] == 0
    assert empty.kind == "informative" and empty.subject is None
    assert sorted(loaded.edges) == [(1, 0, "uses")]
