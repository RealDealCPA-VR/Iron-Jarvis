"""Skill tools (§23 + §19).

Exposes the registry to agents: ``skill_search`` finds relevant skills and
``skill_load`` returns a skill's full instructions so the agent can apply them.
Both wrap a :class:`SkillRegistry` injected via the constructor.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult
from .framework import SkillRegistry


class SkillSearchTool(Tool):
    """Find skills relevant to a query (§23)."""

    name = "skill_search"
    description = "Search available skills by topic; returns matching names and descriptions."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer"},
        },
        "required": ["query"],
    }
    #: v1.372.0: a search of the skill library changes nothing (the IJ
    #: harness arms it in plan mode, whose tools must all be reads).
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query", ""))
        k = int(args.get("k", 5))
        hits = self._registry.search(query, k)
        folder = str(getattr(ctx, "workspace", "") or "")
        if folder and len(hits) < k:
            # v1.417.0: the session folder's own skills (``skills/native``)
            # are found too — after the library's, never in its place.
            from . import native
            from .framework import SkillRegistry as _Reg

            local = _Reg()
            for s in await asyncio.to_thread(native.project_skills, folder):
                if self._registry.get(s.name) is None:
                    local._skills[s.name] = s
            hits = hits + local.search(query, k - len(hits))
        # Review (v1.417.0): what the model reads of each hit is the list's
        # own (``native.shown_entry``) — a description from another program's
        # folder or the session folder's own is SCANNED, a flagged name left
        # out. Off the loop (the scan reads the text).
        from . import native as _native

        shown = await asyncio.to_thread(
            lambda: [e for e in (_native.shown_entry(s) for s in hits) if e is not None]
        )
        if not shown:
            return ToolResult(ok=True, output="(no matching skills)", data={"skills": []})
        lines = [f"{n}: {desc}" for n, desc in shown]
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"skills": [{"name": n, "description": desc} for n, desc in shown]},
        )


class SkillLoadTool(Tool):
    """Load a skill's instructions by name (§23)."""

    name = "skill_load"
    description = (
        "Load a skill by name and return its full instructions, after a line naming the "
        "skill's base directory (its scripts/ and references/ are there — run and read them "
        "from that folder). args fills the skill's $ARGUMENTS."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "args": {"type": "string", "description": "Optional arguments for the skill ($ARGUMENTS)"},
        },
        "required": ["name"],
    }
    #: v1.372.0: loading a skill returns its text and changes nothing.
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from . import native

        name = str(args.get("name", "")).strip()
        # v1.417.0 (skill parity): the registry's skill, else the session
        # FOLDER's own (``.claude/skills``, ``.agents/skills`` … from its
        # folder up to the repository root — ``skills/native``), else a
        # plugin skill asked for by Claude Code's ``<plugin>:<skill>`` name.
        folder = str(getattr(ctx, "workspace", "") or "")
        skill = self._registry.get(name) or await asyncio.to_thread(
            native.find, self._registry, name, folder,
        )
        if skill is None:
            return ToolResult(ok=False, error=f"unknown skill '{name}'")
        if not getattr(skill, "model_invocable", True):
            # Claude Code's rule (``disable-model-invocation: true``; Codex's
            # ``allow_implicit_invocation: false``): only the user runs it.
            return ToolResult(
                ok=False,
                error=(
                    f"The skill '{skill.name}' runs only when the user asks for it "
                    f"(they type /{skill.name}); it cannot be loaded by a model."
                ),
            )
        # v1.372.0: the ONE rule ``inject`` and the "/skill" playbook follow
        # (``framework.guarded_instructions``): a skill that did not come from
        # the user (another program's skill folder, a plugin, an agent-made
        # one, a project's own) is scanned for planted instructions before a
        # model reads it; the user's own and the bundled ones come back
        # verbatim. Off the loop (the scan reads the text). v1.417.0: Claude
        # Code's result — the base directory line, then the body with its
        # arguments (``args``) and ``${CLAUDE_SKILL_DIR}`` filled. The skill's
        # ``allowed-tools`` grants nothing: every call it leads to is asked
        # for under Iron Jarvis's own rules.
        text = await asyncio.to_thread(
            native.invocation_text, skill, str(args.get("args") or ""),
            event_bus=getattr(ctx, "event_bus", None),
            session_id=getattr(ctx, "session_id", None),
            cap=None,
        )
        return ToolResult(
            ok=True,
            output=text,
            data={
                "name": skill.name,
                "description": skill.description,
                "base_dir": native.base_dir(skill),
                "examples": skill.examples,
                "scripts": skill.scripts,
                "templates": skill.templates,
            },
        )


class SkillCreateTool(Tool):
    """Author a durable skill from the loop (v1.90.0) — how the agent keeps a
    PROVEN solution (a validated formula workflow, a working script) for
    future reuse instead of re-deriving it every time."""

    name = "skill_create"
    description = (
        "SAVE a proven approach as a reusable skill for future conversations: "
        "name + one-line description + full markdown instructions (include "
        "the exact formulas/spec/code that worked and how they were "
        "validated). The skill appears on the Skills page immediately and is "
        "searchable via skill_search. Use after solving something non-obvious "
        "— e.g. a validated financial-statement formula workflow, or the "
        "run_code script that cracked a task."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "instructions": {"type": "string", "description": "Full markdown playbook"},
        },
        "required": ["name", "description", "instructions"],
    }

    def __init__(self, registry: SkillRegistry, config: Any) -> None:
        self._registry = registry
        self._config = config

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .loader import SKILL_FILE, save_skill, slugify

        skills_root = self._config.home / "skills"
        name = str(args.get("name", ""))
        # Provenance (v1.297.0): a skill the LOOP CREATES is "agent"-made and
        # remembers the session — the curator's sweep only ever considers
        # agent/proposal skills, never the user's own. ONLY on a new file: an
        # overwrite of an existing slug keeps that file's provenance (the same
        # rule `learning.approve` follows), or an agent rewriting the user's
        # own skill would turn it into a sweep candidate.
        provenance: dict[str, Any] = {}
        if not (skills_root / slugify(name) / SKILL_FILE).is_file():
            provenance = {
                "created_by": "agent",
                "created_session": str(getattr(ctx, "session_id", "") or ""),
            }
            # v1.470.0: a skill made in CHAT was asked for by the user and
            # approved on a card, so it is pinned: the curator's sweep never
            # archives it for sitting unused. It stays "agent"-made (a model
            # wrote the words), so it is still scanned before a model reads it.
            if provenance["created_session"] == "chat":
                provenance["pinned"] = True
        try:
            path = save_skill(
                skills_root,
                name,
                str(args.get("description", "")),
                str(args.get("instructions", "")),
                **provenance,
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        # Live rescan (same repopulate the daemon's /skills endpoints use) so
        # the new skill is searchable in THIS session, not after a restart.
        try:
            self._registry.repopulate(
                self._config.home,
                getattr(self._config, "extra_skill_paths", None),
            )
        except Exception:  # noqa: BLE001 — the file is saved; a rescan hiccup
            pass  # must not fail the creation (next boot picks it up)
        return ToolResult(
            ok=True,
            output=f"skill saved: {path}",
            data={"path": str(path), "name": str(args.get("name", "")).strip()},
        )


def skill_tools(registry: SkillRegistry, config: Any = None) -> list[Tool]:
    """Build the skill tools bound to ``registry`` (§23). With ``config`` the
    set includes ``skill_create`` (needs the home dir + rescan paths)."""
    tools: list[Tool] = [SkillSearchTool(registry), SkillLoadTool(registry)]
    if config is not None:
        tools.append(SkillCreateTool(registry, config))
    return tools
