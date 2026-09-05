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
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from typing import List, Optional, Protocol, Sequence, runtime_checkable

logger = logging.getLogger("grap-mcp.kee")

# Default OpenAI-compatible endpoint (Ollama). Reused for the HTTP embedder so
# embeddings can be fully local via Ollama (e.g. nomic-embed-text).
# 127.0.0.1, NOT "localhost": on Windows dual-stack, "localhost" resolves to
# ::1 first, and Ollama listens on IPv4 only. urllib then blocks in SYN_SENT
# instead of falling back to IPv4 — a law-ro pipeline run hung there for 24
# minutes without the socket timeout ever firing.
_DEFAULT_EMBED_BASE_URL = "http://127.0.0.1:11434/v1"
_DEFAULT_OLLAMA_EMBED_MODEL = "nomic-embed-text"

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


# Texts per /embeddings request. Bounds the payload so a large caller cannot
# blow the server's request limit (see OpenAICompatEmbedder.embed).
EMBED_BATCH = 128


class OpenAICompatEmbedder:
    """Embedder backed by an OpenAI-compatible /embeddings endpoint.

    Works with Ollama (`http://127.0.0.1:11434/v1`, e.g. model
    `nomic-embed-text`), vLLM, and hosted APIs — so embeddings can be fully
    local via Ollama. Stdlib urllib only; vectors are L2-normalized for
    consistency with the other embedders.
    """

    name = "ollama"

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None,
                 api_key: Optional[str] = None, timeout: float = 60.0) -> None:
        self.base_url = (base_url or os.environ.get("GRAP_EMBED_BASE_URL")
                         or os.environ.get("GRAP_LLM_BASE_URL")
                         or _DEFAULT_EMBED_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("GRAP_EMBED_MODEL", _DEFAULT_OLLAMA_EMBED_MODEL)
        self.api_key = api_key or os.environ.get("GRAP_LLM_API_KEY")
        self.timeout = timeout
        self.dim = 0
        # Same transient-failure hardening the LLM provider has. The embedder is
        # on the critical path of ingest, resolution and projection, so a blip
        # here stalls or kills a whole run.
        try:
            self.retries = int(os.environ.get("GRAP_EMBED_RETRIES", "3"))
        except ValueError:
            self.retries = 3
        try:
            self.retry_backoff = float(os.environ.get("GRAP_EMBED_RETRY_BACKOFF", "2.0"))
        except ValueError:
            self.retry_backoff = 2.0

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed any number of texts, splitting into bounded requests.

        The batching lives HERE rather than at the call sites on purpose. Two
        callers already batched (`working_graph._prefetch_vectors`,
        `ingest.ingest_paths`) and a third did not: `reconcile._dedup_strings`
        embeds every distinct characteristic in one call, which was fine at 5,000
        and returned HTTP 400 at 23,956 — mid-run, at 1,547 documents, after the
        batch's extraction had already been paid for. A payload limit is a
        property of the transport, so the transport should enforce it; leaving it
        to callers means every new caller re-discovers the wall.
        """
        texts = list(texts)
        if len(texts) <= EMBED_BATCH:
            return self._embed_batch(texts)
        out: List[List[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            out.extend(self._embed_batch(texts[start:start + EMBED_BATCH]))
        return out

    def _embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                return self._embed_once(texts)
            except urllib.error.HTTPError as exc:
                if exc.code < 500:              # client error: retry won't help
                    raise
                last_exc = exc
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last_exc = exc
            if attempt < self.retries:
                time.sleep(self.retry_backoff * (2 ** attempt))
        raise RuntimeError(
            f"embedding request to {self.base_url}/embeddings failed after "
            f"{self.retries + 1} attempts: {last_exc}. Is the server running and "
            f"is model '{self.model}' pulled?")

    def _embed_once(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": list(texts)}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/embeddings",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        # Network errors deliberately propagate raw so `embed()` can classify and
        # retry them; it raises the friendly RuntimeError once attempts run out.
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        try:
            rows = sorted(body["data"], key=lambda d: d.get("index", 0))
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"unexpected embeddings response shape: {body!r}") from exc
        if vectors:
            self.dim = len(vectors[0])
        return [_l2_normalize(v) for v in vectors]


_HTTP_BACKENDS = {"ollama", "http", "openai", "openai-compat"}


def get_embedder(prefer_local: bool = True) -> Embedder:
    """Return an embedder, chosen by GRAP_EMBED_BACKEND:

      - "ollama"/"http"  : OpenAI-compatible HTTP embedder (fully local via
                           Ollama, e.g. nomic-embed-text); falls back to hashing
                           if the endpoint is unreachable.
      - "hashing"        : deterministic offline hashing embedder.
      - "local"          : sentence-transformers only (no fallback).
      - unset/"auto"     : try sentence-transformers, fall back to hashing.

    Every fallback keeps ingest working offline / without a model.
    """
    backend = os.environ.get("GRAP_EMBED_BACKEND", "").strip().lower()

    if backend in _HTTP_BACKENDS:
        try:
            emb = OpenAICompatEmbedder()
            emb.embed(["warmup"])  # force a real call so we fail here, not mid-ingest
            logger.info("KEE embedder: HTTP model '%s' at %s (dim=%d)",
                        emb.model, emb.base_url, emb.dim)
            return emb
        except Exception as exc:  # noqa: BLE001 - endpoint unreachable / bad shape
            logger.warning("KEE embedder: HTTP embedder unavailable (%s); "
                           "falling back to hashing embedder", exc)
            return HashingEmbedder()

    if backend == "hashing":
        return HashingEmbedder()

    if backend == "local" or (prefer_local and backend in ("", "auto")):
        try:
            emb = LocalEmbedder()
            emb.embed(["warmup"])  # force load so we fail here, not mid-ingest
            logger.info("KEE embedder: local model '%s' (dim=%d)", emb.model_name, emb.dim)
            return emb
        except Exception as exc:  # noqa: BLE001 - any load/import/network failure
            if backend == "local":
                raise
            logger.warning("KEE embedder: local model unavailable (%s); "
                           "falling back to hashing embedder", exc)
    return HashingEmbedder()
