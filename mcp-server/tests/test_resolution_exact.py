"""E-14: Stage 1 rule 1, identifier match (spec-entity-resolution.md).

The normalised mention equals an entity's native_id or its last path segment.
Several matches are narrowed in order: same source, same file set, same
release. What remains, if it is still more than one, links to none and the
candidates are recorded as ambiguous.

The normaliser folds case. Identifiers are case-sensitive, so a normalised
match is kept only when exactly one entity has that form. Two entities that
differ only by case, such as VehSpdFL and vehSpdFl, link to neither.
"""

from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.resolution import Mention, ResolutionTables
from grap_mcp.kee.resolution.exact import (
    IdentifiedEntity,
    SourceScope,
    link_identifiers,
)

DECIDED_AT = "2026-11-13T00:00:00Z"


def _mention(source_id, item_id, text, type_hint=None, span_start=0):
    return Mention(
        source_id=source_id,
        item_id=item_id,
        span_start=span_start,
        span_end=span_start + len(text),
        text=text,
        sentence=text,
        type_hint=type_hint,
        extractor="fixture",
    )


def _entity(type_name, native_id, source_id, release=None, file_set=None):
    return IdentifiedEntity(
        type=type_name,
        native_id=native_id,
        source_id=source_id,
        release=release,
        file_set=file_set,
    )


def _run(tmp_path, mentions, entities, sources=None, name="graph_store.db"):
    path = str(tmp_path / name)
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        ids = tables.add_mentions(mentions)
        stored = [tables.mention(mention_id) for mention_id in ids]
        report, printed = _link(tables, stored, entities, sources)
        decisions = {
            mention.mention_id: tables.conn.execute(
                "SELECT verdict, method, decider, score, proposal FROM link_decisions "
                "WHERE mention_id = ? ORDER BY decision_id",
                (mention.mention_id,),
            ).fetchall()
            for mention in stored
        }
    by_text = {mention.text: mention for mention in stored}
    return report, printed, by_text, decisions


def _link(tables, mentions, entities, sources):
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        report = link_identifiers(
            tables,
            mentions,
            entities,
            sources=sources,
            decided_at=DECIDED_AT,
        )
    return report, buffer.getvalue()


def _key(entity):
    scope = entity.source_id if entity.release is None else f"{entity.source_id}@{entity.release}"
    return f"{entity.type} | {scope} | {entity.native_id}"


def test_bracket_call_and_path_segment_match_one_entity(tmp_path):
    """A normalised mention links when it equals the native id or the last segment."""
    mentions = [
        _mention("spec", "spec#0", "[SWS_Com_00123]"),
        _mention("spec", "spec#1", "Com_SendSignal()"),
        _mention("spec", "spec#2", "Signal1", type_hint="signal"),
        _mention("spec", "spec#3", "Com_SendSignalGroup"),
    ]
    entities = [
        _entity("Requirement", "SWS_Com_00123", "spec"),
        _entity("Interface", "Com_SendSignal", "spec"),
        _entity("Signal", "/ISignal/Signal1", "spec"),
        _entity("Interface", "Com_SendSignalGroup", "spec"),
    ]
    report, printed, by_text, decisions = _run(tmp_path, mentions, entities)

    assert report["links"] == (
        (by_text["[SWS_Com_00123]"].mention_id, _key(entities[0])),
        (by_text["Com_SendSignal()"].mention_id, _key(entities[1])),
        (by_text["Signal1"].mention_id, _key(entities[2])),
        (by_text["Com_SendSignalGroup"].mention_id, _key(entities[3])),
    )
    assert report["ambiguous"] == 0
    assert report["unlinked"] == 0
    for mention in by_text.values():
        rows = decisions[mention.mention_id]
        assert len(rows) == 1
        assert rows[0]["verdict"] == "same"
        assert rows[0]["method"] == "identifier"
        assert rows[0]["decider"] == "rule"
        assert rows[0]["score"] is None
        assert rows[0]["proposal"] == 0
    assert "n_mentions=4" in printed
    assert "n_entities=4" in printed
    assert "n_documents=1" in printed
    assert "linked_identifier=4" in printed
    assert "SWS_Com_00123" not in printed
    assert "Signal1" not in printed


def test_send_signal_does_not_match_send_signal_group(tmp_path):
    """Equal after normalisation is the whole form, not a shared prefix."""
    mentions = [_mention("spec", "spec#0", "Com_SendSignal")]
    entities = [_entity("Interface", "Com_SendSignalGroup", "spec")]
    report, _printed, _by_text, decisions = _run(tmp_path, mentions, entities)
    assert report["links"] == ()
    assert report["unlinked"] == 1
    assert report["ambiguous"] == 0
    assert decisions[next(iter(decisions))] == []


def test_all_caps_without_a_break_does_not_match_the_camel_name(tmp_path):
    """VEHSPDFL and VehSpdFL are different normalised forms."""
    mentions = [_mention("spec", "spec#0", "VEHSPDFL", type_hint="signal")]
    entities = [_entity("Signal", "VehSpdFL", "spec")]
    report, _printed, _by_text, _decisions = _run(tmp_path, mentions, entities)
    assert report["links"] == ()
    assert report["unlinked"] == 1


def test_one_case_variant_matches_on_the_normalised_form(tmp_path):
    """The folded form links when exactly one entity has it."""
    mentions = [_mention("spec", "spec#0", "VehSpdFL", type_hint="signal")]
    entities = [_entity("Signal", "vehSpdFl", "other")]
    report, _printed, by_text, decisions = _run(tmp_path, mentions, entities)
    mention_id = by_text["VehSpdFL"].mention_id
    assert report["links"] == ((mention_id, _key(entities[0])),)
    assert decisions[mention_id][0]["verdict"] == "same"
    assert decisions[mention_id][0]["decider"] == "rule"


def test_vehspdfl_and_vehspdfl_case_variant_are_ambiguous(tmp_path):
    """VehSpdFL and vehSpdFl differ only by case: link to neither.

    The same-source entity is not chosen. Scope does not break a case tie.
    """
    mentions = [_mention("fl", "fl#0", "VehSpdFL", type_hint="signal")]
    same_source = _entity("Signal", "VehSpdFL", "fl", file_set="body", release="4.2")
    other_case = _entity("Signal", "vehSpdFl", "fr", file_set="body", release="4.2")
    entities = [other_case, same_source]
    report, printed, by_text, decisions = _run(tmp_path, mentions, entities)
    mention_id = by_text["VehSpdFL"].mention_id
    assert report["links"] == ()
    assert report["ambiguous"] == 1
    assert report["unlinked"] == 0
    assert report["n_ambiguous_candidates"] == 2
    assert report["ambiguous_mentions"] == (
        (mention_id, (_key(same_source), _key(other_case))),
    )
    assert decisions[mention_id] == []
    assert "ambiguous=1" in printed
    assert "n_ambiguous_candidates=2" in printed
    assert "VehSpdFL" not in printed
    assert "vehSpdFl" not in printed


def test_path_segments_that_differ_only_by_case_are_ambiguous(tmp_path):
    """The last segment is the matched surface, and case still blocks a link."""
    mentions = [_mention("spec", "spec#0", "VehSpdFL", type_hint="signal")]
    left = _entity("Signal", "/ISignal/VehSpdFL", "spec")
    right = _entity("Signal", "/ISignal/vehSpdFl", "other")
    report, _printed, by_text, decisions = _run(
        tmp_path, mentions, [right, left],
    )
    mention_id = by_text["VehSpdFL"].mention_id
    assert report["links"] == ()
    assert report["ambiguous_mentions"] == (
        (mention_id, tuple(sorted((_key(left), _key(right))))),
    )
    assert decisions[mention_id] == []


def test_same_source_beats_file_set_and_release(tmp_path):
    """One entity in the mention's source wins, even when another matches later scopes."""
    mentions = [_mention("file-a", "file-a#0", "Signal1", type_hint="signal")]
    same_source = _entity("Signal", "Signal1", "file-a", file_set="body", release="4.2")
    better_later = _entity(
        "Signal", "/Other/Signal1", "file-b", file_set="chassis", release="4.3",
    )
    sources = {"file-a": SourceScope(file_set="chassis", release="4.3")}
    report, _printed, by_text, _decisions = _run(
        tmp_path, mentions, [better_later, same_source], sources,
    )
    mention_id = by_text["Signal1"].mention_id
    assert report["links"] == ((mention_id, _key(same_source)),)
    assert report["ambiguous"] == 0


def test_file_set_is_next_when_no_source_matches(tmp_path):
    """With no shared source, the shared file set wins over the shared release."""
    mentions = [_mention("spec", "spec#0", "Signal1", type_hint="signal")]
    in_set = _entity("Signal", "/Body/Signal1", "file-a", file_set="body", release="4.2")
    in_release = _entity(
        "Signal", "/Chassis/Signal1", "file-b", file_set="chassis", release="4.3",
    )
    sources = {"spec": SourceScope(file_set="body", release="4.3")}
    report, _printed, by_text, _decisions = _run(
        tmp_path, mentions, [in_release, in_set], sources,
    )
    assert report["links"] == ((by_text["Signal1"].mention_id, _key(in_set)),)


def test_release_is_used_when_source_and_file_set_do_not_separate(tmp_path):
    """Release is the third preference, applied to whoever survived the first two."""
    mentions = [_mention("extract", "extract#0", "Signal1", type_hint="signal")]
    current = _entity("Signal", "Signal1", "extract", file_set="body", release="4.2")
    older = _entity("Signal", "/Old/Signal1", "extract", file_set="body", release="4.1")
    other_set = _entity(
        "Signal", "/Chassis/Signal1", "other", file_set="chassis", release="4.2",
    )
    sources = {"extract": SourceScope(file_set="body", release="4.2")}
    report, _printed, by_text, _decisions = _run(
        tmp_path, mentions, [older, other_set, current], sources,
    )
    assert report["links"] == ((by_text["Signal1"].mention_id, _key(current)),)


def test_two_left_after_scope_are_ambiguous_and_the_eliminated_one_is_not(tmp_path):
    """Scope drops the other source. The two that remain are recorded, and neither is linked."""
    mentions = [_mention("extract", "extract#0", "Signal1", type_hint="signal")]
    first = _entity("Signal", "Signal1", "extract", file_set="body", release="4.2")
    second = _entity("Signal", "/Also/Signal1", "extract", file_set="body", release="4.2")
    other = _entity("Signal", "/Away/Signal1", "other", file_set="body", release="4.2")
    sources = {"extract": SourceScope(file_set="body", release="4.2")}
    report, _printed, by_text, decisions = _run(
        tmp_path, mentions, [other, second, first], sources,
    )
    mention_id = by_text["Signal1"].mention_id
    assert report["links"] == ()
    assert report["ambiguous_mentions"] == (
        (mention_id, (_key(second), _key(first))),
    )
    assert _key(other) not in report["ambiguous_mentions"][0][1]
    assert decisions[mention_id] == []


def test_a_single_match_in_another_source_still_links(tmp_path):
    """Scope order applies only when several entities match."""
    mentions = [_mention("spec", "spec#0", "Signal1", type_hint="signal")]
    only = _entity("Signal", "/ISignal/Signal1", "other", release="4.2")
    report, _printed, by_text, _decisions = _run(tmp_path, mentions, [only])
    assert report["links"] == ((by_text["Signal1"].mention_id, _key(only)),)


def test_signal_hint_is_not_linked_to_a_component(tmp_path):
    """A type conflict blocks the link. An unknown hint does not invent one."""
    blocked = [_mention("spec", "spec#0", "VehSpdFL", type_hint="signal")]
    component = [_entity("Component", "VehSpdFL", "spec")]
    report, _printed, _by_text, decisions = _run(tmp_path, blocked, component)
    assert report["links"] == ()
    assert report["ambiguous"] == 0
    assert report["unlinked"] == 1
    assert decisions[next(iter(decisions))] == []

    plain = [_mention("spec", "spec#0", "VehSpdFL")]
    report, _printed, by_text, _decisions = _run(
        tmp_path, plain, component, name="plain.db",
    )
    assert report["links"] == ((by_text["VehSpdFL"].mention_id, _key(component[0])),)


def test_signal_hint_keeps_the_signal_and_drops_the_component(tmp_path):
    """The component is not a candidate, so the remaining signal is the one match."""
    mentions = [_mention("spec", "spec#0", "VehSpdFL", type_hint="signal")]
    component = _entity("Component", "vehSpdFl", "spec")
    signal = _entity("Signal", "VehSpdFL", "other")
    report, _printed, by_text, _decisions = _run(
        tmp_path, mentions, [component, signal],
    )
    assert report["links"] == ((by_text["VehSpdFL"].mention_id, _key(signal)),)
    assert report["ambiguous"] == 0


def test_camel_and_underscore_forms_use_scope_not_the_case_rule(tmp_path):
    """VehSpdFL and VEH_SPD_FL fold together and do not differ only by case."""
    mentions = [_mention("fl", "fl#0", "VehSpdFL", type_hint="signal")]
    camel = _entity("Signal", "VehSpdFL", "fl")
    underscored = _entity("Signal", "VEH_SPD_FL", "fr")
    report, _printed, by_text, _decisions = _run(
        tmp_path, mentions, [underscored, camel],
    )
    assert report["links"] == ((by_text["VehSpdFL"].mention_id, _key(camel)),)


def test_same_fold_that_is_not_only_case_stays_ambiguous_when_scope_cannot_separate(tmp_path):
    """Both writings survive every scope preference, so neither is linked."""
    mentions = [_mention("fl", "fl#0", "VehSpdFL", type_hint="signal")]
    camel = _entity("Signal", "VehSpdFL", "fl", file_set="body", release="4.2")
    underscored = _entity("Signal", "VEH_SPD_FL", "fl", file_set="body", release="4.2")
    sources = {"fl": SourceScope(file_set="body", release="4.2")}
    report, _printed, by_text, decisions = _run(
        tmp_path, mentions, [underscored, camel], sources,
    )
    mention_id = by_text["VehSpdFL"].mention_id
    assert report["links"] == ()
    assert report["ambiguous_mentions"] == (
        (mention_id, (_key(underscored), _key(camel))),
    )
    assert decisions[mention_id] == []


def test_input_order_does_not_change_links_or_recorded_candidates(tmp_path):
    """Mentions are decided in key order, and candidates are recorded in key order."""
    later = _mention("spec", "spec#1", "Signal1", type_hint="signal")
    earlier = _mention("spec", "spec#0", "Com_SendSignal")
    case_mention = _mention("spec", "spec#2", "VehSpdFL", type_hint="signal")
    call = _entity("Interface", "Com_SendSignal", "other")
    signal = _entity("Signal", "/ISignal/Signal1", "other")
    case_a = _entity("Signal", "VehSpdFL", "a")
    case_b = _entity("Signal", "vehSpdFl", "b")
    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        ids = tables.add_mentions([case_mention, later, earlier])
        stored = [tables.mention(mention_id) for mention_id in ids]
        report, _printed = _link(
            tables,
            list(reversed(stored)),
            [case_b, signal, case_a, call],
            None,
        )
        rows = tables.conn.execute(
            "SELECT mention_id FROM link_decisions ORDER BY decision_id"
        ).fetchall()
    by_text = {mention.text: mention for mention in stored}
    call_id = by_text["Com_SendSignal"].mention_id
    signal_id = by_text["Signal1"].mention_id
    case_id = by_text["VehSpdFL"].mention_id
    assert report["links"] == (
        (call_id, _key(call)),
        (signal_id, _key(signal)),
    )
    assert report["ambiguous_mentions"] == (
        (case_id, tuple(sorted((_key(case_a), _key(case_b))))),
    )
    assert [row["mention_id"] for row in rows] == [call_id, signal_id]


def test_a_human_not_same_is_not_replaced(tmp_path):
    """A reviewer's not_same stays the latest decision. The rule does not append over it."""
    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        mention_id = tables.add_mentions([
            _mention("spec", "spec#0", "Signal1", type_hint="signal"),
        ])[0]
        entity = _entity("Signal", "Signal1", "spec")
        tables.append_decision(
            mention_id, _key(entity), "not_same", method="review", score=None,
            decider="human", decided_at="2026-11-12T00:00:00Z",
        )
        mention = tables.mention(mention_id)
        report, _printed = _link(tables, [mention], [entity], None)
        winner = tables.decision_for(mention_id, _key(entity))
        history = tables.decisions_for(mention_id, _key(entity))
    assert report["links"] == ()
    assert report["unlinked"] == 1
    assert winner.decider == "human"
    assert winner.verdict == "not_same"
    assert len(history) == 1


def test_nothing_to_measure_prints_na(tmp_path, capsys):
    """No mentions is n/a. A run with mentions and no link prints zeros, with its n."""
    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        empty = link_identifiers(
            tables, [], [], sources=None, decided_at=DECIDED_AT,
        )
    printed = capsys.readouterr().out
    assert empty["mentions"] is None
    assert empty["linked_identifier"] is None
    assert empty["ambiguous"] is None
    assert empty["unlinked"] is None
    assert empty["n_mentions"] == 0
    assert empty["n_entities"] == 0
    assert empty["n_documents"] == 0
    assert "mentions=n/a" in printed
    assert "linked_identifier=n/a" in printed
    assert "ambiguous=n/a" in printed
    assert "unlinked=n/a" in printed
    assert "n_mentions=0" in printed
    assert "n_entities=0" in printed
    assert "n_documents=0" in printed

    mentions = [_mention("spec", "spec#0", "Signal1", type_hint="signal")]
    report, printed, _by_text, _decisions = _run(tmp_path, mentions, [])
    assert report["mentions"] == 1
    assert report["linked_identifier"] == 0
    assert report["ambiguous"] == 0
    assert report["unlinked"] == 1
    assert report["n_mentions"] == 1
    assert report["n_entities"] == 0
    assert report["n_documents"] == 1
    assert "linked_identifier=0" in printed
    assert "n/a" not in printed
