"""Phase 2: the durable source of truth for the extraction graph.

A versioned sqlite sidecar (the EntityIndex pattern) holding WorkingGraph's
state: concepts, aliases, characteristics, edges, per-chunk provenance, the
chunk content-hash manifest, relation vocabulary, and parked relations.
Loads a snapshot and writes only changed rows in one transaction; no graph logic.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


def _migrate_edges_include_relation(c) -> None:
    """Rebuild `edges` so the relation type is part of the primary key.

    The previous key was (from_id, to_id), so a second relation between the
    same nodes replaced the first. One pair has to be able to carry several
    (`about` and `refers_to`; `sends` and `receives`). A store already on the
    new key is left untouched, so a run that committed the rebuild but not
    user_version can be repeated, and so can a fresh database whose first
    migration already created the new key.
    """
    info = list(c.execute("PRAGMA table_info(edges)"))
    pk = [row["name"] for row in sorted(info, key=lambda row: row["pk"]) if row["pk"]]
    if pk == ["from_id", "to_id", "type"]:
        return
    # One transaction: a failure rolls the old table back into place.
    c.executescript(
        """
        BEGIN;
        DROP TABLE IF EXISTS edges_with_relation;
        CREATE TABLE edges_with_relation (
            from_id INTEGER,
            to_id INTEGER,
            type TEXT,
            PRIMARY KEY(from_id, to_id, type)
        );
        INSERT INTO edges_with_relation (from_id, to_id, type)
            SELECT from_id, to_id, type FROM edges;
        DROP TABLE edges;
        ALTER TABLE edges_with_relation RENAME TO edges;
        COMMIT;
        """
    )


def _migrate_edge_sources_include_relation(c) -> None:
    """Rebuild `edge_sources` so the relation type is part of the primary key.

    The previous key was (from_id, to_id, chunk_id). Two relations between the
    same nodes then shared one source list. A store already on the new key is
    left untouched. An older row records no relation, so it is copied onto each
    edge that still exists for that pair.
    """
    info = list(c.execute("PRAGMA table_info(edge_sources)"))
    pk = [row["name"] for row in sorted(info, key=lambda row: row["pk"]) if row["pk"]]
    if pk == ["from_id", "to_id", "type", "chunk_id"]:
        return
    c.executescript(
        """
        BEGIN;
        DROP TABLE IF EXISTS edge_sources_with_relation;
        CREATE TABLE edge_sources_with_relation (
            from_id INTEGER,
            to_id INTEGER,
            type TEXT,
            chunk_id TEXT,
            PRIMARY KEY(from_id, to_id, type, chunk_id)
        );
        INSERT INTO edge_sources_with_relation (from_id, to_id, type, chunk_id)
            SELECT s.from_id, s.to_id, e.type, s.chunk_id
            FROM edge_sources s
            JOIN edges e ON e.from_id = s.from_id AND e.to_id = s.to_id;
        DROP TABLE edge_sources;
        ALTER TABLE edge_sources_with_relation RENAME TO edge_sources;
        COMMIT;
        """
    )


def _migrate_resolution_tables(c) -> None:
    """Add the four entity-resolution tables (spec §3).

    Chained after the edges-key migration. The statements are idempotent, so
    a run that created the tables and stopped before user_version was bumped
    can be repeated. Graph save and load do not read or write these tables.
    """
    from .resolution.schema import apply_resolution_schema

    apply_resolution_schema(c)


def _migrate_statements_empty_subject(c) -> None:
    """Allow an empty subject for the kinds that have none.

    A store created before that change has NOT NULL subject columns. Rebuilding
    is idempotent, so a run that finished the table and stopped before
    ``user_version`` was bumped can be repeated.
    """
    from .statements.schema import apply_statements_schema

    apply_statements_schema(c)


def _migrate_statements_table(c) -> None:
    """Add the statements table (spec-statements.md §3).

    Chained after the resolution tables. The DDL is idempotent, so a run that
    created the table and stopped before user_version was bumped can be
    repeated. Rows are not updated in place. Graph save and load do not read
    or write this table.
    """
    from .statements.schema import apply_statements_schema

    apply_statements_schema(c)


_MIGRATIONS = [
    lambda c: c.executescript(
        """
        CREATE TABLE IF NOT EXISTS concepts (concept_id INTEGER PRIMARY KEY, canonical TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aliases (concept_id INTEGER, alias TEXT, PRIMARY KEY(concept_id, alias));
        CREATE TABLE IF NOT EXISTS characteristics (concept_id INTEGER, text TEXT, PRIMARY KEY(concept_id, text));
        CREATE TABLE IF NOT EXISTS edges (from_id INTEGER, to_id INTEGER, type TEXT, PRIMARY KEY(from_id, to_id, type));
        CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS concept_hashes (concept_id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS concept_sources (concept_id INTEGER, chunk_id TEXT, PRIMARY KEY(concept_id, chunk_id));
        CREATE TABLE IF NOT EXISTS char_sources (concept_id INTEGER, text TEXT, chunk_id TEXT, PRIMARY KEY(concept_id, text, chunk_id));
        CREATE TABLE IF NOT EXISTS edge_sources (from_id INTEGER, to_id INTEGER, type TEXT, chunk_id TEXT, PRIMARY KEY(from_id, to_id, type, chunk_id));
        CREATE TABLE IF NOT EXISTS parked (from_concept TEXT, type TEXT, to_concept TEXT, chunk_id TEXT, reason TEXT, PRIMARY KEY(from_concept, type, to_concept, chunk_id));
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """
    ),
    # A chunk that fails extraction deliberately does NOT get its hash recorded,
    # so the next run retries it. That is right for transient failures and wrong
    # forever after for deterministic ones: `20300#2` failed in every run for
    # weeks, one wasted generation per batch, and it held parse_failure_rate at a
    # constant 9.1% that had nothing to do with the documents being ingested.
    # This table is the dead-letter queue that bounds the retry.
    lambda c: c.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunk_failures (
            chunk_id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            reason TEXT,
            quarantined INTEGER NOT NULL DEFAULT 0,
            last_ts REAL
        );
        """
    ),
    lambda c: c.execute(
        "CREATE TABLE IF NOT EXISTS embeddings (text TEXT PRIMARY KEY, vector TEXT NOT NULL)"
    ),
    lambda c: c.execute(
        "CREATE TABLE IF NOT EXISTS extractions (chunk_id TEXT PRIMARY KEY, record TEXT NOT NULL)"
    ),
    _migrate_edges_include_relation,
    # Provenance was keyed by the node pair, so two relations between the same
    # nodes shared one source list and the second write replaced the first.
    # The relation is now part of the key. A store already on that key is left
    # untouched. An older row has no relation of its own: it is copied onto
    # each edge that still exists for that pair, which is one edge for every
    # store written before a pair could hold two relations.
    _migrate_edge_sources_include_relation,
    _migrate_resolution_tables,
    _migrate_statements_table,
    _migrate_statements_empty_subject,
]

SCHEMA_VERSION = len(_MIGRATIONS)


@dataclass
class GraphState:
    concepts: Dict[int, str] = field(default_factory=dict)
    aliases: Dict[int, List[str]] = field(default_factory=dict)
    chars: Dict[int, List[str]] = field(default_factory=dict)
    edges: List[Tuple[int, int, str]] = field(default_factory=list)
    concept_sources: Dict[int, List[str]] = field(default_factory=dict)
    char_sources: Dict[Tuple[int, str], List[str]] = field(default_factory=dict)
    edge_sources: Dict[Tuple[int, int, str], List[str]] = field(default_factory=dict)
    manifest: Dict[str, str] = field(default_factory=dict)
    rel_vocab: List[str] = field(default_factory=list)
    parked: List[dict] = field(default_factory=list)
    concept_hashes: Dict[int, str] = field(default_factory=dict)  # node -> hex core content_hash
    # chunk_id -> {content_hash, attempts, reason, quarantined, last_ts}
    chunk_failures: Dict[str, dict] = field(default_factory=dict)

    # Stable extraction ids are independent of disposable Rust node ids.
    next_concept_id: int = 0
    embeddings: Dict[str, List[float]] = field(default_factory=dict)
    embedding_model: str = ""
    chunk_strategy: str = "legacy"
    extractions: Dict[str, dict] = field(default_factory=dict)


class GraphStore:
    """Open (and migrate) the durable graph store at `db_path`. Context manager."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()
        # Default for the mentions DELETE trigger. ResolutionTables replaces
        # this while it is replacing one item's rows. Absent here, a delete
        # would fail as a missing function instead of the table's own refusal.
        self.conn.create_function("grap_mention_replace_ok", 0, lambda: 0)
        # Same gate for the statements table. StatementTables replaces this
        # while it is replacing one item's rows.
        self.conn.create_function("grap_statement_replace_ok", 0, lambda: 0)

    def _migrate(self) -> None:
        v = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i in range(v, len(_MIGRATIONS)):
            _MIGRATIONS[i](self.conn)
            self.conn.execute(f"PRAGMA user_version = {i + 1}")
        self.conn.commit()

    def save(self, state: GraphState) -> int:
        """Synchronize changed rows atomically and return SQLite's write count.

        Snapshot comparison still scans O(N) rows. Unchanged checkpoints do no
        writes; changed checkpoints write only additions, updates and removals.
        The primary-key lengths below describe the existing schema, not another
        graph model. BEGIN IMMEDIATE keeps the comparison and writes together.
        """
        tables = {
            "concepts": (1, list(state.concepts.items())),
            "aliases": (2, [(cid, a) for cid, al in state.aliases.items() for a in al]),
            "characteristics": (2, [(cid, t) for cid, ts in state.chars.items() for t in ts]),
            "edges": (3, [tuple(e) for e in state.edges]),
            "chunks": (1, list(state.manifest.items())),
            "concept_hashes": (1, list(state.concept_hashes.items())),
            "concept_sources": (2, [(cid, ch) for cid, chs in state.concept_sources.items() for ch in chs]),
            "char_sources": (3, [(cid, txt, ch) for (cid, txt), chs in state.char_sources.items() for ch in chs]),
            "edge_sources": (4, [(f, t, ty, ch) for (f, t, ty), chs in state.edge_sources.items() for ch in chs]),
            "parked": (4, [(p["from"], p["type"], p["to"], p["chunk_id"], p["reason"]) for p in state.parked]),
            "chunk_failures": (1, [
                (cid, f.get("content_hash", ""), int(f.get("attempts", 0)),
                 f.get("reason"), int(bool(f.get("quarantined"))), f.get("last_ts"))
                for cid, f in state.chunk_failures.items()]),
            "embeddings": (1, [(text, json.dumps(vec)) for text, vec in state.embeddings.items()]),
            "extractions": (1, [(source, json.dumps(record, ensure_ascii=False, sort_keys=True))
                                 for source, record in state.extractions.items()]),
            "meta": (1, [("rel_vocab", json.dumps(state.rel_vocab)),
                         ("next_concept_id", str(state.next_concept_id)),
                         ("embedding_model", state.embedding_model),
                         ("chunk_strategy", state.chunk_strategy)]),
        }
        c = self.conn
        start = c.total_changes
        try:
            c.execute("BEGIN IMMEDIATE")
            for table, (key_len, rows) in tables.items():
                cursor = c.execute(f"SELECT * FROM {table}")
                columns = [col[0] for col in cursor.description]
                old = {tuple(r)[:key_len]: tuple(r) for r in cursor}
                desired = {}
                for row in rows:
                    desired.setdefault(row[:key_len], row)
                removed = old.keys() - desired.keys()
                if removed:
                    where = " AND ".join(f"{col}=?" for col in columns[:key_len])
                    c.executemany(f"DELETE FROM {table} WHERE {where}", sorted(removed))
                changed = [row for key, row in desired.items() if old.get(key) != row]
                if changed:
                    keys = ",".join(columns[:key_len])
                    values = ",".join("?" for _ in columns)
                    update = ",".join(f"{col}=excluded.{col}" for col in columns[key_len:])
                    action = f"UPDATE SET {update}" if update else "NOTHING"
                    c.executemany(f"INSERT INTO {table} VALUES ({values}) "
                                  f"ON CONFLICT ({keys}) DO {action}", changed)
            c.commit()
        except Exception:
            c.rollback()
            raise
        return c.total_changes - start

    def load(self) -> GraphState:
        c = self.conn
        s = GraphState()
        s.concepts = {r["concept_id"]: r["canonical"]
                      for r in c.execute("SELECT * FROM concepts")}
        for r in c.execute("SELECT * FROM aliases"):
            s.aliases.setdefault(r["concept_id"], []).append(r["alias"])
        for r in c.execute("SELECT * FROM characteristics"):
            s.chars.setdefault(r["concept_id"], []).append(r["text"])
        for cid in s.concepts:
            s.aliases.setdefault(cid, [])
            s.chars.setdefault(cid, [])
        s.edges = [(r["from_id"], r["to_id"], r["type"]) for r in c.execute("SELECT * FROM edges")]
        for r in c.execute("SELECT * FROM concept_sources"):
            s.concept_sources.setdefault(r["concept_id"], []).append(r["chunk_id"])
        for cid in s.concepts:
            s.concept_sources.setdefault(cid, [])
        for r in c.execute("SELECT * FROM char_sources"):
            s.char_sources.setdefault((r["concept_id"], r["text"]), []).append(r["chunk_id"])
        for r in c.execute("SELECT * FROM edge_sources"):
            s.edge_sources.setdefault((r["from_id"], r["to_id"], r["type"]), []).append(r["chunk_id"])
        s.manifest = {r["chunk_id"]: r["content_hash"] for r in c.execute("SELECT * FROM chunks")}
        s.concept_hashes = {r["concept_id"]: r["content_hash"]
                            for r in c.execute("SELECT * FROM concept_hashes")}
        s.parked = [{"from": r["from_concept"], "type": r["type"], "to": r["to_concept"],
                     "chunk_id": r["chunk_id"], "reason": r["reason"]}
                    for r in c.execute("SELECT * FROM parked")]
        s.chunk_failures = {
            r["chunk_id"]: {"content_hash": r["content_hash"], "attempts": r["attempts"],
                            "reason": r["reason"], "quarantined": bool(r["quarantined"]),
                            "last_ts": r["last_ts"]}
            for r in c.execute("SELECT * FROM chunk_failures")}
        row = c.execute("SELECT value FROM meta WHERE key='rel_vocab'").fetchone()
        s.rel_vocab = json.loads(row["value"]) if row else []
        meta = dict(c.execute("SELECT key, value FROM meta"))
        s.next_concept_id = int(meta.get("next_concept_id", "0"))
        s.embedding_model = meta.get("embedding_model", "")
        s.chunk_strategy = meta.get("chunk_strategy", "legacy")
        s.extractions = {r["chunk_id"]: json.loads(r["record"])
                         for r in c.execute("SELECT * FROM extractions")}
        for r in c.execute("SELECT * FROM embeddings"):
            # Embeddings are a disposable cache, unlike extraction records.
            try:
                vec = json.loads(r["vector"])
                if (isinstance(vec, list) and vec
                        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in vec)):
                    s.embeddings[r["text"]] = vec
            except (ValueError, TypeError):
                pass  # missing/broken cache entries are embedded during projection
        return s

    def get_extraction(self, source: str):
        """Read one source receipt without materializing the full graph."""
        row = self.conn.execute("SELECT record FROM extractions WHERE chunk_id=?", (source,)).fetchone()
        return json.loads(row["record"]) if row else None

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
