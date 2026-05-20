"""
smoke_test.py

Lightweight smoke test for the demo layer.
Can be used in CI to verify that the Python + CLI integration still works.

Usage:
    python -m demo.python.smoke_test --graph-dir /tmp/grap-smoke
"""

import tempfile
import shutil
from pathlib import Path

import typer
from rich.console import Console

from .grap_cli import run_grap_cli
from .ingest import find_documents

console = Console()
app = typer.Typer()


@app.command()
def main(graph_dir: str = typer.Option(None, "--graph-dir")):
    if graph_dir:
        target = Path(graph_dir)
        if target.exists():
            shutil.rmtree(target)
    else:
        target = Path(tempfile.mkdtemp(prefix="grap-smoke-"))

    sample_dir = Path(__file__).parent.parent / "sample-documents"
    docs = find_documents(sample_dir)

    if not docs:
        console.print("[red]No sample documents found. Smoke test cannot run.[/red]")
        raise typer.Exit(1)

    console.print(f"Running smoke test with {len(docs)} documents...")

    # Ingest using the improved ingest.py logic (via CLI)
    for doc in docs:
        run_grap_cli(
            ["add-file", "--graph-dir", str(target), "--path", str(doc), "--json"],
            json_output=True,
            check=True,
        )

    # Verify stats work
    stats = run_grap_cli(["stats", "--graph-dir", str(target), "--segments", "--json"], json_output=True)
    assert stats is not None
    assert stats.get("node_count", 0) > 0

    # Verify query works
    query_result = run_grap_cli(["query", "--graph-dir", str(target), "rust", "--json"], json_output=True)
    assert query_result is not None

    console.print(f"[green][OK][/green] Smoke test passed. Graph created at {target}")
    console.print(f"Nodes: {stats.get('node_count')}, Segments: {stats.get('segment_count')}")


if __name__ == "__main__":
    app()
