"""Tool Registry (§19).

Central registration, discovery, permission enforcement, execution, logging,
and event emission. Every invocation is gated by the Permission Engine and
recorded as a ToolInvocation (§19 responsibilities).
"""

from __future__ import annotations

from typing import Any, Iterable

from ..core.db import dumps, session_scope
from ..core.events import EventType
from ..core.ids import new_id
from ..core.models import PermissionMode, ToolInvocation, UndoJournal
from .base import Reversibility, Tool, ToolContext, ToolResult
from .permissions import PermissionEngine
from .undo import finalize_post_hash


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        #: names of agent/user-authored (custom) tools, expanded by the
        #: ``"custom:*"`` allowlist sentinel so every agent can reach them.
        self._custom: set[str] = set()
        #: names of external MCP tools (``mcp__<server>__<tool>``), expanded by
        #: the ``"mcp:*"`` allowlist sentinel. Kept SEPARATE from ``_custom`` so
        #: an agent can opt into user-authored tools without also inheriting
        #: every connected external integration (Gmail/Drive/GitHub/...).
        self._mcp: set[str] = set()

    def register(self, tool: Tool, custom: bool = False, mcp: bool = False) -> None:
        if not tool.name:
            raise ValueError("tool must have a name")
        self._tools[tool.name] = tool
        if custom:
            self._custom.add(tool.name)
        if mcp:
            self._mcp.add(tool.name)

    def unregister(self, name: str) -> bool:
        """Remove a tool (used when a custom or MCP tool is deleted). False if absent."""
        self._custom.discard(name)
        self._mcp.discard(name)
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def custom_names(self) -> list[str]:
        return sorted(self._custom)

    def mcp_names(self, server: str | None = None) -> list[str]:
        """Registered external MCP tool names. With ``server`` given, only the
        tools of ``mcp__<server>__*`` (used to count/unload one server's tools)."""
        if server is None:
            return sorted(self._mcp)
        prefix = f"mcp__{server}__"
        return sorted(n for n in self._mcp if n.startswith(prefix))

    def specs(self, allowed: list[str] | None = None) -> list[dict[str, Any]]:
        tools = list(self._tools.values())
        if allowed is not None:
            allow = set(allowed)
            wild = "custom:*" in allow  # reach every custom tool, by name unknown
            mcp_wild = "mcp:*" in allow  # reach every connected MCP tool
            tools = [
                t for t in tools
                if t.name in allow
                or (wild and t.name in self._custom)
                or (mcp_wild and t.name in self._mcp)
            ]
        return [t.spec() for t in tools]

    async def invoke(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ToolContext,
        perms: PermissionEngine,
        agent_overrides: dict[str, str] | None = None,
        session_allow: "Iterable[str] | None" = None,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"unknown tool '{name}'")

        decision = perms.authorize(
            tool.perm_key(), args, agent_overrides, session_allow=session_allow
        )
        reversibility = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
        rev_value = reversibility.value if isinstance(reversibility, Reversibility) else str(reversibility)

        if not decision.allowed:
            inv_id = self._record(
                ctx, name, args, decision.mode, ok=False,
                output=decision.reason, reversibility=rev_value,
            )
            await ctx.event_bus.publish(
                EventType.TOOL_DENIED,
                {"tool": name, "mode": decision.mode.value, "reason": decision.reason,
                 "invocation_id": inv_id, "reversibility": rev_value},
                session_id=ctx.session_id,
            )
            return ToolResult(ok=False, error=f"permission denied: {decision.reason}")

        # TX-01 undo: snapshot the INVERSE *before* the mutation, for reversible
        # tools only. Best-effort — a capture failure degrades to no-undo (the
        # tool still runs) rather than blocking the action, matching the
        # returns_untrusted_content best-effort discipline.
        undo_desc: dict[str, Any] | None = None
        if reversibility == Reversibility.REVERSIBLE:
            try:
                undo_desc = await tool.capture_undo(args, ctx)
            except Exception:  # noqa: BLE001 — capture never blocks the tool
                undo_desc = None

        try:
            result = await tool.execute(args, ctx)
        except Exception as exc:  # tools must not crash the runtime
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        # For a raw/binary write the capture could not predict the post-image, so
        # re-hash the file NOW (after a successful write) to arm the anti-clobber
        # guard on a future undo. Best-effort — never blocks the tool.
        if result.ok and undo_desc is not None:
            try:
                finalize_post_hash(undo_desc, ctx)
            except Exception:  # noqa: BLE001 — telemetry/guard must never break the tool
                pass

        inv_id = self._record(
            ctx,
            name,
            args,
            decision.mode,
            ok=result.ok,
            output=result.output if result.ok else (result.error or ""),
            reversibility=rev_value,
            # Only journal an inverse for a SUCCESSFUL mutation (a failed write
            # changed nothing, so there is nothing to undo).
            undo=undo_desc if result.ok else None,
        )
        await ctx.event_bus.publish(
            EventType.TOOL_EXECUTED,
            {"tool": name, "ok": result.ok, "mode": decision.mode.value,
             "invocation_id": inv_id, "reversibility": rev_value},
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
        *,
        reversibility: str | None = None,
        undo: "dict[str, Any] | None" = None,
    ) -> str:
        """Persist the ToolInvocation (+ an UndoJournal row when an inverse was
        captured) and return the invocation id so the caller can tag its event."""
        # Redact secret-bearing args BEFORE persisting — args_json is stored in the
        # DB at rest, returned by /sessions/{id}/export, and included in backups, so
        # a plaintext credential here would defeat the encrypted vault.
        tool = self._tools.get(name)
        safe_args = tool.redact_args(args) if tool is not None else args
        inv_id = new_id("tool")
        record = ToolInvocation(
            id=inv_id,
            session_id=ctx.session_id,
            agent_run_id=ctx.agent_run_id,
            tool=name,
            args_json=dumps(safe_args),
            verdict=mode,
            ok=ok,
            output=output[:4000],
            reversibility=reversibility,
        )
        with session_scope(ctx.engine) as db:
            db.add(record)
            if undo:
                # The inverse descriptor (from Tool.capture_undo) is a small,
                # redaction-safe dict; the big pre-image itself is already a blob
                # ref or a small inline value inside it.
                db.add(
                    UndoJournal(
                        action_id=inv_id,
                        session_id=ctx.session_id,
                        agent_run_id=ctx.agent_run_id,
                        tool=name,
                        kind=str(undo.get("kind") or ""),
                        reversible=bool(undo.get("reversible", True)),
                        pre_ref=undo.get("pre_ref"),
                        pre_inline=undo.get("pre_inline"),
                        pre_sha256=undo.get("pre_sha256"),
                        post_sha256=undo.get("post_sha256"),
                    )
                )
            db.commit()
        return inv_id
