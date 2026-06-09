"""In-process session journal — the MCP's working memory.

Records which KG nodes were touched, what was queried (including zero-result
"negative evidence"), and what was edited, with recency + frequency. Cheap and
always on; not persisted (complements the Workspace audit log).
"""
import threading
import time

_lock = threading.Lock()
_start = time.time()
_nodes: dict[int, dict] = {}
_queries: dict[str, dict] = {}
_edits: dict[str, dict] = {}
_tool_calls: dict[str, int] = {}


def reset() -> None:
    global _start, _nodes, _queries, _edits, _tool_calls
    with _lock:
        _start = time.time(); _nodes = {}; _queries = {}; _edits = {}; _tool_calls = {}


def record_node_access(node_id: int, tool: str) -> None:
    with _lock:
        e = _nodes.setdefault(int(node_id), {"reads": 0, "last_tool": "", "last_ts": 0.0})
        e["reads"] += 1; e["last_tool"] = tool; e["last_ts"] = time.time()
        _tool_calls[tool] = _tool_calls.get(tool, 0) + 1


def record_query(query: str, result_count: int) -> None:
    with _lock:
        e = _queries.setdefault(query, {"count": 0, "result_count": 0, "last_ts": 0.0})
        e["count"] += 1; e["result_count"] = int(result_count); e["last_ts"] = time.time()


def record_edit(target: str) -> None:
    with _lock:
        e = _edits.setdefault(target, {"edits": 0, "last_ts": 0.0})
        e["edits"] += 1; e["last_ts"] = time.time()


def get_context(max_nodes: int = 50, max_queries: int = 20, max_edits: int = 20,
                sort_by: str = "recency") -> dict:
    with _lock:
        if sort_by == "frequency":
            nkey = lambda kv: kv[1]["reads"]; qkey = lambda kv: kv[1]["count"]; ekey = lambda kv: kv[1]["edits"]
        else:
            nkey = qkey = ekey = lambda kv: kv[1]["last_ts"]
        nodes = sorted(_nodes.items(), key=nkey, reverse=True)[:max_nodes]
        queries = sorted(_queries.items(), key=qkey, reverse=True)[:max_queries]
        edits = sorted(_edits.items(), key=ekey, reverse=True)[:max_edits]
        return {
            "session_duration_s": round(time.time() - _start, 2),
            "nodes_accessed": [{"node": nid, "reads": d["reads"], "last_tool": d["last_tool"]} for nid, d in nodes],
            "recent_queries": [{"query": q, "count": d["count"], "result_count": d["result_count"],
                                "negative_evidence": d["result_count"] == 0} for q, d in queries],
            "edits": [{"target": t, "edits": d["edits"]} for t, d in edits],
            "tool_calls": dict(_tool_calls),
            "total_unique_nodes": len(_nodes),
            "total_unique_queries": len(_queries),
        }
