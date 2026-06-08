# What grap can borrow from jcodemunch — architecture for the next phase

**Source studied:** `jcodemunch-mcp` (indexed locally as `jgravelle/jcodemunch-mcp`, 494 files / 8 559 symbols, mostly Python). Explored with the jcodemunch MCP itself — `get_repo_map`, `get_tectonic_map`, `search_symbols`, `get_symbol_source`. Every claim below cites a real symbol or file in that repo.

**grap context:** [`grap-capabilities-and-apis.md`](grap-capabilities-and-apis.md) (the engine + MCP surface) and [`what-we-would-like-to-do.md`](what-we-would-like-to-do.md) (the KG/PG/TG roadmap).

---

## 0. TL;DR — the borrow list

jcodemunch and grap are the **same shape**: a graph + hybrid retrieval engine with an MCP surface, both obsessed with not wasting agent context. jcodemunch is further along on *agent-facing efficiency and graph analytics*; grap is further along on *engine performance and governance/reproducibility*. So the borrow direction is clear.

| # | Pattern from jcodemunch | Fills which grap gap | Lands in | Priority |
|---|---|---|---|---|
| **A** | Token-budgeted **columnar wire encoding** (`#MUNCH`) + a savings ledger | grap_mcp returns plain JSON; no token budget | `grap_mcp` (+ a tiny `pygrap` helper) | **P0** |
| **B** | **Rank-based multi-signal fusion (WRR)** + a **graph-centrality channel** | grap fuses *linearly* (`FusionWeights`) and has **no centrality signal** | Rust core (`query`/ranking) → `pygrap` → `grap_mcp` | **P0** |
| **C** | **Content-hash everything** + drift/freshness verification | grap content-addresses *Workspace artifacts* but not KG nodes/chunks | Rust core (node hash) + KEE ingest + MCP result annotation | **P0** |
| **D** | **Generic graph analytics** (blast-radius, cycles, orphans, hotspots, a "health radar") | grap is a graph engine but exposes no analytics | Rust core (cheap with masks) → `grap_mcp` | **P1** |
| **E** | **Incremental, watched ingestion** (debounced, hash-skip) | KEE ingest is batch / full | `pygrap`/KEE + `grap_mcp` | **P1** |
| **F** | **Session journal + context-bundle assembly** | the PG "fetch relevant KG context" step has no working memory | `grap_mcp` | **P1** |
| **G** | A **concrete `Telemetry` impl**: pulse + ranking ledger | grap's `Telemetry` trait is no-op by default | wire the Rust trait + `grap_mcp` | **P2** |
| **H** | **Tool tiering** + self-documenting surface (`guide`, `suggest_queries`) | grap_mcp has 8 domains / ~40 tools, all always-on | `grap_mcp` | **P2** |
| **I** | **Transport hardening** (bearer-auth + rate-limit middleware) + **SQLite index store w/ versioned migrations** | grap_mcp is local-trust only; roadmap wants an "index table" | `grap_mcp` + the KG lookup table | **P2** |

The two P0s in **bold** are the crown jewels and the rest of the doc front-loads them.

> **Full implementation specs now exist for the three P0 items** (buildable, TDD, exact files/signatures), each favouring the Rust core:
> - [`spec-content-hash-core.md`](spec-content-hash-core.md) — **C**, entirely in the core.
> - [`spec-rrf-centrality-fusion.md`](spec-rrf-centrality-fusion.md) — **B**, entirely in the core.
> - [`spec-token-encoding.md`](spec-token-encoding.md) — **A**, a core codec primitive + thin MCP schemas.
>
> P1/P2 are specified at architecture level in [§3](#3-p1--high-value-naturally-fits-graps-graph-nature)/[§4](#4-p2--robustness--operability) below, plus an explicit core-vs-MCP allocation in **[§4.5](#45-p1p2-coremcp-allocation--concrete-hooks)**.

---

## 1. Why jcodemunch is the right codebase to mine

Run `get_tectonic_map` on it and the plates line up almost 1:1 with grap's own concerns:

- **`parser/` + `storage/`** — turn raw input into content-hashed, indexed symbols in a versioned SQLite store. grap's analogue is *chunk → node → on-disk index*.
- **`retrieval/signal_fusion.py` + `tools/search_symbols.py`** — hybrid BM25 + embedding + structural ranking. grap's analogue is `search_and_traverse_hybrid`.
- **`encoding/`** — squeeze every result into the fewest tokens. grap has **no analogue at all** — this is pure upside.
- **`tools/get_dead_code_v2.py`, `health_radar.py`, `get_blast_radius`, `get_dependency_cycles`** — graph analytics over the import graph. grap *is* a graph engine and exposes none of these.
- **`tools/session_journal.py`** — agent working memory. grap's roadmap needs exactly this for the PG "fetch context" step.

So the mining isn't speculative: jcodemunch has already built, for a code graph, most of the agent-facing layer grap's roadmap calls for on a knowledge graph — and grap's masked-traversal core can do several of these *faster* than jcodemunch's pure-Python.

---

## 2. P0 — the crown jewels

### A. Token-budgeted columnar wire encoding (`#MUNCH`)

**What jcodemunch does.** Every tool response can be re-encoded from JSON into a compact, self-describing text format before it crosses the wire to the agent. Look at `encoding/schema_driven.py::encode` and `encoding/format.py::write_table`:

1. A per-tool **schema** (`encoding_id`, e.g. `ss1` for search, `ro1` for repo outline) declares: which arrays are **tables** (a 1-char tag, the key, the column order, and *which columns to string-intern*), which keys are **scalars**, nested-dict keys, `_meta` keys, and raw **json blobs**.
2. It builds one shared **legend** across all interned columns — repeated strings (file paths above all) become `@1`, `@2`, … (interned only when used ≥2× and it actually saves chars). See `Legends.finalize(min_uses=2, min_chars_saved=1)`.
3. It emits: a header `#MUNCH/1 tool=… enc=…`, the legend lines, **one scalar line** carrying `__stypes` (type hints) and `__tables` (the schema itself, so the decoder is self-sufficient), then **tag-prefixed CSV rows**.
4. A decoder (`encoding/decoder.py::decode`, plus a TS client in `clients/ts/`) loads the schema by `encoding_id` and reverses it. Format is negotiable per call/repo: `auto | compact | json` (`encoding/__init__.py::default_format`).
5. It tracks the win: `tokens_saved` per call and a cumulative `total_tokens_saved` in `_meta`. (In this very session the cumulative counter was reading ~20.6M tokens saved.)

You have been *reading* this format throughout this exploration — every `#MUNCH/1 … enc=ro1 … @1=… t,@5grap-rust,…` block is it.

**Why grap wants it.** grap_mcp returns plain JSON. KG/PG/TG results are *exactly* the high-redundancy, path-and-id-heavy payloads this format crushes: `graph_neighbors`, `legal_graph_from_relations`, `query_events`, `list_runs`, `get_audit_log`, `reconstruct_run`. Node ids, edge property names, run ids and event types repeat on every row — prime intern targets.

**Where it lands & how.**
- A new `grap_mcp/encoding/` module, schema-per-tool, applied at the MCP boundary only (the Python objects stay rich internally). This is **additive and reversible** — keep `format=json` as the default until a decoder ships, exactly as jcodemunch did.
- Start with the three highest-volume tools (`graph_neighbors`, `query_events`, `legal_graph_from_relations`); intern `node_id`, `relation`/`event_type`, and any path columns.
- Carry `tokens_saved`/`total_tokens_saved` in `_meta` and feed the same ledger as pattern **G**.

> Net effect: the single highest-ROI, lowest-risk borrow. It changes no engine semantics and directly lowers the cost of every agent turn against grap.

---

### B. Rank-based multi-signal fusion (WRR) + a graph-centrality channel

**What jcodemunch does.** `retrieval/signal_fusion.py::fuse` is **Weighted Reciprocal Rank Fusion**, not linear score addition. Each *channel* emits a ranked id list; fusion is:

```
score(symbol) = Σ_channels  weight_channel / (k + rank_in_channel)      # k = smoothing, default 60
```

Channels built today (`build_*_channel`):
- **lexical** — BM25 over name/signature/summary (`weight 1.0`)
- **identity** — exact / prefix / segment name match
- **structural** — **PageRank of the symbol's containing file** over the import graph (`weight 0.4`) ← *grap has nothing like this*
- **similarity** — embedding cosine (only if embeddings exist; optional channel)

Weights + `k` are config-loaded (`load_fusion_weights`) and `tune_weights`-able; per-channel contributions and ranks are retained for an explainable `debug` breakdown. Because it ranks, **the channels never need comparable score scales.**

**Why grap wants it.** grap's fusion is *linear*: `FusionWeights { text, graph }` in Rust and `text_weight/vector_weight/graph_weight` in `pygrap.search_and_traverse_hybrid`. Linear addition of a BM25 score, a cosine in [0,1], and a cumulative-strength number is a scale-mismatch hazard — whichever signal has the largest raw magnitude dominates regardless of intent. RRF fixes that for free. And grap has **no centrality signal at all**, even though it owns a property-weighted edge graph that is *perfect* for PageRank (and the roadmap already wants `visit_count` edges, a second centrality-like signal).

**Where it lands & how.**
- **Rust core (`query.rs`/a new `ranking` module):** add a `rank_fuse(channels: &[RankedChannel], k, weights)` that implements the formula above. grap already produces three rankable streams inside `search_and_traverse_hybrid` (lexical seeds, vector seeds, traversal-strength order) — feed each as a channel instead of summing weighted scores.
- **New centrality channel:** compute PageRank over the live, *mask-filtered* edge graph (respect the same `outgoing_property_mask` the traversal uses, so "centrality among `cites` edges" is a first-class query). This is cheap on grap's arena adjacency and is reusable by the analytics in **D**.
- **`visit_count` as a channel:** once the roadmap's `visit_count` edge property lands, a usage-frequency channel drops straight into the same `fuse`.
- **Keep `FusionWeights` as the back-compat API**, add `FusionMode::{Linear, Rrf}` with `Rrf` default; surface `k` + per-channel weights through `pygrap` and the MCP, and expose the per-channel rank breakdown as grap's explanation payload (it dovetails with the reasoner's "sectioned, reproducible explanations").

> This is the borrow with the deepest reach: it improves *every* retrieval grap does and gives the KG a structural-importance signal it currently can't express.

---

### C. Content-hash everything + drift / freshness verification

**What jcodemunch does.**
- `parser/symbols.py::compute_content_hash` = SHA-256 of the symbol's source bytes, stored on every symbol. `make_symbol_id` gives stable content-addressed ids.
- `tools/get_symbol.py` verifies on read in two modes (`verify_against`): **`cache`** (self-referential — does the bytes still hash to the stored `content_hash`?) and **`git_sha`** — *externally attested*: `git show HEAD:<file>`, slice the symbol's line range, compare. Returns `git_sha_match | git_sha_mismatch | git_unavailable` (`_verify_against_git_sha`).
- A `FreshnessProbe` annotates every search result with `fresh | edited_uncommitted | stale_index` and sets a repo-level `repo_is_stale` flag that feeds a **confidence** score on the whole response.

**Why grap wants it.** grap content-addresses *lifecycle* artifacts (Versions, Activations, RunRecords) but **KG nodes and ingested chunks are not hashed**. That blocks three roadmap items at once:
- **Incremental re-ingest** (pattern E): "has this chunk changed since last time?" is a hash compare.
- **Provenance integrity:** the roadmap wants every node/edge linked to a source chunk. A content hash on the node *plus* a "does this still match the source document?" check is the KG equivalent of `verify_against=git_sha` — it tells an auditor whether the KG drifted from its sources.
- **Result trust:** annotate every `graph_neighbors`/reasoner hit with a freshness flag and roll a confidence number, so an agent knows when the KG is stale relative to the corpus.

**Where it lands & how.**
- **Rust core:** add `content_hash: [u8; 32]` to the node record (cheap; `add_node` computes it from the blob). Expose `verify_node(id) -> {Match, Mismatch, Unknown}` against the stored source pointer.
- **KEE ingest:** key the dedup/skip on content hash (complements the existing `ConceptRegistry` + `merge_nodes` dedup — hash handles *exact* repeats, the registry handles *semantic* ones).
- **MCP:** a `FreshnessProbe`-style annotation on read tools + a `repo_is_stale` flag derived from "corpus mtime > index time."

---

## 3. P1 — high value, naturally fits grap's graph nature

### D. Generic graph analytics over KG / PG / TG

**What jcodemunch does** (all over the import/call graph):
- `get_blast_radius` — transitive impact of changing a node.
- `get_dependency_cycles` — cycles in the graph.
- `get_dead_code_v2` — **reachability from entry points** (`_reachable_from_entry_points`) → unreached nodes.
- `get_hotspots` / `get_churn_rate` — `complexity × log(1 + churn)`.
- `get_tectonic_map` — fuse **structural + behavioral + temporal** coupling into clusters, flag **drifters** (a file whose folder ≠ its logical module) and **nexus** (god-module) plates.
- `health_radar.py::compute_radar` — normalize a handful of signals onto a 0–100 scale per axis, average to a **composite + letter grade**, gracefully **omit axes** with no data, and `diff_radar` for before/after regression detection.

**Why grap wants it.** grap is literally a node+edge engine and exposes *zero* analytics — yet the roadmap explicitly wants `visit_count`, `confidence`, `lifetime/validity` edges and "easier debugging / traceability." Those map directly:
- **Blast radius of a requirement change** = masked reverse traversal from a KG node (the doc's `predecessors_by_relation`, generalized).
- **Orphan / dead-knowledge detection** = grap's *exact* `_reachable_from_entry_points`, but "entry points" are e.g. promoted PG steps or top-level requirements; unreachable KG nodes are stale knowledge.
- **Hotspots by `visit_count`** rather than churn — the roadmap's visit-count edges *are* the churn signal.
- **PG cycle detection** — a PG with loops/branches needs cycle and unreachable-step checks before `promote_pg`; this slots into the existing validation gate.
- **A "KG health radar"** — axes like *orphan %, low-confidence-edge %, expired-edge %, unprovenanced-node %, ontology-coverage %* → composite + grade, with `diff` between two `kg_version`s. This is a governance artifact, not just a metric.

**Where it lands & how.** Implement the graph kernels in the **Rust core** (BFS/reverse-BFS with masks, Tarjan SCC for cycles, PageRank from **B**) — they're fast on arena adjacency and reuse the centrality work. Surface a thin `grap_mcp` "analytics" domain on top. Reuse `compute_radar`'s shape verbatim: per-axis normalized score, averaged composite, letter grade, omit-on-missing, `diff`.

### E. Incremental, watched ingestion

**What jcodemunch does.** `watcher.py` (`watch_folders`, `WatcherManager`) debounces filesystem events (default debounce ms) and re-indexes **only changed files**; `index_folder(incremental=True)` diffs against the stored index and content-hashes to skip unchanged work. There's even a `watch_claude_worktrees` mode.

**Why grap wants it.** KEE ingest today is batch ("chunk + add docs"). For a *living* KG (the roadmap's word), re-ingesting a 2 000-document corpus because one file changed is a non-starter. With node content hashes (**C**) in place, a watcher gives grap "edit a source doc → only its chunks/nodes refresh," plus background re-embedding of just the touched nodes.

**Where it lands & how.** A `pygrap`/KEE watcher (Python `watchdog`, debounced) that calls a new `ingest_incremental(path)` keyed on content hash; reuse grap's existing on-disk WAL + tiered-merge so the index stays append-friendly during live updates. Optional `grap_mcp` `watch_corpus(path)` tool.

### F. Session journal + context-bundle assembly

**What jcodemunch does.** `tools/session_journal.py::SessionJournal` records, per session, files accessed (read count, last tool, last ts), queries (count, result count), and edits; `get_context` / `get_session_snapshot` return that sorted by **recency or frequency**, including **negative evidence** ("searched X, found nothing"). Higher-level tools (`assemble_task_context`, `get_ranked_context`, `get_context_bundle`) pack a budgeted, query-driven context blob in one call.

**Why grap wants it.** The PG's very first real step is *"Fetch relevant KG context → context bundle"* (`what-we-would-like-to-do.md`, §4). That bundle is exactly `assemble_task_context`: a budgeted pack of the most relevant + most recently-touched KG nodes for the current task. A session journal also makes runs **more reproducible** — "what did this run actually look at?" becomes a recorded fact, which strengthens the Temporal Graph's reconstruction story rather than competing with it.

**Where it lands & how.** A `grap_mcp` session layer that records every `graph_neighbors`/`run_procedure`/`detect_gaps` touch, plus a `get_context_bundle(node_or_query, token_budget)` that runs `search_and_traverse_hybrid`, packs results under a token budget (using the byte-length packing from **A**/`_search_symbols_fusion`), and returns it in the compact encoding. Cross-link recorded touches into the audit log so they show up in `reconstruct_run`.

---

## 4. P2 — robustness & operability

### G. A concrete `Telemetry` implementation: pulse + ranking ledger
grap ships a `Telemetry` trait that is **no-op by default** (`record_traversal_nodes_visited`, `record_mask_prune`, …) — good design, but nothing implements it. jcodemunch shows the concrete impl: `storage/token_tracker.py::write_pulse` (atomic JSON pulse file, env-gated by `JCODEMUNCH_EVENT_LOG`), `record_savings`, `record_ranking_event` into SQLite, and a `cost_avoided()` translation. **Borrow:** a `GrapTelemetry` impl that writes the same kind of env-gated pulse + a ranking/savings ledger, surfaced via an MCP `get_stats` and reused by **A** (token savings) and **B** (which channel won). Keep it strictly opt-in to preserve "nothing phones home."

### H. Tool tiering + self-documenting surface
jcodemunch has `set_tool_tier` / `tier_resolver.py` to expose a *minimal* tool set by default (an agent doesn't need 40 tools to start), plus `jcodemunch_guide` and `suggest_queries` for discoverability. grap_mcp's 8 domains / ~40 tools (`list_capabilities`) are all always-on. **Borrow:** a `core | standard | full` tier so a fresh agent sees ~6 grap tools; a `grap_guide` tool; and `suggest_queries(node)` that proposes next traversals. This is straight context-economy — fewer tokens spent on tool schemas, fewer wrong-tool calls.

### I. Transport hardening + a real index table
- **Hardening:** `server.py` has `BearerAuthMiddleware` + `RateLimitMiddleware` + a project-aware config. grap_mcp has *roles* (viewer/operator/admin) but no transport auth — fine for local, necessary for the "as a team" deployment the capabilities doc advertises. Borrow the middleware pattern for the streamable-HTTP transport.
- **Index table:** the roadmap wants "an index table for fast lookup" of entities/aliases→ids. jcodemunch's `storage/sqlite_store.py` with **explicit, versioned schema migrations** (`_migrate_v13_to_v14`, …) is the proven shape for that lookup/metadata sidecar — versioned so it survives format evolution. grap can keep blobs/adjacency in its arena/on-disk index and put the entity/alias lookup table in a migrated SQLite sidecar.

---

### 4.5 P1/P2 core/MCP allocation + concrete hooks

Applying the rule **"embed in the Rust core whenever it can be; MCP only for the skin"** to the remaining points. Each row names the *core* piece (the hot, reusable, embeddable kernel) and the *skin* piece (presentation / session / transport glue).

| Pt | Core (rusty, blazing-fast) | Skin (grap_mcp / pygrap glue) |
|---|---|---|
| **D** Graph analytics | **all the kernels**: `pagerank` (built in [B](spec-rrf-centrality-fusion.md)), `blast_radius` (reverse-BFS w/ mask), `cycles` (Tarjan SCC), `orphans` (reachability), `hotspots` (visit_count×recency), `health_radar` scoring | tool wrappers, axis labels, `diff` formatting, gating `promote_pg` on cycle/reachability results |
| **E** Incremental ingest | `changed_nodes(&old_hashes)` diff using node `content_hash` ([C](spec-content-hash-core.md)); `update_node_content` already in core | the **watcher** (Python `watchdog`, debounce), chunking, re-embedding orchestration |
| **F** Session journal | — (nothing; this is per-agent session state, not engine) | **all of it**: `SessionJournal`, `get_context_bundle` (uses core search + [A](spec-token-encoding.md) budget packing), audit cross-links |
| **G** Telemetry | a concrete `Telemetry` impl (the trait already exists at `src/lib.rs:190`) writing counters; reuse [A](spec-token-encoding.md)'s `estimate_tokens_saved` | env-gated pulse file + `get_stats` MCP tool |
| **H** Tool tiering | — | **all of it**: tier resolver, `grap_guide`, `suggest_queries` (MCP-only concern) |
| **I** Transport + index table | **entity→NodeId index** as a core `HashMap<String, NodeId>` alias table persisted with the graph (the roadmap's "index table"), `resolve_entity(alias)` | bearer-auth + rate-limit middleware on the HTTP transport |

**Concrete core signatures to add (so D/E/I are unambiguous):**

```rust
// src/ranking.rs (D — reuses pagerank from spec B)
pub fn blast_radius(g: &KnowledgeGraph, from: NodeId, mask: Option<u64>, max_depth: u32) -> Vec<NodeId>;
pub fn dependency_cycles(g: &KnowledgeGraph, mask: Option<u64>) -> Vec<Vec<NodeId>>; // Tarjan SCC, len>1
pub fn orphans(g: &KnowledgeGraph, entry_points: &[NodeId], mask: Option<u64>) -> Vec<NodeId>;

// src/analytics.rs (D — composite, mirrors jcodemunch compute_radar)
pub struct HealthAxis { pub name: &'static str, pub score: f64, pub raw: f64 }
pub struct HealthRadar { pub axes: Vec<HealthAxis>, pub composite: f64, pub grade: char, pub omitted: Vec<&'static str> }
pub fn kg_health_radar(g: &IndexedKnowledgeGraph) -> HealthRadar;

// src/graph_ops.rs or src/index.rs (E — incremental ingest support)
/// NodeIds whose stored content_hash differs from (or is absent in) `prior`.
pub fn changed_nodes(&self, prior: &std::collections::HashMap<NodeId, [u8; 32]>) -> Vec<NodeId>;

// src/graph.rs (I — the entity/alias index table, persisted in serialize.rs)
pub fn register_alias(&mut self, alias: &str, node: NodeId);
pub fn resolve_entity(&self, alias: &str) -> Option<NodeId>;
```

Everything above is graph/array math over arena data — it stays in the core, gets reused by the MCP, and keeps the engine ignorant of MCP/session/transport concerns. The skin items (F, H, transport-auth) are genuinely presentation/session/network and would only *slow down* and *pollute* the core if embedded — so they stay in `grap_mcp`.

**Build order for P1/P2:** D and E depend on the P0 core work (PageRank from B, `content_hash` from C) — so land the three P0 specs first, then D (pure core, high value), then E/F, then the P2 operability items.

---

## 5. What *not* to borrow (scope discipline)

- **The multi-language tree-sitter parser** (`parser/extractor.py`, 7 000+ lines, 40+ languages). grap ingests prose/structured docs, not source ASTs. Irrelevant.
- **Code-specific resolvers** — PSR-4, tsconfig aliases, barrel-export expansion (`parser/imports.py`). These exist to resolve *import edges*; grap's edges come from extracted relations, not language semantics.
- **The IDE/CLI installer surface** (`cli/init.py` injecting CLAUDE.md / Cursor / Windsurf rules, `vscode-extension/`). Product packaging, not architecture.
- **`benchmarks/`, `munch-bench/`, `speedreview/`** — useful as *inspiration* for a grap eval harness later, but not a borrow now.
- **Don't fork the lifecycle/governance model.** grap's `Workspace` (versions → activations → runs → audit, gated `promote`) is *more* mature than anything in jcodemunch. Keep it; the borrows above feed *into* it (analytics gate `promote_pg`; the session journal enriches `reconstruct_run`), they don't replace it.

---

## 6. Suggested sequencing

1. **Phase 1 (P0, mostly additive, low risk):**
   - **A** ship `grap_mcp/encoding/` for the 3 hottest tools, `format=json` default, `tokens_saved` in `_meta`.
   - **C** add `content_hash` to the Rust node record + `verify_node`; key KEE dedup on it.
   - **B** implement `rank_fuse` (RRF) in the Rust core behind `FusionMode::Rrf`, port the lexical/vector/strength channels; **then** add the PageRank centrality channel.
2. **Phase 2 (P1, builds on Phase 1):**
   - **D** Rust graph kernels (reverse-BFS blast radius, Tarjan cycles, reuse PageRank) → analytics MCP domain → KG health radar with `diff` over `kg_version`s; wire PG cycle/reachability checks into the existing `publish_pg`/`promote_pg` validation gate.
   - **E** incremental watched ingestion (needs C's hashes).
   - **F** session journal + `get_context_bundle` (needs A's budget packing); cross-link into the audit log.
3. **Phase 3 (P2, operability):** **G** telemetry impl, **H** tool tiering + guide, **I** transport auth + SQLite entity-index sidecar.

Dependency spine: **C → E**, **B → D**, **A → F**. Start A/B/C in parallel.

---

## 7. Appendix — precise mechanisms for implementers

**RRF fusion** (`signal_fusion.py::fuse`, defaults `DEFAULT_WEIGHTS = {lexical 1.0, structural 0.4, …}`, `DEFAULT_SMOOTHING = 60`):

```
for channel in channels:
    w = channel.weight or weights[channel.name]
    for rank0, sid in enumerate(channel.ranked_ids):   # rank is 0-based here
        contribution = w / (k + rank0 + 1)              # 1-based rank in the denominator
        score[sid]   += contribution
        # retain per-channel contribution + rank for the debug/explanation payload
sort sids by score descending
```

**`#MUNCH` columnar encoding** (`schema_driven.py::encode` + `format.py::write_table`), per response:

```
#MUNCH/1 tool=<tool> enc=<schema_id>
@1=<interned string>            # legend: strings used >=2x and saving >=1 char
@2=<interned string>
<scalars> __stypes=k:type|...  __tables=tag:key:col|col|...   # self-describing; decode needs no out-of-band schema
<tag>,<col1>,<col2>,...         # tag-prefixed CSV, one block per declared table
```
Decoder (`encoding/decoder.py::decode`) reads `enc=<schema_id>`, loads that schema, and reverses legend + tables back to JSON. Negotiated per call via `format = auto | compact | json`.

**Content-hash drift check** (`get_symbol.py::_verify_against_git_sha`): `git show HEAD:<file>` → slice `lines[line-1:end_line]` → compare (rstripped) to the cached source → `git_sha_match | git_sha_mismatch | git_unavailable`. grap analogue: compare a node's stored `content_hash` against a re-hash of its current source chunk.

*All file paths above are in the `jcodemunch-mcp` repo (`E:\projects\jcodemunch-mcp`), indexed as `jgravelle/jcodemunch-mcp` — query them directly with the jcodemunch MCP to read the full implementations.*
