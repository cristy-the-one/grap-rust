"""
smoke_test.py

Lightweight, CI-friendly smoke test for the demo layer.

It verifies the core loop:
- grap-cli binary can be found
- Ingestion works
- stats + query work
- Visualization export works

Usage:
    python -m demo.python.smoke_test
    python -m demo.python.smoke_test --graph-dir /tmp/grap-smoke --keep
"""

import shutil
import tempfile
from pathlib import Path

import typer
from rich.console import Console

from .grap_cli import find_grap_cli, run_grap_cli
from .ingest import find_documents

console = Console()
app = typer.Typer(add_completion=False)


@app.command()
def main(
    graph_dir: str | None = typer.Option(None, "--graph-dir", help="Custom graph directory"),
    keep: bool = typer.Option(False, "--keep", help="Do not delete the graph after the test"),
):
    """Run a quick smoke test of the grap demo stack."""

    binary = find_grap_cli()
    console.print(f"[cyan]Using grap-cli at:[/cyan] {binary}")

    if graph_dir:
        target = Path(graph_dir).expanduser().resolve()
        if target.exists():
            shutil.rmtree(target)
    else:
        target = Path(tempfile.mkdtemp(prefix="grap-smoke-"))

    try:
        sample_dir = Path(__file__).parent.parent / "sample-documents"
        docs = find_documents(sample_dir)

        if not docs:
            console.print("[red]No sample documents found. Cannot run smoke test.[/red]")
            raise typer.Exit(1)

        console.print(f"Running smoke test with {len(docs)} documents into {target}...")

        # Ingest documents
        for doc in docs:
            run_grap_cli(
                ["add-file", "--graph-dir", str(target), "--path", str(doc), "--json"],
                json_output=True,
                check=True,
            )

        # Basic stats check
        stats = run_grap_cli(
            ["stats", "--graph-dir", str(target), "--segments", "--json"],
            json_output=True,
            check=True,
        )
        assert stats is not None
        assert stats.get("node_count", 0) > 0, "No nodes found after ingestion"

        # Query check
        query_res = run_grap_cli(
            ["query", "--graph-dir", str(target), "rust", "--json"],
            json_output=True,
            check=True,
        )
        assert query_res is not None

        # Visualization export check (the new Phase 4 feature)
        export_res = run_grap_cli(
            ["export", "--graph-dir", str(target), "--format", "json"],
            json_output=True,
            check=True,
        )
        assert export_res is not None
        assert "nodes" in export_res

        console.print(
            f"[green][OK][/green] Smoke test passed!\n"
            f"  Nodes: {stats.get('node_count')}\n"
            f"  Edges: {stats.get('edge_count', 'N/A')}\n"
            f"  Segments: {stats.get('segment_count')}"
        )

    finally:
        if not keep and not graph_dir:  # only auto-clean temp dirs
            shutil.rmtree(target, ignore_errors=True)
            console.print("[dim]Cleaned up temporary graph directory.[/dim]")
        elif keep:
            console.print(f"[yellow]Graph left at:[/yellow] {target}")


if __name__ == "__main__":
    app()
