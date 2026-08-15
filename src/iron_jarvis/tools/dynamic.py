"""Dynamic (agent-authored) tools — "tools that make tools" (§19 extension).

An agent (or a user) can create a NEW named tool at runtime: a description, a set
of typed parameters, and an argv command template whose ``{param}`` placeholders
are filled from the call arguments. The definition is persisted as a
:class:`~iron_jarvis.core.models.DynamicToolRecord` and rebuilt into a
:class:`CommandTool` that plugs straight into the existing
:class:`~iron_jarvis.tools.registry.ToolRegistry`, so EVERY future agent/session
can discover and call it (reuse). Mirrors the dynamic-agent registry.

Safety: the command runs with ``shell=False`` and each parameter value lands in a
single argv element (so a value can never inject extra shell words/commands), is
scoped to the session workspace, has a wall-clock timeout, and is permission-gated
under ``custom:<name>`` (default ``ask`` — fail-closed, like ``shell``).
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from typing import TYPE_CHECKING, Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.models import DynamicToolRecord
from .base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:  # avoid importing the heavy SQLAlchemy symbol at runtime
    from sqlalchemy import Engine

#: Maximum command runtime regardless of the record's request (a guardrail).
MAX_TIMEOUT_SECONDS = 600


def _build_input_schema(params: list[dict]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        props[name] = {
            "type": str(p.get("type", "string")) or "string",
            "description": str(p.get("description", "")),
        }
        if p.get("required"):
            required.append(name)
    return {"type": "object", "properties": props, "required": required}


class CommandTool(Tool):
    """A runtime-built tool that fills an argv template from its parameters and
    runs it (shell=False) inside the session workspace."""

    def __init__(self, record: DynamicToolRecord) -> None:
        self.name = record.name
        self.description = record.description or f"custom tool {record.name}"
        self.permission_key = f"custom:{record.name}"  # default 'ask' (fail-closed)
        try:
            self._params = json.loads(record.params_json or "[]")
        except (TypeError, ValueError):
            self._params = []
        try:
            self._argv = [str(a) for a in json.loads(record.argv_json or "[]")]
        except (TypeError, ValueError):
            self._argv = []
        self._timeout = max(1, min(int(record.timeout_seconds or 60), MAX_TIMEOUT_SECONDS))
        self.input_schema = _build_input_schema(self._params)

    def _render(self, args: dict[str, Any]) -> list[str]:
        """Substitute ``{param}`` placeholders, each value as ONE literal argv
        element (no shell, so values cannot inject extra words). A SINGLE
        simultaneous pass: a value that itself contains another param's
        ``{placeholder}`` is never re-expanded, so rendering is order-independent."""
        names = [str(p.get("name", "")) for p in self._params if p.get("name")]
        if not names:
            return list(self._argv)
        pattern = re.compile(r"\{(" + "|".join(map(re.escape, names)) + r")\}")
        return [
            pattern.sub(lambda m: str(args.get(m.group(1), "")), element)
            for element in self._argv
        ]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        missing = [
            p["name"]
            for p in self._params
            if p.get("required") and not str(args.get(p.get("name", ""), "")).strip()
        ]
        if missing:
            return ToolResult(ok=False, error=f"missing required: {', '.join(missing)}")
        argv = [a for a in self._render(args) if a != ""]
        if not argv:
            return ToolResult(ok=False, error="custom tool has an empty command")
        try:
            # Offloaded for the same reason ShellTool is (v1.175.0): this runs on
            # the daemon's single event loop and blocks for up to self._timeout
            # (capped at MAX_TIMEOUT_SECONDS), so inline it would freeze every
            # request and every other session while a custom tool runs.
            proc = await asyncio.to_thread(
                lambda: subprocess.run(
                    argv,
                    shell=False,  # argv form: a parameter value can't inject shell words
                    cwd=ctx.workspace,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, error="command timed out")
        except FileNotFoundError:
            return ToolResult(ok=False, error=f"command not found: {argv[0]!r}")
        except OSError as exc:
            return ToolResult(ok=False, error=f"could not run command: {exc}")
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return ToolResult(
            ok=proc.returncode == 0,
            output=out.strip(),
            data={"returncode": proc.returncode},
            error=None if proc.returncode == 0 else f"exit {proc.returncode}",
        )


class DynamicToolRegistry:
    """Persisted registry of agent/user-authored tools. ``load`` rebuilds them on
    boot so they survive a restart; ``register`` upserts by unique ``name``."""

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine
        self._records: dict[str, DynamicToolRecord] = {}

    def load(self) -> "DynamicToolRegistry":
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(DynamicToolRecord)))
        self._records = {r.name: r for r in rows}
        return self

    def register(
        self,
        name: str,
        description: str,
        params: list[dict],
        argv: list[str],
        timeout_seconds: int = 60,
        created_by: str = "",
    ) -> DynamicToolRecord:
        """Create or update a custom tool (upsert by unique ``name``)."""
        name = (name or "").strip()
        if not name:
            raise ValueError("tool name is required")
        if not argv:
            raise ValueError("tool command (argv) is required")
        params_json = json.dumps(list(params or []))
        argv_json = json.dumps([str(a) for a in argv])
        with session_scope(self.engine) as db:
            existing = db.exec(
                select(DynamicToolRecord).where(DynamicToolRecord.name == name)
            ).first()
            if existing is not None:
                existing.description = description
                existing.params_json = params_json
                existing.argv_json = argv_json
                existing.timeout_seconds = int(timeout_seconds or 60)
                record = existing
            else:
                record = DynamicToolRecord(
                    name=name,
                    description=description,
                    params_json=params_json,
                    argv_json=argv_json,
                    timeout_seconds=int(timeout_seconds or 60),
                    created_by=created_by,
                )
            db.add(record)
            db.commit()
            db.refresh(record)
        self._records[name] = record
        return record

    def get(self, name: str) -> DynamicToolRecord | None:
        record = self._records.get(name)
        if record is not None:
            return record
        with session_scope(self.engine) as db:
            record = db.exec(
                select(DynamicToolRecord).where(DynamicToolRecord.name == name)
            ).first()
        if record is not None:
            self._records[name] = record
        return record

    def list(self) -> list[DynamicToolRecord]:
        return sorted(self._records.values(), key=lambda r: r.name)

    def remove(self, name: str) -> bool:
        with session_scope(self.engine) as db:
            row = db.exec(
                select(DynamicToolRecord).where(DynamicToolRecord.name == name)
            ).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
        self._records.pop(name, None)
        return True

    def build_tool(self, record: DynamicToolRecord) -> CommandTool:
        return CommandTool(record)


# --- agent-facing tools: create / list / delete reusable custom tools --------

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


class ToolCreateTool(Tool):
    """Let an agent author a REUSABLE tool that all future agents can call."""

    name = "tool_create"
    description = (
        "Create a REUSABLE custom tool that you and every FUTURE agent can call. "
        "Provide a unique `name` (identifier), a `description`, typed `parameters` "
        "(each an object {name,type,required,description}), and a `command` argv "
        "array (the program followed by its args; use {param} placeholders that "
        "get filled from the call arguments — each value becomes one literal argv "
        "element, so there is no shell and values can't inject commands). Optional "
        "`timeout_seconds`. The definition is persisted and runs under permission "
        "'custom:<name>'. Example: name 'wc_lines', command ['wc','-l','{file}'], "
        "parameters [{name:'file',type:'string',required:true}]."
    )
    permission_key = "tool_create"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {"type": "array", "items": {"type": "object"}},
            "command": {"type": "array", "items": {"type": "string"}},
            "timeout_seconds": {"type": "integer"},
        },
        "required": ["name", "command"],
    }

    def __init__(self, platform) -> None:
        self.p = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not _NAME_RE.match(name):
            return ToolResult(
                ok=False,
                error="name must be a valid identifier (letter, then letters/digits/_)",
            )
        reg = self.p.registry
        if reg.get(name) is not None and name not in set(reg.custom_names()):
            return ToolResult(
                ok=False, error=f"'{name}' is a built-in tool; choose another name"
            )
        command = args.get("command")
        if not isinstance(command, list) or not [c for c in command if str(c).strip()]:
            return ToolResult(ok=False, error="command must be a non-empty argv array")
        params = args.get("parameters") or []
        if not isinstance(params, list):
            return ToolResult(ok=False, error="parameters must be an array")
        try:
            rec = self.p.tools_registry.register(
                name,
                str(args.get("description", "")),
                params,
                [str(c) for c in command],
                int(args.get("timeout_seconds", 60) or 60),
                created_by=getattr(ctx, "session_id", ""),
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        # Register into the LIVE registry as custom so it's reachable immediately
        # (and by every future agent via the "custom:*" allowlist sentinel).
        self.p.registry.register(self.p.tools_registry.build_tool(rec), custom=True)
        return ToolResult(
            ok=True,
            output=f"created reusable tool '{name}' (runs under permission custom:{name})",
            data={"name": name},
        )


class ToolListTool(Tool):
    name = "tool_list"
    description = "List the custom (agent/user-authored) tools available to call."
    permission_key = "tool_list"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, platform) -> None:
        self.p = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rows = self.p.tools_registry.list()
        if not rows:
            return ToolResult(ok=True, output="(no custom tools yet)", data={"tools": []})
        lines = [f"{r.name}: {r.description}" for r in rows]
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"tools": [r.name for r in rows]},
        )


class ToolDeleteTool(Tool):
    name = "tool_delete"
    description = "Delete a custom tool by name; it stops being available to agents."
    permission_key = "tool_delete"
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    def __init__(self, platform) -> None:
        self.p = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if name not in set(self.p.registry.custom_names()):
            return ToolResult(ok=False, error=f"no custom tool '{name}'")
        self.p.tools_registry.remove(name)
        self.p.registry.unregister(name)
        return ToolResult(ok=True, output=f"deleted custom tool '{name}'")


def dynamic_tool_tools(platform) -> list[Tool]:
    """Build the agent-facing custom-tool management tools bound to ``platform``."""
    return [ToolCreateTool(platform), ToolListTool(platform), ToolDeleteTool(platform)]
