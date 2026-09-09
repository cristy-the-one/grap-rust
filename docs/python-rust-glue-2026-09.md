# Python/Rust boundary: bounded ingestion and cooperative native work

Implemented September 9, 2026. Python continues to orchestrate the product; Rust
owns graph insertion, indexing and retrieval. No additional service or language
runtime is introduced.

## Changes

- Rust `add_nodes` inserts ordered records, reserves node capacity and invalidates
  PageRank once per batch. All supplied embeddings are checked for nonempty,
  finite, consistent dimensions before mutation. Python argument conversion also
  precedes mutation. Allocation failures are not rollback transactions.
- Initial ingestion consumes chunks in bounded batches instead of accumulating
  every corpus chunk. Incremental ingestion bounds embedding and native insertion
  batches too. File/chunk order, labels and unchanged manifest IDs are preserved.
- Python bindings release the GIL during batch insertion, save, text/hybrid
  traversal, vector search and vector compaction. Python argument/result conversion
  still holds the GIL; this is not an asynchronous API.
- Application evidence and ontology handles serialize native calls through one
  lock per handle. Waiting for that lock permits other Python threads to run.
  Cached method wrappers avoid retaining handles through self-reference cycles.

Raw `pygrap` handles retain PyO3's borrowing rules: simultaneous accesses involving
a mutation can raise borrowing errors. The application adapter serializes calls
through its shared handle; separate handles, processes, and multi-call application
operations are not made transactional. Synchronous native work on an event-loop
thread still occupies that thread even while other Python threads can run.

Ingestion still retains the file list and one source file's text/chunk list.
Incremental ingestion still retains each changed file's chunk/hash map. Earlier
successful batches can survive a later failure; no corpus-wide rollback is added.

## Measurements

Same local machine and CPython 3.13 release builds. Baseline is commit `e0ed59b`;
JSON results are in `experiments/glue-before.json` and `glue-after.json`.
`experiments/glue_benchmark.py` reproduces the workload without a model.

| Measurement | Before | After |
| --- | ---: | ---: |
| Python ingestion peak allocation, 128 files / 11,520 chunks | 10,438,622 bytes | 656,308 bytes |
| Other Python thread progresses during approximately 64 MB native save | No | Yes |
| Native save duration, single probe | 25.8 ms | 27.7 ms |

The ingestion measurement uses a sink graph to isolate Python buffering with
`tracemalloc`; it is not total process memory or Rust heap usage. The approximately
94% reduction applies to this workload, not arbitrarily large individual files.

Insertion medians over three runs, 4,000 nodes, batches of 128:

| Payload | Raw scalar | Raw batch | Serialized scalar | Serialized batch |
| --- | ---: | ---: | ---: | ---: |
| Approximately 24 characters | 4.21 ms | 4.19 ms | 5.24 ms | 4.21 ms |
| Approximately 800 characters | 80.55 ms | 79.89 ms | 82.30 ms | 80.00 ms |
| Approximately 800 characters + 256-dimensional embedding | 86.42 ms | 88.92 ms | 89.46 ms | 94.47 ms |

There is no consistent raw insertion speed improvement. Batching reduces wrapper
overhead for small records, but vector batches can be slower in this measurement.
The demonstrated benefits are bounded buffering, prevalidation and letting other
Python threads progress. This does not measure GUI latency or LLM inference speed.

## Validation

Passed: 173 Rust tests with default features, 190 with all features, and 595
Python tests against each of the exact and optional HNSW release wheels. Rust
formatting and strict core Clippy checks pass. Existing PyO3/Starlette deprecation
warnings remain.

Rust tests compare batch/scalar content, labels, embeddings, ordered IDs and search;
invalid batches must not insert partial records or consume IDs. Python tests cover
argument conversion, save/reload, GIL progress, concurrent shared-handle access,
prompt handle destruction and streaming embedding order.

`experiments/evaluate_index_layout.py` additionally compares scalar versus batched
insertion before and after reload on 96 sampled full Romanian-law documents and
14 queries. All comparisons match; source hashes and results are recorded in
`experiments/glue-real-documents.json`. These are retrieval invariance checks,
not evidence of improved legal interpretation or model accuracy.
