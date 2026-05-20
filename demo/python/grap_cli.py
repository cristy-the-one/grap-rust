"""
grap_cli.py

Helper module to locate and invoke the `grap-cli` Rust binary from Python.

Design goals:
- Work out of the box after `cargo build`
- Support Windows + Linux + macOS
- Make it trivial for scripts to get structured (JSON) output
- Provide excellent error messages when the binary is missing
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


def find_grap_cli() -> Path:
    """
    Find the grap-cli binary with a sensible priority order.

    Priority:
    1. GRAP_CLI_PATH environment variable (explicit override)
    2. In PATH (shutil.which)
    3. Common cargo output locations relative to this file:
       - ../../target/debug/grap-cli(.exe)
       - ../../target/release/grap-cli(.exe)
    """
    # 1. Explicit override
    env_path = os.environ.get("GRAP_CLI_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return p
        print(f"[grap] GRAP_CLI_PATH points to non-existent file: {env_path}", file=sys.stderr)

    # 2. In PATH
    candidates = ["grap-cli", "grap-cli.exe"]
    for name in candidates:
        found = shutil.which(name)
        if found:
            return Path(found)

    # 3. Relative to this script (works when running from the repo)
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent  # demo/python -> demo -> repo root

    for profile in ("debug", "release"):
        for name in ("grap-cli", "grap-cli.exe"):
            candidate = repo_root / "target" / profile / name
            if candidate.exists():
                return candidate

    raise FileNotFoundError(
        "Could not find the 'grap-cli' binary.\n"
        "Please build it first:\n"
        "    cargo build --release --bin grap-cli --features \"fulltext,sharding\"\n\n"
        "Or set the GRAP_CLI_PATH environment variable to the full path of the binary."
    )


def run_grap_cli(args: list[str], *, json_output: bool = True, check: bool = True) -> Any:
    """
    Run the grap-cli binary and return the result.

    Args:
        args: Arguments to pass (e.g. ["stats", "--graph-dir", "foo", "--segments"])
        json_output: If True, expect JSON and parse it. Otherwise return raw stdout.
        check: If True, raise on non-zero exit.

    Returns:
        Parsed JSON (dict/list) if json_output=True, otherwise str.
    """
    binary = find_grap_cli()

    full_cmd = [str(binary)] + args
    if json_output and "--json" not in args:
        full_cmd.append("--json")

    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Failed to execute grap-cli at {binary}") from e

    if check and result.returncode != 0:
        print("=== grap-cli stderr ===", file=sys.stderr)
        print(result.stderr or "(no stderr)", file=sys.stderr)
        print("=== grap-cli stdout ===", file=sys.stderr)
        print(result.stdout or "(no stdout)", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, full_cmd, result.stdout, result.stderr)

    if json_output:
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    else:
        return result.stdout


def ensure_graph_dir(path: str | Path) -> Path:
    """Convenience helper that ensures the directory exists (but does not create a graph)."""
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p
