"""E-20: export and import of the review spreadsheet (spec §5).

The sheet has Sheet 2's columns. The spec adds the score in front of the empty
decision column. ``mention_id`` and ``entity_key`` are on the sheet so a filled
row is attached to the stored pair and not to another row with the same name.

A filled row becomes a link decision whose decider is ``human``. ``same`` holds
generally and also stores a ``must_link`` constraint. ``different`` holds
generally and stores ``cannot_link``. ``same only here`` is a decision only.
``unclear`` and a blank decision are not decisions. A label outside the guide
list writes nothing.
"""

import csv

import pytest

from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.resolution import Mention, ResolutionTables
from grap_mcp.kee.resolution.normalise import normalise
from grap_mcp.kee.resolution.review import (
    COLUMNS,
    ReviewCandidate,
    ReviewRefusal,
    decision_in_force,
    export_review_list,
    import_review_list,
)

# Sheet 2, then the score the spec places before the decision, then the pair's
# own identity. ``label`` is the empty decision column.
EXPECTED_COLUMNS = [
    "packet_id",
    "source_id",
    "item_id",
    "passage",
    "name",
    "other_source_id",
    "other_item_id",
    "other_passage",
    "other_name",
    "score",
    "label",
    "note",
    "mention_id",
    "entity_key",
]

WIDGET = "Signal | demo | Widget"
GADGET = "Signal | demo | Gadget"
WHEN = "2026-11-13T12:00:00Z"


def _mention(text="Widget", sentence="The Widget sends.", source_id="demo", item_id="demo#0"):
    return Mention(
        source_id=source_id,
        item_id=item_id,
        span_start=0,
        span_end=len(text),
        text=text,
        sentence=sentence,
        type_hint="signal",
        extractor="fixture",
    )


def _open(tmp_path):
    path = str(tmp_path / "graph_store.db")
    graph = GraphStore(path)
    graph.__enter__()
    return graph, ResolutionTables(graph)


def _read(path):
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def _discovered(mention):
    return f"Term | {mention.source_id} | {normalise(mention.text)}"


def test_columns_are_sheet2_plus_the_score_and_the_pair_identity():
    """The header is fixed. A reviewer's decision column is ``label``."""
    assert list(COLUMNS) == EXPECTED_COLUMNS


def test_export_writes_both_contexts_the_score_and_an_empty_decision(tmp_path, capsys):
    """One undecided pair becomes one row. A pair a human already decided is
    not listed again. A proposal whose mention was re-extracted is not given
    a made-up sentence; it is counted and left off the sheet.
    """
    graph, tables = _open(tmp_path)
    try:
        kept = _mention()
        gone = _mention(text="Gadget", sentence="The Gadget sends.", item_id="demo#1")
        kept_id, gone_id = tables.add_mentions([kept, gone])
        tables.append_decision(
            kept_id, WIDGET, "same", method="embedding", score=0.75,
            decider="stage3", decided_at=WHEN, proposal=True,
        )
        tables.append_decision(
            gone_id, GADGET, "not_same", method="embedding", score=0.5,
            decider="stage3", decided_at=WHEN, proposal=True,
        )
        tables.append_decision(
            kept_id, GADGET, "same", method="embedding", score=0.9,
            decider="stage3", decided_at=WHEN, proposal=True,
        )
        tables.append_decision(
            kept_id, GADGET, "same", method="review", score=0.9,
            decider="human", decided_at=WHEN,
        )
        tables.replace_item_mentions("demo", "demo#1", [])
        path = tmp_path / "review.csv"
        export_review_list(tables, path)
        report = capsys.readouterr().out.strip()
    finally:
        graph.__exit__(None, None, None)

    header, rows = _read(path)
    assert header == EXPECTED_COLUMNS
    assert len(rows) == 1
    row = rows[0]
    assert row["packet_id"] == "review-0001"
    assert row["source_id"] == "demo"
    assert row["item_id"] == "demo#0"
    assert row["name"] == "Widget"
    assert row["passage"] == "The Widget sends."
    assert row["other_name"] == "Widget"
    assert row["other_source_id"] == "demo"
    assert row["other_item_id"] == ""
    assert row["other_passage"] == ""
    assert row["score"] == "0.75"
    assert row["label"] == ""
    assert row["note"] == ""
    assert row["mention_id"] == str(kept_id)
    assert row["entity_key"] == WIDGET
    assert "n_rows=1" in report
    assert "n_proposals=3" in report
    assert "n_mentions=1" in report
    assert "n_documents=1" in report
    assert "n_without_sentence=1/1" in report
    assert "n_orphan=1" in report
    assert "Widget sends" not in report


def test_export_keeps_the_entity_sentence_it_was_given(tmp_path):
    """Stage 3 has the entity's sentence in hand. Export writes that sentence,
    not a sentence looked up by name.
    """
    graph, tables = _open(tmp_path)
    try:
        mention = _mention(text="ministru", sentence="se numește ministru.")
        mention_id = tables.add_mentions([mention])[0]
        path = tmp_path / "review.csv"
        export_review_list(tables, path, [ReviewCandidate(
            mention_id=mention_id,
            entity_key="Term | other | minister",
            other_name="minister",
            other_sentence="funcția de minister.",
            other_source_id="other",
            other_item_id="other#0",
            score=0.5,
        )])
    finally:
        graph.__exit__(None, None, None)

    _header, rows = _read(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "ministru"
    assert row["passage"] == "se numește ministru."
    assert row["other_name"] == "minister"
    assert row["other_passage"] == "funcția de minister."
    assert row["other_source_id"] == "other"
    assert row["other_item_id"] == "other#0"
    assert row["score"] == "0.5"
    assert row["label"] == ""
    assert row["entity_key"] == "Term | other | minister"


@pytest.mark.parametrize(
    "label,verdict,kind",
    [
        ("same", "same", "must_link"),
        ("same only here", "same", None),
        ("different", "not_same", "cannot_link"),
        ("part of", "not_same", None),
        ("realises", "not_same", None),
        ("same, other release", "not_same", None),
        ("unclear", None, None),
        ("", None, None),
    ],
)
def test_import_maps_the_guide_label(tmp_path, label, verdict, kind):
    """B3 collapses the guide labels. A general identity becomes a constraint.
    ``unclear`` and a blank cell do not. A later rule does not displace a human.
    """
    graph, tables = _open(tmp_path)
    try:
        mention = _mention()
        mention_id = tables.add_mentions([mention])[0]
        tables.append_decision(
            mention_id, WIDGET, "not_same", method="identifier", score=None,
            decider="rule", decided_at="2026-11-13T09:00:00Z",
        )
        path = tmp_path / "review.csv"
        export_review_list(tables, path, [ReviewCandidate(
            mention_id=mention_id,
            entity_key=WIDGET,
            other_name="Widget",
            other_sentence="A widget on the bus.",
            other_source_id="demo",
            score=0.75,
        )])
        _header, rows = _read(path)
        rows[0]["label"] = label
        rows[0]["note"] = "holds generally" if label == "same only here" else ""
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

        report = import_review_list(tables, path, decided_at=WHEN)
        tables.append_decision(
            mention_id, WIDGET, "not_same", method="identifier", score=None,
            decider="rule", decided_at="2026-11-13T18:00:00Z",
        )
        in_force = decision_in_force(tables, mention_id, WIDGET)
        constraints = tables.constraints()
        history = tables.decisions_for(mention_id, WIDGET)
        latest = tables.decision_for(mention_id, WIDGET)
    finally:
        graph.__exit__(None, None, None)

    assert "n_rows=1" in report
    assert "n_documents=1" in report
    if verdict is None:
        assert in_force.decider == "rule"
        assert in_force.verdict == "not_same"
        assert constraints == []
        assert "n_decided=0/1" in report
        return

    assert in_force.decider == "human"
    assert in_force.method == "review"
    assert in_force.verdict == verdict
    assert in_force.proposal is False
    assert in_force.score == 0.75
    assert in_force.decided_at == WHEN
    # Append order still sees the re-run. The human row is what stays in force.
    assert latest.decider == "rule"
    assert any(row.decider == "human" for row in history)
    assert "n_decided=1/1" in report
    if kind is None:
        assert constraints == []
        assert "n_constraints=0" in report
    else:
        assert len(constraints) == 1
        constraint = constraints[0]
        assert constraint.kind == kind
        assert constraint.origin == "human"
        assert constraint.reason == label
        assert {constraint.entity_a, constraint.entity_b} == {_discovered(mention), WIDGET}
        assert "n_constraints=1" in report


def test_importing_again_does_not_duplicate_the_constraint(tmp_path):
    """A re-run keeps the human decision and does not store the same standing
    rule twice. A second, different human label is a new decision.
    """
    graph, tables = _open(tmp_path)
    try:
        mention = _mention()
        mention_id = tables.add_mentions([mention])[0]
        path = tmp_path / "review.csv"
        export_review_list(tables, path, [ReviewCandidate(
            mention_id=mention_id,
            entity_key=WIDGET,
            other_name="Widget",
            other_sentence="A widget on the bus.",
            other_source_id="demo",
            score=None,
        )])
        _header, rows = _read(path)
        rows[0]["label"] = "same"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        import_review_list(tables, path, decided_at=WHEN)
        import_review_list(tables, path, decided_at="2026-11-13T13:00:00Z")
        humans = [
            row for row in tables.decisions_for(mention_id, WIDGET) if row.decider == "human"
        ]
        constraints = tables.constraints()
        in_force = decision_in_force(tables, mention_id, WIDGET)
    finally:
        graph.__exit__(None, None, None)

    assert len(humans) == 2
    assert len(constraints) == 1
    assert constraints[0].kind == "must_link"
    assert in_force.decided_at == "2026-11-13T13:00:00Z"
    assert in_force.score is None


def test_same_entity_on_both_sides_has_no_constraint(tmp_path):
    """A constraint is between two entities. The link decision is still stored."""
    graph, tables = _open(tmp_path)
    try:
        mention = _mention()
        mention_id = tables.add_mentions([mention])[0]
        key = _discovered(mention)
        path = tmp_path / "review.csv"
        export_review_list(tables, path, [ReviewCandidate(
            mention_id=mention_id,
            entity_key=key,
            other_name="widget",
            other_sentence="widget",
            other_source_id="demo",
            score=1,
        )])
        _header, rows = _read(path)
        rows[0]["label"] = "same"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        report = import_review_list(tables, path, decided_at=WHEN)
        in_force = decision_in_force(tables, mention_id, key)
        constraints = tables.constraints()
    finally:
        graph.__exit__(None, None, None)

    assert in_force.verdict == "same"
    assert in_force.decider == "human"
    assert constraints == []
    assert "n_self=1" in report


def test_a_bad_label_writes_nothing(tmp_path):
    """One label outside the guide list refuses the whole sheet. The good row
    is not imported either, and the rule decision already stored is untouched.
    """
    graph, tables = _open(tmp_path)
    try:
        first = _mention()
        second = _mention(text="Gadget", sentence="The Gadget sends.", item_id="demo#1")
        first_id, second_id = tables.add_mentions([first, second])
        tables.append_decision(
            first_id, WIDGET, "not_same", method="identifier", score=None,
            decider="rule", decided_at=WHEN,
        )
        path = tmp_path / "review.csv"
        export_review_list(tables, path, [
            ReviewCandidate(
                mention_id=first_id, entity_key=WIDGET, other_name="Widget",
                other_sentence="A widget.", other_source_id="demo", score=0.75,
            ),
            ReviewCandidate(
                mention_id=second_id, entity_key=GADGET, other_name="Gadget",
                other_sentence="A gadget.", other_source_id="demo", score=0.7,
            ),
        ])
        _header, rows = _read(path)
        rows[0]["label"] = "same"
        rows[1]["label"] = "sme"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        with pytest.raises(ReviewRefusal, match=r"pairs: 1 label outside the guide list") as caught:
            import_review_list(tables, path, decided_at=WHEN)
        message = str(caught.value)
        decisions = tables.decisions_for(first_id, WIDGET)
        other = tables.decisions_for(second_id, GADGET)
        constraints = tables.constraints()
    finally:
        graph.__exit__(None, None, None)

    assert "n_rows=2" in message
    assert "n_documents=1" in message
    assert "sme" not in message
    assert len(decisions) == 1
    assert decisions[0].decider == "rule"
    assert other == []
    assert constraints == []


def test_a_row_that_disagrees_with_the_mention_writes_nothing(tmp_path):
    """The name on the sheet has to be the text that was stored. A mismatch
    is refused before any decision is appended.
    """
    graph, tables = _open(tmp_path)
    try:
        mention_id = tables.add_mentions([_mention()])[0]
        path = tmp_path / "review.csv"
        export_review_list(tables, path, [ReviewCandidate(
            mention_id=mention_id, entity_key=WIDGET, other_name="Widget",
            other_sentence="A widget.", other_source_id="demo", score=0.75,
        )])
        _header, rows = _read(path)
        rows[0]["label"] = "same"
        rows[0]["name"] = "Gadget"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        with pytest.raises(ReviewRefusal, match=r"pairs: 1 row whose mention does not match"):
            import_review_list(tables, path, decided_at=WHEN)
        decisions = tables.decisions_for(mention_id, WIDGET)
        constraints = tables.constraints()
    finally:
        graph.__exit__(None, None, None)

    assert decisions == []
    assert constraints == []


def test_empty_store_prints_measured_zeros_and_na(tmp_path, capsys):
    """Nothing to list is a measured zero. A fraction with no rows is ``n/a``."""
    graph, tables = _open(tmp_path)
    try:
        path = tmp_path / "review.csv"
        export_review_list(tables, path)
        report = capsys.readouterr().out.strip()
    finally:
        graph.__exit__(None, None, None)

    header, rows = _read(path)
    assert header == EXPECTED_COLUMNS
    assert rows == []
    assert "n_rows=0" in report
    assert "n_proposals=0" in report
    assert "n_mentions=0" in report
    assert "n_documents=0" in report
    assert "n_without_sentence=n/a" in report
