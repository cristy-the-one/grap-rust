# Statement chunking: live accuracy evaluation

This is the historical v4 baseline. The subsequent
[qualified-rule fix and regression evaluation](qualified-legal-rules.md) changes
the extraction/retention path in both chunk modes; its results are separate.
Use commit `f03bd31` for the original runner. Saved baseline replies and judgments
remain unchanged.

The structural improvement did **not** establish an extraction-accuracy gain.
In this seven-target diagnostic, the current KEE prompt/model recovered two
complete target meanings with legacy chunks and none with statement chunks.
This is evidence against promoting statement chunking as an accuracy fix on its
own, not a population estimate or proof that legacy chunking is generally better.

## Frozen comparison

- Seven real passages from seven Romanian-law JSON documents: six previously
  labeled reference cases spanning obligations, exceptions, institutional facts
  and prohibitions, plus the known Article 59 truncation.
- References and a semantic rubric were frozen before this run. The assistant
  reviewed the outputs against those references; review was not blinded or
  independently adjudicated by a lawyer.
- Both arms use the original full-document chunk boundaries at target 800.
  Every chunk intersecting the target sentence is included; chunks are neither
  trimmed to the answer nor artificially shifted. Neighboring context differs
  as a consequence of the chunking policy. Three targets fit inside one legacy
  chunk, providing useful comparisons beyond known split sentences.
- Local `grap-legal-gemma4`, temperature 0, requested seed 20260908, reasoning
  disabled, 2,048-token output cap, unchanged `document-claims-v4` prompt.
  Installed Ollama model digest:
  `75afe35a779b9f5c3d7cee8346478f36bb7bb6e63c0c35d13f4894fc4fec2ec1`
  (Gemma4 8.0B Q4_K_M, local profile with 16,384 context).
- Stateless KEE calls isolate chunking from graph feedback and fuzzy merging.
  Arm order alternates. One generation per chunk; no seed-repeat stability
  study. This is not an end-to-end graph-augmented corpus benchmark.

## Findings

| Target | Legacy | Statements | Main observation |
|---|---|---|---|
| Bank-executor secrecy | Partial | Partial | Both lose subject scope/exception; statement output flattens conditional release into unconditional edges. |
| Forecast Directorate reorganization | Partial | Partial | New output turns formation through reorganization into an active “reorganizes” relation. |
| A.N.L. board allowance | Absent | Partial | New output recovers 20% allowance but loses the director exclusion; old complete article-based claim is filtered. |
| Railway technical obligation | Faithful | Absent | New output contains only act/publication metadata despite intact operative text. |
| Agricultural equipment sale prohibition | Partial | Partial | New output recovers the equipment list but loses acquisition scope and full-repayment limit. |
| Electricity connection without meters | Faithful | Absent | New raw output contains the complete conditional prohibition under Article 14, then normalization removes it. |
| Arbitration commission, Article 59 | Absent | Absent | New wrong article-endpoint relation is filtered; intact source alone does not recover the target. |

“Faithful” concerns the selected meaning, including governed subject and material
qualifications, in **normalized propositions**. “Partial” includes overbroad
claims; “absent” includes filtered output. Information only in an evidence quote
does not earn semantic credit. Awkward encoding alone is not a failure: the
legacy electricity relation preserves prohibition and condition despite placing
the action in a concept name and translating its predicate. This target score
does not certify neighboring claims or measure precision over all output claims.

| Mechanical measurement | Legacy | Statements |
|---|---:|---:|
| Model requests / successfully parsed fragments | 11 / 11 | 7 / 7 |
| Truncation or request failures | 0 | 0 |
| Normalized concepts + relations with exact quotes | 131 / 131 | 61 / 61 |
| Rejected nonliteral evidence items | 2 | 5 |
| Relations before Rust replay | 42 | 23 |
| Relations after Rust replay | 40 | 22 |
| Fully recovered targets | 2 / 7 | 0 / 7 |

All surviving claims have exact quotes, yet most target meanings are incomplete.
Quote matching is necessary evidence hygiene, not semantic validation.
The Rust-backed replay uses temporary graphs and `HashingEmbedder(64)` with
fuzzy merging disabled. It confirms that the main losses identified above occur
before graph insertion. Its edge counts are a mechanical check, not an accuracy
score; parked edges remain in the audit artifact. No production workspace changed.

Requests took about 114 seconds in total. Legacy includes the initial model load,
and prompt-cache reuse differs, so the per-arm timings are not a fair speed
benchmark. The fewer calls in this targeted probe must not be extrapolated to
the corpus: the earlier full-corpus structural measurement found 5.3% **more**
extractable chunks with statement mode. No table-filter skips occurred here;
the full-corpus table-coverage trade-off remains unevaluated semantically.

## Product decision

Keep statement mode opt-in. The next substantive work should preserve qualified
legal propositions through extraction and normalization:

1. Carry the governed action/subject, modality, conditions and exceptions as a
   single source-backed rule. The existing dedicated legal-rule extractor is
   the appropriate path to evaluate/integrate; flat ontology triples alone
   demonstrably lose these distinctions.
2. Keep article-attached propositions reviewable when they are rejected as
   entity graph nodes. Do not simply disable structural filtering: that would
   restore collisions between unrelated “Articolul 14” nodes and admit the wrong
   Article 59 relation. A scoped source reference and a separate rule record
   should preserve useful content without promoting it as an entity or truth.
3. Add a coverage check for operative statements that yielded only publication
   metadata or were lost entirely during filtering, then target recovery calls
   at those gaps. Silent successful parsing must not imply successful extraction.

These are conclusions from the evaluation, not changes deployed by it. No prompt
or parser was tuned on these seven outputs. Models were unloaded after the run.

## Reproduction and evidence

- [Frozen cases and exact source spans](experiments/chunking-accuracy-cases-2026-09.json)
- [Raw replies, settings, timing and normalized fragments](experiments/chunking-accuracy-results-2026-09.json)
- [Per-target semantic judgments and explanations](experiments/chunking-accuracy-judgments-2026-09.json)
- [Mechanical summary, artifact hashes and Rust replay](experiments/chunking-accuracy-audit-2026-09.json)

The runner checkpoints each response and resumes completed requests without
repeating them. It rejects changed fixture/settings/prompt identities. Use a
fresh result filename for an independent repeat; a fixed seed is not a guarantee
of identical output across backend/model versions.

```powershell
.venv/Scripts/python.exe docs/experiments/evaluate_chunking_accuracy.py run --fixture docs/experiments/chunking-accuracy-cases-2026-09.json --output .logs/chunking-repeat.json
.venv/Scripts/python.exe docs/experiments/evaluate_chunking_accuracy.py audit --fixture docs/experiments/chunking-accuracy-cases-2026-09.json --results docs/experiments/chunking-accuracy-results-2026-09.json --judgments docs/experiments/chunking-accuracy-judgments-2026-09.json --output .logs/chunking-audit.json
```

Semantic judgments belong to the saved responses. A new model run requires a
new semantic review, even if its inputs are unchanged.
