# Qualified rules survive KEE extraction

Legal-rule protection is automatic in normal folder extraction, persistent
Build / Grow, and the streaming extraction wizard. It works with both legacy
and statement chunking. There is no new switch to enable it.

## What changed

The reader identifies Romanian legal source units in the **full document** and
supplies each entity chunk with the complete intersecting units. An exception
outside a fixed-size chunk is therefore still available to its rule classifier.
The model returns kind and literal governed subject; code retains the original
statement, conditions, exceptions, context and character offsets. This reuses
the existing legal-rule validator rather than creating another interpretation
engine or database. These records remain proposals, not declarations of current law.

The normal entity request also asks for these labels. Missing labels, unresolved
subjects and unsupported entitlement labels get at most one focused,
classification-only recovery request per chunk. Failure preserves the complete
statement and exposes unresolved coverage instead of discarding the rule.
Extra model fields are ignored; text and offsets still come exclusively from
the source. Oversized units remain whole and unresolved rather than being cut
to fit a classification request.

Flat relations touching legal propositions, and their flattened requirements,
are held as deferred proposals. They cannot silently turn an exception into an
unconditional edge. Article-attached model output is also retained for inspection,
while article labels remain excluded as global entity identities. Thus a correct
rule attached to “Articolul 14” survives without merging unrelated Article 14s.
Metadata and entity discovery remain available, alongside the qualified rules.

Rules are stored in the existing extraction receipts and exported as
`qualified_legal`, including unresolved coverage and deferred proposals. Stable
source revision/span identities deduplicate rules crossing multiple chunks;
conflicting labels remain visibly conflicted. Rule receipts survive WorkingGraph
save/reload and retraction. They are not converted into unqualified legal edges
in the Rust entity projection.

Source revision and extraction version participate in legal-chunk invalidation.
Changing an exception outside an unchanged entity prefix re-extracts that source
document's chunks. Older legal extractions are upgraded on their next Build /
Grow run, so the first resumed run can perform additional work. No full-corpus
LLM run or migration of the user's existing workspaces was started here.

## Verification on real documents

The original seven reference targets and five additional targets were checked
with local `grap-legal-gemma4`, the same 800-character entity chunk sizes, and
both chunking policies. The additional set includes permission, prohibition,
applicability exclusion, negative financial facts and a long obligation.

| Stage | Legacy | Statements |
|---|---:|---:|
| Original seven: complete statement + correct kind/subject | 7/7 | 7/7 |
| Additional five, before focused recovery | 3/5 | 5/5 |
| Additional five, after bounded recovery | 5/5 | 5/5 |

Every scored target retained its qualifications and exact source spans through
save/reload. The two additional legacy errors were labels (“right” instead of
“permission”/“fact”); the focused recovery corrected them. Some neighboring,
unscored statements still have unresolved labels or subjects, and are visible
as such. This is a 12-target development regression, not a corpus-wide accuracy
estimate or independent lawyer adjudication. The metric now evaluates a complete
source-preserved proposition plus labels, not whether flat triples reconstruct it.

There were 29 primary model requests. Recovery receipts record the additional
labeling calls separately. Primary replies were retained; final parser fixes
were replayed from those replies, without presenting replays as fresh inference.
The full-corpus planning check visited all 2,092 JSON documents and 77,575 planned
units/definitions in about 97 seconds before the final abbreviated-heading fix;
it validated ordered span indexes, not legal semantics. Planning uses interval
indexes so chunk lookup does not scan all document units repeatedly.

Evidence and reproducible scorers:

- [Original seven: live replies](experiments/qualified-rules-results-2026-09.json)
  and [final persistence audit](experiments/qualified-rules-audit-2026-09.json).
- [Additional frozen cases](experiments/qualified-rules-additional-cases-2026-09.json),
  [live replies](experiments/qualified-rules-additional-results-2026-09.json),
  [recovery receipts](experiments/qualified-rules-additional-repairs-2026-09.json)
  and [final audit](experiments/qualified-rules-additional-audit-2026-09.json).
- [Replay/scoring script](experiments/score_qualified_rules.py) verifies source
  fingerprints, target kind, literal subject, preserved qualifiers, deduplication
  and save/reload. [Recovery runner](experiments/repair_qualified_labels.py)
  exercises the same bounded recovery used by production extraction.

## GUI

Refresh the console, open **Persistent ontology**, and use **Qualified legal
rules** to search and expand complete statements with their source locations.
**Load saved rules** reads the selected persistent graph directory without
loading either an LLM or an embedding model. Download includes the complete
records and deferred proposals.

A real-output preview is available at
`C:\dev\grap-rust\.logs\qualified-rules-preview`: enter it under **Persistent
graph dir**, then click **Load saved rules**. It contains the audited statement-mode
extractions and neighboring source units: 31 records, including two unresolved
neighboring records. Search `secretul` to inspect the secrecy exception. It is
a scoped preview, not the full corpus. The user's review workspace is unchanged.

The dedicated legal-rule classifier is now version 9 (a passive-prohibition
subject fix). Existing frozen review results remain readable; an unfinished
campaign created with an older extractor still requires a new campaign to run
new extraction, preserving the existing version-consistency safeguard.
