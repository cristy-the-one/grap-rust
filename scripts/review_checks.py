#!/usr/bin/env python3
"""Review generated checks before a verdict counts.

    python scripts/review_checks.py --run RUN --list
    python scripts/review_checks.py --run RUN --approve CASE:CHECK
    python scripts/review_checks.py --run RUN --reject CASE:CHECK
    python scripts/review_checks.py --run RUN --scorecard

No network and no model. A decision is the sha256 of the check object as it
stands in specification.json. The approvals file stores that hash, the case
id, the check id, and the first verifies id. It does not store requirement text.

--scorecard prints the two reviewed lines and writes nothing. The run script
uses it when it builds the scorecard. --approve and --reject rewrite the
approvals file and, when the run has a verdict, append those two lines to
report.md under "### scorecard (after review)".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

_FORMAT = "grap-check-review/1"
_EN_DASH = "\u2013"
_BO = re.compile(r"^[ \t]*BO_[ \t]+(\d+)[ \t]+([^: \t]+)[ \t]*:")
_VAL = re.compile(r"^\s*VAL_\s+(\d+)\s+(\S+)\s+(.*?)\s*;\s*$")
_PAIR = re.compile(r'(-?\d+)\s+"((?:\\.|[^"\\])*)"')


def check_sha256(check: dict) -> str:
    """sha256 of the check object, stable under key order."""
    payload = json.dumps(check, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        description="Review generated checks before a verdict counts.",
    )
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--scorecard", action="store_true")
    parser.add_argument("--approve", action="append", default=[])
    parser.add_argument("--reject", action="append", default=[])
    args = parser.parse_args(argv)
    decisions = _decisions_in_order(argv)
    if not args.list and not args.scorecard and not decisions:
        parser.error("give --list, --scorecard, --approve, or --reject")

    run = args.run
    approvals = args.approvals if args.approvals is not None else run.parent / "approved-checks.json"
    need_spec = bool(decisions) or args.list
    cases = _load_cases(run, required=need_spec)
    tables = _value_names(run)

    if decisions:
        entries = _load_entries(approvals)
        resolved = [(_kind, *_find(cases, ref)) for _kind, ref in decisions]
        today = date.today().isoformat()
        for kind, case, check in resolved:
            _upsert(entries, case, check, kind, today)
        _write_atomic(approvals, _render(entries))
        verdict = _load_verdict(run)
        if verdict is not None:
            _append_scorecard(run, scorecard_lines(cases, verdict, _decision_map(entries)))
    else:
        entries = _load_entries(approvals)

    if args.list:
        sys.stdout.write(format_list(cases, _decision_map(entries), tables))
    if args.scorecard:
        verdict = _load_verdict(run)
        if verdict is None:
            raise SystemExit("no verdict.json; nothing to score")
        lines = scorecard_lines(cases, verdict, _decision_map(entries))
        sys.stdout.write("\n".join(lines) + "\n")
    return 0


def scorecard_lines(cases: list, verdict: dict, decisions: dict[str, str]) -> list[str]:
    """The two headline lines. A missing decision map reviews nothing."""
    by_id: dict = {}
    for case in cases:
        if isinstance(case, dict):
            by_id.setdefault(case.get("id"), case)
    reviewed_pass = reviewed_fail = 0
    other_pass = other_fail = 0
    verdict_cases = verdict.get("cases") if isinstance(verdict.get("cases"), list) else []
    counts = verdict.get("counts") if isinstance(verdict.get("counts"), dict) else {}
    n_cases = counts.get("cases", len(verdict_cases))
    for item in verdict_cases:
        if not isinstance(item, dict):
            continue
        reviewed = _is_reviewed(by_id.get(item.get("case_id")), decisions)
        name = item.get("verdict")
        if name == "pass":
            if reviewed:
                reviewed_pass += 1
            else:
                other_pass += 1
        elif name == "fail":
            if reviewed:
                reviewed_fail += 1
            else:
                other_fail += 1
    return [
        (
            f"verdicts={reviewed_pass + reviewed_fail} of {n_cases} cases "
            f"(pass={reviewed_pass} fail={reviewed_fail}), reviewed checks only"
        ),
        (
            f"unreviewed_verdicts={other_pass + other_fail} "
            f"(pass={other_pass} fail={other_fail})"
        ),
    ]


def format_list(cases: list, decisions: dict[str, str], tables: dict) -> str:
    blocks = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        for check in case.get("checks") or []:
            if not isinstance(check, dict):
                continue
            digest = check_sha256(check)
            status = decisions.get(digest)
            if status not in ("approved", "rejected"):
                status = "unreviewed"
            header = f"{case.get('id')} {check.get('id')} sha256={digest[:12]}  [{status}]"
            blocks.append(header + "\n" + format_check(check, tables))
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def format_check(check: dict, tables: dict) -> str:
    given = check.get("given")
    if not isinstance(given, list):
        given = []
    given_text = ", ".join(
        format_condition(item, tables) for item in given if isinstance(item, dict)
    )
    given_part = f"given {given_text}" if given_text else "given"
    window = check.get("within_ms") if isinstance(check.get("within_ms"), list) else [0, 0]
    start = window[0] if len(window) > 0 else 0
    end = window[1] if len(window) > 1 else 0
    return (
        f"{given_part}; when {format_condition(check.get('when') or {}, tables)}; "
        f"expect {format_condition(check.get('expect') or {}, tables)}; "
        f"{check.get('mode', '')} within {start}{_EN_DASH}{end} ms"
    )


def format_condition(condition: dict, tables: dict) -> str:
    if not isinstance(condition, dict):
        return ""
    signal = condition.get("signal") if isinstance(condition.get("signal"), dict) else {}
    message = signal.get("message", "")
    name = signal.get("signal", "")
    value = condition.get("value")
    text = f"{message}.{name} {condition.get('op', '')} {_format_value(value)}"
    raw = _raw_key(value)
    if raw is None:
        return text
    label = tables.get((message, name), {}).get(raw)
    if label:
        text += f" ({label})"
    return text


def _is_reviewed(case, decisions: dict[str, str]) -> bool:
    if not isinstance(case, dict):
        return False
    checks = case.get("checks")
    if not isinstance(checks, list) or not checks:
        return False
    for check in checks:
        if not isinstance(check, dict):
            return False
        if decisions.get(check_sha256(check)) != "approved":
            return False
    return True


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    return str(value)


def _raw_key(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    return None


def _value_names(run: Path) -> dict[tuple[str, str], dict[int, str]]:
    inbox = run / "in"
    if not inbox.is_dir():
        return {}
    tables: dict[tuple[str, str], dict[int, str]] = {}
    for path in sorted(inbox.glob("*.dbc")):
        tables.update(_dbc_tables(_read_text(path)))
    return tables


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _dbc_tables(text: str) -> dict[tuple[str, str], dict[int, str]]:
    logical = _logical_lines(text)
    frames: dict[str, str] = {}
    for line in logical:
        match = _BO.match(line)
        if match:
            frames[match.group(1)] = match.group(2)
    tables: dict[tuple[str, str], dict[int, str]] = {}
    for line in logical:
        match = _VAL.match(line.strip())
        if not match:
            continue
        message = frames.get(match.group(1))
        if not message:
            continue
        names: dict[int, str] = {}
        for raw, label in _PAIR.findall(match.group(3)):
            names[int(raw)] = _unescape(label)
        tables[(message, match.group(2))] = names
    return tables


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    pending = None
    for raw in text.splitlines():
        if pending is not None:
            pending = pending + " " + raw.strip()
            if ";" in raw:
                lines.append(pending)
                pending = None
            continue
        if raw.strip().startswith("VAL_") and ";" not in raw:
            pending = raw.strip()
        else:
            lines.append(raw)
    if pending is not None:
        lines.append(pending)
    return lines


def _unescape(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            out.append(text[index + 1])
            index += 2
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def _load_cases(run: Path, *, required: bool) -> list:
    path = run / "spec-out" / "specification.json"
    if not path.is_file():
        if required:
            raise SystemExit(f"no specification at {path}")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list):
        raise SystemExit("specification.json has no cases list")
    return cases


def _load_verdict(run: Path) -> dict | None:
    path = run / "judge-out" / "verdict.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("verdict.json is not an object")
    return data


def _load_entries(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != _FORMAT:
        raise SystemExit(f"{path} is not a {_FORMAT} file")
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise SystemExit(f"{path} has no checks list")
    collapsed: list[dict] = []
    index: dict[str, int] = {}
    for entry in checks:
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise SystemExit(f"{path} has an entry without sha256")
        digest = entry["sha256"]
        if digest in index:
            collapsed[index[digest]] = entry
        else:
            index[digest] = len(collapsed)
            collapsed.append(entry)
    return collapsed


def _decision_map(entries: list[dict]) -> dict[str, str]:
    found = {}
    for entry in entries:
        decision = entry.get("decision")
        if decision in ("approved", "rejected"):
            found[entry["sha256"]] = decision
    return found


def _find(cases: list, ref: str) -> tuple[dict, dict]:
    if ":" not in ref:
        raise SystemExit(f"expected CASE:CHECK, got {ref}")
    case_id, check_id = ref.split(":", 1)
    if case_id == "" or check_id == "":
        raise SystemExit(f"expected CASE:CHECK, got {ref}")
    for case in cases:
        if not isinstance(case, dict) or case.get("id") != case_id:
            continue
        for check in case.get("checks") or []:
            if isinstance(check, dict) and check.get("id") == check_id:
                return case, check
    raise SystemExit(f"unknown check {case_id}:{check_id}")


def _upsert(entries: list[dict], case: dict, check: dict, decision: str, today: str) -> None:
    verifies = case.get("verifies") if isinstance(case.get("verifies"), list) else []
    requirement = verifies[0] if verifies and isinstance(verifies[0], str) else ""
    entry = {
        "requirement": requirement,
        "case": case.get("id"),
        "check": check.get("id"),
        "sha256": check_sha256(check),
        "decision": decision,
        "at": today,
    }
    digest = entry["sha256"]
    for index, existing in enumerate(entries):
        if existing.get("sha256") == digest:
            entries[index] = entry
            return
    entries.append(entry)


def _render(entries: list[dict]) -> str:
    payload = {"format": _FORMAT, "checks": entries}
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _append_scorecard(run: Path, lines: list[str]) -> None:
    report = run / "report.md"
    block = (
        "\n### scorecard (after review)\n"
        "```\n"
        + "\n".join(lines)
        + "\n```\n"
    )
    existing = report.read_text(encoding="utf-8") if report.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    report.write_text(existing + block, encoding="utf-8")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def _decisions_in_order(argv: list[str]) -> list[tuple[str, str]]:
    found = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("--approve", "--reject"):
            if index + 1 >= len(argv):
                raise SystemExit(f"{token} needs CASE:CHECK")
            kind = "approved" if token == "--approve" else "rejected"
            found.append((kind, argv[index + 1]))
            index += 2
            continue
        if token.startswith("--approve=") or token.startswith("--reject="):
            flag, value = token.split("=", 1)
            kind = "approved" if flag == "--approve" else "rejected"
            found.append((kind, value))
        index += 1
    return found


if __name__ == "__main__":
    raise SystemExit(main())
