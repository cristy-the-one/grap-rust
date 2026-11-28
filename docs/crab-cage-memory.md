# Grap as Crab Cage search memory

Crab Cage needs cheap candidate gates and a rustc oracle. Grap belongs outside
that loop: index past source, diagnostics and outcomes to find evidence for the
next proposal. It should never decide that a candidate is correct.

The `crab_memory` example reads an existing run without modifying it:

```sh
cargo run --release --example crab_memory -- /path/to/crab-out "L0113 comparison"
cargo test --example crab_memory
```

It indexes `journal.jsonl` and the available `work/<hash>.rs` programs, links
candidates through shared diagnostic codes, and combines BM25 seed retrieval
with graph traversal. Results carry the run path, journal line, candidate hash,
source path and recorded status. Missing source files leave metadata searchable.
A crash fragment at the end is ignored; malformed complete lines are errors.
No model, embeddings, network service or new dependencies are needed.

Recorded `proven` means agreement on that run's cases, with that run's compilers.
Search relevance does not strengthen that claim. Re-evaluate retrieved regions
against the current nugget and prove any promoted candidate with rustc. Do not
copy an entire saved program into a replacement region.

This is a working integration example, not an automatic proposer or a persistent
memory service. It rebuilds its index on each invocation. Before integrating it
into proposal generation, measure solved tasks per wall-clock budget against
the same seeds and models without retrieval. Persist and incrementally refresh
the graph only if that experiment shows a benefit. Keep Grap out of Lust: its
indexing and extraction dependencies do not belong in a compiler's edit loop.
