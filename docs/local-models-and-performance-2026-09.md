# Installed models and extraction performance — September 2026

Follow-up: the [real Romanian corpus evaluation](real-law-evaluation-2026-09.md)
exposes semantic failures that the synthetic cases below did not. Treat the
model recommendation here as a development baseline, not production accuracy.

Inspected `D:\ComfyUI_windows_portable\ComfyUI\models` (the supplied path had
different separators) and the already-running local Ollama installation. Hardware:
RTX 5080 Laptop GPU, 16,303 MiB VRAM; approximately 63.4 GiB usable system RAM.
No model downloads or changes to ComfyUI model files are needed for this work.

## Performance fixes

### Remove a connection delay from every default local LLM request

The LLM provider defaulted to `localhost` even though the embedding provider
already used numeric IPv4. On this Windows installation, an address-only A/B test
of Ollama's version endpoint measured median 2,056 ms for localhost and 12 ms for
127.0.0.1 (three requests each). The actual application `list_models` path measured
2,039 ms before and 16 ms after, returning the same 12 model names.

Changed the LLM and corpus-script defaults to `http://127.0.0.1:11434/v1`, and aligned
the setup examples. Explicit configured URLs remain authoritative. This removes
connection overhead, not model decoding time; numbers depend on local networking.

### Avoid scanning the whole extraction corpus for new chunks

The regression test initially failed: adding a new empty chunk visited an
unrelated concept's provenance. Three possible costs were checked: provenance
scanning, Rust edge rebuilding, and embedding calls. Only scanning was present.

Reconstruct the existing inverse contribution index once when opening the SQLite
store and maintain it during endpoint promotion. New chunks now append directly;
replacement finds that source's concepts through the same index. No new index
type or persistence schema was introduced. This also fixes incomplete retraction
after a resumed source receives additional assertions, and ensures promoted
endpoints disappear when their last supporting source is retracted.

`docs/experiments/chunk_update_benchmark.py` compares commit `2b80e8f` with the
current implementation using the same Rust binding. At 1,000 existing concepts,
processing 1,000 new empty chunks took median **128.47 ms before / 1.21 ms after**
(three runs). Both arms made zero embedding calls and zero Rust edge rebuilds.
This isolates bookkeeping and is not an end-to-end LLM throughput claim.

Validation: **381 Python tests pass**, including native graph consistency after
source replacement, reopen, and removal of promoted endpoints. Rust source and
the installed binding are unchanged in this pass.

## Model comparison and decision

`docs/experiments/compare_local_models.py` records the exact extraction prompt,
cases, model settings, raw replies, token usage, finish reason and timings. It
uses the project's HTTP payload construction and current normalization/Rust
resolver. Models run sequentially; warm-up is recorded separately. Approximate
entity merging is disabled so this measures extraction and endpoint retention.

The initial ten synthetic cases reuse the preceding development examples. They
are a comparison set, not evidence of production accuracy. Direct ComfyUI probes
additionally exercise the actual local safetensors files through ComfyUI's own
loader and generator without starting a UI server. Serving formats and generation
limits differ, so those probes are compatibility checks rather than equal-runtime
speed comparisons.

**Use the installed `gemma4:latest` with structured extraction as the next local
baseline.** This is a recommendation from small development checks, not a claim
of production accuracy. Keep `qwen3.5:4b-q8_0` as a low-memory alternative;
its instruction-injection failures make it a weaker default for unattended work.
The global model default is unchanged, so installations do not unexpectedly
require this machine's model tags.

The initial ten cases gave Gemma 4 10/10 usable replies, 9/9 selected relations,
2/2 selected requirements, and no selected forbidden claims. Qwen 3.5 4B was
faster in unconstrained generation but emitted an injected Moon claim. Actual
ComfyUI Qwen 3.5 4B BF16 and Gemma 4 E4B FP8 files both generated usable extraction
replies through ComfyUI's loader. On their five-case compatibility probe, their
medians were 3.08 and 7.59 seconds respectively. They did not demonstrate an
advantage over the installed Ollama variants; keep ComfyUI as an optional source
of weights, without making its image/video runtime a GRAP dependency.

Six additional synthetic cases check permissions versus obligations, superseded
drafts, quoted malicious instructions, descriptive context, and separating two
entities' requirements. The interrupted comparison was resumed with only its two
missing requests. Results with unconstrained JSON:

| Installed model | Usable replies | Selected relations | Selected requirements | Forbidden claims | Median seconds |
|---|---:|---:|---:|---:|---:|
| Qwen 2.5 14B Q4 | 6/6 | 5/5 | 4/4 | 1 | 2.89 |
| Qwen 3.5 4B Q8 | 5/6 | 4/5 | 4/4 | 0* | 1.09 |
| Qwen 3.5 9B Q4 | 6/6 | 5/5 | 4/4 | 0 | 2.34 |
| Gemma 4, local tag | 6/6 | 5/5 | 4/4 | 0 | 1.46 |
| GPT-OSS 20B | 6/6 | 5/5 | 4/4 | 1 | 1.74 |
| Qwen 2.5 32B Q4 | 6/6 | 5/5 | 4/4 | 0 | 17.03 |
| Thinkingcap Qwen 27B Q4 | 6/6 | 5/5 | 4/4 | 0 | 24.89 |

*The Qwen 4B malformed reply contained a forbidden entity but was rejected by the
parser. Zero retained forbidden claims therefore does not mean a correct reply.
All models retained the four specifically checked obligations without the
specifically checked optional/cross-entity requirements. These are selected
checks, not an exhaustive measure of precision or recall.

The larger models did not improve these checks enough to justify their observed
latency. This is a deployment comparison, not an isolated parameter-count study:
Thinkingcap's saved preset specifies `num_ctx 32768`; the smaller-model runs used
a 4096 context, and Thinkingcap was observed partly resident on CPU. A smaller
context could change its memory use and latency. Do that controlled experiment
only if real-domain failures give a reason to prefer that model.

## Structured generation experiment

Ollama documents JSON-schema response formats through its
[compatible chat API](https://docs.ollama.com/api/openai-compatibility) and
[structured-output support](https://docs.ollama.com/capabilities/structured-outputs).
Tested both plain and schema-constrained generation using all 16 cases, the same
prompt, temperature 0, thinking disabled, and a 2048 output-token cap:

| Model / mode | Usable | Selected relations | Selected requirements | Forbidden claims | Median seconds |
|---|---:|---:|---:|---:|---:|
| Qwen 3.5 4B / plain | 15/16 | 13/14 | 6/6 | 1 | 0.99 |
| Qwen 3.5 4B / schema | 16/16 | 14/14 | 6/6 | 1 | 0.83 |
| Gemma 4 / plain | 16/16 | 14/14 | 6/6 | 0 | 1.45 |
| Gemma 4 / schema | 16/16 | 14/14 | 6/6 | 0 | 0.81 |

A second Gemma run reversed mode order: schema median **0.785 s**, plain
**1.4395 s**. Schema retained the same selected checks. The plain repeat
incorrectly turned a Romanian description into a requirement in one case.
Temperature zero did not guarantee identical output across requests.

Schema mode roughly halved Gemma's median output-token count (186.5 to 92.5 in
the first comparison). The speed difference includes shorter generated output;
it is not evidence of twice the token-decoding throughput. The tiny cases,
warm server/cache, fixed case order and limited repeats constrain generalization.
Schema validity also did not stop Qwen from turning the injected Moon sentence
into a claim, with a spurious requirement attached to it.

## Implemented product changes

- `GRAP_KEE_STRUCTURED_OUTPUT=1` opts extraction into the measured schema when
  the provider supports configuring it. Both batch and GUI streaming extraction
  use it. The provider is copied with request-specific settings; unrelated calls
  and concurrent users of the original provider are unchanged. Unsupported HTTP
  servers report their error through the existing failed-chunk path. Stub/custom
  providers without this capability retain their existing interface.
- Source receipts record the response format. Schema, quote and semantic checks
  remain separate: a literal quote proves where text came from, not that an
  extracted assertion follows from it.
- Explicitly truncated/filtered/tool-call replies cannot commit a refresh, even
  if their content contains valid JSON. Streaming also requires a completion
  marker. Failed refreshes preserve previous claims and stay pending; repeating
  the same token-limited request is not treated as a transient HTTP retry.
- The GUI used to normalize streamed replies without source text and could
  continue the wizard after failed chunks. It now shares the batch reply
  validation, exports checked evidence, reports incompleteness, and stops before
  automatic wizard adoption when any chunk failed.
- The comparison harness checkpoints through file replacement, refuses accidental
  overwrite, and validates prompt/cases/settings before resuming. It skips completed
  calls, retains failed attempts, and reports untested empty-result behavior as
  null instead of claiming success.

The full application/GUI suite passed **406 tests** after the structured/streamed
changes. A subsequent missing-stream-marker regression and comparison-resume
test also pass (24 focused checks including provider tests). The real local
Gemma/Rust/SQLite smoke run processed four sources in 2.58 seconds with no pending
chunks, then reopened unchanged inputs with **zero further LLM calls**. It uses
hash embeddings and disables approximate entity merging; it does not assess
semantic deduplication quality. Rust engine source is unchanged in this pass.

Enable the measured configuration in the shell that launches GRAP:

```powershell
$env:GRAP_LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:GRAP_LLM_MODEL = 'gemma4:latest'
$env:GRAP_LLM_EXTRA_BODY = '{"reasoning_effort":"none"}'
$env:GRAP_KEE_STRUCTURED_OUTPUT = '1'
```

Changing model or settings does not automatically refresh unchanged source
receipts. Use the existing explicit refresh for a deliberate re-extraction, or a
separate work directory for comparing candidates. Completed model tests unload
their resident models; the service and model files remain available.

## Next product priorities

1. **Measure ontology usefulness on real documents.** Freeze a small reviewed
   Romanian-law and engineering corpus that was not used to tune the prompt.
   Label relation direction, negation, obligations, quotations, entity identity
   and source revisions. Evaluate the final Rust-backed merged graph and resulting
   assessment gaps, not just JSON parsing or how many nodes were produced.
2. **Spend model work on changed or unresolved sources.** Keep the existing
   successful-source cache, names-only graph context and explicit refresh.
   Compare chunk size and actual prompt token counts before increasing context
   or concurrency. The local 4K context needs testing on real long chunks: an
   output schema cannot recover source text silently truncated by a server.
3. **Keep uncertain claims reviewable.** Prioritize source-linked review of
   obligations and contradictions. A second model should be evaluated on these
   unresolved cases before adding automatic cascades. Agreement between models
   is not proof; multiple fast wrong proposals can be worse than one visible
   uncertainty. Preserve the distinction between descriptive relations and
   mandatory assessment criteria throughout the product.
4. **Benchmark entity merging next.** Extraction checks intentionally disabled
   approximate merging. Use the installed BGE-M3 and Qwen embedding model only
   in a separately scored alias/non-alias experiment with Rust retrieval and
   merge thresholds. A faster generator is not useful if the resolver merges two
   distinct legal acts or organizations. Do not replace embeddings from file
   size or model reputation alone.

These priorities do not require a new orchestration service, model router,
ComfyUI dependency or general ontology language. Keep the LLM responsible for
proposals, the Rust graph for retrieval and graph operations, and the harness
for provenance, resumability and measured acceptance.

## Reproducible artifacts

- `experiments/installed-model-inventory-2026-09.json`: installed files/tags.
- `experiments/local-model-comparison-2026-09.json`: original ten-case comparison.
- `experiments/comfy-*-scored-2026-09.json`: direct ComfyUI probes.
- `experiments/local-model-check-comparison-2026-09.json`: completed six-case run.
- `experiments/local-structured-comparison-2026-09.json`: four arms, 16 cases each.
- `experiments/local-structured-repeat-2026-09.json`: reversed Gemma arms.
- `experiments/local-structured-pipeline-smoke-2026-09.json`: persisted pipeline check.

Reports include prompts, fixtures, raw model output and usage/timing information.
For a new run, set `PYTHONPATH=mcp-server` and run
`docs/experiments/compare_local_models.py` with `--models-config`, `--cases` and a
new `--output` path. Supply `--resume` only to continue that same configuration.
