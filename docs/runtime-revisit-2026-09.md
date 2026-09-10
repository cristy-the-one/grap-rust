# Revisiting runtime decisions after subject-boundary validation

This evaluation supersedes the earlier runtime and selective-thinking conclusions
where noted below. Earlier reports remain historical observations, not current
guarantees. All new measurements use legal-rules-v11 and qualified-rules-v2.

## Why revisit

A validator change can alter the apparent accuracy of an existing output, which
units trigger repair, the cost of repair, and what enters the graph as context
for later documents. Re-scoring historical outputs tests only the first of these.
We therefore use three complementary comparisons:

1. Revalidate all 91 saved runtime outputs without new inference. Historical
   latency is retained only as history; it is not re-labelled as current timing.
2. Generate fresh first responses and compare normal repair, thinking replacement,
   and thinking after normal repair on identical first responses. Repeat the
   repair comparison on both historical baseline realizations to distinguish
   changes caused by new generation from changes caused by validation.
3. Rerun structured versus unstructured ingestion through the production HTTP
   provider, real BGE embeddings and a growing temporary graph. Each arm's own
   accepted outputs become later graph context.

The frozen-context runs deliberately retain identical prompts. They isolate
runtime effects; the full-ingress comparison covers changing graph context.
Neither is a corpus-wide or held-out accuracy estimate. There are 12 development
targets; repeated outputs are not additional independent legal examples.

## Findings available from revalidation

Revalidation changed one of the 91 historical scores: temperature 1.0 on
semantic-09 changes from zero to one unresolved rule. Its interior-word subject
had passed the old validator. Other historical scores remained the same. This
does not show their old repair paths or timings would remain the same today.

On the new 12-case outputs, normal repair retains all 12 target answers with one
unresolved rule. Thinking replacement retains the targets but leaves two gaps.
Thinking after normal repair retains one gap. Estimated combined first-call and
repair times are 55.39, 67.45 and 64.01 seconds respectively. These are paired
repair comparisons using a common measured first-call cost, not three fresh
end-to-end job timings.

Fresh output-order testing retains 12/12 targets and one gap in both arms:
normal order takes 55.08 seconds, units-first 50.36. Both make three repairs,
and entity mentions remain 50 versus 44. This repeats the small timing signal
but does not resolve the earlier concept-coverage concerns or the previous
reverse-order run's failure to reproduce a speed advantage.

Repeating repairs on historical realizations A and B gives the same coverage
ordering as the fresh run: normal repair leaves one gap, thinking replacement
two, and normal-then-thinking one. Estimated totals are 56.41 / 68.65 / 65.17
seconds for A and 49.88 / 64.21 / 58.62 for B. Thus the prior claim that thinking
replacement merely *moves* one gap is superseded: with word-boundary validation
it leaves an additional unresolved subject.

Whole-extraction thinking on the four difficult cases takes 89.76 seconds,
retaining 4/4 target answers, one repair and one unresolved rule. The earlier
quality/cost tradeoff remains: better first-pass target labels, substantially
more time, no complete resolution.

Temperature 1.0 now retains 4/4 targets, with three repairs and one gap, in
27.76 seconds. The earlier wrong target did **not** recur. The correct current
conclusion is not that temperature 1.0 always makes that mistake; it is that
these small repeated trials do not establish a stable quality or speed benefit.
This explicitly revises the earlier single-run observation rather than treating
it as a permanent property of the configuration.

The fresh four-case greedy comparison takes 28.09 seconds with three repairs,
4/4 targets and one gap. Temperature 1.0's 27.76 seconds is too small a
difference in this experiment to establish a useful speed advantage.

Smaller context, smaller batch and load/prefix warm-up were re-scored from their
saved outputs but not rerun live in this pass. They had no changed scores and
no previously demonstrated accuracy benefit. This prioritization does not make
their historical timing immutable; a future load, backend, prompt-size or model
change should trigger a new timing comparison.

## Full ingestion with changing graph context

| Production-provider arm | Seconds | Correct targets | Rules / unresolved | Repair calls | Graph entities |
| --- | ---: | ---: | ---: | ---: | ---: |
| Structured legal extraction | 52.08 | 12/12 | 31 / 1 | 4 | 36 |
| Unstructured comparison | 71.84 | 12/12 | 31 / 1 | 2 | 44 |

The structured arm is about 27.5% faster in this rerun, despite making more
repairs. All 86 checked statement, context and subject spans in that arm match
the source; all 87 in the comparison do too. This supports retaining the
structured profile. It does not prove equal entity recall: the entity counts
and deferred proposals differ (12 structured versus 29 unstructured), and exact
source grounding is not a complete semantic oracle. No production graph was
used or modified, and each arm built its own temporary graph from scratch.

The original full-ingress speed observation survives this rerun; the specific
latencies remain measurements on this machine and workload, not constants.
By contrast, the selective-thinking coverage comparison became worse under the
new validator, while the high-temperature classification error did not repeat.
These are deliberately separate, updated conclusions.

## Reproducibility improvements

Runtime reports now include hashes of the KEE Python code and benchmark drivers,
Python and Ollama versions, and installed model digests. Prompt and source hashes
remain recorded. These identify dependencies; they do not prove equivalent GPU
execution, cache state or complete binary/environment reproducibility.

The selective-thinking runner accepts a selected evidence file and prompt file,
so it no longer silently depends on one historical baseline forever. It
recomputes first-pass scores under current validation and keeps historical
scores separately. Escalation rejects mismatched prompt/evidence hashes or
changed code fingerprints rather than mixing generations of experiments.

`experiments/rescore_runtime.py` provides an offline revalidation pass, including
the structured evidence filter. New outputs are written to new files; historical
receipts are preserved. No production defaults or graphs are changed by these
experiments.

During harness review, first-pass diagnostic parsing was adjusted to preserve
the structured evidence-admission setting as well. An offline comparison verified
that all 72 first-pass diagnostic scores in these paired runs were unchanged.
The actual repair arms already used the correct structured setting. Reports
retain the fingerprints captured at execution; later diagnostic-only changes
are not presented as if they had run earlier.

All nine new evidence files and their hashes are listed in
[runtime-revisit-manifest.json](experiments/runtime-revisit-manifest.json).
The fresh-output report can be supplied to `evaluate_selective_thinking.py`
with `--evidence`, together with the committed frozen prompts via `--prompts`.
Use a fresh `--output` path for each run, and pass that paired result to
`--escalate-from` for the bounded second-stage comparison.

Validation: 35 experiment tests passed, including rejection of mixed code,
prompt and response generations. Production code was unchanged in this pass.
