"""Direct-chat routes: /chat, threads, personas.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from pathlib import Path
from sqlmodel import select
from typing import Any

from ..schemas import ChatBody
from ...core.db import session_scope
from ...core.fs_policy import fs_read_ok
from ...core.models import AgentType


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/chat/threads")
    def chat_threads(project_id: str = "") -> dict[str, Any]:
        """List saved threads (newest first). ``project_id`` (optional) scopes
        the list to ONE project's conversations — the in-project workspace fetches
        only its own threads; empty returns every thread (unchanged behavior)."""
        from ...core.models import ChatThreadRecord

        pid = (project_id or "").strip()
        with session_scope(d.platform.engine) as db:
            stmt = select(ChatThreadRecord)
            if pid:
                stmt = stmt.where(ChatThreadRecord.project_id == pid)
            rows = list(db.exec(stmt))
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        out = []
        for r in rows[:100]:
            try:
                count = len(json.loads(r.messages_json or "[]"))
            except Exception:  # noqa: BLE001
                count = 0
            out.append(
                {"id": r.id, "title": r.title or "(untitled)",
                 "persona": r.persona, "messages": count,
                 "project_id": r.project_id,
                 "updated_at": r.updated_at.isoformat()}
            )
        return {"threads": out}

    @app.get("/chat/threads/{thread_id}")
    def chat_thread(thread_id: str) -> dict[str, Any]:
        from ...core.models import ChatThreadRecord

        with session_scope(d.platform.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
        if r is None:
            raise HTTPException(status_code=404, detail="no such thread")
        try:
            msgs = json.loads(r.messages_json or "[]")
        except Exception:  # noqa: BLE001
            msgs = []
        return {
            "id": r.id, "title": r.title, "persona": r.persona,
            "project_id": r.project_id, "messages": msgs,
        }

    @app.put("/chat/threads/{thread_id}")
    def save_chat_thread(thread_id: str, body: dict) -> dict[str, Any]:
        """Upsert a thread (the chat autosaves after every turn). Send
        {messages, title?, persona?, project_id?}; 'new' as the id creates a
        thread — stamped with the ACTIVE project (the context spine) unless the
        body names one explicitly."""
        from ...core.ids import utcnow as _now
        from ...core.models import ChatThreadRecord

        msgs = body.get("messages")
        if not isinstance(msgs, list):
            raise HTTPException(status_code=400, detail="messages list required")
        with session_scope(d.platform.engine) as db:
            r = None if thread_id == "new" else db.get(ChatThreadRecord, thread_id)
            created = r is None
            if r is None:
                r = ChatThreadRecord()
            # Auto-title from the first user message when none is set.
            title = (body.get("title") or r.title or "").strip()
            if not title:
                first = next(
                    (m.get("content", "") for m in msgs if m.get("role") == "user"), ""
                )
                title = (first[:48] + ("…" if len(first) > 48 else "")) or "New chat"
            r.title = title
            r.persona = str(body.get("persona") or r.persona or "")
            if "project_id" in body:  # explicit tag (or explicit null to clear)
                r.project_id = body.get("project_id") or None
            elif created:  # new conversations inherit the active project
                r.project_id = getattr(d.platform.config, "active_project_id", None)
            r.messages_json = json.dumps(msgs[-200:])
            r.updated_at = _now()
            db.add(r)
            db.commit()
            db.refresh(r)
        return {"id": r.id, "title": r.title, "project_id": r.project_id}

    @app.delete("/chat/threads/{thread_id}")
    def delete_chat_thread(thread_id: str) -> dict[str, Any]:
        from ...core.models import ChatThreadRecord

        with session_scope(d.platform.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
            if r is None:
                raise HTTPException(status_code=404, detail="no such thread")
            db.delete(r)
            db.commit()
        return {"deleted": thread_id}

    @app.get("/chat/personas")
    def chat_personas() -> dict[str, Any]:
        return {
            "personas": [
                {"name": k, "description": v["description"]}
                for k, v in d._PERSONAS.items()
            ]
        }

    @app.post("/chat")
    async def chat_complete(body: ChatBody) -> dict[str, Any]:
        """One conversational turn: full history in → one reply out.

        DIRECT completion through the router (retry + failover included) — no
        agent loop, no workspace, so replies come back in seconds and read like
        a chat, not a work summary. Personas + file attachments (text extracted;
        images passed to vision) + active-project context all fold into the
        system prompt.
        """
        from ...providers.adapters.base import LLMMessage

        if not body.messages:
            raise HTTPException(status_code=400, detail="messages is required")

        # Persona: builtin name, or free text used verbatim.
        want = (body.persona or "").strip()
        persona = (
            d._PERSONAS[want]["prompt"]
            if want in d._PERSONAS
            else (want or d._PERSONAS["assistant"]["prompt"])
        )
        system = persona + (
            "\n\n# Environment\n"
            f"- You run locally on the user's machine; their home directory is {Path.home()}.\n"
            "- You are the CHAT surface: answer directly. For multi-step jobs "
            "with tools, the user can switch this conversation to Agent mode."
        )
        # Context spine: ground THIS turn in a SPECIFIC project (body.project_id)
        # or fall back to the globally-active one. An in-project conversation
        # carries its OWN instructions + brief + knowledge, independent of what's
        # active elsewhere — explicit project_id always wins.
        pid = (body.project_id or "").strip() or getattr(
            d.platform.config, "active_project_id", None
        )
        resolved_proj = None
        if pid:
            try:
                from ...core.models import Project

                with session_scope(d.platform.engine) as db:
                    resolved_proj = db.get(Project, pid)
            except Exception:  # noqa: BLE001 — never block a chat turn
                resolved_proj = None
        if resolved_proj is not None:
            block = f"\n\n# Project: {resolved_proj.name}"
            instructions = (resolved_proj.instructions or "").strip()
            if instructions:
                block += f"\n\nInstructions (follow these):\n{instructions[:2000]}"
            if resolved_proj.brief:
                block += f"\n\nAbout this project: {resolved_proj.brief[:1500]}"
            # Knowledge keyed off THIS turn's question (the last user message);
            # ground() retrieves the relevant items. Never let it break a turn.
            query = next(
                (m.content or "" for m in reversed(body.messages) if m.role == "user"),
                "",
            )
            try:
                from ...projects.knowledge import ground

                knowledge = ground(d.platform, pid, query)
                if knowledge:
                    block += f"\n\nProject knowledge (reference):\n{knowledge}"
            except Exception:  # noqa: BLE001 — retrieval must never break a chat turn
                pass
            system += block

        # Self-correction: fold accumulated lessons + user preferences into the
        # system prompt so the chat surface gets a little smarter every turn
        # too (same injection the agent runtime does). Never blocks a turn.
        learning = getattr(d.platform, "learning", None)
        if learning is not None:
            try:
                system = learning.apply_to_prompt(system)
            except Exception:  # noqa: BLE001 — never block a chat turn
                pass

        # Attachments: text formats extracted inline; images go to VISION.
        images: list[dict[str, str]] = []
        attach_block = ""
        _IMG = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif"}
        for raw in (body.attachments or [])[:4]:
            p = Path(raw)
            if not p.is_absolute():
                p = d.platform.config.home / "uploads" / p.name
            ok, _reason = fs_read_ok(str(p))
            if not ok or not p.is_file():
                continue
            suffix = p.suffix.lower()
            if suffix in _IMG:
                import base64 as _b64

                if p.stat().st_size <= 8 * 1024 * 1024:
                    images.append(
                        {"data_b64": _b64.b64encode(p.read_bytes()).decode("ascii"),
                         "media_type": _IMG[suffix]}
                    )
            else:
                try:
                    from ...documents.readers import extract_text

                    text = extract_text(p)[:6000]
                    attach_block += f"\n\n## Attached file: {p.name}\n{text}"
                except Exception as exc:  # noqa: BLE001
                    attach_block += f"\n\n## Attached file: {p.name}\n(could not read: {exc})"
        if attach_block:
            system += "\n\n# Attachments (provided by the user this turn)" + attach_block

        # "/" skill invocation: the chosen skill's playbook rides the system
        # prompt (provider-agnostic, same as the terminal assist).
        if (body.skill or "").strip():
            sk = d.platform.skills.get(body.skill.strip())
            if sk is None:
                raise HTTPException(status_code=404, detail=f"no such skill: {body.skill}")
            system += (
                f"\n\n# Skill invoked by the user: {sk.name}\n"
                "FOLLOW this playbook for this request.\n" + sk.instructions[:8000]
            )

        # Full multi-turn history (bounded), images ride on the LAST user turn.
        msgs: list[LLMMessage] = []
        for m in body.messages[-30:]:
            role = m.role if m.role in ("user", "assistant") else "user"
            msgs.append(LLMMessage(role=role, content=(m.content or "")[:12000]))
        if images and msgs:
            for m in reversed(msgs):
                if m.role == "user":
                    m.images = images
                    break

        # "+" armed tools: a SMALL tool loop (max 4 rounds) with exactly the
        # tools the user selected — auto-allowed because arming them WAS the
        # user's explicit consent for this conversation.
        armed = [t for t in (body.tools or [])[:6] if d.platform.registry.get(t)]
        tool_specs = d.platform.registry.specs(armed) if armed else []
        tools_used: list[str] = []
        if armed:
            from ...tools.base import ToolContext

            # Run the tools IN the grounded project's folder when it has one, so
            # read_file / list_files / edit_file / write_document reach the
            # user's REAL files (file_search returns their absolute paths, which
            # then resolve inside this workspace). Without this the tools confine
            # to a throwaway scratch dir and every read of a project file fails
            # with "escapes the session workspace". Confinement still holds — the
            # tools cannot escape the chosen folder.
            tool_ws = d.platform.config.home / "uploads"
            in_project_folder = False
            if resolved_proj is not None and (resolved_proj.root or "").strip():
                proot = Path(resolved_proj.root)
                if proot.is_dir():
                    tool_ws, in_project_folder = proot, True
            tool_ws.mkdir(parents=True, exist_ok=True)
            ctx = ToolContext(
                workspace=tool_ws, session_id="chat", agent_run_id="chat",
                config=d.platform.config, event_bus=d.platform.event_bus,
                engine=d.platform.engine,
            )
            system += (
                "\n\n# Tools\nThe user armed these tools for this chat: "
                + ", ".join(armed)
                + ". Use them when they help; answer directly when they don't."
                + (
                    f"\nYour file tools operate INSIDE the project folder ({resolved_proj.root}); "
                    "read, edit, and create files there directly, and use the absolute paths "
                    "that file_search returns."
                    if in_project_folder
                    else ""
                )
            )
        overrides = {t: "allow" for t in armed}
        # Routing: an explicit body choice always wins; otherwise fall back to
        # the resolved project's per-project default model, if it has one.
        provider_choice = (body.provider or "").strip() or (
            (resolved_proj.default_provider or "").strip() if resolved_proj else ""
        )
        model_choice = (body.model or "").strip() or (
            (resolved_proj.default_model or "").strip() if resolved_proj else ""
        )
        try:
            for _round in range(4):
                route = await d.platform.router.complete(
                    provider=provider_choice or None,
                    model=model_choice or None,
                    system=system,
                    messages=msgs,
                    tools=tool_specs,
                    task_class="chat",
                )
                calls = route.response.tool_calls or []
                if not calls or not armed:
                    break
                msgs.append(LLMMessage(role="assistant",
                                       content=route.response.text,
                                       tool_calls=calls))
                for tc in calls:
                    tools_used.append(tc.name)
                    try:
                        result = await d.platform.registry.invoke(
                            tc.name, tc.arguments, ctx, d.platform.permissions, overrides
                        )
                        content = result.output if result.ok else (result.error or "error")
                    except Exception as exc:  # noqa: BLE001
                        content = f"{type(exc).__name__}: {exc}"
                    msgs.append(LLMMessage(role="tool", tool_call_id=tc.id,
                                           name=tc.name, content=str(content)[:12000]))
        except Exception as exc:  # noqa: BLE001 — honest, human error
            raise HTTPException(status_code=502, detail=str(exc))
        # USAGE LEDGER: direct chat turns must count like agent runs, or the
        # Usage page under-reports the user's main surface. Persist a run row
        # (session_id "chat") with the adapters' reported token usage.
        try:
            from ...core.ids import utcnow as _now
            from ...core.models import AgentRun, AgentState

            usage = route.response.usage or {}
            with session_scope(d.platform.engine) as db:
                db.add(AgentRun(
                    session_id="chat",
                    agent_type=AgentType.BUILDER,
                    provider=route.provider,
                    model=route.model,
                    state=AgentState.COMPLETED,
                    steps=1,
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    finished_at=_now(),
                ))
                db.commit()
        except Exception:  # noqa: BLE001 — accounting must never break a reply
            pass
        return {
            "reply": route.response.text or "(no reply)",
            "provider": route.provider,
            "model": route.model,
            "attached": len(body.attachments or []),
            "images": len(images),
            "skill": (body.skill or "").strip() or None,
            "tools_used": tools_used,
        }
