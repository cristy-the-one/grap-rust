"""Pure adoption-policy logic: diffs and the evaluate matrix (no workspace)."""
import hashlib

from grap_mcp.governance import (DEFAULT_ADOPTION_POLICY, diff_ontology_dicts,
                                 evaluate_proposal_dict, merged_policy)


def _outcome(oid, name, chars, chash=True):
    return {"id": oid, "name": name, "description": name,
            "required_characteristics": list(chars)}


def _proposal(outcomes, with_hashes=True, chunk_counts=None):
    p = {"version": "kee-candidate", "process_outcomes": outcomes,
         "mapping_hints": []}
    if with_hashes:
        p["concept_hashes"] = {
            o["id"]: hashlib.sha256(o["name"].encode("utf-8")).hexdigest()
            for o in outcomes}
    if chunk_counts is not None:
        p["source_chunk_counts"] = chunk_counts
    return p


def _policy(**over):
    return {**DEFAULT_ADOPTION_POLICY, "enabled": True, **over}


def test_merged_policy_defaults_and_override():
    assert merged_policy({}) == DEFAULT_ADOPTION_POLICY
    got = merged_policy({"adoption_policy": {"enabled": True}})
    assert got["enabled"] is True
    assert got["max_outcomes_removed"] == 0    # default retained


def test_diff_ontology_dicts():
    base = _proposal([_outcome("a", "A", ["x", "y"]), _outcome("b", "B", ["z"])],
                     with_hashes=False)
    target = _proposal([_outcome("a", "A2", ["x"]), _outcome("c", "C", ["w"])],
                       with_hashes=False)
    d = diff_ontology_dicts(base, target)
    assert d["outcomes_added"] == ["c"]
    assert d["outcomes_removed"] == ["b"]
    assert d["outcomes_changed"][0]["id"] == "a"
    assert d["outcomes_changed"][0]["name_changed"] is True
    assert d["outcomes_changed"][0]["characteristics_removed"] == ["y"]
    # y (changed on a) + z (removed with b)
    assert d["characteristics_removed"] == 2
    assert d["characteristics_added"] == 0


def test_evaluate_bootstrap_passes_without_active():
    prop = _proposal([_outcome("a", "A", ["x"])], chunk_counts={"a": 2})
    v = evaluate_proposal_dict(prop, None, _policy())
    assert v["pass"] is True
    assert {c["name"] for c in v["checks"]} == {"integrity", "validity", "churn", "provenance"}


def test_evaluate_fails_on_tampered_hash():
    prop = _proposal([_outcome("a", "A", ["x"])], chunk_counts={"a": 2})
    prop["concept_hashes"]["a"] = "0" * 64
    v = evaluate_proposal_dict(prop, None, _policy())
    assert v["pass"] is False
    assert next(c for c in v["checks"] if c["name"] == "integrity")["ok"] is False


def test_evaluate_fails_on_churn_budget():
    active = _proposal([_outcome("a", "A", ["x"])], with_hashes=False)
    prop = _proposal([_outcome("b", "B", ["y"])], chunk_counts={"b": 2})  # removes a
    v = evaluate_proposal_dict(prop, active, _policy())  # max_outcomes_removed=0
    churn = next(c for c in v["checks"] if c["name"] == "churn")
    assert v["pass"] is False and churn["ok"] is False


def test_evaluate_fails_on_pg_referenced_removal_even_with_budget():
    active = _proposal([_outcome("a", "A", ["x"]), _outcome("b", "B", ["y"])],
                       with_hashes=False)
    prop = _proposal([_outcome("b", "B", ["y"])], chunk_counts={"b": 2})
    v = evaluate_proposal_dict(prop, active,
                               _policy(max_outcomes_removed=5),
                               pg_referenced_ids={"a"})
    churn = next(c for c in v["checks"] if c["name"] == "churn")
    assert v["pass"] is False and churn["ok"] is False
    assert "referenced by the active PG" in str(churn["detail"])


def test_evaluate_provenance_floor():
    prop = _proposal([_outcome("a", "A", ["x"])], chunk_counts={"a": 0})
    v = evaluate_proposal_dict(prop, None, _policy(min_source_chunks=1))
    prov = next(c for c in v["checks"] if c["name"] == "provenance")
    assert v["pass"] is False and prov["ok"] is False

    # missing counts entirely is also a failure when the floor is active…
    prop2 = _proposal([_outcome("a", "A", ["x"])])
    v2 = evaluate_proposal_dict(prop2, None, _policy(min_source_chunks=1))
    assert v2["pass"] is False

    # …but a floor of 0 disables the check
    v3 = evaluate_proposal_dict(prop2, None, _policy(min_source_chunks=0))
    assert v3["pass"] is True


def test_evaluate_fails_on_invalid_ontology():
    prop = _proposal([_outcome("", "", ["x"])], with_hashes=False,
                     chunk_counts={})
    v = evaluate_proposal_dict(prop, None, _policy(min_source_chunks=0))
    validity = next(c for c in v["checks"] if c["name"] == "validity")
    assert v["pass"] is False and validity["ok"] is False
