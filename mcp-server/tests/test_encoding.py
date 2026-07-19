from grap_mcp.encoding import encode_response, default_format


def test_graph_neighbors_compact_two_tables():
    response = {
        "status": "ok", "center": 5,
        "neighbors": [
            {"id": 7, "label": "Brake", "kind": "act", "title": "..."},
            {"id": 9, "label": "Signal", "kind": "act", "title": "..."},
        ],
        "edges": [
            {"source": 5, "target": 7, "type": "cites"},
            {"source": 5, "target": 9, "type": "cites"},
        ],
    }
    payload, meta = encode_response("graph_neighbors", response, requested_format="compact")
    assert isinstance(payload, str)
    assert payload.startswith("#MUNCH/1 tool=graph_neighbors")
    assert "__tables=n:neighbors:id|label|kind|title,e:edges:source|target|type" in payload
    # Both repeated values are interned; assert the legend bodies are present
    # without pinning the exact @N (interning order is value-dependent).
    assert "=act" in payload and "=cites" in payload  # kind + type interned
    assert meta["tokens_saved"] >= 0


def test_json_is_default_passthrough():
    response = {"status": "ok", "center": 0, "neighbors": [], "edges": []}
    payload, meta = encode_response("graph_neighbors", response, requested_format=None)
    assert payload == response          # JSON default until a decoder ships
    assert meta["format"] == "json"


def test_unknown_tool_falls_back_to_json():
    payload, meta = encode_response("some_tool_without_schema", {"x": 1}, requested_format="compact")
    assert payload == {"x": 1}
    assert meta["format"] == "json"


def test_new_schemas_fallback_without_pygrap():
    # list_runs
    lr = {"runs": [{"run_id": "r1", "kind": "production", "status": "completed", "gaps_found": 3}], "count": 1}
    p, m = encode_response("list_runs", lr, requested_format="compact")
    # Without pygrap, falls to json but still recognizes schema
    assert p == lr or isinstance(p, str)
    assert m["format"] in ("json", "compact")

    # query_events
    qe = {"events": [{"ts": 123, "type": "kg_loaded", "subject": "foo"}], "count": 1}
    p, m = encode_response("query_events", qe, requested_format="compact")
    assert m["format"] in ("json", "compact")


def test_detect_gaps_schema_matches_real_gap_keys():
    # The gaps table columns must line up with reasoner.detect_gaps output —
    # a realistic gap dict round-trips its values into the compact payload.
    dg = {
        "status": "ok", "run_id": "run-1234", "kind": "production",
        "scope": "all", "gaps_found": 1,
        "gaps": [{
            "outcome_id": "SYS.5.BP3",
            "outcome_name": "Requirement Verification",
            "missing_characteristic": "acceptance_criteria_defined",
            "signals": {"structural": [], "lexical": []},
            "confidence": 0.87,
        }],
    }
    p, m = encode_response("detect_gaps", dg, requested_format="compact")
    if m["format"] == "compact":          # pygrap wire available
        assert isinstance(p, str)
        assert "SYS.5.BP3" in p
        assert "acceptance_criteria_defined" in p
        assert "0.87" in p
    else:                                  # binding unavailable: JSON passthrough
        assert p == dg


def test_int_list_tools_have_no_schema_and_fall_back_to_json():
    # blast_radius / orphans return plain int lists, cycles lists of lists —
    # unrepresentable in the row-of-dicts encoder, so compact requests must
    # return untouched JSON (a broken encoding would error or emit nulls).
    for tool, resp in [
        ("get_blast_radius", {"status": "ok", "center": 1, "impacted": [2, 3, 5]}),
        ("find_orphans", {"status": "ok", "orphan_count": 2, "orphans": [7, 9]}),
        ("find_cycles", {"status": "ok", "cycle_count": 1, "cycles": [[1, 2, 3]]}),
    ]:
        p, m = encode_response(tool, resp, requested_format="compact")
        assert p == resp
        assert m["format"] == "json"
