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

from ..schemas import ChatBody, PersonaCreateBody, PersonaSaveBody
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
            # A thread is tagged to a project ONLY when saved with an explicit
            # project_id (the in-project chat does this). Threads from the main
            # chat stay project-agnostic — no leaking the globally-active one.
            if "project_id" in body:  # explicit tag (or explicit null to clear)
                r.project_id = body.get("project_id") or None
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

    def _persona_store():
        from ...personas import PersonaStore

        return PersonaStore(d.platform.engine)

    @app.get("/chat/personas")
    def chat_personas() -> dict[str, Any]:
        """Every persona — built-ins (with any user override applied) + the user's
        own — each fully editable, carrying its title + prompt."""
        from ...personas import merged

        return {"personas": merged(_persona_store(), d._PERSONAS)}

    @app.put("/chat/personas/{name}")
    def save_persona(name: str, body: PersonaSaveBody) -> dict[str, Any]:
        """Create or update a persona under ``name`` (a built-in name → an
        override; a new name → a new persona). The saved version wins next time."""
        from ...personas import merged, slugify

        slug = slugify(name)
        if not body.prompt.strip():
            raise HTTPException(status_code=400, detail="a persona prompt is required")
        _persona_store().upsert(
            slug,
            title=(body.title.strip() or slug.capitalize()),
            description=body.description.strip(),
            prompt=body.prompt.strip(),
        )
        saved = next(
            (p for p in merged(_persona_store(), d._PERSONAS) if p["name"] == slug), None
        )
        return {"saved": slug, "persona": saved}

    @app.post("/chat/personas")
    def create_persona(body: PersonaCreateBody) -> dict[str, Any]:
        """Create a NEW persona; its id is slugified from ``name`` or ``title``."""
        from ...personas import merged, slugify

        if not body.prompt.strip():
            raise HTTPException(status_code=400, detail="a persona prompt is required")
        slug = slugify(body.name or body.title)
        _persona_store().upsert(
            slug,
            title=(body.title.strip() or slug.capitalize()),
            description=body.description.strip(),
            prompt=body.prompt.strip(),
        )
        saved = next(
            (p for p in merged(_persona_store(), d._PERSONAS) if p["name"] == slug), None
        )
        return {"created": slug, "persona": saved}

    @app.delete("/chat/personas/{name}")
    def delete_persona(name: str) -> dict[str, Any]:
        """Remove a saved persona — reverts a built-in to its default, or deletes
        a custom one. 404 only when the name is neither saved nor a built-in."""
        from ...personas import slugify

        slug = slugify(name)
        removed = _persona_store().delete(slug)
        if not removed and slug not in d._PERSONAS:
            raise HTTPException(status_code=404, detail="no such persona")
        return {"deleted": slug, "reverted_to_builtin": slug in d._PERSONAS}

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

        # Persona: a user override/creation wins, then a built-in, then the value
        # is treated as free-text instructions (used verbatim).
        from ...personas import resolve_prompt

        want = (body.persona or "").strip()
        persona = resolve_prompt(_persona_store(), d._PERSONAS, want)
        system = persona + (
            "\n\n# Environment\n"
            f"- You run locally on the user's machine; their home directory is {Path.home()}.\n"
            "- You are the CHAT surface: answer directly. For multi-step jobs "
            "with tools, the user can switch this conversation to Agent mode."
        )
        # A project only applies INSIDE the Projects module: the in-project chat
        # sends an explicit project_id and grounds in that project's
        # instructions + brief + knowledge. The MAIN chat sends none and stays
        # project-agnostic — the globally "active" project never leaks in here.
        pid = (body.project_id or "").strip() or None
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

        # MEMORY FABRIC: fold in the most relevant snippets from every store
        # (files, notes, memory graph, lessons, past sessions — project
        # knowledge is already injected above when a project is set) so a plain
        # chat turn is grounded in what the user knows, without arming a tool.
        fabric = getattr(d.platform, "fabric", None)
        if fabric is not None:
            last_user = next(
                (m.content or "" for m in reversed(body.messages) if m.role == "user"),
                "",
            )
            if last_user.strip():
                try:
                    grounding = fabric.ground(
                        last_user,
                        project_id=pid,
                        sources=["files", "notes", "memory", "lessons", "sessions"],
                    )
                    if grounding:
                        system += grounding
                except Exception:  # noqa: BLE001 — retrieval must never break a turn
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
                    # Too large to send to vision — be HONEST rather than answering
                    # blind on an image the user thinks was seen (>8 MB is dropped
                    # by every vision API's inline-image cap).
                    _mb = p.stat().st_size / (1024 * 1024)
                    attach_block += (
                        f"\n\n## Attached image: {p.name}\n(NOT analyzed — {_mb:.0f} MB "
                        "exceeds the 8 MB inline-image limit; ask the user to resize "
                        "it or describe what they want from it.)"
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
        tools_used: list[str] = []          # ONLY tools that actually executed
        last_tool_output = ""               # last SUCCESSFUL output (no-reply synthesis)
        denied_tools: list[str] = []        # armed tools the engine refused this turn
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
            # Precedence: an explicit chat WORKSPACE folder (the Build-like panel)
            # wins, then the grounded project root, then the uploads scratch dir.
            ws = (body.workspace_dir or "").strip()
            if ws:
                from ...core.fs_policy import fs_path_allowed, is_protected_path

                wp = Path(ws)
                if (
                    wp.is_absolute()
                    and wp.is_dir()
                    and fs_path_allowed(str(wp))
                    and not is_protected_path(str(wp))
                ):
                    tool_ws, in_project_folder = wp, True
            elif resolved_proj is not None and (resolved_proj.root or "").strip():
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
                    f"\nYour file tools operate INSIDE the folder {tool_ws}; "
                    "read, edit, and create files there directly, and use the absolute paths "
                    "that file_search returns."
                    if in_project_folder
                    else ""
                )
            )
        # Auto-allow keyed by BOTH the tool NAME and its perm_key(): the
        # permission engine authorizes on perm_key(), so for GROUPED tools
        # (pixio_*, view_image / image_*, mcp_*) whose perm_key differs from the
        # name a name-only override never matches — arming them would silently
        # DENY. Keying both hits either lookup.
        overrides: dict[str, str] = {}
        for _name in armed:
            overrides[_name] = "allow"
            _tool = d.platform.registry.get(_name)
            if _tool is not None:
                overrides[_tool.perm_key()] = "allow"
        # Routing: an explicit body choice always wins; otherwise fall back to
        # the resolved project's per-project default model, if it has one.
        provider_choice = (body.provider or "").strip() or (
            (resolved_proj.default_provider or "").strip() if resolved_proj else ""
        )
        model_choice = (body.model or "").strip() or (
            (resolved_proj.default_model or "").strip() if resolved_proj else ""
        )
        # Accumulate token usage + completion count ACROSS the (up to 4) tool
        # rounds so the Usage ledger reflects the WHOLE turn — a multi-round
        # armed-tool turn is several separately-billed completions, not one.
        usage_in = usage_out = completions = 0
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
                _u = route.response.usage or {}
                usage_in += int(_u.get("input_tokens", 0) or 0)
                usage_out += int(_u.get("output_tokens", 0) or 0)
                completions += 1
                calls = route.response.tool_calls or []
                if not calls or not armed:
                    break
                msgs.append(LLMMessage(role="assistant",
                                       content=route.response.text,
                                       tool_calls=calls))
                for tc in calls:
                    ran = False
                    try:
                        result = await d.platform.registry.invoke(
                            tc.name, tc.arguments, ctx, d.platform.permissions, overrides
                        )
                        if result.ok:
                            content = result.output
                            ran = True
                            last_tool_output = str(result.output or "")
                        else:
                            content = result.error or "error"
                            # An honest permission refusal is not "used" — record it
                            # so the reply can note it (a tool-internal failure just
                            # rides back to the model as its tool-message content).
                            if "permission denied" in (result.error or ""):
                                denied_tools.append(tc.name)
                    except Exception as exc:  # noqa: BLE001
                        content = f"{type(exc).__name__}: {exc}"
                    # tools_used counts ONLY tools that actually executed — a denied
                    # or failed call is not honestly reported as run.
                    if ran:
                        tools_used.append(tc.name)
                        # FENCE externally-sourced tool output before the model
                        # sees it — a planted file / web page / memory / PDF can't
                        # inject instructions (the same guard the agent runtime
                        # applies to returns_untrusted_content tools).
                        _t = d.platform.registry.get(tc.name)
                        if getattr(_t, "returns_untrusted_content", False):
                            from ...computeruse.safety import (
                                detect_injection,
                                wrap_untrusted,
                            )

                            _inj = detect_injection(str(content))
                            content = wrap_untrusted(
                                f"[content withheld — suspected {_inj['category']}: "
                                f"{_inj['reason']}]"
                                if _inj["flagged"]
                                else str(content)
                            )
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

            with session_scope(d.platform.engine) as db:
                db.add(AgentRun(
                    session_id="chat",
                    agent_type=AgentType.BUILDER,
                    provider=route.provider,
                    model=route.model,
                    state=AgentState.COMPLETED,
                    steps=max(1, completions),
                    input_tokens=usage_in,
                    output_tokens=usage_out,
                    finished_at=_now(),
                ))
                db.commit()
        except Exception:  # noqa: BLE001 — accounting must never break a reply
            pass
        # Reply honesty: if the model returned no final text but tools DID run
        # with output, synthesize a short summary from the last result rather
        # than the bare "(no reply)" placeholder (which reads like the turn did
        # nothing). Denied armed tools get an honest footer note.
        reply = route.response.text or ""
        if not reply.strip() and last_tool_output:
            snippet = last_tool_output.strip()[:600]
            ran = ", ".join(dict.fromkeys(tools_used)) or "the armed tools"
            reply = f"Ran {ran}. Result:\n{snippet}"
        elif not reply.strip():
            reply = "(no reply)"
        if denied_tools:
            names = ", ".join(dict.fromkeys(denied_tools))
            reply += f"\n\n_Note: {names} could not run (permission denied)._"
        return {
            "reply": reply,
            "provider": route.provider,
            "model": route.model,
            "attached": len(body.attachments or []),
            "images": len(images),
            "skill": (body.skill or "").strip() or None,
            "tools_used": tools_used,
        }
