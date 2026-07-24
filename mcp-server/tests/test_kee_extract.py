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


def test_build_user_prompt_with_context_includes_known_block():
    from grap_mcp.kee.working_graph import Context
    from grap_mcp.kee.extract import build_user_prompt_with_context

    ctx = Context(
        entities=["Test Plan", "Verification"],
        characteristics={"Test Plan": ["coverage", "schedule"]},
        relations=[("Verification", "uses", "Test Plan")],
        relation_vocab=["uses", "produces"],
    )
    prompt = build_user_prompt_with_context("Some chunk about testing.", ctx)
    assert "Some chunk about testing." in prompt
    assert "Test Plan" in prompt and "Verification" in prompt
    assert "uses" in prompt
    # When context is empty, the prompt must equal the plain prompt (no empty block).
    empty = Context(entities=[], characteristics={}, relations=[], relation_vocab=[])
    from grap_mcp.kee.extract import build_user_prompt
    assert build_user_prompt_with_context("X", empty) == build_user_prompt("X")


def test_context_prompt_is_bounded_regardless_of_graph_size():
    """A concept can accumulate 100+ characteristics and the vocab hundreds of
    verbs; the extraction prompt must NOT grow with the ontology, or it will
    overflow the model context window at scale (law-ro 400s at ~3200 outcomes)."""
    from grap_mcp.kee.working_graph import Context
    from grap_mcp.kee import extract as ex

    huge = Context(
        entities=[f"Concept {i}" for i in range(16)],
        # each entity carries 139 long characteristics (the real worst case)
        characteristics={f"Concept {i}": [f"characteristic phrase number {j} of many"
                                          for j in range(139)] for i in range(16)},
        relations=[(f"Concept {i}", "rel", f"Concept {i+1}") for i in range(15)],
        relation_vocab=[f"relation_verb_{k}" for k in range(418)],
    )
    prompt = ex.build_user_prompt_with_context("the chunk text here", huge)

    # bounded: block ceiling + the chunk tail, comfortably under a 16k-token slot
    assert len(prompt) <= ex.MAX_CONTEXT_CHARS + 400
    # only a few characteristics per entity survive, not all 139
    assert prompt.count("characteristic phrase number") <= 16 * ex.MAX_CHARS_PER_ENTITY
    # dedup guidance preserved: entity names and the chunk still present
    assert "Concept 0" in prompt
    assert "the chunk text here" in prompt


def test_relation_vocab_is_capped_by_frequency():
    """_top_relation_types returns the most-common verbs, capped."""
    from grap_mcp.kee.working_graph import WorkingGraph, REL_VOCAB_CAP
    from grap_mcp.kee.embeddings import HashingEmbedder
    import tempfile
    wg = WorkingGraph(HashingEmbedder(), workdir=tempfile.mkdtemp(), merge_threshold=0.99)
    # 100 distinct verbs; "common" appears most
    wg.relations = ([{"from_id": 0, "to_id": 1, "type": "common"}] * 10 +
                    [{"from_id": 0, "to_id": 1, "type": f"v{i}"} for i in range(100)])
    top = wg._top_relation_types(REL_VOCAB_CAP)
    assert len(top) == REL_VOCAB_CAP
    assert top[0] == "common"
