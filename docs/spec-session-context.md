# Session Journal + Context Bundle (P1-F) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give the MCP a working memory — a session journal of what nodes were touched and what was queried (incl. negative evidence) — and a `get_context_bundle` tool that assembles a token-budgeted pack of the most relevant KG nodes for a query. That bundle *is* the Procedure-Graph "fetch relevant KG context" step, made explicit and reusable.

**Architecture:** Pure MCP/Python skin. An in-process `session.py` journal (cheap, always-on, no disk) records node accesses (read count + last tool + recency), queries (count + result_count, where `result_count == 0` is surfaced as negative evidence), and edits. `get_context_bundle` calls the KG's hybrid search (`kg.search_and_traverse_hybrid`, which returns `QueryResult{node, text, fused_score, ...}` already sorted by fused score), greedily packs results under a token budget (≈`len(text)//4` tokens, the same heuristic the wire encoder uses), records the query, and returns the bundle. No core changes.

**Tech Stack:** Python (`grap_mcp`), `pytest`. Reuses pygrap `search_and_traverse_hybrid`, `get_node_label`.

**Scope discipline:** The journal is in-process per server run (not persisted — it complements, not replaces, the Workspace audit log). Recording is hooked into a couple of high-signal tools (`graph_neighbors` touches; `get_context_bundle` query), not all ~40 (FastMCP has no central dispatch). Cross-linking journal touches into the audit log is a noted follow-up.

---

## File Structure
- **Create** `mcp-server/grap_mcp/session.py` — the journal.
- **Modify** `mcp-server/grap_mcp/server.py` — `get_session_context` + `get_context_bundle` tools; record touches in `graph_neighbors`; capabilities registration.
- **Tests** `mcp-server/tests/test_session.py`.

---

## Task 1: `session.py` journal

**Files:** Create `mcp-server/grap_mcp/session.py`. Test: `mcp-server/tests/test_session.py`.

- [ ] **Step 1: Failing test** — `mcp-server/tests/test_session.py`:
```python
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
    # node 7 read twice
    n7 = next(n for n in ctx["nodes_accessed"] if n["node"] == 7)
    assert n7["reads"] == 2 and n7["last_tool"] == "get_context_bundle"
    assert ctx["total_unique_nodes"] == 2
    # negative evidence flagged
    neg = [q for q in ctx["recent_queries"] if q["negative_evidence"]]
    assert any(q["query"] == "no such thing" for q in neg)
    assert ctx["total_unique_queries"] == 2
    assert ctx["edits"][0]["target"] == "doc:spec#ab12"

def test_frequency_sort(tmp_path):
    sess.reset()
    sess.record_node_access(1, "t"); sess.record_node_access(1, "t")
    sess.record_node_access(2, "t")
    ctx = sess.get_context(sort_by="frequency")
    assert ctx["nodes_accessed"][0]["node"] == 1  # most-read first
```

- [ ] **Step 2: Run, verify FAIL** — `cd mcp-server && python -m pytest tests/test_session.py -q` → module missing.

- [ ] **Step 3: Implement** `mcp-server/grap_mcp/session.py`:
```python
"""In-process session journal — the MCP's working memory.

Records which KG nodes were touched, what was queried (including zero-result
"negative evidence"), and what was edited, with recency + frequency. Cheap and
always on; not persisted (it complements the Workspace audit log). Powers
get_session_context and feeds get_context_bundle.
"""
import threading
import time

_lock = threading.Lock()
_start = time.time()
_nodes: dict[int, dict] = {}      # node_id -> {reads, last_tool, last_ts}
_queries: dict[str, dict] = {}    # query   -> {count, result_count, last_ts}
_edits: dict[str, dict] = {}      # target  -> {edits, last_ts}
_tool_calls: dict[str, int] = {}


def reset() -> None:
    global _start, _nodes, _queries, _edits, _tool_calls
    with _lock:
        _start = time.time()
        _nodes = {}
        _queries = {}
        _edits = {}
        _tool_calls = {}


def record_node_access(node_id: int, tool: str) -> None:
    with _lock:
        e = _nodes.setdefault(int(node_id), {"reads": 0, "last_tool": "", "last_ts": 0.0})
        e["reads"] += 1
        e["last_tool"] = tool
        e["last_ts"] = time.time()
        _tool_calls[tool] = _tool_calls.get(tool, 0) + 1


def record_query(query: str, result_count: int) -> None:
    with _lock:
        e = _queries.setdefault(query, {"count": 0, "result_count": 0, "last_ts": 0.0})
        e["count"] += 1
        e["result_count"] = int(result_count)
        e["last_ts"] = time.time()


def record_edit(target: str) -> None:
    with _lock:
        e = _edits.setdefault(target, {"edits": 0, "last_ts": 0.0})
        e["edits"] += 1
        e["last_ts"] = time.time()


def get_context(max_nodes: int = 50, max_queries: int = 20, max_edits: int = 20,
                sort_by: str = "recency") -> dict:
    """Summarize the session. sort_by = 'recency' (last_ts) or 'frequency' (counts)."""
    with _lock:
        if sort_by == "frequency":
            nkey = lambda kv: kv[1]["reads"]
            qkey = lambda kv: kv[1]["count"]
            ekey = lambda kv: kv[1]["edits"]
        else:
            nkey = qkey = ekey = lambda kv: kv[1]["last_ts"]
        nodes = sorted(_nodes.items(), key=nkey, reverse=True)[:max_nodes]
        queries = sorted(_queries.items(), key=qkey, reverse=True)[:max_queries]
        edits = sorted(_edits.items(), key=ekey, reverse=True)[:max_edits]
        return {
            "session_duration_s": round(time.time() - _start, 2),
            "nodes_accessed": [
                {"node": nid, "reads": d["reads"], "last_tool": d["last_tool"]}
                for nid, d in nodes
            ],
            "recent_queries": [
                {"query": q, "count": d["count"], "result_count": d["result_count"],
                 "negative_evidence": d["result_count"] == 0}
                for q, d in queries
            ],
            "edits": [{"target": t, "edits": d["edits"]} for t, d in edits],
            "tool_calls": dict(_tool_calls),
            "total_unique_nodes": len(_nodes),
            "total_unique_queries": len(_queries),
        }
```

- [ ] **Step 4: Run, verify PASS** — `cd mcp-server && python -m pytest tests/test_session.py -q`.

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/session.py mcp-server/tests/test_session.py
git commit -m "feat(mcp): in-process session journal (node touches, queries, negative evidence)"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: `get_session_context` tool + hook `graph_neighbors`

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: extend `test_session.py`.

- [ ] **Step 1: Failing test** — append:
```python
import grap_mcp.server as s

def test_get_session_context_tool(monkeypatch):
    sess.reset()
    sess.record_query("q", result_count=1)
    out = s.get_session_context()
    assert out["status"] == "ok"
    assert out["total_unique_queries"] == 1
```

- [ ] **Step 2: Run, verify FAIL** — tool undefined.

- [ ] **Step 3: Implement** — add `get_session_context` to `server.py` (read-role; no KG needed):
```python
@mcp.tool()
def get_session_context(sort_by: str = "recency") -> dict:
    """The MCP's working memory: recently/frequently touched nodes, recent
    queries (with negative evidence), and edits this session."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        from grap_mcp import session
        ctx = session.get_context(sort_by=sort_by)
        ctx["status"] = "ok"
        return ctx
    except Exception as e:
        return {"status": "error", "message": str(e)}
```
   Then hook `graph_neighbors` (~line 522) to record each touched neighbor. Right before its `return {"status": "ok", "center": node_id, "neighbors": neighbors, "edges": edges}`, add (failure-proof):
```python
        try:
            from grap_mcp import session
            session.record_node_access(node_id, "graph_neighbors")
            for nb in neighbors:
                session.record_node_access(nb["id"], "graph_neighbors")
        except Exception:
            pass
```
   Register `get_session_context` in `list_capabilities` (introspection/audit group).

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_session.py -q`; full `python -m pytest -q` (capabilities coverage passes; existing `graph_neighbors` tests still pass — the hook is guarded).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_session.py
git commit -m "feat(mcp): get_session_context tool; record node touches in graph_neighbors"
```

---

## Task 3: `get_context_bundle` — the PG "fetch context" step

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: extend `test_session.py`.

- [ ] **Step 1: Failing test** — append (use a fake KG exposing `search_and_traverse_hybrid` + `get_node_label`):
```python
class _Hit:
    def __init__(self, node, text, score):
        self.node = node; self.text = text; self.fused_score = score

class _BundleKG:
    def search_and_traverse_hybrid(self, query, **kw):
        return [_Hit(1, "alpha " * 50, 0.9), _Hit(2, "beta " * 50, 0.5),
                _Hit(3, "gamma " * 50, 0.2)]
    def get_node_label(self, nid):
        return f"doc:{nid}"

def test_context_bundle_packs_within_budget(monkeypatch):
    sess.reset()
    monkeypatch.setitem(s._state, "kg", _BundleKG())
    # each hit ~ len("alpha "*50)=300 chars => ~75 tokens; budget 120 => ~1 fits
    out = s.get_context_bundle("memory safety", token_budget=120)
    assert out["status"] == "ok"
    assert out["tokens_used"] <= 120
    assert len(out["bundle"]) >= 1
    assert out["bundle"][0]["node"] == 1     # highest fused_score first
    assert out["truncated"] is True          # not all 3 fit
    # query recorded in the journal
    assert any(q["query"] == "memory safety" for q in sess.get_context()["recent_queries"])
```

- [ ] **Step 2: Run, verify FAIL** — tool undefined.

- [ ] **Step 3: Implement** — add to `server.py` (read-tool pattern + KG guard):
```python
@mcp.tool()
def get_context_bundle(query: str, max_results: int = 20, token_budget: int = 2000,
                       min_strength: float = 0.3, max_depth: int = 3) -> dict:
    """Assemble a token-budgeted bundle of the most relevant KG nodes for
    `query` — the Procedure-Graph 'fetch relevant KG context' step. Greedily
    packs hybrid-search hits (highest fused score first) under `token_budget`
    (~4 chars/token) and records the query in the session journal."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.get("kg")
    if not kg:
        return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        from grap_mcp import session
        results = kg.search_and_traverse_hybrid(
            query, max_results=max_results, min_strength=min_strength, max_depth=max_depth
        )
        session.record_query(query, len(results))
        bundle = []
        used = 0
        for r in results:  # already sorted by fused_score desc
            text = getattr(r, "text", "") or ""
            toks = max(1, len(text) // 4)
            if used + toks > token_budget:
                continue  # skip oversized; keep packing smaller relevant hits
            try:
                label = kg.get_node_label(r.node)
            except Exception:
                label = None
            bundle.append({"node": r.node, "label": label,
                           "score": round(getattr(r, "fused_score", 0.0), 4), "text": text})
            try:
                session.record_node_access(r.node, "get_context_bundle")
            except Exception:
                pass
            used += toks
        return {"status": "ok", "query": query, "count": len(bundle),
                "tokens_used": used, "token_budget": token_budget,
                "tokens_remaining": max(0, token_budget - used),
                "truncated": len(bundle) < len(results), "bundle": bundle}
    except Exception as e:
        logger.exception("get_context_bundle failed")
        return {"status": "error", "message": str(e)}
```
   (Confirm `kg.search_and_traverse_hybrid` accepts those keyword args — the pygrap signature has `max_seeds`, `min_strength`, `max_depth`, `max_results` among others with defaults; pass only `max_results`/`min_strength`/`max_depth` as kwargs, which are valid. `get_node_label` returns `Optional[str]`.) Register `get_context_bundle` in `list_capabilities` (knowledge group).

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_session.py -q`; full `python -m pytest -q`.

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_session.py
git commit -m "feat(mcp): get_context_bundle — budget-packed KG context (PG fetch-context step)"
```
(Do NOT add out/, terminals/, mcps/, *.patch, *.png, *.pdf, CLAUDE.md, untracked docs/*.md.)

---

## Self-Review
- **Coverage:** journal (T1), tool + graph_neighbors hook (T2), context bundle (T3). Matches the P1-F architecture; `get_context_bundle` is the explicit PG fetch-context step.
- **Type consistency:** `record_node_access(int,str)`/`record_query(str,int)`/`record_edit(str)`/`get_context(...,sort_by)` consistent T1–T3; bundle uses `QueryResult.node`/`.text`/`.fused_score` (real pygrap fields).
- **Honest scope:** in-process journal (not persisted); recording hooked into graph_neighbors + get_context_bundle (not every tool); negative evidence = zero-result queries.
- **Follow-up (out of scope):** persisting/cross-linking journal touches into the Workspace audit log so they appear in `reconstruct_run`; returning the bundle in compact `#MUNCH` form.
