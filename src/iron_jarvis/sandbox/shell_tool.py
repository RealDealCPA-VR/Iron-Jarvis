"""Sandboxed ``shell`` tool (§16/§17).

Replaces the Phase 0–3 placeholder ShellTool: runs commands through the
Sandbox Manager (native by default) under the session's sandbox policy. Keeps
``permission_key='shell'`` so it stays gated at ``ask`` (§17).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .base import Sandbox, SandboxResult
from .manager import SandboxManager
from .native import NativeSandbox, scrubbed_env_description
from .policy import SandboxPolicy

logger = logging.getLogger(__name__)

_NO_CONFINEMENT_WARNING = (
    "sandbox: filesystem/host/network policy NOT enforced — "
    "ran on the native runtime (Docker unavailable); "
    "workspace_only/host_access/internet limits are advisory only for this run"
)

#: Compact per-result marker for repeat native-fallback results (v1.205.0).
#: The full paragraph above rode EVERY shell result in a live session — honest
#: once, transcript-burning noise on the other 24. The paragraph renders on
#: the FIRST native-runtime result per session; every later result in that
#: session still carries THIS marker, so per-result truth is preserved while
#: the paragraph appears once.
_COMPACT_ADVISORY = "[native runtime — limits advisory, see first shell result]"

#: Cap on the per-session advisory memory — same LRU idiom as
#: ``agents/consult_tool._MAX_TRACKED_RUNS`` (move_to_end on touch,
#: ``popitem(last=False)`` to evict). An evicted session honestly sees the
#: full paragraph again rather than leaking memory forever.
_MAX_ADVISED_SESSIONS = 512

#: POSIX commands a model guesses at on Windows when nobody described the
#: environment (the live task tried mv/ls/python/powershell 14 times). All of
#: these are absent from cmd.exe AND from the scrubbed System32 PATH.
_POSIX_ONLY_ON_WINDOWS = frozenset(
    {"mv", "cp", "ls", "rm", "cat", "grep", "chmod", "touch", "which"}
)


def runtime_description(os_name: str | None = None) -> str:
    """The model-facing ``shell`` description, computed per-OS at registration
    (v1.205.0).

    The runtime is decided PER CALL — ``execute`` builds a ``SandboxManager``
    and ``manager.get()`` probes Docker on every invocation — so the
    description cannot promise one runtime. It keeps today's wording for the
    Docker case and is honest about what the native fallback actually is.
    """
    return (
        "Run a shell command inside the sandboxed session workspace "
        "(§16/§17). The runtime is decided per call: when Docker is "
        "available the command runs in an isolated Linux container; "
        "otherwise it runs on the native runtime — "
        + scrubbed_env_description(os_name)
    )


def _posix_guess_hint(command: str, os_name: str | None = None) -> str | None:
    """One deterministic line when a FAILED native command starts with a POSIX
    tool that does not exist on this platform (v1.205.0). No model calls; never
    fires on success or on non-POSIX failures; never rewrites the real output.
    """
    if (os_name or os.name) != "nt":
        return None
    stripped = command.strip()
    if not stripped:
        return None
    token = stripped.split(None, 1)[0].strip("\"'").lower()
    if token not in _POSIX_ONLY_ON_WINDOWS:
        return None
    return (
        f"('{token}' is a POSIX command; this shell is Windows cmd — the "
        "built-in rename_file/list_files tools cover most file work)"
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

    def __init__(self) -> None:
        # OS-honest description, computed once at registration (v1.205.0) —
        # ``spec()`` reads the instance attribute, so the registered tool
        # advertises the real environment instead of the generic class default.
        self.description = runtime_description()
        #: Sessions that have already seen the full native-runtime advisory.
        #: INSTANCE-scoped (platform.py registers exactly one instance), LRU
        #: with the consult_tool idiom so it stays bounded.
        self._advised_sessions: "OrderedDict[str, None]" = OrderedDict()

    def _advisory_line(self, session_id: str) -> str:
        """Full paragraph on a session's FIRST native-fallback result, the
        compact marker on every later one (v1.205.0). Touch-and-evict keeps
        the memory bounded; an evicted session sees the paragraph again."""
        key = str(session_id or "")
        first = key not in self._advised_sessions
        self._advised_sessions[key] = None
        self._advised_sessions.move_to_end(key)
        while len(self._advised_sessions) > _MAX_ADVISED_SESSIONS:
            self._advised_sessions.popitem(last=False)
        return f"[warning] {_NO_CONFINEMENT_WARNING}" if first else _COMPACT_ADVISORY

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

        # BOTH steps below block, and this coroutine runs on the daemon's single
        # event loop, so they are offloaded together in ONE thread hop (v1.175.0):
        #   * manager.get() probes Docker (ping + info = a socket round-trip to
        #     the Docker daemon, which hangs for seconds when Docker Desktop is
        #     starting or wedged), and
        #   * sandbox.run() is subprocess.run(shell=True) for up to policy
        #     .timeout_s — an ARBITRARY user/model command.
        # Inline, either one freezes every request, WS event delivery, and every
        # other session (the v1.153.1 rule; the four-hour "Daemon offline"
        # outage was `pathlib.is_file`, far cheaper than this). The protected
        # copy of this logic lived in the SHADOWED tools/builtins.ShellTool —
        # platform.py registers THIS class under the same name, so the offload
        # has to be here.
        def _select_and_run() -> tuple[Sandbox, SandboxResult]:
            # Resolve the concrete runtime once so we can tell whether
            # confinement actually held (and warn the operator when it didn't).
            sandbox = manager.get()
            return sandbox, sandbox.run(
                args["command"], cwd=ctx.workspace, timeout=policy.timeout_s
            )

        sandbox, result = await asyncio.to_thread(_select_and_run)
        native_fallback = isinstance(sandbox, NativeSandbox)
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
        # POSIX-guess hint (v1.205.0): a failed native command whose first
        # token is a POSIX tool absent on this platform gets ONE appended line
        # pointing at the built-in tools. Deterministic, failure-only, and the
        # real output is never rewritten.
        if native_fallback and not ok:
            hint = _posix_guess_hint(args["command"])
            if hint:
                output = f"{output}\n{hint}" if output else hint
        # Operator-visible warning whenever isolation was requested but the run
        # landed on the unconfined native runtime (F11). Rendered in full on
        # the session's first such result, as a compact marker after (v1.205.0)
        # — every result still carries a marker, the paragraph appears once.
        if native_fallback and isolating:
            logger.warning(_NO_CONFINEMENT_WARNING)
            advisory = self._advisory_line(ctx.session_id)
            data["confinement_warning"] = advisory
            output = f"{advisory}\n{output}" if output else advisory

        return ToolResult(
            ok=ok,
            output=output,
            data=data,
            error=error,
        )
