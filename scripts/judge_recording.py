#!/usr/bin/env python3
"""Export a judge request, run loom-judge, and import the verdict.

    python scripts/judge_recording.py \
        --spec FILE --spec-id TEXT --log FILE \
        --dbc FILE[@CHANNEL] --loom-judge PATH \
        --run-id ID --workspace DIR --out DIR

``--dbc`` may be repeated. The request is written to ``<out>/request.json``
by ``export_request``. The binary is then:

    <loom-judge> --log ... --dbc ... --request <out>/request.json --out <out>/verdict.json

Exit 0 imports the verdict and prints its counts line. Exit 2 reprints the
binary's stderr and imports nothing. Any other exit prints
``loom-judge failed (exit N)`` and imports nothing. The verdict file is left
in ``<out>``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from export_judge_request import export_request
from import_verdicts import import_verdicts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Export a judge request, run loom-judge, and import the verdict.",
    )
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--spec-id", required=True)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--dbc", action="append", required=True)
    parser.add_argument("--loom-judge", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    specification = json.loads(args.spec.read_text(encoding="utf-8"))
    request = export_request(specification, args.spec_id)
    args.out.mkdir(parents=True, exist_ok=True)
    request_path = args.out / "request.json"
    verdict_path = args.out / "verdict.json"
    request_path.write_text(
        json.dumps(request, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    command = [str(args.loom_judge), "--log", str(args.log)]
    for dbc in args.dbc:
        command.extend(["--dbc", dbc])
    command.extend(
        ["--request", str(request_path), "--out", str(verdict_path)]
    )
    proc = subprocess.run(command, capture_output=True)
    if proc.returncode == 0:
        line = import_verdicts(
            verdict_path, request_path, args.run_id, args.workspace
        )
        print(line)
        return
    if proc.returncode == 2:
        sys.stderr.buffer.write(proc.stderr)
        raise SystemExit(2)
    print(f"loom-judge failed (exit {proc.returncode})", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
