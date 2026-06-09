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
