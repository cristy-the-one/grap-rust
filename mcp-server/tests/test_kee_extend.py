"""Phase 2: the extend (sync-with-retraction) orchestration."""

import json
import pygrap  # noqa: F401

from grap_mcp.kee.graph_extract import extend_graph_ontology
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.embeddings import HashingEmbedder


def _frag(outcomes, relations=None):
    return json.dumps({"outcomes": outcomes, "relations": relations or []})


def _handler(system, user):
    if "alpha" in user:
        return _frag([{"name": "Alpha", "characteristics": []}])
    if "beta" in user:
        return _frag([{"name": "Beta", "characteristics": []}])
    if "gamma" in user:
        return _frag([{"name": "Gamma", "characteristics": []}])
    return "{}"


def test_extend_new_then_unchanged_skips_llm(tmp_path):
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")

    p1 = StubProvider(handler=_handler)
    out1 = extend_graph_ontology(wd, str(docs), p1, HashingEmbedder(), sim=0.9)
    assert out1["added"] == 1 and out1["removed"] == 0
    assert any(o["name"] == "Alpha" for o in out1["ontology"]["process_outcomes"])

    p2 = StubProvider(handler=_handler)
    out2 = extend_graph_ontology(wd, str(docs), p2, HashingEmbedder(), sim=0.9)
    assert out2["added"] == 0 and out2["changed"] == 0 and out2["removed"] == 0
    assert len(p2.calls) == 0
    assert any(o["name"] == "Alpha" for o in out2["ontology"]["process_outcomes"])


def test_extend_changed_and_removed(tmp_path):
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")
    (docs / "b.txt").write_text("beta topic", encoding="utf-8")

    extend_graph_ontology(wd, str(docs), StubProvider(handler=_handler), HashingEmbedder(), sim=0.9)

    (docs / "a.txt").write_text("gamma topic", encoding="utf-8")
    (docs / "b.txt").unlink()
    out = extend_graph_ontology(wd, str(docs), StubProvider(handler=_handler), HashingEmbedder(), sim=0.9)

    names = {o["name"] for o in out["ontology"]["process_outcomes"]}
    assert names == {"Gamma"}
    assert out["changed"] == 1 and out["removed"] == 1


def test_extend_honors_chunk_chars(tmp_path):
    # bigger chunks -> fewer chunks -> fewer LLM calls (the cost lever for
    # large corpora); default stays 800
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic " * 200, encoding="utf-8")  # ~2.4K chars

    p_small = StubProvider(handler=_handler)
    out_small = extend_graph_ontology(tmp_path.joinpath("g-small").as_posix(),
                                      str(docs), p_small, HashingEmbedder(), sim=0.9)
    assert out_small["added"] > 1                  # default 800-char chunks

    p_big = StubProvider(handler=_handler)
    out_big = extend_graph_ontology(tmp_path.joinpath("g-big").as_posix(),
                                    str(docs), p_big, HashingEmbedder(), sim=0.9,
                                    chunk_chars=10_000)
    assert out_big["added"] == 1                   # whole doc in one chunk
    assert len(p_big.calls) < len(p_small.calls)


def test_server_lifecycle_tools(tmp_path, monkeypatch):
    import grap_mcp.server as s
    from grap_mcp.kee.llm import StubProvider
    from grap_mcp.kee.embeddings import HashingEmbedder

    monkeypatch.setattr(s, "get_provider", lambda model=None: StubProvider(handler=_handler))
    monkeypatch.setattr(s, "get_embedder", lambda: HashingEmbedder())

    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")

    ext = s.extend_extraction_graph(wd, str(docs), sim=0.9)
    assert ext["added"] == 1

    opened = s.open_extraction_graph(wd)
    assert opened["concepts"] == 1
    assert s._state.get("kg") is not None

    exported = s.export_ontology(wd)
    assert any(o["name"] == "Alpha" for o in exported["ontology"]["process_outcomes"])


def test_extend_emits_progress_events(tmp_path):
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")

    events = []
    extend_graph_ontology(wd, str(docs), StubProvider(handler=_handler),
                          HashingEmbedder(), sim=0.9,
                          progress=lambda ev: events.append(ev))
    phases = [e.get("phase") for e in events]
    assert "diff" in phases and "done" in phases
    diff = next(e for e in events if e.get("phase") == "diff")
    assert diff["new"] == 1 and diff["changed"] == 0 and diff["removed"] == 0
    assert any(e.get("phase") == "chunk" for e in events)


def test_extend_progress_default_none_is_unchanged(tmp_path):
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")
    out = extend_graph_ontology(wd, str(docs), StubProvider(handler=_handler),
                                HashingEmbedder(), sim=0.9)
    assert out["added"] == 1


def _three_docs(tmp_path):
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")
    (docs / "b.txt").write_text("beta topic", encoding="utf-8")
    (docs / "c.txt").write_text("gamma topic", encoding="utf-8")
    return docs


def test_extend_concurrency_one_matches_default(tmp_path):
    docs = _three_docs(tmp_path)
    out_default = extend_graph_ontology(str(tmp_path / "g1"), str(docs),
                                        StubProvider(handler=_handler),
                                        HashingEmbedder(), sim=0.9)
    out_c1 = extend_graph_ontology(str(tmp_path / "g2"), str(docs),
                                   StubProvider(handler=_handler),
                                   HashingEmbedder(), sim=0.9, concurrency=1)
    assert out_c1 == out_default


def test_extend_concurrency_processes_all_chunks(tmp_path):
    docs = _three_docs(tmp_path)
    p = StubProvider(handler=_handler)
    out = extend_graph_ontology(str(tmp_path / "g"), str(docs), p,
                                HashingEmbedder(), sim=0.9, concurrency=4)
    assert out["added"] == 3
    names = {o["name"] for o in out["ontology"]["process_outcomes"]}
    assert names == {"Alpha", "Beta", "Gamma"}
    assert len(p.calls) == 3


def test_extend_concurrent_upserts_in_chunk_order(tmp_path):
    # The earliest chunk finishes LAST, yet upserts must land in chunk order.
    # process_outcomes is ordered by node id, i.e. by upsert order.
    import time
    delays = {"alpha": 0.2, "beta": 0.1, "gamma": 0.0}

    def slow(system, user):
        for k, d in delays.items():
            if k in user:
                time.sleep(d)
        return _handler(system, user)

    docs = _three_docs(tmp_path)
    out = extend_graph_ontology(str(tmp_path / "g"), str(docs),
                                StubProvider(handler=slow), HashingEmbedder(),
                                sim=0.9, concurrency=4)
    names = [o["name"] for o in out["ontology"]["process_outcomes"]]
    assert names == ["Alpha", "Beta", "Gamma"]


def test_extend_concurrent_progress_in_order(tmp_path):
    docs = _three_docs(tmp_path)
    events = []
    extend_graph_ontology(str(tmp_path / "g"), str(docs),
                          StubProvider(handler=_handler), HashingEmbedder(),
                          sim=0.9, concurrency=2, progress=events.append)
    assert events[0]["phase"] == "diff" and events[-1]["phase"] == "done"
    assert [e["i"] for e in events if e["phase"] == "chunk"] == [1, 2, 3]


def test_server_extend_accepts_concurrency(tmp_path, monkeypatch):
    import grap_mcp.server as s
    monkeypatch.setattr(s, "get_provider", lambda model=None: StubProvider(handler=_handler))
    monkeypatch.setattr(s, "get_embedder", lambda: HashingEmbedder())
    docs = _three_docs(tmp_path)
    ext = s.extend_extraction_graph(str(tmp_path / "g"), str(docs), sim=0.9,
                                    concurrency=2)
    assert ext["status"] == "ok" and ext["added"] == 3


def test_checkpoint_persists_progress_mid_run(tmp_path):
    """A checkpoint must persist the manifest, so an interrupted run resumes
    instead of re-extracting everything from scratch."""
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    for i in range(6):
        (docs / f"d{i}.txt").write_text(f"alpha topic {i}", encoding="utf-8")

    seen = []

    class Boom(RuntimeError):
        pass

    def handler(system, user):
        seen.append(user)
        # die after enough chunks that a checkpoint (every 2) has landed
        if len(seen) == 5:
            raise Boom("simulated crash mid-run")
        return _frag([{"name": f"C{len(seen)}", "characteristics": []}])

    try:
        extend_graph_ontology(wd, str(docs), StubProvider(handler=handler),
                              HashingEmbedder(), sim=0.9, checkpoint_every=2)
    except Boom:
        pass

    # Re-open the durable graph: work before the crash survived.
    from grap_mcp.kee.working_graph import WorkingGraph
    wg = WorkingGraph.open(wd, HashingEmbedder(), merge_threshold=0.9)
    assert len(wg.manifest) >= 2, (
        f"checkpoint should have persisted completed chunks, got {len(wg.manifest)}")
    assert len(wg._live_concepts()) >= 2


def test_checkpoint_disabled_keeps_single_final_save(tmp_path):
    """checkpoint_every=0 preserves the old all-or-nothing behaviour."""
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")
    out = extend_graph_ontology(wd, str(docs), StubProvider(handler=_handler),
                                HashingEmbedder(), sim=0.9, checkpoint_every=0)
    assert out["added"] == 1


# ---- the poison chunk ------------------------------------------------------
#
# A chunk that fails deliberately keeps its hash out of the manifest so the next
# run retries it. Unbounded, that is one wasted generation per batch forever:
# `20300#2` was still being re-sent 750 documents into the law-ro pilot.

def _always_unparseable(system, user):
    return "I cannot produce JSON for this."


def test_poison_chunk_stops_being_retried(tmp_path):
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "bad.txt").write_text("unextractable text", encoding="utf-8")

    calls = []
    for _ in range(4):                       # four consecutive runs
        p = StubProvider(handler=_always_unparseable)
        out = extend_graph_ontology(wd, str(docs), p, HashingEmbedder(),
                                    sim=0.9, max_extract_attempts=3)
        calls.append(len(p.calls))
    # tried on runs 1-3, quarantined at the third, never sent again
    assert calls == [1, 1, 1, 0], calls
    assert [q["chunk_id"] for q in out["quarantined"]] == []      # already quarantined
    assert out["quality"]["quarantined_total"] == 1


def test_provider_outage_never_quarantines(tmp_path):
    """An outage must not dead-letter the corpus: transport errors are exempt."""
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")

    def _down(system, user):
        raise RuntimeError("connection refused")

    for _ in range(5):
        out = extend_graph_ontology(wd, str(docs), StubProvider(handler=_down),
                                    HashingEmbedder(), sim=0.9, max_extract_attempts=3)
        assert out["quality"]["quarantined_total"] == 0

    # the server comes back: the chunk is still pending and extracts normally
    p = StubProvider(handler=_handler)
    out = extend_graph_ontology(wd, str(docs), p, HashingEmbedder(), sim=0.9)
    assert len(p.calls) == 1
    assert any(o["name"] == "Alpha" for o in out["ontology"]["process_outcomes"])
