"""Phase 2: the durable source of truth for the extraction graph.

A versioned sqlite sidecar (the EntityIndex pattern) holding WorkingGraph's
state: concepts, aliases, characteristics, edges, per-chunk provenance, the
chunk content-hash manifest, relation vocabulary, and parked relations.
Pure load-all / save-all persistence; no graph logic.
"""

from __future__ import annotations

import json
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

    def save(self, state: GraphState) -> None:
        """Full replace: clear every table, then write `state`. Rolls back on error."""
        c = self.conn
        try:
            for t in ("concepts", "aliases", "characteristics", "edges", "chunks",
                      "concept_sources", "char_sources", "edge_sources", "parked", "meta",
                      "concept_hashes"):
                c.execute(f"DELETE FROM {t}")
            c.executemany("INSERT INTO concepts(concept_id, canonical) VALUES (?,?)",
                          list(state.concepts.items()))
            c.executemany("INSERT INTO aliases(concept_id, alias) VALUES (?,?)",
                          [(cid, a) for cid, al in state.aliases.items() for a in al])
            c.executemany("INSERT INTO characteristics(concept_id, text) VALUES (?,?)",
                          [(cid, t) for cid, ts in state.chars.items() for t in ts])
            c.executemany("INSERT INTO edges(from_id, to_id, type) VALUES (?,?,?)",
                          [(f, t, ty) for (f, t, ty) in state.edges])
            c.executemany("INSERT INTO chunks(chunk_id, content_hash) VALUES (?,?)",
                          list(state.manifest.items()))
            c.executemany("INSERT INTO concept_hashes(concept_id, content_hash) VALUES (?,?)",
                          list(state.concept_hashes.items()))
            c.executemany("INSERT INTO concept_sources(concept_id, chunk_id) VALUES (?,?)",
                          [(cid, ch) for cid, chs in state.concept_sources.items() for ch in chs])
            c.executemany("INSERT INTO char_sources(concept_id, text, chunk_id) VALUES (?,?,?)",
                          [(cid, txt, ch) for (cid, txt), chs in state.char_sources.items() for ch in chs])
            c.executemany("INSERT INTO edge_sources(from_id, to_id, chunk_id) VALUES (?,?,?)",
                          [(f, t, ch) for (f, t), chs in state.edge_sources.items() for ch in chs])
            # OR IGNORE: the working graph dedupes parked entries, but a save
            # must never lose an entire extraction over a duplicate row.
            c.executemany("INSERT OR IGNORE INTO parked(from_concept, type, to_concept, chunk_id, reason) VALUES (?,?,?,?,?)",
                          [(p["from"], p["type"], p["to"], p["chunk_id"], p["reason"]) for p in state.parked])
            c.execute("INSERT INTO meta(key, value) VALUES ('rel_vocab', ?)",
                      (json.dumps(state.rel_vocab),))
            c.commit()
        except Exception:
            c.rollback()
            raise

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
        row = c.execute("SELECT value FROM meta WHERE key='rel_vocab'").fetchone()
        s.rel_vocab = json.loads(row["value"]) if row else []
        return s

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
