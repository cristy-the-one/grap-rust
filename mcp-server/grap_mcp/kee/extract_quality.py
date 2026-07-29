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
        # Three different events, previously added together under one name.
        # "parse failure" should mean the model returned something unparseable —
        # not that the server was down, and not that a chunk honestly had
        # nothing in it to extract.
        self.parse_failures = 0       # reply arrived, no JSON in it
        self.empty_extractions = 0    # valid JSON, no outcomes and no relations
        self.provider_errors = 0      # transport/server failure, model not at fault
        self.rel_counts: list[int] = []
        self.outcome_counts: list[int] = []
        self.rel_types: Counter = Counter()
        self.class_named = 0          # outcome name equals a class name (LEAKED past the filter)
        self.class_named_rejected = 0 # ...and how many the filter refused
        self.class_edges_rejected = 0 # relations dropped for a class-named endpoint
        self.notation_leak = 0        # relation type carries prompt notation ("-->")
        self.empty_typed = 0          # relation with a blank type

    # ---- accumulation -----------------------------------------------------
    def record_failure(self, reason: Optional[str] = None) -> None:
        """Count a chunk that produced no fragment. `reason` is one of
        "unparsed" | "empty" | "provider"; None keeps the old lumped behaviour."""
        self.chunks += 1
        if reason == "empty":
            self.empty_extractions += 1
        elif reason == "provider":
            self.provider_errors += 1
        else:
            self.parse_failures += 1

    def record(self, frag: Dict[str, Any]) -> None:
        self.chunks += 1
        outcomes = frag.get("outcomes") or []
        relations = frag.get("relations") or []
        self.outcome_counts.append(len(outcomes))
        self.rel_counts.append(len(relations))
        # Two different questions, deliberately kept apart:
        #   class_named_rejected — the model TRIED to name an outcome after a
        #     class and the extractor refused. Reported, never alarmed: it is
        #     handled, and the rate is interesting (how often the context format
        #     tips the model into organising by type).
        #   class_named        — a class-named outcome REACHED the graph. Should
        #     be 0 forever now; if it is not, the filter has a hole and the
        #     circuit breaker stops the loop.
        # Filtering without counting would have silenced the very signal that
        # caught chunk 19811#3.
        self.class_named_rejected += len(frag.get("rejected_class_named") or [])
        self.class_edges_rejected += int(frag.get("rejected_class_edges") or 0)
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
            "empty_extractions": self.empty_extractions,
            "provider_errors": self.provider_errors,
            # The old `parse_failure_rate` was this: every chunk that yielded no
            # fragment, whatever the cause. Kept under an honest name so the
            # history series in extract_quality_history.jsonl stays comparable.
            "no_fragment_rate": round(
                (self.parse_failures + self.empty_extractions + self.provider_errors) / n, 3),
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
            "class_named_rejected": self.class_named_rejected,
            "class_edges_rejected": self.class_edges_rejected,
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
        # Alarm on the lumped rate, which is what the 0.15 threshold was
        # calibrated against — but name the split so the operator can see
        # whether it is the model, the server, or genuinely empty text.
        no_frag = self.parse_failures + self.empty_extractions + self.provider_errors
        if self.chunks >= 10 and no_frag / self.chunks > 0.15:
            out.append(f"no_fragment: {no_frag}/{self.chunks} chunks yielded nothing "
                       f"(unparsed {self.parse_failures}, empty {self.empty_extractions}, "
                       f"provider {self.provider_errors}) "
                       "(> 15%; baseline 0-20%)")
        return out

    def write(self, path) -> None:
        p = Path(path)
        try:
            p.write_text(json.dumps(self.report(), indent=2, ensure_ascii=False),
                         encoding="utf-8")
        except Exception:
            pass
