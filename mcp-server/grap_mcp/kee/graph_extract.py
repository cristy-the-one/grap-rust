"""Phase 1: the budgeted, graph-augmented extraction loop.

Single streaming pass: for each chunk, retrieve the relevant known subgraph,
extract with that context, upsert (resolve-or-create + ground relations), and
spend a bounded re-look only when the graph flags a conflict/correlation.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Sequence, Tuple

from . import extract
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


def graph_extract_ontology(chunks: Sequence[Tuple[str, str]], provider, embedder, *,
                           sim: float = 0.86, version: str = "kee-candidate",
                           relook_ratio: float = 0.2, context_cap: int = 8,
                           workdir: Optional[str] = None) -> Dict[str, Any]:
    wg = WorkingGraph(embedder, merge_threshold=sim, context_cap=context_cap,
                      workdir=workdir)
    budget = math.ceil(relook_ratio * len(chunks)) if chunks else 0
    relooks = 0

    for source, text in chunks:
        ctx = wg.context_for(text)
        frag = _extract_with_context(provider, source, text, ctx)
        if frag is None:
            continue
        sig = wg.upsert_fragment(frag, source)
        if sig.warrants_relook() and relooks < budget:
            relooks += 1
            fctx = wg.focused_context(sig)
            frag2 = _extract_with_context(provider, source, text, fctx)
            if frag2 is not None:
                wg.upsert_fragment(frag2, source, relook=True)

    return {
        "ontology": wg.to_candidate_ontology(version),
        "chunks": len(chunks),
        "relooks": relooks,
        "parked": len(wg.parked),
    }
