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
