# Semantic extraction: subjects, rule types and measured limits

The legal extractor now uses `legal-rules-v7`. Models copy a subject phrase rather
than calculate token positions. Code requires the quote to occur exactly once within
its own statement, or uses an existing explicit-syntax match. An absent, ambiguous
or obviously invalid phrase leaves the subject unresolved. The complete source rule
and its context still survive; no fabricated replacement wording is accepted.

This fixes a measured weakness: the old contract could preserve every passage yet
return “Este”, “Direcţia” or an unrelated object as its subject. It also removes
numbering from the prompt and eliminates the token-offset arrays previously built
for every unit. Stored subject evidence still has exact full-source offsets.

Classification instructions now distinguish optional procedures from required ones.
Explicit indemnity wording can identify a right even when the same statement says
its beneficiaries are not employees. Negation checks apply to the operative wording,
so an earlier negative clause does not suppress a later entitlement.

## Reference set and scoring

[The frozen references](experiments/real-law-semantic-cases.json) contain 17 passages
from nine real corpus documents: 11 development cases from four documents and six
held-out cases from five different documents. Source byte hashes and exact passage
offsets are checked before generation. The annotations were committed in `0c37386`
before the model comparison. The extraction change was committed in `1e4d308` before
the held-out results were inspected; a subsequent internal allocation cleanup does
not change the extraction contract or scoring behavior.

These are **assistant-authored references, not independent expert legal validation**.
They were selected for specific subject/classification problems and are too few to
estimate corpus-wide accuracy. The held-out split tests transfer to different
documents within this exercise; it is now observed data for future development.

Each case specifies one primary operative kind, acceptable complete subject phrases,
and qualifications that must remain in the same statement. “Subject” can mean an
action or object governed by a passive rule, not necessarily its responsible actor.
Alternative exact subject phrases are allowed only where specified before generation.
Scope exclusions and consequences are classified as facts in this rubric; this
does not assert their present-day legal validity.

The evaluator separately counts:

- Correct primary kind.
- Complete subject matching a frozen acceptable phrase.
- Required qualification text retained in the same statement.
- Joint success on those three checks.
- Exact evidence grounding, missing results and extra claims.

Grounded text alone does not count as correct meaning. Empty output, duplicate
claims, or missing evidence cannot pass the corresponding checks. The qualification
measure proves text retention, not complete semantic interpretation of exceptions.

## Results

All runs used temperature zero, no retries, 2,048 output tokens and 16K-context
profiles derived from the already installed models. No extra weights were downloaded.

| Contract/model | Split | Kind correct | Complete subject | Joint correct | Qualifications retained |
|---|---|---:|---:|---:|---:|
| v6 Gemma | Development | 8/11 | 3/11 | 3/11 | 11/11 |
| v7 Gemma | Development | 10/11 | 11/11 | 10/11 | 11/11 |
| v6 Gemma | Held out | 6/6 | 1/6 | 1/6 | 6/6 |
| v7 Gemma | Held out | 6/6 | 4/6 | 4/6 | 6/6 |
| v7 Qwen 3.5 9B | Development | 10/11 | 9/11 | 9/11 | 11/11 |
| v7 Qwen 3.5 9B | Held out | 4/6 | 5/6 | 4/6 | 6/6 |

All these trials had exact evidence spans and no missing or extra claims. That is
separate from the semantic errors in the table. An intermediate v7 development run
is retained to show the limited prompt/classification refinement before the holdout.

Gemma remains the recommended local profile (`grap-legal-gemma4`). Qwen recovered
one additional held-out subject but made two kind errors, leaving joint accuracy
unchanged. Its held-out pass took 5.694 seconds versus Gemma's 3.703 seconds. These
are single observed runs, not a repeated throughput benchmark. The original Gemma
development baseline included model loading, so its total is not a fair speed ratio.
No automatic ensemble or global model-setting change was introduced.

Remaining Gemma errors are recorded rather than hidden:

- “Prezentele norme nu se aplică…” was classified as a prohibition instead of an
  applicability exclusion (development case 11).
- A sanctions clause selected a circumstance as subject, which was rejected and
  left unresolved (held-out case 14).
- A connection prohibition included its condition in the subject phrase rather
  than separating the governed action (held-out case 15).

## Complete-document integration and efficiency

The 41,244-character source 16918 was reprocessed through the public application
and the existing resumable ledger. The v7 upgrade started new work rather than reuse
v6 classifications. Separate processes generated three batches and then five;
the second reused the first three. The partial pass exposed zero current claims.
The completed receipt contained 47 proposals, with every claim/context/subject/
provision span checked against the original full document. Rust export reopened
with 48 nodes and 47 source links. More proposals are not an accuracy claim.

The eight requests contained 61,197 UTF-8 bytes of system/user text, versus 71,998
for the prior v6 run on this source: approximately **15% less request text**.
Source-only message bytes fell from 57,918 to 43,789. These are measured payload-text
sizes, not tokenizer counts or HTTP wire sizes. Longer copied subjects can increase
output size, so lower input size does not guarantee lower latency on every passage.

The new version deliberately invalidates old-version partial work and causes older
successful receipts to be processed again when selected. Historical receipts remain
available. The old wire format is not silently interpreted as the new one.

## Reproduce and inspect

- [Gemma v6 development](experiments/semantic-gemma-dev-v6.json),
  [v7 development](experiments/semantic-gemma-dev-v7-final.json).
- [Gemma v6 holdout](experiments/semantic-gemma-holdout-v6.json),
  [v7 holdout](experiments/semantic-gemma-holdout-v7.json).
- [Qwen development](experiments/semantic-qwen-dev-v7.json),
  [Qwen holdout](experiments/semantic-qwen-holdout-v7.json).
- [Live partial pass](experiments/semantic-v7-resume-pass1.json),
  [completed pass](experiments/semantic-v7-resume-pass2.json).

```powershell
$env:PYTHONPATH='C:\dev\grap-rust\mcp-server'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe docs/experiments/evaluate_legal_semantics.py --split dev --output .logs/new-semantic-dev.json
.venv/Scripts/python.exe docs/experiments/evaluate_legal_semantics.py --split holdout --output .logs/new-semantic-holdout.json
```

Use fresh output names. The evaluator records fixture and extractor hashes, raw
requests/replies, case scores and receipts. For the baseline, export
`51daea7:mcp-server/grap_mcp/kee/legal_rules.py` to a local Python file and pass it
using `--extractor-file`. The optional Qwen comparison profile is reproducible with
`ollama create grap-legal-qwen9b -f scripts/legal-qwen9b.Modelfile`.

**489 tests pass**, covering the application, GUI harness, experiment scoring and
native Rust integration. New tests check unique literal subjects, exact offsets,
nonemployee benefit clauses, malformed subject types and scoring false positives.
Rust source itself was unchanged. Test models were unloaded after the runs.

The next evidence need is broader expert-reviewed coverage of applicability,
consequences, compound rules and actor/action distinctions. The current benchmark
provides a repeatable regression check, not an unattended legal-reasoning guarantee.
