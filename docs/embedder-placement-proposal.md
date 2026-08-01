# Should the embedder run on CPU and the extractor on GPU?

> **Status:** proposal, nothing implemented, nothing measured beyond what is cited.
> Raised 2026-08-01 during the break at 1,725 documents. Read this before
> touching model placement.

## The question

bge-m3 (embeddings) and Bonsai-27B (extraction) currently share one 16 GB RTX
5080. Should the embedder move to CPU?

## The real argument is VRAM, not embedding speed

The two models compete, and we have already paid for it: the pilot runs Bonsai at
`BONSAI_CTX=16384` **because** bge-m3 is resident, and that contention once
produced a 30× decode collapse (see [[laptop-rtx5080-env]],
[[model-residency-lessons]]). bge-m3 is not merely occupying memory — it is
setting a ceiling on the extraction configuration.

That matters because of where the time goes. Measured per batch (the `where:`
line, 2026-07-31):

```
extract 1,330-1,700 s   promote 123-133 s   candidate_ontology 32-40 s   gaps 85 s
```

Extraction is **60-75% of a batch**, and decode on a Q2 27B is
**memory-bandwidth-bound**, not compute-bound: every token streams the weights,
so additional concurrent sequences amortise that read. Extra `-np` slots are
close to free throughput until bandwidth saturates. Freeing a couple of GB
plausibly buys another slot or two, which multiplies the dominant cost.

An encoder gets none of that benefit. bge-m3 is ~568M parameters doing a *single
forward pass* — no autoregressive loop, no KV cache, no per-token weight
streaming. It is precisely the workload that does not need the GPU's one special
capability.

**Right tool for the job:** GPU bandwidth for the decoder, CPU cycles for the
encoder — and the CPU is idle anyway. Near-zero CPU during scoring was the clue
that killed "finding 16" (Theme I).

## Second argument: it deletes a failure mode

Ollama's implicit lifecycle has bitten us — models unload after idle, and a
reload under memory pressure can land on CPU silently. `keep_alive=-1` is a
workaround that *pins VRAM to prevent an accident*. Deliberately placing the
embedder on CPU removes the eviction hazard, the reload race, the contention
jitter, and the need for the pin. It also makes the phase timings cleaner, since
two processes sharing a GPU produce latency attributable to neither.

## The catch: the embedding workload is bimodal

Cost lands very differently on the two modes:

| mode | size | where it appears | CPU risk |
|---|---|---|---|
| short strings — concept names, characteristics, parked endpoints | tens of tokens | inside loops (resolution, promote, export) | low; tens of ms per text |
| document chunks at ingest | ~2,000 chars ≈ 500 tokens | once per batch | ~25× the work per item, but ingest is **0.6-1.3 s of a 2,200 s batch** — noise |

The strings that appear in hot loops are the cheap ones. The expensive ones are
not on the hot path. That asymmetry is what makes this plausible at all.

## SEQUENCING — do not skip this

We just took `promote_s` from 1,241 s to 123 s by batching ~6,727 endpoint
embeddings (`428e66f`). **If CPU embedding is 5-10× slower per text, that phase
returns to 600-1,200 s and the optimisation is erased.**

So: **make the work disappear before making it cheaper.**

1. **First, persist `_vec_cache`.** It is still per-process — every launch
   re-embeds names it has embedded a hundred times. Persist name→vector the way
   `char_tokens_cache.db` persists characteristic tokens (`342f747`). Most
   recurring embedding work then stops existing regardless of device.
2. **Then move the embedder to CPU.** What remains is genuinely new names plus
   ingest chunks — bounded, and latency-tolerant.
3. **Then raise `-np`** with the reclaimed VRAM and measure tokens/s.

Moving to CPU *before* caching would look like a regression and would probably be
blamed on the CPU rather than on the missing cache.

## The subtle risk

Vectors computed on CPU in fp32 and on GPU in fp16 differ in their last bits.
Normally irrelevant — except this project has a **0.86 cosine merge threshold**,
and the canary investigation found retrieval scores clustered in the **fourth
decimal place**, with top-k membership decided by differences that small
(docs/canary-noise-floor-investigation.md).

I would not *expect* merge decisions to flip on 1e-6 drift, but "I would not
expect" is the phrase that has been wrong most often in this project. The
concrete hazard is **mixing**: vectors already in the index computed one way,
new ones computed another, compared against each other. If we do this, the switch
should be clean and one-directional, and existing near-threshold pairs should be
checked for changed verdicts — cheap to test offline against the stored index.

Note also that changing the *model* is a different, far more expensive question:
every stored vector would be invalid and the whole index would need rebuilding.
This proposal is same model, different device.

## What to measure before believing any of it

1. **bge-m3 CPU throughput on our actual length distribution** — short names and
   2,000-char chunks measured *separately*, never as an average that hides the
   bimodality.
2. **Whether freeing that VRAM genuinely permits more `-np` slots, and what
   tokens/s does at the higher count.** This is the entire payoff and it is
   currently an assumption.
3. **Cross-device vector agreement** on a sample of existing concepts, checked
   against the 0.86 threshold.

## Expectation, stated so it can be scored

Net win, driven **almost entirely by (2)**. If (2) is flat — because the GPU is
already bandwidth-saturated at `-np 4` — the whole exercise is a lateral move
that trades embedding latency for nothing.

That is the same shape as the three hypotheses the phase timer demolished in one
afternoon (Theme IX), so: get the number before committing.

## Practical notes

- Ollama takes CPU-only placement via `OLLAMA_NUM_GPU=0` or `num_gpu: 0` in the
  request options; no code change is needed in `kee/embeddings.py`, which speaks
  plain OpenAI-compatible HTTP.
- `GRAP_EMBED_BASE_URL` already points at the embedder independently of
  `GRAP_LLM_BASE_URL`, so the two can be moved apart without touching the
  pipeline.
