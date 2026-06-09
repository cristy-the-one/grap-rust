"""SQLite entity-index sidecar: fast alias/name -> node-id lookup for a KG.

Stdlib sqlite3 (no extra dependency), stored next to the KG. Schema is versioned
via PRAGMA user_version with forward-only migrations. Lookup is exact-alias,
normalized case-insensitively; fuzzy/semantic matching is search_and_traverse_hybrid's job.
"""
import sqlite3
from typing import Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

_MIGRATIONS = [
    lambda c: c.executescript(
        """
        CREATE TABLE IF NOT EXISTS aliases (
            alias_norm TEXT NOT NULL,
            alias      TEXT NOT NULL,
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
    """Open (and migrate) the entity-index db at `db_path`. Context manager."""

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
