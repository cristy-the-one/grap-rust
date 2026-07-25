"""One governed pipeline pass — the autonomy demonstration.

No new documents. The only pending change is the backlog the working graph has
accumulated ahead of the active ontology (3998 concepts vs 3892 active outcomes)
plus whatever the ~9% parse-failure chunks yield on retry. So this exercises
exactly the governance path end to end:

    extract (near no-op) -> propose -> churn -> canary -> adopt (or roll back)

with the CALIBRATED budgets: the scale-free rate gate as the quality check, the
absolute budgets as a blast-radius backstop, and canary_max_new_gaps=1200 sitting
above the measured 816-857 floor (see docs/canary-noise-floor-investigation.md).

The question it answers: does `active` advance with nobody in the loop?

Strict policy (5/0/0/0) is restored in a finally, even on interrupt.
"""
import json, os, sys, io, time
from pathlib import Path

PILOT = Path(__file__).resolve().parent
ROOT = PILOT.parent

os.environ["GRAP_WORKSPACE"] = str(PILOT / "ws")
os.environ["GRAP_ROLE"] = "admin"
os.environ["GRAP_LLM_BASE_URL"] = "http://127.0.0.1:8080/v1"
os.environ["GRAP_LLM_MODEL"] = "ternary-bonsai-27b"
os.environ["GRAP_LLM_EXTRA_BODY"] = '{"chat_template_kwargs": {"enable_thinking": false}}'
os.environ["GRAP_LLM_TIMEOUT"] = "420"
os.environ["GRAP_LLM_RETRIES"] = "2"
os.environ["GRAP_LLM_MAX_TOKENS"] = "2048"
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
sys.stdout = io.TextIOWrapper(open(1, "wb", closefd=False), encoding="utf-8",
                              errors="replace", line_buffering=True)

ARMED = {"enabled": True, "canary_enabled": True,
         "max_outcomes_added": 400, "max_outcomes_removed": 25,
         "max_characteristics_removed": 80,
         "max_outcomes_added_per_chunk": 3.0,
         "canary_max_new_gaps": 1200}
STRICT = {"max_outcomes_added": 5, "max_outcomes_removed": 0,
          "max_characteristics_removed": 0, "canary_max_new_gaps": 0}


def active_size():
    o = s._active_ontology_dict(s._get_workspace())
    return len(o.get("process_outcomes", [])) if o else 0


def main():
    for k, v in ARMED.items():
        r = s.set_policy(k, v, rationale="autonomy demonstration: calibrated budgets "
                                         "(canary above its measured 816-857 floor)")
        assert r["status"] == "ok", r
    print(f"[one-pass] policy armed: {ARMED}", flush=True)

    s.load_knowledge_graph(str(PILOT / "kg"))
    before = active_size()
    q0 = s.list_pending_interventions()
    print(f"[one-pass] active BEFORE: {before} outcomes | queue {q0.get('count')} "
          f"{[i.get('kind') for i in q0.get('pending', [])]}", flush=True)

    try:
        t0 = time.time()
        rep = s.run_pipeline(str(PILOT / "pipeline.json"))
        dt = time.time() - t0

        ext = (rep.get("steps") or {}).get("extract") or {}
        adopt = ext.get("auto_adoption") or {}
        gaps = (rep.get("steps") or {}).get("gaps") or {}
        after = active_size()

        print(f"\n[one-pass] pipeline {rep.get('status')} in {dt/60:.1f} min", flush=True)
        print(f"  extract      : added={ext.get('added')} changed={ext.get('changed')} "
              f"promoted={ext.get('promoted')} recovered={ext.get('relations_recovered')}")
        print(f"  churn        : pass={adopt.get('pass')} {adopt.get('failed_checks') or 'ok'}")
        for v in (adopt.get("churn_violations") or []):
            print(f"       {str(v)[:150]}")
        print(f"  canary       : {adopt.get('canary')} "
              f"regressions={adopt.get('new_gaps')} growth={adopt.get('growth_gaps')}")
        print(f"  gaps         : {gaps.get('gaps_found')} delta={gaps.get('delta')}")
        q = ext.get("quality") or {}
        print(f"  quality      : relCV={q.get('relation_count_cv')} "
              f"types={q.get('distinct_relation_types')} "
              f"parsefail={q.get('parse_failure_rate')} alarms={q.get('alarms')}")
        print(f"\n  ACTIVE: {before} -> {after}   "
              f"{'*** ADVANCED AUTONOMOUSLY ***' if after > before else '(unchanged)'}")
        st = s.get_factory_status(); q2 = s.list_pending_interventions()
        print(f"  HEALTHY={st.get('healthy')} alarms={st.get('alarms')} "
              f"queue={q2.get('count')} {[i.get('kind') for i in q2.get('pending', [])]}")
        (PILOT / "one-pass-result.json").write_text(
            json.dumps({"active_before": before, "active_after": after,
                        "wall_s": round(dt), "extract": ext, "gaps": gaps},
                       indent=2, default=str), encoding="utf-8")
    finally:
        for k, v in STRICT.items():
            s.set_policy(k, v, rationale="restore strict after autonomy demonstration")
        print("[one-pass] strict policy restored", flush=True)


if __name__ == "__main__":
    main()
