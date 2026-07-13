"""Project (context-spine) routes.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from typing import Any

from .. import app as _app
from ..app import _agent_type, _session_view
from ..schemas import (
    PROJECT_TASK_OUTPUTS,
    ProjectCreate,
    ProjectKnowledgeBody,
    ProjectPatch,
    ProjectTaskBody,
    ToolPlanBody,
)
from ...core.db import session_scope


class ProjectKnowledgePatch(BaseModel):
    """Rename (``name``) or edit (``text``) one knowledge item. Only the fields
    provided change; editing ``text`` re-embeds the item (mirrors add_knowledge)."""

    name: str | None = None
    text: str | None = None


def _validate_root(root: str) -> str:
    """Normalise + validate a project folder root. Empty is allowed (a project
    without a folder does chat-only tasks). A NON-empty root must be an absolute
    path to an existing directory — a typo silently degraded every file task,
    Studio launch, and terminal cwd downstream, so reject it HERE with an honest
    error. Returns the cleaned root (empty string when none)."""
    from pathlib import Path

    root = (root or "").strip()
    if not root:
        return ""
    p = Path(root)
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail="folder must be an absolute path")
    if not p.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"folder does not exist on this machine: {root}",
        )
    return str(p)


def _validate_project_model(d, provider: str, model: str) -> None:
    """Reject a per-project default pinned to a provider that isn't installed —
    otherwise every task in the project 400s at run time on a dead provider.
    Model ids churn, so we validate the PROVIDER only (the catchable typo);
    an empty provider clears the pin."""
    provider = (provider or "").strip()
    if not provider:
        return
    try:
        known = {p.get("provider") for p in d.platform.providers.health()}
    except Exception:  # noqa: BLE001 — never let validation crash the patch
        return
    if known and provider not in known:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provider '{provider}' — pick one from the model list",
        )


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/projects")
    def list_projects() -> dict[str, Any]:
        from pathlib import Path

        from sqlalchemy import func

        from ...core.models import Project, ProjectKnowledge
        from ...core.models import Session as SessionModel

        active_id = getattr(d.platform.config, "active_project_id", None)
        with session_scope(d.platform.engine) as db:
            projects = list(db.exec(select(Project)))
            # Grouped counts (one query each) instead of an N+1 SELECT per project.
            # NB: build the dict from .all() rows — dict(result) would trip the
            # SQLAlchemy Result's mapping protocol (it has .keys()).
            sess_counts = {
                pid: cnt
                for pid, cnt in db.exec(
                    select(SessionModel.project_id, func.count()).group_by(
                        SessionModel.project_id
                    )
                ).all()
            }
            know_counts = {
                pid: cnt
                for pid, cnt in db.exec(
                    select(ProjectKnowledge.project_id, func.count()).group_by(
                        ProjectKnowledge.project_id
                    )
                ).all()
            }
        out = []
        for p in projects:
            # root_exists lets the tiles flag a moved/deleted folder BEFORE a task
            # fails on it (the spine is only as good as its folder).
            root_exists = bool(p.root) and Path(p.root).is_dir()
            out.append(
                {
                    **p.model_dump(),
                    "session_count": int(sess_counts.get(p.id, 0)),
                    "knowledge_count": int(know_counts.get(p.id, 0)),
                    "root_exists": root_exists,
                    "active": p.id == active_id,
                }
            )
        out.sort(key=lambda x: str(x.get("created_at")), reverse=True)
        return {"projects": out}

    @app.post("/projects")
    def create_project(body: ProjectCreate) -> dict[str, Any]:
        from ...core.models import Project

        name = (body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="project name is required")
        root = _validate_root(body.root or "")  # honest 400 on a bad folder
        project = Project(name=name, brief=(body.brief or "").strip(), root=root)
        with session_scope(d.platform.engine) as db:
            db.add(project)
            db.commit()
            db.refresh(project)
        # First project with nothing active -> make it active, so the spine
        # starts working immediately (chat/Spotlight tag into it from now on).
        activated = False
        if not getattr(d.platform.config, "active_project_id", None):
            d.platform.config.active_project_id = project.id
            d._persist_config(["active_project_id"])
            activated = True
        return {**project.model_dump(), "active": activated}

    @app.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        from pathlib import Path

        from ...core.models import Project
        from ...core.models import Session as SessionModel

        with session_scope(d.platform.engine) as db:
            project = db.get(Project, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="no such project")
            sessions = list(
                db.exec(
                    select(SessionModel)
                    .where(SessionModel.project_id == project_id)
                    .order_by(SessionModel.created_at.desc())  # type: ignore[attr-defined]
                    .limit(20)
                )
            )
        return {
            "project": {
                **project.model_dump(),
                # root_exists lets the workspace warn about a moved/deleted folder
                # BEFORE a task fails on it (mirrors the tiles in list_projects).
                "root_exists": bool(project.root) and Path(project.root).is_dir(),
                "active": project.id == getattr(d.platform.config, "active_project_id", None),
            },
            "sessions": [_session_view(s) for s in sessions],
        }

    @app.patch("/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch) -> dict[str, Any]:
        from ...core.models import Project

        if body.status is not None and body.status not in ("active", "archived"):
            raise HTTPException(status_code=400, detail="status must be active|archived")
        with session_scope(d.platform.engine) as db:
            project = db.get(Project, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="no such project")
            if body.name is not None and body.name.strip():
                project.name = body.name.strip()
            if body.brief is not None:
                project.brief = body.brief.strip()
            if body.root is not None:
                project.root = _validate_root(body.root)  # honest 400 on a bad folder
            if body.status is not None:
                project.status = body.status
            if body.instructions is not None:
                project.instructions = body.instructions.strip()
            if body.default_provider is not None:
                _validate_project_model(
                    d,
                    body.default_provider,
                    body.default_model
                    if body.default_model is not None
                    else project.default_model,
                )
                project.default_provider = body.default_provider.strip()
            if body.default_model is not None:
                project.default_model = body.default_model.strip()
            db.add(project)
            db.commit()
            db.refresh(project)
        # Archiving the ACTIVE project deactivates it (new work shouldn't tag
        # into something the user closed out).
        if project.status == "archived" and (
            getattr(d.platform.config, "active_project_id", None) == project_id
        ):
            d.platform.config.active_project_id = None
            d._persist_config(["active_project_id"])
        return project.model_dump()

    @app.delete("/projects/{project_id}")
    def delete_project(project_id: str) -> dict[str, Any]:
        """Remove a project from Iron Jarvis ONLY — the folder it pointed at
        and every file on disk are untouched (the root is just a reference).
        Sessions that were tagged to it keep their history; they simply lose
        the project association."""
        from ...core.models import ChatThreadRecord, Project, ProjectKnowledge
        from ...core.models import Session as SessionModel
        from ...workflows.models import WorkflowRunRecord

        knowledge_deleted = 0
        with session_scope(d.platform.engine) as db:
            proj = db.get(Project, project_id)
            if proj is None:
                raise HTTPException(status_code=404, detail="no such project")
            # Untag sessions, chat threads, and workflow runs (history preserved,
            # association dropped) — otherwise their project_id dangles at a
            # project that no longer exists and the UI 404s following it.
            for model in (SessionModel, ChatThreadRecord, WorkflowRunRecord):
                for row in db.exec(select(model).where(model.project_id == project_id)):
                    row.project_id = None
                    db.add(row)
            # Knowledge belongs TO the project — delete it (no home without it).
            for k in db.exec(
                select(ProjectKnowledge).where(ProjectKnowledge.project_id == project_id)
            ):
                db.delete(k)
                knowledge_deleted += 1
            db.delete(proj)
            db.commit()
        if getattr(d.platform.config, "active_project_id", None) == project_id:
            d.platform.config.active_project_id = None
            d._persist_config(["active_project_id"])
        return {
            "deleted": project_id,
            "files_touched": 0,
            "knowledge_deleted": knowledge_deleted,
        }

    @app.post("/projects/{project_id}/activate")
    def activate_project(project_id: str) -> dict[str, Any]:
        from ...core.models import Project

        with session_scope(d.platform.engine) as db:
            project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="no such project")
        if project.status != "active":
            raise HTTPException(status_code=400, detail="unarchive the project first")
        d.platform.config.active_project_id = project_id
        d._persist_config(["active_project_id"])
        return {"active_project_id": project_id, "name": project.name}

    @app.post("/projects/deactivate")
    def deactivate_project() -> dict[str, Any]:
        d.platform.config.active_project_id = None
        d._persist_config(["active_project_id"])
        return {"active_project_id": None}

    @app.post("/projects/{project_id}/knowledge")
    def add_project_knowledge(project_id: str, body: ProjectKnowledgeBody) -> dict[str, Any]:
        """Add a knowledge item to a project — the Claude-Projects-style
        grounding substrate. A pasted note (``text``) is stored verbatim; a file
        (``content_b64``) is decoded, its text extracted server-side, and stored
        (kind='file'). Every item is embedded on write and injected into this
        project's chats/tasks. 400 if BOTH text and content_b64 are empty."""
        import base64
        import re
        from pathlib import Path

        from ...core.models import Project
        from ...documents.readers import extract_text
        from ...projects.knowledge import add_knowledge

        with session_scope(d.platform.engine) as db:
            if db.get(Project, project_id) is None:
                raise HTTPException(status_code=404, detail="no such project")

        b64 = (body.content_b64 or "").strip()
        note = (body.text or "").strip()
        if not b64 and not note:
            raise HTTPException(
                status_code=400, detail="text or content_b64 is required"
            )

        if b64:
            # Reject an oversized upload BEFORE decoding so we never buffer the
            # bytes (4/3 accounts for base64 expansion) — same guard as
            # /documents/upload and /ltm/ingest-document.
            approx_bytes = (len(b64) * 3) // 4
            if approx_bytes > _app._MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"file too large (~{approx_bytes // (1024 * 1024)} MB); "
                        f"limit is {_app._MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                    ),
                )
            try:
                data = base64.b64decode(b64, validate=False)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
            if not data:
                raise HTTPException(status_code=400, detail="empty file")
            # extract_text dispatches by suffix, so the decoded bytes need to live
            # in a real file with the original name/extension first.
            safe = (
                re.sub(r"[^A-Za-z0-9._-]", "_", body.filename or "").strip("._")
                or "upload"
            )
            uploads = d.platform.config.home / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            target = uploads / safe
            target.write_bytes(data)
            try:
                extracted = (extract_text(target) or "").strip()
            except Exception as exc:  # noqa: BLE001 — report the real reason, don't fabricate
                raise HTTPException(
                    status_code=400, detail=f"could not read {safe}: {exc}"
                )
            if not extracted:
                raise HTTPException(
                    status_code=400, detail=f"no extractable text in {safe}"
                )
            item_text = extracted
            item_name = (body.name or body.filename or safe).strip() or safe
            kind = "file"
        else:
            item_text = note
            first_line = note.splitlines()[0] if note.splitlines() else note
            item_name = (body.name or first_line).strip() or "note"
            kind = "note"

        try:
            rec = add_knowledge(
                d.platform, project_id, item_name, item_text, kind=kind
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": rec.id, "name": rec.name, "kind": rec.kind, "size": rec.size}

    @app.get("/projects/{project_id}/knowledge")
    def list_project_knowledge(project_id: str) -> dict[str, Any]:
        """Metadata for every knowledge item in the project (newest first)."""
        from ...core.models import Project
        from ...projects.knowledge import list_knowledge

        with session_scope(d.platform.engine) as db:
            if db.get(Project, project_id) is None:
                raise HTTPException(status_code=404, detail="no such project")
        items = list_knowledge(d.platform, project_id)
        return {"knowledge": items, "count": len(items)}

    @app.get("/projects/{project_id}/knowledge/{knowledge_id}")
    def get_project_knowledge(
        project_id: str, knowledge_id: str
    ) -> dict[str, Any]:
        """Full text (+ name/kind) of one knowledge item, for the viewer. 404 if
        it doesn't belong to the project (never leak another project's item)."""
        from ...core.models import ProjectKnowledge

        with session_scope(d.platform.engine) as db:
            rec = db.get(ProjectKnowledge, knowledge_id)
            if rec is None or rec.project_id != project_id:
                raise HTTPException(status_code=404, detail="no such knowledge item")
            return {
                "id": rec.id,
                "name": rec.name,
                "kind": rec.kind,
                "text": rec.text,
                "size": rec.size,
                "created_at": rec.created_at.isoformat() if rec.created_at else None,
            }

    @app.patch("/projects/{project_id}/knowledge/{knowledge_id}")
    def patch_project_knowledge(
        project_id: str, knowledge_id: str, body: ProjectKnowledgePatch
    ) -> dict[str, Any]:
        """Rename and/or edit one knowledge item. Editing text RE-EMBEDS it so
        retrieval stays accurate (mirrors add_knowledge). 404 if it isn't in the
        project; 400 if an edit blanks the text."""
        import json

        from ...core.models import ProjectKnowledge
        from ...projects.knowledge import _embed

        with session_scope(d.platform.engine) as db:
            rec = db.get(ProjectKnowledge, knowledge_id)
            if rec is None or rec.project_id != project_id:
                raise HTTPException(status_code=404, detail="no such knowledge item")
            if body.name is not None and body.name.strip():
                rec.name = body.name.strip()[:200]
            if body.text is not None:
                new_text = body.text.strip()
                if not new_text:
                    raise HTTPException(status_code=400, detail="knowledge text is empty")
                if new_text != rec.text:
                    embedder = getattr(d.platform, "embedder", None)
                    rec.text = new_text
                    rec.size = len(new_text)
                    rec.embedding_json = json.dumps(_embed(embedder, new_text))
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return {
                "id": rec.id,
                "name": rec.name,
                "kind": rec.kind,
                "text": rec.text,
                "size": rec.size,
            }

    @app.delete("/projects/{project_id}/knowledge/{knowledge_id}")
    def delete_project_knowledge(
        project_id: str, knowledge_id: str
    ) -> dict[str, Any]:
        """Remove one knowledge item. 404 if it doesn't belong to the project."""
        from ...projects.knowledge import remove_knowledge

        if not remove_knowledge(d.platform, project_id, knowledge_id):
            raise HTTPException(status_code=404, detail="no such knowledge item")
        return {"deleted": knowledge_id}

    @app.post("/projects/{project_id}/task")
    async def run_project_task(project_id: str, body: ProjectTaskBody) -> dict[str, Any]:
        """Plain-text task INSIDE the project's folder, with a chosen
        deliverable: 'chat' = the answer lands in the session summary; any file
        output = the agent writes it into the folder with write_document
        (markdown structure becomes real docx/pdf/pptx/html structure; rows
        become real xlsx/csv cells). Returns the STARTED session (flat, like
        POST /sessions with wait:false) plus target_path for file outputs."""
        import re
        from pathlib import Path

        from ...core.models import Project

        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        output = (body.output or "chat").strip().lower()
        if output not in PROJECT_TASK_OUTPUTS:
            raise HTTPException(
                status_code=400,
                detail=f"output must be one of: {', '.join(PROJECT_TASK_OUTPUTS)}",
            )
        with session_scope(d.platform.engine) as db:
            project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="no such project")
        root = Path(project.root) if project.root else None
        if output != "chat" and (root is None or not root.is_dir()):
            # A root that IS set but points at a missing/moved folder is a
            # different failure than never setting one — say so, so the user
            # fixes the right thing instead of hunting for a setting they already
            # filled in.
            if project.root:
                detail = (
                    f"This project's folder no longer exists: {project.root}. "
                    "Update it on the project page."
                )
            else:
                detail = (
                    "a file deliverable needs the project to have a folder — "
                    "set one on the Projects page first"
                )
            raise HTTPException(status_code=400, detail=detail)

        in_folder = root is not None and root.is_dir()
        lines = [f"Task: {text}", ""]
        if in_folder:
            # The session RUNS IN this folder (it's the workspace) — the agent
            # reads/writes it directly with plain filenames.
            lines.insert(
                0,
                "You are working directly inside the project folder — it is your "
                "current directory. Read and create files here with plain "
                "relative paths.",
            )
        # NOTE: the project's custom instructions, brief, KNOWLEDGE grounding,
        # and recent-activity are injected by the runtime's _project_context for
        # EVERY project-tagged session (this task's session is one). We must NOT
        # repeat them here — doing so duplicated the whole knowledge base into
        # the prompt and doubled the token cost of every project task.
        target_path: str | None = None
        rel_name: str | None = None
        if output == "chat":
            lines.append(
                "Deliverable: a clear, complete written answer in your final "
                "summary — the summary IS the deliverable. Don't create files "
                "unless the task itself requires them."
            )
        else:
            stem = Path(body.filename or "").stem.strip()
            if not stem:
                stem = re.sub(r"[^A-Za-z0-9]+", "-", text[:40]).strip("-").lower() or "task"
            rel_name = f"{stem}.{output}"
            target_path = str(root / rel_name)
            lines.append(
                f"Deliverable: write the result to '{rel_name}' (in this folder) "
                "using the write_document tool — markdown headings/lists/tables "
                "become real structure in docx/pdf/pptx/html; pass a list of "
                "rows for xlsx/csv. State the saved file in your final summary."
            )
        lines.append(
            "Work autonomously to completion — make reasonable choices instead "
            "of asking questions."
        )
        # The task body carries no provider/model, so a project that pins its own
        # default_provider/default_model steers every task it runs (None = the
        # global default, i.e. the prior behavior).
        provider = (project.default_provider or "").strip() or None
        model = (project.default_model or "").strip() or None
        try:
            session = await d.orchestrator.create_session(
                "\n".join(lines),
                _agent_type("builder"),
                provider,
                model=model,
                project_id=project.id,
                allow_tools=body.allow_tools or None,
                workspace_root=str(root) if in_folder else None,
            )
        except (PermissionError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        d._spawn_bg(session.id, d.orchestrator.run_session(session.id))
        view = _session_view(session)
        view["output"] = output
        if target_path:
            view["target_path"] = target_path
        return view

    @app.get("/projects/{project_id}/deliverable")
    def check_deliverable(project_id: str, path: str) -> dict[str, Any]:
        """Does a task's file deliverable ACTUALLY exist on disk? The task strip
        used to claim 'Saved: <path>' from the INTENDED path — fabricated success
        when the agent never wrote it. This lets the UI verify the real file
        (and show its real size) instead. Constrained to the project's folder."""
        from pathlib import Path

        from ...core.fs_policy import fs_read_ok
        from ...core.models import Project

        with session_scope(d.platform.engine) as db:
            project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="no such project")
        root = Path(project.root) if project.root else None
        if root is None:
            raise HTTPException(status_code=400, detail="project has no folder")
        p = Path((path or "").strip())
        if not p.is_absolute():
            raise HTTPException(status_code=400, detail="absolute path required")
        # Never stat arbitrary disk — the target must live under the project root.
        try:
            inside = p == root or root.resolve() in p.resolve().parents
        except OSError:
            inside = False
        if not inside:
            raise HTTPException(status_code=400, detail="path is outside the project folder")
        ok, reason = fs_read_ok(str(p))
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        if p.is_file():
            return {"exists": True, "size": p.stat().st_size, "path": str(p)}
        return {"exists": False, "size": 0, "path": str(p)}

    @app.post("/projects/{project_id}/task/plan")
    async def plan_project_task(project_id: str, body: ToolPlanBody) -> dict[str, Any]:
        """Ask the model which registry tools a plain-text task will likely
        need, so the UI can request permission for the WHOLE bundle at once
        instead of prompting per tool mid-run. Honest, best-effort: returns an
        empty plan (task still runnable) when no real model can answer."""
        import json as _json

        from ...core.models import Project
        from ...providers.adapters.base import LLMMessage

        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        with session_scope(d.platform.engine) as db:
            if db.get(Project, project_id) is None:
                raise HTTPException(status_code=404, detail="no such project")

        # Only tools whose DEFAULT mode is 'ask' are worth bundling — allow
        # tools already run, deny tools never will. Map name -> perm_key so the
        # grant the UI sends back matches what the permission engine checks.
        specs = d.platform.registry.specs()
        askable: dict[str, dict[str, str]] = {}
        for spec in specs:
            name = spec.get("name")
            tool = d.platform.registry.get(name) if name else None
            if tool is None:
                continue
            pk = tool.perm_key()
            if d.platform.permissions.mode_for(pk).value != "ask":
                continue
            askable[name] = {"perm_key": pk, "description": spec.get("description", "")}
        if not askable:
            return {"tools": [], "note": "no permissioned tools needed"}

        adapter, used = d._failover_adapter("mock")
        if adapter is None:
            return {"tools": [], "note": "connect a model to auto-detect tool needs"}
        catalog = "\n".join(f"- {n}: {v['description'][:120]}" for n, v in askable.items())
        system = (
            "You choose which tools an agent will need for a task. Reply with "
            "ONLY a JSON array of the tool NAMES (from the catalog) the task "
            "will actually use — omit tools it won't. Be minimal but complete."
        )
        prompt = f"Task: {text}\n\nTool catalog:\n{catalog}"
        try:
            resp, _, _ = await d._one_shot_complete(
                used, adapter, system=system,
                messages=[LLMMessage(role="user", content=prompt)],
            )
            picked = _json.loads((resp.text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            names = [str(n) for n in picked if str(n) in askable] if isinstance(picked, list) else []
        except Exception:  # noqa: BLE001 — a bad plan must never block the task
            names = []
        tools = [
            {"name": n, "perm_key": askable[n]["perm_key"], "why": askable[n]["description"][:100]}
            for n in dict.fromkeys(names)  # de-dupe, keep order
        ]
        return {"tools": tools}
