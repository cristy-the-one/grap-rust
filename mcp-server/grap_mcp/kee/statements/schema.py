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

STATEMENTS_DDL = """
CREATE TABLE IF NOT EXISTS statements (
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


def apply_statements_schema(conn) -> None:
    """Create the statements table and its triggers if they are not there yet."""
    conn.executescript(STATEMENTS_DDL)


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
