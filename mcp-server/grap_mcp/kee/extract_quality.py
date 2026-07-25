"""Extraction quality telemetry.

The law-ro distillation arc produced a set of failure modes that every
*quantity* metric was blind to (see docs/dark-factory-lessons.md, Theme VIII):
schema echoing, type overloading, class-names-used-as-instance-names, notation
leakage, and template collapse. Each is cheap to detect and none shows up in a
yield or token average, so extraction now measures them continuously.

The rule this encodes: **pair every quantity metric with a shape metric.** What
exposed the worst failure was not any per-chunk average but a *variance* — the
model emitted exactly 3 relations on 10/10 chunks. Constant output is the
signature of a template-filler and is invisible to a mean.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional


class QualityMonitor:
    """Accumulates per-chunk extraction shape, reports run-level indicators."""

    def __init__(self, class_vocab=None):
        self.class_vocab = {c.strip().lower() for c in (class_vocab or []) if c.strip()}
        self.chunks = 0
        self.parse_failures = 0
        self.rel_counts: list[int] = []
        self.outcome_counts: list[int] = []
        self.rel_types: Counter = Counter()
        self.class_named = 0          # outcome name equals a class name
        self.notation_leak = 0        # relation type carries prompt notation ("-->")
        self.empty_typed = 0          # relation with a blank type

    # ---- accumulation -----------------------------------------------------
    def record_failure(self) -> None:
        self.chunks += 1
        self.parse_failures += 1

    def record(self, frag: Dict[str, Any]) -> None:
        self.chunks += 1
        outcomes = frag.get("outcomes") or []
        relations = frag.get("relations") or []
        self.outcome_counts.append(len(outcomes))
        self.rel_counts.append(len(relations))
        for o in outcomes:
            name = str(o.get("name", "")).strip().lower().replace(" ", "")
            if name and name in self.class_vocab:
                self.class_named += 1
        for r in relations:
            t = str(r.get("type", "")).strip()
            if not t:
                self.empty_typed += 1
                continue
            if "-->" in t or "<->" in t:
                self.notation_leak += 1
            self.rel_types[t] += 1

    # ---- reporting --------------------------------------------------------
    @staticmethod
    def _cv(xs) -> float:
        if not xs:
            return 0.0
        m = sum(xs) / len(xs)
        if m == 0:
            return 0.0
        var = sum((x - m) ** 2 for x in xs) / len(xs)
        return math.sqrt(var) / m

    def report(self) -> Dict[str, Any]:
        n = self.chunks or 1
        rel_cv = self._cv(self.rel_counts)
        return {
            "chunks": self.chunks,
            "parse_failures": self.parse_failures,
            "parse_failure_rate": round(self.parse_failures / n, 3),
            "outcomes_per_chunk": round(sum(self.outcome_counts) / max(1, len(self.outcome_counts)), 2),
            "relations_per_chunk": round(sum(self.rel_counts) / max(1, len(self.rel_counts)), 2),
            # shape metrics — the ones a mean cannot see
            "relation_count_cv": round(rel_cv, 3),
            "distinct_relation_types": len(self.rel_types),
            "top_relation_type_share": round(
                max(self.rel_types.values()) / sum(self.rel_types.values()), 3)
            if self.rel_types else 0.0,
            # known failure modes, each from a real incident
            "class_named_outcomes": self.class_named,
            "notation_leaks": self.notation_leak,
            "blank_relation_types": self.empty_typed,
            "alarms": self.alarms(rel_cv),
        }

    def alarms(self, rel_cv: Optional[float] = None) -> list[str]:
        """Named alarms rather than a silent number. Thresholds come from measured
        baselines (law-ro iter 4/4b), not from guesses."""
        out = []
        cv = self._cv(self.rel_counts) if rel_cv is None else rel_cv
        if self.chunks >= 5 and self.rel_counts and cv <= 0.10:
            out.append(f"template_collapse: relation-count CV {cv:.2f} <= 0.10 "
                       "(near-constant output; baseline 0.42-0.82)")
        if self.class_named:
            out.append(f"class_named_outcomes: {self.class_named} outcome(s) named "
                       "with a class name (would collapse instances into one node)")
        if self.notation_leak:
            out.append(f"notation_leak: {self.notation_leak} relation type(s) carry "
                       "prompt notation")
        if self.chunks >= 10 and self.parse_failures / self.chunks > 0.15:
            out.append(f"parse_failures: {self.parse_failures}/{self.chunks} "
                       "(> 15%; baseline 0-20%)")
        return out

    def write(self, path) -> None:
        p = Path(path)
        try:
            p.write_text(json.dumps(self.report(), indent=2, ensure_ascii=False),
                         encoding="utf-8")
        except Exception:
            pass
