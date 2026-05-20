"""
stats.py

Beautiful statistics viewer for a grap knowledge graph.
Especially useful for demonstrating the on-disk segmented index and vacuum behavior.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from .grap_cli import run_grap_cli, ensure_graph_dir

app = typer.Typer(add_completion=False, help="Show rich statistics for a grap knowledge graph")
console = Console()


@app.command()
def main(
    graph_dir: str = typer.Option(..., "--graph-dir", "-g", help="Path to the grap graph directory"),
    segments: bool = typer.Option(True, "--segments/--no-segments", help="Show per-segment details"),
):
    """Display node/edge counts and on-disk segment information."""
    data = run_grap_cli(["stats", "--graph-dir", graph_dir, "--segments" if segments else ""])

    if data is None:
        console.print("[red]No data returned from grap-cli[/red]")
        raise typer.Exit(1)

    graph_type = data.get("type", "single")
    node_count = data.get("node_count", 0)
    edge_count = data.get("edge_count", 0)
    segment_count = data.get("segment_count", 0)
    segment_list = data.get("segments") or []

    # Header
    console.print(Panel.fit(
        f"[bold cyan]grap Knowledge Graph Statistics[/bold cyan]\n"
        f"[dim]{graph_dir}[/dim]",
        border_style="cyan"
    ))

    # Summary table
    summary = Table(title="Overview", box=box.SIMPLE_HEAVY)
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", style="green", justify="right")

    summary.add_row("Graph Type", graph_type)
    summary.add_row("Nodes", f"{node_count:,}")
    summary.add_row("Edges", f"{edge_count:,}")
    summary.add_row("Text Segments", str(segment_count))

    console.print(summary)
    console.print()

    if segments and segment_list:
        seg_table = Table(title="Segment Details (On-Disk Index)", box=box.ROUNDED)
        seg_table.add_column("#", style="dim", justify="right")
        seg_table.add_column("Tier", justify="center")
        seg_table.add_column("Total Docs", justify="right")
        seg_table.add_column("Live Docs", justify="right")
        seg_table.add_column("Deleted", justify="right", style="red")
        seg_table.add_column("Delete %", justify="right")
        seg_table.add_column("Approx Size", justify="right", style="yellow")

        for i, seg in enumerate(segment_list):
            total = seg.get("doc_count", 0)
            live = seg.get("live_doc_count", 0)
            deleted = total - live
            delete_ratio = (deleted / total * 100.0) if total > 0 else 0.0
            size_kb = seg.get("size_bytes", 0) / 1024.0

            seg_table.add_row(
                str(i),
                str(seg.get("tier", "?")),
                f"{total:,}",
                f"{live:,}",
                f"{deleted:,}" if deleted > 0 else "0",
                f"{delete_ratio:.1f}%",
                f"{size_kb:,.1f} KB",
            )

        console.print(seg_table)
        console.print(
            "\n[italic dim]High delete ratios are excellent candidates for `vacuum`.[/italic dim]"
        )


if __name__ == "__main__":
    app()
