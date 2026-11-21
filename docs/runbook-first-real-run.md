# Runbook: the first real run (P-17, then GJ-06)

The owner runs this on their own Windows machine with the real corpus and a local model. Every
command below has been run end to end on synthetic data: `mcp-server/tests/test_rehearsal.py`
(DRY-01) builds the graph, generates a specification and judges a recording, using the same
scripts and flags. This runbook replaces the synthetic files with real ones and the stub model
with a local one. Nothing else changes.

Commands are PowerShell, run from the root of the grap-rust checkout with its venv active.

**Or run it in one go:** `scripts\first-real-run.ps1` does §1 to §5 and writes the §7 report
to `<run>\report.md`. Before anything runs, it refuses a run folder inside a git repository, a
model server that isn't on this machine, a model the server doesn't list, and a run id that
has already been used. Each step's full output goes to `<run>\logs\`. The report keeps only
the count and status lines, with their exit codes and durations. `-From generate` (or
`judge`) resumes an existing run, for example after the model server is fixed.

```powershell
.\scripts\first-real-run.ps1 -RunId p17-1 -ReqIf D:\corpus\spec.reqif -Dbc D:\corpus\bus.dbc `
    -Release R1 -ScopeIds REQ-A,REQ-B -Model <model id> -Recording D:\logs\drive.asc
```

---

## 0. Rules for this run

These hold for every step and every file the run produces.

- **No customer file and no run output goes into either repository.** The ReqIF, DBC and ARXML
  files, the recordings, and everything the run writes (graph, workspace, emitted documents,
  `specification.json`, `request.json`, `verdict.json`) stay under the run folder in §1, which is
  outside both repositories. Don't copy any of them into `C:\dev\grap-rust` or
  `D:\projects\signal-loom`, and don't paste them into an issue, a PR, a chat or a model prompt
  outside this machine.
- **The model is local.** `--provider local` calls the server at `GRAP_LLM_BASE_URL` on this
  machine. Don't point that variable at a hosted service. Requirement text goes into the prompt,
  and it must not leave the machine without written clearance.
- **The gold set stays where it is.** It is read from `C:\dev\grap-gold\`. Open sheets are read
  by default. The sealed sheets are read only by `score_gold.py --sealed`, and only once the
  open results are settled (§6).
- **What you can share** is what the scripts print: step lines, counts, statuses, the verdict
  counts line and the scorer's aggregates. None of the scripts print requirement text, prompts,
  replies or signal values. If any of them ever does, stop and report it as a bug.

---

## 1. Set up the run folder

One folder per run. `p17-1` is the run id used below; use a new id for every later run.

```powershell
$RUN = "D:\grap-runs\p17-1"
New-Item -ItemType Directory -Force "$RUN\in", "$RUN\graph-out", "$RUN\ws", "$RUN\spec-out", "$RUN\judge-out" | Out-Null
```

Copy the inputs into `$RUN\in`: the ReqIF file(s), the DBC file(s), any ARXML file(s), and
(for §5) one recording as `.asc`.

**Pick a scope before you start:** 3 to 10 requirements that you know well and that name
signals you can find in the DBC. The first run is for finding out what breaks, not for
covering the corpus.

---

## 2. Start the local model and check it answers

Start your model server; `scripts/serve-bonsai.ps1` starts the llama.cpp one. Then tell grap
where it is and which model to use:

```powershell
$env:GRAP_LLM_BASE_URL = "http://127.0.0.1:11434/v1"   # your server's /v1 address
$env:GRAP_LLM_MODEL    = "<model id your server lists>"
Invoke-RestMethod "$env:GRAP_LLM_BASE_URL/models" | ConvertTo-Json -Depth 4
```

The model id you set must appear in that list. The default model is `llama3.1`; if nothing
serves it, every model step fails with "Is the server running and is model … available?".

---

## 3. Build the graph

Build it twice, once with the stub and once with the local model. The comparison is the first
finding of the run.

```powershell
python scripts/build_automotive_graph.py --reqif "$RUN\in\<spec>.reqif" --dbc "$RUN\in\<bus>.dbc" `
    --release <release> --out "$RUN\graph-stub"
python scripts/build_automotive_graph.py --reqif "$RUN\in\<spec>.reqif" --dbc "$RUN\in\<bus>.dbc" `
    --release <release> --out "$RUN\graph-out" --provider local
```

- `--dbc`, `--arxml` and `--reqif` can each be repeated. An ARXML file keeps the release from
  its AUTOSAR schema. `--release` is used for files that state none, including a DBC whose
  `VERSION` is blank.
- A failing step stops the build and names itself (`step N (name) failed: …`).
  `structural_missing` above zero stops it at step 7.

**What to write down from each build:** the `step 4 (statements)` and `step 5 (mentions)`
lines.

**Why it matters.** A requirement reaches a signal only through a mention that matches the
signal's DBC id, `<Message>.<Signal>`, exactly (case-sensitive), or the last `/` segment of an
id. The stub marks no statements of its own. If your requirements name signals in prose ("the
brake pedal") rather than by id, `mentions_written` will be low, the context for those
requirements will hold no signals, and generation will fail with invented-name issues. That
failure is the truth about the corpus, not something to work around. Record it.

From here on, `graph-out` (the local build) is the graph used.

---

## 4. Generate the specification (P-17)

```powershell
python scripts/generate_test_spec.py --graph "$RUN\graph-out" --workspace "$RUN\ws" `
    --run-id p17-1 --scope-ids REQ-A,REQ-B,REQ-C --provider local --out "$RUN\spec-out"
```

- `--scope-ids` takes requirement ids (the ReqIF identifiers) or Requirement node keys,
  separated by commas. An id that matches no requirement is an error and nothing runs.
- **Success** prints `p17-1 <path to specification.json>` and exits 0.
- **Failure** prints `<status> n_issues=<n>` and exits 1. No `specification.json` is written,
  and the rejected last draft is not written to disk either. The issues are recorded as
  `procedure_check_failed` events on the run in `$RUN\ws`. Note the status and the count;
  they are the result.

Repeat with a new `--run-id` for each scope you try. Runs can share the workspace.

---

## 5. Judge a recording (GJ-06)

This needs a `specification.json` from §4, and one real recording of the same bus as the DBC.

Build loom-judge once, from the signal-loom checkout:

```powershell
cargo build --release -p loom-judge --manifest-path D:\projects\signal-loom\Cargo.toml
$LOOM = "D:\projects\signal-loom\target\release\loom-judge.exe"
```

Then:

```powershell
python scripts/judge_recording.py --spec "$RUN\spec-out\specification.json" --spec-id p17-1 `
    --log "$RUN\in\<recording>.asc" --dbc "$RUN\in\<bus>.dbc" --loom-judge $LOOM `
    --run-id p17-1 --workspace "$RUN\ws" --out "$RUN\judge-out"
```

- `--run-id` and `--workspace` must be the ones §4 used. An unknown run is refused before
  loom-judge starts.
- `--dbc FILE@CHANNEL` binds a DBC to one CAN channel. Without `@`, it applies to all of them.
- **Exit 0:** the verdict is imported as one `judge_verdict` event per case, and a counts line
  is printed. Share that line.
- **Exit 2:** loom-judge refused the request or the files; its own message is printed. Nothing
  is imported. A refusal is a finding: record it.
- **Exit 1:** loom-judge could not start, crashed, or the import refused the verdict (the
  verdict was for a different request, or the format is wrong). Nothing is imported.

`$RUN\judge-out` holds `request.json` and `verdict.json`. To look at a verdict case by case,
open the recording in signal-loom: it shows the same evidence the verdict was judged on.

---

## 6. Score the context and review the cases (P-17's remaining part)

```powershell
python scripts/score_gold.py --gold C:\dev\grap-gold\<automotive folder> --system "$RUN\spec-out\testpoints.json"
python scripts/export_testpoint_review.py --gold C:\dev\grap-gold\<automotive folder> `
    --system "$RUN\spec-out\testpoints.json" --out "$RUN\review.csv"
```

Fill the `verdict` column of `review.csv` (same, acceptable, wrong, missing) yourself. Score with
`--sealed` only after the open-sheet results are written down and nothing more will be tuned
against them. The first scores are the baseline; no threshold is set.

---

## 7. What to report back

Paste these into the chat. They contain no corpus content.

0. **The scorecard first.** The run's result is `verdicts=N of M cases (reviewed checks only)`: generated tests that
   reached a pass or a fail on the recording. A passed validation only lets a case in; on its
   own it is not a result. `cases_with_checks` shows how many cases the judge could test at
   all. `pass_already_held=K of P check passes` counts passes whose expected value already
   held before the trigger (loom-judge from SL-22 on): such a pass did not depend on the
   trigger, so it says little. `scripts\first-real-run.ps1` writes this block at the end of
   every run, including one that stopped. The headline counts pass and fail only over cases
   whose every check a person approved; review a check with
   `python scripts/review_checks.py --run <run> --approve <case>:<check>`.
1. The `step 4` and `step 5` lines of both builds (§3).
2. For each run id: the generation line, i.e. `p17-1 …` or `<status> n_issues=<n>` (§4).
3. The judge counts line, or the refusal message (§5).
4. Once §6 is runnable: the scorer's printed aggregates.
5. Anything that broke but isn't in this runbook, with the step and the exit code.

---

## Card RUN-03: write the testpoints document from a run

**[S].** Plain Python. No change to a validator, the resolver, `procedure_context.py` or any
fixture.

`scripts/generate_test_spec.py`: when the recorded status is `ok`, also write
`<out>/testpoints.json` next to `specification.json`.

- `needs` comes from `as_testpoints(projection, context)`, where `context` is the
  `fetch_context` output for the run's selected requirements. Use the same requirements the
  run used, not a second selection that could differ.
- Each row also gets six text fields, taken from the cases in the last draft whose `verifies`
  contains that requirement's id:
  - `preconditions`, `stimulus`, `expected_observable_result` and `pass_criterion` come from
    the `positive` cases: each field's distinct non-empty values, in case order, joined with
    `" | "`.
  - `variants` is the distinct non-empty `variant` values across all of those cases, joined
    with `" | "`.
  - `negative_cases` is the `stimulus` of each `negative` case, joined with `" | "`.
  - A field with no values is `""`.
- No `testable` field. The scorer then prints that figure as `n/a`, which is correct, since
  nothing produces it.
- The file is `json.dumps(obj, indent=2, ensure_ascii=False)` plus a newline. Print
  `format_summary(document)` after the existing success line. Print no row.

Tests (red first, each shown failing before it passes):

1. The rehearsal (`test_rehearsal.py`): `testpoints.json` exists, and the row for
   `REQ-BRK-001` has `needs` containing `BrakeCmd.BrakePedal` and `Lamps.BrakeLamp`, and
   `stimulus` equal to `"the pedal is pressed"`.
2. `score_gold.py --gold <tmp folder with a one-row testpoints.csv> --system <that file>`
   exits 0 and prints `testpoints_needs_recall`.
3. When the status is not `ok`, `testpoints.json` is not written.

Then change §6 of `docs/runbook-first-real-run.md`: remove the "Not runnable yet" note.
