"""Sandboxed ``shell`` tool (§16/§17).

Replaces the Phase 0–3 placeholder ShellTool: runs commands through the
Sandbox Manager (native by default) under the session's sandbox policy. Keeps
``permission_key='shell'`` so it stays gated at ``ask`` (§17).
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .manager import SandboxManager
from .policy import SandboxPolicy


class SandboxedShellTool(Tool):
    """Run a shell command inside the sandboxed session workspace (§16)."""

    name = "shell"
    description = "Run a shell command inside the sandboxed session workspace (§16/§17)."
    permission_key = "shell"  # defaults to 'ask' — fail-closed in headless mode
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        policy = SandboxPolicy.from_config(getattr(ctx.config, "sandbox", {}))
        # Honor an optional runtime hint on config without depending on it.
        prefer = getattr(ctx.config, "sandbox_runtime", "native") or "native"
        manager = SandboxManager(policy, prefer=prefer)
        result = manager.run(
            args["command"], cwd=ctx.workspace, timeout=policy.timeout_s
        )
        ok = result.returncode == 0 and not result.timed_out
        if result.timed_out:
            error: str | None = "command timed out"
        elif not ok:
            error = f"exit {result.returncode}"
        else:
            error = None
        return ToolResult(
            ok=ok,
            output=result.combined.strip(),
            data={
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "duration_s": result.duration_s,
            },
            error=error,
        )
