"""Phase 3: extractor — JSON robustness, normalization, provenance."""

import grap_mcp.kee.extract as ex
from grap_mcp.kee.llm import StubProvider


def test_extract_json_plain():
    assert ex.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    raw = "```json\n{\"a\": 1, \"b\": [2,3]}\n```"
    assert ex.extract_json(raw) == {"a": 1, "b": [2, 3]}


def test_extract_json_embedded_in_prose():
    raw = 'Sure! Here is the result: {"outcomes": []} — hope that helps.'
    assert ex.extract_json(raw) == {"outcomes": []}


def test_extract_json_handles_braces_in_strings():
    raw = '{"name": "a {weird} value", "x": 1}'
    assert ex.extract_json(raw) == {"name": "a {weird} value", "x": 1}


def test_extract_json_malformed_returns_none():
    assert ex.extract_json("not json at all") is None
    assert ex.extract_json('{"a": ') is None


def test_normalize_drops_nameless_outcomes_and_bad_relations():
    obj = {
        "outcomes": [
            {"name": "Verification", "characteristics": ["test plan", 5, " "]},
            {"characteristics": ["x"]},          # no name -> dropped
            "garbage",                            # not a dict -> dropped
        ],
        "relations": [
            {"from": "A", "type": "requires", "to": "B"},
            {"from": "A", "to": "B"},             # no type -> dropped
        ],
    }
    frag = ex.normalize_fragment(obj, source="doc#0")
    assert frag["source"] == "doc#0"
    assert len(frag["outcomes"]) == 1
    assert frag["outcomes"][0]["name"] == "Verification"
    assert frag["outcomes"][0]["characteristics"] == ["test plan"]
    assert frag["relations"] == [{"from": "A", "type": "requires", "to": "B"}]


def test_extract_fragments_end_to_end_with_stub():
    resp = ('{"outcomes":[{"name":"System Qualification Test",'
            '"characteristics":["test plan","test specification"]}],'
            '"relations":[{"from":"test plan","type":"evidences","to":"qualification"}]}')
    provider = StubProvider(responses=[resp, "no json here"])
    chunks = [("doc#0", "text about qualification testing"),
              ("doc#1", "unparseable chunk")]
    frags = ex.extract_fragments(chunks, provider)
    assert len(frags) == 1                        # second chunk dropped (no JSON)
    assert frags[0]["source"] == "doc#0"
    assert frags[0]["outcomes"][0]["name"] == "System Qualification Test"
    # the system prompt was actually sent
    assert provider.calls[0][0].startswith("You are an information-extraction")


def test_extract_fragments_skips_empty_outcomes_and_relations():
    provider = StubProvider(responses=['{"outcomes":[],"relations":[]}'])
    frags = ex.extract_fragments([("d#0", "nothing relevant")], provider)
    assert frags == []
