"""
visualize.py

Simple visualization helper for grap graphs.

Usage examples:
    python -m demo.python.visualize --graph-dir ./my-kg --output graph.dot
    python -m demo.python.visualize --graph-dir ./my-kg --render graph.png

It uses the `grap-cli export` command under the hood.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from .grap_cli import find_grap_cli

app = typer.Typer(add_completion=False, help="Visualize a grap knowledge graph")
console = Console()


def _export_graph(graph_dir: Path) -> dict[str, Any]:
    binary = find_grap_cli()
    cmd = [str(binary), "--graph-dir", str(graph_dir), "export", "--format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print("[red]Failed to export graph[/red]")
        console.print(result.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def generate_dot(graph_dir: Path) -> str:
    """Generate Graphviz DOT content for a grap graph. Reusable from other scripts."""
    data = _export_graph(graph_dir)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    dot_lines = ["digraph G {", '  rankdir=LR;', '  node [shape=box, style=rounded];']

    for n in nodes:
        nid = n["id"]
        label = n.get("label") or f"Node {nid}"
        snippet = n.get("snippet", "").replace('"', "'").replace("\n", " ")[:60]
        dot_lines.append(f'  {nid} [label="{label}\\n{snippet}"];')

    for e in edges:
        src = e["source"]
        tgt = e["target"]
        strength = e.get("strength", 0.5)
        dot_lines.append(f'  {src} -> {tgt} [label="{strength:.2f}"];')

    dot_lines.append("}")
    return "\n".join(dot_lines)


def write_dot(graph_dir: Path, output_path: Path) -> Path:
    """Generate and write a .dot file for the given graph. Returns the path written."""
    dot_content = generate_dot(graph_dir)
    output_path.write_text(dot_content, encoding="utf-8")
    return output_path


@app.command()
def main(
    graph_dir: str = typer.Option(..., "--graph-dir", "-g"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file (.dot, .png, .svg)"),
    render: bool = typer.Option(False, "--render", help="Try to render using graphviz"),
):
    """Export or render a grap graph."""
    graph_path = Path(graph_dir).expanduser().resolve()
    data = _export_graph(graph_path)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    dot_lines = ["digraph G {", '  rankdir=LR;', '  node [shape=box, style=rounded];']

    for n in nodes:
        nid = n["id"]
        label = n.get("label") or f"Node {nid}"
        snippet = n.get("snippet", "").replace('"', "'").replace("\n", " ")[:60]
        dot_lines.append(f'  {nid} [label="{label}\\n{snippet}"];')

    for e in edges:
        src = e["source"]
        tgt = e["target"]
        strength = e.get("strength", 0.5)
        dot_lines.append(f'  {src} -> {tgt} [label="{strength:.2f}"];')

    dot_lines.append("}")

    dot_content = "\n".join(dot_lines)

    if output:
        out_path = Path(output)
        out_path.write_text(dot_content, encoding="utf-8")
        console.print(f"[green]Wrote DOT to {out_path}[/green]")

        if render or out_path.suffix in {".png", ".svg", ".pdf"}:
            try:
                from graphviz import Source
                src = Source(dot_content)
                fmt = out_path.suffix.lstrip(".") or "png"
                src.render(out_path.with_suffix(""), format=fmt, cleanup=True)
                console.print(f"[green]Rendered to {out_path.with_suffix('.' + fmt)}[/green]")
            except ImportError:
                console.print("[yellow]graphviz Python package not installed. Install with `pip install graphviz` to render.[/yellow]")
    else:
        print(dot_content)


if __name__ == "__main__":
    app()
