"""Phase 3: extractor — JSON robustness, normalization, provenance."""

import grap_mcp.kee.extract as ex
from grap_mcp.kee.extract_quality import QualityMonitor
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
    """Pins the legacy "full" mode, which lists characteristics, known relations
    and a relation vocabulary. The default is now "names" (see
    test_names_mode_lists_instances_but_no_patterns) because listing patterns
    caused the model to instantiate them; "full" is kept for comparison."""
    from grap_mcp.kee.working_graph import Context
    from functools import partial
    from grap_mcp.kee.extract import build_user_prompt_with_context as _b
    build_user_prompt_with_context = partial(_b, mode="full")

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
    # and the same holds in the default "names" mode with nothing to say
    assert _b("X", empty) == build_user_prompt("X")


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


# --- context mode "names" (measured-best format, distillation iter 4b arm C) ---

def test_names_mode_lists_instances_but_no_patterns(monkeypatch):
    """The asymmetry: instances are safe to list (reuse = dedup), patterns are not
    (reuse = assert). 'names' mode must carry NO relations and NO relation vocab."""
    from grap_mcp.kee.working_graph import Context
    from grap_mcp.kee import extract as ex

    ctx = Context(entities=["ORDONANŢA nr. 33", "GUVERNUL"],
                  characteristics={"ORDONANŢA nr. 33": ["a", "b", "c"]},
                  relations=[("GUVERNUL", "emite", "ORDONANŢA nr. 33")],
                  relation_vocab=["emite", "contrasemnează", "adoptată de"])
    p = ex.build_user_prompt_with_context("chunk text", ctx, mode="names")

    assert "ORDONANŢA nr. 33" in p and "GUVERNUL" in p     # instances present
    assert "chunk text" in p
    assert "RELATION VOCABULARY" not in p                   # no pattern list
    assert "KNOWN RELATIONS" not in p
    assert "contrasemnează" not in p                        # vocab not leaked
    assert "characteristics:" not in p                      # no characteristic dump
    assert "ONLY if THIS text states it" in p               # assert-only instruction
    # and it is much smaller than full mode
    assert len(p) < len(ex.build_user_prompt_with_context("chunk text", ctx, mode="full"))


def test_names_mode_caps_the_name_list():
    from grap_mcp.kee.working_graph import Context
    from grap_mcp.kee import extract as ex
    ctx = Context(entities=[f"Concept {i}" for i in range(200)],
                  characteristics={}, relations=[], relation_vocab=[])
    block = ex.build_names_context(ctx, class_vocab=["Foo"])
    assert block.count("- Concept ") == ex.MAX_NAMES
    assert "Foo" in block


def test_class_vocab_is_configuration_not_hardcoded(monkeypatch):
    """Class names are DOMAIN content; the generic extractor must not carry them."""
    import importlib
    from grap_mcp.kee.working_graph import Context
    monkeypatch.setenv("GRAP_KEE_CLASS_VOCAB", "Alpha, Beta")
    import grap_mcp.kee.extract as ex
    ex = importlib.reload(ex)
    try:
        ctx = Context(entities=["X"], characteristics={}, relations=[], relation_vocab=[])
        assert "Alpha, Beta" in ex.build_names_context(ctx)
        monkeypatch.delenv("GRAP_KEE_CLASS_VOCAB")
        ex2 = importlib.reload(ex)
        assert ex2.CLASS_VOCAB == []
    finally:
        monkeypatch.delenv("GRAP_KEE_CLASS_VOCAB", raising=False)
        importlib.reload(ex)


# ---- class-name filter -----------------------------------------------------
#
# Chunk 19811#3 (2026-07-28) inverted the ontology: a reference-dense amending
# clause with no dominant subject pushed the model into organising by TYPE. It
# emitted the class vocabulary as outcomes and filed the real acts underneath as
# characteristics, then wired nine edges off the generic node. A node literally
# named "NormativeAct" is an embedding magnet, so every future act is a
# candidate to collapse into it.

_VOCAB = ["NormativeAct", "AuthoritativeBody", "Official", "StructuralPart"]


def test_class_named_outcomes_are_rejected():
    obj = {
        "outcomes": [
            {"name": "NormativeAct", "characteristics": ["Legea nr. 9/1998"]},
            {"name": "AuthoritativeBody", "characteristics": ["PRIM-MINISTRU RADU VASILE"]},
            {"name": "Hotărârea Guvernului nr. 753/1998", "characteristics": ["publicată 1998"]},
        ],
        "relations": [],
    }
    frag = ex.normalize_fragment(obj, "19811#3", class_vocab=_VOCAB)
    assert [o["name"] for o in frag["outcomes"]] == ["Hotărârea Guvernului nr. 753/1998"]
    assert sorted(frag["rejected_class_named"]) == ["AuthoritativeBody", "NormativeAct"]


def test_relations_touching_a_class_name_are_dropped():
    """Endpoints matter as much as outcomes: an ungrounded relation to
    "NormativeAct" gets parked, and promote_parked_endpoints promotes endpoints
    that recur — rebuilding the node the outcome filter just refused."""
    obj = {"outcomes": [], "relations": [
        {"from": "NormativeAct", "type": "publicată în", "to": "Monitorul Oficial"},
        {"from": "Legea nr. 9/1998", "type": "aprobată de", "to": "StructuralPart"},
        {"from": "Legea nr. 9/1998", "type": "publicată în", "to": "Monitorul Oficial"},
    ]}
    frag = ex.normalize_fragment(obj, "s", class_vocab=_VOCAB)
    assert len(frag["relations"]) == 1
    assert frag["relations"][0]["from"] == "Legea nr. 9/1998"
    assert frag["rejected_class_edges"] == 2


def test_class_match_is_case_and_space_insensitive():
    obj = {"outcomes": [{"name": "  normative act ", "characteristics": []}],
           "relations": []}
    frag = ex.normalize_fragment(obj, "s", class_vocab=_VOCAB)
    assert frag["outcomes"] == []
    assert frag["rejected_class_named"] == ["normative act"]


def test_instances_that_merely_contain_a_class_word_survive():
    """The filter must reject the class name itself, not anything mentioning it."""
    obj = {"outcomes": [
        {"name": "Normative Acts of 1998", "characteristics": []},
        {"name": "Official Gazette nr. 458", "characteristics": []},
    ], "relations": []}
    frag = ex.normalize_fragment(obj, "s", class_vocab=_VOCAB)
    assert len(frag["outcomes"]) == 2
    assert frag["rejected_class_named"] == []


def test_quality_monitor_counts_rejections_without_alarming():
    """Filtering must not silence the signal that caught the incident."""
    qm = QualityMonitor(class_vocab=_VOCAB)
    for _ in range(6):
        qm.record(ex.normalize_fragment(
            {"outcomes": [{"name": "NormativeAct", "characteristics": []},
                          {"name": "Legea nr. 9/1998", "characteristics": []}],
             "relations": [{"from": "a", "type": "t", "to": "b"},
                           {"from": "a", "type": "u", "to": "c"}]},
            "s", class_vocab=_VOCAB))
    r = qm.report()
    assert r["class_named_rejected"] == 6      # the model kept trying
    assert r["class_named_outcomes"] == 0      # nothing reached the graph
    assert not any("class_named_outcomes" in a for a in r["alarms"])
