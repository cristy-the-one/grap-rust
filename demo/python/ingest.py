"""
ingest.py

Basic document ingestion into a grap knowledge graph.

Phase 2 version:
- Recursively walks a directory
- Treats each .md / .txt / .rst file as one node (full content)
- Very simple relationship inference (optional, disabled by default)

This is intentionally simple so it is easy to understand and extend
(adding chunking, LLM relation extraction, etc. is straightforward later).
"""

from __future__ import annotations

import typer
from pathlib import Path
from typing import List

from rich.console import Console
from rich.progress import track

from .grap_cli import run_grap_cli, ensure_graph_dir

app = typer.Typer(add_completion=False, help="Ingest documents into a grap knowledge graph")
console = Console()


SUPPORTED_EXTENSIONS = {".md", ".txt", ".rst", ".markdown"}


def find_documents(root: Path) -> List[Path]:
    docs = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            docs.append(p)
    return docs


@app.command()
def main(
    input: str = typer.Option(..., "--input", "-i", help="Directory containing documents to ingest"),
    graph_dir: str = typer.Option(..., "--graph-dir", "-g"),
    shards: int = typer.Option(1, "--shards", help="Number of shards (1 = single graph)"),
    recreate: bool = typer.Option(False, "--recreate", help="Delete existing graph before ingesting"),
):
    """Ingest documents into a new or existing grap graph."""
    input_path = Path(input).expanduser().resolve()
    graph_path = ensure_graph_dir(graph_dir)

    if recreate and graph_path.exists():
        # We can't easily delete the whole directory safely here.
        # For now we just warn the user.
        console.print(
            f"[yellow]Warning:[/yellow] --recreate is not fully implemented yet. "
            "Please remove the graph directory manually if you want a fresh start."
        )

    documents = find_documents(input_path)
    if not documents:
        console.print(f"[red]No supported documents found under {input_path}[/red]")
        raise typer.Exit(1)

    console.print(f"Found [bold]{len(documents)}[/bold] documents under [cyan]{input_path}[/cyan]")

    # For Phase 2 we do the simplest possible thing:
    # Call the (future) ingest command on the CLI, or fall back to printing instructions.
    # Since the Rust `ingest` command does not exist yet, we give a helpful message.

    console.print(
        "\n[bold]Phase 2 limitation[/bold]\n"
        "The Rust-side `ingest` command is not implemented yet.\n"
        "For now you can create graphs from Python by calling the library directly, or we can "
        "add a simple Rust ingestion command in the next phase.\n\n"
        "You can still use the existing Rust examples to create graphs, then query them with these Python tools."
    )

    # Future improvement: once the CLI has an `ingest` subcommand, we would do:
    # run_grap_cli(["ingest", "--input", str(input_path), "--graph-dir", str(graph_path), "--shards", str(shards)])


if __name__ == "__main__":
    app()
