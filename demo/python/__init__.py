"""
grap Python Demo Package

This package provides convenient Python wrappers around the `grap-cli` binary
for ingesting, querying, and inspecting grap knowledge graphs.

Intended for demos, workshops, and rapid experimentation.
"""

from .grap_cli import run_grap_cli, find_grap_cli
from . import stats, query, ingest

__all__ = ["run_grap_cli", "find_grap_cli", "stats", "query", "ingest"]
