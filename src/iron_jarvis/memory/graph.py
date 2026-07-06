"""The memory GRAPH — one connected view of everything Iron Jarvis remembers.

Nodes come from the three memory surfaces (learned lessons, layered working
memory, long-term markdown notes); edges are cosine SIMILARITY over the shared
embedder (real Ollama when connected, deterministic offline mock otherwise),
merged with the user's own curation: ``manual`` links always show, ``blocked``
pairs never do (see :class:`~iron_jarvis.memory.models.MemoryLinkRecord`).

Node ids are opaque strings the API and the link table share:

* ``lesson:<record id>``
* ``wm:<layer>:<scope or '-'>:<key>``
* ``ltm:<source>:<ref>``  (markdown-backed sources only — they are the only
  connectors that can ENUMERATE notes; Notion/cloud sources are search-only)
"""

from __future__ import annotations

import json
import math
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from .models import MemoryLinkRecord

#: Bound the graph: pairwise similarity is O(n²) and the view stays readable.
MAX_NODES_PER_GROUP = 60
#: Auto-edges kept per node (strongest first).
MAX_EDGES_PER_NODE = 3
#: Minimum cosine similarity for an automatic edge.
DEFAULT_THRESHOLD = 0.45
#: Snippet length shipped to the UI.
_SNIPPET = 220


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    """One row per pair regardless of click order."""
    return (a, b) if a <= b else (b, a)


def _cosine(u: list[float], v: list[float]) -> float:
    if not u or not v or len(u) != len(v):
        return 0.0
    dot = sum(x * y for x, y in zip(u, v))
    nu = math.sqrt(sum(x * x for x in u))
    nv = math.sqrt(sum(y * y for y in v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return dot / (nu * nv)


def _lesson_nodes(platform) -> list[dict[str, Any]]:
    out = []
    for r in platform.learning.lessons(scope=None, limit=MAX_NODES_PER_GROUP):
        out.append(
            {
                "id": f"lesson:{r.id}",
                "label": (r.text or "")[:60] or "(empty lesson)",
                "group": "lesson",
                "snippet": (r.text or "")[:_SNIPPET],
                "meta": {"source": r.source, "weight": r.effective_weight},
                "_text": r.text or "",
            }
        )
    return out


def _working_memory_nodes(platform) -> list[dict[str, Any]]:
    out = []
    for layer in platform.memory.LAYERS:
        for r in platform.memory.list(layer):
            if len(out) >= MAX_NODES_PER_GROUP:
                return out
            scope = r.scope_id or "-"
            out.append(
                {
                    "id": f"wm:{r.layer}:{scope}:{r.key}",
                    "label": r.key[:60],
                    "group": "memory",
                    "snippet": (r.text or "")[:_SNIPPET],
                    "meta": {"layer": r.layer, "scope": r.scope_id},
                    "_text": f"{r.key}. {r.text or ''}",
                }
            )
    return out


def _ltm_nodes(platform) -> list[dict[str, Any]]:
    """Notes from every ENUMERABLE (markdown-dir) LTM source. Search-only
    connectors (Notion, cloud drives, RAG endpoints) can't list their items —
    the UI states that honestly rather than pretending they're all here."""
    out: list[dict[str, Any]] = []
    for conn in platform.ltm.connectors():
        files = getattr(conn, "_files", None)
        read = getattr(conn, "_read", None)
        if not callable(files) or not callable(read):
            continue
        try:
            paths = files()
        except Exception:  # noqa: BLE001 — a broken vault must not kill the graph
            continue
        for path in paths:
            if len(out) >= MAX_NODES_PER_GROUP:
                return out
            try:
                text = read(path)
            except Exception:  # noqa: BLE001
                continue
            out.append(
                {
                    "id": f"ltm:{conn.name}:{path}",
                    "label": getattr(path, "stem", str(path))[:60],
                    "group": "note",
                    "snippet": (text or "")[:_SNIPPET],
                    "meta": {"source": conn.name, "ref": str(path)},
                    "_text": text or "",
                }
            )
    return out


def _embed_all(platform, nodes: list[dict[str, Any]]) -> tuple[list[list[float]], str]:
    """One vector per node via the SHARED embedder (platform.embedder — real
    when connected, mock offline); memory's own embedder is the last resort."""
    embedder = getattr(platform, "embedder", None) or platform.memory.embedder
    name = str(getattr(embedder, "model", type(embedder).__name__))
    vectors: list[list[float]] = []
    for node in nodes:
        try:
            vectors.append(list(embedder.embed(node["_text"][:2000])))
        except Exception:  # noqa: BLE001 — an embed failure = an isolated node
            vectors.append([])
    return vectors, name


def build_memory_graph(
    platform, *, threshold: float = DEFAULT_THRESHOLD
) -> dict[str, Any]:
    """Assemble the full graph payload: nodes, similarity+manual edges, and
    which embedder scored the similarities (so the UI can say so)."""
    nodes = _lesson_nodes(platform) + _working_memory_nodes(platform) + _ltm_nodes(platform)
    ids = {n["id"] for n in nodes}

    with session_scope(platform.engine) as db:
        links = list(db.exec(select(MemoryLinkRecord)))
    blocked = {(l.a, l.b) for l in links if l.kind == "blocked"}
    manual = [l for l in links if l.kind == "manual"]

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    # The user's own connections first — they beat any similarity math.
    for link in manual:
        pair = (link.a, link.b)
        if link.a in ids and link.b in ids and pair not in seen:
            seen.add(pair)
            edges.append({"a": link.a, "b": link.b, "weight": 1.0, "kind": "manual"})

    vectors, embedder_name = _embed_all(platform, nodes)
    # Per-node strongest neighbours above the threshold, minus blocked pairs.
    candidates: dict[int, list[tuple[float, int]]] = {}
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            sim = _cosine(vectors[i], vectors[j])
            if sim < threshold:
                continue
            if canonical_pair(nodes[i]["id"], nodes[j]["id"]) in blocked:
                continue
            candidates.setdefault(i, []).append((sim, j))
            candidates.setdefault(j, []).append((sim, i))
    for i, neigh in candidates.items():
        for sim, j in sorted(neigh, reverse=True)[:MAX_EDGES_PER_NODE]:
            pair = canonical_pair(nodes[i]["id"], nodes[j]["id"])
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(
                {"a": pair[0], "b": pair[1], "weight": round(sim, 3), "kind": "similar"}
            )

    for n in nodes:
        n.pop("_text", None)
    payload: dict[str, Any] = {
        "nodes": nodes,
        "edges": edges,
        "embedder": embedder_name,
    }
    if embedder_name == "mock":
        payload["note"] = (
            "similarity is scored by the offline mock embedder (lexical-ish); "
            "connect a local Ollama for true semantic edges"
        )
    return payload


def set_link(platform, a: str, b: str) -> dict[str, Any]:
    """Create a MANUAL edge (and lift any block on the pair)."""
    if not a or not b or a == b:
        raise ValueError("two distinct node ids are required")
    ca, cb = canonical_pair(a, b)
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(
                select(MemoryLinkRecord)
                .where(MemoryLinkRecord.a == ca)
                .where(MemoryLinkRecord.b == cb)
            )
        )
        for row in rows:
            if row.kind == "manual":
                return {"linked": True, "note": "already linked"}
            db.delete(row)  # lift the block — the user changed their mind
        db.add(MemoryLinkRecord(a=ca, b=cb, kind="manual"))
        db.commit()
    return {"linked": True}


def remove_link(platform, a: str, b: str) -> dict[str, Any]:
    """Disconnect a pair: a manual link is deleted; a similarity edge is
    BLOCKED (persisted) so it never reappears."""
    if not a or not b or a == b:
        raise ValueError("two distinct node ids are required")
    ca, cb = canonical_pair(a, b)
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(
                select(MemoryLinkRecord)
                .where(MemoryLinkRecord.a == ca)
                .where(MemoryLinkRecord.b == cb)
            )
        )
        had_manual = any(r.kind == "manual" for r in rows)
        for row in rows:
            db.delete(row)
        if not had_manual:
            db.add(MemoryLinkRecord(a=ca, b=cb, kind="blocked"))
        db.commit()
    return {"removed": "manual" if had_manual else "auto", "blocked": not had_manual}
