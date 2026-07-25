"""Review fixes 1-4 (docs/dark-factory-review-p3-p5.md): fresh canary
baseline, rename-aware churn + canary counting, restart rehydration, and
opt-in PG write-back."""
import hashlib
import importlib

from grap_mcp.governance import (DEFAULT_ADOPTION_POLICY, diff_ontology_dicts,
                                 evaluate_proposal_dict)
from grap_mcp.kee.llm import StubProvider


def _sha(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _onto(entries):
    """entries: list of (oid, name, chars)."""
    out = ["version: kee-candidate", "process_outcomes:"]
    for oid, name, chars in entries:
        out += [f"- id: {oid}", f"  name: {name}", f"  description: {name}.",
                f"  required_characteristics: [{', '.join(chars)}]"]
    out += ["mapping_hints: []", "concept_hashes:"]
    out += [f"  {oid}: {_sha(name)}" for oid, name, _ in entries]
    out += ["source_chunk_counts:"]
    out += [f"  {oid}: 2" for oid, _, _ in entries]
    return "\n".join(out) + "\n"


def _server(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", role)
    import grap_mcp.server as s
    importlib.reload(s)
    return s


def _adopt(s, yaml_text):
    vh = s._get_workspace().publish_ontology(yaml_text, metadata={}).hash
    out = s.adopt_proposed_ontology(vh, rationale="test")
    assert out["status"] == "ok"
    return out["new_version_hash"]


# ---- fix 2: rename detection + rename-aware churn (pure) ---------------------

def _prop(entries):
    return {"process_outcomes": [
        {"id": oid, "name": name, "required_characteristics": list(chars)}
        for oid, name, chars in entries]}


def test_diff_detects_renames_conservatively():
    base = _prop([("rv", "RV", ["x", "y"]), ("qa", "QA", ["z"])])
    target = _prop([("rv2", "RV2", ["x", "y"]), ("qa", "QA", ["z"])])
    d = diff_ontology_dicts(base, target)
    assert d["outcomes_renamed"] == [["rv", "rv2"]]
    # ambiguous fingerprints (two removed share one char set) -> no pairing
    base2 = _prop([("a", "A", ["x"]), ("b", "B", ["x"])])
    target2 = _prop([("c", "C", ["x"])])
    assert diff_ontology_dicts(base2, target2)["outcomes_renamed"] == []
    # empty char sets never pair
    base3 = _prop([("a", "A", [])])
    target3 = _prop([("c", "C", [])])
    assert diff_ontology_dicts(base3, target3)["outcomes_renamed"] == []


def test_churn_does_not_count_renames_but_keeps_pg_refs_strict():
    policy = {**DEFAULT_ADOPTION_POLICY, "enabled": True, "min_source_chunks": 0}
    active = _prop([("rv", "RV", ["x"])])
    prop = _prop([("rv2", "RV2", ["x"])])
    prop["concept_hashes"] = {"rv2": _sha("RV2")}
    # rename within a zero-removal budget: passes
    v = evaluate_proposal_dict(prop, active, policy)
    churn = next(c for c in v["checks"] if c["name"] == "churn")
    assert v["pass"] is True and churn["ok"] is True
    assert "renames not counted" in str(churn["detail"])
    # but a rename of a PG-referenced outcome still violates (dangling ref)
    v2 = evaluate_proposal_dict(prop, active, policy, pg_referenced_ids={"rv"})
    assert v2["pass"] is False


# ---- fixes 1+2 in the flow: fresh baseline + rename carryover -----------------

def test_canary_uses_fresh_baseline_not_stale_production_run(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    _adopt(s, _onto([("rv", "Requirement Verification", ["verification_method"])]))
    docs = tmp_path / "evidence"
    docs.mkdir()
    (docs / "req.md").write_text(
        "The verification method is specified for each requirement.", encoding="utf-8")
    assert s.ingest_documents(str(docs))["status"] == "ok"
    prod_run = s.detect_gaps()["run_id"]
    assert s.set_policy("enabled", True, rationale="on")["status"] == "ok"
    assert s.set_policy("canary_enabled", True, rationale="on")["status"] == "ok"

    _, auto = s._publish_and_gate_proposal(
        _onto([("rv", "Requirement Verification",
                ["verification_method", "quantum_safety_audit"])]),
        "test", "p", "e", 1)
    assert auto["canary"] == "failed"          # quantum char has no evidence
    # the baseline was a fresh pre-adoption experiment, not the stale
    # production run
    assert auto["baseline_run"] and auto["baseline_run"] != prod_run


def test_canary_treats_renamed_gap_as_carryover(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    # v1 outcome has an uncovered characteristic -> a standing gap
    _adopt(s, _onto([("rv", "Requirement Verification", ["quantum_x"])]))
    assert s.set_policy("enabled", True, rationale="on")["status"] == "ok"
    assert s.set_policy("canary_enabled", True, rationale="on")["status"] == "ok"

    # pure rename: new id + name, identical characteristic set
    _, auto = s._publish_and_gate_proposal(
        _onto([("rv2", "Requirement Checking", ["quantum_x"])]),
        "test", "p", "e", 1)
    assert auto["adopted"] is True             # churn: rename not counted
    assert auto["canary"] == "passed"          # the gap moved, none are new
    assert auto["new_gaps"] == 0
    assert s._state.ontology.outcome("rv2") is not None


# ---- fix 3: restart rehydration ------------------------------------------------

def test_restart_rehydrates_active_ontology(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    yaml_text = _onto([("rv", "Requirement Verification", ["verification_method"])])
    vh = s._get_workspace().publish_ontology(yaml_text, metadata={}).hash
    assert s.adopt_proposed_ontology(vh, rationale="pre-restart")["status"] == "ok"

    import grap_mcp.server as smod
    s2 = importlib.reload(smod)                # simulated restart
    assert s2._state.ontology is None
    s2.get_status()                            # first workspace open
    assert s2._state.ontology is not None
    assert s2._state.ontology_version is not None
    assert s2._state.ontology.outcome("rv") is not None


def test_rehydration_follows_rollback(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    h1 = _adopt(s, _onto([("rv", "Requirement Verification", ["verification_method"])]))
    _adopt(s, _onto([("qa", "Quality Assurance", ["quality_plan"])]))
    assert s.rollback_ontology(h1, reason="prefer v1")["status"] == "ok"

    import grap_mcp.server as smod
    s2 = importlib.reload(smod)
    s2.get_status()
    assert s2._state.ontology_version == h1    # latest event wins (rollback)
    assert s2._state.ontology.outcome("rv") is not None


# ---- fix 4: PG write-back -------------------------------------------------------

PG_V1 = """\
name: P
version: 0.1.0
steps:
  - id: s1
    operation: verify
    references: ["kg:aspice.process_outcome.rv"]
"""

DRAFT_OK = """\
name: P
version: 0.1.1
steps:
  - id: s1
    operation: verify
    references: ["kg:aspice.process_outcome.rv"]
  - id: s2
    operation: assure
    references: ["kg:aspice.process_outcome.qa"]
"""


def _pg_setup(tmp_path, monkeypatch, write_back):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    _adopt(s, _onto([("rv", "Requirement Verification", ["verification_method"]),
                     ("qa", "Quality Assurance", ["quality_plan"])]))
    pgf = tmp_path / "p.pg.yaml"
    pgf.write_text(PG_V1, encoding="utf-8")
    assert s.load_procedure_graph(str(pgf))["status"] == "ok"
    monkeypatch.setattr(s, "get_provider",
                        lambda model=None: StubProvider(default=DRAFT_OK))
    assert s.set_policy("pg_autopromote_enabled", True, rationale="on")["status"] == "ok"
    if write_back:
        assert s.set_policy("pg_write_back", True, rationale="sync file")["status"] == "ok"
    return s, pgf


def test_write_back_syncs_pg_file_and_state(tmp_path, monkeypatch):
    s, pgf = _pg_setup(tmp_path, monkeypatch, write_back=True)
    out = s.draft_pg_patch()
    assert out["auto_promoted"] is True and out["written_back"] is True
    assert "s2" in pgf.read_text(encoding="utf-8")           # file tracks head
    assert any(st.id == "s2" for st in s._state.pg.steps)    # state reloaded
    # next draft is a no-op: the file already covers the ontology
    assert "already covers" in s.draft_pg_patch()["note"]


def test_without_write_back_file_stays_untouched(tmp_path, monkeypatch):
    s, pgf = _pg_setup(tmp_path, monkeypatch, write_back=False)
    out = s.draft_pg_patch()
    assert out["auto_promoted"] is True and out["written_back"] is False
    assert "s2" not in pgf.read_text(encoding="utf-8")


def test_set_policy_write_back_bool_cast(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    out = s.set_policy("pg_write_back", 1, rationale="cast")
    assert out["adoption_policy"]["pg_write_back"] is True


# --- canary discriminative power (2026-07-24 review) -------------------------
# The canary counted every new gap target, including gaps on outcomes that did
# not exist before. Because outcome ids are content-derived, growth swamped
# regression and no budget could discriminate. These pin the partition.

class _FakeWS:
    """Minimal workspace: get_record returns a stored ontology yaml."""
    def __init__(self, records):
        self._records = records

    def get_record(self, h):
        return self._records.get(h)


def test_canary_ignores_gaps_on_newly_added_outcomes(monkeypatch):
    import grap_mcp.server as s
    prev_yaml = _onto([("existing_a", "A", ["c1"]), ("existing_b", "B", ["c2"])])
    ws = _FakeWS({"prevhash": prev_yaml})

    # diff says: 3 gaps on brand-new outcomes (growth) + 1 on a pre-existing one
    monkeypatch.setattr(s, "diff_runs", lambda a, b: {
        "status": "ok",
        "new_gaps": ["brand_new_1:x", "brand_new_2:y", "brand_new_3:z",
                     "existing_a:c1"],
        "resolved_gaps": [], "counts": {}})
    monkeypatch.setattr(s, "_run_gap_detection",
                        lambda *a, **k: {"status": "ok", "run_id": "run-canary"})

    c = s._canary_check(ws, "newhash", baseline_run_id="run-base",
                        prev_version="prevhash")
    assert c["raw_new_gaps"] == 4          # what the old canary would have gated on
    assert c["growth_gaps"] == 3           # expected: new outcomes lack evidence
    assert c["new_gaps"] == 1              # the only real regression
    assert c["regressions"] == 1


def test_canary_still_catches_a_real_regression(monkeypatch):
    """A batch that adds nothing but breaks existing coverage must be caught."""
    import grap_mcp.server as s
    prev_yaml = _onto([("existing_a", "A", ["c1"]), ("existing_b", "B", ["c2"])])
    ws = _FakeWS({"prevhash": prev_yaml})
    monkeypatch.setattr(s, "diff_runs", lambda a, b: {
        "status": "ok",
        "new_gaps": ["existing_a:c1", "existing_b:c2"],
        "resolved_gaps": [], "counts": {}})
    monkeypatch.setattr(s, "_run_gap_detection",
                        lambda *a, **k: {"status": "ok", "run_id": "run-canary"})

    c = s._canary_check(ws, "newhash", baseline_run_id="run-base",
                        prev_version="prevhash")
    assert c["new_gaps"] == 2 and c["growth_gaps"] == 0


def test_canary_falls_back_when_baseline_ontology_unavailable(monkeypatch):
    """No prev ontology -> conservative old behaviour, not a silent pass."""
    import grap_mcp.server as s
    ws = _FakeWS({})
    monkeypatch.setattr(s, "diff_runs", lambda a, b: {
        "status": "ok", "new_gaps": ["x:1", "y:2"], "resolved_gaps": [], "counts": {}})
    monkeypatch.setattr(s, "_run_gap_detection",
                        lambda *a, **k: {"status": "ok", "run_id": "run-canary"})
    c = s._canary_check(ws, "newhash", baseline_run_id="run-base", prev_version=None)
    assert c["new_gaps"] == 2      # gates on everything rather than nothing


# --- scale-free churn: additions per supporting chunk (2026-07-25) ------------
# The absolute budget compounds: a rejected proposal never becomes the baseline,
# so the next diff includes the rejected delta and the gate stays shut forever.
# The rate budget asks a scale-free question instead — is this proposal anomalous
# relative to the evidence justifying it?

def _onto_prov(entries, chunks_per_outcome):
    """Ontology yaml-as-dict with provenance, for rate checks."""
    outs, prov = [], {}
    for i, (oid, name, chars) in enumerate(entries):
        outs.append({"id": oid, "name": name, "description": name,
                     "required_characteristics": list(chars)})
        prov[oid] = [f"chunk{i}#{k}" for k in range(chunks_per_outcome)]
    return {"version": "v", "process_outcomes": outs, "mapping_hints": [],
            "concept_hashes": {oid: _sha(name) for oid, name, _ in entries},
            "source_chunk_counts": {oid: chunks_per_outcome for oid, _, _ in entries},
            "provenance": prov}


def test_rate_budget_flags_an_anomalous_proposal():
    """Many outcomes from little evidence = anomalous, regardless of totals."""
    base = _onto_prov([("a", "A", ["c"])], 1)
    # 12 new outcomes all from ONE shared chunk -> 12/chunk
    outs = [("a", "A", ["c"])] + [(f"n{i}", f"N{i}", ["c"]) for i in range(12)]
    target = _onto_prov(outs, 1)
    for oid in target["provenance"]:
        target["provenance"][oid] = ["same_chunk#0"]
    pol = {**DEFAULT_ADOPTION_POLICY, "max_outcomes_added": 100,
           "max_outcomes_added_per_chunk": 3.0}
    res = evaluate_proposal_dict(target, base, pol)
    churn = next(c for c in res["checks"] if c["name"] == "churn")
    assert not churn["ok"]
    assert any("anomalous" in str(v) for v in churn["detail"])


def test_rate_budget_passes_proportionate_growth():
    """Same number of additions, but each backed by its own chunk -> 1/chunk."""
    base = _onto_prov([("a", "A", ["c"])], 1)
    outs = [("a", "A", ["c"])] + [(f"n{i}", f"N{i}", ["c"]) for i in range(12)]
    target = _onto_prov(outs, 1)          # distinct chunk per outcome
    pol = {**DEFAULT_ADOPTION_POLICY, "max_outcomes_added": 100,
           "max_outcomes_added_per_chunk": 3.0}
    res = evaluate_proposal_dict(target, base, pol)
    churn = next(c for c in res["checks"] if c["name"] == "churn")
    assert churn["ok"], churn["detail"]


def test_rate_is_scale_free_so_a_backlog_does_not_cascade():
    """Three batches' worth of accumulated additions still passes the rate check,
    because supporting evidence grew with them — this is the cascade fix."""
    base = _onto_prov([("a", "A", ["c"])], 1)
    pol = {**DEFAULT_ADOPTION_POLICY, "max_outcomes_added": 100,
           "max_outcomes_added_per_chunk": 3.0}
    for n in (12, 24, 36):                # backlog growing across rejected batches
        outs = [("a", "A", ["c"])] + [(f"n{i}", f"N{i}", ["c"]) for i in range(n)]
        res = evaluate_proposal_dict(_onto_prov(outs, 1), base, pol)
        churn = next(c for c in res["checks"] if c["name"] == "churn")
        assert churn["ok"], f"n={n}: {churn['detail']}"


def test_blast_radius_stop_says_it_will_not_self_clear():
    """When growth is proportionate but over the absolute budget, the message must
    distinguish it from an anomaly and state the required action."""
    base = _onto_prov([("a", "A", ["c"])], 1)
    outs = [("a", "A", ["c"])] + [(f"n{i}", f"N{i}", ["c"]) for i in range(12)]
    pol = {**DEFAULT_ADOPTION_POLICY, "max_outcomes_added": 5,
           "max_outcomes_added_per_chunk": 3.0}
    res = evaluate_proposal_dict(_onto_prov(outs, 1), base, pol)
    churn = next(c for c in res["checks"] if c["name"] == "churn")
    assert not churn["ok"]
    joined = " ".join(str(v) for v in churn["detail"])
    assert "blast-radius" in joined and "NOT clear on its own" in joined
    assert "anomalous" not in joined       # proportionate: not an anomaly


def test_rate_budget_disabled_by_default_preserves_behaviour():
    base = _onto_prov([("a", "A", ["c"])], 1)
    outs = [("a", "A", ["c"])] + [(f"n{i}", f"N{i}", ["c"]) for i in range(12)]
    target = _onto_prov(outs, 1)
    for oid in target["provenance"]:
        target["provenance"][oid] = ["same_chunk#0"]
    pol = {**DEFAULT_ADOPTION_POLICY, "max_outcomes_added": 100}   # rate = 0.0
    res = evaluate_proposal_dict(target, base, pol)
    churn = next(c for c in res["checks"] if c["name"] == "churn")
    assert churn["ok"]                     # no rate check applied


# --- gap_regression must fire on a NET regression, not any new gap ------------

class _EvWS:
    """Workspace stub exposing only what list_pending_interventions reads."""
    def __init__(self, subject):
        self._subject = subject

    def query_events(self, kind, **kw):
        if kind != "gap_delta":
            return []
        class E:
            pass
        e = E(); e.subject = self._subject; e.ts = 1
        return [e]

    def list_runs(self, *a, **k):
        return []

    def read_audit(self, *a, **k):
        return []


def _gap_alarm_fires(subject, monkeypatch):
    import grap_mcp.server as s
    monkeypatch.setattr(s, "_get_workspace", lambda: _EvWS(subject))
    monkeypatch.setattr(s, "_open_kee_workdir_parked", lambda *a, **k: [], raising=False)
    q = s.list_pending_interventions()
    return any(i.get("kind") == "gap_regression" for i in q.get("pending", []))


def test_net_improvement_is_not_a_gap_regression(monkeypatch):
    """new=3, resolved=6 is a net gain of three and must NOT queue."""
    assert not _gap_alarm_fires("new=3;resolved=6;run=run-x", monkeypatch)


def test_growth_is_not_counted_as_gap_regression(monkeypatch):
    """The real incident: adopting 106 outcomes brought ~300 initially-uncovered
    characteristics, so a healthy cycle reported new=713 and queued. Gaps on
    outcomes that did not exist at the baseline are GROWTH, not regression."""
    # 713 new, but 700 of them are on outcomes that did not exist before
    assert not _gap_alarm_fires("new=713;resolved=165;growth=700;run=run-x",
                                monkeypatch)
    # strip the growth term and the same event fires (the old behaviour)
    assert _gap_alarm_fires("new=713;resolved=165;run=run-x", monkeypatch)


def test_real_regression_survives_the_growth_partition(monkeypatch):
    """Growth must not become a loophole: genuine regressions still fire."""
    assert _gap_alarm_fires("new=713;resolved=100;growth=300;run=run-x",
                            monkeypatch)


def test_net_regression_still_fires(monkeypatch):
    assert _gap_alarm_fires("new=9;resolved=2;run=run-x", monkeypatch)


def test_equal_new_and_resolved_is_not_a_regression(monkeypatch):
    assert not _gap_alarm_fires("new=4;resolved=4;run=run-x", monkeypatch)
