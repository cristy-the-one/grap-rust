# Review Romanian legal extraction against complete sources

The console now has a dedicated **Legal accuracy review** page at `/legal-review`.
It uses the same application operations as MCP and batch callers. It is a human
validation workflow, not a claim that the corpus has already been validated.

## Start and use it

From the repository root:

```powershell
.\aspice-gui\scripts\run-legal-review.ps1
```

Open [Legal source review](http://127.0.0.1:8766/legal-review). The launcher uses
the repository's `.venv` when present and the workspace `pilot-law-ro/review`.
Ollama is needed only to process documents, not to inspect or annotate stored results.
The model profile must already exist; this workflow downloads no model weights.

1. Choose a campaign, or create a sample from a corpus folder. A blank seed creates
   a random seed that is saved. Reusing the seed and same file names repeats the
   document selection; the manifest hash identifies their actual source bytes.
2. **Process sample** spends the selected batch budget, one request at a time.
   **Stop after current batch** finishes the current request, then stops. The next
   run reuses saved batches. Each request has no transport retries and a 90-second
   timeout. Closing the browser does not cancel an already submitted request.
3. Click **Start reviewing**, then select a document. Read its full source beside
   the claims. **Show evidence** and **Show provision context** locate their exact
   passages. Claims follow source order. Expand **Extracted qualifications** to
   inspect the recorded conditions, exceptions, dates and scope.
4. Enter your reviewer name and assess each claim's **Rule type**, **Complete
   subject**, and **Conditions & scope** as correct, incorrect or uncertain.
   Record suggested corrections and reasons where useful. Corrected subject text
   must occur in the claim's statement. Corrections remain annotations.
5. Select any missing rule in the source and click **Add selected passage as a
   missed rule**. Explain what was omitted. Source offsets, including Romanian
   characters and Unicode outside the basic multilingual plane, are checked when
   saving. Zero-claim documents can still have omissions and need full-source review.
6. **Save draft** works before the entire document is assessed. After checking the
   whole source for omissions and assessing every claim, tick the completion box
   and **Save review**. Uncertain judgments are allowed and remain uncertain in counts.
7. **Export evidence & review** downloads the frozen population manifest, sampled
   source text, pinned receipts, current annotations and their version history.

## What is frozen and measured

Every recursively discovered JSON file is eligible. A seeded SHA-256 ordering
selects documents without replacement; malformed or unreadable selected files are
retained as failures, never silently replaced. The manifest records every file's
relative path and byte hash. Sampled bytes are copied into the workspace before
processing, so later edits to the original corpus do not change the review target.

Each document pins its first complete receipt. Partial work contributes no current
reviewable claims. Documents exceeding the explicit 100,000-character limit remain
deferred. A changed extractor version requires a new campaign for unfinished work;
old pinned receipts stay readable. The default review generation settings match the
tested Gemma path: temperature zero, 2,048 output tokens, reasoning disabled.

Review edits use a version check to reject stale browser submissions. All saved
versions survive. They neither rewrite model receipts nor promote source claims
into trusted legal conclusions or merged entities in the graph.

The dashboard separates population size, sample size, completed extractions and
completed human reviews. It counts correct, incorrect, uncertain and unreviewed
judgments, plus reported omissions. **It does not estimate corpus-wide accuracy.**
Claim-only review cannot measure omitted rules; full-source checks are necessary.
Neither source grounding nor reviewer agreement establishes present-day legal validity.

For a defensible population estimate, finish the randomly selected document reviews
without replacing inconvenient cases, define the target measure and review rubric,
and obtain independent legal review of uncertain cases. Documents are the sampling
units: claims within one document are correlated, so treating every claim as an
independent random trial would overstate statistical precision. A census campaign
can select the entire folder by requesting at least its file count (up to 5,000).

## Prepared first campaign

On 2026-09-07 the `real-data-law-ro` population contained **2,092 JSON files**.
The saved seed `ro-law-review-2026-09-07` selected 24 documents before generation.
Eight local Gemma batch requests completed seven sources with 17 proposed claims;
17 sampled sources remain unprocessed. No human judgments were entered.

Three completed documents returned zero claims: `19584.json`, `18044.json` and
`39321.json`. They are visible in the review queue and are useful places to inspect
omissions; zero output is not recorded as an accuracy success. The initial
[manifest, source receipts and counts](experiments/legal-review-initial-sample.json)
are committed for reproducibility. This is preparation evidence, not a scored result.

The review page and source/receipt data remain usable after the LLM is unloaded.
The prepared campaign is stored in `pilot-law-ro/review`; runtime data is ignored
by Git. Back up that workspace to retain review history and frozen source copies.

## Validation and next use

540 tests passed across the application, GUI, experiment scoring and native Rust
integration. New tests cover sample reproducibility, failed-file retention, frozen
sources, immutable receipts, versioned edits, cross-workspace isolation, invalid
annotations, uncertainty, partial batches and extractor-version changes.

Browser checks used an isolated test workspace: save/reopen, uncertain judgments,
Unicode omission selection, literal HTML-looking source text, multi-batch completion,
source-order presentation and real-corpus reading layout. Test annotations were not
copied into the user's campaign. The model was unloaded after preparation.

The useful next input is your saved review evidence. It can identify recurring
extraction errors and supply independently checked regression references before
expanding cross-document inference, temporal applicability and entity merging.

Full generic KEE remains available separately. See the
[measured full-path timing and ontology assessment](full-kee-timing-2026-09.md).
