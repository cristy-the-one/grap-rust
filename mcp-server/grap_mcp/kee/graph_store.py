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

_MIGRATIONS = [
    lambda c: c.executescript(
        """
        CREATE TABLE IF NOT EXISTS concepts (concept_id INTEGER PRIMARY KEY, canonical TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aliases (concept_id INTEGER, alias TEXT, PRIMARY KEY(concept_id, alias));
        CREATE TABLE IF NOT EXISTS characteristics (concept_id INTEGER, text TEXT, PRIMARY KEY(concept_id, text));
        CREATE TABLE IF NOT EXISTS edges (from_id INTEGER, to_id INTEGER, type TEXT, PRIMARY KEY(from_id, to_id));  -- one edge type per (from,to) pair; matches WorkingGraph._edge_types
        CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS concept_hashes (concept_id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS concept_sources (concept_id INTEGER, chunk_id TEXT, PRIMARY KEY(concept_id, chunk_id));
        CREATE TABLE IF NOT EXISTS char_sources (concept_id INTEGER, text TEXT, chunk_id TEXT, PRIMARY KEY(concept_id, text, chunk_id));
        CREATE TABLE IF NOT EXISTS edge_sources (from_id INTEGER, to_id INTEGER, chunk_id TEXT, PRIMARY KEY(from_id, to_id, chunk_id));
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
    edge_sources: Dict[Tuple[int, int], List[str]] = field(default_factory=dict)
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


class GraphStore:
    """Open (and migrate) the durable graph store at `db_path`. Context manager."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

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
            "edges": (2, [tuple(e) for e in state.edges]),
            "chunks": (1, list(state.manifest.items())),
            "concept_hashes": (1, list(state.concept_hashes.items())),
            "concept_sources": (2, [(cid, ch) for cid, chs in state.concept_sources.items() for ch in chs]),
            "char_sources": (3, [(cid, txt, ch) for (cid, txt), chs in state.char_sources.items() for ch in chs]),
            "edge_sources": (3, [(f, t, ch) for (f, t), chs in state.edge_sources.items() for ch in chs]),
            "parked": (4, [(p["from"], p["type"], p["to"], p["chunk_id"], p["reason"]) for p in state.parked]),
            "chunk_failures": (1, [
                (cid, f.get("content_hash", ""), int(f.get("attempts", 0)),
                 f.get("reason"), int(bool(f.get("quarantined"))), f.get("last_ts"))
                for cid, f in state.chunk_failures.items()]),
            "embeddings": (1, [(text, json.dumps(vec)) for text, vec in state.embeddings.items()]),
            "meta": (1, [("rel_vocab", json.dumps(state.rel_vocab)),
                         ("next_concept_id", str(state.next_concept_id)),
                         ("embedding_model", state.embedding_model)]),
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
            s.edge_sources.setdefault((r["from_id"], r["to_id"]), []).append(r["chunk_id"])
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

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
