"""DDL for the four entity-resolution tables (spec §3).

The statements are idempotent. A migration that creates them and then stops
before ``user_version`` is bumped can be run again.

Mutation rules, enforced by triggers:

- ``mentions`` rejects UPDATE. DELETE is allowed only while the connection
  function ``grap_mention_replace_ok`` returns 1, which is the item
  re-extraction path.
- ``link_decisions`` rejects UPDATE and DELETE. The row appended later wins.
- ``constraints`` rejects UPDATE. DELETE is how a standing rule is removed.
- ``groupings`` rejects UPDATE and DELETE. A later run is a new ``run_id``.
"""

from __future__ import annotations


RESOLUTION_DDL = """
CREATE TABLE IF NOT EXISTS mentions (
    mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    span_start INTEGER NOT NULL,
    span_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    sentence TEXT NOT NULL,
    type_hint TEXT,
    extractor TEXT NOT NULL,
    UNIQUE (source_id, item_id, span_start, span_end)
);

CREATE TRIGGER IF NOT EXISTS mentions_no_update
BEFORE UPDATE ON mentions
BEGIN
    SELECT RAISE(ABORT, 'mentions cannot be updated in place');
END;

CREATE TRIGGER IF NOT EXISTS mentions_no_stray_delete
BEFORE DELETE ON mentions
WHEN grap_mention_replace_ok() = 0
BEGIN
    SELECT RAISE(ABORT, 'mentions are replaced only when their item is re-extracted');
END;

CREATE TABLE IF NOT EXISTS link_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mention_id INTEGER NOT NULL,
    entity_key TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('same', 'not_same')),
    method TEXT NOT NULL,
    score REAL,
    decider TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    proposal INTEGER NOT NULL DEFAULT 0 CHECK (proposal IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_link_decisions_pair
    ON link_decisions (mention_id, entity_key, decision_id);

CREATE TRIGGER IF NOT EXISTS link_decisions_no_update
BEFORE UPDATE ON link_decisions
BEGIN
    SELECT RAISE(ABORT, 'link_decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS link_decisions_no_delete
BEFORE DELETE ON link_decisions
BEGIN
    SELECT RAISE(ABORT, 'link_decisions are append-only');
END;

CREATE TABLE IF NOT EXISTS constraints (
    constraint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('must_link', 'cannot_link')),
    origin TEXT NOT NULL,
    reason TEXT NOT NULL,
    CHECK (entity_a < entity_b),
    UNIQUE (entity_a, entity_b, kind)
);

CREATE TRIGGER IF NOT EXISTS constraints_no_update
BEFORE UPDATE ON constraints
BEGIN
    SELECT RAISE(ABORT, 'constraints are added or removed, not edited');
END;

CREATE TABLE IF NOT EXISTS groupings (
    run_id TEXT NOT NULL,
    mention_id INTEGER NOT NULL,
    entity_key TEXT NOT NULL,
    PRIMARY KEY (run_id, mention_id)
);

CREATE TRIGGER IF NOT EXISTS groupings_no_update
BEFORE UPDATE ON groupings
BEGIN
    SELECT RAISE(ABORT, 'groupings are kept per run; a recompute appends a new run');
END;

CREATE TRIGGER IF NOT EXISTS groupings_no_delete
BEFORE DELETE ON groupings
BEGIN
    SELECT RAISE(ABORT, 'groupings are kept per run; a recompute appends a new run');
END;
"""


def apply_resolution_schema(conn) -> None:
    """Create the four tables and their triggers if they are not there yet."""
    conn.executescript(RESOLUTION_DDL)
