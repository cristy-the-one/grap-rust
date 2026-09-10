# Selective thinking for legal repairs

## Decision

Do not enable selective thinking in production on this evidence. It increased
cost without a reliable coverage gain. Keep the bounded normal repair path.

## Design

Replayed the exact first responses from both prior 12-case baseline runs,
using their saved source context and runtime options. Each first response was
fed to the real finalizer twice: normal repair and thinking-enabled repair.
Arm order alternated between cases. Cases with no unresolved rules made no LLM
call. A third strategy replayed the normal repair's merged labels and attempted
thinking only on remaining unresolved units. The source statements, entity
extraction and graph context were held fixed; no production graph was written.

These are 12 development cases with two saved output realizations, not 24
independent cases or a held-out legal benchmark. Total times below combine the
saved first-call duration with measured repair time; they are estimates, not
fresh end-to-end job measurements. Preloading is excluded.

| Strategy | Run A total seconds | Run B total seconds | Repair calls A/B | Unresolved rules A/B |
| --- | ---: | ---: | ---: | ---: |
| Normal repair | 56.42 | 49.77 | 3 / 4 | 1 / 1 |
| Thinking instead of normal repair | 68.27 | 63.84 | 3 / 4 | 1 / 1 |
| Normal, then thinking for remaining gaps | 65.11 | 58.39 | 4 / 5 | 1 / 1 |

All strategies retained 12/12 selected target answers in both realizations.
Thinking was genuinely enabled: server responses contained thinking output,
whose character count (not its text) is recorded. Replacing normal repair
increased repair time from 2.23 to 14.09 seconds in A and 2.59 to 16.65 in B.
Escalation added about 8.6–8.7 seconds for the one remaining gap each time.

## Why the apparent local success was misleading

Thinking replacement appeared to fill the missing subject in semantic-09 but
left a different rule unresolved in semantic-13. Inspection also found that
the accepted semantic-09 subject started with `cei membri...`, a substring
beginning inside `acei membri...` in the source. Exact substring matching alone
does not establish a valid subject boundary. This is a validator limitation;
the nominal resolved status is not evidence of a sound legal interpretation.

The narrower escalation instead produced a stitched subject combining separated
pieces of source text. The existing exact-quote check correctly left that rule
unresolved in both realizations. This was not an API failure or a truncated
response. More reasoning did not make that source quote valid.

The next useful target is subject-span integrity and handling discontinuous
subjects explicitly. The engine must not silently accept an interior-word
substring or invent a contiguous quote from separated phrases. Any extension
should preserve the complete qualified statement and its original evidence.

## Reproduction and artifacts

`experiments/gemma-selective-thinking.json` preserves paired outputs and scores;
`experiments/gemma-selective-escalation.json` preserves the narrower strategy.
Both point back to the frozen prompt and baseline evidence hashes.

```powershell
$env:PYTHONPATH = 'C:\dev\grap-rust\mcp-server'
.venv/Scripts/python.exe docs/experiments/evaluate_selective_thinking.py --output .logs/selective-new.json
.venv/Scripts/python.exe docs/experiments/evaluate_selective_thinking.py --escalate-from .logs/selective-new.json --output .logs/escalation-new.json
```

The experiment changes neither production defaults nor repair policy. Models
are unloaded after the run.
