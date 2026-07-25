"""10x10 incremental-adoption loop (v2): grow the corpus to 400 docs, 10 at a
time, letting the dark factory auto-adopt each batch. Captures how the ontology
and KG evolve batch by batch.

Calibration learned from v1 (all recorded in the code below):
- GRAP_LLM_TIMEOUT must exceed max_tokens/throughput. 2048 tok / ~5.8 tok/s =
  ~353s, so a 300s timeout made every capped generation time out and burn 3
  doomed retries (~15 min each). 420s fixes it.
- canary_max_new_gaps: the canary originally counted EVERY new gap target,
  including gaps on newly added outcomes (whose characteristics have no
  matching evidence yet). That made the count ~constant (mean 848, sd 17 over
  27 batches) regardless of what a batch did, so no budget discriminated: 40
  rolled back every batch, 2500 accepted every batch. The canary now gates on
  REGRESSIONS only (new gaps on outcomes that existed before), so a small
  budget is meaningful again; growth gaps are reported separately.

Resumable: targets TOTAL_DOCS and stages from wherever docs/ currently is.
Writes loop-metrics.json after every batch.
"""
import json, os, shutil, sys, time
from pathlib import Path

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent
DOCS = PILOT / "docs"
POOL = ROOT / "real-data-law-ro"
# Phase 2 (2026-07-24): finer 5-doc batches, continue 400 -> 600. Separate
# metrics file so the 10-doc study (loop-metrics-10doc.json) stays intact.
METRICS = PILOT / "loop-metrics-5doc.json"

TOTAL_DOCS = 600
PER_BATCH = 5

os.environ["GRAP_WORKSPACE"] = str(PILOT / "ws")
os.environ["GRAP_ROLE"] = "admin"
os.environ["GRAP_LLM_BASE_URL"] = "http://127.0.0.1:8080/v1"
os.environ["GRAP_LLM_MODEL"] = "ternary-bonsai-27b"
os.environ["GRAP_LLM_EXTRA_BODY"] = '{"chat_template_kwargs": {"enable_thinking": false}}'
os.environ["GRAP_LLM_TIMEOUT"] = "420"        # > 2048/5.8 = 353s (v1 lesson)
os.environ["GRAP_LLM_RETRIES"] = "2"
os.environ["GRAP_LLM_MAX_TOKENS"] = "2048"
os.environ["GRAP_EMBED_BACKEND"] = "ollama"
os.environ["GRAP_EMBED_MODEL"] = "bge-m3"
os.environ["GRAP_EMBED_BASE_URL"] = "http://127.0.0.1:11434/v1"
os.environ.pop("GRAP_LLM_STUB", None)

sys.path.insert(0, str(ROOT / "mcp-server"))
import grap_mcp.server as s  # noqa: E402

WORKDIR_DB = PILOT / "kee-workdir" / "graph_store.db"


def wg_counts():
    import sqlite3
    if not WORKDIR_DB.exists():
        return {}
    c = sqlite3.connect(f"file:{WORKDIR_DB}?mode=ro", uri=True)
    out = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ("concepts", "characteristics", "edges", "chunks", "parked")}
    c.close()
    return out


def active_size():
    onto = s._active_ontology_dict(s._get_workspace())
    return len(onto.get("process_outcomes", [])) if onto else 0


def latest_canary(ws, since_ts=None):
    """Latest canary event, but ONLY if it is newer than `since_ts`.

    A rejected proposal never runs a canary, so without this guard the metrics
    reported the previous batch's canary as if it were this batch's — which made
    11 consecutive churn rejections look like 11 passing canaries."""
    evs = ws.query_events("canary_result") or []
    if not evs:
        return {}
    if since_ts is not None and evs[-1].ts <= since_ts:
        return {"result": None, "new": None, "growth": None, "stale": True}
    kv = s._kv_subject(evs[-1].subject)
    return {"ts": evs[-1].ts, "result": kv.get("result"),
            "new": int(kv.get("new", 0) or 0),
            "growth": int(kv.get("growth", 0) or 0)}


def next_batch_docs(n):
    present = {p.name for p in DOCS.glob("*.json")}
    pool = sorted((p for p in POOL.glob("*.json") if p.name not in present),
                  key=lambda p: p.stat().st_size)
    return pool[:n]


STRICT = {"max_outcomes_added": 5, "max_outcomes_removed": 0,
          "max_characteristics_removed": 0, "canary_max_new_gaps": 0}


def restore_strict(why: str = "restore strict after loop"):
    for k, v in STRICT.items():
        s.set_policy(k, v, rationale=why)


def main():
    # The canary now gates on REGRESSIONS only (gaps on outcomes that existed
    # before), not on growth, so this budget is a real threshold rather than the
    # blunt 2500 that accepted everything. Growth gaps are reported separately.
    policy = {"enabled": True, "canary_enabled": True,
              "max_outcomes_added": 150, "max_outcomes_removed": 20,
              "max_characteristics_removed": 60, "canary_max_new_gaps": 25}
    for k, v in policy.items():
        r = s.set_policy(k, v, rationale="10x10 loop v2 (timeout+canary calibrated)")
        assert r["status"] == "ok", r

    s.load_knowledge_graph(str(PILOT / "kg"))
    ws = s._get_workspace()
    rows = json.loads(METRICS.read_text()) if METRICS.exists() else []
    start_batch = len(rows)
    present0 = len(list(DOCS.glob("*.json")))
    print(f"[loop] resume: {present0} docs present, target {TOTAL_DOCS}, "
          f"{len(rows)} batches already recorded, active={active_size()}")

    cfg = str(PILOT / "pipeline.json")
    try:
        bi = start_batch
        while len(list(DOCS.glob("*.json"))) < TOTAL_DOCS:
            bi += 1
            pre_wg, pre_active = wg_counts(), active_size()
            want = min(PER_BATCH, TOTAL_DOCS - len(list(DOCS.glob("*.json"))))
            for src in next_batch_docs(want):
                shutil.copy2(src, DOCS / src.name)
            docs_total = len(list(DOCS.glob("*.json")))

            t0 = time.time()
            canary_ts0 = (ws.query_events("canary_result") or [None])[-1]
            canary_ts0 = getattr(canary_ts0, "ts", None)
            rep = s.run_pipeline(cfg)
            dt = time.time() - t0

            post_wg, post_active = wg_counts(), active_size()
            ext = rep.get("steps", {}).get("extract", {})
            adopt = ext.get("auto_adoption") or {}
            gaps = rep.get("steps", {}).get("gaps", {})
            can = latest_canary(ws, since_ts=canary_ts0)
            row = {
                "batch": bi, "docs_total": docs_total, "wall_s": round(dt),
                "extract_added": ext.get("added"),
                "churn_pass": bool(adopt.get("pass")),
                "churn_detail": adopt.get("failed_checks") or "ok",
                "canary_result": can.get("result"),
                "canary_new_gaps": can.get("new"),
                "canary_growth_gaps": can.get("growth"),
                "active_before": pre_active, "active_after": post_active,
                "active_advanced": post_active > pre_active,
                "concepts": post_wg.get("concepts"),
                "concepts_delta": post_wg.get("concepts", 0) - pre_wg.get("concepts", 0),
                "chars": post_wg.get("characteristics"),
                "edges": post_wg.get("edges"),
                "edges_delta": post_wg.get("edges", 0) - pre_wg.get("edges", 0),
                "parked": post_wg.get("parked"),
                "gaps_found": gaps.get("gaps_found"),
                "gaps_delta": gaps.get("delta"),
            }
            rows.append(row)
            METRICS.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
            print(f"[loop] batch {bi:>2} docs={docs_total} added={row['extract_added']} "
                  f"churn={'ok' if row['churn_pass'] else row['churn_detail']} "
                  f"canary={row['canary_result']}(new={row['canary_new_gaps']}) "
                  f"active={pre_active}->{post_active} conc+={row['concepts_delta']} "
                  f"edges+={row['edges_delta']} {row['wall_s']}s", flush=True)
    finally:
        restore_strict("restore strict after loop")
        print("[loop] strict policy restored", flush=True)
    print(f"[loop] done; metrics -> {METRICS}", flush=True)


if __name__ == "__main__":
    main()
