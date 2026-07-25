"""Snapshot the pilot's durable state before a run that can change it.

Every batch loop so far has been preceded by a hand-typed copy, and the hand
typed the wrong thing at least once: the early snapshots took `kee-workdir` only.
That directory holds the *working graph* — the extraction truth — but not the
evidence index (`kg/`) and not the governed workspace (`ws/`, which carries the
audit trail the active ontology is rehydrated from). Restoring from such a
snapshot would have brought back the graph with no evidence to score it against
and no record of what had been adopted.

The three directories are one unit of state. Backing up a subset is not a
smaller backup; it is a backup that does not restore.

Usage:
    python backup_state.py            # snapshot -> _backup-YYYYmmdd-HHMM/
    python backup_state.py --verify   # check the newest snapshot is complete
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

PILOT = Path(__file__).resolve().parent

# Each entry is (directory, a file that must exist inside it for the copy to
# count as real). Presence of the directory alone proves nothing — an
# interrupted copytree leaves one behind.
STATE = [
    ("kee-workdir", "graph_store.db"),      # working graph: extraction truth
    ("kg", "ingest-manifest.json"),         # evidence index gaps are scored against
    ("ws", "config.json"),                  # workspace: audit trail, active ontology
]


def snapshot() -> Path:
    dest = PILOT / f"_backup-{time.strftime('%Y%m%d-%H%M')}"
    dest.mkdir(parents=True, exist_ok=True)
    for name, _sentinel in STATE:
        src = PILOT / name
        if not src.exists():
            print(f"[backup] SKIP {name} (absent)")
            continue
        print(f"[backup] {name} -> {dest.name}/{name}", flush=True)
        shutil.copytree(src, dest / name, dirs_exist_ok=True)
    return dest


def verify(dest: Path) -> bool:
    ok = True
    for name, sentinel in STATE:
        if not (PILOT / name).exists():
            continue
        f = dest / name / sentinel
        if f.is_file() and f.stat().st_size > 0:
            print(f"[backup] ok      {name}/{sentinel}  {f.stat().st_size:,} bytes")
        else:
            print(f"[backup] MISSING {name}/{sentinel}")
            ok = False
    return ok


def newest() -> Path | None:
    snaps = sorted(PILOT.glob("_backup-*"), key=lambda p: p.name)
    return snaps[-1] if snaps else None


if __name__ == "__main__":
    if "--verify" in sys.argv:
        d = newest()
        if d is None:
            print("[backup] no snapshot found")
            raise SystemExit(1)
        print(f"[backup] verifying {d.name}")
        raise SystemExit(0 if verify(d) else 1)
    d = snapshot()
    # A backup nobody checked is a backup nobody has: verify in the same breath.
    raise SystemExit(0 if verify(d) else 1)
