"""Persist a projection beside its graph directory.

The file is ``projection.json``. It holds ``nodes``, ``edges``, and
``counts``. The write goes to a temp file in the same directory and then
renames over the destination, so a crash leaves the previous file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


def save_projection(path: str | Path, projection: Mapping[str, Any]) -> None:
    """Write ``nodes``, ``edges``, and ``counts`` as JSON."""
    destination = os.fspath(path)
    payload = {
        "nodes": projection["nodes"],
        "edges": projection["edges"],
        "counts": projection["counts"],
    }
    temporary = destination + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def load_projection(path: str | Path) -> dict:
    """Read the projection JSON back.

    A missing file raises ``FileNotFoundError`` and the message names
    ``path``. Truncated JSON is refused by the decoder.
    """
    with open(os.fspath(path), encoding="utf-8") as handle:
        return json.load(handle)
