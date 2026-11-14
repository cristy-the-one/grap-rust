"""DDL for the statements table (spec-statements.md §3).

The statements are idempotent. A migration that creates the table and then
stops before ``user_version`` is bumped can be run again.

A row is never updated. DELETE is allowed only while the connection function
``grap_statement_replace_ok`` returns 1, which is the item re-extraction path.
The text of a span is not stored; a span is two character offsets into the
item, and the text is read back from that item.

``kind`` is not listed here. The accessor reads the allowed kinds from the
automotive schema file.
"""

from __future__ import annotations

import json
from pathlib import Path

_MARKING_PATH = Path(__file__).with_name("marking.json")

STATEMENTS_DDL = """
CREATE TABLE IF NOT EXISTS statements (
    statement_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    unit_start INTEGER NOT NULL,
    unit_end INTEGER NOT NULL,
    kind TEXT NOT NULL,
    subject_start INTEGER,
    subject_end INTEGER,
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
    CHECK (
        (subject_start IS NULL) = (subject_end IS NULL)
        AND (
            subject_start IS NULL
            OR (subject_start >= 0 AND subject_end > subject_start)
        )
        AND __SUBJECT_OPTIONAL__
    ),
    CHECK (
        (condition_start IS NULL) = (condition_end IS NULL)
        AND (
            condition_start IS NULL
            OR (condition_start >= 0 AND condition_end > condition_start)
        )
    ),
    CHECK (
        (required_behaviour_start IS NULL) = (required_behaviour_end IS NULL)
        AND (
            required_behaviour_start IS NULL
            OR (
                required_behaviour_start >= 0
                AND required_behaviour_end > required_behaviour_start
            )
        )
    ),
    CHECK (
        (exception_start IS NULL) = (exception_end IS NULL)
        AND (
            exception_start IS NULL
            OR (exception_start >= 0 AND exception_end > exception_start)
        )
    ),
    CHECK (origin IN ('rule', 'model')),
    CHECK (json_valid(values_and_timing) AND json_type(values_and_timing) = 'array'),
    UNIQUE (source_id, item_id, n)
);

CREATE TRIGGER IF NOT EXISTS statements_no_update
BEFORE UPDATE ON statements
BEGIN
    SELECT RAISE(ABORT, 'statements cannot be updated in place');
END;

CREATE TRIGGER IF NOT EXISTS statements_no_stray_delete
BEFORE DELETE ON statements
WHEN grap_statement_replace_ok() = 0
BEGIN
    SELECT RAISE(ABORT, 'statements are replaced only when their item is re-extracted');
END;
"""


def marking_rules() -> dict:
    """Field rules for stage 2. Kind names stay in the data file."""
    raw = json.loads(_MARKING_PATH.read_text(encoding="utf-8"))
    known = set(statement_kinds())
    optional = _kind_list(raw, "subject_optional", known)
    required = _kind_list(raw, "required_behaviour_required", known)
    fallback = raw.get("no_statement_kind")
    if not isinstance(fallback, str) or fallback not in optional:
        raise ValueError("the no-statement kind must be one that may omit a subject")
    return {
        "subject_optional": optional,
        "required_behaviour_required": required,
        "no_statement_kind": fallback,
    }


def _kind_list(raw: dict, key: str, known: set[str]) -> tuple[str, ...]:
    value = raw.get(key)
    if (
        not isinstance(value, list)
        or not value
        or len(value) != len(set(value))
        or any(not isinstance(item, str) or item not in known for item in value)
    ):
        raise ValueError(f"{key} must list kinds from the schema file")
    return tuple(value)


def _subject_clause(kinds: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + kind.replace("'", "''") + "'" for kind in kinds)
    return "(subject_start IS NOT NULL OR kind IN (" + quoted + "))"


def apply_statements_schema(conn) -> None:
    """Create the statements table, or rebuild it when subject cannot be empty.

    The rebuild is idempotent. A migration that finished the table and stopped
    before ``user_version`` was bumped can be run again.
    """
    clause = _subject_clause(marking_rules()["subject_optional"])
    ddl = STATEMENTS_DDL.replace("__SUBJECT_OPTIONAL__", clause)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'statements'"
    ).fetchone()
    if row is None:
        conn.executescript(ddl)
        return
    info = {item["name"]: item for item in conn.execute("PRAGMA table_info(statements)")}
    current = (
        clause in (row["sql"] or "")
        and info.get("subject_start") is not None
        and info["subject_start"]["notnull"] == 0
        and info["subject_end"]["notnull"] == 0
    )
    if current:
        return
    _rebuild_statements(conn, ddl)


def _rebuild_statements(conn, ddl: str) -> None:
    marker = "CREATE TRIGGER"
    at = ddl.find(marker)
    if at < 0:
        raise ValueError("statements triggers are missing")
    table_sql, triggers = ddl[:at], ddl[at:]
    create = table_sql.replace(
        "CREATE TABLE IF NOT EXISTS statements (",
        "CREATE TABLE statements_rebuilt (",
        1,
    )
    columns = (
        "statement_id, source_id, item_id, n, "
        "unit_start, unit_end, kind, "
        "subject_start, subject_end, "
        "condition_start, condition_end, "
        "required_behaviour_start, required_behaviour_end, "
        "values_and_timing, "
        "exception_start, exception_end, "
        "origin, extractor"
    )
    script = (
        "BEGIN;\n"
        "DROP TABLE IF EXISTS statements_rebuilt;\n"
        + create
        + f"\nINSERT INTO statements_rebuilt ({columns})\n"
        + f"SELECT {columns} FROM statements;\n"
        "DROP TABLE statements;\n"
        "ALTER TABLE statements_rebuilt RENAME TO statements;\n"
        "COMMIT;\n"
    )
    try:
        conn.executescript(script)
        conn.executescript(triggers)
    except Exception:
        conn.rollback()
        raise


def statement_kinds() -> tuple[str, ...]:
    """The statement kinds in the automotive schema file, in file order."""
    from grap_mcp.schema import load_automotive_schema

    kinds = load_automotive_schema().statements["kinds"]
    if (
        not isinstance(kinds, tuple)
        or not kinds
        or not all(isinstance(kind, str) and kind.strip() for kind in kinds)
    ):
        raise ValueError("statement kinds are missing from the schema file")
    return kinds
