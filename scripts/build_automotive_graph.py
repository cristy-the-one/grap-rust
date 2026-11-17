#!/usr/bin/env python3
"""Build one automotive graph from DBC, ARXML, and ReqIF files.

Steps, in order: read, project, save the graph and projection.json (graph
first), statements, mentions, stage-1 identifier resolution, statement
edges, and the eight questions. A failing step stops the run and names
that step. structural_missing above zero stops at the edges step. On
load, every projection node's engine id must be a live graph node with
the same label.

``--provider stub`` is the default. The stub marks nothing. No step reads
gold/. Item text is not printed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

_MCP = Path(__file__).resolve().parents[1] / "mcp-server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

import pygrap  # noqa: E402

from grap_mcp.kee.graph_store import GraphStore  # noqa: E402
from grap_mcp.kee.llm import OpenAICompatProvider, StubProvider  # noqa: E402
from grap_mcp.kee.resolution.exact import (  # noqa: E402
    IdentifiedEntity,
    SourceScope,
    link_identifiers,
)
from grap_mcp.kee.resolution.tables import ResolutionTables  # noqa: E402
from grap_mcp.kee.statements.edges import write_statement_edges  # noqa: E402
from grap_mcp.kee.statements.marking import mark_items  # noqa: E402
from grap_mcp.kee.statements.mentions import write_statement_mentions  # noqa: E402
from grap_mcp.kee.statements.tables import StatementTables  # noqa: E402
from grap_mcp.kee.statements.units import label_item  # noqa: E402
from grap_mcp.readers.arxml import read_arxml  # noqa: E402
from grap_mcp.readers.dbc import read_dbc  # noqa: E402
from grap_mcp.readers.reqif import read_reqif  # noqa: E402
from grap_mcp.schema.project import project_items  # noqa: E402
from grap_mcp.schema.projection_store import (  # noqa: E402
    load_projection,
    save_projection,
)
from grap_mcp.schema.questions import run_checks  # noqa: E402

# These are not entities a mention can resolve to. Item shares a native id
# with the typed node; Source names the file; unresolved is a placeholder.
_NOT_ENTITIES = frozenset({"Item", "Source", "unresolved", "Statement"})


class StepFailure(Exception):
    """A pipeline step failed. The run stops and names this step."""

    def __init__(self, number: int, name: str, message: str):
        self.number = number
        self.name = name
        self.message = message
        super().__init__(message)


class ProjectionMismatch(Exception):
    """The projection names a node the graph does not have under that label."""

    def __init__(self, mismatched: int, nodes: int):
        self.mismatched = mismatched
        self.nodes = nodes
        super().__init__(
            f"projection does not match graph mismatched={mismatched} "
            f"(n={nodes} nodes)"
        )


def save_graph_and_projection(graph_dir, projection, kg) -> None:
    """Write the graph, then projection.json beside that directory."""
    graph_dir = Path(graph_dir)
    graph_dir.mkdir(parents=True, exist_ok=True)
    kg.save()
    save_projection(graph_dir.parent / "projection.json", projection)


def load_graph_and_projection(graph_dir):
    """Load the graph and projection.json and refuse a label mismatch.

    Every projection node's engine id must be a live node whose label is
    the projection label (or the node type, when the row has no label).
    """
    graph_dir = Path(graph_dir)
    kg = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    projection = load_projection(graph_dir.parent / "projection.json")
    nodes = projection["nodes"]
    mismatched = sum(1 for node in nodes if not _same_label(kg, node))
    if mismatched:
        raise ProjectionMismatch(mismatched, len(nodes))
    return kg, projection


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _execute(args)
    except ProjectionMismatch as exc:
        print(str(exc))
        return 1
    except StepFailure as exc:
        print(f"step {exc.number} ({exc.name}) failed: {exc.message}")
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the automotive graph from DBC, ARXML, and ReqIF files.",
    )
    parser.add_argument("--dbc", action="append", default=[], help="DBC file (repeatable)")
    parser.add_argument("--arxml", action="append", default=[], help="ARXML file (repeatable)")
    parser.add_argument("--reqif", action="append", default=[], help="ReqIF file (repeatable)")
    parser.add_argument("--release", required=True, help="Release for items that state none")
    parser.add_argument("--out", required=True, type=Path, help="Output directory")
    parser.add_argument(
        "--provider",
        choices=("stub", "local"),
        default="stub",
        help="stub marks nothing; local calls the configured local model",
    )
    return parser


def _execute(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    graph_dir = out / "graph"
    _guard(3, "save", lambda: _load_if_present(out, graph_dir))

    items = _guard(1, "read", lambda: _read(args))
    # The previous pair matched. Replace its graph with this run's nodes
    # once the sources have been read.
    if graph_dir.exists():
        shutil.rmtree(graph_dir)
    print("step 2 (project)")
    kg, projection = _guard(2, "project", lambda: _project(graph_dir, items))
    _guard(3, "save", lambda: _save_step(graph_dir, projection, kg))

    provider = _provider(args.provider)
    with GraphStore(str(out / "graph_store.db")) as store:
        statement_tables = StatementTables(store)
        resolution = ResolutionTables(store)
        eligible = [item for item in items if item.kind_hint in _statement_item_kinds()]
        _guard(4, "statements", lambda: _statements(eligible, provider, statement_tables))
        mentions = _guard(
            5, "mentions", lambda: _mentions(eligible, statement_tables, resolution),
        )
        _guard(
            6, "resolve",
            lambda: _resolve(mentions, items, projection, resolution),
        )
        _guard(
            7, "edges",
            lambda: _edges(eligible, statement_tables, resolution, kg, projection, graph_dir),
        )
    _guard(8, "questions", lambda: _questions(kg, projection))


def _guard(number: int, name: str, fn):
    try:
        return fn()
    except (StepFailure, ProjectionMismatch):
        raise
    except Exception as exc:
        raise StepFailure(number, name, f"{type(exc).__name__}: {exc}") from exc


def _load_if_present(out: Path, graph_dir: Path) -> None:
    projection_path = out / "projection.json"
    if not graph_dir.exists() and not projection_path.exists():
        return
    if not graph_dir.exists() or not projection_path.exists():
        raise StepFailure(3, "save", "graph and projection.json are saved together")
    load_graph_and_projection(graph_dir)


def _read(args) -> list:
    if not args.dbc and not args.arxml and not args.reqif:
        raise StepFailure(1, "read", "no source files")
    items = []
    if args.dbc:
        found = _read_files(read_dbc, args.dbc, args.release)
        _print_read("dbc", found)
        items.extend(found)
    if args.arxml:
        found = _read_arxml(args.arxml, args.release)
        _print_read("arxml", found)
        items.extend(found)
    if args.reqif:
        found = _read_files(read_reqif, args.reqif, args.release)
        _print_read("reqif", found)
        items.extend(found)
    return items


def _read_files(reader, paths, release: str) -> list:
    items = []
    for path in paths:
        items.extend(reader(path, corpus_root=Path(path).parent))
    return _with_release(items, release)


def _read_arxml(paths, release: str) -> list:
    ordered = sorted((Path(path) for path in paths), key=lambda path: path.as_posix())
    source_id = ordered[0].stem
    items, _report = read_arxml(ordered, source_id=source_id)
    return _with_release(list(items), release)


def _with_release(items, release: str) -> list:
    """Give a release to items that do not state one. Placeholders stay unset."""
    stamped = []
    for item in items:
        if item.release is None and item.kind_hint != "unresolved":
            stamped.append(replace(item, release=release))
        else:
            stamped.append(item)
    return stamped


def _print_read(reader: str, items) -> None:
    n_items = len(items)
    n_links = sum(len(item.links) for item in items)
    documents = len({item.source_id for item in items})
    print(
        f"step 1 (read) {reader} items={n_items} "
        f"(n={n_items} items, documents={documents}) "
        f"links={n_links} (n={n_links} links, documents={documents})"
    )


def _project(graph_dir: Path, items):
    kg = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    return kg, project_items(items, kg)


def _save_step(graph_dir: Path, projection, kg) -> None:
    print("step 3 (save)")
    save_graph_and_projection(graph_dir, projection, kg)
    nodes = len(projection["nodes"])
    documents = projection["counts"]["documents"]
    print(
        f"step 3 (save) nodes={nodes} (n={nodes} nodes, documents={documents})"
    )


def _provider(name: str):
    if name == "stub":
        # "{}" is not a marking reply, so no unit is marked.
        return StubProvider()
    if name == "local":
        return OpenAICompatProvider()
    raise StepFailure(4, "statements", f"unknown provider {name}")


def _statement_item_kinds() -> frozenset[str]:
    path = Path(label_item.__code__.co_filename).with_name("rules.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    kinds = payload["item_kinds"]
    if not isinstance(kinds, list) or any(not isinstance(kind, str) for kind in kinds):
        raise ValueError("item_kinds must be a list of strings")
    return frozenset(kinds)


def _statements(eligible, provider, statement_tables) -> None:
    units = []
    for item in eligible:
        units.extend(label_item(item))
    sent = sum(1 for unit in units if unit.to_model)
    result = mark_items(eligible, provider, statement_tables)
    stored = sum(len(mark.statement_ids) for mark in result.items)
    review = sum(len(mark.review) for mark in result.items)
    n_units = len(units)
    documents = len({item.source_id for item in eligible})
    print(
        f"step 4 (statements) units={n_units} sent={sent} "
        f"stored={stored} review={review} "
        f"(n={n_units} units, documents={documents})"
    )


def _mentions(eligible, statement_tables, resolution) -> list:
    found = []
    written = 0
    for item in eligible:
        statements = statement_tables.statements_for_item(item.source_id, item.item_id)
        ids = write_statement_mentions(
            resolution, item.text, item.source_id, item.item_id, statements,
        )
        written += len(ids)
        found.extend(resolution.mentions_for_item(item.source_id, item.item_id))
    documents = len({item.source_id for item in eligible})
    print(
        f"step 5 (mentions) mentions_written={written} "
        f"(n={written} mentions, documents={documents})"
    )
    return found


def _resolve(mentions, items, projection, resolution) -> None:
    print("step 6 (resolve)")
    link_identifiers(
        resolution,
        mentions,
        _entities(projection),
        sources=_sources(items),
        decided_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _entities(projection) -> list[IdentifiedEntity]:
    found = []
    seen = set()
    for node in projection["nodes"]:
        if not isinstance(node, dict):
            continue
        type_name = node.get("type")
        if type_name in _NOT_ENTITIES or not isinstance(type_name, str):
            continue
        key = node.get("key")
        if not isinstance(key, str) or key in seen:
            continue
        source_id = node.get("source_id")
        native_id = node.get("native_id")
        if not isinstance(source_id, str) or not isinstance(native_id, str):
            continue
        seen.add(key)
        found.append(IdentifiedEntity(
            type=type_name,
            native_id=native_id,
            source_id=source_id,
            release=_node_release(node, source_id),
        ))
    return found


def _node_release(node, source_id: str) -> str | None:
    scope = node.get("scope")
    prefix = f"{source_id}@"
    if isinstance(scope, str) and scope.startswith(prefix) and len(scope) > len(prefix):
        return scope[len(prefix):]
    return None


def _sources(items) -> dict[str, SourceScope]:
    files: dict[str, set[str]] = {}
    releases: dict[str, set] = {}
    for item in items:
        if item.kind_hint == "unresolved":
            continue
        files.setdefault(item.source_id, set()).add(item.file)
        releases.setdefault(item.source_id, set()).add(item.release)
    scopes = {}
    for source_id, names in files.items():
        rels = releases[source_id]
        file_set = next(iter(names)) if len(names) == 1 else None
        release = next(iter(rels)) if len(rels) == 1 else None
        scopes[source_id] = SourceScope(file_set=file_set, release=release)
    return scopes


def _edges(eligible, statement_tables, resolution, kg, projection, graph_dir) -> None:
    print("step 7 (edges)")
    missing = 0
    n_statements = 0
    for item in eligible:
        statements = statement_tables.statements_for_item(item.source_id, item.item_id)
        report = write_statement_edges(
            resolution,
            kg,
            projection,
            item.source_id,
            item.item_id,
            statements,
            kind_hint=item.kind_hint,
            release=item.release,
        )
        structural = report["structural_missing"]
        if isinstance(structural, int) and structural > 0:
            missing += structural
        n_statements += report["n_statements"]
    if missing > 0:
        raise StepFailure(
            7,
            "edges",
            f"structural_missing={missing} (n={n_statements} statements)",
        )
    save_graph_and_projection(graph_dir, projection, kg)


def _questions(kg, projection) -> None:
    print("step 8 (questions)")
    run_checks(kg, projection)


def _same_label(kg, node) -> bool:
    if not isinstance(node, dict):
        return False
    engine_id = node.get("engine_id")
    if isinstance(engine_id, bool) or not isinstance(engine_id, int) or engine_id < 0:
        return False
    label = node.get("label")
    if not isinstance(label, str) or label == "":
        label = node.get("type")
    if not isinstance(label, str) or label == "":
        return False
    try:
        if kg.is_node_deleted(engine_id):
            return False
        return kg.get_node_label(engine_id) == label
    except (ValueError, OverflowError, TypeError):
        return False


if __name__ == "__main__":
    sys.exit(main())
