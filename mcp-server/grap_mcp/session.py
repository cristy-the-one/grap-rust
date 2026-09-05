"""In-process session journal — the MCP's working memory.

Records which KG nodes were touched, what was queried (including zero-result
"negative evidence"), and what was edited, with recency + frequency. Cheap and
always on; not persisted (complements the Workspace audit log).
"""
import threading
import time

class SessionJournal:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._start = time.time(); self._nodes = {}; self._queries = {}; self._edits = {}; self._tool_calls = {}


    def record_node_access(self, node_id: int, tool: str) -> None:
        with self._lock:
            e = self._nodes.setdefault(int(node_id), {"reads": 0, "last_tool": "", "last_ts": 0.0})
            e["reads"] += 1; e["last_tool"] = tool; e["last_ts"] = time.time()
            self._tool_calls[tool] = self._tool_calls.get(tool, 0) + 1


    def record_query(self, query: str, result_count: int) -> None:
        with self._lock:
            e = self._queries.setdefault(query, {"count": 0, "result_count": 0, "last_ts": 0.0})
            e["count"] += 1; e["result_count"] = int(result_count); e["last_ts"] = time.time()


    def record_edit(self, target: str) -> None:
        with self._lock:
            e = self._edits.setdefault(target, {"edits": 0, "last_ts": 0.0})
            e["edits"] += 1; e["last_ts"] = time.time()


    def get_context(self, max_nodes: int = 50, max_queries: int = 20, max_edits: int = 20,
                    sort_by: str = "recency") -> dict:
        with self._lock:
            if sort_by == "frequency":
                nkey = lambda kv: kv[1]["reads"]; qkey = lambda kv: kv[1]["count"]; ekey = lambda kv: kv[1]["edits"]
            else:
                nkey = qkey = ekey = lambda kv: kv[1]["last_ts"]
            nodes = sorted(self._nodes.items(), key=nkey, reverse=True)[:max_nodes]
            queries = sorted(self._queries.items(), key=qkey, reverse=True)[:max_queries]
            edits = sorted(self._edits.items(), key=ekey, reverse=True)[:max_edits]
            return {
                "session_duration_s": round(time.time() - self._start, 2),
                "nodes_accessed": [{"node": nid, "reads": d["reads"], "last_tool": d["last_tool"]} for nid, d in nodes],
                "recent_queries": [{"query": q, "count": d["count"], "result_count": d["result_count"],
                                    "negative_evidence": d["result_count"] == 0} for q, d in queries],
                "edits": [{"target": t, "edits": d["edits"]} for t, d in edits],
                "tool_calls": dict(self._tool_calls),
                "total_unique_nodes": len(self._nodes),
                "total_unique_queries": len(self._queries),
            }

_default = SessionJournal()
reset = _default.reset
record_node_access = _default.record_node_access
record_query = _default.record_query
record_edit = _default.record_edit
get_context = _default.get_context
