"""Per-handle native-call serialization, independent of Python's GIL.

This does not serialize separate graph handles or multi-call application edits.
"""
from functools import wraps
from threading import RLock


class SerializedGraph:
    def __init__(self, graph):
        self._graph = graph
        self._lock = RLock()

    def __getattr__(self, name):
        attribute = getattr(self._graph, name)
        if not callable(attribute):
            return attribute
        lock = self._lock

        @wraps(attribute)
        def call(*args, **kwargs):
            # Python lock acquisition releases the GIL while waiting. Lock only
            # one native call, not embedding requests or an entire ingestion job.
            with lock:
                return attribute(*args, **kwargs)
        # Avoid per-node wrapper construction, and do not capture self: a
        # self-cycle would delay closing native mmap/file handles on Windows.
        setattr(self, name, call)
        return call


def add_node_batch(graph, rows):
    """One bounded native insertion, with compatibility for scalar-only adapters."""
    method = getattr(graph, "add_nodes", None)
    if method is not None:
        return method(rows)
    return [graph.add_node(text=text, label=label, **({"embedding": embedding}
            if embedding is not None else {})) for text, label, embedding in rows]
