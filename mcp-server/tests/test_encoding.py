from grap_mcp.encoding import encode_response, default_format


def test_graph_neighbors_compact_interns_relation():
    response = {
        "neighbors": [
            {"node_id": 7, "relation": "cites"},
            {"node_id": 9, "relation": "cites"},
        ],
        "count": 2,
    }
    payload, meta = encode_response("graph_neighbors", response, requested_format="compact")
    assert isinstance(payload, str)
    assert payload.startswith("#MUNCH/1 tool=graph_neighbors")
    assert "@1=cites" in payload
    assert meta["tokens_saved"] >= 0


def test_json_is_default_passthrough():
    response = {"neighbors": [], "count": 0}
    payload, meta = encode_response("graph_neighbors", response, requested_format=None)
    assert payload == response          # JSON default until a decoder ships
    assert meta["format"] == "json"


def test_unknown_tool_falls_back_to_json():
    payload, meta = encode_response("some_tool_without_schema", {"x": 1}, requested_format="compact")
    assert payload == {"x": 1}
    assert meta["format"] == "json"
