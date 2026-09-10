# Subject quote boundary validation

The shared legal-rule extractor now rejects subject spans that start or end
inside a Unicode word. For example, `cei membri` inside `acei membri` no longer
becomes an accepted subject. This check covers both model-selected subjects and
the final spans chosen by the explicit-syntax fallback, including after focused
label repair. It uses the original source coordinates without normalization.

Letters, numbers, combining marks and connector punctuation count as word
characters. This handles Romanian precomposed and decomposed diacritics and
prevents partial identifiers. Other punctuation remains a delimiter. This is
a lexical integrity check, not proof that a quoted phrase is the complete legal
subject; discontinuous subjects and phrase-level completeness remain separate
problems. Abbreviations embedded in valid subjects and enclosing punctuation
continue to work.

On rejection the statement and source evidence remain unchanged, the subject
stays empty, no subject evidence is emitted, and the receipt records
`selected subject cuts through a source word` plus `subject unresolved`.
The existing bounded repair remains available; it cannot bypass the check.
Extractor versions advance to legal-rules-v11 and qualified-rules-v2.
Existing stored graphs and historical receipts were not rewritten; these checks
apply on subsequent extraction/revalidation.

Offline replay of both saved baseline realizations and both repair arms
preserved all 48 selected target scores. It rejected the two observed
interior-word subject spans from the thinking arm (one per realization),
correctly exposing their unresolved status. These 48 outputs are repeated
development cases, not 48 independent legal examples. Per-case evidence is in
[subject-boundary-replay.json](experiments/subject-boundary-replay.json).

Regression tests cover start/end cuts, Romanian Unicode marks, digits,
underscores, punctuation and exact evidence offsets. A finalizer regression
confirms that repeating the bad quote in a repair cannot resolve the rule.
No models were loaded for this fix or replay.

Validation: 567 MCP-server tests, 26 GUI tests and 32 experiment tests passed
(625 total).
