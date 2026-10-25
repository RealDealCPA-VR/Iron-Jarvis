"""Sandboxed ``shell`` tool (§16/§17).

Replaces the Phase 0–3 placeholder ShellTool: runs commands through the
Sandbox Manager (native by default) under the session's sandbox policy. Keeps
``permission_key='shell'`` so it stays gated at ``ask`` (§17).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import threading
from collections import OrderedDict
from typing import Any

from ..core import procs
from ..core.trust import LOW_TRUST_SHELL_REFUSAL
from ..tools.base import Tool, ToolContext, ToolResult
from .base import Sandbox, SandboxResult
from .cmd_guard import semicolon_refusal
from .manager import SandboxManager
from .native import (
    NativeSandbox,
    bash_env_description,
    build_env_description,
    scrubbed_env_description,
)
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

#: ``data["confinement"]`` vocabulary (v1.232.0, audit T5) — ONE word that
#: says how confined the run was, carried by the registry onto the ledger row
#: and the ``tool.executed`` event so a surface can show it without parsing
#: the advisory prose. ``native-unconfined`` is the case the user must see: the
#: policy asked for isolation and the native runtime could not give it.
CONFINEMENT_SANDBOX = "sandbox"
CONFINEMENT_NATIVE_UNCONFINED = "native-unconfined"
CONFINEMENT_NATIVE = "native"

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


def runtime_description(os_name: str | None = None, *, build_tools: "tuple[str, ...] | None" = None) -> str:
    """The model-facing ``shell`` description, computed per-OS at registration
    (v1.205.0).

    The runtime is decided PER CALL — ``execute`` builds a ``SandboxManager``
    and ``manager.get()`` probes Docker on every invocation — so the
    description cannot promise one runtime. It keeps today's wording for the
    Docker case and is honest about what the native fallback actually is.

    ``build_tools`` (v1.377.0): a BUILD SESSION's commands run with the
    user's own PATH (``native.build_env``); the native part then names the
    tools really found on it (``native.build_env_tools``). Omitted: the
    scrubbed words, exactly as before.
    """
    native = (
        scrubbed_env_description(os_name) if build_tools is None
        else build_env_description(build_tools, os_name)
    )
    return (
        "Run a shell command inside the sandboxed session workspace "
        "(§16/§17). The runtime is decided per call: when Docker is "
        "available the command runs in an isolated Linux container; "
        "otherwise it runs on the native runtime — "
        + native
    )


#: v1.386.0: what a BASH-WRITTEN command tool (the IJ harness's Claude
#: dialect, ``Bash``) adds when no Git for Windows bash was found and cmd.exe
#: runs its commands: the model was trained on bash, so the differences that
#: break (or, with ``;``, destroy) are named.
NOT_BASH_NOTE = (
    "This is Windows cmd.exe, not bash (no Git for Windows bash was found on this PC): ';' does "
    "not separate commands (use '&&'), there are no heredocs ('<<'), '/dev/null' is 'nul', $VAR "
    "and $(...) are not expanded (%VAR% is), single quotes do not quote, and a trailing '&' does "
    "not start a background job (use run_in_background)."
)


def bash_runtime_description(found: Any, *, build_tools: "tuple[str, ...] | None" = None) -> str:
    """The ``shell`` words for a command written in BASH (v1.386.0) on
    Windows — what really runs it: Git for Windows' bash when ``found`` (a
    ``sandbox/git_bash.GitBash``), else cmd.exe with :data:`NOT_BASH_NOTE`.
    ``build_tools``: a Build session's tools found on the PATH that shell
    sees (``native.build_env_tools``); None = a run with the scrubbed
    environment. Never claims bash when cmd runs, nor cmd when bash runs."""
    if found is None:
        return runtime_description("nt", build_tools=build_tools) + " " + NOT_BASH_NOTE
    native = bash_env_description(
        getattr(found, "version", ""), build_tools or (), build=build_tools is not None,
    )
    return (
        "Run a shell command inside the sandboxed session workspace "
        "(§16/§17). The runtime is decided per call: when Docker is "
        "available the command runs in an isolated Linux container; "
        "otherwise it runs on the native runtime — "
        + native
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


#: The longest a single ``shell`` call may ask for (v1.364.0): Claude Code's
#: Bash maximum. The registry's own per-call deadline still applies on top.
MAX_TIMEOUT_S = 600.0


def _call_limits(args: dict[str, Any], ctx: ToolContext, policy: SandboxPolicy):
    """``(timeout, working folder, folder relative to the workspace)`` for one
    call, or ``ValueError`` in words (v1.364.0, IJ harness Phase 1).

    ``timeout_s`` absent = the policy's timeout, exactly as before; given, it
    is clamped to 1..600 s. ``cwd`` absent = the workspace; given, it must be
    a folder INSIDE the workspace (the same ``safe_path`` rule every file
    tool obeys) — a ``cwd`` is never a way out of the session's folder."""
    from ..tools.base import safe_path

    timeout = policy.timeout_s
    raw_t = args.get("timeout_s")
    if raw_t is not None:
        if isinstance(raw_t, bool):
            raise ValueError("`timeout_s` must be a number of seconds")
        try:
            timeout = float(raw_t)
        except (TypeError, ValueError):
            raise ValueError(f"`timeout_s` must be a number of seconds, got {raw_t!r}") from None
        if timeout != timeout or timeout <= 0:  # NaN or not positive
            raise ValueError("`timeout_s` must be more than 0 seconds")
        timeout = min(max(timeout, 1.0), MAX_TIMEOUT_S)
    cwd = ctx.workspace
    rel = ""
    raw_cwd = args.get("cwd")
    if raw_cwd is not None and str(raw_cwd).strip() not in ("", "."):
        try:
            cwd = safe_path(ctx.workspace, str(raw_cwd))
        except PermissionError:
            raise ValueError(
                f"`cwd` '{raw_cwd}' is outside the session's folder — commands run "
                "inside it only"
            ) from None
        if not cwd.is_dir():
            raise ValueError(f"`cwd` '{raw_cwd}' is not a folder in the session's workspace")
        try:
            rel = str(cwd.relative_to(ctx.workspace.resolve())).replace("\\", "/")
        except ValueError:
            rel = ""
    return timeout, cwd, rel


class SandboxedShellTool(Tool):
    """Run a shell command inside the sandboxed session workspace (§16)."""

    name = "shell"
    description = "Run a shell command inside the sandboxed session workspace (§16/§17)."
    permission_key = "shell"  # defaults to 'ask' — fail-closed in headless mode
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_s": {
                "type": "number",
                "description": "Stop the command after this many seconds (default: the sandbox setting, at most 600).",
            },
            "cwd": {
                "type": "string",
                "description": "Folder to run in, relative to the workspace (default: the workspace).",
            },
        },
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

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> Any:
        """v1.375.0: while a Build turn works on its own branch, a ``git
        commit`` / ``git push`` / ``gh pr create`` (and the rest of
        ``harness/git_guard``'s list) asks first in every posture, once per
        call, and a command the tool refuses shows no card; None otherwise —
        the ordinary permission verdict decides, exactly as before.
        v1.381.0: and in a turn a PROJECT's or a PLUGIN's Claude Code command
        started with shell lines (or a later turn running one of its lines),
        the command asks first the same way (``harness/command_shell``)."""
        from ..harness.command_shell import tool_check

        return tool_check(args.get("command") if isinstance(args, dict) else "")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # v1.375.0: git the plan rules out on a session's branch (a force
        # push, deleting a branch, a history rewrite — harness/git_guard) is
        # refused before anything runs, whatever the posture.
        from ..harness.git_guard import refusal

        refused = refusal(args.get("command") if isinstance(args, dict) else "")
        if refused:
            return ToolResult(ok=False, output="", error=refused)
        policy = SandboxPolicy.from_config(getattr(ctx.config, "sandbox", {}))
        # Honor an optional runtime hint on config without depending on it.
        prefer = getattr(ctx.config, "sandbox_runtime", "native") or "native"
        # When the policy asks for confinement the native runtime can't give,
        # prefer Docker; SandboxManager.get() falls back to native only when the
        # daemon is unreachable (F11).
        isolating = _is_isolating(policy)
        # LOW TRUST (v1.298.0): the caller — the agent runtime, for a run
        # whose effective trust is low — sets ``args["_isolate"] = True``
        # after the model's own arguments (the model cannot unset it). It
        # makes isolation MANDATORY: Docker is preferred as under an
        # isolating policy, and a native fallback is REFUSED below instead of
        # advised about. The key is ledgered with the call like any argument.
        must_isolate = bool(args.get("_isolate")) if isinstance(args, dict) else False
        if (isolating or must_isolate) and prefer != "docker":
            prefer = "docker"
        manager = SandboxManager(policy, prefer=prefer)
        # v1.364.0: a per-call timeout and working folder. Checked (and the
        # folder resolved) off the loop, before anything runs.
        try:
            timeout, run_cwd, rel_cwd = await asyncio.to_thread(_call_limits, args, ctx, policy)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))

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
        # A STOP REACHES THE COMMAND (v1.372.0). A cancelled await does not
        # cancel the worker thread (the v1.228.0 trap): Stop on a chat turn, on
        # an agent run or on a Build session's helper used to leave the
        # command running until its own timeout. The native runtime publishes
        # the child's handle here (``procs.run``'s ``on_spawn`` / ``cancelled``
        # seams — ``procs.run_async``'s own mechanism), and a cancel kills its
        # tree below. Only the REAL native ``run`` takes the seams: a test
        # double that overrides it keeps its own signature.
        _lock = threading.Lock()
        _box: dict[str, Any] = {"proc": None, "cancelled": False}

        def _published(proc: Any) -> bool:
            with _lock:
                _box["proc"] = proc
                return bool(_box["cancelled"])

        def _was_cancelled() -> bool:
            with _lock:
                return bool(_box["cancelled"])

        # v1.377.0 (the user's decision): a BUILD SESSION's command — its own
        # turn or one of its helpers, keyed on the session — runs with the
        # user's PATH and home folder, secrets removed (``native.build_env``);
        # every other run keeps the scrubbed environment. Decided here, before
        # the worker thread (an in-memory read).
        from ..harness.command_env import is_build_call

        build = is_build_call(ctx.session_id)
        # v1.386.0: a command the MODEL WROTE IN BASH — the IJ harness's
        # Claude dialect (`Bash`; `HarnessTurn.ctx_for` sets it on the call's
        # context, never a model argument) — runs in Git for Windows' bash on
        # Windows when one is found (`sandbox/git_bash`). Everything else, and
        # every other OS, keeps the platform's own shell exactly as before.
        bash_written = getattr(ctx, "shell_syntax", "") == "bash" and os.name == "nt"
        #: What ran it, for the result (and the POSIX-guess hint below).
        ran: dict[str, Any] = {"bash": None, "refused": ""}

        def _native_run(sandbox: Sandbox) -> SandboxResult | None:
            bash = None
            if bash_written:
                from .git_bash import find

                bash = find()
            if bash is None and os.name == "nt":
                # cmd.exe runs it: a POSIX file command (or an installer)
                # before an unquoted ';' would get the rest of the line as its
                # arguments (`rm -f a; python b` deletes b) — refused before
                # anything runs (`cmd_guard`).
                refused = semicolon_refusal(args["command"])
                if refused:
                    ran["refused"] = refused
                    return None
            if type(sandbox).run is NativeSandbox.run:
                ran["bash"] = bash
                return sandbox.run(  # type: ignore[call-arg]
                    args["command"], cwd=run_cwd, timeout=timeout,
                    on_spawn=_published, cancelled=_was_cancelled,
                    **({"build": True} if build else {}),
                    **({"bash": bash} if bash is not None else {}),
                )
            return sandbox.run(args["command"], cwd=run_cwd, timeout=timeout)

        def _select_and_run() -> tuple[Sandbox, SandboxResult | None]:
            # Resolve the concrete runtime once so we can tell whether
            # confinement actually held (and warn the operator when it didn't).
            sandbox = manager.get()
            # Low trust + no Docker: NOTHING runs. The refusal is decided in
            # the same hop, before any process exists (v1.298.0).
            if must_isolate and isinstance(sandbox, NativeSandbox):
                return sandbox, None
            if isinstance(sandbox, NativeSandbox):
                return sandbox, _native_run(sandbox)
            if not rel_cwd:
                return sandbox, sandbox.run(args["command"], cwd=run_cwd, timeout=timeout)
            # An isolating runtime mounts the WORKSPACE (so ".." still reaches
            # the session's other files) and changes into the subfolder first.
            return sandbox, sandbox.run(
                f"cd {shlex.quote(rel_cwd)} && {args['command']}",
                cwd=ctx.workspace,
                timeout=timeout,
            )

        # v1.333.0: what a FINISHED command left running hidden (a dev server
        # the agent started on purpose) belongs to this session until its run
        # ends (`procs.end_session`); the context reaches the worker thread.
        try:
            with procs.owned_by(ctx.session_id):
                sandbox, result = await asyncio.to_thread(_select_and_run)
        except asyncio.CancelledError:
            # The call was stopped: the command's tree is killed now (shielded,
            # so a second cancel cannot abandon it half-way); a cancel before
            # the spawn finished is honoured by the worker itself (``on_spawn``
            # answers True). Always re-raised.
            with _lock:
                _box["cancelled"] = True
                proc = _box["proc"]
            if proc is not None:
                try:
                    await asyncio.shield(asyncio.to_thread(procs.kill_tree, proc))
                except asyncio.CancelledError:
                    pass
            raise
        if result is None and ran["refused"]:
            # The `;` guard (v1.386.0): an ordinary failed result, ledgered
            # like any other; nothing was started.
            return ToolResult(
                ok=False,
                output="",
                data={"shell": "cmd", "refused": "semicolon"},
                error=ran["refused"],
            )
        if result is None:
            logger.warning("shell refused under low trust: %s", LOW_TRUST_SHELL_REFUSAL)
            return ToolResult(
                ok=False,
                output="",
                data={"confinement": "refused", "low_trust": True},
                error=LOW_TRUST_SHELL_REFUSAL,
            )
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
            "confinement": (
                CONFINEMENT_SANDBOX
                if not native_fallback
                else CONFINEMENT_NATIVE_UNCONFINED
                if isolating
                else CONFINEMENT_NATIVE
            ),
        }
        if native_fallback and ran["bash"] is not None:
            # v1.386.0 (additive, only when bash ran): which shell ran it.
            data["shell"] = "bash"
        output = result.combined.strip()
        # POSIX-guess hint (v1.205.0): a failed native command whose first
        # token is a POSIX tool absent on this platform gets ONE appended line
        # pointing at the built-in tools. Deterministic, failure-only, and the
        # real output is never rewritten. Never after bash ran it (v1.386.0):
        # "this shell is Windows cmd" would be a lie there.
        if native_fallback and not ok and ran["bash"] is None:
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
