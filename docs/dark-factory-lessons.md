# The Dark Factory Bring-Up: Lessons in the Seams

> **Status:** living document. Started 2026-07-23, during the law-ro pilot
> scale-up (49 → 200 → 300 → 400 docs) and the incremental-adoption loop.
> **How to add:** append to the relevant theme, keep the narrative voice, and
> prefer "here is the incident → here is the transferable principle → here is
> the name the wider field already gave it." Date new entries. Correct old ones
> in place and note the correction — this document is itself append-mostly, not
> append-only.

---

## Why this document exists

A "dark factory" is a plant that runs lights-out: no humans on the floor, only
an exception queue someone glances at. The KEE dark factory tries to do that for
knowledge — ingest documents, extract an ontology, adopt it under policy, detect
gaps — with a local LLM and a Rust graph core, unattended.

Bringing it up was not mostly an AI problem. The model behaved; the Rust core
was fast and correct, as promised. Almost every real difficulty lived in the
**seams**: I/O, configuration, process lifecycle, control loops, and — again
and again — in *confident explanations that turned out to be wrong*. This
document is the memory of that, organized so the lessons transfer rather than
just accumulate.

## The one-line thesis

> **A knowledge factory is a distributed pipeline, and it fails like one.**
> Retries, timeouts, idempotency, checkpointing, canaries, backpressure,
> observability, the fallacies of distributed computing — the whole classical
> catalogue showed up, unbidden, in a project nominally about LLMs and graphs.
> The scarce skill was not ML. It was systems thinking and the discipline to
> *measure before believing*.

---

## I. Measure, don't infer — the misdiagnosis thread

This is the spine of the whole story, so it comes first. Over the bring-up, at
least four "known facts" were confidently asserted and later demolished by a
single measurement.

- **"Scoring is journaling-bound; move state off synced storage."** This was
  recorded as *finding 16*: ~2s per characteristic, near-zero CPU, therefore
  disk/sync latency. We even moved the repo off OneDrive partly on its strength.
  The real cause, found by profiling, was **single-text embedding round-trips**
  to Ollama (~2.2s of pure per-request overhead). The Rust search was <1ms; a
  full 1,671-outcome scoring pass was ~6 seconds. Storage was never involved.
- **"The run is stalled — kill it."** A long run showed a quiet log and an idle
  GPU on a spot check, so it looked hung. It was not: extraction *successes log
  nothing*, and the GPU is idle *between* chunks. Killing it discarded ~6 hours
  of in-memory work.
- **"The model silently fell back to the Intel iGPU."** `nvidia-smi
  --query-gpu=memory.used` reported `0 MiB`, which looks exactly like an iGPU
  fallback. The full `nvidia-smi` process table showed the model resident on the
  RTX the entire time. The *instrument* was unreliable for Vulkan-under-WDDM, not
  the system.
- **"Adoption has an anomaly — it says adopted but the ontology didn't grow."**
  Not an anomaly: the audit trail showed each batch adopted and then the
  **canary auto-rolled it back**. The control was working; our reading of it was
  incomplete.

**The principle.** An unfalsified hypothesis, stated with confidence, becomes
institutional knowledge and then steers real decisions (a whole repo move).
Plausibility is not evidence. Every one of these was resolved in minutes by one
targeted measurement — a profiler, a delta over a window, the *other* column in
the tool, the event log.

**The parallels the field already named.**
- *Premature optimization* (Knuth) — but the sharper edge here is
  **premature diagnosis**: optimizing, or re-architecting, against a guessed
  bottleneck. `finding 16` cost a storage migration that changed nothing.
- The **scientific method / hypothesis-driven debugging**: a bug is a
  falsifiable claim; if you can't state the experiment that would disprove your
  theory, you don't have a theory, you have a hunch.
- **Confirmation bias & the unreliable gauge**: a scary metric (`0 MiB`) plus a
  prior fear (iGPU fallback) manufactured a false conclusion. *Who watches the
  watchmen* — instrument your instruments.

**What it changes going forward.** Progress is judged over a *window* (a delta,
a rate), never a snapshot. Silence is not evidence of failure when success is
silent — so we now watch `entities.db` mtime and completed-task counts, not
vibes. And any "well-known" performance fact in this project is suspect until it
has a number attached to it and a date.

---

## II. The process is not the state — durability

For most of the bring-up, extraction was **all-or-nothing**: the working graph
(and the manifest that makes a re-run resumable) was written only by a single
`save()` at the very end. A 200-doc run therefore held ~18 hours of work in RAM,
and any interruption — a kill, a hang, a client restart — vaporized it. We
learned this the hard way twice before fixing it, and then the fix immediately
paid for itself when an unrelated hang forced a kill and the checkpoint survived.

**The principle.** Long-running stateful work must make progress *durable*
incrementally, and must be able to *resume* rather than restart. The unit of
value is the committed state, not the running process; treat the process as
disposable.

**The enabler was itself a lesson.** Checkpointing was "impossible" at first
because `save()` re-embedded every concept one at a time (~62 minutes at 1,671
concepts). Batching that embed made `save()` cost **3.7s** — a ~1000x change
that turned a checkpoint from prohibitive to ~0.25% overhead. *A durability
feature was gated on a performance bug three layers away.* You cannot design the
reliability story independently of the cost story.

**The parallels.**
- **Crash-only software** (Candea & Fox): if the only way to stop is to crash,
  then crashing must be safe and recovery must be the normal path. We made the
  kill a non-event.
- **Write-ahead logging / checkpointing**: the manifest is a WAL — record what's
  durably done so replay skips it. It was *already there*; it just wasn't
  flushed until the end. The fix was to `save()` at window boundaries, not to
  invent a mechanism.
- **Idempotent, resumable jobs / reconciliation loops** (the Kubernetes-style
  "converge toward desired state"): the resumed loop asks "what's still missing
  to reach 400 docs?" rather than "run these 100 docs," so a restart is safe.
- **The actor model's "let it crash"**: supervised restart is only a virtue if
  state is externalized. Ours wasn't, until it was.

---

## III. The failure economics of retries and timeouts

We added retry-with-backoff to the LLM client to stop transient 5xx/timeouts
from dropping chunks. It was correct — and it made a later run *worse*, because
of a second change made in isolation: a `max_tokens=2048` cap. At ~5.8 tok/s a
capped generation needs ~353s, but the client timeout was 300s. So every long
generation **timed out mid-stream**, and the retry logic dutifully re-issued it
three times — three guaranteed 300s failures, ~15 minutes burned per chunk, and
the chunk lost anyway.

**The principle.** A retry only helps if the underlying operation *can* succeed
on retry. Retrying a structurally-doomed operation is not resilience; it's
amplification. And **timeouts are not independent knobs** — a timeout is a
budget, and it must dominate the worst-case work it bounds (`timeout >
max_tokens / throughput`, with margin).

**The parallels.**
- **Retry storms / retry amplification** and the **thundering herd**: retries
  multiply load precisely when the system is least able to absorb it. The fix in
  the wider field is bounded retries + backoff + a **circuit breaker** that
  stops retrying a call that keeps failing the same way. We bounded attempts;
  the deeper fix was removing the doom (raise the timeout so the work fits).
- **Coupling & invariants / "action at a distance"**: adding `max_tokens` (a
  local, reasonable change) silently violated a global invariant (the timeout
  budget). The parameters *looked* independent and were not. The remedy is to
  reason about parameter *interactions*, and ideally to encode the invariant
  (assert `timeout > max_tokens/throughput`) so the coupling can't be forgotten.
- **Little's Law, obliquely**: doomed-but-retried requests occupy slots for
  their full timeout, collapsing effective concurrency. Wasted occupancy is
  wasted throughput.

**Forward note.** Config that has arithmetic relationships (`timeout`,
`max_tokens`, throughput; `-c` context ÷ `-np` slots; `keep_alive` vs the
inter-request gap) should carry that arithmetic *in the code or the launch
script*, with a comment stating the inequality. Several launch flags in
`scripts/serve-bonsai.ps1` now do exactly this.

### The same lesson, a second time: the retry that outlived the run (2026-07-25)

The 750-doc run finished 5/5 autonomous and its log carried one warning in every
batch: `no JSON parsed for 20300#2`. The same chunk. Checking every run log ever
produced, it had failed in **all of them** — 14×, 14×, 11×, 7×, 5×, 5×. So had a
stable set of about eighteen others, most of which eventually parsed.

The cause is a single line of deliberate design: a chunk that fails extraction
does not get its content hash recorded, *so the next run tries it again*. That is
exactly right for a transient failure and unbounded for a deterministic one.
There was no attempt counter, so "retry next run" meant "retry every batch, for
as long as the corpus exists" — the retry outlived the run that created it. Cost:
one wasted generation per batch, permanently, growing with nothing.

This is a **poison message** on a work queue, and the standard answer is the one
we had already written down in this very theme and not applied here: bound the
attempts, then **dead-letter** it. Three deterministic failures now quarantine
the chunk; the document is untouched and the entry is reported, so it is a
decision to stop paying, not a silent drop.

Two details matter more than the fix:

- **Which failures may quarantine.** Transport errors are exempt. If they
  counted, a server outage — the one failure mode guaranteed to hit *every*
  chunk at once — would dead-letter the entire corpus in three batches. A
  circuit breaker that trips on the network is a system that amputates itself
  during a brownout. There is a test pinning this.
- **The failure was contaminating its own instrument.** Three unrelated events
  (transport error, unparseable reply, valid-JSON-but-empty) all returned `None`
  and were all counted as "parse failures". That is why `parse_failure_rate` read
  exactly **0.091 in all five batches**: 2 of 22, being the same stuck chunk plus
  one legitimately empty one, every time. A number that never moves is not a
  measurement of the documents; it is a constant wearing a metric's name. Theme
  VIII again, from the other end — and the giveaway was the *stability*, the same
  tell as the 830 ± 12 canary floor.

**And a third defect the fix's own test exposed:** the working graph reloaded its
state only `if st.concepts` — so a run in which *every* chunk failed dropped its
manifest and re-did all of that work next time. The manifest is the record of
what was **attempted**, not of what succeeded. Conditioning recovery on the
presence of output means the runs that most need to remember what they tried are
precisely the ones that forget.

**Measured on the next run (800 docs, five batches).** The lumped rate that had
been pinned at 0.091 went **0.136 → 0.087 → 0.091 → 0.000 → 0.050**. Batch 4 is
the first batch in the project's history with *no* failed chunk — and under the
old code that fact was unobservable, because the constant was hiding it. The
dead-letter queue quarantined exactly two chunks, both at batch 3 (three attempts
each), and left a third alone at one attempt, still eligible. The metric now
tracks the documents instead of tracking one stuck chunk.

The confirmation worth keeping is the second quarantine, `18897#8`, reason
`empty`. It had been failing on every batch for as long as `20300#2` had — and it
had **never printed a warning**, because only the unparseable branch logged. It
was visible solely as the gap between "one warning in the log" and
"parse_failures: 2" in the telemetry. The stuck chunk we could see cost the same
as the stuck chunk we could not; the only reason the invisible one got fixed is
that the visible one led us to the code path they shared. **A defect with no log
line is not a smaller defect — it is the same defect with the evidence removed.**

---

## IV. Chatty vs chunky — the cost lives at the interface

The single most impactful performance fix was embarrassingly classic. Every
caller in the codebase embedded texts in **batches** — except one, on the
hottest path (concept resolution during extraction), which sent **one text per
HTTP request**. Against a local server the per-request overhead dwarfs the work:
50 single calls took 111s (2,222ms each); the same 50 batched took 2.67s (53ms
each). A **42x** difference, all fixed cost, all avoidable.

**The principle.** When an operation has a large fixed per-call cost (a network
round-trip, a syscall, a transaction), the interface's *granularity* dominates
its economics. Batch across the natural unit of work, not below it.

**The parallels.**
- This is the **N+1 query problem** verbatim — the ORM antipattern where a loop
  issues one query per item instead of one query for the set. It showed up here
  as N+1 *embeddings*. The same fix (fetch/compute in a batch) applied.
- **Chatty vs chunky interfaces** (Fowler's *first law of distributed objects*:
  "don't distribute your objects"): each fine-grained call across a boundary
  pays the boundary's tax.
- **Latency vs throughput**, and **amortization**: batching trades a little
  latency-per-item for a large throughput win because the fixed cost is spread.
  Ingest had the same bug at file granularity (batched *within* a file, not
  *across* files) — ~200 round-trips for a 200-doc corpus, fixed by batching in
  bounded groups that also cap peak memory.

**Forward note.** The tell was *consistency*: one call site diverged from the
established pattern of every other. In a codebase, the outlier is where the bug
hides. A lint/review habit of "why does this one place do it differently?" would
have caught all three batching sites for free.

---

## V. The environment is part of the system

A cluster of incidents share a root: we treated the *environment* — filesystem,
network, GPU, process lifetime — as a neutral backdrop, and it was an active
participant with surprising semantics.

- **OneDrive forked the state.** The pilot workspace, under a sync-conflict-prone
  synced folder, quietly split into 17 conflict copies (`ws(1)`, `kg(1)`, …)
  while the *unsuffixed* names became empty husks. A stateful, write-heavy
  workload on eventually-consistent sync storage produced **split-brain**.
- **The venv wasn't relocatable.** Moving the repo broke the console-script
  shims (hard-coded interpreter paths), so tools silently executed against the
  *old* location. Edits here, execution there.
- **`localhost` hung the pipeline for 24 minutes.** On Windows dual-stack,
  `localhost` resolves to IPv6 `::1` first; Ollama listens on IPv4 only. `curl`
  fell back; Python's `urllib` blocked in `SYN_SENT`, and — the compounding
  failure — the embedder had *none* of the timeout/retry hardening the LLM
  client had. Asymmetric robustness: the weakest link was the one nobody
  hardened.
- **The GPU picked the wrong device, and the model got evicted.** llama.cpp
  binds Vulkan device 0 (the iGPU) by default; Ollama unloads a model after
  `keep_alive` idle, and under memory pressure a reload can land on CPU.
  Implicit resource binding and implicit lifecycle, both invisible until they
  bit.

**The principle.** The environment is not configuration you set once; it is a
set of *contracts* — consistency model, name resolution, device binding,
resource lifetime — and each contract has fine print. "It works on my machine"
is usually a statement about which fine print you happened to satisfy.

**The parallels.**
- **The fallacies of distributed computing** (Deutsch/Gosling): "the network is
  reliable," "latency is zero," "there is one administrator," "the network is
  homogeneous." The `::1`-vs-`127.0.0.1` hang is the network-is-reliable and
  topology fallacies in miniature. `localhost` is not simple.
- **CAP / split-brain**: OneDrive chose availability and gave us divergence. You
  cannot put the single source of truth on a substrate that will happily make
  two of it.
- **Hermetic builds / 12-factor / config-as-code**: relocatable-by-design,
  no absolute paths baked into artifacts, environment declared explicitly. The
  venv and the server config both moved toward this (a self-verifying launch
  script that auto-detects the discrete GPU and pins the embedder).
- **Explicit over implicit / principle of least astonishment**: every default
  that bit us (device 0, `keep_alive`, `localhost`) was a *reasonable* default
  in a *different* context. Make the environment's choices explicit and
  self-checking rather than inherited.
- **Defense in depth, applied evenly**: hardening the LLM client but not the
  embedder is a fence with a gate left open. Robustness has to be uniform across
  the critical path.

---

## VI. Governance is a control system

The dark factory's whole reason to exist is *governed* autonomy: policy-gated
adoption, a canary that watches for regressions and rolls back, an exception
queue. Bringing that up taught us that a governance layer is a **feedback
controller**, and it succeeds or fails on the same terms.

- **The canary rolled back every growth batch.** Adopting new legal outcomes
  *inherently* adds ~850 self-coverage gaps (a fresh outcome's characteristics
  don't yet have threshold-matching evidence). A canary budget of 40 "new gaps"
  therefore auto-rolled-back *every* batch that grew the ontology. The control
  was doing precisely what it was told; what it was told measured the wrong
  thing. It conflated **expected growth** (new outcomes needing evidence) with
  **regression** (existing coverage breaking).
- **The reject pile was the treasure.** 1,904 relations were parked as
  "ungrounded" because their endpoints (`GUVERNUL`, `MONITORUL OFICIAL`) were
  never emitted as outcomes — and those were the *most valuable* edges in the
  graph (issuer, publication, amendment links). Promoting endpoints that recur
  across chunks (frequency as the gate) recovered 573 edges (401 → 974).

**The principle.** A control loop optimizes exactly the proxy you give it, so
the proxy had better mean what you think. And the *rejects* of a governed system
are data, not garbage — the queue is a dataset.

**The parallels.**
- **Goodhart's Law / "you get what you measure"**: "minimize new gaps" is a
  proxy for "don't regress," but during growth the proxy and the goal diverge.
  A canary needs to distinguish *new-outcome gaps* (fine) from *existing-outcome
  regressions* (not), or it will fight the mission. This is choosing the right
  **SLI**.
- **Control theory**: a controller tuned to the wrong setpoint doesn't fail
  loudly; it oscillates or clamps. Ours clamped growth to zero and looked, at a
  glance, like an "adoption anomaly."
- **Canary / progressive delivery / blue-green** — but note the direct
  isomorphism: **the dark factory *is* a CI/CD pipeline for knowledge.** Extract
  = build; propose = artifact; churn gate = policy check; canary = canary deploy
  with auto-rollback; adopt = promote to production; the exception queue = the
  failed-pipeline dashboard; `adopt_proposed_ontology(..., rationale=…)` = a
  **break-glass manual approval** with an audit record. Everything DevOps knows
  about safe deployment applies, including that a too-strict gate is a
  reliability problem of its own (it blocks good changes).
- **The dead-letter queue as a source, not a sink**: the "ungrounded" pile is a
  DLQ, and mining it (schema-on-read over the rejects) recovered the graph's
  best structure.

---

## VII. The audit trail is the debugger

The canary mystery — "adopted but didn't grow" — was solved *entirely* from the
append-only event log: `ontology_adopted` events proving adoption compounded
(2969 → 3002 → 3016), then `canary_result: failed; new=850` and
`ontology_rolled_back` events proving why active kept reverting. No live
debugging, no reproduction; the system had already recorded the truth.

**The principle.** A system that records *why* it did what it did can be
understood after the fact without re-running it — which, for slow, expensive,
partly-non-deterministic pipelines, is the difference between diagnosable and
not.

**The parallels.**
- **Event sourcing**: state is a fold over an immutable event log; the log is
  authoritative and lets you reconstruct any past state (and the *reasons*).
- **Observability vs monitoring**: monitoring tells you *that* something is
  wrong; observability lets you ask *why* without shipping new code. The audit
  trail plus per-run explanations is the "why" layer.
- **Forensic / postmortem debugging**: the best incident reviews read the record
  rather than re-enact the crime. Design the record so that's possible.

---

## Field data — the incremental-adoption study (2026-07-23/24)

Sections I–VII are the qualitative lessons. This is the first quantitative one:
what actually happens to a knowledge ontology when you grow it in small,
individually-governed increments. We ran the factory over docs 300→400 in
**10-document batches**, letting it auto-adopt each batch under a widened churn
budget with the canary live — i.e. the CI/CD pipeline of Section VI, exercised
as a controlled experiment.

**Both v1 failures were the earlier themes made concrete.** The run first surfaced
the timeout×`max_tokens` coupling (Theme III — every capped generation timed
out) and a canary that rolled back *every* growth batch (Theme VI — a budget of
40 "new gaps" against ~850 that adoption inherently produces). Fixed to timeout
420 and canary 2500, the study ran clean.

**The saturation curve is a plateau, not a knee.** Across six clean 10-doc
batches (docs 350→400):

| batch | docs | new outcomes | new edges | canary new-gaps | active ontology |
|------:|-----:|-------------:|----------:|----------------:|----------------:|
| 4 | 350 | 16 | 14 | 819 | 3061 → 3077 |
| 5 | 360 | 16 | 15 | 830 | 3077 → 3093 |
| 6 | 370 | 15 | 10 | 840 | 3093 → 3108 |
| 7 | 380 | 18 | 15 | 850 | 3108 → 3126 |
| 8 | 390 | 17 | 11 | 867 | 3126 → 3143 |
| 9 | 400 | 17 |  6 | 869 | 3143 → 3160 |

Mean marginal growth is **~16.5 outcomes per 10 documents, essentially flat**.
The early graph added ~90 concepts per 10 docs; by 350 docs the same volume of
Romanian legal text adds a fifth as many. But the striking part is that it does
*not keep falling* over batches 4–9 — the dedup knee happened earlier, and we
are watching the mature plateau, where new documents mostly **enrich existing
outcomes** (characteristics, edges, evidence) rather than mint new ones. The
ontology is filling in, not sprawling.

**Growth compounds; gaps and canary-pressure drift up slowly.** The active
ontology grew monotonically (2969 → 3160), every batch passed the canary, and
"new gaps" crept 819 → 869 while outcomes-per-batch stayed flat — later
increments are marginally less covered by prior evidence, but never near the
2500 ceiling. Self-coverage gaps rose 1275 → 1389: new outcomes add gaps faster
than fresh evidence resolves old ones, but the two are within sight of balancing.

**The transferable reading.** This is the empirical signature of a healthy
incremental system: bounded, predictable per-increment change; monotonic
compounding under a governance gate that neither blocks nor rubber-stamps; and a
cost metric (gaps) that grows sub-linearly with the corpus. It is what a
*continuously deployed* codebase looks like when it's going well — small PRs,
green canaries, slowly-growing but not exploding tech-debt. A **5-document**
follow-up (docs 400→600) is running to resolve whether the plateau is truly flat
or gently sloping at double the resolution.

**A scaling wall the coarse phase had hidden.** Partway through the 5-doc run,
HTTP 400s from the model climbed to a ~20% chunk-loss rate on the largest-graph
batches. The obvious guess was document size; the measurement said otherwise —
chunks were capped at ~500 tokens, but *prompts* had grown to 5k–17k. The
extraction context injected into every prompt was **unbounded**: the full
relation vocabulary (418 verbs by 400 docs) and each context entity's *entire*
characteristic list (one concept had 139). So the prompt grew with the ontology
and eventually overran the model's context window — the factory getting slower
and lossier the bigger it got. Bounded to a fixed size (a few characteristics
per entity, the top-N verbs, a hard ceiling), an extraction prompt is now the
same size at 3,000 outcomes as at 30. The lesson generalizes past this bug: **a
per-item cost that scales with total accumulated state is a wall you hit later,
not never** — and the finer-grained experiment is what made us hit it while we
were still watching.

### Round 2 (docs 400→600, 5-doc batches): what it actually taught us

The finer round completed — 38 batches, 600 documents, working graph at **3,739
concepts / 12,172 characteristics / 2,624 edges**. Its intended purpose was to
resolve the plateau at double resolution. It failed at that and succeeded at
something more useful.

**The measurement is confounded, and honesty requires saying so.** Outcomes per
chunk by regime: 0.344 (10-doc) → 0.154 (5-doc, before the context fix) → 0.239
(after it) → 0.548 (after promotion was wired in). That is not a saturation
curve; it is a record of our own changes. The dip is chunk loss from the context
wall; the jump is endpoint promotion adding concepts that never came from
extraction. Three regime changes inside one measurement. The robust claim
survives — marginal growth fell sharply from the early corpus (~90 outcomes per
10 docs) to the mature one (~5–16 per batch), so dedup is real and large — but
*whether the mature plateau is flat or sloping*, the entire reason for going to
5-doc resolution, is not answerable from this data.

> **You cannot run a controlled measurement and an improvement effort on the same
> run.** Every fix we shipped mid-flight was correct and worth shipping, and each
> one invalidated the baseline. Next time: freeze the code, then measure. This is
> the experimental-design counterpart to *measure, don't infer* — it is not enough
> to measure, the measurement has to be **controlled**, and a live improvement
> effort is an uncontrolled variable.

**The reject cascade happened exactly as predicted — from a *good* change.**
Wiring endpoint promotion in made its first run promote ~570 accumulated
endpoints at once, blowing the `max_outcomes_added: 150` budget. Because a
churn-rejected proposal never becomes active, the next batch's diff stayed just
as large: **11 consecutive rejections**, the active ontology frozen at 3,342
while the working graph kept growing. When we finally adopted, the queue
collapsed **12 → 1** — eleven accumulated rejections resolved by one decision,
confirming the mechanism precisely.

The lesson is sharper than "tune the budget": **a one-time structural improvement
can permanently lock out autonomous adoption.** A gate that reasons about the
*size* of a change cannot tell a bulk improvement from a bulk regression, and
rejection is self-perpetuating when the baseline never moves. The fix is to
**amortize** — promote at most N endpoints per run, so every increment stays
inside the budget. Same principle as small, always-green PRs; the cure for a
change too big to review is to stop making changes that big.

Promotion itself was the round's biggest win: **edges 1,843 → 2,624, parked 3,430
→ 2,901** — 763 grounded relations recovered (issuer, publication, amendment
structure that had been silently discarded).

**And the metrics were lying.** Those eleven rejected batches recorded
`canary=passed, new=898`, because the reporter read the latest `canary_result`
event unconditionally — but a rejected proposal never runs a canary, so it was
re-reading a stale verdict from before the restart. Eleven rejections looked like
eleven passes. *Instrumentation is code and can be wrong in the direction that
flatters you*; a metric that cannot report "not measured" will invent a
measurement.

## VIII. The instrument is the experiment

Theme I says *measure, don't infer*. The distillation arc taught the next lesson,
which is harder: **measuring is not enough — the instrument has to be right, and
you will usually not notice that it isn't.** Five iterations produced three
instrument failures, each of a different kind:

- **A metric that punished the truth.** Iteration 1 scored relation quality by
  *purity* — the fraction of a relation's instances in its dominant type pair. It
  failed at 0.41. But `se referă la` ("references") *legitimately* links any kind
  of thing to any other, so low purity was the correct reading of a genuinely
  polysemous relation. The metric was penalising the data for being honest.
  Replacing it with **normalized entropy over type pairs** separated specific from
  generic relations cleanly (a +0.14 bimodal gap) and put the right relations on
  each side.
- **Metrics that were unanimously fooled.** Iteration 4 pre-registered four
  checks — prompt tokens, yield, canonical-direction rate, parse failures. All
  four **passed**, and the experiment was a disaster: the model was echoing the
  schema, emitting each axiom ~7 times regardless of content, copying the ASCII
  arrow notation into the relation-*type* field, and fabricating a false relation
  to fill the third slot. Every metric was a *quantity* metric; the failure was
  *qualitative*. Yield went **up** (2.33×) because fabrication is productive.
- **A threshold set without a baseline.** Iteration 4b pre-registered
  "schema violations ≤ 10%". The baseline was **50%** — the threshold was
  unachievable by construction, and it nearly made me diagnose a real defect
  (type overloading) by the wrong name.
- **A saving computed from structure, mistaken for a saving.** Three separate
  times a benefit was calculated from the *shape* of the data and would have been
  shipped on that number alone. Collapsing layout whitespace "saves 452 chunks
  (~7.5 h)" — measured, it cost **44% of extracted characteristics**, because runs
  of spaces are what delimit table cells. A corpus audit reported "exclude 142
  flagged files, save 71 h" — inspected, every flag was legitimate formatting and
  acting on it would have deleted **4,280 chunks of real legislation**. A distilled
  schema as extraction context cut the prompt to 0.13× and *raised* yield 2.33× —
  read, the model was echoing the schema and fabricating relations. Every estimate
  was arithmetically correct. Every one was about the wrong quantity.

**The principles.**

> **Pre-registration protects against moving the goalposts. It does not protect
> against measuring the wrong thing.** Both are needed and they are different
> disciplines.

> **Measure the baseline before setting an absolute threshold.** A number with no
> control is not a target, it is a guess wearing a target's clothes.

> **Pair every quantity metric with a shape metric.** What exposed iteration 4 was
> not any of its four checks but a *variance*: arm B emitted exactly 3 relations on
> 10/10 chunks. Constant output is the signature of a template-filler, and it is
> invisible to any per-chunk average. Iteration 4b pre-registered the coefficient
> of variation as a template detector, and it worked.

> **A saving computed from structure is not a saving until output quality is
> measured on the same data.** Token counts, file counts and chunk counts are
> proxies for cost, never for value. The proxy is cheap and feels like evidence,
> which is exactly what makes it dangerous: it arrives already quantified, in
> units that look like the units you care about. Before acting on any efficiency
> estimate, run the expensive thing on a sample and compare *output*, not size —
> and set the acceptance threshold against a measured baseline before looking at
> the result.

**The parallels.** *Goodhart's Law* again, but pointed inward — at our own
instruments rather than at the system's controls (Theme VI). *Construct validity*
from measurement theory: does the metric measure the thing you named, or merely
something correlated with it under normal conditions? And **Campbell's Law**: the
more a quantitative indicator is used for decisions, the more it distorts what it
was meant to track. Also *garbage in, gospel out* — a dashboard laundering a
number into a belief.

## Field data — round 3: the distillation arc (2026-07-25)

Five iterations, each pre-registered, run in a single day at seconds-to-minutes of
compute apiece. Three refuted, two confirmed — **and the refutations did the
steering.**

| # | question | verdict |
|---|---|---|
| 1 | does the corpus have a learnable grammar? | CONFIRMED (purity refuted) |
| 2 | is class typing the bottleneck; is entropy the right statistic? | REFUTED on thresholds; entropy confirmed |
| 3 | is high entropy label noise or real polysemy? | REFUTED decisively → polysemy |
| 3b | does inverse canonicalization clean the schema? | CONFIRMED 4/4 |
| 4 | is a small schema good extraction context? | REFUTED on quality (all 4 metrics passed) |
| 4b | is it good as a *constraint* rather than a template? | CONFIRMED 6/6 for the hybrid |

**What was found.** The abstraction is real: `AuthoritativeBody --emite-->
NormativeAct`, 416 instances, entropy 0.05, 100% direction-consistent — a clean
typed axiom deduced from data and traceable to source documents. But the grammar
is **genuinely small** (2–3 axioms over a substrate of untyped cross-referencing),
and iteration 3 established that decisively rather than by assumption: entropy is
invariant to label quality, so better classes cannot grow it. That smallness is a
finding about Romanian legal text — legal cross-referencing *is* type-agnostic, and
a distiller that forced `se referă la` into a typed axiom would be lying about the
law.

**The novel finding — an asymmetry in what you may put in an LLM's context.**

> Listing **instances** is safe and useful: "reuse what you see" *is* deduplication.
> Listing **patterns** is unsafe: "reuse what you see" becomes *assert this
> pattern*. A **class vocabulary** is the safe middle — it aids naming without
> asserting anything.

Both failure modes were observed. Given three axioms as context, the model
**fabricated** instances of all of them (iteration 4). Given a closed list of
permitted relation *types* — a weaker form of the same mistake — it **overloaded**
them, forcing every "body does something" statement onto the nearest type until
relation objects became verb clauses (`Ministerul --emite--> "organizează şi
asigura…"`) and distinct relation types collapsed 34 → 14 (arm B2). Only the
hybrid — class names plus a bounded list of instance *names*, no relation types —
beat the baseline on diversity (37 types vs 34) while cutting the prompt 3.6×.

**And the answer turned out to be already built.** The winning context is a fixed
class list plus a bounded instance sample — which is precisely what `context_cap`
and the caps from `a25dfc1` already provide. **The unbounded-context problem was
solved when we bounded the context;** distillation's contribution to "pitch black"
is a small stable class vocabulary, not a replacement for instances. Iteration 4
spent its effort on a second mechanism that was never needed — a reminder to check
whether the thing you are about to build is already there in another form.

**The ossification risk is immediate, not eventual.** The proposal worried that a
schema-guided extractor would *slowly* come to confirm its own schema. It does it
on the first chunk. Anti-ossification machinery (MDL-style eviction of components
that stop paying for themselves; a residual set of unexplained patterns that
doubles as the alarm) is therefore a **prerequisite** for any self-improving loop,
not a later refinement.

## Meta-observations

- **The core was never the problem.** The Rust graph engine was fast and correct
  throughout; a full scoring pass is seconds. Every multi-hour cost was LLM
  decode, and every *outage* was in the seams — config, I/O, lifecycle, control.
  This is the usual shape of production trouble: not the algorithm, the
  plumbing. Budget attention accordingly (*Amdahl's Law* points at the LLM;
  *incident statistics* point at the glue).
- **Most fixes were subtractions or reconciliations, not inventions.** Batch the
  one call site that wasn't batched. Flush the WAL that already existed. Make the
  timeout respect the token budget. Use the IPv4 literal. Recover the rejects.
  The leverage was in *consistency* and *coherence*, not new machinery.
- **Confidence is a hazard when unmeasured.** The recurring failure mode was not
  ignorance; it was a *fluent, plausible wrong answer* (from human and model
  alike). The antidote was cheap, specific measurement — and the humility to run
  it before acting.
- **Instrument the happy path, because success is silent.** Nearly every "is it
  stuck?" scare came from a system that logs failures loudly and successes not
  at all. Progress needs a positive heartbeat, not just an error stream.
- **Refutation was more productive than confirmation.** Across the distillation
  arc, three of five iterations were refutations — and they are what redirected the
  work: iteration 3 killed the "better classes will grow the schema" plan, and
  iteration 4 stopped us shipping a schema-as-context feature that actively
  degrades extraction. The two confirmations polished; the refutations steered. A
  research loop that only produces confirmations is not learning, it is agreeing
  with itself.
- **The progress came from the interruptions, not the patience.** This is the
  through-line, stated plainly: every leap — the 42× embedding batch, checkpointing,
  the canary calibration, the bounded context — landed because we *stopped to
  measure against a stated expectation* and let the result confirm or overturn
  it. A hypothesis you commit to and then check is worth far more than one you
  wait to have settled for you. The wrong guesses (finding 16, the iGPU alarm,
  the "flat" plateau, "it's the doc size") were only wrong for as long as it took
  to run the check — and running the check is what produced the next direction.
  Waiting for the end result would have delivered a slower, lossier factory and
  none of the understanding. The tight measure → hypothesize → check → correct
  loop *was* the method; the deliverables were its residue.

---

## Open questions / to revisit

- **A growth-aware canary — now known to be a defect, not a nicety.** A review
  across 27 governed batches measured the canary's discriminative power and it
  has none: `new_gaps` sat at **mean 848, sd 17 (CV 2.0%)** while the batches
  behind it ranged from 0 to 45 outcomes added; halving the batch size (10→5
  docs) moved it **0.6%**; correlation with outcomes added was **−0.30**. The
  cause is in `_canary_check`: it diffs gap *target strings*
  (`outcome_id:characteristic`), and outcome ids are content-derived, so every
  adoption reshuffles identities across thousands of outcomes and the count is
  dominated by identity churn rather than lost coverage. The consequence is that
  **no threshold discriminates** — 40 rejects every batch, 2500 accepts every
  batch — so the factory's principal safety control is currently decorative. The
  fix is to compare coverage only over the outcomes present in *both* runs
  (the intersection), isolating genuine regression from expected growth and from
  identity churn. This is a **prerequisite for "pitch black"**: autonomy without
  a working canary is not lights-out, it is unsupervised.
- **Self-coverage calibration.** Self-coverage gaps scale with ontology size
  because characteristic-vs-evidence matching is token-coverage over a threshold.
  Is there a normalized coverage metric that doesn't inflate with growth?
- **Backpressure, not just retries.** We bound retries and timeouts, but the
  system has no explicit backpressure when the LLM is saturated. Should the
  factory throttle ingest to match extraction throughput?
- **The environment contract as a preflight.** Many Section-V incidents would
  have been caught by a startup preflight: assert IPv4 reachability, assert the
  discrete GPU is bound, assert relocatable paths, assert `timeout >
  max_tokens/throughput`. Worth formalizing.
- **Idempotency of adoption vs the working graph.** The working graph compounds
  monotonically while the active ontology can roll back; the divergence between
  them under repeated batches deserves a clean mental model.

---

## Revision log

- **2026-07-26** — 800-doc run completed 5/5 autonomous (active 4,097 → 4,222),
  the second consecutive fully unattended round and the first carrying the
  dead-letter queue. Measured confirmation added to Theme III: the parse-failure
  rate, constant at 0.091 for five batches, now moves (0.136 → 0.087 → 0.091 →
  0.000 → 0.050); two chunks quarantined, one of which had been failing invisibly
  because only the unparseable branch logged.
- **2026-07-25 (latest)** — 750-doc run completed 5/5 autonomous (active 3,998 →
  4,097). Its log exposed a chunk that had failed extraction in *every run ever
  made* and been re-sent on every batch: added *The retry that outlived the run*
  to Theme III (poison message → dead-letter queue; transport errors exempt so an
  outage cannot dead-letter the corpus; three failure causes were being counted
  as one, which is why `parse_failure_rate` read a constant 0.091). Added the
  structural-saving lesson to Theme VIII and the companion design note
  `docs/table-preserving-reduction.md`. Commits `ef412bc`, `292516a`.
- **2026-07-25 (later)** — Consolidated the distillation arc: added *Theme VIII —
  the instrument is the experiment* (three instrument failures in five iterations:
  a metric that punished the truth, four quantity metrics unanimously fooled by a
  qualitative collapse, and a threshold set without a baseline) and *Field data —
  round 3* (five pre-registered iterations; the instances-vs-patterns asymmetry;
  the finding that bounded context had already solved the problem iteration 4 was
  chasing). New meta-observation on refutation being the productive outcome.
  Commits `e1ba19c`, `d0b32ed`-range through iteration 4b.
- **2026-07-25** — Round 2 complete (600 docs, 38 batches; graph 3,739 concepts /
  2,624 edges). Added *Round 2: what it actually taught us*: the saturation
  measurement is confounded by our own mid-flight fixes (measure XOR improve);
  the predicted reject cascade fired from a good change (11 rejections, queue
  collapsed 12→1 on adoption); endpoint promotion recovered 763 relations; and
  the metrics reported stale canary verdicts as passes. Fixes committed in
  `746e6e8` (amortized promotion, honest canary reporting). Active ontology
  re-baselined 3,342 → 3,637.
- **2026-07-24 (later)** — 5-doc phase (docs 400→600) surfaced a scaling wall:
  the extraction context was unbounded and prompts overflowed the model window
  at ~3,200 outcomes (~20% chunk loss on large-graph batches). Fixed by bounding
  the context (`a25dfc1`), loop restarted. Added the scaling-wall note to Field
  data and a meta-observation on why the tight measure→check loop, not patience,
  produced the progress (the collaborator's framing).
- **2026-07-24** — Added *Field data — the incremental-adoption study*: the
  10-doc batch run (docs 300→400) completed, 9 batches, active ontology
  compounding 2969→3160, marginal growth flat at ~16.5 outcomes/10 docs (the
  saturation plateau). Both v1 config bugs (timeout, canary=40) recorded under
  Themes III and VI. Launched the finer 5-doc phase (docs 400→600) to resolve
  the plateau at double resolution.
- **2026-07-23** — Document created during the 300→400 incremental-adoption loop.
  Captures the 49→200→300 scale-up, the checkpointing/batching/timeout/IPv4
  fixes, the endpoint-promotion recovery, and the canary-calibration discovery.
  Relevant commits: `b0d10b1` (self-loop parking), `c8518f3` (batch resolution
  embeddings), `5af022e` (checkpointing + ingest batching), `d81b376` (max_tokens
  cap + server config in-repo), `49ca25e`/`c10db8a` (endpoint promotion),
  `c6ebe0e` (embedder IPv4 + retry), `d969315` (pin embedder), `ceb5211` (LLM
  retry).
