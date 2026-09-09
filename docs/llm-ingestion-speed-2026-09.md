# LLM ingestion: combine independent embedding work

September 9, 2026. Baseline: `0132dfe`.

## Finding and implementation

The graph-augmented extraction loop already skips unchanged chunks, bounds second
looks, caches concept vectors, batches vectors inside a fragment, and supports
concurrent generation windows. Nevertheless it sent separate requests to embed
each next chunk's retrieval query and each preceding fragment's names.

The loop now combines the current window's names and the next window's query
texts into bounded embedding requests. Queries retain their exact text cache
keys, including whitespace. Only vectors are prepared early: context retrieval,
entity resolution, graph updates, second looks and checkpoint ordering retain
their existing sequence. The next context still sees preceding graph updates.
Default generation concurrency remains one.

On reopening a graph, the first concurrent window's query vectors are also
batched. Failed extraction replies contribute no names. Empty graphs with no
proposed names avoid speculative query embedding. The existing lazy fallback
remains available after prefetch failure; failures can add latency.

Larger batches make alignment assumptions consequential. HTTP embedding responses
now require an index for every input exactly once, finite values and a consistent
nonzero dimension, including consistency with earlier responses. Incomplete
prefetch responses from other embedders are not partially cached.

This affects the durable extension and single-use graph-augmented extraction
paths. The GUI's separate extract-only token stream uses stateless extraction
followed by reconciliation, so this change does not accelerate its generation.

## Evidence

The initial deterministic test failed with 9 requests for five sequential chunks
and 7 requests with width-three windows. It now passes with 5 and 2 requests,
respectively. Differential tests compare complete prompts and results with
prefetch disabled, including relooks. All 601 Python tests pass.

`experiments/embedding_schedule_benchmark.py` measures real local `bge-m3:latest`
embeddings on six Romanian-law cases with fixed extraction replies and temporary
graphs. A warm-up precedes four runs ordered old/new/new/old. It performs no LLM
generation, no downloads and no writes to source or production graphs.

| Schedule | Requests | Run times |
| --- | ---: | ---: |
| Previous | 11 | 0.824 s, 0.497 s |
| Combined | 6 | 0.331 s, 0.300 s |

All four runs produced identical prompts and results. Raw measurements and case
identifiers are in `experiments/embedding-schedule-live.json`. Two repetitions
and a small warm workload do not establish corpus-wide speed. Generation latency
was excluded: do not apply this timing ratio to a full LLM extraction run.
Batching can introduce numerical differences in other embedding implementations;
the test establishes equivalence for this measured model/workload, not every model.

## Other avenues assessed

- **Generation concurrency:** already implemented. More simultaneous requests can
  only help if the inference server has capacity. It also changes which earlier
  chunks are visible in a window. Keep the default until a paired throughput and
  extraction-quality experiment establishes a useful setting.
- **Model switching:** combining embedding work reduces request transitions, but
  this experiment did not load a generator and therefore does not establish model
  eviction or reload savings. Measure residency and load time under the actual
  generator/embedder combination before changing server configuration.
- **Repeated generation:** unchanged source chunks are already skipped. A new
  response cache must include the full graph context, rule context, prompt and
  model configuration; source-text-only caching would reuse obsolete proposals.
- **Prompt/output cost:** preserving qualified rules takes precedence over shorter
  answers. A useful next experiment separates prompt evaluation, output decoding,
  label-repair calls and transport retries on a fixed document set. End-to-end
  seconds per chunk alone cannot identify which of these to optimize.
- **Language changes:** this measured cost sits in model-service request scheduling.
  Replacing Python orchestration would not remove those requests. The Rust engine
  continues to own graph/index work.

No extraction instructions, rule filtering, token caps, model selection, merge
thresholds or generation concurrency defaults were changed.
