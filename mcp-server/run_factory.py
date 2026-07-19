#!/usr/bin/env python3
"""Headless factory runner: one-shot or watch loop around run_pipeline.

    python mcp-server/run_factory.py --config pipeline.json --once
    python mcp-server/run_factory.py --config pipeline.json --watch --interval 300

Watch mode fingerprints the corpus (evidence_dirs + extract.docs_path) and
only runs the pipeline when files actually changed — no LLM calls on a quiet
corpus. Configure workspace / role / local model via the usual env vars
(GRAP_WORKSPACE, GRAP_ROLE, GRAP_LLM_BASE_URL, GRAP_LLM_MODEL,
GRAP_LLM_EXTRA_BODY, GRAP_EMBED_BACKEND). Point an OS scheduler (Task
Scheduler / cron) at --once for fully unattended operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def corpus_fingerprint(dirs) -> str:
    """Stable digest of (path, size, mtime) for every file under `dirs`."""
    h = hashlib.sha256()
    for d in sorted(str(x) for x in dirs):
        base = Path(d)
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file():
                st = f.stat()
                h.update(f"{f.relative_to(base)}|{st.st_size}|{st.st_mtime_ns}\n"
                         .encode("utf-8"))
    return h.hexdigest()


def watched_dirs(cfg: dict) -> list:
    dirs = list(cfg.get("evidence_dirs") or [])
    docs = (cfg.get("extract") or {}).get("docs_path")
    if docs:
        dirs.append(docs)
    return dirs


def _run_once(s, config_path: str) -> int:
    rep = s.run_pipeline(config_path)
    print(json.dumps(rep, indent=2, default=str))
    st = s.get_factory_status()
    if st.get("healthy"):
        print("factory: healthy (needs nobody)")
    else:
        print(f"factory: NEEDS ATTENTION — alarms={st.get('alarms')} "
              f"pending={st.get('pending_interventions')}")
    return 0 if rep.get("status") == "ok" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="pipeline.json")
    ap.add_argument("--once", action="store_true", help="run the line once and exit")
    ap.add_argument("--watch", action="store_true", help="poll the corpus and run on change")
    ap.add_argument("--interval", type=int, default=300, help="watch poll seconds")
    args = ap.parse_args()

    import grap_mcp.server as s
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))

    if not args.watch:
        return _run_once(s, args.config)

    last = None
    dirs = watched_dirs(cfg)
    print(f"watching {dirs} every {args.interval}s (Ctrl+C to stop)")
    while True:
        fp = corpus_fingerprint(dirs)
        if fp != last:
            print(f"[{time.strftime('%H:%M:%S')}] corpus changed -> run_pipeline")
            _run_once(s, args.config)
            last = fp
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
