"""
interactive_demo.py

Holistic, narrative-driven demo for the grap knowledge graph engine.

This script walks the user through a complete story that showcases:
- Ingestion of real documents
- Statistics and segment observability
- Search + hybrid traversal
- Live addition of content
- Delete + tombstone behavior
- Incremental vacuum and space reclamation
- Persistence roundtrips

It is designed to be impressive in a live demo or workshop setting.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich import box

from .grap_cli import run_grap_cli, find_grap_cli, ensure_graph_dir

app = typer.Typer(add_completion=False, help="Run the full holistic grap demo")
console = Console()


SAMPLE_DOCS = Path(__file__).parent.parent / "sample-documents"


def pause(msg: str = "Press Enter to continue..."):
    Prompt.ask(f"\n[dim]{msg}[/dim]", default="")


@app.command()
def main(
    graph_dir: str = typer.Option("demo-graph", "--graph-dir", "-g", help="Where to store the demo graph"),
    auto: bool = typer.Option(False, "--auto", help="Run without pauses (for recording)"),
    reset: bool = typer.Option(False, "--reset", help="Delete the graph directory first and start fresh"),
):
    """Run the complete interactive grap demonstration."""
    graph_path = Path(graph_dir).expanduser().resolve()

    if reset and graph_path.exists():
        console.print(f"[yellow]Resetting demo graph at {graph_path}...[/yellow]")
        shutil.rmtree(graph_path)

    graph_path = ensure_graph_dir(graph_path)

    console.print(Panel.fit(
        "[bold cyan]grap Holistic Demo[/bold cyan]\n\n"
        "A live demonstration of the high-performance Rust knowledge graph engine,\n"
        "driven from Python.",
        border_style="cyan",
        box=box.ROUNDED,
    ))

    if not auto:
        pause()

    # === Step 1: Create initial graph by ingesting curated docs ===
    console.print("\n[bold yellow]Step 1: Ingesting curated documents[/bold yellow]")

    created_nodes = []
    for doc in sorted(SAMPLE_DOCS.glob("*.md")):
        result = run_grap_cli(
            ["add-file", "--graph-dir", str(graph_path), "--path", str(doc), "--json"],
            json_output=True,
            check=True,   # Surface errors during ingestion
        )
        if result and "node_id" in result:
            created_nodes.append((doc.stem, result["node_id"]))

    console.print(f"[green][OK][/green] Ingested {len(created_nodes)} documents into the knowledge graph.")
    if created_nodes:
        console.print("[dim]Node IDs:[/dim]", [nid for _, nid in created_nodes])

    if not auto:
        pause()

    # === Step 2: Show rich statistics ===
    console.print("[bold yellow]Step 2: Exploring statistics and segment information[/bold yellow]")
    run_grap_cli(["stats", "--graph-dir", str(graph_path), "--segments"], json_output=False)
    console.print()

    if not auto:
        pause()

    # === Step 3: Interesting queries ===
    console.print("[bold yellow]Step 3: Hybrid search + graph traversal[/bold yellow]")

    queries = [
        "ownership",
        "borrow checker",
        "memory safety",
    ]

    for q in queries:
        console.print(f"\n[cyan]Query:[/cyan] {q}")
        run_grap_cli(
            ["query", "--graph-dir", str(graph_path), "--traverse", q],
            json_output=False,
        )
        if not auto:
            time.sleep(0.8)

    if not auto:
        pause()

    # === Step 4: Live addition ===
    console.print("\n[bold yellow]Step 4: Adding new content live[/bold yellow]")

    new_doc = Path(graph_path) / "new-concept.md"
    new_doc.write_text(
        "# Concurrency in Rust\n\n"
        "Rust makes fearless concurrency possible through the type system and ownership.\n"
        "Data races are impossible by construction."
    )

    run_grap_cli(
        ["add-file", "--graph-dir", str(graph_path), "--path", str(new_doc), "--label", "Concurrency"],
        json_output=False,
    )

    console.print("[green][OK][/green] Added new document about concurrency.")

    if not auto:
        pause()

    # === Step 5: Delete + incremental vacuum (the heart of the 0.2 demo) ===
    console.print("\n[bold yellow]Step 5: Delete + incremental vacuum[/bold yellow]")

    if created_nodes:
        # Delete the last ingested document (usually "lifetimes" or similar)
        label, node_id = created_nodes[-1]
        console.print(f"Deleting node [cyan]{node_id}[/cyan] (\"{label}\") — this creates a tombstone...")

        run_grap_cli(
            ["delete-node", "--graph-dir", str(graph_path), str(node_id)],
            json_output=False,
            check=False,
        )

        console.print("\n[bold]Before vacuum[/bold] (note increased delete ratio):")
        run_grap_cli(["stats", "--graph-dir", str(graph_path), "--segments"], json_output=False)

        if not auto:
            pause("Press Enter to run vacuum...")

        console.print("\n[bold]Running incremental vacuum...[/bold]")
        run_grap_cli(["vacuum", "--graph-dir", str(graph_path), "--show-segments"], json_output=False)

        console.print("\n[bold]After vacuum[/bold] (space reclaimed, segments may have merged or shrunk):")
        run_grap_cli(["stats", "--graph-dir", str(graph_path), "--segments"], json_output=False)
    else:
        console.print("[yellow]No tracked nodes to delete — skipping live delete demo.[/yellow]")

    if not auto:
        pause()

    # === Step 6: Persistence roundtrip ===
    console.print("\n[bold yellow]Step 6: Persistence & closing thoughts[/bold yellow]")
    console.print(
        "Everything you just did (including on-disk segments, tombstones, and vacuum state) "
        "was durably written to disk using `save_to_dir`."
    )
    console.print(f"\nYou can now exit and later continue with:\n  python -m demo.python.interactive_demo --graph-dir {graph_path}")

    if not auto:
        pause()

    # === Step 7: Visualization (new in Phase 4) ===
    console.print("\n[bold yellow]Step 7: Generating graph visualization[/bold yellow]")
    try:
        from . import visualize as viz

        dot_path = graph_path / "demo-graph.dot"
        viz.write_dot(graph_path, dot_path)

        console.print(f"[green]✓[/green] Visualization DOT file generated:")
        console.print(f"  {dot_path}")
        console.print("\nYou can render it with:")
        console.print(f"  python -m demo.python.visualize --graph-dir {graph_path} --render graph.png")

    except Exception as e:
        console.print(f"[yellow]Visualization step skipped:[/yellow] {e}")
        console.print("You can still generate it manually with:")
        console.print(f"  python -m demo.python.visualize --graph-dir {graph_path} --output demo-graph.dot")

    console.print("\n[bold green]Demo complete![/bold green]")
    console.print(
        "You have experienced: document ingestion → rich observability → hybrid search → "
        "live modification → delete + real incremental vacuum → persistence + visualization."
    )


if __name__ == "__main__":
    app()
