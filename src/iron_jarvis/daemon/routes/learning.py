"""Learning routes: lessons, memory layers, improvement engine.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from typing import Any

from ..schemas import GraphNodeDeleteBody, GraphLinkBody, LessonCreateBody, MemoryWrite
from ...core.db import session_scope


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/lessons")
    def lessons(scope: str | None = "user", limit: int = 20) -> dict[str, Any]:
        return {
            "lessons": [
                lr.model_dump() for lr in d.platform.learning.lessons(scope=scope, limit=limit)
            ]
        }

    @app.post("/lessons/compact")
    async def compact_lessons(distill: bool = True) -> dict[str, Any]:
        """Compact the learned-lesson pile: deterministic dedup of reflection
        echoes ALWAYS, then model distillation of the remaining raw reflections
        into a few short generalized lessons — only through a REAL provider
        (mock distillation would fabricate lessons into every future prompt).
        Honest note when no real model is connected."""
        out: dict[str, Any] = {"deduped": d.platform.learning.dedup(), "distilled": 0, "removed": 0}
        if not distill:
            return out
        adapter, used = d._failover_adapter("mock")
        if adapter is None:
            out["note"] = "no real model connected — deterministic dedup only"
            return out

        from ...providers.adapters.base import LLMMessage

        async def _complete(prompt: str) -> str:
            resp, _, _ = await d._one_shot_complete(
                used,
                adapter,
                system=(
                    "You distill working notes into short, general, reusable "
                    "lessons. Reply with ONLY a JSON array of strings."
                ),
                messages=[LLMMessage(role="user", content=prompt)],
            )
            return resp.text or ""

        try:
            res = await d.platform.learning.distill(_complete)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — unusable model reply etc.
            raise HTTPException(status_code=422, detail=f"distillation failed: {exc}")
        out.update(res)
        return out

    @app.post("/lessons")
    def create_lesson(body: LessonCreateBody) -> dict[str, Any]:
        """User-authored lesson ('remember that I prefer…') — injected into
        future runs like any learned one, weighted as an explicit preference."""
        from ...learning.models import LessonRecord

        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        with session_scope(d.platform.engine) as db:
            rec = LessonRecord(text=text[:2000], scope=body.scope or "user",
                               source="preference", weight=3)
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return {"id": rec.id, "text": rec.text}

    @app.post("/memory")
    def memory_write(body: MemoryWrite) -> dict[str, Any]:
        """Write straight into working memory (the layered store agents search).

        THE only POST /memory. A second, richer handler used to be registered
        further down this module; FastAPI dispatches the FIRST match, so that one
        was dead code while ``/openapi.json`` advertised ITS schema (OpenAPI
        generation keeps the last) — ``scope_id`` was documented but silently
        dropped. Merged here: ``scope_id`` is honored, and the layer default
        stays ``"user"`` (what actually shipped) rather than the dead handler's
        ``"project"``, so no caller that omits ``layer`` changes destination.

        ``key`` falls back to "note" when blank and ``text`` is capped, both
        carried over from the handler that was live. The response echoes the
        RECORD, so a substituted key or layer is visible rather than reflected
        back as sent. An unknown layer is a client error (400); anything else
        stays a 500 — a DB failure must not masquerade as bad input.
        """
        try:
            rec = d.platform.memory.write(
                body.layer,
                body.key.strip() or "note",
                (body.text or "").strip()[:8000],
                scope_id=body.scope_id,
            )
        except ValueError as exc:  # unknown layer -> client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "id": rec.id, "layer": rec.layer, "key": rec.key, "scope_id": rec.scope_id,
        }

    @app.delete("/lessons/{lesson_id}")
    def delete_lesson(lesson_id: str) -> dict[str, Any]:
        """Remove one learned lesson — the user curates what sticks."""
        from ...learning.models import LessonRecord

        with session_scope(d.platform.engine) as db:
            r = db.get(LessonRecord, lesson_id)
            if r is None:
                raise HTTPException(status_code=404, detail="no such lesson")
            db.delete(r)
            db.commit()
        return {"deleted": lesson_id}

    @app.get("/improvement")
    def improvement_stats() -> dict[str, Any]:
        """Per-lesson + per-agent outcome stats and quality trend."""
        if d.platform.improvement is None:
            raise HTTPException(status_code=503, detail="improvement engine unavailable")
        return d.platform.improvement.stats()

    @app.post("/improvement/reflect")
    async def improvement_reflect(limit: int = 5) -> dict[str, Any]:
        """Run model reflection over recent low-scoring sessions (on-demand).

        Returns structured suggestions; applies NOTHING (no prompt/lesson/source
        edits). Safe + deterministic offline via the mock model + heuristic fallback.
        """
        if d.platform.improvement is None:
            raise HTTPException(status_code=503, detail="improvement engine unavailable")
        return await d.platform.improvement.reflect(limit=limit)

    @app.get("/memory/search")
    def memory_search(q: str, k: int = 5) -> dict[str, Any]:
        hits = d.platform.memory.search(q, k=k)
        return {
            "results": [
                {"layer": r.layer, "key": r.key, "text": r.text, "score": score}
                for r, score in hits
            ]
        }

    @app.get("/memory/recall")
    def memory_recall(
        q: str,
        k: int = 8,
        project_id: str | None = None,
        sources: str | None = None,
    ) -> dict[str, Any]:
        """Federated recall across EVERY memory store via the Memory Fabric:
        files, notes (LTM), the memory graph, a project's knowledge, lessons,
        and past sessions — ranked + de-duplicated. ``sources`` optionally filters
        by a comma-separated subset (files,notes,memory,knowledge,lessons,sessions)."""
        fabric = getattr(d.platform, "fabric", None)
        if fabric is None:
            raise HTTPException(status_code=503, detail="memory fabric unavailable")
        want = [s.strip() for s in (sources or "").split(",") if s.strip()] or None
        hits = fabric.recall(
            q, k=max(1, min(int(k), 50)), project_id=(project_id or None), sources=want
        )
        by_source: dict[str, int] = {}
        for h in hits:
            by_source[h.source] = by_source.get(h.source, 0) + 1
        return {
            "results": [h.as_dict() for h in hits],
            "by_source": by_source,
            "count": len(hits),
            "query": q,
        }

    # NOTE: /memory/graph* must register BEFORE /memory/{layer}/{key}.
    @app.get("/memory/graph")
    def memory_graph(threshold: float = 0.45) -> dict[str, Any]:
        """The graph view of everything remembered: lessons + working memory +
        enumerable long-term notes as nodes; similarity (shared embedder) and
        user-drawn manual links as edges. Blocked pairs stay hidden."""
        from ...memory.graph import build_memory_graph

        return build_memory_graph(
            d.platform, threshold=max(0.0, min(float(threshold), 1.0))
        )

    @app.post("/memory/graph/node/delete")
    def memory_graph_node_delete(body: GraphNodeDeleteBody) -> dict[str, Any]:
        """Delete the MEMORY behind a graph node (v1.115.0), by composite id.

        Dispatches on the id prefix the graph builder mints:
          lesson:<id>              -> the lesson row
          wm:<layer>:<scope>:<key> -> the working-memory row (scope "-" = None;
                                      the key may itself contain ':' — split 3)
          ltm:<base>:<ref>         -> REFUSED. Long-term notes are files in the
                                      user's memory base (their vault, Notion…);
                                      a canvas click must never reach into those
                                      — the error says where to manage it.
        Either way the node's graph links (manual AND blocks) are cleaned up,
        so a later note with the same key doesn't inherit ghost edges.
        """
        from ...memory.graph import MemoryLinkRecord

        node_id = (body.id or "").strip()
        if not node_id:
            raise HTTPException(status_code=400, detail="node id required")

        deleted = False
        if node_id.startswith("lesson:"):
            from ...learning.models import LessonRecord

            with session_scope(d.platform.engine) as db:
                r = db.get(LessonRecord, node_id.split(":", 1)[1])
                if r is not None:
                    db.delete(r)
                    db.commit()
                    deleted = True
        elif node_id.startswith("wm:"):
            parts = node_id.split(":", 3)
            if len(parts) != 4:
                raise HTTPException(status_code=400, detail=f"malformed node id: {node_id}")
            _, layer, scope, key = parts
            try:
                deleted = d.platform.memory.delete(
                    layer, key, scope_id=None if scope == "-" else scope
                )
            except ValueError as exc:  # unknown layer
                raise HTTPException(status_code=400, detail=str(exc))
        elif node_id.startswith("ltm:"):
            base = node_id.split(":", 2)[1]
            raise HTTPException(
                status_code=400,
                detail=(
                    f"this note lives in the '{base}' memory base — manage it "
                    "there (deleting from the graph would reach into your own "
                    "files)"
                ),
            )
        else:
            raise HTTPException(status_code=400, detail=f"unknown node kind: {node_id}")

        if not deleted:
            raise HTTPException(status_code=404, detail=f"no such node: {node_id}")

        # Sweep this node's edges — manual links AND similarity blocks — so a
        # future item reusing the key starts clean.
        with session_scope(d.platform.engine) as db:
            from sqlmodel import or_, select as _select

            rows = list(
                db.exec(
                    _select(MemoryLinkRecord).where(
                        or_(MemoryLinkRecord.a == node_id, MemoryLinkRecord.b == node_id)
                    )
                )
            )
            for r in rows:
                db.delete(r)
            db.commit()
        return {"deleted": node_id, "links_removed": len(rows)}

    @app.post("/memory/graph/link")
    def memory_graph_link(body: GraphLinkBody) -> dict[str, Any]:
        """User-drawn connection between two nodes (lifts any block)."""
        from ...memory.graph import set_link

        try:
            return set_link(d.platform, body.a.strip(), body.b.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/memory/graph/unlink")
    def memory_graph_unlink(body: GraphLinkBody) -> dict[str, Any]:
        """Disconnect two nodes: manual links are deleted; similarity edges are
        blocked (persisted) so they never reappear."""
        from ...memory.graph import remove_link

        try:
            return remove_link(d.platform, body.a.strip(), body.b.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/memory/{layer}/{key}")
    def memory_read(layer: str, key: str, scope_id: str | None = None) -> dict[str, Any]:
        """Read one working-memory entry. ``scope_id`` addresses the same
        (layer, key, scope) triple POST /memory writes — omitted means the
        unscoped record, since ``_find`` treats None as ``scope_id IS NULL``
        rather than "any scope". Without this param a scoped write would not be
        readable back through the API at all."""
        text = d.platform.memory.read(layer, key, scope_id=scope_id)
        if text is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"layer": layer, "key": key, "text": text, "scope_id": scope_id}
