"""Embedders for the knowledge extraction engine.

Two implementations behind one protocol:
  - HashingEmbedder: deterministic, offline, dependency-free. Used for tests and
    as the fallback when sentence-transformers is unavailable.
  - LocalEmbedder: sentence-transformers (real semantic vectors), lazy-loaded.

`get_embedder()` tries the local model and falls back to hashing with a warning,
so ingest never blocks on a missing/offline model.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from typing import List, Protocol, Sequence, runtime_checkable

logger = logging.getLogger("grap-mcp.kee")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """Maps texts to fixed-dimension, L2-normalized float vectors."""

    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class HashingEmbedder:
    """Deterministic bag-of-tokens hashing embedder.

    Cross-run deterministic (uses hashlib, not Python's salted hash()). Captures
    enough lexical overlap to cluster obvious near-duplicates in tests without a
    model download. Not a semantic model — that's LocalEmbedder's job.
    """

    name = "hashing"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _one(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall((text or "").lower()):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        return _l2_normalize(vec)

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._one(t) for t in texts]


class LocalEmbedder:
    """sentence-transformers embedder (lazy-loaded)."""

    name = "local"

    def __init__(self, model: str | None = None) -> None:
        self.model_name = model or os.environ.get("GRAP_EMBED_MODEL", "all-MiniLM-L6-v2")
        self._model = None
        self.dim = 0

    def _ensure(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # may download
            self._model = SentenceTransformer(self.model_name)
            # method was renamed across sentence-transformers versions
            get_dim = (getattr(self._model, "get_embedding_dimension", None)
                       or self._model.get_sentence_embedding_dimension)
            self.dim = int(get_dim())

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        self._ensure()
        arr = self._model.encode(list(texts), normalize_embeddings=True)
        return [[float(x) for x in row] for row in arr]


def get_embedder(prefer_local: bool = True) -> Embedder:
    """Return an embedder: try the local model, fall back to hashing.

    The fallback keeps ingest working offline / without a downloaded model.
    """
    if prefer_local:
        try:
            emb = LocalEmbedder()
            emb.embed(["warmup"])  # force load so we fail here, not mid-ingest
            logger.info("KEE embedder: local model '%s' (dim=%d)", emb.model_name, emb.dim)
            return emb
        except Exception as exc:  # noqa: BLE001 - any load/import/network failure
            logger.warning("KEE embedder: local model unavailable (%s); "
                           "falling back to hashing embedder", exc)
    return HashingEmbedder()
