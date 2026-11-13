"""E-15: Stage 1 rules 2 and 3 (spec-entity-resolution.md).

A defined alias applies only inside the source that states it, unless that
source is marked as a shared glossary. Self-reference uses a fixed phrase
table and the entity the source declares. A type conflict blocks both.

Identifiers stay case-sensitive. When two entities differ only by case, the
mention links to neither and the candidates are recorded as ambiguous.
Same source does not break that tie.
"""

from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.resolution import Mention, ResolutionTables
from grap_mcp.kee.resolution.alias import DefinitionText, link_defined
from grap_mcp.kee.resolution.exact import IdentifiedEntity

DECIDED_AT = "2026-11-13T00:00:00Z"

# The three abbreviations the embedder misses. Each expansion is the name
# in the frozen identity pair; the sentences are the list form those pairs
# are labelled with, not a copy of a source file.
CNVM = "Comisia Na\u0163ionala a Valorilor Mobiliare"
OSP = "Oficiul de supraveghere a pie\u0163elor reglementate de valori mobiliare"
SVM = "societate de valori mobiliare"


def _sentence(term, meaning):
    return (
        "\u00cen \u00een\u0163elesul prezentului regulament, termenii au "
        "urm\u0103toarele semnifica\u021bii: . " + term + " - " + meaning + ";"
    )


CNVM_SENTENCE = _sentence("C.N.V.M.", CNVM)
OSP_SENTENCE = _sentence("OSP", OSP)
SVM_SENTENCE = _sentence("S.V.M.", SVM)


def _mention(source_id, item_id, text, type_hint=None, span_start=0, sentence=None):
    return Mention(
        source_id=source_id,
        item_id=item_id,
        span_start=span_start,
        span_end=span_start + len(text),
        text=text,
        sentence=sentence if sentence is not None else text,
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


def _definition(source_id, text):
    return DefinitionText(source_id=source_id, text=text)


def _run(
    tmp_path,
    mentions,
    entities,
    definitions,
    subjects=None,
    languages=None,
    shared_glossaries=None,
    name="graph_store.db",
):
    path = str(tmp_path / name)
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        ids = tables.add_mentions(mentions)
        stored = [tables.mention(mention_id) for mention_id in ids]
        report, printed = _link(
            tables, stored, entities, definitions, subjects, languages, shared_glossaries,
        )
        decisions = {
            mention.mention_id: tables.conn.execute(
                "SELECT verdict, method, decider, score, proposal FROM link_decisions "
                "WHERE mention_id = ? ORDER BY decision_id",
                (mention.mention_id,),
            ).fetchall()
            for mention in stored
        }
    by_text = {(mention.source_id, mention.item_id): mention for mention in stored}
    return report, printed, by_text, decisions


def _link(tables, mentions, entities, definitions, subjects, languages, shared_glossaries):
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        report = link_defined(
            tables,
            mentions,
            entities,
            definitions,
            subjects=subjects,
            languages=languages,
            shared_glossaries=shared_glossaries,
            decided_at=DECIDED_AT,
        )
    return report, buffer.getvalue()


def _key(entity):
    scope = entity.source_id if entity.release is None else f"{entity.source_id}@{entity.release}"
    return f"{entity.type} | {scope} | {entity.native_id}"


def _law_entities():
    return [
        _entity("Term", CNVM, "18185"),
        _entity("Term", OSP, "18185"),
        _entity("Term", SVM, "17984"),
    ]


def _law_definitions():
    return [
        _definition("18185", CNVM_SENTENCE),
        _definition("18185", OSP_SENTENCE),
        _definition("17984", SVM_SENTENCE),
    ]


def test_three_law_abbreviations_resolve_from_their_defining_sentences(tmp_path):
    """C.N.V.M., OSP and S.V.M. each link through the sentence that defines them."""
    mentions = [
        _mention("18185", "18185#1", "C.N.V.M.", sentence="C.N.V.M. aprob\u0103 prospectul."),
        _mention("18185", "18185#2", "OSP", sentence="OSP supravegheaz\u0103 pia\u0163a."),
        _mention("17984", "17984#0", "S.V.M.", sentence="S.V.M. verific\u0103 ordinele."),
    ]
    entities = _law_entities()
    report, printed, by_text, decisions = _run(
        tmp_path, mentions, entities, _law_definitions(),
    )
    cnvm, osp, svm = entities
    assert report["links"] == (
        (by_text["17984", "17984#0"].mention_id, _key(svm)),
        (by_text["18185", "18185#1"].mention_id, _key(cnvm)),
        (by_text["18185", "18185#2"].mention_id, _key(osp)),
    )
    assert report["linked_defined_alias"] == 3
    assert report["linked_self_reference"] == 0
    assert report["ambiguous"] == 0
    assert report["unlinked"] == 0
    assert report["n_mentions"] == 3
    assert report["n_entities"] == 3
    assert report["n_documents"] == 2
    assert report["n_definitions"] == 3
    for key, method in (
        (("18185", "18185#1"), "defined_alias"),
        (("18185", "18185#2"), "defined_alias"),
        (("17984", "17984#0"), "defined_alias"),
    ):
        rows = decisions[by_text[key].mention_id]
        assert len(rows) == 1
        assert rows[0]["verdict"] == "same"
        assert rows[0]["method"] == method
        assert rows[0]["decider"] == "rule"
        assert rows[0]["score"] is None
        assert rows[0]["proposal"] == 0
    assert "linked_defined_alias=3" in printed
    assert "n_mentions=3" in printed
    assert "n_entities=3" in printed
    assert "n_documents=2" in printed
    assert "n_definitions=3" in printed
    assert "C.N.V.M." not in printed
    assert "S.V.M." not in printed
    assert CNVM not in printed
    assert OSP not in printed


def test_a_definition_does_not_leave_its_source(tmp_path):
    """The same abbreviation in another source stays unlinked."""
    mentions = [
        _mention("18185", "18185#0", "C.N.V.M."),
        _mention("other", "other#0", "C.N.V.M."),
    ]
    entity = _entity("Term", CNVM, "18185")
    report, _printed, by_text, decisions = _run(
        tmp_path,
        mentions,
        [entity],
        [_definition("18185", CNVM_SENTENCE)],
    )
    home = by_text["18185", "18185#0"]
    away = by_text["other", "other#0"]
    assert report["links"] == ((home.mention_id, _key(entity)),)
    assert report["unlinked_ids"] == (away.mention_id,)
    assert report["n_documents"] == 2
    assert decisions[home.mention_id][0]["method"] == "defined_alias"
    assert decisions[away.mention_id] == []


def test_a_shared_glossary_applies_in_other_sources(tmp_path):
    """The owner marking the defining source as a shared glossary widens it."""
    mentions = [_mention("other", "other#0", "OSP")]
    entity = _entity("Term", OSP, "18185")
    report, printed, by_text, _decisions = _run(
        tmp_path,
        mentions,
        [entity],
        [_definition("18185", OSP_SENTENCE)],
        shared_glossaries=["18185"],
    )
    assert report["links"] == ((by_text["other", "other#0"].mention_id, _key(entity)),)
    assert "n_definitions=1" in printed
    assert "n_documents=1" in printed


def test_two_definitions_of_one_abbreviation_are_ambiguous(tmp_path):
    """Two meanings in the same source link to neither."""
    mentions = [_mention("18185", "18185#0", "OSP")]
    first = _entity("Term", OSP, "18185")
    second = _entity("Term", "Alt oficiu", "18185")
    other = _definition(
        "18185",
        _sentence("OSP", "Alt oficiu"),
    )
    report, printed, by_text, decisions = _run(
        tmp_path,
        mentions,
        [second, first],
        [_definition("18185", OSP_SENTENCE), other],
    )
    mention_id = by_text["18185", "18185#0"].mention_id
    assert report["links"] == ()
    assert report["ambiguous"] == 1
    assert report["unlinked"] == 0
    assert report["n_ambiguous_candidates"] == 2
    assert report["ambiguous_mentions"] == (
        (mention_id, tuple(sorted((_key(first), _key(second))))),
    )
    assert decisions[mention_id] == []
    assert "ambiguous=1" in printed
    assert "n_ambiguous_candidates=2" in printed
    assert "OSP" not in printed


def test_case_variants_of_the_defined_entity_link_to_neither(tmp_path):
    """Two entities that differ only by case stay ambiguous. Scope does not choose."""
    mentions = [_mention("18185", "18185#0", "C.N.V.M.")]
    same_source = _entity("Term", CNVM, "18185")
    other_case = _entity("Term", CNVM.casefold(), "other")
    report, printed, by_text, decisions = _run(
        tmp_path,
        mentions,
        [other_case, same_source],
        [_definition("18185", CNVM_SENTENCE)],
    )
    mention_id = by_text["18185", "18185#0"].mention_id
    assert report["links"] == ()
    assert report["ambiguous"] == 1
    assert report["ambiguous_mentions"] == (
        (mention_id, tuple(sorted((_key(same_source), _key(other_case))))),
    )
    assert decisions[mention_id] == []
    assert "ambiguous=1" in printed
    assert CNVM.casefold() not in printed


def test_one_case_variant_of_the_defined_entity_links(tmp_path):
    """The folded name links when exactly one entity has it."""
    mentions = [_mention("18185", "18185#0", "c.n.v.m.")]
    entity = _entity("Term", CNVM, "18185")
    report, _printed, by_text, decisions = _run(
        tmp_path,
        mentions,
        [entity],
        [_definition("18185", CNVM_SENTENCE)],
    )
    mention_id = by_text["18185", "18185#0"].mention_id
    assert report["links"] == ((mention_id, _key(entity)),)
    assert decisions[mention_id][0]["method"] == "defined_alias"


def test_signal_hint_is_not_linked_to_a_component(tmp_path):
    """A type conflict blocks a defined alias. An unknown hint does not invent one."""
    blocked = [_mention("18185", "18185#0", "OSP", type_hint="signal")]
    component = _entity("Component", OSP, "18185")
    report, _printed, _by_text, decisions = _run(
        tmp_path, blocked, [component], [_definition("18185", OSP_SENTENCE)],
    )
    assert report["links"] == ()
    assert report["ambiguous"] == 0
    assert report["unlinked"] == 1
    assert decisions[next(iter(decisions))] == []

    plain = [_mention("18185", "18185#0", "OSP")]
    report, _printed, by_text, _decisions = _run(
        tmp_path,
        plain,
        [component],
        [_definition("18185", OSP_SENTENCE)],
        name="plain.db",
    )
    assert report["links"] == ((by_text["18185", "18185#0"].mention_id, _key(component)),)


def test_signal_hint_keeps_the_signal_and_drops_the_component(tmp_path):
    """The component is not a candidate, so the remaining signal is the one match."""
    mentions = [_mention("18185", "18185#0", "OSP", type_hint="signal")]
    component = _entity("Component", OSP.casefold(), "18185")
    signal = _entity("Signal", OSP, "other")
    report, _printed, by_text, _decisions = _run(
        tmp_path,
        mentions,
        [component, signal],
        [_definition("18185", OSP_SENTENCE)],
    )
    assert report["links"] == ((by_text["18185", "18185#0"].mention_id, _key(signal)),)
    assert report["ambiguous"] == 0


def test_hereafter_and_a_glossary_entry_assign_the_alias(tmp_path):
    """The other two definition forms: 'X — hereafter Y', and a glossary line."""
    mentions = [
        _mention("mod", "mod#0", "BCM"),
        _mention("gloss", "gloss#0", "ECU"),
        _mention("ro", "ro#0", "ADT"),
    ]
    brake = _entity("Component", "Brake Control Module", "mod")
    ecu = _entity("Ecu", "electronic control unit", "gloss")
    office = _entity("Term", "Agen\u0163ia de test", "ro")
    definitions = [
        _definition("mod", "Brake Control Module \u2014 hereafter BCM"),
        _definition("gloss", "ECU: electronic control unit"),
        _definition("ro", "Agen\u0163ia de test, denumit\u0103 \u00een continuare ADT"),
    ]
    report, _printed, by_text, decisions = _run(
        tmp_path, mentions, [ecu, office, brake], definitions,
    )
    assert report["links"] == (
        (by_text["gloss", "gloss#0"].mention_id, _key(ecu)),
        (by_text["mod", "mod#0"].mention_id, _key(brake)),
        (by_text["ro", "ro#0"].mention_id, _key(office)),
    )
    assert report["linked_defined_alias"] == 3
    for key in (("gloss", "gloss#0"), ("mod", "mod#0"), ("ro", "ro#0")):
        assert decisions[by_text[key].mention_id][0]["method"] == "defined_alias"


def test_self_reference_uses_the_phrase_table_and_the_declared_entity(tmp_path):
    """'prezenta lege', 'this module' and 'the module' link only in their language."""
    ro_entity = _entity("Term", "Legea de test", "act")
    en_entity = _entity("Component", "Brake Control Module", "mod")
    mentions = [
        _mention("act", "act#0", "prezenta lege"),
        _mention("act", "act#1", "Prezenta  lege"),
        _mention("act", "act#2", "this module"),
        _mention("act", "act#3", "prezentul regulament"),
        _mention("mod", "mod#0", "this module"),
        _mention("mod", "mod#1", "the module"),
        _mention("mod", "mod#2", "the module controller"),
        _mention("bare", "bare#0", "prezenta lege"),
    ]
    report, printed, by_text, decisions = _run(
        tmp_path,
        mentions,
        [ro_entity, en_entity],
        [],
        subjects={"act": [ro_entity], "mod": [en_entity]},
        languages={"act": "ro", "mod": "en"},
    )
    assert report["links"] == (
        (by_text["act", "act#0"].mention_id, _key(ro_entity)),
        (by_text["act", "act#1"].mention_id, _key(ro_entity)),
        (by_text["mod", "mod#0"].mention_id, _key(en_entity)),
        (by_text["mod", "mod#1"].mention_id, _key(en_entity)),
    )
    assert report["linked_self_reference"] == 4
    assert report["linked_defined_alias"] == 0
    assert report["unlinked"] == 4
    assert report["unlinked_ids"] == (
        by_text["act", "act#2"].mention_id,
        by_text["act", "act#3"].mention_id,
        by_text["bare", "bare#0"].mention_id,
        by_text["mod", "mod#2"].mention_id,
    )
    for key in (("act", "act#0"), ("act", "act#1"), ("mod", "mod#0"), ("mod", "mod#1")):
        rows = decisions[by_text[key].mention_id]
        assert rows[0]["method"] == "self_reference"
        assert rows[0]["decider"] == "rule"
    assert "linked_self_reference=4" in printed
    assert "n_mentions=8" in printed
    assert "prezenta lege" not in printed


def test_self_reference_without_a_declared_entity_stays_unlinked(tmp_path):
    """The phrase alone is not an entity. The source has to declare one."""
    mentions = [_mention("act", "act#0", "prezenta lege")]
    report, _printed, _by_text, decisions = _run(
        tmp_path,
        mentions,
        [_entity("Term", "Legea de test", "act")],
        [],
        languages={"act": "ro"},
    )
    assert report["links"] == ()
    assert report["unlinked"] == 1
    assert report["ambiguous"] == 0
    assert decisions[next(iter(decisions))] == []


def test_self_reference_case_variants_link_to_neither(tmp_path):
    """Two declared entities that differ only by case are ambiguous."""
    mentions = [_mention("mod", "mod#0", "the module")]
    left = _entity("Component", "Brake Control Module", "mod")
    right = _entity("Component", "brake control module", "other")
    report, _printed, by_text, decisions = _run(
        tmp_path,
        mentions,
        [right, left],
        [],
        subjects={"mod": [left, right]},
        languages={"mod": "en"},
    )
    mention_id = by_text["mod", "mod#0"].mention_id
    assert report["links"] == ()
    assert report["ambiguous_mentions"] == (
        (mention_id, tuple(sorted((_key(left), _key(right))))),
    )
    assert decisions[mention_id] == []


def test_a_signal_hint_blocks_self_reference_to_a_component(tmp_path):
    """A type conflict blocks rule 3 as well."""
    mentions = [_mention("mod", "mod#0", "this module", type_hint="signal")]
    component = _entity("Component", "Brake Control Module", "mod")
    report, _printed, _by_text, decisions = _run(
        tmp_path,
        mentions,
        [component],
        [],
        subjects={"mod": [component]},
        languages={"mod": "en"},
    )
    assert report["links"] == ()
    assert report["unlinked"] == 1
    assert report["ambiguous"] == 0
    assert decisions[next(iter(decisions))] == []


def test_defined_alias_wins_over_self_reference(tmp_path):
    """Rule 2 is applied before rule 3. One mention gets one decision."""
    alias_entity = _entity("Term", "Legea special\u0103", "act")
    subject = _entity("Term", "Legea de test", "act")
    mentions = [_mention("act", "act#0", "prezenta lege")]
    report, _printed, by_text, decisions = _run(
        tmp_path,
        mentions,
        [subject, alias_entity],
        [_definition("act", _sentence("prezenta lege", "Legea special\u0103"))],
        subjects={"act": [subject]},
        languages={"act": "ro"},
    )
    mention_id = by_text["act", "act#0"].mention_id
    assert report["links"] == ((mention_id, _key(alias_entity)),)
    assert report["linked_defined_alias"] == 1
    assert report["linked_self_reference"] == 0
    assert len(decisions[mention_id]) == 1
    assert decisions[mention_id][0]["method"] == "defined_alias"


def test_a_human_not_same_is_not_replaced(tmp_path):
    """A reviewer's not_same stays the latest decision."""
    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        mention_id = tables.add_mentions([
            _mention("18185", "18185#0", "OSP"),
        ])[0]
        entity = _entity("Term", OSP, "18185")
        tables.append_decision(
            mention_id, _key(entity), "not_same", method="review", score=None,
            decider="human", decided_at="2026-11-12T00:00:00Z",
        )
        mention = tables.mention(mention_id)
        report, _printed = _link(
            tables, [mention], [entity], [_definition("18185", OSP_SENTENCE)],
            None, None, None,
        )
        winner = tables.decision_for(mention_id, _key(entity))
        history = tables.decisions_for(mention_id, _key(entity))
    assert report["links"] == ()
    assert report["unlinked"] == 1
    assert winner.decider == "human"
    assert winner.verdict == "not_same"
    assert len(history) == 1


def test_input_order_does_not_change_links(tmp_path):
    """Mentions are decided in key order, and candidates are recorded in key order."""
    later = _mention("18185", "18185#1", "OSP")
    earlier = _mention("17984", "17984#0", "S.V.M.")
    path = str(tmp_path / "graph_store.db")
    entities = [_entity("Term", SVM, "17984"), _entity("Term", OSP, "18185")]
    definitions = [
        _definition("18185", OSP_SENTENCE),
        _definition("17984", SVM_SENTENCE),
    ]
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        ids = tables.add_mentions([later, earlier])
        stored = [tables.mention(mention_id) for mention_id in ids]
        report, _printed = _link(
            tables, list(reversed(stored)), list(reversed(entities)),
            list(reversed(definitions)), None, None, None,
        )
    by_item = {(mention.source_id, mention.item_id): mention for mention in stored}
    assert report["links"] == (
        (by_item["17984", "17984#0"].mention_id, _key(entities[0])),
        (by_item["18185", "18185#1"].mention_id, _key(entities[1])),
    )


def test_nothing_to_measure_prints_na(tmp_path, capsys):
    """No mentions is n/a. A run with mentions and no definition prints zeros, with its n."""
    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        tables = ResolutionTables(store)
        empty = link_defined(
            tables, [], [], [], decided_at=DECIDED_AT,
        )
    printed = capsys.readouterr().out
    assert empty["mentions"] is None
    assert empty["linked_defined_alias"] is None
    assert empty["linked_self_reference"] is None
    assert empty["ambiguous"] is None
    assert empty["unlinked"] is None
    assert empty["n_mentions"] == 0
    assert empty["n_entities"] == 0
    assert empty["n_documents"] == 0
    assert empty["n_definitions"] == 0
    assert "mentions=n/a" in printed
    assert "linked_defined_alias=n/a" in printed
    assert "linked_self_reference=n/a" in printed
    assert "ambiguous=n/a" in printed
    assert "unlinked=n/a" in printed
    assert "n_mentions=0" in printed
    assert "n_entities=0" in printed
    assert "n_documents=0" in printed
    assert "n_definitions=0" in printed

    mentions = [_mention("18185", "18185#0", "C.N.V.M.")]
    report, printed, _by_text, _decisions = _run(tmp_path, mentions, [], [], name="zeros.db")
    assert report["mentions"] == 1
    assert report["linked_defined_alias"] == 0
    assert report["linked_self_reference"] == 0
    assert report["ambiguous"] == 0
    assert report["unlinked"] == 1
    assert report["n_mentions"] == 1
    assert report["n_definitions"] == 0
    assert "linked_defined_alias=0" in printed
    assert "n/a" not in printed


def test_a_word_inside_the_expansion_is_not_the_alias(tmp_path):
    """The mention has to equal the assigned name, not a token of the long name."""
    mentions = [_mention("18185", "18185#0", "pie\u0163elor")]
    report, _printed, _by_text, decisions = _run(
        tmp_path,
        mentions,
        [_entity("Term", OSP, "18185")],
        [_definition("18185", OSP_SENTENCE)],
    )
    assert report["links"] == ()
    assert report["unlinked"] == 1
    assert decisions[next(iter(decisions))] == []
