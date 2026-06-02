# Make the ASPICE GUI Useful — Design Spec

> Branch `feature/gui-useful` (worktree `.worktrees/gui-useful`), based on
> `feature/aspice-gui`. Implement via `superpowers:writing-plans`.
> Phased: A (MCP ingest capability) → B (GUI backend: working bootstrap + security +
> fixes) → C (GUI frontend: real structured views).

## Goal

Turn the ASPICE console from a JSON-dumping demo into a useful tool: ingest project
knowledge into the KG, run gap detection against *real* content, and present gaps /
explanations / runs / quality as structured views — plus fix the issues from the
prior review.

## Decisions (approved during brainstorming)

1. **Ingestion is a real MCP capability** (`create_knowledge_graph`, `ingest_documents`)
   in `grap_mcp`, so the demo, MCP surface, and GUI all gain it.
2. **Rich structured views** in the GUI (vanilla JS, no framework).
3. **Isolated worktree** off `feature/aspice-gui`; merge strategy decided at finish.

## Context (current state in this worktree)

- Branch tip `a9b1e91`: the agent's `wizard_bootstrap` now loads an **empty** KG
  (`load_knowledge_graph` on a fresh path) before ontology/PG — so it no longer
  errors, but the KG has no content, so `detect_gaps` flags *every* characteristic
  (hollow). This spec replaces that with real ingestion.
- `grap_mcp` surface is current (all lifecycle/reasoner/governance tools). The
  reasoner (`reasoner.py`) does lexical BM25 search over KG nodes, excluding
  system-projected nodes by id. Ingested document nodes are real evidence.
- `bridge.py` calls `grap_mcp.server` tool functions in-process; `main.py` exposes
  REST endpoints; `frontend/{index.html,app.js,styles.css}` is a single-output SPA.
- Baseline: `cd mcp-server && python -m pytest` → 46 passed.

---

## Part A — MCP ingest capability (`mcp-server/grap_mcp`)

### A1. `grap_mcp/ingest.py` (new)
```python
def chunk_text(text: str, max_chars: int = 800) -> list[str]:
    """Split text into chunks on blank lines / markdown headings, capped at max_chars."""

def ingest_paths(kg, root: str) -> dict:
    """Read .md/.txt under `root` (file or dir), chunk, add_node(text, label=f"doc:{name}#{i}").
    Returns {"files": int, "nodes_added": int, "labels": [...] (sample)}."""
```
- `chunk_text`: split on blank-line boundaries; further split any chunk longer than
  `max_chars` at the last whitespace; drop empty/whitespace-only chunks.
- `ingest_paths`: if `root` is a file, ingest it; if a dir, walk for `*.md`/`*.txt`
  (sorted, recursive). Each chunk → `kg.add_node(text=chunk, label=f"doc:{stem}#{idx}")`.
  Never raises on a bad single file — skip + count. Pure (no embeddings).

### A2. Tools in `server.py` (operator-gated, audited)
- `create_knowledge_graph(path)`: `pygrap.IndexedKnowledgeGraph.with_merge_policy(path, 2)`,
  `save_to_dir(path)`, set `_state["kg"]`, reset `_state["projected_nodes"]` (fresh KG),
  emit `kg_created` audit event, return `{status, path, node_count}`.
- `ingest_documents(path)`: requires a loaded KG; `ingest_paths(kg, path)`;
  `kg.save_to_dir(<kg path tracked in state>)` (track the KG path in `_state["kg_path"]`
  on create/load); emit `documents_ingested` event (details: files, nodes_added);
  return `{status, **ingest_result}`.
  - Track `_state["kg_path"]` in `create_knowledge_graph` and `load_knowledge_graph`
    so `ingest_documents` knows where to persist.
- Update `list_capabilities` `knowledge` domain →
  `["load_knowledge_graph", "create_knowledge_graph", "ingest_documents", "load_ontology"]`.
- Update `mcp-server/CAPABILITIES.md` + `docs/architecture-grap-mcp.md` counts
  (knowledge gains 2 tools → 33 total).

### A3. Tests (`mcp-server/tests/test_ingest.py`)
- `chunk_text` splits + caps; empty/whitespace dropped.
- `ingest_paths` on a temp dir of 2 `.md` files → expected `nodes_added`/`files`
  (FakeKG capturing add_node).
- Server-level: `create_knowledge_graph` → `ingest_documents(<covering docs>)` →
  `load_ontology` → `load_procedure_graph(<refs SYS.5.BP3>)` → `detect_gaps`:
  ingesting docs that cover a characteristic yields **fewer** gaps than an empty KG
  (proves ingestion feeds the reasoner). Uses real pygrap + a tmp workspace.
- Capabilities drift test passes with the 2 new tools.

---

## Part B — GUI backend (`aspice-gui/backend`)

### B1. Working bootstrap with real ingestion
- New `aspice-gui/sample-knowledge/` with 2–3 ASPICE-flavored requirement `.md`
  files. Content deliberately **covers some characteristics and omits others** so
  `detect_gaps` returns a meaningful mix (not all-gaps, not zero-gaps). E.g. one doc
  describes verification methods + acceptance criteria (covers SYS.5.BP3 partly),
  another describes software unit interfaces (covers SWE.1.BP4 partly), leaving real
  gaps (e.g. test environment, quality-management).
- `wizard_bootstrap` flow: `create_knowledge_graph(<ws>/kg)` →
  `ingest_documents(<sample-knowledge>)` → `load_ontology` → `load_procedure_graph` →
  return the per-step results + a `detect_gaps` preview. Result: a working POC that
  detects *real* gaps.

### B2. Security + correctness fixes
- Remove the wildcard `CORSMiddleware` (SPA is same-origin; this closes the
  CSRF/DNS-rebinding hole). Keep `127.0.0.1` bind.
- Replace deprecated `@app.on_event("startup")` with a `lifespan` async context.
- `bridge.ensure_workspace()` resets the full reasoning state
  (`kg, kg_path, ontology, ontology_version, pg, current_logic_id, pg_version,
  projected_nodes, explanations`) — not just `ws` — so switching workspace starts clean.
- New endpoints: `POST /api/kg/create` (PathBody) → `create_knowledge_graph`;
  `POST /api/ingest` (PathBody) → `ingest_documents`.
- `default_examples()` adds `"sample_knowledge": <abs path to aspice-gui/sample-knowledge>`.

### B3. Backend tests (`aspice-gui/tests/test_app.py`)
- FastAPI `TestClient`: `/api/health` ok; `/api/wizard/bootstrap` returns ok with a
  populated KG (node_count > 0) and a gaps preview with `gaps_found >= 1`.
  (Requires fastapi + httpx in the env.)

---

## Part C — GUI frontend (`aspice-gui/frontend`, vanilla JS)

Replace the single `#output` JSON box with panels + a persistent header. Keep the
existing nav/panel pattern and `api()/post()` helpers; add small render helpers.

- **Header (persistent):** role `<select>` (viewer/operator/admin); a status strip
  showing loaded KG (+node count), ontology, PG, and current production activation —
  refreshed after each mutating action via `/api/status` + `/api/production`.
- **Knowledge panel:** "Create KG" (path), "Ingest documents" (path, prefilled to
  sample-knowledge), and a "Bootstrap demo" button; shows node count + ingest result.
- **Gaps panel:** "Detect gaps" / "Run experiment" → render each gap as a **card**
  (outcome id + name, missing characteristic, a confidence bar); clicking a card
  fetches `/api/gaps/explain` and expands the sectioned explanation
  (structured / statistical / rendered).
- **Lifecycle panel:** publish → promote → activate, threading the returned
  `version_hash` between steps (auto-filled); shows validation result + production state.
- **Runs / Temporal panel:** list runs (production | experiment toggle) → click a run
  → reconstruct report + `query_events` timeline (event type · subject · links).
- **Quality panel:** production-only metrics + `experiments_excluded` (quarantine).
- **Audit feed:** recent events. **Raw drawer:** collapsible last-response JSON for
  debugging.
- `styles.css`: add card / badge / bar / timeline styles consistent with the current
  theme.

---

## Testing Strategy

- Python (pytest, from `mcp-server/`): `test_ingest.py` (chunk/ingest/server-level
  fewer-gaps-after-ingest) + existing suite stays green + capabilities drift updated.
- GUI backend (pytest, from `aspice-gui/`): `TestClient` health + bootstrap-produces-
  gaps.
- Manual: launch the GUI on a **free port** with its **own** `GRAP_WORKSPACE` (so it
  never touches the live agent's instance): bootstrap → see real gaps with
  explanations → publish/promote/activate → runs/quality/audit views.

## Build / Run Notes

- No Rust changes (pure Python + frontend); no pygrap rebuild.
- Launch for manual check (own port + workspace):
  `cd aspice-gui && GRAP_WORKSPACE=<tmp> python -m uvicorn backend.main:app --host 127.0.0.1 --port 8770`.

## Out of Scope

Embeddings/vector ingestion; auth identity backend; multi-user; packaging/installer;
changing the Rust engine.

## Acceptance Criteria

- The MCP surface can **create a KG and ingest documents**; ingested content is real
  evidence the reasoner searches (covering docs reduce gaps), proven by a test.
- The GUI's one-click bootstrap produces a **populated** KG and detects a *meaningful*
  mix of gaps (not all, not none), with sectioned explanations viewable per gap.
- Gaps, runs/Temporal Graph, quality, and audit are shown as **structured views**, not
  raw JSON (a raw drawer remains for debugging).
- Wildcard CORS removed; `on_event` replaced; workspace switch resets state.
- Existing Python tests stay green; new ingest + GUI tests pass.
