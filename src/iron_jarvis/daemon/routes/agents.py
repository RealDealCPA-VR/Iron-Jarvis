"""Agent/tool routes: registry, skills, custom tools, MCP, dynamic agents.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from typing import Any

from ..app import _session_view
from ..schemas import (
    AgentCreate,
    AgentPatch,
    CustomToolCreate,
    McpServerBody,
    McpSuggestBody,
    RemoteAgentCreate,
    RemoteAgentRun,
    SkillApplyBody,
    SkillCreate,
    SpawnBody,
    ToolGenerateBody,
)

# Importing this registers the RemoteAgentRecord table on the shared metadata.
from ...agents.remote import RemoteAgentRegistry


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/tools")
    def tools() -> dict[str, Any]:
        return {"tools": d.platform.registry.specs()}

    @app.post("/skills/{name}/apply")
    async def apply_skill(name: str, body: SkillApplyBody) -> dict[str, Any]:
        """USE a skill right here: the skill's full instructions + the user's
        request go to the model in one shot (retry/failover included) and the
        result comes straight back — no session plumbing."""
        sk = d.platform.skills.get(name)
        if sk is None:
            raise HTTPException(status_code=404, detail="no such skill")
        if not (body.request or "").strip():
            raise HTTPException(status_code=400, detail="request is required")
        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
        from ...providers.adapters.base import LLMMessage

        system = (
            "Fulfil the user's request by FOLLOWING this skill playbook exactly.\n\n"
            f"# Skill: {sk.name}\n{sk.instructions[:8000]}"
        )
        resp, used_provider, used_model = await d._one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[LLMMessage(role="user", content=body.request.strip()[:6000])],
        )
        return {
            "reply": resp.text or "(no reply)",
            "skill": sk.name,
            "provider": used_provider,
            "model": used_model,
        }

    @app.get("/skills")
    def skills() -> dict[str, Any]:
        items = [
            {"name": s.name, "description": s.description, "source": s.source}
            for s in d.platform.skills.list()
        ]
        # A per-source tally so the dashboard can show "12 Claude · 8 Codex · …".
        counts: dict[str, int] = {}
        for it in items:
            counts[it["source"]] = counts.get(it["source"], 0) + 1
        return {"skills": items, "counts": counts}

    @app.get("/skills/{name}")
    def skill(name: str) -> dict[str, Any]:
        sk = d.platform.skills.get(name)
        if sk is None:
            raise HTTPException(status_code=404, detail="no such skill")
        return {
            "name": sk.name,
            "description": sk.description,
            "instructions": sk.instructions,
            "source": sk.source,
        }

    @app.post("/skills/rescan")
    def rescan_skills() -> dict[str, Any]:
        """Re-scan every source (builtin + user + Claude + Codex + extra paths)
        so newly-added external skills show up without restarting the daemon."""
        counts = d._rescan_skills()
        return {"total": sum(counts.values()), "counts": counts}

    @app.post("/skills")
    def create_skill(body: SkillCreate) -> dict[str, Any]:
        """Author a new skill (name + description + instructions).

        Persists ``<home>/skills/<slug>/SKILL.md`` and re-scans so it shows up
        immediately — user skills sit alongside the built-ins and the pulled-in
        Claude/Codex skills, searchable/injectable by agents the same way.
        """
        from ...skills import save_skill as _save_skill

        try:
            _save_skill(
                d.platform.config.home / "skills",
                body.name,
                body.description,
                body.instructions,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # Re-scan so the new skill (and any external ones) are live without a restart.
        d._rescan_skills()
        sk = d.platform.skills.get(body.name.strip())
        return {"name": sk.name if sk else body.name, "created": True}

    @app.get("/agents")
    def list_agents() -> dict[str, Any]:
        import json as _json

        from ...agents.types import _DEFINITIONS

        return {
            "builtin": [t.value for t in _DEFINITIONS],
            "dynamic": [
                {
                    "name": r.name,
                    "description": r.description,
                    "provider": r.provider,
                    "model": r.model,
                    # Editable fields so the Agents page can PATCH them without a
                    # separate detail fetch.
                    "system_prompt": r.system_prompt,
                    "tools": _json.loads(r.tools_json or "[]"),
                }
                for r in d.platform.agents_registry.list()
            ],
        }

    @app.post("/agents")
    def create_agent(body: AgentCreate) -> dict[str, Any]:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        rec = d.platform.agents_registry.register(
            name,
            body.system_prompt,
            body.tools,
            description=body.description,
            provider=body.provider,
            model=body.model,
        )
        return {"name": rec.name, "provider": rec.provider, "model": rec.model}

    # --- Remote agents (run elsewhere) ------------------------------------
    # Registered BEFORE the /agents/{name} routes below so the literal
    # /agents/remote path is never swallowed by the {name} param match.

    def _remote_reg() -> RemoteAgentRegistry:
        return RemoteAgentRegistry(d.platform.engine)

    def _remote_view(r) -> dict[str, Any]:
        # STATUS only — never the token / secret value.
        return {
            "name": r.name,
            "base_url": r.base_url,
            "kind": r.kind,
            "model": r.model or "",
            "enabled": r.enabled,
            "timeout_s": r.timeout_s,
            "has_credential": bool(r.secret_name),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    @app.get("/agents/remote")
    def list_remote_agents() -> dict[str, Any]:
        return {"agents": [_remote_view(r) for r in _remote_reg().list()]}

    @app.post("/agents/remote")
    def add_remote_agent(body: RemoteAgentCreate) -> dict[str, Any]:
        import re as _re

        from ...agents.remote import KINDS

        name = (body.name or "").strip()
        if not _re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", name):
            raise HTTPException(status_code=400, detail="invalid remote agent name")
        if not (body.base_url or "").strip():
            raise HTTPException(status_code=400, detail="base_url is required")
        if body.kind not in KINDS:
            raise HTTPException(
                status_code=400, detail=f"kind must be one of {', '.join(KINDS)}"
            )
        secret_name: str | None = None
        if (body.token or "").strip():
            secret_name = "remote_agent_" + name
            d.platform.secrets.set(secret_name, body.token.strip(), kind="token")
        rec = _remote_reg().upsert(
            name,
            body.base_url.strip(),
            body.kind,
            secret_name=secret_name,
            model=(body.model or "").strip() or None,
            enabled=body.enabled,
            timeout_s=int(body.timeout_s or 120),
        )
        return _remote_view(rec)

    @app.delete("/agents/remote/{name}")
    def delete_remote_agent(name: str) -> dict[str, Any]:
        reg = _remote_reg()
        rec = reg.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        # Drop its vault secret too (best-effort — an absent secret is fine).
        if rec.secret_name:
            try:
                d.platform.secrets.delete(rec.secret_name)
            except Exception:  # noqa: BLE001
                pass
        reg.remove(name)
        return {"removed": name}

    @app.post("/agents/remote/{name}/test")
    async def test_remote_agent(name: str) -> dict[str, Any]:
        reg = _remote_reg()
        rec = reg.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        return await reg.test(rec, d.platform.secrets.get)

    @app.post("/agents/remote/{name}/run")
    async def run_remote_agent(name: str, body: RemoteAgentRun) -> dict[str, Any]:
        reg = _remote_reg()
        rec = reg.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such remote agent")
        if not rec.enabled:
            raise HTTPException(status_code=400, detail="remote agent is disabled")
        res = await reg.run(rec, body.task or "", d.platform.secrets.get)
        if not res.get("ok"):
            # 424 Failed Dependency — the remote agent itself failed to answer.
            raise HTTPException(status_code=424, detail=res.get("detail") or "remote call failed")
        return {"result": res.get("result") or "", "agent": name, "kind": rec.kind}

    # --- Dynamic-agent edit / delete (catch-all {name} — keep AFTER remote) ---

    @app.patch("/agents/{name}")
    def patch_agent(name: str, body: AgentPatch) -> dict[str, Any]:
        rec = d.platform.agents_registry.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown agent")
        import json as _json

        try:
            tools = _json.loads(rec.tools_json or "[]")
        except (TypeError, ValueError):
            tools = []
        updated = d.platform.agents_registry.register(
            name,
            body.system_prompt if body.system_prompt is not None else rec.system_prompt,
            [str(t) for t in body.tools] if body.tools is not None else tools,
            base_type=rec.base_type,
            description=body.description if body.description is not None else rec.description,
            provider=rec.provider,
            model=rec.model,
        )
        return {"name": updated.name, "description": updated.description}

    @app.delete("/agents/{name}")
    def delete_agent(name: str) -> dict[str, Any]:
        if not d.platform.agents_registry.remove(name):
            raise HTTPException(status_code=404, detail="unknown agent")
        return {"removed": name}

    # --- Agent threads: cross-source panels with roles (the Agents page) -----
    # IMPORTANT: registered BEFORE the {name} catch-alls above would matter,
    # but "/agents/threads/..." never collides because those routes were
    # declared first in this file; keep new thread routes below explicit paths.

    def _threads():
        from ...agents.threads import AgentThreads

        return AgentThreads(d.platform.engine)

    def _thread_view(rec) -> dict[str, Any]:
        import json as _json

        msgs = _json.loads(rec.messages_json or "[]")
        return {
            "id": rec.id,
            "title": rec.title,
            "participants": _json.loads(rec.participants_json or "[]"),
            "messages": msgs,
            "message_count": len(msgs),
            "updated_at": rec.updated_at.isoformat(),
        }

    @app.get("/agents/threads")
    def list_agent_threads() -> dict[str, Any]:
        out = []
        for rec in _threads().list():
            view = _thread_view(rec)
            view.pop("messages")  # list rows stay light; GET one for the transcript
            out.append(view)
        return {"threads": out}

    @app.post("/agents/threads")
    def create_agent_thread(body: dict) -> dict[str, Any]:
        from ...agents.threads import clean_participants

        try:
            participants = clean_participants(body.get("participants"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rec = _threads().create(str(body.get("title") or ""), participants)
        return _thread_view(rec)

    @app.get("/agents/threads/{thread_id}")
    def get_agent_thread(thread_id: str) -> dict[str, Any]:
        rec = _threads().get(thread_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such thread")
        return _thread_view(rec)

    @app.put("/agents/threads/{thread_id}/participants")
    def set_agent_thread_participants(thread_id: str, body: dict) -> dict[str, Any]:
        from ...agents.threads import clean_participants

        try:
            participants = clean_participants(body.get("participants"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rec = _threads().update_participants(thread_id, participants)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such thread")
        return _thread_view(rec)

    @app.delete("/agents/threads/{thread_id}")
    def delete_agent_thread(thread_id: str) -> dict[str, Any]:
        if not _threads().delete(thread_id):
            raise HTTPException(status_code=404, detail="no such thread")
        return {"deleted": thread_id}

    @app.post("/agents/threads/{thread_id}/say")
    async def agent_thread_say(thread_id: str, body: dict) -> dict[str, Any]:
        """One speaking round: the user's message (optional — empty continues
        the panel), then every participant answers in order, each seeing the
        replies before it. Failures are honest per-participant entries."""
        try:
            return await _threads().run_round(
                thread_id, str(body.get("message") or ""), d
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="no such thread")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # Custom (agent/user-authored) reusable tools.
    @app.get("/tools/custom")
    def list_custom_tools() -> dict[str, Any]:
        import json as _json

        def _load(s: str):
            try:
                return _json.loads(s or "[]")
            except (TypeError, ValueError):
                return []

        return {
            "tools": [
                {
                    "name": r.name,
                    "description": r.description,
                    "parameters": _load(r.params_json),
                    "command": _load(r.argv_json),
                    "timeout_seconds": r.timeout_seconds,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in d.platform.tools_registry.list()
            ]
        }

    @app.post("/tools/custom")
    def create_custom_tool(body: CustomToolCreate) -> dict[str, Any]:
        import re as _re

        name = (body.name or "").strip()
        if not _re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$", name):
            raise HTTPException(status_code=400, detail="invalid tool name")
        if d.platform.registry.get(name) is not None and name not in set(
            d.platform.registry.custom_names()
        ):
            raise HTTPException(status_code=400, detail=f"'{name}' is a built-in tool")
        if not body.command:
            raise HTTPException(status_code=400, detail="command (argv) is required")
        try:
            rec = d.platform.tools_registry.register(
                name, body.description, body.parameters, body.command, body.timeout_seconds
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        d.platform.registry.register(d.platform.tools_registry.build_tool(rec), custom=True)
        return {"name": rec.name}

    @app.post("/tools/custom/generate")
    async def generate_custom_tool(body: ToolGenerateBody) -> dict[str, Any]:
        """Describe the tool you want in plain language — an LLM designs the
        command-line tool (name, typed parameters, argv template) and it is
        registered immediately, usable by every agent."""
        import json as _json

        from ...providers.adapters.base import LLMMessage

        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")

        system = (
            "You design COMMAND-LINE tools for an agent platform running on "
            f"{'Windows' if os.name == 'nt' else 'POSIX'}. A tool runs an argv "
            "command in a workspace, with {param} placeholders filled from typed "
            "parameters. Respond with ONLY a JSON object (no prose, no fence): "
            '{"name": "snake_case_name", "description": "one line: what it does '
            'and when an agent should use it", "parameters": [{"name": "...", '
            '"type": "string|number|boolean", "required": true, "description": '
            '"..."}], "command": ["program", "arg", "{param}"], '
            '"timeout_seconds": 60}. Prefer python -c or powershell -Command for '
            "portability; keep it safe (no destructive defaults); every {param} "
            "in command MUST exist in parameters."
        )
        resp, _p, _m = await d._one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[
                LLMMessage(role="user", content=f"Design a tool for: {body.description}")
            ],
        )
        text = resp.text or ""
        start, depth, obj = text.find("{"), 0, ""
        if start >= 0:
            for i in range(start, len(text)):
                depth += (text[i] == "{") - (text[i] == "}")
                if depth == 0:
                    obj = text[start : i + 1]
                    break
        try:
            spec = _json.loads(obj)
        except Exception:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail="the model did not return a valid tool spec — try rephrasing",
            )
        # Register through the SAME validated path as a hand-made tool.
        create = CustomToolCreate(
            name=str(spec.get("name") or ""),
            description=str(spec.get("description") or body.description)[:300],
            parameters=[p for p in (spec.get("parameters") or []) if isinstance(p, dict)],
            command=[str(c) for c in (spec.get("command") or [])],
            timeout_seconds=int(spec.get("timeout_seconds") or 60),
        )
        result = create_custom_tool(create)
        return {
            **result,
            "spec": create.model_dump(),
            "reply": (
                f"Built the `{create.name}` tool — it's live for every agent now. "
                "Try it, and delete/regenerate if it's not quite right."
            ),
        }

    @app.get("/mcp/catalog")
    def mcp_catalog() -> dict[str, Any]:
        return {"catalog": d._MCP_CATALOG}

    @app.get("/mcp/servers")
    def mcp_servers() -> dict[str, Any]:
        """Configured MCP servers, each annotated with how many of its tools are
        currently LIVE in the registry (0 = configured but not loaded — usually a
        server that failed to connect, or was added and needs a restart)."""
        servers = []
        for s in list(getattr(d.platform.config, "mcp_servers", None) or []):
            name = s.get("name") or ""
            loaded = d.platform.registry.mcp_names(name) if name else []
            servers.append(
                {
                    **s,
                    "tools_loaded": len(loaded),
                    "tool_names": [n.split("__", 2)[-1] for n in loaded],
                }
            )
        return {"servers": servers}

    @app.post("/mcp/servers/{name}/test")
    def test_mcp_server(name: str) -> dict[str, Any]:
        """Connect to a configured server RIGHT NOW and list its tools — proves
        the command/URL + auth work without waiting for a restart. Read-only."""
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        cfg = next((s for s in servers if s.get("name") == name), None)
        if cfg is None:
            raise HTTPException(status_code=404, detail="no such server")
        from ...mcp.tools import mcp_tools as _mcp_tools

        try:
            tools = _mcp_tools([cfg], secret_resolver=d.platform.secrets.get)
        except Exception as exc:  # noqa: BLE001 — report, never crash
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "tools": []}
        names = [t.name.split("__", 2)[-1] for t in tools]
        return {
            "ok": bool(tools),
            "count": len(tools),
            "tools": names,
            "error": None if tools else "connected but the server advertised no tools",
        }

    @app.post("/mcp/servers")
    def add_mcp_server(body: McpServerBody) -> dict[str, Any]:
        """Register an external MCP server (persisted; loaded live best-effort,
        guaranteed on the next restart)."""
        import re as _re

        name = (body.name or "").strip()
        if not _re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$", name):
            raise HTTPException(status_code=400, detail="invalid server name")
        if not (body.command or "").strip():
            raise HTTPException(status_code=400, detail="command is required")
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        if any(s.get("name") == name for s in servers):
            raise HTTPException(status_code=400, detail=f"server '{name}' already exists")
        cfg = {
            "name": name,
            "command": body.command.strip(),
            "args": [str(a) for a in body.args],
            "env": dict(body.env or {}),
            **({"cwd": body.cwd} if body.cwd else {}),
            **({"auto_approve": True} if body.auto_approve else {}),
        }
        servers.append(cfg)
        d.platform.config.mcp_servers = servers
        d._persist_config(["mcp_servers"])
        # Best-effort LIVE load so its tools appear without a restart. Registered
        # with mcp=True so the ``mcp:*`` sentinel reaches them from the agent loop
        # and they survive the next restart (boot re-registers the same way).
        loaded = 0
        try:
            from ...mcp.tools import mcp_tools as _mcp_tools

            for tool in _mcp_tools([cfg], secret_resolver=d.platform.secrets.get):
                d.platform.registry.register(tool, mcp=True)
                loaded += 1
        except Exception:  # noqa: BLE001 — persisted config still loads on restart
            loaded = 0
        # auto_approve is applied when the resolver is built at boot, so a server
        # added live with auto_approve won't be trusted by headless agents until
        # the next restart — be honest about that.
        note = None
        if not loaded:
            note = "saved — restart the daemon to load its tools"
        elif body.auto_approve:
            note = "loaded — restart the daemon so autonomous agents may use it without asking"
        return {
            "name": name,
            "added": True,
            "tools_loaded": loaded,
            "auto_approve": bool(body.auto_approve),
            "note": note,
        }

    @app.delete("/mcp/servers/{name}")
    def delete_mcp_server(name: str) -> dict[str, Any]:
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        kept = [s for s in servers if s.get("name") != name]
        if len(kept) == len(servers):
            raise HTTPException(status_code=404, detail="no such server")
        d.platform.config.mcp_servers = kept
        d._persist_config(["mcp_servers"])
        # Unregister its live tools NOW so they disappear from every agent's
        # loadout immediately (they won't come back on restart either — the
        # config no longer lists the server).
        unloaded = 0
        for tool_name in d.platform.registry.mcp_names(name):
            if d.platform.registry.unregister(tool_name):
                unloaded += 1
        return {"removed": name, "tools_unloaded": unloaded}

    @app.post("/mcp/suggest")
    async def suggest_mcp_server(body: McpSuggestBody) -> dict[str, Any]:
        """Describe what you want to connect — an LLM proposes the MCP server
        config (returned for review; nothing is added until you confirm)."""
        import json as _json

        from ...providers.adapters.base import LLMMessage

        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
        system = (
            "You configure MCP (Model Context Protocol) stdio servers. Respond "
            "with ONLY a JSON object: {\"name\": \"kebab-name\", \"command\": "
            "\"npx\", \"args\": [\"-y\", \"<package>\", ...], \"env\": "
            "{\"KEY\": \"<what to put here>\"}, \"reply\": \"one short line: "
            "what this connects and any credential the user must supply\"}. "
            "Prefer well-known official/community MCP packages; if none fits, "
            "say so in reply and return an empty command."
        )
        resp, _p, _m = await d._one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[LLMMessage(role="user", content=body.description)],
        )
        text = resp.text or ""
        start, depth, obj = text.find("{"), 0, ""
        if start >= 0:
            for i in range(start, len(text)):
                depth += (text[i] == "{") - (text[i] == "}")
                if depth == 0:
                    obj = text[start : i + 1]
                    break
        try:
            spec = _json.loads(obj)
        except Exception:  # noqa: BLE001
            raise HTTPException(
                status_code=422, detail="no valid suggestion — try rephrasing"
            )
        return {"suggestion": spec}

    @app.delete("/tools/custom/{name}")
    def delete_custom_tool(name: str) -> dict[str, Any]:
        removed = d.platform.tools_registry.remove(name)
        d.platform.registry.unregister(name)
        return {"removed": removed}

    @app.post("/agents/{name}/spawn")
    async def spawn_agent_ep(name: str, body: SpawnBody) -> dict[str, Any]:
        from ...agents.runtime import AgentRuntime
        from ...agents.types import get_agent_definition
        from ...core.ids import utcnow
        from ...core.models import AgentState, AgentType, SessionStatus

        definition = d.platform.agents_registry.definition(name)
        rec = d.platform.agents_registry.get(name)
        if definition is None:
            try:
                definition = get_agent_definition(AgentType(name))
            except ValueError:
                raise HTTPException(status_code=404, detail="unknown agent")
        provider = rec.provider if (rec and rec.provider) else None
        session = await d.orchestrator.create_session(
            body.task, definition.type, provider=provider
        )

        async def _run_spawned() -> None:
            run = await AgentRuntime(d.platform).run(session, definition)
            session.status = (
                SessionStatus.COMPLETED
                if run.state is AgentState.COMPLETED
                else SessionStatus.FAILED
            )
            session.summary = run.result
            session.finished_at = utcnow()
            d.orchestrator._save(session)

        if body.wait:
            await _run_spawned()
        else:
            # Non-blocking spawn: the UI jumps straight to the live session view
            # (parity with POST /sessions wait:false).
            d._spawn_bg(session.id, _run_spawned())
        return _session_view(session)
