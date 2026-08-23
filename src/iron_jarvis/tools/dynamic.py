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
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.models import DynamicToolRecord
from .base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:  # avoid importing the heavy SQLAlchemy symbol at runtime
    from sqlalchemy import Engine

#: Maximum command runtime regardless of the record's request (a guardrail).
MAX_TIMEOUT_SECONDS = 600

#: How long a CommandTool trusts its last argv[0] health probe (v1.205.0).
#: ``specs()`` asks on every model-facing catalog build, so an uncached probe
#: would walk the whole PATH (× PATHEXT on Windows) per request per tool.
_HEALTH_TTL_SECONDS = 30.0

#: When the missing program has an obvious built-in equivalent, the refusal
#: SAYS so — an agent proposing a dead tool should learn what to reach for
#: instead. Grounded in a live task (v1.205.0): a custom `rename_real_file`
#: built on POSIX `mv` failed 22/22 times on the user's packaged install
#: ("command not found: 'mv'") while the built-in rename_file succeeded 15/15.
#: Names here MUST be real registered built-ins.
_BUILTIN_HINTS: dict[str, str] = {
    "mv": "for file renames the built-in rename_file already works",
    "move": "for file renames the built-in rename_file already works",
    "ren": "for file renames the built-in rename_file already works",
    "cat": "for reading files the built-in read_file already works",
    "type": "for reading files the built-in read_file already works",
    "ls": "for listing folders the built-in list_files already works",
    "dir": "for listing folders the built-in list_files already works",
    "grep": "for searching file contents the built-in grep already works",
    "findstr": "for searching file contents the built-in grep already works",
}


def _which(prog: str) -> "str | None":
    """Does ``prog`` resolve to a runnable program on THIS machine?

    A thin seam around :func:`shutil.which` (which honours Windows ``PATHEXT``,
    so ``mv`` finds ``mv.EXE``) — module-level so tests can stand in a fake
    resolver without patching ``shutil`` for the whole process.
    """
    return shutil.which(prog)


def missing_program_error(argv: "list[str] | None") -> str:
    """Why this command template cannot run on this machine, or ``""``.

    THE MEASURED FAILURE (v1.205.0): ``tool_create`` never checked that the
    template's program exists, so an agent authored ``rename_real_file`` around
    POSIX ``mv`` on a Windows install, the dead tool was persisted, advertised
    to every future run, and failed 22/22 times mid-task with
    ``command not found: 'mv'``. Both creation doors (the ``tool_create`` agent
    tool AND ``POST /tools/custom``) refuse through THIS one function so the
    two can never drift, and :meth:`CommandTool.missing_program` reuses it for
    advertise-time health on tools that were persisted before the check existed
    (or whose program was uninstalled later).

    A templated ``argv[0]`` (``{prog}``) is filled at call time, so there is
    nothing to check yet — the capability deny-floor screens that hole
    separately. An empty argv is the callers' "command is required" case, not
    ours.
    """
    if not argv:
        return ""
    prog = str(argv[0]).strip()
    if not prog or "{" in prog:
        return ""
    if _which(prog):
        return ""
    base = Path(prog).name.lower()
    for ext in (".exe", ".bat", ".cmd", ".com"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    hint = _BUILTIN_HINTS.get(
        base, "a built-in tool may already cover this — check the built-in tools first"
    )
    return (
        f"'{prog}' is not installed on this machine (nothing named '{prog}' "
        f"resolves on PATH) — custom tools run real programs; {hint}"
    )


#: The pointer appended when a PERSISTED dead tool is invoked anyway (v1.205.0):
#: creation now refuses, but a tool created before the check — or whose program
#: was uninstalled since — stays in the registry (never deleted automatically)
#: and must fail honestly instead of with a bare "command not found".
_DEAD_TOOL_POINTER = (
    "this custom tool's program isn't installed; built-in tools may already "
    "cover this — see the Tools page"
)


def _build_input_schema(params: list[dict]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        # A NON-DICT PARAMETER MUST NOT BRICK THE BOOT (v1.178.0 review finding).
        # `tool_create` COMMITS the record and only then builds the tool, so a
        # parameter list like ["path"] (a bare string where an object belongs)
        # is persisted before anything rejects it — and `build_tool` is called
        # for EVERY stored record in `build_platform`, with no guard. The next
        # start then raises `'str' object has no attribute 'get'` while wiring
        # the registry, which is before the daemon can serve anything or explain
        # itself. Skipping the malformed entry costs that one parameter; raising
        # costs the install. `ToolCreateTool` rejects the shape up front so this
        # is a floor, not the error message.
        if not isinstance(p, dict):
            continue
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
            stored = json.loads(record.params_json or "[]")
        except (TypeError, ValueError):
            stored = []
        # FILTERED ONCE, HERE (v1.178.0 review finding). Three sites read these
        # entries as mappings — the schema build, `_render`, and `execute`'s
        # missing-args check — so a persisted non-dict (a bare "path" string
        # where an object belongs) raises in whichever runs first. The schema
        # build runs at BOOT, for every stored record, with no guard around it:
        # `build_platform` would die wiring the registry, before the daemon can
        # serve anything or say why. Dropping the malformed entry costs one
        # parameter; raising costs the install.
        self._params = [p for p in stored if isinstance(p, dict)] if isinstance(stored, list) else []
        try:
            self._argv = [str(a) for a in json.loads(record.argv_json or "[]")]
        except (TypeError, ValueError):
            self._argv = []
        self._timeout = max(1, min(int(record.timeout_seconds or 60), MAX_TIMEOUT_SECONDS))
        self.input_schema = _build_input_schema(self._params)
        # argv[0] health probe cache (v1.205.0) — see missing_program().
        self._health = ""
        self._health_at = 0.0

    def missing_program(self) -> str:
        """``""`` when this tool's program resolves (or argv[0] is templated),
        else the honest :func:`missing_program_error` text.

        THE HEALTH SIGNAL (v1.205.0). ``ToolRegistry.specs`` asks this before
        advertising a custom tool to a model, and ``execute`` asks it before
        running — so a persisted tool whose program is gone stops being offered
        and fails honestly, while the record itself is never touched (the user
        sees and deletes it on the Tools page). Cached for a short TTL because
        ``specs()`` runs per model request and an uncached miss walks the whole
        PATH; the TTL also means a freshly INSTALLED program is picked up
        within seconds rather than never.
        """
        now = time.monotonic()
        if self._health_at and (now - self._health_at) < _HEALTH_TTL_SECONDS:
            return self._health
        self._health = missing_program_error(self._argv)
        self._health_at = now
        return self._health

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
        # DEAD PROGRAM FIRST (v1.205.0): a tool that can NEVER run must say so
        # before quibbling about arguments — "missing required: path" on a tool
        # whose program does not exist sends the caller fixing the wrong thing.
        dead = self.missing_program()
        if dead:
            return ToolResult(ok=False, error=f"{dead}. Note: {_DEAD_TOOL_POINTER}.")
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
            # Reachable when argv[0] was templated (missing_program() cannot
            # judge a placeholder) or the program vanished inside the health
            # TTL — same honest wording as the pre-check, never a bare
            # "command not found" (the 22-failure live shape, v1.205.0).
            return ToolResult(
                ok=False,
                error=f"command not found: {argv[0]!r} — {_DEAD_TOOL_POINTER}",
            )
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
        # REJECT THE SHAPE BEFORE IT IS COMMITTED (v1.178.0 review finding). This
        # method persists the record and only THEN builds the tool, so without
        # this an entry like ["path"] is stored and every later boot has to cope
        # with it. `CommandTool` now drops such an entry rather than raising, but
        # a dropped parameter is a tool that silently ignores an argument — an
        # honest refusal here is the better half of the same fix.
        bad = [p for p in params if not isinstance(p, dict)]
        if bad:
            return ToolResult(
                ok=False,
                error=(
                    "each entry in `parameters` must be an object like "
                    '{"name": "path", "type": "string", "required": true} — '
                    f"got {bad[0]!r}"
                ),
            )
        # THE PROGRAM MUST EXIST (v1.205.0). Refused HERE, before anything is
        # persisted: a dead tool used to be committed, advertised to every
        # future run, and fail forever ("command not found: 'mv'", 22/22 on a
        # live task). The refusal names the missing program and the built-in
        # that already covers the job, so the proposing agent can pick that
        # instead. Same check, same wording as POST /tools/custom.
        not_installed = missing_program_error(
            [str(c) for c in command if str(c).strip()]
        )
        if not_installed:
            return ToolResult(ok=False, error=not_installed)
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
        # A dead tool is LISTED but marked (v1.205.0): tool_list is management,
        # so hiding it would make it undeletable by an agent — but offering it
        # unmarked invites the call that can only fail.
        lines = []
        for r in rows:
            try:
                argv = [str(a) for a in json.loads(r.argv_json or "[]")]
            except (TypeError, ValueError):
                argv = []
            mark = (
                " [unavailable: its program is not installed on this machine]"
                if missing_program_error(argv)
                else ""
            )
            lines.append(f"{r.name}: {r.description}{mark}")
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
