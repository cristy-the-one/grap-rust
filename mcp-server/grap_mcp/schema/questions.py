"""Answer the eight questions in schema §9.

Each question follows the path in that row. The walk uses
``working_graph._relation_filter``, the lookup pair of
``_relation_properties``. This module does not encode relations.

A question whose starting population is empty prints ``n/a``. A count is
printed with the number of rows and documents behind it.
"""

from __future__ import annotations

from grap_mcp.kee.working_graph import _relation_filter

# §9 question 4. Outgoing only, and no further than two steps.
_NEEDS = (
    "about",
    "refers_to",
    "applies_in",
    "configured_by",
    "raises",
    "has_type",
    "part_of",
)


def upstream_requirements(kg, requirement: int) -> list[int]:
    """Requirement —traces_to→ Requirement, repeated. The start is not included."""
    found: list[int] = []
    seen = {requirement}
    pending = [requirement]
    while pending:
        node = pending.pop()
        for nxt in _out(kg, node, "traces_to"):
            if nxt in seen or _label(kg, nxt) != "Requirement":
                continue
            seen.add(nxt)
            found.append(nxt)
            pending.append(nxt)
    return _by_name(kg, found)


def involved_signals(kg, requirement: int) -> list[dict]:
    """Requirement —has_statement→ Statement —about/refers_to→ Signal —has_type→ DataType."""
    found: dict[int, set[str]] = {}
    for statement in _out(kg, requirement, "has_statement"):
        if _label(kg, statement) != "Statement":
            continue
        for relation in ("about", "refers_to"):
            for signal in _out(kg, statement, relation):
                if _label(kg, signal) != "Signal":
                    continue
                found.setdefault(signal, set()).add(relation)
    rows = []
    for signal, relations in found.items():
        scaling = tuple(sorted(
            _native(kg, datatype)
            for datatype in _out(kg, signal, "has_type")
            if _label(kg, datatype) == "DataType"
        ))
        rows.append({
            "native_id": _native(kg, signal),
            "engine_id": signal,
            "relations": tuple(sorted(relations)),
            "scaling": scaling,
        })
    rows.sort(key=lambda row: (row["native_id"], row["engine_id"]))
    return rows


def signal_travel(kg, signal: int, projection: dict) -> list[dict]:
    """Signal —part_of→ Pdu —part_of→ Frame —transmitted_on→ Network.

    The frame identifier is the triggering edge's ``IDENTIFIER`` attribute.
    It lives on the projection record, not on the engine edge. Absent is
    ``None``.
    """
    index = {node["engine_id"]: node for node in projection["nodes"]}
    rows = []
    for pdu in _out(kg, signal, "part_of"):
        if _label(kg, pdu) != "Pdu":
            continue
        frames = [frame for frame in _out(kg, pdu, "part_of") if _label(kg, frame) == "Frame"]
        if not frames:
            rows.append(_travel(_native(kg, pdu), None, None, None))
            continue
        for frame in frames:
            networks = [
                network for network in _out(kg, frame, "transmitted_on")
                if _label(kg, network) == "Network"
            ]
            if not networks:
                rows.append(_travel(_native(kg, pdu), _native(kg, frame), None, None))
                continue
            for network in networks:
                identifiers = _identifiers(projection["edges"], index, frame, network)
                for identifier in identifiers:
                    rows.append(_travel(
                        _native(kg, pdu), _native(kg, frame), _native(kg, network), identifier,
                    ))
    rows.sort(key=lambda row: (
        row["pdu"] or "",
        row["frame"] or "",
        row["network"] or "",
        row["identifier"] or "",
    ))
    return rows


def tester_needs(kg, requirement: int) -> dict[int, int]:
    """Nodes within two outgoing steps of the requirement's statements.

    The steps use only the seven relations named in §9. The statements
    themselves are the start, at distance 0, and are not returned.
    """
    found: dict[int, int] = {}
    statements = [
        node for node in _out(kg, requirement, "has_statement")
        if _label(kg, node) == "Statement"
    ]
    for start in statements:
        seen = {start}
        frontier = [start]
        for depth in (1, 2):
            nxt: list[int] = []
            for node in frontier:
                for relation in _NEEDS:
                    for hop in _out(kg, node, relation):
                        if hop in seen:
                            continue
                        seen.add(hop)
                        previous = found.get(hop)
                        if previous is None or depth < previous:
                            found[hop] = depth
                        nxt.append(hop)
            frontier = nxt
    return found


def parameter_settings(kg, statement: int) -> list[int]:
    """Statement —configured_by→ Parameter."""
    return _by_name(kg, [
        node for node in _out(kg, statement, "configured_by")
        if _label(kg, node) == "Parameter"
    ])


def affected_by_signal(kg, signal: int) -> dict[str, list[int]]:
    """Signal ←about/refers_to— Statement ←has_statement— Requirement ←verifies— TestConcept."""
    statements = _incoming(kg, [signal], ("about", "refers_to"), "Statement")
    requirements = _incoming(kg, statements, ("has_statement",), "Requirement")
    concepts = _incoming(kg, requirements, ("verifies",), "TestConcept")
    return {
        "statements": _by_name(kg, statements),
        "requirements": _by_name(kg, requirements),
        "test_concepts": _by_name(kg, concepts),
    }


def unverified_requirements(kg, requirement_ids: list[int]) -> list[int]:
    """Requirements with no incoming verifies edge."""
    return _by_name(kg, [
        requirement for requirement in requirement_ids
        if _label(kg, requirement) == "Requirement" and not _in(kg, requirement, "verifies")
    ])


def same_signal(kg, signal: int) -> list[int]:
    """Signal —corresponds_to→ Signal. Outgoing, as the path is drawn."""
    return _by_name(kg, [
        node for node in _out(kg, signal, "corresponds_to")
        if _label(kg, node) == "Signal"
    ])


def run_checks(kg, projection: dict) -> str:
    """Print the eight questions for every node of the starting type."""
    documents = projection["counts"]["documents"]
    requirements = _of_type(projection, "Requirement")
    signals = _of_type(projection, "Signal")
    statements = _of_type(projection, "Statement")
    n_req = len(requirements)
    n_sig = len(signals)
    n_stmt = len(statements)
    lines: list[str] = []

    if n_req == 0:
        lines.append(f"q1 n/a (n=0 requirements, documents={documents})")
    else:
        lines.append(f"q1 (n={n_req} requirements, documents={documents})")
        for node in requirements:
            found = upstream_requirements(kg, node["engine_id"])
            lines.append(
                f"  {node['native_id']} upstream={len(found)} "
                f"(n={n_req} requirements, documents={documents})"
            )

    if n_req == 0:
        lines.append(f"q2 n/a (n=0 requirements, documents={documents})")
    else:
        lines.append(f"q2 (n={n_req} requirements, documents={documents})")
        for node in requirements:
            rows = involved_signals(kg, node["engine_id"])
            lines.append(
                f"  {node['native_id']} signals={len(rows)} "
                f"(n={n_req} requirements, documents={documents})"
            )
            for row in rows:
                shown = ", ".join(row["scaling"])
                suffix = f": {shown}" if shown else ""
                lines.append(
                    f"    {row['native_id']} relations={','.join(row['relations'])} "
                    f"scaling={len(row['scaling'])} "
                    f"(n={len(row['scaling'])} data types, documents={documents})"
                    f"{suffix}"
                )

    if n_sig == 0:
        lines.append(f"q3 n/a (n=0 signals, documents={documents})")
    else:
        lines.append(f"q3 (n={n_sig} signals, documents={documents})")
        for node in signals:
            rows = signal_travel(kg, node["engine_id"], projection)
            frames = len({row["frame"] for row in rows if row["frame"]})
            lines.append(
                f"  {node['native_id']} frames={frames} "
                f"(n={n_sig} signals, documents={documents})"
            )
            for row in rows:
                lines.append(
                    "    pdu={pdu} frame={frame} identifier={identifier} network={network}".format(
                        pdu=_shown(row["pdu"]),
                        frame=_shown(row["frame"]),
                        identifier=_shown(row["identifier"]),
                        network=_shown(row["network"]),
                    )
                )

    if n_req == 0:
        lines.append(f"q4 n/a (n=0 requirements, documents={documents})")
    else:
        lines.append(f"q4 (n={n_req} requirements, documents={documents})")
        for node in requirements:
            walked = [
                hop for hop in _out(kg, node["engine_id"], "has_statement")
                if _label(kg, hop) == "Statement"
            ]
            if not walked:
                lines.append(
                    f"  {node['native_id']} needs=n/a "
                    f"(n=0 statements, documents={documents})"
                )
                continue
            needs = tester_needs(kg, node["engine_id"])
            lines.append(
                f"  {node['native_id']} needs={len(needs)} "
                f"(n={len(walked)} statements, documents={documents})"
            )

    if n_stmt == 0:
        lines.append(f"q5 n/a (n=0 statements, documents={documents})")
    else:
        lines.append(f"q5 (n={n_stmt} statements, documents={documents})")
        for node in statements:
            found = parameter_settings(kg, node["engine_id"])
            lines.append(
                f"  {node['native_id']} parameters={len(found)} "
                f"(n={n_stmt} statements, documents={documents})"
            )

    if n_sig == 0:
        lines.append(f"q6 n/a (n=0 signals, documents={documents})")
    else:
        lines.append(f"q6 (n={n_sig} signals, documents={documents})")
        for node in signals:
            hit = affected_by_signal(kg, node["engine_id"])
            lines.append(
                f"  {node['native_id']} statements={len(hit['statements'])} "
                f"requirements={len(hit['requirements'])} "
                f"test_concepts={len(hit['test_concepts'])} "
                f"(n={n_sig} signals, documents={documents})"
            )

    if n_req == 0:
        lines.append(f"q7 n/a (n=0 requirements, documents={documents})")
    else:
        missing = unverified_requirements(kg, [node["engine_id"] for node in requirements])
        lines.append(
            f"q7 unverified={len(missing)} (n={n_req} requirements, documents={documents})"
        )
        for node in missing:
            lines.append(f"  {_native(kg, node)}")

    if n_sig == 0:
        lines.append(f"q8 n/a (n=0 signals, documents={documents})")
    else:
        lines.append(f"q8 (n={n_sig} signals, documents={documents})")
        for node in signals:
            found = same_signal(kg, node["engine_id"])
            lines.append(
                f"  {node['native_id']} same={len(found)} "
                f"(n={n_sig} signals, documents={documents})"
            )

    text = "\n".join(lines) + "\n"
    print(text, end="")
    return text


def _travel(pdu, frame, network, identifier) -> dict:
    return {
        "pdu": pdu,
        "frame": frame,
        "network": network,
        "identifier": identifier,
    }


def _identifiers(edges, index, frame: int, network: int) -> list:
    frame_node = index.get(frame)
    network_node = index.get(network)
    if frame_node is None or network_node is None:
        return [None]
    found = []
    for edge in edges:
        if edge.get("relation") != "transmitted_on":
            continue
        if edge.get("from_key") != frame_node["key"] or edge.get("to_key") != network_node["key"]:
            continue
        attributes = edge.get("attributes") or {}
        found.append(attributes.get("IDENTIFIER"))
    return found or [None]


def _of_type(projection: dict, type_name: str) -> list[dict]:
    nodes = [node for node in projection["nodes"] if node["type"] == type_name]
    nodes.sort(key=lambda node: (node["native_id"], node["key"]))
    return nodes


def _incoming(kg, starts: list[int], relations: tuple[str, ...], label: str) -> list[int]:
    found: list[int] = []
    seen: set[int] = set()
    for start in starts:
        for relation in relations:
            for node in _in(kg, start, relation):
                if node in seen or _label(kg, node) != label:
                    continue
                seen.add(node)
                found.append(node)
    return found


def _out(kg, node: int, relation: str) -> list[int]:
    return _hop(kg.neighbors_by_relation, node, relation)


def _in(kg, node: int, relation: str) -> list[int]:
    return _hop(kg.predecessors_by_relation, node, relation)


def _hop(method, node: int, relation: str) -> list[int]:
    name, value = _relation_filter(relation)
    found: list[int] = []
    seen: set[int] = set()
    for nxt, _strength in method(node, name, value):
        if nxt in seen:
            continue
        seen.add(nxt)
        found.append(nxt)
    return found


def _label(kg, node: int):
    return kg.get_node_label(node)


def _native(kg, node: int) -> str:
    return kg.get_node_text(node).split("\n", 1)[0]


def _by_name(kg, nodes: list[int]) -> list[int]:
    return sorted(nodes, key=lambda node: (_native(kg, node), node))


def _shown(value) -> str:
    if value is None:
        return "n/a"
    return str(value)
