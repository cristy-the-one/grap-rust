#!/usr/bin/env python3
"""Write a loom-judge/1 request from a validated §5 specification.

    python scripts/export_judge_request.py --spec FILE --spec-id TEXT --out FILE

One case per specification case, in specification order. A case whose
checks are [] is kept. Each check is written with the keys id, given,
when, expect, mode, within_ms, in that order. given is left out when it
is []. channel inside a signal reference is left out when it is null.
tolerance is left out when it is null. A key is never written as null:
signal-loom refuses unknown keys, and null is not a value it accepts.

The file is json.dumps(obj, indent=2, ensure_ascii=False) plus a newline.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def export_request(specification: dict, spec_id: str) -> dict:
    """The judge request for one validated specification."""
    if not isinstance(specification, dict) or not isinstance(specification.get("cases"), list):
        raise ValueError("specification must be an object with a cases list")
    cases = []
    for case in specification["cases"]:
        if not isinstance(case, dict):
            raise ValueError("case must be an object")
        cases.append(
            {
                "case_id": case["id"],
                "verifies": case["verifies"],
                "kind": case["kind"],
                "checks": [_check(item) for item in case["checks"]],
            }
        )
    return {
        "format": "loom-judge/1",
        "spec_id": spec_id,
        "cases": cases,
    }


def _check(item: dict) -> dict:
    if not isinstance(item, dict):
        raise ValueError("check must be an object")
    stored = {"id": item["id"]}
    given = _condition_list(item.get("given"))
    if given:
        stored["given"] = given
    stored["when"] = _condition(item["when"])
    stored["expect"] = _condition(item["expect"])
    stored["mode"] = item["mode"]
    stored["within_ms"] = item["within_ms"]
    return stored


def _condition_list(value) -> list:
    # [] and null both mean absent. A missing key arrives here as None.
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("given must be a list")
    return [_condition(item) for item in value]


def _condition(item: dict) -> dict:
    if not isinstance(item, dict):
        raise ValueError("condition must be an object")
    stored = {
        "signal": _signal(item["signal"]),
        "op": item["op"],
        "value": item["value"],
    }
    if item.get("tolerance") is not None:
        stored["tolerance"] = item["tolerance"]
    return stored


def _signal(item: dict) -> dict:
    if not isinstance(item, dict):
        raise ValueError("signal must be an object")
    stored = {
        "message": item["message"],
        "signal": item["signal"],
    }
    if item.get("channel") is not None:
        stored["channel"] = item["channel"]
    return stored


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Write a loom-judge/1 request from a validated §5 specification.",
    )
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--spec-id", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    specification = json.loads(args.spec.read_text(encoding="utf-8"))
    request = export_request(specification, args.spec_id)
    text = json.dumps(request, indent=2, ensure_ascii=False) + "\n"
    args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
