"""
demo.py

Convenience entrypoint for the holistic grap demo.

Usage examples:
    python -m demo.python.demo --help
    python -m demo.python.demo stats --graph-dir ./my-kg
"""

from __future__ import annotations

import typer
from rich.console import Console

from . import stats, query

console = Console()
app = typer.Typer(
    add_completion=False,
    help="Holistic demo entrypoint for the grap knowledge graph engine"
)


app.command()(stats.main)   # type: ignore
app.command()(query.main)   # type: ignore


@app.command()
def info():
    """Show information about the current demo setup."""
    console.print("[bold cyan]grap Holistic Demo[/bold cyan]")
    console.print("Python layer driving the high-performance Rust `grap` engine.\n")
    console.print("Available commands: [green]stats[/green], [green]query[/green] (more coming)")


if __name__ == "__main__":
    app()
