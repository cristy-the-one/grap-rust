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
METRICS = PILOT / "loop-metrics-1683.json"

TOTAL_DOCS = 1683
# Batches are sized by CHUNKS, not documents — a chunk is ~95s of decode, a
# document is anywhere from 1 to 248 of them. 25 keeps a batch at the wall-time
# the count-based runs actually produced (10 docs ~ 22 chunks ~ 35 min), so the
# schedule stays what it has been while the documents get larger.
CHUNK_BUDGET = 25
CHUNK_CHARS = 2000            # must match pipeline.json extract.chunk_chars
MAX_DOCS_PER_BATCH = 25       # guard for a run of tiny documents

os.environ["GRAP_WORKSPACE"] = str(PILOT / "ws")
os.environ["GRAP_ROLE"] = "admin"
os.environ["GRAP_LLM_BASE_URL"] = "http://127.0.0.1:8080/v1"
os.environ["GRAP_LLM_MODEL"] = "ternary-bonsai-27b"
os.environ["GRAP_LLM_EXTRA_BODY"] = '{"chat_template_kwargs": {"enable_thinking": false}}'
os.environ["GRAP_LLM_TIMEOUT"] = "420"        # > 2048/5.8 = 353s (v1 lesson)
os.environ["GRAP_LLM_RETRIES"] = "2"
os.environ["GRAP_LLM_MAX_TOKENS"] = "2048"
# Measured-best extraction context (distillation iter 4b arm C): class vocabulary
# + known concept NAMES only. The class list is the distilled schema from iter 5b.
os.environ["GRAP_KEE_CONTEXT_MODE"] = "names"
os.environ["GRAP_KEE_CLASS_VOCAB"] = ("NormativeAct, AuthoritativeBody, Official, "
                                      "OfficialGazette, StructuralPart, "
                                      "LegalChallenge, Budget")
os.environ["GRAP_EMBED_BACKEND"] = "ollama"
os.environ["GRAP_EMBED_MODEL"] = "bge-m3"
os.environ["GRAP_EMBED_BASE_URL"] = "http://127.0.0.1:11434/v1"
os.environ.pop("GRAP_LLM_STUB", None)

sys.path.insert(0, str(ROOT / "mcp-server"))
import grap_mcp.server as s  # noqa: E402
from grap_mcp.ingest import chunk_text  # noqa: E402
from grap_mcp.kee import tabular  # noqa: E402
from grap_mcp.kee.pipeline import _text_and_label_from_json  # noqa: E402

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


def doc_chunks(p):
    """Chunks this document will cost, using the pipeline's own chunker.

    Cheap (a read plus a split) next to ~95s of decode per chunk, and it has to
    be the real chunker: file size is a poor proxy because the law-aware JSON
    extractor discards metadata, and chunking splits on blank lines first."""
    try:
        text, _ = _text_and_label_from_json(json.loads(p.read_text(encoding="utf-8")), p.stem)
    except Exception:
        return 1
    # Count only what will actually be SENT to the model. Tabular chunks are
    # skipped at extraction and so never enter the manifest — counting them here
    # would make pending_chunks() permanently non-zero and shrink every batch
    # budget by the size of the tables it is not going to process.
    cs = [c for c in chunk_text(text, max_chars=CHUNK_CHARS) if not tabular.is_tabular(c)]
    return max(1, len(cs))


def pending_chunks():
    """Chunks sitting in docs/ that the working graph has not extracted yet.

    Normally zero. After an interrupted run it is that batch's unfinished work,
    which the next `run_pipeline` will pick up whether or not this driver stages
    anything more."""
    import sqlite3
    if not WORKDIR_DB.exists():
        return 0
    c = sqlite3.connect(f"file:{WORKDIR_DB}?mode=ro", uri=True)
    recorded = {}
    for (cid,) in c.execute("SELECT chunk_id FROM chunks"):
        recorded[cid.rsplit("#", 1)[0]] = recorded.get(cid.rsplit("#", 1)[0], 0) + 1
    c.close()
    total = 0
    for p in DOCS.glob("*.json"):
        total += max(0, doc_chunks(p) - recorded.get(p.stem, 0))
    return total


def next_batch_docs(remaining_docs):
    """Stage documents until the batch's CHUNK budget is spent.

    Batches used to be sized by document COUNT, which was fine while documents
    were uniform: 10 docs ~ 22 chunks ~ 35 min. The corpus is staged
    smallest-first, so the remaining documents are all larger than the processed
    ones and the tail is extreme — `50738.json` alone is 248 chunks, ~6.5h, and
    under count-based staging it would have shared a batch with nine others and
    turned a 35-minute batch into an all-nighter.

    Work, not cardinality, is what a batch should hold constant. A document
    bigger than the whole budget gets a batch to itself rather than being
    unschedulable."""
    present = {p.name for p in DOCS.glob("*.json")}
    pool = sorted((p for p in POOL.glob("*.json") if p.name not in present),
                  key=lambda p: p.stat().st_size)
    # Work already staged but not yet extracted counts against this batch's
    # budget. An interrupted run (the 2026-07-28 session restart killed one
    # mid-batch) leaves its documents in docs/, and the next run extracts them
    # alongside whatever it stages — so a naive resume produced a double-size
    # batch. The budget is about how much the FACTORY will do, not about how
    # much this function copies.
    pending = pending_chunks()
    picked, spent = [], pending
    if pending:
        # Two quite different causes, same accounting: an interrupted run leaves
        # a whole batch unextracted, and ordinary batches leave a small tail of
        # chunks that failed and are awaiting their next of three attempts.
        # Both are work the next run_pipeline will do, so both belong in the
        # budget — but calling a routine retry tail "interrupted" sent me
        # looking for an interruption that had not happened.
        print(f"[loop] {pending} chunks already staged and unextracted "
              f"(interrupted batch and/or retry tail); "
              f"staging against the remaining budget", flush=True)
    for p in pool[:max(1, remaining_docs)]:
        c = doc_chunks(p)
        # `picked or pending`: the empty-picked case exists so a document larger
        # than the whole budget still gets scheduled (alone) rather than being
        # skipped forever. That exemption must not apply when the budget is
        # already spoken for by an interrupted batch — otherwise a resume stages
        # a document on top of a full batch, which is the case it was added for.
        if (picked or pending) and spent + c > CHUNK_BUDGET:
            break
        picked.append(p)
        spent += c
        if len(picked) >= MAX_DOCS_PER_BATCH or spent >= CHUNK_BUDGET:
            break
    return picked, spent


def rss_mb():
    """Resident set size, or None. The working graph lives in RAM for the whole
    process and a run is now ~28h, so growth here is worth a column.

    The restypes/argtypes below are load-bearing, not decoration. Without them
    ctypes defaults GetCurrentProcess's return to c_int, which truncates the
    64-bit pseudo-handle, and GetProcessMemoryInfo just returns 0 — silently,
    because the whole thing sat inside `except: pass`. The first run wrote
    `rss_mb: null` seven times and nobody would have noticed until we went
    looking for a memory trend that was never recorded. Theme VIII, again:
    test the path, not the producer."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = []
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                               ctypes.POINTER(PMC), wintypes.DWORD]
        c = PMC(); c.cb = ctypes.sizeof(PMC)
        if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return round(c.WorkingSetSize / (1024 * 1024), 1)
    except Exception:
        logger_warn = True  # noqa: F841 - keep the failure non-fatal, see above
    return None


# ---- circuit breaker -------------------------------------------------------
#
# The loop reported every batch and stopped for nothing. At ~7 batches per
# launch with a human reading the output every few hours that was fine. At 40
# batches it is not: a degradation at batch 12 would grind through 28 more
# before anyone looked, spending ~20h of decode and 28 adoptions on a factory
# that had already told us it was unwell.
#
# Anything here means STOP and let a human look. These are not tuning knobs —
# each one is a condition where continuing destroys information or compounds a
# fault. Routine variation (parse-failure ticks, quarantines, wall-time drift)
# deliberately does NOT stop the loop.
CANARY_WARN = 40          # log loudly; one outlier is not a pattern
CANARY_WARN_STREAK = 2    # ...but two in a row is


def abort_reasons(row, prev_rows):
    out = []
    q = row.get("quality") or {}
    # Check the infrastructure BEFORE the governance verdicts. When the LLM
    # server is unreachable every chunk fails, nothing is extracted, no proposal
    # exists, and `churn_pass` is False purely because there was nothing to
    # adopt — which the churn branch below then reported as "churn REJECTED
    # (ok)". That is a true stop for a false reason, and it sends whoever reads
    # it to the governance policy instead of to the dead server. The first real
    # trip of this breaker (2026-07-28) said exactly that.
    provider_errors = q.get("provider_errors") or 0
    chunks = q.get("chunks") or 0
    if provider_errors and chunks and provider_errors >= chunks:
        out.append(f"LLM PROVIDER UNREACHABLE — all {chunks} chunks failed with transport "
                   f"errors. Nothing was extracted and nothing was quarantined (provider "
                   f"errors are exempt by design). Check the inference server, then resume; "
                   f"the work is still pending.")
        return out                      # everything below is a consequence of this
    if row.get("canary_result") == "failed":
        out.append(f"canary FAILED (reg={row.get('canary_new_gaps')}) — adoption was rolled back")
    if not row.get("churn_pass") and (row.get("extract_added") or 0) > 0:
        out.append(f"churn REJECTED ({row.get('churn_detail')}) — a rejected proposal never "
                   "becomes the baseline, so the next batch churns against a stale one")
    elif not row.get("churn_pass"):
        out.append(f"no proposal to adopt (extract_added={row.get('extract_added')}) — "
                   "the batch produced nothing")
    if row.get("adopted_hash") and not row.get("active_advanced"):
        out.append("adopted but the active ontology did not advance — the "
                   "'adopted with active_delta=0' failure mode")
    # Quality alarms that mean the extraction itself degraded. The no_fragment
    # alarm is excluded on purpose: empty/unparseable chunks are absorbed by the
    # dead-letter queue and self-clear, as the 1050->1100 run demonstrated.
    for a in q.get("alarms", []):
        if not a.startswith("no_fragment"):
            out.append(f"quality alarm: {a}")
    recent = [r.get("canary_new_gaps") or 0 for r in prev_rows[-(CANARY_WARN_STREAK - 1):]]
    if (row.get("canary_new_gaps") or 0) >= CANARY_WARN and \
            all(v >= CANARY_WARN for v in recent) and len(recent) == CANARY_WARN_STREAK - 1:
        out.append(f"canary regressions >= {CANARY_WARN} on {CANARY_WARN_STREAK} consecutive "
                   f"batches ({recent + [row.get('canary_new_gaps')]}) — no longer an outlier")
    return out


STRICT = {"max_outcomes_added": 5, "max_outcomes_removed": 0,
          "max_characteristics_removed": 0, "canary_max_new_gaps": 0}


def restore_strict(why: str = "restore strict after loop"):
    for k, v in STRICT.items():
        s.set_policy(k, v, rationale=why)


def main():
    # The canary now gates on REGRESSIONS only (gaps on outcomes that existed
    # before), not on growth, so this budget is a real threshold rather than the
    # blunt 2500 that accepted everything. Growth gaps are reported separately.
    # ARMED for autonomous adoption. The scale-free rate is now the QUALITY gate
    # (observed 0.67 outcomes/chunk over 405 chunks, so 3.0 leaves ~4.5x headroom
    # and still catches a real anomaly); the absolute budgets are a BLAST-RADIUS
    # backstop sized at ~10% of the 3892-outcome active ontology, generous enough
    # that a single batch (~55-70 outcomes) adopts and therefore never accumulates
    # a cascade. Removals sized from the 650-doc run (14 outcomes / 48 chars over
    # 5 batches).
    #
    # canary_max_new_gaps RE-CALIBRATED (2026-07-26) after the floor was fixed.
    #
    # History, because the number moved by 100x and the reason matters. The
    # budget used to be 1200, set above a measured floor of 830 +/- 12
    # "regressions" per batch. That floor was never semantic regression: adoption
    # projected the ontology INTO the evidence KG, taking the index from 4,692
    # nodes to 23,170, so the canary baseline scored a clean corpus and the
    # candidate scored one that was 80% ontology scaffolding. Excluding those
    # nodes from results never stopped them being ranked. The ontology now lives
    # in its own graph (`_project_ontology`), and the floor went with it.
    #
    # MEASURED over the first ten batches on a clean index (950->1000 and
    # 1000->1050): 0, 0, 0, 2, 2, 3, 3, 4, 5, 11 — mean 3.0, sd 3.1, max 11.
    # 50 is ~4.5x the observed max, so ordinary variance cannot trip it (the
    # batch-1 re-baseline transient shrank 84 -> 11 between the two runs), while
    # still being 24x tighter than the old budget and small enough to be a real
    # gate: batches add ~25-30 outcomes, so 50 PRE-EXISTING outcomes losing
    # coverage in one adoption is unambiguous. Deliberately not ~16 (the 1.4x
    # margin the old budget used), because the corpus is entering its
    # large-document tail and variance is visibly rising — a threshold that has
    # to be loosened again is a threshold nobody trusts.
    # See docs/canary-noise-floor-investigation.md.
    policy = {"enabled": True, "canary_enabled": True,
              "max_outcomes_added": 400,
              # RESTORED 2026-07-31 after the structural-label re-baseline
              # adopted (batch 3 of the 1683 run: 59 outcomes and 1,694
              # characteristics removed, canary passed at 48). The widening was
              # for that one adoption; leaving it open would let an accidental
              # mass-removal through unnoticed, which is the thing this budget
              # exists to stop.
              "max_outcomes_removed": 25,
              "max_characteristics_removed": 80,
              "max_outcomes_added_per_chunk": 3.0,
              "canary_max_new_gaps": 50}
    for k, v in policy.items():
        r = s.set_policy(k, v, rationale="loop v3: canary re-calibrated to the "
                                         "post-fix measured floor (10 batches, max 11)")
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
            want = TOTAL_DOCS - len(list(DOCS.glob("*.json")))
            staged, staged_chunks = next_batch_docs(want)
            for src in staged:
                shutil.copy2(src, DOCS / src.name)
            docs_total = len(list(DOCS.glob("*.json")))
            print(f"[loop] batch {bi:2d} staging {len(staged)} docs "
                  f"= {staged_chunks} chunks (budget {CHUNK_BUDGET})", flush=True)

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
                # the *reason*, not just the check name: metrics recorded a bare
                # ['churn'] fourteen times across two runs and the actual budget
                # that tripped had to be queried separately each time
                "churn_violations": adopt.get("churn_violations"),
                "failed_detail": adopt.get("failed_detail"),
                "adopted_hash": adopt.get("version_hash") or adopt.get("adopted"),
                "canary_result": can.get("result"),
                "canary_new_gaps": can.get("new"),
                "canary_growth_gaps": can.get("growth"),
                "canary_raw_new_gaps": can.get("raw_new"),
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
                "context_mode": ext.get("context_mode"),
                "quality": ext.get("quality"),
                "step_s": rep.get("step_s"),
                "phase_s": ext.get("phase_s"),
                "staged_docs": len(staged),
                "staged_chunks": staged_chunks,
                "sec_per_chunk": round(dt / max(1, (ext.get("added") or 1)), 1),
                "rss_mb": rss_mb(),
            }
            rows.append(row)
            METRICS.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
            print(f"[loop] batch {bi:>2} docs={docs_total} added={row['extract_added']} "
                  f"churn={'ok' if row['churn_pass'] else row['churn_detail']} "
                  f"canary={row['canary_result']}(reg={row['canary_new_gaps']},"
                  f"grow={row['canary_growth_gaps']}) "
                  f"active={pre_active}->{post_active} conc+={row['concepts_delta']} "
                  f"edges+={row['edges_delta']} {row['wall_s']}s", flush=True)
            if not row["churn_pass"] and row.get("churn_violations"):
                for v in (row["churn_violations"] if isinstance(row["churn_violations"], list)
                          else [row["churn_violations"]]):
                    print(f"        churn: {str(v)[:150]}", flush=True)
            q = row.get("quality") or {}
            if q:
                print(f"        quality: relCV={q.get('relation_count_cv')} "
                      f"types={q.get('distinct_relation_types')} "
                      f"parsefail={q.get('parse_failure_rate')} "
                      f"clsname={q.get('class_named_outcomes')} "
                      f"notation={q.get('notation_leaks')}", flush=True)
                for a in q.get("alarms", []):
                    print(f"        !! QUALITY ALARM: {a}", flush=True)
            print(f"        cost: {row['sec_per_chunk']}s/chunk  rss={row['rss_mb']}MB",
                  flush=True)
            if row.get("step_s") or row.get("phase_s"):
                parts = [f"{k}={v}" for k, v in (row.get("step_s") or {}).items()]
                parts += [f"{k}={v}" for k, v in (row.get("phase_s") or {}).items()]
                print("        where: " + "  ".join(parts), flush=True)
            if (row.get("canary_new_gaps") or 0) >= CANARY_WARN:
                print(f"        !! canary regressions {row['canary_new_gaps']} "
                      f">= warn threshold {CANARY_WARN}", flush=True)

            stop = abort_reasons(row, rows[:-1])
            if stop:
                print("[loop] CIRCUIT BREAKER — stopping the loop:", flush=True)
                for r in stop:
                    print(f"         * {r}", flush=True)
                print(f"[loop] stopped after batch {bi} at {docs_total} docs; "
                      f"metrics -> {METRICS}", flush=True)
                raise SystemExit(3)
    finally:
        restore_strict("restore strict after loop")
        print("[loop] strict policy restored", flush=True)
    print(f"[loop] done; metrics -> {METRICS}", flush=True)


if __name__ == "__main__":
    main()
