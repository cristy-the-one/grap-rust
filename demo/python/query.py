"""
query.py

Run text search (and optional graph traversal) against a grap knowledge graph
with nice terminal output.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from .grap_cli import run_grap_cli

app = typer.Typer(add_completion=False, help="Query a grap knowledge graph")
console = Console()


@app.command()
def main(
    query: str = typer.Argument(..., help="Search query (use quotes for phrase search)"),
    graph_dir: str = typer.Option(..., "--graph-dir", "-g"),
    traverse: bool = typer.Option(False, "--traverse", "-t", help="Also run graph traversal from text seeds"),
    max_results: int = typer.Option(10, "--max-results", "-k"),
):
    """Search the knowledge graph and optionally traverse from the best seeds."""
    args = ["query", "--graph-dir", graph_dir, "--max-results", str(max_results)]
    if traverse:
        args.append("--traverse")

    # Pass the actual query as the last positional argument
    result = run_grap_cli(args + [query], json_output=True)

    if not result:
        console.print("[yellow]No results.[/yellow]")
        return

    title = "Hybrid Search + Traversal" if traverse else "Text Search"
    console.print(Panel.fit(f"[bold magenta]{title}[/bold magenta]\n[cyan]{query}[/cyan]"))

    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Node", style="cyan", width=8)
    table.add_column("Score", justify="right", style="green", width=8)
    if traverse:
        table.add_column("Text", style="white")
    else:
        table.add_column("Text", style="white")

    for i, item in enumerate(result):
        node_id = item.get("node_id") or item.get("graph_result", {}).get("node_id")
        score = item.get("fused_score") or item.get("score", 0.0)
        snippet = item.get("text_snippet", "")

        table.add_row(
            str(i),
            str(node_id),
            f"{score:.3f}",
            snippet[:180].replace("\n", " "),
        )

    console.print(table)
    console.print(f"\n[italic dim]Total results: {len(result)}[/italic dim]")


if __name__ == "__main__":
    app()
