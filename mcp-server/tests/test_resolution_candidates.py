"""E-16: Stage 2 candidates, shadow mode only (spec-entity-resolution.md).

Candidates are computed and reported. The run does not create, merge, or
change a link. A mention Stage 1 already linked is not a candidate source.
A proposal is not a link. Type-incompatible entities are dropped. The report
prints a denominator beside every count.
"""

import pytest

from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.resolution.candidates import Entity, find_candidates
from grap_mcp.kee.resolution.tables import Mention, ResolutionTables
from grap_mcp.kee.working_graph import WorkingGraph

ECU = "Signal | bus | EcuCtrl"
BRAKE = "Component | bus | Brake"
WIDGET = "Signal | bus | Widget"
OTHER = "Signal | bus | Other"


def _mention(text, sentence, type_hint, mention_id, source="act", item="act#0", start=0):
    return Mention(
        source_id=source,
        item_id=item,
        span_start=start,
        span_end=start + len(text),
        text=text,
        sentence=sentence,
        type_hint=type_hint,
        extractor="fixture",
        mention_id=mention_id,
    )


def _entity(key, name, long_name=None, description=None):
    return Entity(key=key, name=name, long_name=long_name, description=description)


def _module_catalogue():
    """Five signals. ``module`` is in four of them; every other token is in one."""
    names = (
        "module alpha",
        "module beta",
        "module gamma",
        "module delta",
        "vehicle speed sensor",
    )
    return [
        _entity(f"Signal | bus | {name}", name)
        for name in names
    ]


def _graph_snapshot(wg):
    return (
        wg.node_count(),
        wg.kg.edge_count(),
        tuple((row["from_id"], row["type"], row["to_id"]) for row in wg.relations),
        tuple(sorted(wg.canonical.items())),
        tuple(sorted((node, tuple(names)) for node, names in wg.aliases_by_node.items())),
        tuple(sorted(wg._edge_types.items())),
        tuple(wg.parked),
        tuple(sorted((node, tuple(values)) for node, values in wg.chars.items())),
    )


def _store_snapshot(store):
    tables = (
        "mentions", "link_decisions", "constraints", "groupings",
        "edges", "concepts", "aliases", "edge_sources",
    )
    return {
        name: [tuple(row) for row in store.conn.execute(f"SELECT * FROM {name}")]
        for name in tables
    }


class _RecordingEmbedder:
    """Vectors chosen so the wrong type is nearer than the compatible entity."""

    name = "recording"
    dim = 2

    def __init__(self):
        self.texts = []

    def embed(self, texts):
        self.texts.extend(texts)
        vectors = []
        for text in texts:
            if "The ECU resets." in text:
                vectors.append([1.0, 0.0])
            elif "Brake Pedal" in text:
                vectors.append([1.0, 0.0])
            elif "Widget controller" in text:
                vectors.append([0.8, 0.2])
            elif "Other device" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.0, 0.0])
        return vectors


def test_shadow_mode_leaves_the_graph_unchanged(tmp_path, capsys):
    """Shadow mode records candidates and does not touch the graph or its links.

    The working graph already has ``EcuA sends FrameX``. The store already has
    a ``not_same`` decision. Both are still exactly that after the run. Turning
    shadow mode off raises, and still changes nothing.
    """
    wg = WorkingGraph(
        HashingEmbedder(), workdir=str(tmp_path / "wg"), merge_threshold=0.99,
    )
    wg.upsert_fragment({
        "source": "sends#0",
        "outcomes": [
            {"name": "EcuA", "characteristics": ["engine"]},
            {"name": "FrameX", "characteristics": []},
        ],
        "relations": [{"from": "EcuA", "type": "sends", "to": "FrameX"}],
    }, "sends#0")
    assert wg.node_count() == 2
    assert wg.kg.edge_count() == 1

    path = str(tmp_path / "store.db")
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        mention_id = tables.add_mentions([_mention(
            "ECU", "The ECU resets.", "signal", None, item="act#0",
        )])[0]
        mention = _mention(
            "ECU", "The ECU resets.", "signal", mention_id, item="act#0",
        )
        tables.append_decision(
            mention_id, ECU, "not_same", method="review", score=None,
            decider="human", decided_at="2026-11-13T12:00:00Z",
        )
        entities = [
            _entity(ECU, "EcuCtrl", "Electronic Control Unit", "controls the engine"),
            _entity(BRAKE, "Brake", "Brake Pedal", "driver input"),
        ]
        before_graph = _graph_snapshot(wg)
        before_store = _store_snapshot(store)

        run = find_candidates(
            [mention], entities, shadow=True, graph=wg, tables=tables,
        )

        assert _graph_snapshot(wg) == before_graph
        assert _store_snapshot(store) == before_store
        assert [(row.mention_id, row.entity_key, row.sources) for row in run.candidates] == [
            (mention_id, ECU, frozenset({"initials", "rare_token"})),
        ]
        printed = capsys.readouterr().out.strip()
        assert "n_mentions=1" in printed
        assert "n_entities=2" in printed
        assert "n_documents=1" in printed
        assert "n_candidate_pairs=1" in printed
        assert "candidates_per_mention=1" in printed
        assert "n_type_incompatible=1" in printed
        assert "embedding_k=n/a" in printed

        with pytest.raises(RuntimeError, match="shadow"):
            find_candidates(
                [mention], entities, shadow=False, graph=wg, tables=tables,
            )
        assert _graph_snapshot(wg) == before_graph
        assert _store_snapshot(store) == before_store


def test_rare_tokens_ignore_the_common_token_and_keep_input_order_out(capsys):
    """A shared token is a candidate only when it is rare in the entity catalogue.

    ``module`` is in 4 of 5 entities. ``speed`` is in 1. The median token
    frequency is 1, over 8 token types. Reversing the inputs does not change
    the pairs or the per-mention counts.
    """
    entities = _module_catalogue()
    speed = _mention("speed", "The speed is read.", "signal", 1, item="act#0")
    module = _mention("module", "The module starts.", "signal", 2, item="act#1")
    forwards = find_candidates([speed, module], entities, shadow=True)
    printed = capsys.readouterr().out.strip()
    backwards = find_candidates(
        [module, speed], list(reversed(entities)), shadow=True,
    )

    expected = [(1, "Signal | bus | vehicle speed sensor", frozenset({"rare_token"}))]
    assert [(row.mention_id, row.entity_key, row.sources) for row in forwards.candidates] == expected
    assert backwards.candidates == forwards.candidates
    assert forwards.report["candidates_per_mention"] == (1, 0)
    assert forwards.report["rare_token_median"] == 1
    assert forwards.report["n_token_types"] == 8
    assert printed == (
        "shadow=1 mentions=2 n_mentions=2 n_unlinked=2 n_linked_skipped=0 "
        "n_entities=5 n_documents=1 n_examined_pairs=10 n_candidate_pairs=1 "
        "candidates_per_mention=1,0 n_type_incompatible=0 n_from_rare_tokens=1 "
        "n_from_initials=0 n_from_embedding=n/a rare_token_median=1 "
        "n_token_types=8 embedding_k=n/a n_embedded_entities=n/a"
    )


def test_initials_of_a_multi_word_name_match_a_dotted_mention():
    """``E.C.U.`` is the initials of ``Electronic Control Unit`` and nothing else."""
    mention = _mention("E.C.U.", "The E.C.U. resets.", "signal", 7)
    entity = _entity(ECU, "Unit", "Electronic Control Unit", "controls the engine")
    run = find_candidates([mention], [entity], shadow=True)
    assert [(row.mention_id, row.entity_key, row.sources) for row in run.candidates] == [
        (7, ECU, frozenset({"initials"})),
    ]


def test_type_conflict_drops_the_candidate():
    """A mention hinted as a signal is not a candidate for a component."""
    mention = _mention("ECU", "The ECU resets.", "signal", 3)
    component = _entity(BRAKE, "Brake", "Electronic Control Unit", "controls the engine")
    run = find_candidates([mention], [component], shadow=True)
    assert run.candidates == ()
    assert run.report["n_type_incompatible"] == 1
    assert run.report["n_candidate_pairs"] == 0
    assert run.report["candidates_per_mention"] == (0,)


def test_unknown_type_hint_is_not_treated_as_a_conflict():
    """A hint that is not a schema type is not a conflict this stage can judge."""
    mention = _mention("ECU", "The ECU resets.", "widget", 4)
    entity = _entity(ECU, "Unit", "Electronic Control Unit", "controls the engine")
    run = find_candidates([mention], [entity], shadow=True)
    assert run.candidates[0].entity_key == ECU
    assert run.report["n_type_incompatible"] == 0


def test_embedding_uses_context_and_does_not_give_the_slot_to_the_wrong_type(capsys):
    """The nearest neighbour is by the name in context, never the bare name.

    The component is the nearer vector and the wrong type, so it is dropped
    and does not take the one neighbour place. ``neighbour_k`` is the caller's:
    this test passes 1. No neighbour count is printed as a number when the
    caller does not supply one.
    """
    mention = _mention("ECU", "The ECU resets.", "signal", 5)
    widget = _entity(WIDGET, "Widget", "Widget controller", "the controller box")
    brake = _entity(BRAKE, "Brake", "Brake Pedal", "driver input")
    other = _entity(OTHER, "Other", "Other device", "something else")
    embedder = _RecordingEmbedder()
    run = find_candidates(
        [mention], [brake, other, widget], shadow=True,
        embedder=embedder, neighbour_k=1,
    )
    assert [(row.mention_id, row.entity_key, row.sources) for row in run.candidates] == [
        (5, WIDGET, frozenset({"embedding"})),
    ]
    assert all(text != "ECU" and text != "Widget" and text != "Brake" for text in embedder.texts)
    assert any("The ECU resets." in text and "ECU" in text for text in embedder.texts)
    assert any(
        "Widget controller" in text and "the controller box" in text
        for text in embedder.texts
    )
    assert any("Brake Pedal" in text and "driver input" in text for text in embedder.texts)
    printed = capsys.readouterr().out
    assert "embedding_k=1" in printed
    assert "n_embedded_entities=3" in printed
    assert "n_entities=3" in printed
    assert "n_from_embedding=1" in printed

    off = find_candidates([mention], [widget], shadow=True, embedder=embedder)
    assert off.candidates == ()
    assert "embedding_k=n/a" in capsys.readouterr().out


def test_a_stage1_link_is_skipped_and_a_proposal_is_not_a_link(tmp_path, capsys):
    """Stage 1 left the linked mention out. A proposal has not linked anything."""
    path = str(tmp_path / "store.db")
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        rows = [
            _mention("ECU", "The ECU resets.", "signal", None, source="act", item="act#0"),
            _mention("ECU", "The ECU resets.", "signal", None, source="act", item="act#1"),
            _mention("ECU", "The ECU resets.", "signal", None, source="other", item="other#0"),
        ]
        ids = tables.add_mentions(rows)
        linked, proposed, free = (
            _mention("ECU", "The ECU resets.", "signal", ids[0], source="act", item="act#0"),
            _mention("ECU", "The ECU resets.", "signal", ids[1], source="act", item="act#1"),
            _mention("ECU", "The ECU resets.", "signal", ids[2], source="other", item="other#0"),
        )
        tables.append_decision(
            ids[0], ECU, "same", method="identifier", score=None,
            decider="rule", decided_at="2026-11-13T08:00:00Z", proposal=False,
        )
        tables.append_decision(
            ids[1], ECU, "same", method="embedding", score=None,
            decider="stage3", decided_at="2026-11-13T09:00:00Z", proposal=True,
        )
        entity = _entity(ECU, "Unit", "Electronic Control Unit", "controls the engine")
        before = _store_snapshot(store)
        run = find_candidates(
            [free, linked, proposed], [entity], shadow=True, tables=tables,
        )
        assert _store_snapshot(store) == before
        assert [(row.mention_id, row.entity_key) for row in run.candidates] == [
            (ids[1], ECU),
            (ids[2], ECU),
        ]
        assert run.report["n_mentions"] == 3
        assert run.report["n_unlinked"] == 2
        assert run.report["n_linked_skipped"] == 1
        assert run.report["n_documents"] == 2
        assert run.report["candidates_per_mention"] == (1, 1)
        printed = capsys.readouterr().out
        assert "n_documents=2" in printed
        assert "n_linked_skipped=1" in printed
        assert "candidates_per_mention=1,1" in printed


def test_nothing_to_measure_prints_na_not_a_score(capsys):
    """No unlinked mention has no per-mention count. An empty input is n/a."""
    entity = _entity(ECU, "Unit", "Electronic Control Unit", "controls the engine")
    empty = find_candidates([], [entity], shadow=True)
    assert empty.candidates == ()
    assert empty.report["candidates_per_mention"] is None
    assert empty.report["n_candidate_pairs"] is None
    assert empty.report["n_mentions"] == 0
    assert empty.report["n_entities"] == 1
    first = capsys.readouterr().out.strip()
    assert "mentions=0" in first
    assert "n_mentions=0" in first
    assert "n_entities=1" in first
    assert "candidates_per_mention=n/a" in first
    assert "n_candidate_pairs=n/a" in first
    assert "n_documents=0" in first

    mention = _mention("ECU", "The ECU resets.", "signal", 9)
    none_left = find_candidates(
        [mention], [entity], shadow=True, linked_mention_ids={9},
    )
    assert none_left.candidates == ()
    assert none_left.report["n_unlinked"] == 0
    assert none_left.report["candidates_per_mention"] is None
    second = capsys.readouterr().out.strip()
    assert "n_linked_skipped=1" in second
    assert "candidates_per_mention=n/a" in second
