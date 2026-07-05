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
    CustomToolCreate,
    McpServerBody,
    McpSuggestBody,
    SkillApplyBody,
    SkillCreate,
    SpawnBody,
    ToolGenerateBody,
)


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
        from ...agents.types import _DEFINITIONS

        return {
            "builtin": [t.value for t in _DEFINITIONS],
            "dynamic": [
                {
                    "name": r.name,
                    "description": r.description,
                    "provider": r.provider,
                    "model": r.model,
                }
                for r in d.platform.agents_registry.list()
            ],
        }

    @app.post("/agents")
    def create_agent(body: AgentCreate) -> dict[str, Any]:
        rec = d.platform.agents_registry.register(
            body.name,
            body.system_prompt,
            body.tools,
            description=body.description,
            provider=body.provider,
            model=body.model,
        )
        return {"name": rec.name, "provider": rec.provider, "model": rec.model}

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
        return {"servers": list(getattr(d.platform.config, "mcp_servers", None) or [])}

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
        }
        servers.append(cfg)
        d.platform.config.mcp_servers = servers
        d._persist_config(["mcp_servers"])
        # Best-effort LIVE load so its tools appear without a restart.
        loaded = 0
        try:
            from ...mcp.tools import mcp_tools as _mcp_tools

            for tool in _mcp_tools([cfg], secret_resolver=d.platform.secrets.get):
                d.platform.registry.register(tool, custom=True)
                loaded += 1
        except Exception:  # noqa: BLE001 — persisted config still loads on restart
            loaded = 0
        return {
            "name": name,
            "added": True,
            "tools_loaded": loaded,
            "note": None if loaded else "saved — restart the daemon to load its tools",
        }

    @app.delete("/mcp/servers/{name}")
    def delete_mcp_server(name: str) -> dict[str, Any]:
        servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
        kept = [s for s in servers if s.get("name") != name]
        if len(kept) == len(servers):
            raise HTTPException(status_code=404, detail="no such server")
        d.platform.config.mcp_servers = kept
        d._persist_config(["mcp_servers"])
        return {"removed": name, "note": "restart the daemon to fully unload its tools"}

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
