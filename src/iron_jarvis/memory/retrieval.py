"""Vector retrieval (§22 retrieval pipeline).

``Retriever`` is the storage-agnostic contract. ``SqliteVectorRetriever`` is the
default backend: it keeps each embedding inline on the ``MemoryRecord`` row and
ranks candidates by numpy cosine similarity against the query embedding. Moving
to pgvector swaps this class without touching the layer manager.

**A stored vector carries the identity of the embedder that produced it.**
``build_embedder`` picks per BOOT — the offline ``MockEmbedder`` is 64-dim, a
local ``nomic-embed-text`` is 768-dim — so a daily driver whose Ollama is
sometimes up writes rows under one embedder and queries them under the other.
The old format was a bare JSON list, and the ranker simply DROPPED every row
whose vector length differed from the query's: a week of remembered facts went
permanently invisible to recall while the rows sat intact in SQLite and the
awareness index kept advertising them. That is the project's own named
anti-goal — a degraded retrieval wearing the same face as no-such-memory.

So: a vector is now stored as ``{"model": ..., "dim": n, "v": [...]}`` (a bare
list still decodes, untagged — this is additive, never a migration), a
non-comparable row is ranked LEXICALLY instead of dropped, and
:meth:`SqliteVectorRetriever.search_report` hands back a note saying it
happened. Lexical rescue rather than lazy re-embedding is deliberate: recall is
a READ path, and re-embedding N rows would mean N blocking HTTP calls to Ollama
per search plus writes from a read. Rows repair themselves as they are rewritten.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod

import numpy as np
from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.logging import get_logger
from .embeddings import Embedder
from .models import MemoryRecord

_log = get_logger("retrieval")


#: Safety cap on the candidate rows a single recall scores — high enough that a
#: real (layer,scope) never truncates in practice (a scope reaching this takes far
#: longer than "weeks"), low enough to bound memory/parse on a pathological store.
#: Cosine is VECTORIZED (one numpy matmul), so scoring this many stays fast.
_MAX_RECALL_CANDIDATES = 10000

#: A keyword rescue is a WEAKER claim than a real cosine hit, so it is damped into
#: the lower half of the [0,1] band every store here ranks in: surfaced, never
#: allowed to outrank genuine similarity.
_ORPHAN_DAMP = 0.5

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def _lexical(query_tokens: set[str], text: str) -> float:
    """Fraction of query terms present in the text, in ``[0,1]`` — the same cheap
    offline relevance ``memory/fabric.py`` gives its non-embedded stores. Kept
    local so the retriever stays dependency-free (it is imported at package init).
    """
    if not query_tokens:
        return 0.0
    return len(query_tokens & _tokens(text)) / len(query_tokens)


def embedder_model(embedder: object) -> str:
    """The identity an embedder stamps on the vectors it produces (``""`` when it
    declares none — a test stub, say, which then behaves exactly as before)."""
    return str(getattr(embedder, "model", "") or "")


def encode_embedding(vec: list[float], model: str = "") -> str:
    """JSON for one stored vector, TAGGED with its producing embedder.

    An empty ``model`` writes the legacy bare list, so a caller with no identity
    to record does not fabricate one.
    """
    values = [float(x) for x in vec]
    if not model:
        return json.dumps(values)
    return json.dumps({"model": model, "dim": len(values), "v": values})


def decode_embedding(raw: str | None) -> tuple[list[float], str]:
    """``(vector, producing-model)`` for a stored cell.

    Accepts BOTH shapes: the tagged object and the bare list written before
    v1.192.0 (and still written by other paths). ``""`` means "not recorded",
    which is not the same claim as "written by a different model". Elements are
    left as JSON numbers — the caller coerces, exactly as the old code did, so
    the hot path costs no more than before.
    """
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return [], ""
    model = ""
    if isinstance(data, dict):
        model = str(data.get("model") or "")
        data = data.get("v")
    return (data if isinstance(data, list) else []), model


def vector_comparable(
    vec: list[float], model: str, *, q_dim: int, q_model: str
) -> bool:
    """Whether a stored vector may be cosine-ranked against the query vector.

    Same dimension AND — when BOTH identities are known — the same model: two
    different 768-dim models produce vectors whose cosine is meaningless. An
    unrecorded identity stays comparable on dimension alone; legacy rows and the
    other bare-list writers must keep ranking exactly as they did.
    """
    if not vec or len(vec) != q_dim:
        return False
    return not (model and q_model and model != q_model)


def _identity_label(model: str, dim: int) -> str:
    if not dim:
        return "no stored vector"
    return f"{model or 'unrecorded embedder'}, {dim} dims"


def _mismatch_note(
    orphans: list[tuple[object, str]],
    total: int,
    rescued: int,
    q_model: str,
    q_dim: int,
) -> str:
    """One honest sentence about rows the active embedder cannot rank."""
    counts: dict[str, int] = {}
    for _row, label in orphans:
        counts[label] = counts.get(label, 0) + 1
    breakdown = ", ".join(f"{n}×[{label}]" for label, n in sorted(counts.items()))
    return (
        f"{len(orphans)} of {total} stored memories carry a vector the active "
        f"embedder ({q_model or 'unrecorded embedder'}, {q_dim} dims) cannot be "
        f"compared against, so they cannot be similarity-ranked: {breakdown}. "
        f"{rescued} matched this query by keyword instead."
    )


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class Retriever(ABC):
    """Storage-agnostic memory index (§22)."""

    @abstractmethod
    def add(self, record: MemoryRecord) -> MemoryRecord:
        """Embed (if needed) and persist a record."""
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        k: int = 5,
        layer: str | None = None,
        scope_id: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        """Return the top-k (record, score) pairs sorted by score desc."""
        ...


class SqliteVectorRetriever(Retriever):
    """Default retriever: inline embeddings + numpy cosine ranking (§22)."""

    def __init__(self, engine: Engine, embedder: Embedder) -> None:
        self.engine = engine
        self.embedder = embedder

    def add(self, record: MemoryRecord) -> MemoryRecord:
        if record.embedding_json in ("", "[]"):
            record.embedding_json = encode_embedding(
                self.embedder.embed(record.text), embedder_model(self.embedder)
            )
        with session_scope(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)  # re-load expired attrs before the session closes
        return record

    def search(
        self,
        query: str,
        k: int = 5,
        layer: str | None = None,
        scope_id: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        hits, _note = self.search_report(query, k=k, layer=layer, scope_id=scope_id)
        return hits

    def search_report(
        self,
        query: str,
        k: int = 5,
        layer: str | None = None,
        scope_id: str | None = None,
    ) -> tuple[list[tuple[MemoryRecord, float]], str]:
        """:meth:`search`, plus a note about rows that could NOT be similarity-ranked.

        The note is ``""`` on a clean run. It is non-empty exactly when this store
        holds vectors the ACTIVE embedder cannot compare against (the boot-time
        mock↔Ollama switch), and it names both identities and how many rows the
        keyword fallback rescued — so a caller can tell the user "12 memories are
        here but unrankable" instead of showing them an empty result.
        """
        q = np.asarray(self.embedder.embed(query), dtype=np.float64)
        stmt = select(MemoryRecord)
        if layer is not None:
            stmt = stmt.where(MemoryRecord.layer == layer)
        if scope_id is not None:
            stmt = stmt.where(MemoryRecord.scope_id == scope_id)
        stmt = stmt.order_by(MemoryRecord.created_at.desc()).limit(_MAX_RECALL_CANDIDATES)
        with session_scope(self.engine) as db:
            rows = list(db.exec(stmt))
        qn = float(np.linalg.norm(q))
        if not rows or qn == 0.0:
            # A query that embeds to nothing is not an embedder mismatch: stay
            # silent rather than blame the store for an empty question.
            return [], ""
        q_model = embedder_model(self.embedder)
        # Vectorized cosine: stack the candidate embeddings and do ONE matmul rather
        # than a Python per-row loop (the measured hot cost) — keeps recall fast
        # while still scoring the whole candidate set for correctness.
        keep: list[MemoryRecord] = []
        vecs: list[np.ndarray] = []
        orphans: list[tuple[MemoryRecord, str]] = []
        for row in rows:
            vec, model = decode_embedding(row.embedding_json)
            if vector_comparable(vec, model, q_dim=int(q.size), q_model=q_model):
                keep.append(row)
                vecs.append(np.asarray(vec, dtype=np.float64))
            else:
                orphans.append((row, _identity_label(model, len(vec))))
        scored: list[tuple[MemoryRecord, float]] = []
        if vecs:
            matrix = np.vstack(vecs)
            norms = np.linalg.norm(matrix, axis=1)
            norms[norms == 0.0] = 1.0
            sims = (matrix @ q) / (norms * qn)
            scored = [(keep[i], float(sims[i])) for i in range(len(keep))]
        note = ""
        if orphans:
            # These rows are NOT gone — they simply carry a vector this embedder
            # cannot compare against. Rank them by keyword (damped) so a stored
            # fact stays findable, and report the degradation either way.
            qtokens = _tokens(query)
            rescued = 0
            for row, _label in orphans:
                rel = _lexical(qtokens, row.text)
                if rel <= 0.0:
                    continue
                scored.append((row, rel * _ORPHAN_DAMP))
                rescued += 1
            note = _mismatch_note(orphans, len(rows), rescued, q_model, int(q.size))
            _log.warning("memory recall degraded: %s", note)
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[: max(0, k)], note
