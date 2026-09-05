# Installed models and extraction performance — September 2026

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

## Model comparison in progress

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
