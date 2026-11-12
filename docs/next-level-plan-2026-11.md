# grap — status review and next-level plan

**Date:** 2026-11-12. **Scope:** how the knowledge graph gets *built* (the extraction engine under
`mcp-server/grap_mcp/kee/`), and what would raise its efficiency and usefulness.
**Status:** proposal. No code, configuration or data was changed.

Evidence base: the September reports in `docs/`, a read of the extraction modules, and one run today
of the Python suite (`mcp-server/tests`: **576 passed**, 25 s). The Rust tests and the live-model
experiments were not re-run; their numbers below are quoted from the named reports.

---

## 1. Verdict

1. **The engine is ahead of the knowledge.** The Rust core (text + vector + graph retrieval,
   persistence, sharding, ANN) is solid after the September correctness pass and is already larger
   than the corpus needs: 2,092 documents, about 78,000 statements.
2. **The graph that exists is thin.** What is reliable is a document-level citation graph built
   without a model: 978 resolved edges, against 12,276 reference occurrences that did not resolve.
   Model-extracted claims have been run on six whole documents.
3. **Extraction discipline is better than most published systems**: every claim carries a verified
   source quote, runs are resumable, refreshes replace rather than duplicate, and failures stay
   visible. Keep all of it.
4. **Nothing measures whether the graph is right or useful.** Every quality figure rests on 12 to 23
   passages chosen and judged by the assistants that wrote the code. There is no question-answering
   or retrieval test at all.
5. **The stated goal and the work have diverged.** `what-we-would-like-to-do.md` describes
   automotive test-specification generation from three graphs. The last two months of work are
   Romanian law. The procedure graph still does not execute steps and the temporal graph is a log
   file — the September audit says so, correctly.

The repo has been idle since 2026-09-10 on `codex/engine-correctness-efficiency`, with the newest
goal and capability documents untracked.

---

## 2. Where it stands against current practice

Current published work on building graphs from text (TRACE-KG, AutoSchemaKG, Wikontic, EDC) has
converged on doing four things **together**: extract with source grounding, induce entity types,
canonicalise relations with their qualifiers, and resolve entities with alias tracking. On the cost
side the trend (LazyGraphRAG, LinearRAG) is to index everything cheaply and spend model calls only
where a question or a priority needs them.

| Stage | grap today | Gap |
|---|---|---|
| Source grounding | Verified quotes, offsets, receipts | **None — ahead** |
| Incremental update | Per-chunk replace, resumable | **None — ahead** |
| Structure before models | Statement splitter, definitions, boxed tables, citations | Provision hierarchy and amendment operations not modelled; 12,276 references unresolved |
| Entity resolution | First-fit cosine clustering at 0.86 plus a regex veto | Order-dependent; 3 of 8 known aliases missed; lowering the threshold causes false merges |
| Relation vocabulary | Free verbs: 418 distinct for 3,235 concepts | Post-hoc vocabulary discovery exists but is experimental and not applied to the graph |
| Entity types | Optional class list from an environment variable | No typing step |
| Schema | Generic path stores everything as ASPICE "outcomes" and "requirements" | The September evaluation already found this wrong for law |
| Time | Source revision kept; in-force flag stored as metadata | No act versions, no "as of" queries |
| Cost | About 2.5 s per chunk, one request at a time through Ollama | Roughly 20 hours for the generic path over the corpus; no batching server evaluated |
| Evaluation | Small assistant-authored cases | No independent gold set, no downstream task |

Three defaults also lag the project's own measurements: the default model is `llama3.1` and the
default HTTP embedder `nomic-embed-text`, while the reports select a tuned Gemma model and BGE-M3;
and with no backend configured the embedder silently falls back to a hashing stub, which makes
semantic merging meaningless without saying so.

One finding from the repo should be kept as a rule: feeding relation *patterns* back into the
extraction prompt made the model fabricate instances of them. Canonicalisation must therefore stay
**after** extraction, as a typing and validation step — which is also how EDC does it.

---

## 3. The plan

Each phase has a gate. A phase that fails its gate is recorded and dropped.

### Phase 0 — Choose the proving ground and build the yardstick *(blocks everything)*

- **Domain (decided 2026-11-12): automotive is the goal; Romanian law is test data.** Law stays as
  the public stress corpus; nothing law-only may enter the core. Automotive is the corpus the
  product is judged on.
- **Two independent gold sets.** Law: labelled by a person trained in Romanian law, working from a
  written labelling guide. Automotive: labelled by the owner (AUTOSAR expert). Each: about 150
  passages with their claims; about 200 entity pairs (same / different); about 100 questions each
  with the passages that answer them.
- **A sealed quarter of it**, scored only through a script that prints aggregates.
- **Five numbers, always with intervals:** claim precision and recall; entity-pair F1; share of
  questions answered with the correct source; GPU-seconds per thousand characters; reviewer minutes
  per accepted claim.
- **Gate:** the current system is scored on all five. That baseline is the first real result this
  project will have.

### Phase 1 — Gains that need no model

- Make defaults match measurements; fail loudly instead of falling back to the hashing embedder.
- **Resolve the references.** Give every cited-but-absent act a placeholder node so the 12,276
  unresolved occurrences become graph structure and a visible coverage list instead of a count.
- **Model the hierarchy**: act → article → paragraph → item, each addressable.
- **Adopt existing identifiers.** `CONTEXT.md` has independently reinvented the work / version /
  document distinction that the European Legislation Identifier and Akoma Ntoso standardise. Use
  their vocabulary rather than a house one. (Whether the Romanian portal publishes such identifiers
  was not confirmed and needs a short research ticket.)
- Stop skipping tabular chunks in the generic path now that tables are recovered deterministically.
- **Gate:** resolved-reference share rises; no gold-set regression.

### Phase 2 — Throughput

- Serve the model through a batching server (the untracked `scripts/serve-vllm.sh` is a start) with
  concurrent requests, schema-constrained output and a cached system prompt.
- Tier the work: deterministic labels first, a small model for kind and subject, the large model
  only for provisions the cheap stages flag.
- **Gate:** identical accepted claims on the frozen cases at a measured multiple of today's
  throughput. Target: the whole corpus overnight rather than in a day of GPU time.

### Phase 3 — Entity resolution worth trusting

- Replace threshold clustering with: cheap blocking → pairwise decision on evidence (names, numbers,
  issuer, defining passage) → transitive closure with the existing veto.
- Use the 320 explicit definitions already extracted as **scoped aliases**, which is exactly the
  three-of-eight the embedder misses.
- **Gate:** entity-pair F1 on the sealed set, with zero false merges on the distinct pairs.

### Phase 4 — A typed vocabulary

- Type every entity; map free verbs onto a small relation set, keeping conditions, exceptions and
  dates as qualifiers on the edge. Apply it to the graph, not just to a report.
- Split the legal claim model cleanly from the ASPICE outcome model in storage.
- **Gate:** distinct relation types fall by an order of magnitude with no loss of gold claims.

### Phase 5 — Time

- Act versions and validity intervals; amendment as an operation on a provision, distinct from a
  citation. Enables "what did article N say on date D".
- **Gate:** a set of dated questions from Phase 0 answered against the right version.

### Phase 6 — Answering from the graph

- Retrieval that returns provisions with their citations, applicable version and stated gaps.
- Only now test graph-walk ranking and centrality — the September audit rightly switched centrality
  off for lack of evidence, and Phase 0 supplies the evidence either way.
- **Gate:** correct-source rate beats text-plus-vector search alone. If it does not, the graph is
  not earning its cost for retrieval, and that is worth knowing.

### Phase 7 — The three-graph goal

- Build a minimal procedure runtime for **one** real procedure — generate a test specification from
  a requirement set — with supported operations, guards and per-step results. Record its events in
  the existing log.
- Only after Phases 0–4, because a procedure that reads an unvalidated graph produces confident
  nonsense with a perfect audit trail.

### Not doing

- **No more engine work** — sharding, ANN tuning, on-disk formats. Nothing in the application uses
  them and the corpus is three orders of magnitude below where they matter.
- **No more lifecycle, policy or promotion machinery** until a procedure actually executes.
- **No two-model agreement by default, no fine-tuning** before the gold set exists.
- **No external graph or vector database, no agent framework.**

---

## 4. Order and cost

| Phase | Depends on | Main cost |
|---|---|---|
| 0 | — | **Human labelling time** — the bottleneck |
| 1 | — (can start now) | Small |
| 2 | 0 for the gate | Small to medium |
| 3 | 0 | Medium |
| 4 | 3 | Medium |
| 5 | 1 | Medium |
| 6 | 0, 3, 4 | Medium |
| 7 | 4 | Large |

Recommended: **1 and 0 in parallel → 2 → 3 → 4 → 6**, with 5 slotted in when dated questions matter,
and 7 last.

## 5. Decisions (owner, 2026-11-12)

1. **Automotive is the goal; law is test data.**
2. **Gold sets:** law by a person trained in Romanian law, given a labelling guide; automotive by
   the owner.
3. **`codex/engine-correctness-efficiency` merged to `master`**; goal documents committed.
4. **Engine work is frozen.**

Consequence for ordering: automotive sources are not prose. Requirements arrive as ReqIF, Word,
PDF and spreadsheets; signals and software structure as DBC and ARXML. The reader today accepts
only Markdown, text and one JSON shape. Deterministic readers for the structured formats move into
Phase 1 — VSS-engine already has DBC and ARXML ingest to reuse — and the model is kept for prose.

## 5a. How this gets implemented

[work-packages-2026-11.md](work-packages-2026-11.md) cuts the plan into cards, each tagged by the
capability it needs. Cards tagged **[L]** need a capable model; the rest are written so a small one
can carry them. Labelling follows [gold-set-guide-law.md](gold-set-guide-law.md) and
[gold-set-guide-automotive.md](gold-set-guide-automotive.md).

## 6. Relation to VSS-engine

VSS-engine solved the part grap lacks — sealed benchmarks, calibrated confidence, explicit
abstention, gates that must be seen to fail — and lacks the part grap has: a real graph store with
provenance. Its signal-to-VSS mappings are natural graph content (signals, messages, ECUs, VSS
nodes). Storing them in grap would give the automotive goal its first validated subgraph.

## Sources

- TRACE-KG — https://www.alphaxiv.org/abs/2604.03496.md
- AutoSchemaKG (ACL 2026) — https://aclanthology.org/2026.acl-long.942.pdf
- Wikontic (AAAI 2026) — https://ojs.aaai.org/index.php/AAAI/article/view/42341/46302
- Extract, Define, Canonicalize (EMNLP 2024) — https://aclanthology.org/2024.emnlp-main.548/
- When to use graphs in RAG — https://arxiv.org/pdf/2506.05690
- RAGU — https://arxiv.org/pdf/2607.11683
- LinearRAG — https://arxiv.org/pdf/2510.10114
- ELI / Akoma Ntoso mapping — https://zenodo.org/records/10162105
- Akoma Ntoso vocabulary (OASIS) — https://docs.oasis-open.org/legaldocml/akn-core/v1.0/csd01/part1-vocabulary/akn-core-v1.0-csd01-part1-vocabulary.html
- In-repo: `architecture-value-audit-2026-09.md`, `real-law-evaluation-2026-09.md`,
  `corpus-scale-workflow-2026-09.md`, `full-kee-timing-2026-09.md`,
  `llm-extraction-improvement-2026-09.md`, `ontology-distillation-proposal.md`.
