"""The showcase: the SAME compliance question answered two ways —

  BARE       the local LLM alone (its own knowledge, no corpus access)
  AUGMENTED  the same local LLM given ONE MCP tool (run_compliance_audit),
             which runs the authored checklist against the knowledge graph and
             returns grounded satisfied/gap verdicts WITH corpus evidence.

The point: the bare model cannot know whether 49 specific Romanian acts satisfy
a data-protection-impact requirement — so it guesses or demurs. The augmented
model calls the reasoner, gets the provable GAP with the queries that came up
empty, and reports it. That verdict is only possible because of the MCP.

Run:
    python agent_demo.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("GRAP_EMBED_BACKEND", "ollama")
os.environ.setdefault("GRAP_EMBED_MODEL", "bge-m3")
os.environ.setdefault("GRAP_EMBED_BASE_URL", "http://localhost:11434/v1")

from llm_client import chat, MODEL, BASE_URL      # noqa: E402
from audit import load_audit, run_audit           # noqa: E402

_DOCS = ROOT / "pilot-law-ro" / "docs"
_NDOCS = len(list(_DOCS.glob("*.json"))) if _DOCS.exists() else 0

QUESTION = (
    f"Am un corpus de {_NDOCS} de acte normative românești (1998-1999). Verifică dacă "
    "acestea respectă următoarele cerințe de conformitate: (1) indicarea "
    "emitentului, (2) publicarea în Monitorul Oficial, (3) referința la "
    "modificarea și completarea altor acte, (4) evaluarea impactului asupra "
    "protecției datelor cu caracter personal, (5) o perioadă de consultare "
    "publică. Pentru fiecare cerință spune dacă este ÎNDEPLINITĂ sau LIPSEȘTE și "
    "justifică pe baza probelor din corpus."
)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_compliance_audit",
        "description": (
            "Audit the loaded corpus of Romanian normative acts against the "
            "authored requirement checklist. Returns each requirement as "
            "satisfied or gap, with the supporting corpus evidence snippet, a "
            "coverage score, and the search queries used. This is grounded "
            "ground-truth from the knowledge graph — use it before answering, "
            "and report any requirement with no supporting evidence as a GAP."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]

BARE_SYSTEM = (
    "Ești auditor de conformitate. Răspunde exclusiv din cunoștințele tale, fără "
    "acces la corpus. Fii concret."
)
AUG_SYSTEM = (
    "Ești auditor de conformitate. Trebuie să folosești unealta "
    "run_compliance_audit pentru a obține verdicte fundamentate ÎNAINTE de a "
    "răspunde. Citează probele. O cerință fără probe se raportează ca LIPSĂ — nu "
    "ghici. Răspunde EXCLUSIV în limba română, indiferent de limba în care sunt "
    "returnate datele din unealtă."
)


def _text(msg: dict) -> str:
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()


def run_bare() -> str:
    return _text(chat([{"role": "system", "content": BARE_SYSTEM},
                       {"role": "user", "content": QUESTION}], max_tokens=700))


def _summarize_report(report: dict) -> dict:
    """Compact the audit for the model: verdict + one evidence line each."""
    reqs = []
    for r in report["requirements"]:
        ev = next((c["evidence_snippet"][:120] for c in r["characteristics"]
                   if c["satisfied"] and c["evidence_snippet"]), "")
        gapq = [q for c in r["characteristics"] if not c["satisfied"] for q in c["queries"]]
        reqs.append({
            "cerinta": r["name"],
            "verdict": "ÎNDEPLINITĂ" if r["satisfied"] else "LIPSEȘTE",
            "proba": ev,
            "interogari_fara_rezultat": gapq if not r["satisfied"] else [],
        })
    return {"summary": report["summary"], "requirements": reqs}


def run_augmented(audit_ctx) -> tuple[str, dict]:
    kg, on, pg = audit_ctx
    msgs = [{"role": "system", "content": AUG_SYSTEM},
            {"role": "user", "content": QUESTION}]
    first = chat(msgs, tools=TOOLS, max_tokens=700)
    calls = first.get("tool_calls") or []

    report = run_audit(kg, on, pg)          # run it regardless (guarantees grounding)
    compact = _summarize_report(report)

    if calls:
        # Model asked for the tool: answer its call ids properly.
        msgs.append({"role": "assistant", "content": first.get("content") or "",
                     "tool_calls": calls})
        for c in calls:
            msgs.append({"role": "tool", "tool_call_id": c.get("id", "call_0"),
                         "content": json.dumps(compact, ensure_ascii=False)})
        called_tool = True
    else:
        # Fallback: model didn't emit a call — inject the grounded result so the
        # demo is deterministic, and tell it to base the answer on this.
        msgs.append({"role": "user", "content":
                     "Rezultatul verificat al run_compliance_audit (folosește-l ca "
                     "adevăr fundamentat):\n" + json.dumps(compact, ensure_ascii=False)})
        called_tool = False

    final = chat(msgs, tools=TOOLS, max_tokens=900)
    answer = _text(final)
    return answer, {"called_tool": called_tool, "report": report}


def _rule(title: str) -> str:
    return f"\n{'=' * 78}\n{title}\n{'=' * 78}"


def main() -> int:
    # The whole demo answers in Romanian; the Windows console's cp1252 codepage
    # cannot encode ţ/ş/ă, so force UTF-8 before printing anything.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"model: {MODEL} @ {BASE_URL}")
    print(f"\nQUESTION:\n{QUESTION}")

    kg_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "pilot-law-ro" / "kg")
    audit_ctx = load_audit(kg_path,
                           str(HERE / "requirement-ontology.yaml"),
                           str(HERE / "audit.pg.yaml"))

    print(_rule("A) BARE LLM  — no corpus access, no MCP"))
    print(run_bare())

    print(_rule("B) MCP-AUGMENTED LLM  — same model + run_compliance_audit tool"))
    answer, meta = run_augmented(audit_ctx)
    print(answer)

    r = meta["report"]["summary"]
    print(_rule("GROUND TRUTH (what the tool actually returned)"))
    print(f"tool invoked by model: {meta['called_tool']}")
    print(f"{r['requirements_satisfied']}/{r['requirements_total']} requirements satisfied, "
          f"{r['characteristics_gap']} characteristic gap(s), threshold {r['threshold']}")
    for req in meta["report"]["requirements"]:
        print(f"  [{'PASS' if req['satisfied'] else 'GAP '}] {req['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
