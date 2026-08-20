"""Project knowledge store — the substrate for Claude-Projects-style grounding.

Each item (a file's extracted text, or a pasted note) is embedded on write via
the SHARED embedder. :func:`ground` returns the text to inject into a project's
chats/tasks: the WHOLE knowledge base when it's small, or the query-relevant
items (cosine over the stored vectors) when it exceeds the context budget.
Everything degrades gracefully — no embedder, no query, or a mock embedder all
still yield useful (recency-ordered) grounding.

One degradation used to be invisible: the embedder is chosen per BOOT (offline
mock = 64 dims, a local ``nomic-embed-text`` = 768), so items written while
Ollama was down are stored at a length the next boot's query vector cannot be
compared against. ``_cosine`` returns 0.0 on a length mismatch, which quietly
turned relevance ranking back into recency ranking over a knowledge base too
large to include whole. Those items are now ranked by KEYWORD instead (damped,
so a real cosine hit always wins) and :func:`ground` appends a note saying it
happened — the grounding block never claims to be relevance-ranked when it isn't.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.models import ProjectKnowledge

#: Chars of an item we embed for retrieval (one representative vector/item).
_EMBED_CHARS = 4000
#: Default grounding budget injected into a prompt.
DEFAULT_GROUND_CHARS = 6000
#: A keyword rescue is a weaker claim than a real cosine hit — damped so it can
#: never outrank genuine similarity (same convention as ``memory/retrieval.py``).
_ORPHAN_DAMP = 0.5

_WORD = re.compile(r"[a-z0-9]+")


def _embed(embedder, text: str) -> list[float]:
    if embedder is None or not text.strip():
        return []
    try:
        return list(embedder.embed(text[:_EMBED_CHARS]))
    except Exception:  # noqa: BLE001 — retrieval is best-effort; store text anyway
        return []


def _cosine(u: list[float], v: list[float]) -> float:
    if not u or not v or len(u) != len(v):
        return 0.0
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u))
    nv = math.sqrt(sum(b * b for b in v))
    return dot / (nu * nv) if nu and nv else 0.0


def _lexical(query_tokens: set[str], text: str) -> float:
    """Fraction of query terms present in the text, in ``[0,1]`` — the cheap
    offline relevance the rest of the system gives its non-embedded stores.
    Local by design: this module must stay importable without the memory package.
    """
    if not query_tokens:
        return 0.0
    hit = query_tokens & set(_WORD.findall((text or "").lower()))
    return len(hit) / len(query_tokens)


def _stored_vector(raw: str | None) -> list[float]:
    try:
        vec = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return vec if isinstance(vec, list) else []


def add_knowledge(
    platform, project_id: str, name: str, text: str, *, kind: str = "note"
) -> ProjectKnowledge:
    """Store one knowledge item (embedded on write). Text is required."""
    text = (text or "").strip()
    if not text:
        raise ValueError("knowledge text is empty")
    embedder = getattr(platform, "embedder", None)
    rec = ProjectKnowledge(
        project_id=project_id,
        name=(name or "untitled").strip()[:200],
        kind=kind if kind in ("note", "file") else "note",
        text=text,
        size=len(text),
        # A BARE list on purpose: ProjectKnowledge vectors are also read by
        # memory/fabric.py and rewritten by the knowledge-edit route, both of
        # which assume a list. Tagging this cell with the embedder identity (as
        # MemoryRecord now is) has to land in the SAME change as those two, or a
        # tagged row silently scores 0.0 in the fabric. Dimension is intrinsic to
        # the vector, and dimension is what ground() checks.
        embedding_json=json.dumps(_embed(embedder, text)),
    )
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
    return rec


def list_knowledge(platform, project_id: str) -> list[dict[str, Any]]:
    """Metadata for every knowledge item (newest first) — no text/vectors."""
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(
                select(ProjectKnowledge)
                .where(ProjectKnowledge.project_id == project_id)
                .order_by(ProjectKnowledge.created_at.desc())  # type: ignore[attr-defined]
            )
        )
    return [
        {
            "id": r.id,
            "name": r.name,
            "kind": r.kind,
            "size": r.size,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def remove_knowledge(platform, project_id: str, knowledge_id: str) -> bool:
    """Delete one item. Returns False when it didn't exist (for a clean 404)."""
    with session_scope(platform.engine) as db:
        rec = db.get(ProjectKnowledge, knowledge_id)
        if rec is None or rec.project_id != project_id:
            return False
        db.delete(rec)
        db.commit()
    return True


def ground(
    platform,
    project_id: str,
    query: str = "",
    *,
    char_budget: int = DEFAULT_GROUND_CHARS,
) -> str:
    """The knowledge text to inject for this project. Small base → include it
    ALL; large base → the query-relevant items (cosine) up to ``char_budget``,
    falling back to newest-first when there's no usable query/embedder."""
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(
                select(ProjectKnowledge)
                .where(ProjectKnowledge.project_id == project_id)
                .order_by(ProjectKnowledge.created_at.desc())  # type: ignore[attr-defined]
            )
        )
    if not rows:
        return ""
    total = sum(r.size for r in rows)
    note = ""
    chosen: list[ProjectKnowledge]
    if total <= char_budget:
        chosen = list(reversed(rows))  # oldest→newest reads naturally
    else:
        embedder = getattr(platform, "embedder", None)
        qvec = _embed(embedder, query) if query.strip() else []
        if qvec:
            qdim = len(qvec)
            qtokens = set(_WORD.findall((query or "").lower()))
            unrankable = 0
            rescued = 0
            scored = []
            for r in rows:
                vec = _stored_vector(r.embedding_json)
                if len(vec) == qdim:
                    scored.append((_cosine(qvec, vec), r))
                    continue
                # A vector of a DIFFERENT length came from a different embedder
                # (the offline mock is 64-dim, nomic-embed-text 768-dim, and the
                # choice is made per BOOT). _cosine scores it 0.0, which silently
                # sinks every item written under the other embedder below every
                # item written under this one — relevance ranking replaced by an
                # accident, wearing the same face. Rank it by keyword instead,
                # damped, and SAY SO in the block we hand the model.
                unrankable += 1
                rel = _lexical(qtokens, f"{r.name}\n{r.text}")
                if rel > 0.0:
                    rescued += 1
                scored.append((rel * _ORPHAN_DAMP, r))
            scored.sort(key=lambda x: x[0], reverse=True)
            ordered = [r for _, r in scored]
            if unrankable:
                active = str(getattr(embedder, "model", "") or "unnamed embedder")
                note = (
                    f"_(Note: {unrankable} of {len(rows)} knowledge items carry a "
                    f"vector the active embedder ({active}, {qdim} dims) cannot be "
                    f"compared against — a different embedding model wrote them — "
                    f"so they could not be relevance-ranked; {rescued} were matched "
                    f"by keyword instead. Some project knowledge may therefore be "
                    f"missing from this block.)_"
                )
        else:
            ordered = rows  # newest-first fallback
        chosen = []
        used = 0
        for r in ordered:
            if used and used + r.size > char_budget:
                continue
            chosen.append(r)
            used += r.size
            if used >= char_budget:
                break

    blocks = [f"## {r.name}\n{r.text}" for r in chosen]
    body = "\n\n".join(blocks)
    if len(body) > char_budget + 500:  # hard clamp (a single huge item)
        body = body[:char_budget].rstrip() + "\n…(truncated)"
    if note:  # appended AFTER the clamp — a degradation notice must not be cut
        body = f"{body}\n\n{note}" if body else note
    return body
