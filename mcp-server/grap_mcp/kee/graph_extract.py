"""Phase 1: the budgeted, graph-augmented extraction loop.

Single streaming pass: for each chunk, retrieve the relevant known subgraph,
extract with that context, upsert (resolve-or-create + ground relations), and
spend a bounded re-look only when the graph flags a conflict/correlation.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from . import extract
from .. import incremental
from .. import session
from .pipeline import read_chunks
from .extract_quality import QualityMonitor
from .working_graph import WorkingGraph

logger = logging.getLogger("grap-mcp.kee")


def _extract_with_context(provider, source: str, text: str, context
                          ) -> Tuple[Optional[dict], Optional[str]]:
    """One augmented LLM call -> (fragment, failure reason).

    Returns exactly one of `(frag, None)` or `(None, reason)`. The three ways to
    get nothing back used to be indistinguishable — all of them returned None
    and all of them were counted as "parse failures". They are not the same
    event and they do not deserve the same response:

      "provider" — transport/server error. Transient; always worth retrying, and
                   never grounds for quarantine (an outage would otherwise
                   dead-letter the whole corpus in a few batches).
      "unparsed" — a reply came back and no JSON could be found in it. The model
                   owns this, and it reproduces on the same input.
      "empty"    — valid JSON, nothing extractable in it. Also reproducible, and
                   often simply true (a table of contents has no outcomes).
                   Counting it as a parse failure overstated the failure rate.
    """
    user = extract.build_user_prompt_with_context(text, context)
    try:
        raw = provider.complete(extract.SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001 - provider/network errors are non-fatal
        logger.warning("graph_extract: provider failed on %s: %s", source, exc)
        return None, "provider"
    obj = extract.extract_json(raw)
    if obj is None:
        logger.warning("graph_extract: no JSON parsed for %s", source)
        return None, "unparsed"
    frag = extract.normalize_fragment(obj, source)
    if not (frag["outcomes"] or frag["relations"]):
        return None, "empty"
    return frag, None


def _extract_windows(wg, chunks: List[Tuple[str, str]], provider, concurrency: int,
                     on_chunk: Optional[Callable[[int, str], None]] = None,
                     ) -> Iterator[Tuple[List[Tuple[str, str]],
                                         List[Tuple[Optional[dict], Optional[str]]]]]:
    """Yield (window, results) over `chunks` in windows of `concurrency`.

    Each result is the `(fragment, reason)` pair from `_extract_with_context`.

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
        for window, results in _extract_windows(wg, list(chunks), provider, concurrency):
            for (source, text), (frag, _reason) in zip(window, results):
                if frag is None:
                    continue
                sig = wg.upsert_fragment(frag, source)
                if sig.warrants_relook() and relooks < budget:
                    relooks += 1
                    fctx = wg.focused_context(sig)
                    frag2, _ = _extract_with_context(provider, source, text, fctx)
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
                          concurrency: int = 1,
                          checkpoint_every: int = 25,
                          promote_min_refs: int = 2,
                          promote_max_per_run: int = 25,
                          max_extract_attempts: int = 3) -> Dict[str, Any]:
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

    `max_extract_attempts` bounds how many times a chunk the model cannot
    extract is re-sent across runs before it is quarantined. Transport errors
    are exempt and retry forever, so an outage cannot dead-letter the corpus.
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
        since_ckpt = 0
        quarantined: List[dict] = []
        qm = QualityMonitor(class_vocab=extract.CLASS_VOCAB)
        for window, results in _extract_windows(
                wg, to_extract, provider, concurrency,
                on_chunk=lambda i, source: _emit(
                    {"phase": "chunk", "i": i + 1, "n": n, "source": source})):
            for (source, text), (frag, reason) in zip(window, results):
                if frag is None:
                    qm.record_failure(reason)
                    # Not recording the hash is what makes the retry happen next
                    # run. Keep that for transport errors; bound it for failures
                    # the model reproduces, or the chunk is re-sent every batch
                    # for as long as the corpus lives.
                    if reason != "provider":
                        if wg.record_chunk_failure(source, hashes[source], reason or "unknown",
                                                   max_attempts=max_extract_attempts):
                            wg.manifest[source] = hashes[source]   # stop retrying
                            quarantined.append({"chunk_id": source, "reason": reason})
                            logger.warning(
                                "graph_extract: quarantined %s after %d attempts (%s)",
                                source, max_extract_attempts, reason)
                    continue
                qm.record(frag)
                wg.clear_chunk_failure(source)   # it extracted; it is not a dead letter
                sig = wg.upsert_fragment(frag, source)
                wg.manifest[source] = hashes[source]
                since_ckpt += 1
                if sig.warrants_relook() and relooks < budget:
                    relooks += 1
                    frag2, _ = _extract_with_context(provider, source, text, wg.focused_context(sig))
                    if frag2 is not None:
                        wg.upsert_fragment(frag2, source)
            # Durability checkpoint. Without this the whole run lives in memory
            # until the final save: a 200-doc run held ~18h of extraction in RAM
            # and any interruption lost all of it. save() persists the graph AND
            # the manifest, and the manifest is what lets the next run skip the
            # chunks already done — so a checkpoint bounds loss to the work since
            # the last one and makes an interrupted run genuinely resumable.
            if checkpoint_every > 0 and since_ckpt >= checkpoint_every:
                wg.save()
                qm.write(Path(workdir) / "extract_quality.json")
                _emit({"phase": "checkpoint", "chunks_since": since_ckpt,
                       "concepts": len(wg._live_concepts()),
                       "quality": qm.report()})
                since_ckpt = 0

        # Recover relations parked as `ungrounded` whose endpoints recur across
        # chunks (e.g. GUVERNUL, MONITORUL OFICIAL never appear as outcomes, so
        # the acts' issuer/publication edges are all parked). Runs before the
        # candidate ontology is built so the recovered edges are proposed with
        # it. Frequency (`min_refs`) is the gate; anything still ungrounded stays
        # parked, so this only ever adds grounded structure.
        promo = {}
        if promote_min_refs > 0:
            try:
                promo = wg.promote_parked_endpoints(min_refs=promote_min_refs,
                                                    max_promote=promote_max_per_run)
                if promo.get("relations_recovered"):
                    _emit({"phase": "promote", **promo})
            except Exception:
                logger.exception("endpoint promotion failed; continuing")
                promo = {}

        quality = qm.report()
        # Dead letters are part of the run's quality picture: they are the work
        # the factory decided to stop paying for, and nobody sees them unless
        # they are reported. `total` is cumulative across runs, not just this one.
        quality["quarantined_now"] = len(quarantined)
        quality["quarantined_total"] = len(wg.quarantined_chunks())
        if quarantined:
            _emit({"phase": "quarantine", "chunks": quarantined})
        # Current run, plus an append-only history: the per-run file is
        # overwritten each run, so batch-by-batch telemetry was lost when the
        # pipeline report dropped it (server.py built the extract step from a
        # fixed key list). Keep both.
        qm.write(Path(workdir) / "extract_quality.json")
        try:
            hist = Path(workdir) / "extract_quality_history.jsonl"
            with hist.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": time.time(), **quality},
                                    ensure_ascii=False) + "\n")
        except Exception:
            pass
        for a in quality.get("alarms", []):
            logger.warning("extraction quality alarm: %s", a)

        # Persist the extraction BEFORE deriving the proposal from it. These were
        # the other way round, and on 2026-07-30 `to_candidate_ontology` raised
        # (an unbatched embed call hit HTTP 400 at 23,956 characteristics) — which
        # discarded a batch of extraction that had already been paid for in full,
        # because `save()` never ran. The working graph is the truth and the
        # candidate ontology is a derivative; a failure computing the derivative
        # must not cost the truth. `save()` itself follows the same rule
        # internally, persisting the store before rebuilding the projection.
        wg.save()
        ontology = wg.to_candidate_ontology(version)
        _emit({"phase": "done", "added": len(new), "changed": len(changed),
               "removed": len(removed), "concepts": len(wg._live_concepts()),
               "edges": len(wg.relations), "relooks": relooks, "parked": len(wg.parked)})
        session.record_query(f"graph_extract:{len(to_extract)}", len(wg._live_concepts()))
        return {
            "ontology": ontology,
            "added": len(new), "changed": len(changed), "removed": len(removed),
            "concepts": len(wg._live_concepts()), "edges": len(wg.relations),
            "relooks": relooks, "parked": len(wg.parked),
            "promoted": promo.get("promoted", 0),
            "relations_recovered": promo.get("relations_recovered", 0),
            "quality": quality,
            "context_mode": extract.CONTEXT_MODE,
            "quarantined": quarantined,
        }
    finally:
        wg.close()
