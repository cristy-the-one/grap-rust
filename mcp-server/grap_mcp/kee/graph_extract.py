"""Phase 1: the budgeted, graph-augmented extraction loop.

Single streaming pass: for each chunk, retrieve the relevant known subgraph,
extract with that context, upsert (resolve-or-create + ground relations), and
spend a bounded re-look only when the graph flags a conflict/correlation.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from . import extract
from .. import incremental
from .. import session
from .pipeline import read_chunks
from .working_graph import WorkingGraph

logger = logging.getLogger("grap-mcp.kee")


def _extract_with_context(provider, source: str, text: str, context) -> Optional[dict]:
    """One augmented LLM call -> normalized fragment (or None if unparseable)."""
    user = extract.build_user_prompt_with_context(text, context)
    try:
        raw = provider.complete(extract.SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001 - provider/network errors are non-fatal
        logger.warning("graph_extract: provider failed on %s: %s", source, exc)
        return None
    obj = extract.extract_json(raw)
    if obj is None:
        logger.warning("graph_extract: no JSON parsed for %s", source)
        return None
    frag = extract.normalize_fragment(obj, source)
    if not (frag["outcomes"] or frag["relations"]):
        return None
    return frag


def _extract_windows(wg, chunks: List[Tuple[str, str]], provider, concurrency: int,
                     on_chunk: Optional[Callable[[int, str], None]] = None,
                     ) -> Iterator[Tuple[List[Tuple[str, str]], List[Optional[dict]]]]:
    """Yield (window, fragments) over `chunks` in windows of `concurrency`.

    Each window snapshots its contexts from the current graph, runs the LLM
    calls concurrently, and returns fragments in chunk order so the caller can
    upsert deterministically. The caller upserts between windows, so the next
    window's contexts see everything earlier windows contributed. At
    concurrency=1 this is exactly the original sequential loop (no thread pool).
    """
    width = max(1, int(concurrency))
    if width == 1:
        for i, (source, text) in enumerate(chunks):
            if on_chunk is not None:
                on_chunk(i, source)
            ctx = wg.context_for(text)
            yield [(source, text)], [_extract_with_context(provider, source, text, ctx)]
        return
    with ThreadPoolExecutor(max_workers=width) as pool:
        for start in range(0, len(chunks), width):
            window = chunks[start:start + width]
            for j, (source, _text) in enumerate(window):
                if on_chunk is not None:
                    on_chunk(start + j, source)
            ctxs = [wg.context_for(text) for (_source, text) in window]
            futs = [pool.submit(_extract_with_context, provider, source, text, ctx)
                    for (source, text), ctx in zip(window, ctxs)]
            yield window, [f.result() for f in futs]


def graph_extract_ontology(chunks: Sequence[Tuple[str, str]], provider, embedder, *,
                           sim: float = 0.86, version: str = "kee-candidate",
                           relook_ratio: float = 0.2, context_cap: int = 8,
                           workdir: Optional[str] = None,
                           concurrency: int = 1) -> Dict[str, Any]:
    budget = math.ceil(relook_ratio * len(chunks)) if chunks else 0
    relooks = 0

    with WorkingGraph(embedder, merge_threshold=sim, context_cap=context_cap,
                      workdir=workdir) as wg:
        for window, frags in _extract_windows(wg, list(chunks), provider, concurrency):
            for (source, text), frag in zip(window, frags):
                if frag is None:
                    continue
                sig = wg.upsert_fragment(frag, source)
                if sig.warrants_relook() and relooks < budget:
                    relooks += 1
                    fctx = wg.focused_context(sig)
                    frag2 = _extract_with_context(provider, source, text, fctx)
                    if frag2 is not None:
                        wg.upsert_fragment(frag2, source)

        # Materialize the result before the context manager closes the sqlite
        # handle / removes an owned temp dir (the dict holds no live references).
        return {
            "ontology": wg.to_candidate_ontology(version),
            "chunks": len(chunks),
            "relooks": relooks,
            "parked": len(wg.parked),
        }


def extend_graph_ontology(workdir: str, docs_path: str, provider, embedder, *,
                          sim: float = 0.86, version: str = "kee-candidate",
                          relook_ratio: float = 0.2, context_cap: int = 8,
                          max_files: Optional[int] = None, chunk_chars: int = 800,
                          progress: Optional[Callable[[dict], None]] = None,
                          concurrency: int = 1) -> Dict[str, Any]:
    """Open the durable graph at `workdir`, sync it to `docs_path`, save, return stats.

    new chunk -> extract; changed chunk (hash differs) -> retract old + re-extract;
    removed chunk -> retract. Unchanged chunks are never sent to the LLM.

    `chunk_chars` is the cost lever for large corpora (bigger chunks -> fewer
    LLM calls). NOTE: chunk ids embed the chunk index, so changing it on an
    existing workdir re-chunks everything and triggers a full re-extract.

    `concurrency` > 1 runs windows of N LLM calls in parallel (e.g. against an
    Ollama server with OLLAMA_NUM_PARALLEL); each window's contexts are
    snapshotted before dispatch and results are upserted in chunk order, so
    chunks within a window don't see each other's results. Default 1 keeps the
    fully sequential semantics.
    """
    wg = WorkingGraph.open(workdir, embedder, merge_threshold=sim, context_cap=context_cap)
    try:
        def _emit(ev):
            if progress is not None:
                progress(ev)

        chunks = read_chunks(docs_path, max_files=max_files, chunk_chars=chunk_chars)
        current = {src: text for (src, text) in chunks}
        hashes = {src: incremental.chunk_hash(text) for src, text in current.items()}
        prev = dict(wg.manifest)

        new = [s for s in current if s not in prev]
        changed = [s for s in current if s in prev and prev[s] != hashes[s]]
        removed = [s for s in prev if s not in current]

        for s in changed + removed:
            wg.retract_chunk(s)
        for s in removed:
            wg.manifest.pop(s, None)

        to_extract = [(s, current[s]) for s in new + changed]
        budget = math.ceil(relook_ratio * len(to_extract)) if to_extract else 0
        relooks = 0
        _emit({"phase": "diff", "new": len(new), "changed": len(changed),
               "removed": len(removed)})
        n = len(to_extract)
        for window, frags in _extract_windows(
                wg, to_extract, provider, concurrency,
                on_chunk=lambda i, source: _emit(
                    {"phase": "chunk", "i": i + 1, "n": n, "source": source})):
            for (source, text), frag in zip(window, frags):
                if frag is None:
                    continue                 # provider error/empty: do NOT record hash -> retried next run
                sig = wg.upsert_fragment(frag, source)
                wg.manifest[source] = hashes[source]
                if sig.warrants_relook() and relooks < budget:
                    relooks += 1
                    frag2 = _extract_with_context(provider, source, text, wg.focused_context(sig))
                    if frag2 is not None:
                        wg.upsert_fragment(frag2, source)

        ontology = wg.to_candidate_ontology(version)
        wg.save()
        _emit({"phase": "done", "added": len(new), "changed": len(changed),
               "removed": len(removed), "concepts": len(wg._live_concepts()),
               "edges": len(wg.relations), "relooks": relooks, "parked": len(wg.parked)})
        session.record_query(f"graph_extract:{len(to_extract)}", len(wg._live_concepts()))
        return {
            "ontology": ontology,
            "added": len(new), "changed": len(changed), "removed": len(removed),
            "concepts": len(wg._live_concepts()), "edges": len(wg.relations),
            "relooks": relooks, "parked": len(wg.parked),
        }
    finally:
        wg.close()
