"""WHICH environment an agent's command runs with (v1.377.0, IJ harness part 2
Phase 6 — the user's decision of 2026-10-23).

A BUILD SESSION's ``shell`` and program tools (``process_start``) get the
user's normal PATH and home folder — ``sandbox/native.build_env``: ``git``,
``node``, ``npm`` and ``python`` run as in the user's own terminal and a
commit carries their own identity; secret variables are still removed. EVERY
OTHER run — chat, agents (a non-Build agent run too), schedules, goals,
workflows, the phone, every unattended door — keeps
``sandbox/native.scrubbed_env`` exactly as before.

THE SWITCH IS KEYED ON THE SESSION, never on the tool, the posture or a
model argument (:func:`build_session_of`):

* a Build session's own turn runs its tools under ``build:<thread id>``
  (``harness/sessions.session_id_for``) — that id IS the answer;
* a HELPER of a Build session runs under its own session id; it belongs to
  the Build session while it is registered as that session's helper
  (``harness/helpers._LIVE``, written when the helper's session exists and
  BEFORE its run starts — ``HelperCall.attach``). A helper of a non-Build
  turn (the benchmark's ``chat``) is not one.

Only ``modify_env = "deny"`` (the default sandbox policy) is switched: a policy
that already lets commands inherit the daemon's whole environment keeps doing
so. Low trust is untouched (``shell`` still needs the Docker sandbox and
refuses the native runtime; the program tools are denied), and so is Local
only (it still cards every command).
"""

from __future__ import annotations

import os
from typing import Any


def build_session_of(session_id: Any) -> str:
    """The Build session (its thread id) a tool call's session belongs to, or
    "" — its own ``build:<id>``, or a helper registered for one. In memory;
    never raises."""
    from . import helpers as H

    sid = str(session_id or "")
    if not sid:
        return ""
    thread = H.build_thread_of(sid)
    if thread:
        return thread
    try:
        with H._LIVE_LOCK:
            parents = [p for p, children in H._LIVE.items() if sid in children]
    except Exception:  # noqa: BLE001 — unknown is not a Build session
        return ""
    for parent in parents:
        thread = H.build_thread_of(parent)
        if thread:
            return thread
    return ""


def is_build_call(session_id: Any) -> bool:
    return bool(build_session_of(session_id))


def command_env(modify_env: str, session_id: Any) -> "dict[str, str] | None":
    """The ``env`` a command of ``session_id`` is spawned with: ``None``
    (inherit the daemon's, unchanged) unless the policy says ``deny``; then
    the Build environment for a Build session, else the scrubbed one."""
    from ..sandbox.native import build_env, scrubbed_env

    if str(modify_env or "deny") != "deny":
        return None
    return build_env() if is_build_call(session_id) else scrubbed_env()


def build_shell_description(os_name: "str | None" = None) -> str:
    """The shell tool's description for a Build session's model (the runtime
    facts its command tools carry): the user's PATH with the tools really
    found on it. BLOCKING (``build_env_tools`` looks on PATH, remembered a
    minute): async callers use ``asyncio.to_thread``."""
    from ..sandbox.native import build_env_tools
    from ..sandbox.shell_tool import runtime_description

    return runtime_description(os_name, build_tools=build_env_tools())


def bash_shell_description(build: bool) -> str:
    """The shell facts a BASH-WRITTEN command tool (the IJ harness's Claude
    dialect, ``Bash``) carries on Windows (v1.386.0) — what really runs its
    commands: Git for Windows' bash when ``sandbox/git_bash.find`` finds one,
    else cmd.exe and what differs from bash. ``build``: the turn is a Build
    session's (its commands get the user's PATH; the words name the tools
    found on the PATH THAT shell sees). "" on any other OS: there the
    platform's own shell is POSIX sh and its words already say so. BLOCKING
    (the first look runs ``bash --version``; tools are looked up on PATH):
    async callers use ``asyncio.to_thread``."""
    if os.name != "nt":
        return ""
    from ..sandbox.git_bash import bash_path_for, find
    from ..sandbox.native import build_env, build_env_tools
    from ..sandbox.shell_tool import bash_runtime_description

    found = find()
    tools = None
    if build:
        env = build_env()
        if found is not None:
            key = next((k for k in env if k.upper() == "PATH"), "PATH")
            env = {**env, key: bash_path_for(found, env.get(key, ""))}
        tools = build_env_tools(env)
    return bash_runtime_description(found, build_tools=tools)


def bash_identity() -> str:
    """What runs a bash-written command on THIS PC, for the benchmark's
    identity (v1.386.0): ``bash: <its --version line>``, ``cmd.exe`` (none
    found), "" on another OS. BLOCKING."""
    if os.name != "nt":
        return ""
    from ..sandbox.git_bash import find

    found = find()
    return f"bash: {found.version}" if found is not None else "cmd.exe"


__all__ = [
    "bash_identity", "bash_shell_description", "build_session_of", "build_shell_description",
    "command_env", "is_build_call",
]
