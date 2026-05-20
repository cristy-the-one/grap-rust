"""
ingest.py

Functional document ingestion into a grap knowledge graph.

Features:
- Recursively walks a directory for supported files (.md, .txt, etc.)
- Adds each file as a node using `grap-cli add-file`
- Captures node IDs (useful for later relationship building or demos)
- Supports `--shards` (creates sharded graph when > 1)
- Progress reporting with rich
- Optional recreation of the target graph
"""

from __future__ import annotations

import shutil
import typer
from pathlib import Path
from typing import Dict

from rich.console import Console
from rich.progress import track

from .grap_cli import run_grap_cli, ensure_graph_dir

app = typer.Typer(add_completion=False, help="Ingest documents into a grap knowledge graph")
console = Console()

SUPPORTED_EXTENSIONS = {".md", ".txt", ".rst", ".markdown", ".mdx"}


def find_documents(root: Path) -> list[Path]:
    docs: list[Path] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            docs.append(p)
    return docs


@app.command()
def main(
    input: str = typer.Option(..., "--input", "-i", help="Directory containing documents to ingest"),
    graph_dir: str = typer.Option(..., "--graph-dir", "-g", help="Target grap graph directory"),
    shards: int = typer.Option(1, "--shards", help="Number of shards (1 = regular graph)"),
    recreate: bool = typer.Option(False, "--recreate", help="Delete existing graph directory first"),
    show_progress: bool = typer.Option(True, "--progress/--no-progress"),
):
    """
    Ingest documents into a grap knowledge graph.

    Each supported file becomes one node. The filename (without extension) is used as the label.
    """
    input_path = Path(input).expanduser().resolve()
    graph_path = Path(graph_dir).expanduser().resolve()

    if recreate and graph_path.exists():
        console.print(f"[yellow]Removing existing graph at {graph_path}[/yellow]")
        shutil.rmtree(graph_path)

    ensure_graph_dir(graph_path)

    documents = find_documents(input_path)
    if not documents:
        console.print(f"[red]No supported documents found under {input_path}[/red]")
        raise typer.Exit(1)

    console.print(f"Found [bold]{len(documents)}[/bold] documents under [cyan]{input_path}[/cyan]")

    node_map: Dict[Path, int] = {}

    for doc in track(documents, description="Ingesting documents...", disable=not show_progress):
        result = run_grap_cli(
            [
                "add-file",
                "--graph-dir", str(graph_path),
                "--path", str(doc),
                "--json",
            ],
            json_output=True,
            check=True,
        )
        if result and "node_id" in result:
            node_map[doc] = result["node_id"]

    console.print(f"\n[green][OK][/green] Successfully ingested {len(node_map)} documents into the graph.")
    console.print(f"Graph location: [cyan]{graph_path}[/cyan]")

    # Placeholder for future relationship inference
    # (e.g. if one document name appears in another document's content, create an edge)
    if len(documents) > 1:
        console.print(
            "[dim]Tip: Future versions will add automatic relationship inference between documents.[/dim]"
        )


if __name__ == "__main__":
    app()
