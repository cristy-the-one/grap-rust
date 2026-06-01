# End-to-End Demo + Architecture Doc — Design Spec

> Two deliverables in one branch (`e2e-demo-and-architecture-doc`): a real
> MCP-client end-to-end demo, and an architecture doc written to feed infographic
> generation. Implement via `superpowers:writing-plans`. Pure docs/script — no
> Rust/pygrap rebuild.

## Decisions (approved during brainstorming)

1. **Demo drives the real MCP server over stdio** (via the `mcp` SDK client),
   narrating a **full vertical tour** of the capability surface.
2. **Architecture doc is structured + visual** — three-graph model, layered
   grap↔pygrap↔MCP architecture, Mermaid diagrams, and an explicit "Suggested
   infographic layout" section.

## Context

- MCP server: `mcp-server/grap_mcp/server.py` runs `mcp.run()` (stdio) on
  `__main__`; importable as `grap_mcp` when `mcp-server/` is on `PYTHONPATH`.
- Capabilities (29 tools across 7 domains): knowledge, pg_lifecycle,
  execution_reasoning, experimentation, governance, validation, audit, auth.
- Example data: `docs/examples/minimal-aspice-ontology.yaml`,
  `docs/examples/simple-aspice-gap-check.pg.yaml`.
- pygrap exposes `IndexedKnowledgeGraph` (build/save a KG) and the lifecycle
  `Workspace`. `mcp` SDK (`>=1.0`) is a dependency.
- Auth: default role from env `GRAP_ROLE` (operator); `set_role` switches at runtime.

---

## Part 1 — Demo (`mcp-server/demo_aspice_e2e.py`)

Async Python script. Sections:

### 1.1 Setup (direct, before the MCP session)
- `workspace = <tempdir>/ws`.
- Build a small KG with `pygrap.IndexedKnowledgeGraph.with_merge_policy(<tmp>/kg, 2)`,
  add a couple of requirement-like nodes, `save_to_dir`.
- Write a PG to `<tmp>/demo.pg.yaml` referencing
  `kg:aspice.process_outcome.SYS.5.BP3`.
- Ontology path = `docs/examples/minimal-aspice-ontology.yaml` (resolved relative to
  the repo root from the script location).

### 1.2 MCP client session
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command=sys.executable, args=["-m", "grap_mcp.server"],
    cwd=<mcp-server dir>,
    env={**os.environ, "GRAP_WORKSPACE": workspace, "GRAP_ROLE": "admin",
         "PYTHONPATH": <mcp-server dir>},
)
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        ...
```
- `async def call(session, name, **args) -> dict`: `res = await session.call_tool(name, args)`;
  unwrap the payload — prefer `res.structuredContent` when present, else
  `json.loads(res.content[0].text)`; return the dict.
- A `narrate(title, explanation)` helper prints a section header + a one-line note;
  `show(result, *keys)` prints selected fields.

### 1.3 Narrated tour (full vertical)
In order, each with a one-line explanation of what it demonstrates:
1. `list_capabilities` — the 7 domains.
2. `load_knowledge_graph(path=<kg>)` — KG (truth).
3. `load_ontology(path=<ontology>)` — ASPICE ontology projected + versioned.
4. `load_procedure_graph(path=<pg>)` — PG (process) parsed + projected.
5. `validate_procedure_graph(path=<pg>)` — publish-time validation.
6. `publish_pg(level="draft")` — content-addressed version + validation report.
7. `promote_pg(version_hash, rationale=...)` — gated promotion.
8. `set_weight("lexical_threshold", 0.4, rationale=...)` then
   `activate(activation_id, version_hash, set_as_production=True)` — frozen
   governed weights surfaced.
9. `detect_gaps()` — hybrid reasoning (production run) → `get_explanation(target)`
   for the first gap (sectioned explanation).
10. `run_experiment(hypothesis=...)` + `get_quality_metrics()` — quarantine
    (experiment excluded from production view).
11. `adopt_experiment(run_id, activation_id, rationale)` — gated transition.
12. `list_runs()`, `reconstruct_run(run_id)`, `query_events(run_id=...)` — Temporal
    Graph + reproducibility.
13. `enforce_retention()` — admin retention sweep.
14. Auth: `set_role("viewer")` → `publish_pg()` returns an error (denied) → print the
    denial → `set_role("admin")`.
- Final summary: print counts (`list_versions`, `list_runs`, `get_audit_log` size).
- Wrap the body in try/finally that prints `✓ demo complete`; non-zero exit on any
  unexpected `status == "error"` (except the intentional viewer denial).

### 1.4 Header docstring
Top-of-file docstring: what it shows, how to run
(`cd mcp-server && python demo_aspice_e2e.py`), and prerequisites (installed pygrap
+ `mcp` SDK).

---

## Part 2 — Architecture doc (`docs/architecture-grap-mcp.md`)

Markdown, structured for infographic generation:

1. **Title + one-line thesis.**
2. **The three graphs** — table: KG (truth / "what we know"), PG (process / "how"),
   TG (history / "what happened, when, why").
3. **Layered architecture** — prose + a Mermaid diagram:
   `grap (Rust engine)` → `pygrap (PyO3)` → `grap_mcp (Python: ontology, reasoner,
   validation, lifecycle wiring)` → `MCP capability surface (7 domains)` →
   `agents/humans`. Note what each layer owns.
4. **How KG / PG / TG map onto grap:**
   - KG: grap nodes/edges + BM25/graph search; ontology projected as labelled
     outcome/characteristic nodes + `requires` edges.
   - PG: human YAML → projected into grap (data-flow + reference edges) →
     content-addressed `Version`s → `Activation`s freezing PG + weights + KG/ontology
     version.
   - TG: append-only audit log as a **relational event graph** (typed `links`) +
     `RunRecord`s + immutable explanation records; enables `events_for_run`,
     `events_linked_to`, `reconstruct`.
   - A Mermaid flow: load → validate → publish → promote → activate → run →
     detect_gaps → explanation → audit/Temporal Graph → reconstruct, with the TG
     receiving events throughout.
5. **Governance & reproducibility:** publish gating, frozen/governed weights, roles
   (viewer/operator/admin), retention enforcement.
6. **Capability surface:** the 7 domains + representative tools (link to
   `mcp-server/CAPABILITIES.md`).
7. **Suggested infographic layout:** panel-by-panel guidance for a generator —
   hero (thesis), three-graph triptych, layer stack, lifecycle flow ribbon,
   governance callouts — with icon/color/arrow hints and key numbers (29
   capabilities, 7 domains, 3 graphs).

---

## Testing / Verification

- **Demo:** run `cd mcp-server && python demo_aspice_e2e.py`; it must complete with
  `✓ demo complete`, show a non-empty gap + explanation, an experiment excluded from
  `production_runs`, a successful `adopt_experiment`, a Temporal-Graph event list for
  the run, a retention sweep, and the intentional viewer denial. Exit code 0.
- **Architecture doc:** reviewed for accuracy against merged code; Mermaid blocks are
  syntactically well-formed. No automated test.

## Out of Scope

Rendering the infographic; a packaged console-script entry for the demo; changing any
server behavior.

## Acceptance Criteria

- The demo drives the real MCP server over stdio through the full vertical and runs
  to completion, narrating each subsystem (lifecycle, reasoning, experimentation,
  Temporal Graph, governance, auth).
- The architecture doc explains how grap + pygrap + grap_mcp + MCP implement KG/PG/TG,
  includes Mermaid diagrams, and ends with an explicit infographic-layout section
  usable to generate an infographic.
