# Gemma runtime configuration: evidence and experiment boundaries

Research date: 2026-09-09. Primary-source review for the local Ollama 0.33.3
Gemma legal extractor. This note records mechanisms and hypotheses, not measured
improvements. Local controlled trials are recorded separately.

## Warming and priming are different operations

An empty native request can preload weights; `keep_alive` controls residency.
This removes loading from the next request's critical path. It does not perform
training or make later independent requests remember legal examples. The
documented default residency is five minutes. A bounded session duration is
preferable to indefinite residency when extraction and embedding compete for
GPU memory. [Ollama FAQ](https://docs.ollama.com/faq)

Pinned 0.33.3 confirms that `/api/embed` with `input: []` schedules and obtains
the runner **before** returning empty embeddings. Positive `keep_alive` reaches
that scheduler; this is a load-only embedding warm-up without synthetic text.
Likewise, empty `/api/chat` messages return `done_reason: "load"` after runner
scheduling. Options merge server defaults, model generation defaults, model
options, then request options. An explicit model `num_ctx` therefore applies to
empty chat preload; otherwise automatic context selection may apply. Warm-up
should use the same model and load-affecting request options as real extraction
to avoid a subsequent reload. Neither empty path evaluates task tokens, and the
empty embedding response omits phase timing metrics.
[Pinned native handlers and options](https://github.com/ollama/ollama/blob/v0.33.3/server/routes.go)

Prefix caching reuses attention computation for matching input tokens. Reuse
requires the relevant prefix still to be available; it is not semantic memory
about a previous document. A stable instruction prefix followed by changing
document content is a useful experiment. A warm-up call containing examples
does not transfer those examples into an unrelated subsequent prompt. This
last conclusion follows from reuse being conditional on matching tokens, not
an update of model weights. [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

Consequently, measure three conditions separately: cold model, resident model
with an unrelated preceding request, and resident model with a matching prefix.
Charge preload/prefill cost to total batch time as well as reporting the latency
of the next user-visible request. Never count warm-up extraction output as gold
data or persist it into the production graph.

## Verify the transport before tuning

Ollama 0.33.3's OpenAI chat request struct accepts temperature, top_p, seed,
penalties, token limit, response format and reasoning controls. It does not have
native `options`, `top_k`, `min_p`, `num_ctx`, `num_batch`, or `keep_alive` fields.
Its conversion explicitly supplies **temperature 1.0 and top_p 1.0 when omitted**.
Thus a Modelfile alone does not guarantee those two values survive this route.
Use supported request fields explicitly and native options or a verified model
profile for other controls. `reasoning_effort="none"` maps to thinking disabled.
The compatibility response also exposes cached prompt-token counts when supplied
by the native response. [Pinned compatibility source](https://github.com/ollama/ollama/blob/v0.33.3/openai/openai.go)

Native API metrics distinguish model load, prompt evaluation and output
generation. Keep those measurements alongside wall time, output token count,
finish reason and repair count. An accepted HTTP request is not evidence that
an unrecognized parameter took effect. [Generate API](https://docs.ollama.com/api/generate)

## Gemma configuration is a quality experiment

Google's E4B card lists 4.5B effective parameters, 8B including embeddings, and
42 layers, consistent with the supplied local metadata. It recommends
temperature 1.0, top_p .95 and top_k 64. Thinking is controlled through the model
template; E2B/E4B differ from larger variants in disabled-thinking output tags.
Use the runtime's supported thinking switch and inspect rendered behavior,
rather than transplanting a larger Gemma template. Google's recommendation is
a candidate, not proof that sampling outperforms greedy extraction on Romanian
law. [Google E4B model card](https://huggingface.co/google/gemma-4-E4B-it)

Screen temperature 0, .2, and 1.0 with explicit top_p/top_k and repeat penalties
held fixed. Repeat nonzero-temperature conditions across seeds. Evaluate full
subjects, exceptions, scope and evidence, not just JSON validity or number of
repair calls. A zero-repair response can silently omit the difficult rules.
Increasing repetition penalties is especially questionable for a format that
must repeat legal terms and quote source text: test it separately if attempted.

Schema-constrained decoding directly addresses syntactic failures; Ollama
supports a JSON schema via native `format` and compatible `response_format`.
The documentation recommends lower temperature for consistency. That differs
from Google's general sampling recommendation and is a reason to compare both
on the actual task. Schemas cannot establish whether a legally qualified rule
was extracted faithfully. [Structured outputs](https://docs.ollama.com/capabilities/structured-outputs)

## Runtime knobs actually reach this backend

The pinned release serves GGML models through an upstream llama-server
subprocess, rather than the older Go ollamarunner. [Runner selection](https://github.com/ollama/ollama/blob/v0.33.3/llm/server.go)
Its build pins llama.cpp b10760. [Version pin](https://github.com/ollama/ollama/blob/v0.33.3/LLAMA_CPP_VERSION)

The adapter maps positive `num_batch` to both logical and physical batch sizes
(`-b`, `-ub`), positive `num_thread` to `-t`, KV type to both K/V cache arguments,
and Flash Attention to explicit on/off or automatic selection. It hardcodes
prompt caching on for generation. Therefore a public `cache_prompt=false`
extra field cannot be assumed to disable it. Confirm effective settings in the
runner command line/logs, particularly after options trigger a reload.
[Pinned llama-server adapter](https://github.com/ollama/ollama/blob/v0.33.3/llm/llama_server.go)

The release defaults include batch 512, automatic threads, top_k 40 and repeat
penalty 1.0 before model/request overrides; generic documentation defaults can
lag source. [Pinned options](https://github.com/ollama/ollama/blob/v0.33.3/api/types.go)

Context allocation consumes memory; check actual context and GPU/CPU placement
with `ollama ps`. Use measured prompt plus output requirements to compare 8K
and 16K, with truncation detected explicitly. A larger advertised context does
not by itself improve the accuracy of a short extraction request.
[Context documentation](https://docs.ollama.com/context-length)

The FAQ describes f16, q8_0 and q4_0 KV caches, with progressively lower memory
and possible precision loss; these are global server settings, and documented
quantized-cache support depends on Flash Attention. Keep f16 as the reference;
try q8_0 only as a measured memory/performance tradeoff. Do not assume halving KV
storage halves the entire model footprint, particularly with Gemma's shared and
sliding-window attention. [Ollama FAQ](https://docs.ollama.com/faq)

Changing cache state or batching can alter floating-point results. Upstream
explicitly warns that cached versus uncached evaluation can yield different
logits across batch sizes. Fixed seeds and temperature zero are therefore not
an end-to-end bitwise reproducibility guarantee. Randomize trial order, retain
raw outputs and compare semantic invariants across repetitions.
[llama.cpp cache caveat](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## Decision order

1. Verify effective configuration and cold/resident/prefix timing separately.
2. Compare decoding profiles while keeping prompt, schema and context constant.
3. Screen context and batch sizes, rejecting truncation and quality regressions.
4. Only then consider server-wide Flash Attention/KV changes, with effective
   backend settings confirmed and previous settings restored after experiments.
5. Promote a candidate only after full extraction, repair and graph-admission
   evaluation. Preserve unresolved subjects rather than improving the repair
   metric by suppressing difficult output.

The prior ingress profile attributed about 94% of its five-call native timing
to output decoding and very little to loading. On that already-resident workload,
preload or prefix caching has limited upside; fewer unnecessary generated tokens
or fewer semantically necessary repair calls is the larger opportunity.
[Previous local measurements](llm-ingress-profiling-2026-09.md)
