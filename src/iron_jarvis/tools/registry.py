"""Tool Registry (§19).

Central registration, discovery, permission enforcement, execution, logging,
and event emission. Every invocation is gated by the Permission Engine and
recorded as a ToolInvocation (§19 responsibilities).
"""

from __future__ import annotations

from typing import Any

from ..core.db import dumps, session_scope
from ..core.events import EventType
from ..core.models import PermissionMode, ToolInvocation
from .base import Tool, ToolContext, ToolResult
from .permissions import PermissionEngine


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        #: names of agent/user-authored (custom) tools, expanded by the
        #: ``"custom:*"`` allowlist sentinel so every agent can reach them.
        self._custom: set[str] = set()

    def register(self, tool: Tool, custom: bool = False) -> None:
        if not tool.name:
            raise ValueError("tool must have a name")
        self._tools[tool.name] = tool
        if custom:
            self._custom.add(tool.name)

    def unregister(self, name: str) -> bool:
        """Remove a tool (used when a custom tool is deleted). False if absent."""
        self._custom.discard(name)
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def custom_names(self) -> list[str]:
        return sorted(self._custom)

    def specs(self, allowed: list[str] | None = None) -> list[dict[str, Any]]:
        tools = list(self._tools.values())
        if allowed is not None:
            allow = set(allowed)
            wild = "custom:*" in allow  # reach every custom tool, by name unknown
            tools = [
                t for t in tools
                if t.name in allow or (wild and t.name in self._custom)
            ]
        return [t.spec() for t in tools]

    async def invoke(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ToolContext,
        perms: PermissionEngine,
        agent_overrides: dict[str, str] | None = None,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"unknown tool '{name}'")

        decision = perms.authorize(tool.perm_key(), args, agent_overrides)
        if not decision.allowed:
            await ctx.event_bus.publish(
                EventType.TOOL_DENIED,
                {"tool": name, "mode": decision.mode.value, "reason": decision.reason},
                session_id=ctx.session_id,
            )
            self._record(ctx, name, args, decision.mode, ok=False, output=decision.reason)
            return ToolResult(ok=False, error=f"permission denied: {decision.reason}")

        try:
            result = await tool.execute(args, ctx)
        except Exception as exc:  # tools must not crash the runtime
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        self._record(
            ctx,
            name,
            args,
            decision.mode,
            ok=result.ok,
            output=result.output if result.ok else (result.error or ""),
        )
        await ctx.event_bus.publish(
            EventType.TOOL_EXECUTED,
            {"tool": name, "ok": result.ok, "mode": decision.mode.value},
            session_id=ctx.session_id,
        )
        return result

    def _record(
        self,
        ctx: ToolContext,
        name: str,
        args: dict,
        mode: PermissionMode,
        ok: bool,
        output: str,
    ) -> None:
        # Redact secret-bearing args BEFORE persisting — args_json is stored in the
        # DB at rest, returned by /sessions/{id}/export, and included in backups, so
        # a plaintext credential here would defeat the encrypted vault.
        tool = self._tools.get(name)
        safe_args = tool.redact_args(args) if tool is not None else args
        record = ToolInvocation(
            session_id=ctx.session_id,
            agent_run_id=ctx.agent_run_id,
            tool=name,
            args_json=dumps(safe_args),
            verdict=mode,
            ok=ok,
            output=output[:4000],
        )
        with session_scope(ctx.engine) as db:
            db.add(record)
            db.commit()
