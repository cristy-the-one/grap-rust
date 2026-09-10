# A cheaper first stage for the Romanian-law collection

This pass implements a usable legal-first workflow, fixes a planning bottleneck,
and removes repeated context from storage. It does **not** establish that full
ontology discovery is solved at corpus scale. The first-stage model can still
misclassify assertions; unresolved work remains explicit.

## Measured collection and practical runtime

The actual collection contains 2,092 JSON documents, 22,496,879 source-text
characters, 78,366 planned statements and 320 explicit definitions. There is
only one duplicate whole-document text. Exact statement repetition is 10.5%;
only 2,891 statements (3.7%) have both a kind and subject that existing syntax
rules independently determine. Deduplication or deterministic labels alone
cannot deliver an order-of-magnitude improvement here.

The fast route plans 7,724 batches, of which 7,723 still need some generation.
It sends 75,397 units for labeling. Sixty oversized units across 37 documents
remain in the review queue, with complete source evidence; they are not
truncated or counted as successfully classified.

Six whole documents were selected at approximately the 10th, 30th, 50th, 70th,
85th and 95th document-length percentiles. The final pilot produced 112 proposed
claims and 97 review-queue entries in 69.62 seconds, using 21 batches. All six
documents have accepted receipts; that status means the first-stage operation
completed, not that their legal interpretation is complete. Repeating the same
command reported six unchanged documents and zero extraction attempts.

| Scenario, assuming the same batch mix and sustained throughput | Label-first estimate |
| --- | ---: |
| Current collection, 7,723 model batches | About 7.1 GPU hours |
| 20 times that collection, approximately 41,840 files | About 142 GPU hours / 5.9 continuous days |

These are throughput extrapolations from a small stratified pilot, not deadlines.
They exclude deeper ontology discovery, resolution of queued ambiguities,
oversized-unit processing, and any additional review cycles. Future years may
have different document sizes, consolidation patterns and table formats. Laptop
thermal behavior and other GPU use can also change sustained throughput.

## What changed

**Planning.** At every candidate sentence boundary, the old parser copied and
searched the entire growing article prefix just to check an abbreviation suffix.
It now inspects a bounded suffix. Actual document `50738.json` exceeded a
five-second test limit before the fix and planned in approximately 0.14 seconds
afterward, producing 3,299 units. The corpus inventory retained exactly the same
statement, definition and uniqueness counts. Inventory wall time fell from
90.67 to 13.45 seconds, but that latter comparison also includes reduced report
checkpoint frequency; the isolated document test is the cleaner parser result.

**Label-first processing.** The new explicit fast extractor requests labels and
literal subjects instead of generating a full entity ontology for every chunk.
Kinds and subjects already fixed independently by existing syntax validation do
not get sent for redundant labeling. Complete statements, conditions, exceptions,
dates and source spans remain available. Existing normal KEE defaults are not
changed.

**Work still to do.** Model nonassertions, fact labels containing modal wording,
and oversized units are retained as a machine-readable `review_queue`. They also
get source-linked review nodes in the exported Rust graph. These signals are
not a complete uncertainty detector: a confidently wrong non-modal fact can
still escape them. The queue is an input to deeper work, not a claim that a
smaller model output preserved every meaning.

**Storage.** Repeated `context_evidence` quotes are stored as offsets against the
revision's complete source text. Public claim/receipt APIs reconstruct the same
quotes; old records with embedded quotes remain readable. Batch checkpoints use
the compact representation too. The planned corpus would otherwise repeat
approximately 4.07 billion characters of article context across units. This is
a planning-level duplication count, not measured existing database size.

On the pilot's 112 claims, serialized claim data falls from 4,078,908 to 158,106
bytes (96.1% less). The actual exported snapshot falls from 21,547,764 to
1,305,186 bytes (93.9% less). Snapshot manifests declare `context_encoding`;
consumers reconstruct context quotes from the linked revision's `source_text`.
The Rust graph still contains the complete source, full rule text and source
relationships. This removes duplicate payload, not legal context.

## Tables and TSV

The new parser recovers only explicit boxed grids whose row delimiters and
column count can be checked without guessing. It recovered nine tables in seven
documents, containing 355 physical rows. All cell spans match the original
source. TSV takes 19,980 characters versus 45,194 characters of boxed layout.
Recognized tables bypass the model in the fast route and become source-linked
table and row nodes, alongside TSV exports.

These are **physical grid rows**, not inferred semantic records. Wrapped cells
are not merged, blanks are not filled, and headers/units are not guessed.
Malformed grids and already-flattened tables remain outside this conversion.
In particular, many tabs in the corpus are amendment-note indentation, not
column delimiters. Turning all whitespace into TSV would corrupt those passages.

An actual recovered example is
[17126-0.tsv](experiments/corpus-table-tsv/17126-0.tsv). It preserves the criteria
and contents columns, including continuation rows. This is a concrete starting
point for future header/row interpretation, not an invented table from prose.

## Quality evidence

Label-first extraction took 10.36 seconds on the existing 12 development targets
and retained all 12 selected target answers, versus about 55 seconds for the
earlier richer extraction. This is a different scope, not proof of a fivefold
speedup for equal ontology quality. Examining additional statements found five
label differences, including permissions labeled as facts and tariff assertions
labeled `other`. The review queue explicitly captures these observed cases;
it does not prove there are no other errors. The whole-document pilot has not
received exhaustive legal-semantic annotation.

Useful first-stage operations include filtering proposed obligations and
prohibitions while retaining their exceptions, looking up explicit definitions
within their source, and retrieving table cells through row/table/source links.
Full cross-act amendment reasoning, dependable entity merging and discovered
ontology relations still require further processing and validation. This route
provides cheaper, inspectable input to that work; it is not the final knowledge
system the user ultimately wants.

## Run and resume

From the repository root, with Ollama running and the existing model installed:

```powershell
$env:PYTHONPATH = 'C:\dev\grap-rust\mcp-server'
.venv/Scripts/python.exe -m grap_mcp.kee.corpus real-data-law-ro --database .logs/legal-corpus.sqlite --batches 100
```

Repeat that command to continue from committed batch checkpoints. `--batches`
is a planned-batch budget, not a time limit. A document publishes its receipt
after all its eligible batches finish; oversized units remain explicit queued
work. No source file is edited. Interrupted and unchanged-work behavior use the
existing durable store.

To also export a Rust graph and per-table TSV files, add
`--export .logs/legal-corpus-snapshot-001`. Each export directory must be new;
this is checked before generation. The CLI's database is separate from the
running GUI unless explicitly configured to use its store. This pass did not
restart or reconfigure the GUI.

Validation: 637 tests passed across MCP, GUI and experiment suites, including
resumption with compact context, quote reconstruction, table-cell offsets,
malformed-grid rejection and preservation of oversized queued passages. The
final export adjustment was additionally checked by the nine fast-route tests
and a real pilot snapshot export. Benchmark models were unloaded afterward.
