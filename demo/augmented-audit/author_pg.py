"""PG authoring tool — natural-language requirements -> validated audit graph.

Pipeline (LLM drafts, machine gates — the same spine as draft_pg_patch):

    requirements.nl.md
        -> local LLM drafts a requirement ONTOLOGY (outcomes + RO keywords)
        -> deterministic validation gate (structural + model load); 1 repair
        -> deterministic PG generation (one audit step per outcome, referenced
           as kg:aspice.process_outcome.<id> so the reasoner scopes correctly)
        -> requirement-ontology.yaml + audit.pg.yaml

The LLM does only the semantic lift (fuzzy requirement -> crisp checkable
characteristics + retrieval keywords). Structural correctness and PG<->ontology
coverage are guaranteed by code, not trusted to the model.

Usage:
    python author_pg.py [requirements.nl.md]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "mcp-server"))
sys.path.insert(0, str(HERE))

from grap_mcp import ontology as onto_mod          # noqa: E402
from llm_client import complete, MODEL, BASE_URL    # noqa: E402

OUTCOME_REF_PREFIX = "kg:aspice.process_outcome."

# One crisp call per requirement — a task too small for a local model to
# malform — then the two ontology lists are assembled deterministically in
# code. (Batching all requirements into one call makes Bonsai repeat the
# section headers per item and produce a broken nested list.)
SYSTEM_ITEM = (
    "You convert ONE plain-language compliance requirement into a single "
    "machine-checkable outcome for a Romanian legal corpus. Output ONLY YAML "
    "with exactly these keys, no prose, no code fences:\n"
    "id: <short ascii snake_case>\n"
    "name: <short human name>\n"
    "description: <one sentence>\n"
    "required_characteristics: [<phrase>, ...]\n"
    "keywords: [<token>, ...]\n"
    "Rules:\n"
    "- 1 or 2 required_characteristics, each a DISTINCTIVE Romanian phrase whose "
    "words are joined by underscores (e.g. 'monitorul_oficial', 'consultare_publica', "
    "'protectia_datelor_personale', 'evaluare_impact'). All words of a phrase must "
    "plausibly appear TOGETHER in a passage that actually satisfies the requirement.\n"
    "- NEVER use a single common word alone (e.g. 'data', 'act', 'publica', 'numar', "
    "'referinta', 'impact') — such words appear everywhere by coincidence and would "
    "falsely pass. A single-word characteristic is allowed ONLY if the word is a "
    "specific legal term (e.g. 'emitent').\n"
    "- keywords are extra Romanian retrieval words (single tokens are fine here)."
)

EXAMPLE_ITEM = (
    "id: publicat_monitorul_oficial\n"
    "name: Publicat în Monitorul Oficial\n"
    "description: Actul este publicat în Monitorul Oficial.\n"
    "required_characteristics: [monitorul_oficial]\n"
    "keywords: [publicat, monitorul, oficial, publicare]\n"
)


def read_requirements(path: Path) -> list[str]:
    """Requirement bullets ('- ' / '* ' lines) from the NL markdown."""
    reqs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*[-*]\s+(.*\S.*)$", line)
        if m and len(m.group(1).strip()) > 20:
            reqs.append(m.group(1).strip())
    return reqs


def _sanitize_id(raw: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (raw or "").strip().lower()).strip("_")
    return s or fallback


def strip_fences(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^```[\w-]*\s*\n(.*?)\n?```\s*$", t, re.DOTALL)
    return m.group(1).strip() if m else t


def _as_tokens(val) -> list[str]:
    if isinstance(val, str):
        val = [p for p in re.split(r"[,\n]", val)]
    return [str(x).strip() for x in (val or []) if str(x).strip()]


def draft_outcome(req: str, idx: int) -> dict | None:
    """One requirement -> one outcome dict {id,name,description,chars,keywords}.
    Up to two attempts; deterministic gate = a usable id + characteristics."""
    user = (f"Requirement:\n{req}\n\nExample of the exact shape "
            f"(do not copy its content):\n{EXAMPLE_ITEM}\nReturn only the YAML.")
    for attempt in (1, 2):
        raw = strip_fences(complete(SYSTEM_ITEM, user,
                                    temperature=0.0 if attempt == 2 else 0.1,
                                    max_tokens=400))
        try:
            d = yaml.safe_load(raw)
        except yaml.YAMLError:
            d = None
        if isinstance(d, dict):
            chars = _as_tokens(d.get("required_characteristics"))
            if chars:
                return {
                    "id": _sanitize_id(d.get("id", ""), f"req_{idx:02d}"),
                    "name": str(d.get("name") or f"Requirement {idx}").strip(),
                    "description": str(d.get("description") or req).strip(),
                    "required_characteristics": chars,
                    "keywords": _as_tokens(d.get("keywords")) or chars,
                }
        user += "\nYour previous answer was invalid. Return ONLY the YAML keys listed, nothing else."
    return None


def assemble_ontology(items: list[dict]) -> dict:
    """Build the requirement ontology from per-item drafts, deduping ids."""
    seen, outs, hints = set(), [], []
    for i, it in enumerate(items, 1):
        oid = it["id"]
        while oid in seen:
            oid = f"{it['id']}_{i}"
        seen.add(oid)
        outs.append({"id": oid, "name": it["name"], "description": it["description"],
                     "required_characteristics": it["required_characteristics"]})
        hints.append({"outcome": oid, "indicators": [{"keywords": it["keywords"]}]})
    return {"version": "req-0.1", "process_outcomes": outs, "mapping_hints": hints}


def generate_pg(onto: dict) -> dict:
    """Deterministic PG: one audit step per outcome, each referencing its
    outcome so reasoner.scoped_outcomes picks up exactly this checklist."""
    steps = [{
        "id": "select-corpus",
        "operation": "Select the corpus in scope for the compliance audit",
        "outputs": {"corpus": "list"},
        "references": ["kg:aspice.scope.current"],
    }]
    for o in onto["process_outcomes"]:
        steps.append({
            "id": f"audit-{o['id']}",
            "operation": f"Verify requirement: {o.get('name', o['id'])}",
            "inputs": {"corpus": "select-corpus.outputs.corpus"},
            "outputs": {"verdict": "object"},
            "references": [f"{OUTCOME_REF_PREFIX}{o['id']}"],
            "notes": o.get("description", ""),
        })
    steps.append({
        "id": "emit-report",
        "operation": "Emit per-requirement satisfied/gap report with provenance",
        "inputs": {v: f"audit-{o['id']}.outputs.verdict"
                   for v, o in [(f"v_{o['id']}", o) for o in onto["process_outcomes"]]},
        "outputs": {"report": "object"},
        "references": ["kg:aspice.report.template.v1"],
    })
    return {"name": "Romanian Normative-Act Compliance Audit",
            "description": "Audits the legal corpus against an authored requirement checklist.",
            "version": "0.1.0", "steps": steps}


def main() -> int:
    req_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "requirements.nl.md"
    reqs = read_requirements(req_path)
    print(f"[author_pg] {len(reqs)} requirement(s) from {req_path.name}")
    for r in reqs:
        print(f"   - {r}")
    print(f"[author_pg] drafting requirement ontology via {MODEL} @ {BASE_URL} ...")

    items = []
    for i, req in enumerate(reqs, 1):
        it = draft_outcome(req, i)
        if it is None:
            print(f"[author_pg] FAILED to draft a valid outcome for requirement {i} after 2 attempts")
            return 2
        print(f"   drafted [{it['id']}]  chars={it['required_characteristics']}")
        items.append(it)

    onto = assemble_ontology(items)
    onto_path = HERE / "requirement-ontology.yaml"
    pg_path = HERE / "audit.pg.yaml"
    onto_path.write_text(yaml.safe_dump(onto, allow_unicode=True, sort_keys=False), encoding="utf-8")
    pg_path.write_text(yaml.safe_dump(generate_pg(onto), allow_unicode=True, sort_keys=False), encoding="utf-8")

    # Prove the artifacts load through the real loaders.
    loaded = onto_mod.load_ontology_from_file(str(onto_path))
    print(f"[author_pg] OK — {len(loaded.process_outcomes)} outcomes, "
          f"{sum(len(o.required_characteristics) for o in loaded.process_outcomes)} characteristics")
    for o in loaded.process_outcomes:
        h = loaded.hint_for(o.id)
        kw = ", ".join(h.keywords) if h else ""
        print(f"   [{o.id}] {o.name}")
        print(f"        characteristics: {o.required_characteristics}")
        print(f"        keywords: {kw}")
    print(f"[author_pg] wrote {onto_path.name} and {pg_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
