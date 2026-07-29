"""Detect flattened table / code-list chunks so they never reach the LLM.

Why this exists
---------------
Half the remaining law-ro corpus by decode cost sits in budget annexes, tariff
schedules and product nomenclatures. A yield probe showed those documents return
*more* extraction output than ordinary ones (16.5 characteristics per chunk vs
8.7) — and that is the problem, not the reassurance: the output is rows. A
"characteristic" like `familia Alexe Valentin — 10.000 lei` is volume, not
knowledge, and paying a generation per chunk to put ten thousand of them into
the ontology is the wrong trade.

Why the obvious detector fails
------------------------------
These are not laid out as tables. The law-aware JSON extractor flattens them
into running text long before extraction sees them, so the whitespace runs, dot
leaders and box-drawing rules described in docs/table-preserving-reduction.md
are already gone. A budget annex arrives looking like prose:

    ...85 01 Rambursări de credite externe 13.630.000 13.630.000 13.630.000...

A layout-based detector scores that 0.00. The signal that survives flattening is
the ratio of tokens that are FIGURES OR CODES rather than words.

Where the line is, and why it is not lower
------------------------------------------
Measured on the 10,231 unprocessed chunks, the score cleanly separates the
extremes — bare number runs and nomenclature score 0.97-1.00, ordinary
legislation 0.09-0.13. The interesting region is 0.55-0.70, which mixes two
things this detector cannot tell apart, because both are numeric-token-dense:

    [0.70] "...Popescu C. Gheorghe Butoieşti - Argineşti 8  31. Popescu M..."   a name list
    [0.70] "...Art. 42 a fost abrogat de art. 107 din LEGEA nr. 303 din..."     real law

So the threshold sits ABOVE that band at 0.75. It excludes 780 chunks (7.6%,
~32h of decode), no document loses every chunk, and citation-dense legislation
is never touched. Lowering it to 0.70 buys five hours and starts eating
abrogation notices — the trade is refused deliberately.
"""
from __future__ import annotations

import os
import re

# A token is "numeric" if it carries no letters at all, or is a code that starts
# and ends with a digit (`20.20.11`, `4412(.13-.19)`).
_NUMERIC_TOKEN = re.compile(r"^[\d.,;:()\[\]%/\-–—]+$")
_CODEY_TOKEN = re.compile(r"^[\d][\d.,()\-/]*[\d)]$")
_SENT_END = re.compile(r"[.;:]\s")

MIN_TOKENS = 20          # below this there is nothing to judge; keep the chunk


def _threshold() -> float:
    """`GRAP_KEE_SKIP_TABULAR`: score at/above which a chunk is skipped.
    "0", "off" or "none" disables skipping entirely."""
    raw = os.environ.get("GRAP_KEE_SKIP_TABULAR", "0.75").strip().lower()
    if raw in ("", "off", "none", "false"):
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.75


def signals(text: str) -> dict:
    toks = text.split()
    if len(toks) < MIN_TOKENS:
        return {"numeric": 0.0, "digits": 0.0, "sent": 100.0, "tokens": len(toks)}
    numeric = sum(1 for t in toks if _NUMERIC_TOKEN.match(t) or _CODEY_TOKEN.match(t))
    digits = sum(ch.isdigit() for ch in text) / max(1, len(text))
    # Sentence enders per 100 tokens. Prose punctuates; a row list does not.
    sents = 100.0 * len(_SENT_END.findall(text)) / max(1, len(toks))
    return {"numeric": numeric / len(toks), "digits": digits, "sent": sents,
            "tokens": len(toks)}


def tabular_score(text: str) -> float:
    """0..1 — high means a flattened table or code list, low means prose."""
    s = signals(text)
    if s["tokens"] < MIN_TOKENS:
        return 0.0
    score = (0.60 * min(1.0, s["numeric"] / 0.35)
             + 0.25 * min(1.0, s["digits"] / 0.20)
             + 0.15 * (1.0 - min(1.0, s["sent"] / 4.0)))
    return round(min(1.0, score), 3)


def is_tabular(text: str, threshold: float | None = None) -> bool:
    th = _threshold() if threshold is None else threshold
    return th > 0 and tabular_score(text) >= th
