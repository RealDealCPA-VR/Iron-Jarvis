"""Learning routes: lessons, memory layers, improvement engine.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from typing import Any

from ..schemas import GraphLinkBody, LessonCreateBody, MemoryWrite, MemoryWriteBody
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
    def memory_write(body: MemoryWriteBody) -> dict[str, Any]:
        """Write straight into working memory (the layered store agents search)."""
        try:
            rec = d.platform.memory.write(body.layer, body.key.strip() or "note",
                                        (body.text or "").strip()[:8000])
        except Exception as exc:  # noqa: BLE001 — bad layer etc.
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": rec.id, "layer": body.layer, "key": body.key}

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

    @app.post("/memory")
    def memory_write(body: MemoryWrite) -> dict[str, Any]:
        try:
            rec = d.platform.memory.write(
                body.layer, body.key, body.text, scope_id=body.scope_id
            )
        except ValueError as exc:  # unknown layer -> client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": rec.id, "layer": rec.layer, "key": rec.key}

    @app.get("/memory/{layer}/{key}")
    def memory_read(layer: str, key: str) -> dict[str, Any]:
        text = d.platform.memory.read(layer, key)
        if text is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"layer": layer, "key": key, "text": text}
