# Engine findings follow-up — September 9, 2026

This closes the functional follow-ups in the earlier engine improvement report
and records additional defects found while verifying them. The old report's
opening list described bugs already repaired; it now states that explicitly.

## Correctness changes and evidence

| Finding | Change | Regression evidence |
| --- | --- | --- |
| Saving unchanged text changes BM25 scores | Use live document/token totals for the whole index and one document frequency per query term across all segments and the active buffer | Flush, mixed buffer/segments, merge, deletion versus fresh index, vacuum, reload |
| Tied disk results depend on internal document ids | Resolve score ties by node id after translation, before the result limit | Replacing the lower node id no longer changes the winner |
| Phrase search accepts reversed or incomplete phrases | Both backends use one ordered matcher requiring every term at distinct positions | Reversed terms, distant tails, repeated terms, later viable intermediate occurrence, extreme gap |
| Long-document phrases disappear | Remove the memory backend's silent 256-position cap | Phrase after 300 occurrences of a common term remains findable |
| Memory-backed graphs reload with no searchable text | Rebuild the default text index from live node blobs when posting files are absent | Portable and mmap reload; deleted documents remain absent |
| Distributed traversal misses local edges and stronger paths | Follow both local and ghost edges; retain shorter routes that leave hop budget | Independent bounded-walk oracle on 20 cyclic graphs at five depths; mixed local/remote path and shortcut cases |
| Cross-shard properties disappear | Store named properties on ghost edges and apply required-property filtering | Property-filtered paths survive save/load; threshold applies per edge, not cumulative score |
| Sharded phrase traversal ignores its gap | Select phrase seeds before using the shared distributed traversal | Reverse-order seed excluded, valid descendant retained, zero-gap multi-term query empty |
| Ordinary inputs concentrate in one shard | Avalanche the ring hashes and encode the full shard id | Old routing gives `[100, 2800, 100]` for 3,000 test documents; new routing puts each shard between 10% and 60% |
| Rebalance fails to create target shards and can discard topology | Validate the plan and build a complete replacement before swapping it into use | Expand 2→3→5, contract 5→2, preserve text hashes, embeddings and qualified paths, reload; invalid plan leaves graph usable |
| Corrupt input becomes an empty graph | Propagate loading errors through Rust and Python instead of silently creating an empty replacement | Corrupt text manifest and ghost topology raise errors; files remain untouched |
| CLI deletion bypasses index maintenance | Use indexed mutation methods for deletion and edge addition | A real CLI subprocess deletes a persisted node; text and vector searches no longer return it |
| HNSW persistence helpers never run through the engine interface | Connect the trait methods to persistence helpers; correct double-subtracted live count and bounded/zero-limit handling | Save/load via `Box<dyn VectorIndex>`, deletion, live membership/count and zero-limit regression |

Named ghost properties are optional when reading older snapshots. Old snapshots
cannot recover properties that earlier versions already discarded. Named-edge
insertion is also available through Python's optional `properties` dictionary.

New graphs use routing version 2. Existing manifests without a routing version
retain version 1 when opened and saved. Explicit rebalancing upgrades routing;
opening a saved graph alone does not change its placement scheme.

Rebalancing reconstructs indexes from nodes, rather than moving postings without
their node mapping. A subsequent pass removed the full payload copy through immutable chunk sharing;
topology and indexes are still rebuilt, and node ids can change. See
[rebalance and HNSW follow-up](graph-copy-hnsw-2026-09.md). Rust's `execute_rebalance_with_mapping` and Python's
`execute_rebalance_to` return the old-to-new handle map. Python previously
returned `None`; callers retaining node handles must now apply that map. Invalid
plans fail before replacing the original graph. Segment suggestions remain
advisory; no unsafe segment-only migration is performed.

## Real-document layout evaluation

[Runner](experiments/evaluate_index_layout.py) and
[recorded results](experiments/index-layout-results-2026-09.json).

The experiment samples 96 JSON documents without replacement using a fixed seed,
from the sorted files in `real-data-law-ro`. It indexes their complete
`text_complet` fields: **1,010,728 characters**. Source file hashes are recorded.
It uses 14 queries and compares every returned node id and raw text score across
five checks: buffer versus tiered segments, single flush, segmented reload,
updates/deletions, and reload after those revisions. **All 70 query comparisons
match exactly.** Every query has at least one hit in the original sample.

This is an engine invariance check, not a legal relevance evaluation. It does
not establish interpretation accuracy, complete corpus coverage, or global
ranking equivalence across different shard counts. No model calls or production
graph mutations are involved; experiment graphs live in a temporary directory.

## Focused performance measurements

Windows x64 release build, Criterion, 10 samples, 100 ms warmup and 200 ms target
measurement. The slow 10K-ingestion baseline required approximately 872 ms to
collect its samples. These are synthetic attribution probes, not corpus-wide
throughput measurements. Values are point estimates from the
[recorded runs](experiments/engine-followup-benchmarks-2026-09.txt). Baselines
were measured in the same working tree before each hot-loop change, rather
than against a complete rollback of the engine.

| Workload | Before | After |
| --- | ---: | ---: |
| Repeated three-term replacement, 1K documents | 2.621 µs | 1.389 µs |
| Repeated three-term replacement, 10K documents | 18.693 µs | 3.883 µs |
| Ingest 1K documents with common repeated terms | 1.768 ms | 1.003 ms |
| Ingest 10K documents with common repeated terms | 87.616 ms | 10.855 ms |

Replacement now updates postings for retained terms in place. Deleted terms
still require shifting a sorted posting vector. Ingestion uses the last posting
for repeated terms in the current monotonically assigned document, eliminating
the earlier scan through every previous document. The removed positional cap
increases memory use for frequent terms in long documents; complete matching
requires retaining that evidence. Interned historical vocabulary is still kept.

Reproduce the probes with:

```powershell
cargo bench -p grap --bench engine_workloads -- 'disk_common_ingestion|replace_document' --sample-size 10 --warm-up-time 0.1 --measurement-time 0.2
```

## Validation and limits

- Full Rust suites: **165** tests with no default features, **175** with all
  features, including doctests.
- The rebuilt Python binding passes the runtime regression suite, including
  native sharded traversal, properties, rebalance handle mapping, deletion,
  reload, and corruption handling. Full Python/API/experiment suite: **590 tests**.
  One existing Starlette deprecation warning remains. The release wheel is
  installed in `.venv`, the GUI has been restarted, and the release CLI rebuilt.
- Strict Clippy and workspace formatting checks pass. Mechanical lint cleanup
  covers size calculations, error construction, defaults, and test/CLI style.
  Nine existing positional public query methods have narrow, documented
  `clippy::too_many_arguments` expectations to retain caller compatibility.
  These are intentional exceptions, not redesigned APIs. The private fusion
  helper now takes the existing weight struct instead of separate scalars.
- The experimental HNSW backend remains approximate. Stored embeddings are
  verified directly in rebalance tests, rather than using approximate retrieval
  as an enumeration of storage. The default Python build uses exact vectors.
- Sharded hybrid search still computes rankings inside each shard. This pass
  repairs distributed text/phrase traversal; it does not claim global hybrid
  fusion or global cross-shard BM25 statistics.
- Proximity uses increasing **byte offsets between token starts** in both text
  adapters. `max_gap=0` matches no multi-term phrase. It is not a literal exact
  phrase API; tokenization and normalization still apply. Custom tokenizer
  configuration is not persisted by the memory-index fallback.
