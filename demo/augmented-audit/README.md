# Augmented Compliance Audit — a task only possible with the MCP

A self-contained showcase: a **local LLM** (Bonsai-27B) audits a corpus of
Romanian normative acts against an **authored requirement checklist**, and
reports which requirements are satisfied and which are **provably missing** —
each verdict backed by the exact corpus evidence.

The point is the delta between the same model with and without the grap MCP:

| | Bare LLM | LLM + naive RAG | **LLM + grap MCP** |
|---|---|---|---|
| Has the 49 acts | no | top-k snippets | whole graph |
| Can prove a requirement is **missing** | no — guesses/hallucinates | no — retrieval always returns *something* | **yes — reports the gap with the queries that came up empty** |
| Evidence per verdict | none | unranked snippets | **specific act + snippet + coverage score** |
| Reproducible | no | no | **yes — tied to KG version + frozen weights** |

Observed live: asked whether the 1998–1999 acts satisfy a data-protection
impact-assessment requirement, the **bare** model invented a governing law
("Legea 159/2002…") and demurred; the **augmented** model called the audit
tool and correctly returned `GAP — no evidence in corpus`.

## Pieces

| File | Role |
|---|---|
| `author_pg.py` | **PG authoring tool.** Plain-language requirements (`requirements.nl.md`) → local LLM drafts a requirement ontology (one crisp call per requirement) → deterministic gate → generates the procedure graph. Writes `requirement-ontology.yaml` + `audit.pg.yaml`. |
| `audit.py` | **Interaction surface.** Runs the authored PG/ontology against the KG via the reasoner; returns per-requirement satisfied/gap **with the corpus evidence** that grounds each verdict. |
| `agent_demo.py` | **The showcase.** Same question, bare LLM vs MCP-augmented LLM, side by side. |
| `llm_client.py` | Shared OpenAI-compatible client for the local LLM (env-configured). |

`author_pg.py` is **draft-then-curate**: the LLM does the semantic lift, but
its characteristics are semantically noisy (it may over-specify or invent
tokens), so review `requirement-ontology.yaml` before auditing — the same
"engine proposes, human curates" philosophy as KEE.

## Run it

Prereqs (already set up on this machine):

- Bonsai-27B on llama-server at `:8080`, launched with `--jinja` (native tool
  calls) and pinned to the RTX 5080 (`--device Vulkan1`).
- Ollama at `:11434` serving `bge-m3` for embeddings.
- The pilot KG at `../../pilot-law-ro/kg`.

```bash
# 1. author the checklist from natural language (writes the two YAML files)
python author_pg.py

# 2. see the grounded per-requirement verdicts + evidence
python audit.py

# 3. the bare-vs-augmented showcase
python agent_demo.py
```

Backend is chosen entirely by environment (defaults target the setup above):

    GRAP_LLM_BASE_URL   default http://127.0.0.1:8080/v1
    GRAP_LLM_MODEL      default ternary-bonsai-27b
    GRAP_EMBED_BASE_URL default http://localhost:11434/v1  (bge-m3)

## Known rough edges

- **Single-token characteristics false-pass.** A requirement whose evidence is
  a single common word (`data`, `publică`) passes by coincidence because the
  word appears *somewhere*. Prefer distinctive compound characteristics
  (`monitorul_oficial`, `evaluare_impact_protectie_datelor`) that must
  co-occur. The authoring prompt now steers toward compounds; still verify.
- **Requirement (5) consultare publică** currently reads PASS; public-
  consultation duties postdate this corpus (Legea 52/2003), so this is likely a
  spurious pass worth tightening into a second clean gap.
