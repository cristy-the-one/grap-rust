import grap_mcp.session as sess


def test_journal_records_and_summarizes():
    sess.reset()
    sess.record_node_access(7, "graph_neighbors")
    sess.record_node_access(7, "get_context_bundle")
    sess.record_node_access(9, "graph_neighbors")
    sess.record_query("brake redundancy", result_count=3)
    sess.record_query("no such thing", result_count=0)
    sess.record_edit("doc:spec#ab12")
    ctx = sess.get_context()
    n7 = next(n for n in ctx["nodes_accessed"] if n["node"] == 7)
    assert n7["reads"] == 2 and n7["last_tool"] == "get_context_bundle"
    assert ctx["total_unique_nodes"] == 2
    neg = [q for q in ctx["recent_queries"] if q["negative_evidence"]]
    assert any(q["query"] == "no such thing" for q in neg)
    assert ctx["total_unique_queries"] == 2
    assert ctx["edits"][0]["target"] == "doc:spec#ab12"


def test_frequency_sort():
    sess.reset()
    sess.record_node_access(1, "t"); sess.record_node_access(1, "t")
    sess.record_node_access(2, "t")
    ctx = sess.get_context(sort_by="frequency")
    assert ctx["nodes_accessed"][0]["node"] == 1


from grap_mcp.application import GrapApplication
s = GrapApplication()


def test_get_session_context_tool():
    s.session.reset()
    s.session.record_query("q", result_count=1)
    out = s.get_session_context()
    assert out["status"] == "ok"
    assert out["total_unique_queries"] == 1


class _Hit:
    def __init__(self, node, text, score):
        self.node = node; self.text = text; self.fused_score = score

class _BundleKG:
    def search_and_traverse_hybrid(self, query, **kw):
        return [_Hit(1, "alpha " * 50, 0.9), _Hit(2, "beta " * 50, 0.5), _Hit(3, "gamma " * 50, 0.2)]
    def get_node_label(self, nid):
        return f"doc:{nid}"

def test_context_bundle_packs_within_budget(monkeypatch):
    s.session.reset()
    monkeypatch.setitem(s._state, "kg", _BundleKG())
    out = s.get_context_bundle("memory safety", token_budget=120)
    assert out["status"] == "ok"
    assert out["tokens_used"] <= 120
    assert len(out["bundle"]) >= 1
    assert out["bundle"][0]["node"] == 1
    assert out["truncated"] is True
    assert any(q["query"] == "memory safety" for q in s.session.get_context()["recent_queries"])
