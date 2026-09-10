# Real LLM ingress: measured speed, rejected shortcuts and grounding fixes

Implementation: `dcd9fae`. Local Gemma legal profile, BGE-M3 embeddings, RTX 5080
Laptop GPU. No corpus-wide inference or production graph writes were performed.

## Outcome

The final structured legal path completed the 12-target graph-augmented workload
in **49.8 seconds**, compared with **69.7 seconds** for the original unstructured
prompt in the same reversed-order comparison. Both retained all 12 checked target
classifications, subjects and qualifications. A preceding valid structured run
took 54.3 seconds; the initial original-prompt run took 67.1 seconds.

These observations support approximately **20–29% lower elapsed time on this
small development workload**, not a corpus-wide or model-independent speed claim.
There are too few repetitions for a stable latency distribution. Seed 42 and
temperature zero did not make every response identical across runs.

The confirmed structured result retains 31 source rules. All 86 checked evidence,
context and subject spans match source text. One additional, unscored rule has an
unresolved subject after bounded repair; it stays visible for review. Entity
granularity and recall remain imperfect: some extracted names are long phrases.
The 12 target checks are not an independent evaluation of every graph entity.

## What consumed the time

Native Ollama phase timings on the first five cases showed:

| Component | Time |
| --- | ---: |
| Output decoding | 19.59 s |
| Prompt evaluation | 0.99 s |
| Model loading across the five requests | 0.013 s |
| Total observed wall time | 20.83 s |

Approximately 94% was output decoding. This is why trimming Python glue or
optimizing prompt preparation further was not the useful target. Both generator
and embedder were resident during the complete-path trial. No model reload storm
was observed. The probes used zero transport retries and observed no transport
failures; they do not quantify the cost of retries during an outage.

Native `/api/chat` was used only to expose server phase durations. The final
complete-path comparisons use the actual OpenAI-compatible provider, real
embeddings, Rust-backed WorkingGraph context/resolution and the normal bounded
relook budget. No relook signals arose in these runs. The two transports' timing
figures are not treated as interchangeable.

## The misleading result we rejected

A compact-JSON instruction was largely ignored. Enabling the old generic schema
then appeared to reduce full ingress from 67.1 to 30.7 seconds, while the narrow
target check still passed 12/12. Inspecting the resulting graph invalidated that
conclusion: entity discovery fell from 44 to 10 concepts, including invented
`Test Plan` and `Order 17` nodes copied from prompt examples. Empty evidence had
been accepted. Faster generation had concealed missing and contaminated output.

The rejected raw result is retained in `experiments/llm-ingress-rejected.json`.
This is diagnostic evidence, not the accepted performance result.

## Changes adopted

1. **Request-specific legal schemas.** When structured extraction is enabled,
   every supplied legal-unit ID is required exactly once. Allowed kinds are
   explicit; subjects may remain empty when uncertain. Oversized units omitted
   from the prompt retain their existing unresolved treatment. Shared providers
   and the generic schema are not mutated.
2. **A legal prompt for structured legal requests.** It removes unrelated process
   examples, emphasizes source-supported entities, and separates complete legal
   rules from flat graph relations. Testing the rewrite in unconstrained mode
   exposed a classification regression, so that mode retains its original prompt.
3. **Missing evidence cannot seed structured graph claims.** Empty or invalid
   evidence is rejected; the original rejected proposals are retained in receipts.
   Source-preserved qualified rules survive independently. Nonempty evidence does
   not, by itself, prove semantic correctness.
4. **A whole statement is not an accepted model-selected subject.** Unless existing
   explicit syntax independently resolves the subject, this selection remains
   unresolved and triggers the existing bounded labeling repair. No guessed
   subject or rewritten rule is substituted.
5. **Both complete and GUI streaming requests use the same per-request schema and
   prompt selection.** Receipts record the response-format hash. Prompt and legal
   classifier versions identify the new behavior.

Requiring IDs reduces omission opportunities; it does not guarantee fewer calls.
The confirmed structured run made 16 calls (including four classification repairs)
versus 14 for its unstructured comparison. It was still faster overall. Failed
repairs remain unresolved rather than being declared correct to improve a score.

Validation: **604 Python tests pass**, including HTTP payload isolation, required
unit IDs, empty-evidence contamination, lossless rule preservation and bounded
repair. Existing Starlette deprecation warning remains. Rust implementation and
generation concurrency defaults were not changed in this pass.

## Running the tested local profile

The launcher now accepts an explicit profile for the models already installed on
this machine:

```powershell
.\aspice-gui\scripts\run-legal-review.ps1 -LocalLegalProfile
```

It selects local `grap-legal-gemma4`, BGE-M3, structured output and disabled
reasoning. Without the switch, existing environment configuration remains in
control. The profile does not download or start inference by itself.

Automatic approval review blocked the attempted GUI restart with only
"blocked by policy". The implementation and profile are saved, but the running
GUI was not refreshed in this activity. Benchmark models were unloaded.

## Reproducibility and remaining limits

The [September 10 revisit](runtime-revisit-2026-09.md) repeats the full-ingress
comparison after the subject-boundary validator change. Use that report for the
new measurements; the observations here remain historical.

- `experiments/profile_llm_ingestion.py`: native phase measurements and prompt
  experiments on frozen, hash-checked source fixtures.
- `experiments/profile_full_ingress.py`: full production-provider comparison on
  12 pre-existing development targets, temporary graphs only. `--reverse` changes
  arm order. It retains raw replies and exported graphs.
- `experiments/llm-phase-initial.json`: raw initial phase evidence.
- `experiments/llm-phase-summary.json`: summaries of the exploratory phase runs.
- `experiments/llm-ingress-confirmed.json`: accepted reversed-order full-path run.

The exploratory seven-case phase run overlapped the Python test suite; use it for
functional observations, not isolated timing. Final full-path measurements ran
without that competing test job. No claim is made that all legal meanings or
entity boundaries are correct, that every provider supports the schema, or that
this elapsed-time ratio holds across the full corpus. Existing extraction stores
are not rewritten by installing the changes; prompt-version invalidation or an
explicit refresh can incur new model work when ingestion is next requested.
