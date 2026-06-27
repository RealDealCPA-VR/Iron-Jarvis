"""Sandboxed ``shell`` tool (§16/§17).

Replaces the Phase 0–3 placeholder ShellTool: runs commands through the
Sandbox Manager (native by default) under the session's sandbox policy. Keeps
``permission_key='shell'`` so it stays gated at ``ask`` (§17).
"""

from __future__ import annotations

import logging
from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .manager import SandboxManager
from .native import NativeSandbox
from .policy import SandboxPolicy

logger = logging.getLogger(__name__)

_NO_CONFINEMENT_WARNING = (
    "sandbox: filesystem/host/network policy NOT enforced — "
    "ran on the native runtime (Docker unavailable); "
    "workspace_only/host_access/internet limits are advisory only for this run"
)


def _is_isolating(policy: SandboxPolicy) -> bool:
    """True when the policy asks for confinement the native runtime can't give.

    Any of an isolating filesystem, denied host access, or non-``allow``
    network egress means the run *should* go to a real isolating runtime
    (Docker) rather than the best-effort native subprocess (§16/§17).
    """
    return (
        policy.host_access == "deny"
        or policy.internet in {"deny", "ask"}
        or policy.filesystem == "workspace_only"
    )


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
        # When the policy asks for confinement the native runtime can't give,
        # prefer Docker; SandboxManager.get() falls back to native only when the
        # daemon is unreachable (F11).
        isolating = _is_isolating(policy)
        if isolating and prefer != "docker":
            prefer = "docker"
        manager = SandboxManager(policy, prefer=prefer)
        # Resolve the concrete runtime once so we can tell whether confinement
        # actually held (and warn the operator when it didn't).
        sandbox = manager.get()
        native_fallback = isinstance(sandbox, NativeSandbox)
        result = sandbox.run(
            args["command"], cwd=ctx.workspace, timeout=policy.timeout_s
        )
        ok = result.returncode == 0 and not result.timed_out
        if result.timed_out:
            error: str | None = "command timed out"
        elif not ok:
            error = f"exit {result.returncode}"
        else:
            error = None

        data: dict[str, Any] = {
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "duration_s": result.duration_s,
            "confinement": "none" if native_fallback else "docker",
        }
        output = result.combined.strip()
        # Operator-visible warning whenever isolation was requested but the run
        # landed on the unconfined native runtime (F11).
        if native_fallback and isolating:
            logger.warning(_NO_CONFINEMENT_WARNING)
            data["confinement_warning"] = _NO_CONFINEMENT_WARNING
            output = (
                f"[warning] {_NO_CONFINEMENT_WARNING}\n{output}"
                if output
                else f"[warning] {_NO_CONFINEMENT_WARNING}"
            )

        return ToolResult(
            ok=ok,
            output=output,
            data=data,
            error=error,
        )
