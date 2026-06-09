# SQLite Entity-Index Sidecar (P2-I, index-table half) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** A fast alias/name → node-id lookup for the KG — resolve `"REQ-123"` or a synonym to its node(s) without scanning the graph — backed by a versioned SQLite sidecar, with MCP tools to resolve, register, and (re)build the index from the KG's node labels.

**Architecture:** A `grap_mcp/entity_index.py` module owning a SQLite db file as a **sidecar** next to the KG (`<kg_path>/entity_index.db`). Python's **stdlib `sqlite3`** → zero new dependency. Versioned schema migrations via `PRAGMA user_version` (the jcodemunch `sqlite_store` robustness pattern). MCP tools wrap it; `reindex_entities` populates aliases from the KG's node labels (`kg.get_node_label`).

**Allocation (core vs skin):** Deliberately in the **MCP/Python skin**, NOT the Rust core — a Rust version needs `rusqlite` (heavy dep against the engine's dependency-light design), and the borrowing doc framed this as a sidecar. The hot-path graph engine is untouched.

**Tech Stack:** Python stdlib (`sqlite3`), `grap_mcp`, `pytest`. No new deps. No Rust changes.

**Scope discipline:** Lookup is by exact alias, normalized case-insensitively (no fuzzy/semantic matching — that's what `search_and_traverse_hybrid` is for). Auto-registering aliases on ingest is a noted follow-up; population is via `reindex_entities` (from labels) + explicit `register_alias`.

---

## File Structure
- **Create** `mcp-server/grap_mcp/entity_index.py` — `EntityIndex` (open + migrate + ops).
- **Modify** `mcp-server/grap_mcp/server.py` — `resolve_entity`, `register_alias`, `reindex_entities`, `aliases_for_node` tools; tier assignment in `_TIERS`; capabilities registration.
- **Tests** `mcp-server/tests/test_entity_index.py`.

---

## Task 1: `entity_index.py` — versioned SQLite store

**Files:** Create `mcp-server/grap_mcp/entity_index.py`. Test: `mcp-server/tests/test_entity_index.py`.

- [ ] **Step 1: Failing test** — `mcp-server/tests/test_entity_index.py`:
```python
from grap_mcp.entity_index import EntityIndex, SCHEMA_VERSION

def test_migrations_set_version_and_are_idempotent(tmp_path):
    db = str(tmp_path / "ei.db")
    with EntityIndex(db) as ei:
        assert ei.schema_version() == SCHEMA_VERSION
    # reopen: migrations must not re-run destructively
    with EntityIndex(db) as ei:
        assert ei.schema_version() == SCHEMA_VERSION

def test_register_and_resolve_case_insensitive(tmp_path):
    with EntityIndex(str(tmp_path / "ei.db")) as ei:
        ei.register("REQ-123", 5, kind="requirement")
        ei.register("Brake_Pedal", 9)
        assert ei.resolve("req-123") == [5]      # case-insensitive
        assert ei.resolve("  REQ-123 ") == [5]    # trimmed
        assert ei.resolve("missing") == []

def test_alias_maps_to_multiple_nodes_and_aliases_for(tmp_path):
    with EntityIndex(str(tmp_path / "ei.db")) as ei:
        ei.register("brake", 1)
        ei.register("brake", 2)
        assert sorted(ei.resolve("brake")) == [1, 2]
        ei.register("pedal", 1)
        assert sorted(ei.aliases_for(1)) == ["brake", "pedal"]

def test_register_many_and_remove_node(tmp_path):
    with EntityIndex(str(tmp_path / "ei.db")) as ei:
        ei.register_many([("a", 1, None), ("b", 1, None), ("a", 2, None)])
        assert ei.count() == 3
        removed = ei.remove_node(1)
        assert removed == 2
        assert ei.resolve("a") == [2]
        assert ei.aliases_for(1) == []
```

- [ ] **Step 2: Run, verify FAIL** — `cd mcp-server && python -m pytest tests/test_entity_index.py -q` → module missing.

- [ ] **Step 3: Implement** `mcp-server/grap_mcp/entity_index.py`:
```python
"""SQLite entity-index sidecar: fast alias/name -> node-id lookup for a KG.

A lightweight sidecar (Python stdlib sqlite3 — no extra dependency) stored next
to the KG. Schema is versioned via PRAGMA user_version with forward-only
migrations (the jcodemunch sqlite_store pattern), so the file survives format
evolution. Lookup is exact-alias, normalized case-insensitively; fuzzy/semantic
matching is the job of search_and_traverse_hybrid, not this index.
"""
import sqlite3
from typing import Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

# Forward-only migrations: _MIGRATIONS[i] migrates user_version i -> i+1.
_MIGRATIONS = [
    # v0 -> v1: initial schema
    lambda c: c.executescript(
        """
        CREATE TABLE IF NOT EXISTS aliases (
            alias_norm TEXT NOT NULL,   -- normalized (lower/trimmed) for lookup
            alias      TEXT NOT NULL,   -- original surface form
            node_id    INTEGER NOT NULL,
            kind       TEXT,
            PRIMARY KEY (alias_norm, node_id)
        );
        CREATE INDEX IF NOT EXISTS idx_aliases_node ON aliases(node_id);
        """
    ),
]


def _normalize(alias: str) -> str:
    return alias.strip().lower()


class EntityIndex:
    """Open (and migrate) the entity-index db at `db_path`. Use as a context
    manager or call `close()`."""

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

    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def register(self, alias: str, node_id: int, kind: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO aliases (alias_norm, alias, node_id, kind) VALUES (?, ?, ?, ?)",
            (_normalize(alias), alias, int(node_id), kind),
        )
        self.conn.commit()

    def register_many(self, rows: Iterable[Tuple[str, int, Optional[str]]]) -> int:
        data = [(_normalize(a), a, int(n), k) for (a, n, k) in rows]
        self.conn.executemany(
            "INSERT OR REPLACE INTO aliases (alias_norm, alias, node_id, kind) VALUES (?, ?, ?, ?)",
            data,
        )
        self.conn.commit()
        return len(data)

    def resolve(self, alias: str) -> List[int]:
        cur = self.conn.execute(
            "SELECT node_id FROM aliases WHERE alias_norm = ? ORDER BY node_id",
            (_normalize(alias),),
        )
        return [r["node_id"] for r in cur.fetchall()]

    def aliases_for(self, node_id: int) -> List[str]:
        cur = self.conn.execute(
            "SELECT alias FROM aliases WHERE node_id = ? ORDER BY alias", (int(node_id),)
        )
        return [r["alias"] for r in cur.fetchall()]

    def remove_node(self, node_id: int) -> int:
        cur = self.conn.execute("DELETE FROM aliases WHERE node_id = ?", (int(node_id),))
        self.conn.commit()
        return cur.rowcount

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
```

- [ ] **Step 4: Run, verify PASS** — `cd mcp-server && python -m pytest tests/test_entity_index.py -q`.

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/entity_index.py mcp-server/tests/test_entity_index.py
git commit -m "feat(mcp): SQLite entity-index sidecar (versioned migrations; alias->node lookup)"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: MCP tools (resolve / register / reindex / aliases_for)

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: `mcp-server/tests/test_entity_index.py` (append).

- [ ] **Step 1: Failing test** — append (mirror `test_analytics.py`'s `import grap_mcp.server as s` + monkeypatch style):
```python
import grap_mcp.server as s

class _LabelKG:
    """Fake KG exposing the id-space + labels reindex needs."""
    def __init__(self, labels):  # labels: {node_id: label or None}
        self._labels = labels
    def node_id_space(self):
        return max(self._labels) + 1 if self._labels else 0
    def is_node_deleted(self, nid):
        return nid not in self._labels
    def get_node_label(self, nid):
        return self._labels.get(nid)

def test_entity_tools_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setitem(s._state, "kg", _LabelKG({0: "REQ-1", 1: "SIG-2", 2: None}))
    monkeypatch.setitem(s._state, "kg_path", str(tmp_path))
    # reindex from labels
    r = s.reindex_entities()
    assert r["status"] == "ok" and r["indexed"] == 2   # node 2 has no label
    # resolve (case-insensitive)
    out = s.resolve_entity("req-1")
    assert out["status"] == "ok" and out["node_ids"] == [0]
    # register a manual alias
    assert s.register_alias("brake-by-wire", 0)["status"] == "ok"
    assert 0 in s.resolve_entity("brake-by-wire")["node_ids"]
    # aliases_for_node
    al = s.aliases_for_node(0)
    assert al["status"] == "ok" and "REQ-1" in al["aliases"] and "brake-by-wire" in al["aliases"]
```

- [ ] **Step 2: Run, verify FAIL** — tools undefined.

- [ ] **Step 3: Implement** — add four tools to `server.py`. Use the established read-tool pattern (`_require("operator")`, KG/kg_path guards). Use `@tiered` (the tiering decorator added in P2-H). The db path is `<kg_path>/entity_index.db`. Open/close the `EntityIndex` per call (sqlite is fast; avoids shared-connection lifecycle). Add a small private helper for the db path:
```python
def _entity_index_path():
    kg_path = _state.get("kg_path")
    if not kg_path:
        return None
    from pathlib import Path as _P
    return str(_P(kg_path) / "entity_index.db")


@tiered
def resolve_entity(alias: str) -> dict:
    """Resolve an entity name/alias to KG node id(s) via the SQLite entity index
    (exact, case-insensitive). Build it first with reindex_entities."""
    denied = _require("operator")
    if denied:
        return denied
    path = _entity_index_path()
    if not path:
        return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
    try:
        from grap_mcp.entity_index import EntityIndex
        with EntityIndex(path) as ei:
            return {"status": "ok", "alias": alias, "node_ids": ei.resolve(alias)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def register_alias(alias: str, node_id: int, kind: str = None) -> dict:
    """Register an alias -> node-id mapping in the entity index."""
    denied = _require("operator")
    if denied:
        return denied
    path = _entity_index_path()
    if not path:
        return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
    try:
        from grap_mcp.entity_index import EntityIndex
        with EntityIndex(path) as ei:
            ei.register(alias, node_id, kind)
        return {"status": "ok", "alias": alias, "node_id": node_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def aliases_for_node(node_id: int) -> dict:
    """List the registered aliases for a node id."""
    denied = _require("operator")
    if denied:
        return denied
    path = _entity_index_path()
    if not path:
        return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
    try:
        from grap_mcp.entity_index import EntityIndex
        with EntityIndex(path) as ei:
            return {"status": "ok", "node_id": node_id, "aliases": ei.aliases_for(node_id)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def reindex_entities() -> dict:
    """Rebuild the entity index from the current KG: every live node's label
    becomes a resolvable alias. Returns the number indexed."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.get("kg")
    path = _entity_index_path()
    if not kg or not path:
        return {"status": "error", "message": "Load or create a Knowledge Graph first."}
    try:
        from grap_mcp.entity_index import EntityIndex
        # id-space upper bound: prefer node_id_space(), fall back to node_count().
        try:
            n = kg.node_id_space()
        except Exception:
            n = kg.node_count()
        rows = []
        for nid in range(n):
            try:
                if kg.is_node_deleted(nid):
                    continue
                label = kg.get_node_label(nid)
            except Exception:
                continue
            if label:
                rows.append((label, nid, None))
        with EntityIndex(path) as ei:
            indexed = ei.register_many(rows)
        return {"status": "ok", "indexed": indexed}
    except Exception as e:
        logger.exception("reindex_entities failed")
        return {"status": "error", "message": str(e)}
```
   **Tier assignment** — add to the `_TIERS` dict (from P2-H): `resolve_entity` → `"core"` (it's retrieval, belongs in the minimal surface); `register_alias`, `aliases_for_node`, `reindex_entities` → leave as default `standard` (do NOT add to `_TIERS`). 
   **Capabilities** — add all four tools to `list_capabilities`'s `domains` (the `knowledge` group) so `test_capabilities_cover_all_registered_tools` passes (the taxonomy must equal the registered set in full tier).
   Verify `kg.node_id_space` / `is_node_deleted` / `get_node_label` exist on the pygrap `IndexedKnowledgeGraph` (they do: `node_id_space`, `is_node_deleted`, `get_node_label`). If `node_id_space` is absent, the `node_count()` fallback covers it.

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_entity_index.py -q`; then full `python -m pytest -q` (capabilities coverage MUST pass — all four new tools added to the taxonomy and registered).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_entity_index.py
git commit -m "feat(mcp): entity-index tools (resolve_entity, register_alias, reindex_entities, aliases_for_node)"
```
(Do NOT add out/, terminals/, mcps/, *.patch, *.png, *.pdf, CLAUDE.md, untracked docs/*.md.)

---

## Self-Review
- **Coverage:** versioned SQLite store (T1), MCP tools incl. reindex-from-labels (T2). Matches the P2-I index-table half.
- **Type consistency:** `EntityIndex.{register,register_many,resolve,aliases_for,remove_node,count,schema_version}` consistent T1–T2; tools return `{status:"ok", ...}` like peers; `_entity_index_path()` helper shared.
- **Robustness:** `PRAGMA user_version` forward-only migrations (idempotent reopen); normalized case-insensitive lookup; per-call open/close (no shared-connection hazard); `INSERT OR REPLACE` makes register idempotent; stdlib only (no new dep); Rust core untouched.
- **Honest scope:** exact-alias lookup only (semantic = hybrid search); auto-register-on-ingest deferred; `remove_node` exists on the store for a future ingest/tombstone integration (not yet wired — follow-up).
