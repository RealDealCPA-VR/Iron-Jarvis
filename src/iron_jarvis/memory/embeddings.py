"""Text embeddings (§22 retrieval — Total Recall).

Two embedders implement the :class:`Embedder` protocol:

* :class:`MockEmbedder` — deterministic, network-free, offline-safe. Tokens are
  hashed into a fixed number of buckets and the vector is L2-normalized, so:
  identical text always yields one vector, and texts that share tokens land
  closer together under cosine similarity. This is the always-available fallback.
* :class:`OllamaEmbedder` — a REAL local embedding model served by Ollama
  (default ``nomic-embed-text``). It calls the local embeddings endpoint over a
  short-timeout ``httpx`` request and raises a clean, catchable
  :class:`EmbedderError` on ANY failure (unreachable / timeout / non-200 / parse).

:func:`build_embedder` is the single factory the rest of the system uses: for
``provider`` ``"ollama"``/``"auto"`` it probes the local server once and returns an
:class:`OllamaEmbedder` only when reachable, otherwise it silently falls back to
the :class:`MockEmbedder`. When an ``engine`` is supplied the chosen embedder is
wrapped in a :class:`CachingEmbedder` so vectors persist across restarts.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Protocol, runtime_checkable

import numpy as np

# Importing the cache module registers EmbeddingRecord on SQLModel.metadata BEFORE
# init_db runs (the platform imports this module early). Keep this import here so
# the persistent embedding table exists wherever embeddings are used.
from .embedding_cache import EmbeddingRecord, EmbeddingStore  # noqa: F401

EMBED_DIM = 64
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Default local embedding model served by Ollama.
DEFAULT_OLLAMA_EMBED_MODEL = "nomic-embed-text"
#: Short timeouts keep a missing/slow server from ever stalling a search or boot.
_EMBED_TIMEOUT = 10.0
_PROBE_TIMEOUT = 2.0

from ..core.logging import get_logger

_log = get_logger("embeddings")


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into a fixed-length vector (§22)."""

    def embed(self, text: str) -> list[float]:
        ...


class EmbedderError(RuntimeError):
    """Raised by a real embedder when it cannot produce a vector (catchable)."""


class MockEmbedder:
    """Deterministic bag-of-hashed-tokens embedder; offline, no network (§22)."""

    #: Embedder identity used as a cache key (vectors of different models differ
    #: in meaning AND dimensionality, so they must never be mixed in the cache).
    model = "mock"

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())

    def embed(self, text: str) -> list[float]:
        """Hash tokens into buckets, then L2-normalize to a unit vector."""
        vec = np.zeros(self.dim, dtype=np.float64)
        for token in self._tokens(text):
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dim
            vec[bucket] += 1.0
        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec /= norm
        return vec.tolist()


class OllamaEmbedder:
    """Real local embeddings via an Ollama server (network optional, fail-loud).

    Calls ``POST {base_url}/api/embeddings`` with ``{"model", "prompt"}`` and
    returns the embedding vector. On ANY failure — connection error, timeout,
    non-200 status, or an unparseable/empty body — it raises :class:`EmbedderError`
    so the caller (the :func:`build_embedder` factory, or a wrapping cache) can
    fall back to the offline :class:`MockEmbedder`. The HTTP client is injectable
    so tests never touch the network.
    """

    def __init__(
        self,
        base_url: str,
        model: str = DEFAULT_OLLAMA_EMBED_MODEL,
        *,
        timeout: float = _EMBED_TIMEOUT,
        http: Any = None,
    ) -> None:
        # Normalize to the Ollama HOST ROOT: ollama_base_url is often the chat
        # endpoint (".../v1"); the native embeddings API lives at /api/embeddings
        # off the host, so strip a trailing "/v1" before appending.
        self.base_url = _ollama_host_root(base_url)
        self.model = model
        self.timeout = timeout
        self._http = http  # injectable httpx.Client-like object

    def _client(self) -> Any:
        if self._http is None:
            import httpx  # lazy: keep the import cost off the offline path

            self._http = httpx.Client(timeout=self.timeout)
        return self._http

    def embed(self, text: str) -> list[float]:
        # LOCAL ONLY (v1.332.0): an embedding request carries the text itself
        # (a chat's question, an attachment's chunk). Under a local-only
        # chat's scope it is never sent to a server off this network: it is
        # refused HERE, at the one embedder that touches the network, as an
        # EmbedderError, so every grounding caller takes its existing no-vector
        # path (lexical scoring, or that store answers nothing). A cached
        # vector (CachingEmbedder) still serves: it never leaves the PC.
        from ..providers import egress as _egress

        if _egress.current_scope().on and not _egress.private_host(self.base_url):
            raise EmbedderError(
                "this chat is local only and the embedding server is not on this"
                " network, so nothing was sent to it"
            )
        url = f"{self.base_url}/api/embeddings"
        try:
            resp = self._client().post(
                url, json={"model": self.model, "prompt": text}
            )
        except Exception as exc:  # connection error / timeout / DNS / etc.
            raise EmbedderError(f"ollama embeddings request failed: {exc}") from exc
        status = getattr(resp, "status_code", 0)
        if status != 200:
            raise EmbedderError(f"ollama embeddings returned HTTP {status}")
        try:
            data = resp.json()
        except Exception as exc:
            raise EmbedderError(f"ollama embeddings: bad JSON: {exc}") from exc
        # /api/embeddings -> {"embedding": [...]}; /api/embed -> {"embeddings": [[...]]}.
        vec = data.get("embedding")
        if vec is None:
            embeddings = data.get("embeddings")
            if isinstance(embeddings, list) and embeddings:
                vec = embeddings[0]
        if not isinstance(vec, list) or not vec:
            raise EmbedderError("ollama embeddings: missing/empty 'embedding'")
        try:
            return [float(x) for x in vec]
        except (TypeError, ValueError) as exc:
            raise EmbedderError(f"ollama embeddings: non-numeric vector: {exc}") from exc


class CachingEmbedder:
    """Wrap a base embedder with the persistent :class:`EmbeddingStore`.

    Transparent: it implements ``embed(text)`` so it drops straight into
    filesearch/ltm. Vectors are keyed by the base embedder's ``model`` so a
    cache built under one model is never reused under another.
    """

    def __init__(self, base: Embedder, engine: Any, *, model: str | None = None) -> None:
        self.base = base
        self.engine = engine
        self.model = model or getattr(base, "model", "mock")
        self.store = EmbeddingStore(engine)

    def embed(self, text: str) -> list[float]:
        return self.store.get_or_compute(self.base.embed, text, model=self.model)


def _ollama_host_root(base_url: str) -> str:
    """The Ollama host root, with a trailing ``/v1`` (the OpenAI-compat chat path)
    stripped, so the native ``/api/embeddings`` endpoint resolves correctly even
    when the chat ``ollama_base_url`` is reused."""
    u = (base_url or "").rstrip("/")
    if u.endswith("/v1"):
        u = u[: -len("/v1")]
    return u.rstrip("/")


def _ollama_embed_ok(
    base_url: str, model: str, *, http: Any = None, timeout: float = _PROBE_TIMEOUT
) -> bool:
    """STRONG probe: actually request an embedding for the configured model and
    require a valid vector. A reachable server WITHOUT the model pulled (the most
    common real failure) then correctly falls back to Mock AT BOOT — so the whole
    process is dimensionally consistent and never hot-swaps embedders mid-search."""
    try:
        vec = OllamaEmbedder(base_url, model=model, timeout=timeout, http=http).embed("ok")
        return isinstance(vec, list) and len(vec) > 0
    except Exception:
        return False


def _build_base_embedder(
    provider: str,
    base_url: str | None,
    model: str,
    *,
    http: Any = None,
    reachable: Any = None,
) -> Embedder:
    """Pick the real or mock embedder per the configured provider + reachability."""
    if provider == "mock":
        return MockEmbedder()
    if provider in ("ollama", "auto"):
        if base_url:
            ok = (
                reachable(base_url)
                if reachable is not None
                else _ollama_embed_ok(base_url, model, http=http)
            )
            if ok:
                try:
                    return OllamaEmbedder(base_url, model=model, http=http)
                except Exception:  # construction must never crash the factory
                    pass
        # Unreachable / model-not-pulled / unconfigured: degrade to the offline
        # embedder. Silent for "auto" (opportunistic); noted for explicit "ollama".
        if provider == "ollama":
            _log.warning(
                "embedder provider 'ollama' unavailable at %s (server down or "
                "model %r not pulled); using MockEmbedder",
                base_url,
                model,
            )
        return MockEmbedder()
    # Unknown provider value: fail safe to the offline embedder.
    return MockEmbedder()


def build_embedder(
    config: Any,
    engine: Any = None,
    *,
    http: Any = None,
    reachable: Any = None,
) -> Embedder:
    """The single place the system constructs an embedder (§22 Total Recall).

    Reads ``embedder_provider`` (``"auto"`` | ``"ollama"`` | ``"mock"``),
    ``embedder_model``, and ``ollama_base_url`` from ``config``. For
    ``"ollama"``/``"auto"`` it probes the local server once and returns an
    :class:`OllamaEmbedder` only when reachable, else the :class:`MockEmbedder`.
    When ``engine`` is given the chosen embedder is wrapped in a
    :class:`CachingEmbedder` so vectors persist across restarts.

    ``http``/``reachable`` are injection seams for offline tests.
    """
    provider = getattr(config, "embedder_provider", "auto") or "auto"
    model = getattr(config, "embedder_model", DEFAULT_OLLAMA_EMBED_MODEL)
    base_url = getattr(config, "ollama_base_url", None)
    base = _build_base_embedder(
        provider, base_url, model, http=http, reachable=reachable
    )
    if engine is not None:
        return CachingEmbedder(base, engine)
    return base
