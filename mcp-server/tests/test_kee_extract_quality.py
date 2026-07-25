"""Extraction quality telemetry — the shape metrics that quantity metrics miss."""
from grap_mcp.kee.extract_quality import QualityMonitor


def _frag(outcomes, relations):
    return {"outcomes": [{"name": n, "characteristics": []} for n in outcomes],
            "relations": [{"from": a, "type": t, "to": b} for a, t, b in relations]}


def test_detects_template_collapse_from_constant_output():
    """The real incident: the model emitted exactly 3 relations on 10/10 chunks
    while every per-chunk average looked healthy. A variance catches it."""
    qm = QualityMonitor()
    for _ in range(10):
        qm.record(_frag(["A"], [("x", "emite", "y"), ("y", "adoptată de", "x"),
                               ("z", "contrasemnează", "w")]))
    r = qm.report()
    assert r["relation_count_cv"] == 0.0
    assert any("template_collapse" in a for a in r["alarms"])


def test_healthy_variance_raises_no_template_alarm():
    qm = QualityMonitor()
    for k in (1, 4, 2, 7, 3, 5, 2, 6, 1, 4):
        qm.record(_frag(["A"], [("x", f"t{i}", "y") for i in range(k)]))
    r = qm.report()
    assert r["relation_count_cv"] >= 0.25
    assert not any("template_collapse" in a for a in r["alarms"])


def test_detects_class_named_outcomes_and_notation_leak():
    qm = QualityMonitor(class_vocab=["NormativeAct", "AuthoritativeBody"])
    qm.record(_frag(["NormativeAct", "ORDONANŢA nr. 33"],
                    [("a", "--emite-->", "b"), ("c", "emite", "d")]))
    r = qm.report()
    assert r["class_named_outcomes"] == 1
    assert r["notation_leaks"] == 1
    assert any("class_named_outcomes" in a for a in r["alarms"])
    assert any("notation_leak" in a for a in r["alarms"])


def test_parse_failures_tracked_and_alarmed():
    qm = QualityMonitor()
    for _ in range(8):
        qm.record(_frag(["A"], [("x", "t", "y")]))
    for _ in range(4):
        qm.record_failure()
    r = qm.report()
    assert r["chunks"] == 12 and r["parse_failures"] == 4
    assert r["parse_failure_rate"] == round(4 / 12, 3)
    # the alarm covers every way a chunk can yield nothing, not just bad JSON
    assert any("no_fragment" in a for a in r["alarms"])


def test_failure_causes_are_not_lumped_together():
    """A server outage and an empty table-of-contents page are not parse failures.

    Lumping them held the reported rate at a constant 9.1% across five batches of
    the 750-doc run — a number about one poisoned chunk, not about the documents.
    """
    qm = QualityMonitor()
    for _ in range(7):
        qm.record(_frag(["A"], [("x", "t", "y")]))
    qm.record_failure("unparsed")
    qm.record_failure("empty")
    qm.record_failure("provider")
    r = qm.report()
    assert r["chunks"] == 10
    assert r["parse_failures"] == 1
    assert r["empty_extractions"] == 1
    assert r["provider_errors"] == 1
    assert r["parse_failure_rate"] == 0.1
    # the lumped figure survives under an honest name, so history stays comparable
    assert r["no_fragment_rate"] == 0.3
    assert any("unparsed 1" in a and "empty 1" in a and "provider 1" in a
               for a in r["alarms"])


def test_clean_run_has_no_alarms(tmp_path):
    qm = QualityMonitor(class_vocab=["NormativeAct"])
    for k in (2, 5, 3, 6, 1, 4, 7, 2):
        qm.record(_frag(["Actul X"], [("a", f"rel{i}", "b") for i in range(k)]))
    r = qm.report()
    assert r["alarms"] == []
    assert r["distinct_relation_types"] >= 5
    qm.write(tmp_path / "q.json")
    assert (tmp_path / "q.json").exists()
