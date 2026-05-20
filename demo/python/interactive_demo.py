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

    # Build a set of existing labels so repeated runs without --reset are idempotent
    existing_labels: set[str] = set()
    try:
        export_result = run_grap_cli(
            ["export", "--graph-dir", str(graph_path), "--format", "json"],
            json_output=True,
            check=False,
        )
        if export_result and "nodes" in export_result:
            for node in export_result.get("nodes", []):
                if lbl := node.get("label"):
                    existing_labels.add(lbl)
    except Exception:
        pass  # brand new or empty graph

    created_nodes = []
    skipped = 0
    for doc in sorted(SAMPLE_DOCS.glob("*.md")):
        label = doc.name  # add-file uses filename as label when none is provided
        if label in existing_labels:
            skipped += 1
            continue

        result = run_grap_cli(
            ["add-file", "--graph-dir", str(graph_path), "--path", str(doc), "--json"],
            json_output=True,
            check=True,
        )
        if result and "node_id" in result:
            created_nodes.append((doc.stem, result["node_id"]))
            existing_labels.add(label)

    if created_nodes:
        console.print(f"[green][OK][/green] Ingested {len(created_nodes)} new documents into the knowledge graph.")
        if created_nodes:
            console.print("[dim]New node IDs:[/dim]", [nid for _, nid in created_nodes])
    else:
        console.print("[dim]Curated documents already present — skipping re-ingestion.[/dim]")

    if skipped:
        console.print(f"[dim]Skipped {skipped} already-ingested document(s).[/dim]")

    if not auto:
        pause()

    # === Step 2: Show rich statistics ===
    console.print("[bold yellow]Step 2: Exploring statistics and segment information[/bold yellow]")
    console.print(
        "[dim]This shows the on-disk segmented text index (grap 0.2).\n"
        "Each segment is an immutable inverted index. 'live' = usable documents, 'deleted' = tombstones.\n"
        "The tier column shows how the background merge policy has grouped segments.\n"
        "High delete_ratio segments will be compacted during vacuum.[/dim]\n"
    )
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
    console.print(
        "[dim]Deletes are cheap: we only write a tombstone bit. The document is logically gone\n"
        "but its bytes stay in the segment until vacuum decides it is worth rewriting.\n"
        "Vacuum only touches segments whose delete_ratio exceeds a threshold (incremental, not full rewrite).[/dim]\n"
    )

    if created_nodes:
        # Delete the last ingested document (usually "lifetimes" or similar)
        label, node_id = created_nodes[-1]
        console.print(f"Deleting node [cyan]{node_id}[/cyan] (\"{label}\") — this creates a tombstone...")

        run_grap_cli(
            ["delete-node", "--graph-dir", str(graph_path), str(node_id)],
            json_output=False,
            check=False,
        )

        console.print("\n[bold]Before vacuum[/bold] — note the raised delete ratio on one or more segments:")
        run_grap_cli(["stats", "--graph-dir", str(graph_path), "--segments"], json_output=False)

        if not auto:
            pause("Press Enter to run vacuum...")

        console.print("\n[bold]Running incremental vacuum...[/bold]")
        console.print("[dim]Only segments above the delete threshold are compacted. Live documents are copied into new segments; tombstones are dropped.[/dim]")
        run_grap_cli(["vacuum", "--graph-dir", str(graph_path), "--show-segments"], json_output=False)

        console.print("\n[bold]After vacuum[/bold] — delete ratios dropped and space was reclaimed:")
        run_grap_cli(["stats", "--graph-dir", str(graph_path), "--segments"], json_output=False)

        console.print(
            "\n[green]This is the key 0.2 feature:[/green] cheap, safe deletes + partial, incremental compaction\n"
            "instead of rewriting the entire index on every change."
        )
    else:
        console.print("[yellow]No tracked nodes to delete — skipping live delete demo.[/yellow]")

    if not auto:
        pause()

    # === Step 6: Persistence roundtrip ===
    console.print("\n[bold yellow]Step 6: Persistence round-trip[/bold yellow]")
    console.print(
        "Every mutation (add, delete, vacuum) was persisted using the atomic `save_to_dir` mechanism.\n"
        "The new per-file staging strategy (writing into `.grap-new/`, then renaming files individually,\n"
        "with `graph.meta` as the final commit record) is what made this reliable on Windows."
    )
    console.print(f"\n[dim]You can exit now and later continue exactly where you left off with:[/dim]\n  python -m demo.python.interactive_demo --graph-dir {graph_path}")

    if not auto:
        pause()

    # === Step 7: Visualization (new in Phase 4) ===
    console.print("\n[bold yellow]Step 7: Generating graph visualization[/bold yellow]")
    try:
        from . import visualize as viz

        dot_path = graph_path / "demo-graph.dot"
        viz.write_dot(graph_path, dot_path)

        console.print(f"[green][OK][/green] Visualization DOT file generated:")
        console.print(f"  {dot_path}")
        console.print("\nYou can render it with:")
        console.print(f"  python -m demo.python.visualize --graph-dir {graph_path} --output graph.png")

    except Exception as e:
        console.print(f"[yellow]Visualization step skipped:[/yellow] {e}")
        console.print("You can still generate it manually with:")
        console.print(f"  python -m demo.python.visualize --graph-dir {graph_path} --output demo-graph.dot")

    console.print("\n[bold green]Demo complete![/bold green]")
    console.print(
        "\n[bold]What you just saw:[/bold]\n"
        "• Real Markdown documents ingested with automatic relationship inference\n"
        "• On-disk segmented inverted index (tiered segments, live/delete counts)\n"
        "• Hybrid text + graph search with traversal\n"
        "• Cheap deletes via tombstones + incremental vacuum (only dirty segments rewritten)\n"
        "• Durable persistence with a Windows-friendly per-file atomic save strategy\n"
        "• Live visualization of the resulting graph\n"
    )
    console.print(
        "[dim]This is the complete 0.2 story: high-performance, observable, incrementally maintainable\n"
        "knowledge graphs that survive crashes and work reliably on real operating systems.[/dim]"
    )


if __name__ == "__main__":
    app()
