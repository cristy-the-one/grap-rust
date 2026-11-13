"""E-17: Stage 3 scores of Stage 2 candidates, shadow mode only.

Scores are computed and reported. The run does not create, merge, or change
a link, and it does not write a link decision. A conflict (differing numbers,
the existing kind veto, or differing identifier tokens) is reported as
not-same. No threshold is applied, so nothing else is reported as same.
The printed line carries a denominator beside every count.
"""

import pytest

from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.resolution.candidates import Candidate, Entity
from grap_mcp.kee.resolution.scoring import score_candidates
from grap_mcp.kee.resolution.tables import Mention, ResolutionTables
from grap_mcp.kee.working_graph import WorkingGraph

ECU = "Signal | act | Unit"
GROUP = "Signal | act | Com_SendSignalGroup"
BRAKE = "Component | act | Brake"


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


def _pair(mention, entity, sources=("rare_token",)):
    return Candidate(mention.mention_id, entity.key, frozenset(sources))


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


def test_shadow_mode_leaves_the_graph_and_writes_no_decision(tmp_path, capsys):
    """A scored conflict is a report. The graph and the decision table stay put.

    The working graph already has ``EcuA sends FrameX``. The store already has
    a ``not_same`` decision. Both are still exactly that after the run, and
    after a refused non-shadow call.
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
            "Com_SendSignal", "Call Com_SendSignal.", "signal", None,
        )])[0]
        mention = _mention(
            "Com_SendSignal", "Call Com_SendSignal.", "signal", mention_id,
        )
        entity = _entity(GROUP, "Com_SendSignalGroup")
        tables.append_decision(
            mention_id, GROUP, "not_same", method="review", score=None,
            decider="human", decided_at="2026-11-13T12:00:00Z",
        )
        before_graph = _graph_snapshot(wg)
        before_store = _store_snapshot(store)

        run = score_candidates(
            [_pair(mention, entity)], [mention], [entity],
            shadow=True, graph=wg, tables=tables,
        )

        assert _graph_snapshot(wg) == before_graph
        assert _store_snapshot(store) == before_store
        assert len(run.proposals) == 1
        proposal = run.proposals[0]
        assert proposal.mention_id == mention_id
        assert proposal.entity_key == GROUP
        assert proposal.features.conflict is True
        assert proposal.outcome == "not_same"
        assert proposal.outcome != "same"
        printed = capsys.readouterr().out.strip()
        assert "n_mentions=1" in printed
        assert "n_entities=1" in printed
        assert "n_documents=1" in printed
        assert "n_proposals=1" in printed
        assert "n_conflict=1" in printed
        assert "n_not_same=1" in printed
        assert "n_same=n/a" in printed

        with pytest.raises(RuntimeError, match="shadow"):
            score_candidates(
                [_pair(mention, entity)], [mention], [entity],
                shadow=False, graph=wg, tables=tables,
            )
        assert _graph_snapshot(wg) == before_graph
        assert _store_snapshot(store) == before_store


def test_differing_numbers_and_the_kind_veto_mean_not_same():
    """The conflict flag is today's veto: different numbers, or different kinds.

    Neither result is written down. The score is still reported.
    """
    numbered = _mention("Legea nr. 101/1998", "See Legea nr. 101/1998.", None, 1)
    other_number = _entity("Term | act | law-58", "Legea nr. 58/1998")
    kinds = _mention("Ministerul Alpha", "Ministerul Alpha decides.", None, 2)
    minister = _entity("Term | act | minister", "ministrul Alpha")

    run = score_candidates(
        [_pair(numbered, other_number), _pair(kinds, minister)],
        [numbered, kinds],
        [other_number, minister],
        shadow=True,
    )
    assert [proposal.features.conflict for proposal in run.proposals] == [True, True]
    assert [proposal.outcome for proposal in run.proposals] == ["not_same", "not_same"]
    assert all(proposal.score is not None for proposal in run.proposals)
    assert run.report["n_same"] is None
    assert run.report["n_not_same"] == 2
    assert run.report["n_proposals"] == 2


@pytest.mark.parametrize("left,right", [
    ("Com_SendSignal", "Com_SendSignalGroup"),
    ("VehSpdFL", "VehSpdFR"),
    ("BrakePedalPosition", "BrakePedalPressure"),
    ("vehicle_speed", "vehicle_speed_raw"),
    ("/ISignal/SigA", "/ISignal/SigB"),
])
def test_differing_identifier_tokens_are_not_same(left, right):
    """Identifier-shaped names with different tokens are not the same.

    These pairs stay apart under identical vectors in the working graph. The
    conflict flag records that here, and still does not merge or link them.
    """
    mention = _mention(left, f"Use {left}.", "signal", 1)
    entity = _entity(f"Signal | act | {right}", right)
    run = score_candidates([_pair(mention, entity)], [mention], [entity], shadow=True)
    proposal = run.proposals[0]
    assert proposal.features.conflict is True
    assert proposal.outcome == "not_same"
    assert run.report["n_same"] is None


def test_the_same_identifier_twice_is_not_a_conflict():
    """Equal identifier tokens are not a conflict, and equality is not a merge.

    No threshold has been fitted, so the outcome stays undecided.
    """
    mention = _mention("Com_SendSignal", "Call Com_SendSignal.", "signal", 1)
    entity = _entity("Signal | act | Com_SendSignal", "Com_SendSignal")
    run = score_candidates([_pair(mention, entity)], [mention], [entity], shadow=True)
    proposal = run.proposals[0]
    assert proposal.features.conflict is False
    assert proposal.features.equal_normalised is True
    assert proposal.outcome == "undecided"
    assert run.report["n_undecided"] == 1
    assert run.report["n_not_same"] == 0
    assert run.report["n_same"] is None


def test_features_and_score_print_their_denominators(capsys):
    """Each feature is measured. The score is their unweighted mean.

    ``ECU`` against ``Electronic Control Unit`` shares initials and not
    tokens. The edit distance of the normalised forms is 20 on the longer
    string of 23. Embedding was not asked for, so that feature is left out
    of the mean and the report says n/a.
    """
    mention = _mention("ECU", "The ECU resets.", "signal", 1)
    entity = _entity(ECU, "Electronic Control Unit", description="controls the engine")
    run = score_candidates([_pair(mention, entity)], [mention], [entity], shadow=True)
    proposal = run.proposals[0]
    features = proposal.features
    assert features.equal_normalised is False
    assert features.token_overlap == 0.0
    assert features.edit_similarity == 1 - 20 / 23
    assert features.initials_match is True
    assert features.embedding_similarity is None
    assert features.type_compatible is True
    assert features.same_source is True
    assert features.conflict is False
    measured = [0.0, 0.0, 1 - 20 / 23, 1.0, 1.0, 1.0]
    assert proposal.score == sum(measured) / len(measured)
    assert proposal.outcome == "undecided"
    assert run.report["n_scored"] == 1
    assert run.report["score_n"] == (len(measured),)
    assert run.report["n_with_embedding"] is None
    assert run.report["n_equal"] == 0
    assert run.report["n_initials_match"] == 1
    assert run.report["n_proposals"] == 1
    printed = capsys.readouterr().out.strip()
    assert "n_proposals=1" in printed
    assert "score_n=6" in printed
    assert "n_with_embedding=n/a" in printed
    assert "n_same=n/a" in printed
    assert "n_documents=1" in printed


def test_an_exact_name_stays_undecided_without_a_threshold():
    """Matching after normalisation does not create a same link."""
    mention = _mention("Widget Controller", "The Widget Controller resets.", "signal", 1)
    entity = _entity("Signal | act | Widget", "Widget Controller")
    run = score_candidates([_pair(mention, entity)], [mention], [entity], shadow=True)
    proposal = run.proposals[0]
    assert proposal.features.equal_normalised is True
    assert proposal.features.conflict is False
    assert proposal.outcome == "undecided"
    assert proposal.score == 5 / 6
    assert run.report["n_same"] is None
    assert run.report["n_equal"] == 1


def test_type_incompatibility_is_a_feature_and_not_a_written_link():
    """A signal mention against a component scores incompatible and stays undecided."""
    mention = _mention("Brake", "The Brake warns.", "signal", 1)
    entity = _entity(BRAKE, "Brake")
    run = score_candidates([_pair(mention, entity)], [mention], [entity], shadow=True)
    proposal = run.proposals[0]
    assert proposal.features.type_compatible is False
    assert proposal.features.conflict is False
    assert proposal.outcome == "undecided"
    assert run.report["n_type_compatible"] == 0
    assert run.report["n_same"] is None


class _ContextEmbedder:
    """Vectors that match only the contextual strings, not a bare name."""

    name = "context"
    dim = 2

    def __init__(self):
        self.texts = []

    def embed(self, texts):
        self.texts.extend(texts)
        vectors = []
        for text in texts:
            if text == "ECU\nThe ECU resets.":
                vectors.append([1.0, 0.0])
            elif text == "Electronic Control Unit\ncontrols the engine":
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


def test_embedding_uses_context_and_joins_the_mean():
    """The cosine is of the mention with its sentence and the entity with its context.

    The bare name is not embedded. The cosine joins the mean, so the
    denominator is 7. Identical vectors give cosine 1.
    """
    mention = _mention("ECU", "The ECU resets.", "signal", 1)
    entity = _entity(
        ECU, "Electronic Control Unit",
        long_name="Electronic Control Unit", description="controls the engine",
    )
    embedder = _ContextEmbedder()
    run = score_candidates(
        [_pair(mention, entity)], [mention], [entity],
        shadow=True, embedder=embedder,
    )
    proposal = run.proposals[0]
    assert proposal.features.embedding_similarity == 1.0
    assert "ECU" not in embedder.texts
    assert "Electronic Control Unit" not in embedder.texts
    assert embedder.texts == [
        "ECU\nThe ECU resets.",
        "Electronic Control Unit\ncontrols the engine",
    ]
    measured = [0.0, 0.0, 1 - 20 / 23, 1.0, 1.0, 1.0, 1.0]
    assert proposal.score == sum(measured) / len(measured)
    assert run.report["n_with_embedding"] == 1
    assert run.report["score_n"] == (7,)
    assert run.report["n_proposals"] == 1


def test_input_order_does_not_change_the_proposals():
    """Each pair is scored from its own text. Reversing the inputs does not matter."""
    ecu = _mention("ECU", "The ECU resets.", "signal", 1)
    unit = _entity(ECU, "Electronic Control Unit")
    send = _mention("Com_SendSignal", "Call Com_SendSignal.", "signal", 2, item="act#1")
    group = _entity(GROUP, "Com_SendSignalGroup")
    forward = score_candidates(
        [_pair(ecu, unit), _pair(send, group)],
        [ecu, send], [unit, group], shadow=True,
    )
    backward = score_candidates(
        [_pair(send, group), _pair(ecu, unit)],
        [send, ecu], [group, unit], shadow=True,
    )
    assert backward.proposals == forward.proposals
    assert [proposal.outcome for proposal in forward.proposals] == ["undecided", "not_same"]


def test_nothing_to_score_prints_na_not_a_number(capsys):
    """No candidate pair has no score. Counts of inputs stay numbers."""
    empty = score_candidates([], [], [], shadow=True)
    assert empty.proposals == ()
    assert empty.report["n_proposals"] is None
    assert empty.report["n_same"] is None
    assert empty.report["n_conflict"] is None
    assert empty.report["scores"] is None
    assert empty.report["n_mentions"] == 0
    assert empty.report["n_entities"] == 0
    first = capsys.readouterr().out.strip()
    assert "n_mentions=0" in first
    assert "n_entities=0" in first
    assert "n_proposals=n/a" in first
    assert "n_same=n/a" in first
    assert "n_conflict=n/a" in first
    assert "scores=n/a" in first

    mention = _mention("ECU", "The ECU resets.", "signal", 1)
    entity = _entity(ECU, "Electronic Control Unit")
    none = score_candidates([], [mention], [entity], shadow=True)
    assert none.proposals == ()
    assert none.report["n_mentions"] == 1
    assert none.report["n_entities"] == 1
    assert none.report["n_proposals"] is None
    second = capsys.readouterr().out.strip()
    assert "n_mentions=1" in second
    assert "n_entities=1" in second
    assert "n_proposals=n/a" in second
    assert "n_documents=1" in second


def test_a_missing_pair_raises_and_changes_nothing(tmp_path):
    """A candidate that names no mention is refused before any score is printed."""
    wg = WorkingGraph(
        HashingEmbedder(), workdir=str(tmp_path / "wg"), merge_threshold=0.99,
    )
    path = str(tmp_path / "store.db")
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        mention = _mention("ECU", "The ECU resets.", "signal", 1)
        entity = _entity(ECU, "Electronic Control Unit")
        missing = Candidate(9, entity.key, frozenset({"rare_token"}))
        before_graph = _graph_snapshot(wg)
        before_store = _store_snapshot(store)
        with pytest.raises(ValueError, match="9"):
            score_candidates(
                [missing], [mention], [entity],
                shadow=True, graph=wg, tables=tables,
            )
        assert _graph_snapshot(wg) == before_graph
        assert _store_snapshot(store) == before_store
