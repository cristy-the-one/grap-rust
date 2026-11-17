"""Persist a projection beside its graph directory.

The file is ``projection.json``. It holds ``nodes``, ``edges``, and
``counts``. The write goes to a temp file in the same directory and then
renames over the destination, so a crash leaves the previous file.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


def save_projection(path: str | Path, projection: Mapping[str, Any]) -> None:
    """Write ``nodes``, ``edges``, and ``counts`` as JSON."""
    destination = Path(path)
    payload = {
        "nodes": projection["nodes"],
        "edges": projection["edges"],
        "counts": projection["counts"],
    }
    fd, temporary = tempfile.mkstemp(
        dir=destination.parent,
        prefix="projection.json.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    _fsync_directory(destination.parent)


def load_projection(path: str | Path) -> dict:
    """Read the projection JSON back.

    A missing file raises ``FileNotFoundError`` naming ``path``. Truncated
    JSON, and a value that is not a dict of ``nodes``, ``edges``, and
    ``counts``, raise ``ValueError`` naming ``path``.
    """
    if not Path(path).is_file():
        raise FileNotFoundError(f"projection file not found: {path}")
    try:
        with open(os.fspath(path), encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"projection file {path} is not valid JSON") from exc
    if not _valid_shape(data):
        raise ValueError(
            f"projection file {path} must be a dict with nodes list, edges list, and counts dict"
        )
    return data


def _valid_shape(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("nodes"), list)
        and isinstance(data.get("edges"), list)
        and isinstance(data.get("counts"), dict)
    )


def _fsync_directory(directory: Path) -> None:
    """Flush the directory entry after rename. POSIX only."""
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
