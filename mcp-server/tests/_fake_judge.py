"""Turn a fake loom-judge script into a path this platform can execute."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def make_runnable(script: Path) -> Path:
    """The path to pass as ``--loom-judge``.

    POSIX sets the executable bit and returns ``script``. Windows writes
    ``script`` with a ``.cmd`` suffix and returns that wrapper.
    """
    if os.name == "nt":
        wrapper = script.with_suffix(".cmd")
        wrapper.write_text(
            f'@"{sys.executable}" "{script}" %*\n',
            encoding="utf-8",
        )
        return wrapper
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script
