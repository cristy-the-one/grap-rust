"""select_requirements and fetch_context (P-12).

Against the projected graph. No model. ``fetch_context`` is schema §9
question 4, the same walk as ``tester_needs``: nodes within two outgoing
steps of a requirement's statements, over ``about``, ``refers_to``,
``applies_in``, ``configured_by``, ``raises``, ``has_type`` and
``part_of``. Statements themselves are not returned.

The operation returns those node keys, grouped by requirement.
``write_testpoints`` writes the scorer's document. ``needs`` is each
node's ``native_id``, which is the identifier Sheet 4 compares. A full
key would not match a labeller's id. ``testable`` is omitted.

Scope is one of three forms:

- a list of ids, or ``{"ids": [...]}`` — each id is a Requirement node
  key or a ``native_id``. Every match is returned. An id that matches no
  Requirement is an error, and nothing is returned.
- ``{"source": ...}`` — a ``source_id``, or, when that matches no node, a
  scope (``source_id@release``). A source that exists and has no
  Requirement returns an empty list. An unknown source is an error.
- ``{"parent": ...}`` — that Requirement, plus every Requirement that
  reaches it by outgoing ``traces_to`` (lower → upper), repeated. A
  ``part_of`` child is not included. The same native id on more than one
  node is an error until the caller names a node key.

Judgements this card had to make, and the tests lock in:

- Parent follows ``traces_to``, not ``part_of``. ReqIF hierarchy is a
  heading path; the relation the schema calls lower-to-upper is
  ``traces_to``.
- ``needs`` carries ``native_id``, in node-key order, one per node.
  Repeated native ids in one requirement are written once.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from grap_mcp.kee.working_graph import _relation_filter
from grap_mcp.schema.questions import tester_needs

_SCOPE_KEYS = ("ids", "source", "parent")
_FORM = (
    "scope must be a list of ids, or a mapping with exactly one of ids, source, parent"
)


class ScopeError(Exception):
    """The scope or the requirement list does not resolve. Nothing was fetched."""


class GraphOperations:
    """The two graph operations, as a callable the interpreter can run.

    Any other operation, including ``invoke_model``, is refused.
    """

    def __init__(self, kg, projection: Mapping[str, Any]):
        self.kg = kg
        self.projection = projection

    def __call__(self, operation: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "select_requirements":
            return {
                "requirements": select_requirements(self.kg, self.projection, inputs["scope"]),
            }
        if operation == "fetch_context":
            return {
                "context": fetch_context(self.kg, self.projection, inputs["requirements"]),
            }
        raise ScopeError(
            f"operation {operation!r} is not one of select_requirements or fetch_context"
        )


def select_requirements(kg, projection: Mapping[str, Any], scope: Any) -> list[dict]:
    """Resolve ``scope`` to Requirement records. Order is described on the module."""
    if isinstance(scope, list):
        return _by_ids(projection, scope)
    if isinstance(scope, Mapping) and len(scope) == 1:
        kind = next(iter(scope))
        if kind == "ids":
            return _by_ids(projection, scope["ids"])
        if kind == "source":
            return _by_source(projection, scope["source"])
        if kind == "parent":
            return _by_parent(kg, projection, scope["parent"])
    raise ScopeError(_FORM)


def fetch_context(kg, projection: Mapping[str, Any], requirements: Any) -> dict:
    """Node keys within two steps of each requirement's statements.

    Group order is the order of ``requirements``. Keys inside a group are
    sorted. A requirement with no statements has an empty group.
    """
    if not isinstance(requirements, list):
        raise ScopeError("requirements must be a list of requirement records")
    by_key = {node["key"]: node for node in projection["nodes"]}
    by_engine = {node["engine_id"]: node for node in projection["nodes"]}
    groups = []
    for req in requirements:
        if not isinstance(req, Mapping) or not isinstance(req.get("key"), str):
            raise ScopeError("requirements entry must name a requirement key")
        node = by_key.get(req["key"])
        if node is None or node["type"] != "Requirement":
            raise ScopeError(f"requirement {req['key']!r} matches nothing")
        if "source_id" in req and req["source_id"] != node["source_id"]:
            raise ScopeError(f"requirement {req['key']!r} source_id does not match the graph")
        if "item_id" in req and req["item_id"] != node["native_id"]:
            raise ScopeError(f"requirement {req['key']!r} item_id does not match the graph")
        found = tester_needs(kg, node["engine_id"])
        absent = [engine_id for engine_id in found if engine_id not in by_engine]
        if absent:
            raise ScopeError(
                f"requirement {node['key']!r} context has {len(absent)} nodes "
                f"that are not in the projection"
            )
        groups.append({
            "key": node["key"],
            "source_id": node["source_id"],
            "item_id": node["native_id"],
            "nodes": sorted(by_engine[engine_id]["key"] for engine_id in found),
        })
    return {"requirements": groups}


def as_testpoints(projection: Mapping[str, Any], context: Mapping[str, Any]) -> dict:
    """The scorer document. ``needs`` is native ids, in node-key order."""
    by_key = {node["key"]: node for node in projection["nodes"]}
    rows = []
    for group in context["requirements"]:
        needs: list[str] = []
        seen: set[str] = set()
        for key in group["nodes"]:
            node = by_key.get(key)
            if node is None:
                raise ScopeError(f"context node {key!r} is not in the projection")
            native = node["native_id"]
            if native in seen:
                continue
            seen.add(native)
            needs.append(native)
        rows.append({
            "source_id": group["source_id"],
            "item_id": group["item_id"],
            "needs": needs,
        })
    return {"testpoints": rows}


def format_summary(document: Mapping[str, Any]) -> str:
    """Counts beside the testpoints document. Nothing selected prints ``n/a``."""
    rows = list(document["testpoints"])
    n_requirements = len(rows)
    n_fetched = sum(len(row["needs"]) for row in rows)
    n_docs = len({row["source_id"] for row in rows})
    counts = f"n_requirements={n_requirements} n_fetched={n_fetched} n_docs={n_docs}"
    if n_requirements == 0:
        return f"testpoints=n/a {counts}"
    return f"testpoints {counts}"


def write_testpoints(path: str | Path, projection: Mapping[str, Any], context: Mapping[str, Any]) -> dict:
    """Write the scorer document and print its denominators."""
    document = as_testpoints(projection, context)
    Path(path).write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(format_summary(document))
    return document


def _by_ids(projection: Mapping[str, Any], ids: Any) -> list[dict]:
    if not isinstance(ids, list) or not ids:
        raise ScopeError("scope ids is empty")
    requirements = _requirements(projection)
    by_key = {node["key"]: node for node in requirements}
    chosen: list[dict] = []
    seen: set[str] = set()
    missing: list[Any] = []
    for raw in ids:
        if not isinstance(raw, str) or raw == "":
            missing.append(raw)
            continue
        if raw in by_key:
            _add(chosen, seen, by_key[raw])
            continue
        matches = sorted(
            (node for node in requirements if node["native_id"] == raw),
            key=lambda node: node["key"],
        )
        if not matches:
            missing.append(raw)
            continue
        for node in matches:
            _add(chosen, seen, node)
    if missing:
        shown = ", ".join(repr(item) for item in missing)
        raise ScopeError(f"requirement ids match nothing: {shown}")
    return chosen


def _by_source(projection: Mapping[str, Any], source: Any) -> list[dict]:
    if not isinstance(source, str) or source == "":
        raise ScopeError("scope source must be a source id")
    known = any(
        node["source_id"] == source or node["scope"] == source
        for node in projection["nodes"]
    )
    if not known:
        raise ScopeError(f"source {source!r} matches nothing")
    requirements = _requirements(projection)
    chosen = [node for node in requirements if node["source_id"] == source]
    if not chosen:
        chosen = [node for node in requirements if node["scope"] == source]
    chosen.sort(key=lambda node: node["key"])
    return [_record(node) for node in chosen]


def _by_parent(kg, projection: Mapping[str, Any], parent: Any) -> list[dict]:
    if not isinstance(parent, str) or parent == "":
        raise ScopeError("scope parent must be a requirement id")
    requirements = _requirements(projection)
    by_key = {node["key"]: node for node in requirements}
    if parent in by_key:
        starts = [by_key[parent]]
    else:
        starts = [node for node in requirements if node["native_id"] == parent]
    if not starts:
        raise ScopeError(f"parent requirement {parent!r} matches nothing")
    if len(starts) > 1:
        keys = ", ".join(sorted(node["key"] for node in starts))
        raise ScopeError(
            f"parent requirement {parent!r} matches {len(starts)} nodes ({keys}); name a node key"
        )
    by_engine = {node["engine_id"]: node for node in requirements}
    start = starts[0]
    found = {start["engine_id"]: start}
    pending = [start["engine_id"]]
    while pending:
        current = pending.pop()
        for nxt in _incoming(kg, current, "traces_to"):
            if nxt in found or nxt not in by_engine:
                continue
            found[nxt] = by_engine[nxt]
            pending.append(nxt)
    ordered = sorted(found.values(), key=lambda node: node["key"])
    return [_record(node) for node in ordered]


def _requirements(projection: Mapping[str, Any]) -> list[dict]:
    return [node for node in projection["nodes"] if node["type"] == "Requirement"]


def _record(node: Mapping[str, Any]) -> dict:
    return {
        "key": node["key"],
        "source_id": node["source_id"],
        "item_id": node["native_id"],
    }


def _add(chosen: list[dict], seen: set[str], node: Mapping[str, Any]) -> None:
    if node["key"] in seen:
        return
    seen.add(node["key"])
    chosen.append(_record(node))


def _incoming(kg, node: int, relation: str) -> list[int]:
    name, value = _relation_filter(relation)
    found: list[int] = []
    seen: set[int] = set()
    for nxt, _strength in kg.predecessors_by_relation(node, name, value):
        if nxt in seen:
            continue
        seen.add(nxt)
        found.append(nxt)
    return found
