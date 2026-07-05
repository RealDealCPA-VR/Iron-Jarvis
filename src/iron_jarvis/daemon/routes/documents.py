"""Document routes: uploads, living documents, read/write, enhance.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from sqlmodel import select
from typing import Any

from .. import app as _app
from ..schemas import DocEnhanceBody, DocWriteBody, LiveDocCreate, UploadBody
from ...core.db import session_scope
from ...core.fs_policy import fs_read_ok


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.post("/documents/upload")
    def documents_upload(body: UploadBody) -> dict[str, Any]:
        """Accept a base64 file and store it under <home>/uploads (no multipart dep)."""
        import base64
        import re

        # Cap the decoded size so a giant upload can't OOM-kill the whole daemon
        # (which would take down every session/terminal with it). 4/3 accounts for
        # base64 expansion; reject BEFORE decoding so we never buffer the bytes.
        approx_bytes = (len(body.content_b64) * 3) // 4
        if approx_bytes > _app._MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"upload too large (~{approx_bytes // (1024 * 1024)} MB); "
                    f"limit is {_app._MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                ),
            )
        name = re.sub(r"[^A-Za-z0-9._-]", "_", body.filename).strip("._") or "upload"
        uploads = d.platform.config.home / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / name
        try:
            data = base64.b64decode(body.content_b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
        target.write_bytes(data)
        return {"path": str(target), "name": name, "bytes": len(data)}

    @app.get("/documents/live")
    def list_livedocs() -> dict[str, Any]:
        from ...core.models import LiveDocRecord

        with session_scope(d.platform.engine) as db:
            rows = list(db.exec(select(LiveDocRecord)))
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return {"docs": [r.model_dump() for r in rows]}

    @app.post("/documents/live")
    async def create_livedoc(body: LiveDocCreate) -> dict[str, Any]:
        from ...core.models import LiveDocRecord

        name = (body.name or "").strip()
        if not name or not (body.prompt or "").strip():
            raise HTTPException(status_code=400, detail="name and prompt are required")
        if body.format not in ("md", "html", "docx", "pdf"):
            raise HTTPException(status_code=400, detail="format must be md|html|docx|pdf")
        rec = LiveDocRecord(name=name, prompt=body.prompt.strip(), format=body.format,
                            provider=body.provider, model=body.model)
        with session_scope(d.platform.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
        # Optional auto-refresh: an event-kind schedule the lifespan handler
        # listens for. Manual-only docs simply skip this.
        if body.cron or body.interval_seconds:
            sched_name = f"livedoc_{rec.id}"
            try:
                d.platform.scheduler.add_task(
                    sched_name, body.cron,
                    interval_seconds=body.interval_seconds,
                    kind="event",
                    payload={"type": "livedoc.regenerate", "livedoc_id": rec.id},
                )
                with session_scope(d.platform.engine) as db:
                    row = db.get(LiveDocRecord, rec.id)
                    row.schedule_name = sched_name
                    db.add(row)
                    db.commit()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"bad schedule: {exc}")
        # First generation now, so the doc exists immediately.
        result = await d._regenerate_livedoc(rec.id)
        return {**result, "name": name, "schedule": bool(body.cron or body.interval_seconds)}

    @app.post("/documents/live/{doc_id}/regenerate")
    async def regenerate_livedoc_ep(doc_id: str) -> dict[str, Any]:
        return await d._regenerate_livedoc(doc_id)

    @app.delete("/documents/live/{doc_id}")
    def delete_livedoc(doc_id: str) -> dict[str, Any]:
        """Remove the living doc + its schedule from the APP. The generated
        file stays on disk (never delete the user's files)."""
        from ...core.models import LiveDocRecord

        with session_scope(d.platform.engine) as db:
            row = db.get(LiveDocRecord, doc_id)
            if row is None:
                raise HTTPException(status_code=404, detail="no such living document")
            sched = row.schedule_name
            db.delete(row)
            db.commit()
        if sched:
            try:
                d.platform.scheduler.remove(sched)
            except Exception:  # noqa: BLE001 — schedule may already be gone
                pass
        return {"deleted": doc_id, "files_touched": 0}

    @app.post("/documents/enhance")
    async def enhance_document(body: DocEnhanceBody) -> dict[str, Any]:
        """Suggest a better filename + polished content BEFORE creating —
        returned for review; nothing is written until the user confirms."""
        import json as _json

        from ...providers.adapters.base import LLMMessage

        if not (body.content or "").strip() and not (body.filename or "").strip():
            raise HTTPException(status_code=400, detail="nothing to enhance")
        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
        system = (
            "You polish document drafts. Respond with ONLY JSON: "
            '{"filename": "improved-name.ext (keep/choose a sensible extension)", '
            '"content": "the improved document content (markdown allowed)", '
            '"notes": "1-3 short bullets on what you changed and why"}. '
            "Improve clarity/structure/professional tone; NEVER invent facts or "
            "figures; keep the user's meaning."
        )
        user = f"Filename: {body.filename or '(none)'}\n\nContent:\n{(body.content or '')[:10000]}"
        resp, _p, _m = await d._one_shot_complete(
            provider, adapter, system=system,
            messages=[LLMMessage(role="user", content=user)],
        )
        text = resp.text or ""
        start, depth, obj = text.find("{"), 0, ""
        if start >= 0:
            for i in range(start, len(text)):
                depth += (text[i] == "{") - (text[i] == "}")
                if depth == 0:
                    obj = text[start:i + 1]
                    break
        try:
            out = _json.loads(obj)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="no valid suggestion — try again")
        return {
            "filename": str(out.get("filename") or body.filename),
            "content": str(out.get("content") or body.content),
            "notes": str(out.get("notes") or ""),
        }

    @app.get("/documents/read")
    def documents_read(path: str) -> dict[str, Any]:
        from ...documents import extract_text

        ok, reason = fs_read_ok(path)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        try:
            text = extract_text(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"cannot read: {exc}")
        return {"path": path, "text": text[:20000]}

    @app.post("/documents/write")
    def documents_write(body: DocWriteBody) -> dict[str, Any]:
        from ...documents import write_document

        base = (d.platform.config.home / "documents").resolve()
        target = (base / body.path).resolve()
        if target != base and not target.is_relative_to(base):
            raise HTTPException(status_code=400, detail="path escapes documents dir")
        out = write_document(target, body.content, kind=body.kind)
        return {
            "path": str(out.relative_to(base)).replace("\\", "/"),
            "bytes": out.stat().st_size,
        }
