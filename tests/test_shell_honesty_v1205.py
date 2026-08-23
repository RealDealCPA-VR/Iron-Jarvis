"""v1.205.0 — the shell tool tells the truth about its environment.

A LIVE user task burned 14 failed shell calls guessing at an environment
nobody described: the native sandbox deliberately scrubs env to a minimal
PATH (``sandbox/native.scrubbed_env``), so on Windows cmd.exe works and
``py.exe`` resolves — but ``python``, ``powershell`` and every POSIX tool
(``mv``/``ls``) do not, and the model was told NONE of this. Meanwhile the
"policy NOT enforced" advisory paragraph rode EVERY one of 25 results —
honest once, transcript-burning noise after.

Three fixes, each asserted here through the REGISTERED tool
(``sandbox/shell_tool.SandboxedShellTool`` — driven the way
``tests/test_event_loop_offload_v1175.py`` drives it, because the v1.175.0
lesson is that a protection asserted on the wrong class protects nobody):

* OS-HONEST DESCRIPTION — computed per-OS at registration; honest that the
  runtime is decided per call (Docker container vs the scrubbed native env).
* POSIX-GUESS HINT — one deterministic appended line when a FAILED native
  command starts with a POSIX tool absent on this platform. Never on
  success, never on non-POSIX failures, never rewriting the real output.
* ADVISORY DEDUPE — the full paragraph on a session's FIRST native-fallback
  result, a compact marker on every later one; bounded LRU session memory
  (the consult_tool idiom), so an evicted session honestly sees it again.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from iron_jarvis.core.config import load_config
from iron_jarvis.sandbox import shell_tool as shell_tool_mod
from iron_jarvis.sandbox.base import SandboxResult
from iron_jarvis.sandbox.manager import SandboxManager
from iron_jarvis.sandbox.native import NativeSandbox, scrubbed_env_description
from iron_jarvis.sandbox.shell_tool import (
    _COMPACT_ADVISORY,
    _NO_CONFINEMENT_WARNING,
    SandboxedShellTool,
    _posix_guess_hint,
    runtime_description,
)
from iron_jarvis.tools.base import ToolContext

# The paragraph's distinctive tail — present ONLY in the full advisory, never
# in the compact marker, so asserting on it separates the two renderings.
_PARAGRAPH_TAIL = "advisory only for this run"


def _ctx(tmp_path: Path, session_id: str = "s1") -> ToolContext:
    config = load_config(str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id=session_id,
        agent_run_id="r1",
        config=config,
        event_bus=None,
        engine=None,
    )


class _CannedSandbox(NativeSandbox):
    """A native-runtime stand-in returning a canned result (no subprocess).

    Subclasses ``NativeSandbox`` so the tool's ``isinstance`` fallback check
    sees a real native runtime — the same trick as the v1175 harness.
    """

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        super().__init__()
        self._canned = SandboxResult(
            stdout=stdout, stderr=stderr, returncode=returncode, duration_s=0.01
        )
        self.commands: list[str] = []

    def run(self, command, *, cwd, timeout=None):  # noqa: ANN001, ARG002
        self.commands.append(command)
        return self._canned


def _native_tool(monkeypatch, sandbox: _CannedSandbox | None = None):
    """A fresh tool whose every call lands on a canned NATIVE runtime."""
    fake = sandbox or _CannedSandbox(stdout="ok")
    monkeypatch.setattr(SandboxManager, "get", lambda self: fake)
    return SandboxedShellTool(), fake


# ---------------------------------------------------------------------------
# 1. OS-honest description
# ---------------------------------------------------------------------------


def test_windows_description_states_cmd_and_no_python():
    """win32 truth: cmd.exe, no python/powershell/POSIX tools, py.exe works,
    and the built-in tools are named as the better path for file work."""
    desc = runtime_description("nt")
    assert "cmd.exe" in desc
    assert "no python" in desc
    assert "powershell" in desc
    assert "py.exe is available" in desc
    # The exact tools the model kept guessing at are called out as absent.
    for tool in ("mv", "ls", "cp", "grep"):
        assert tool in desc
    # And the honest alternative is named.
    assert "rename_file" in desc
    assert "list_files" in desc


def test_posix_description_states_scrubbed_path():
    desc = runtime_description("posix")
    assert "/usr/bin:/bin" in desc
    assert "virtualenvs" in desc
    assert "cmd.exe" not in desc
    assert "rename_file" in desc


def test_description_is_honest_about_per_call_runtime():
    """The runtime is decided PER CALL (execute's manager.get() probes Docker
    every invocation), so the description must describe BOTH outcomes and
    keep the original sandboxed-workspace wording for the Docker case."""
    for os_name in ("nt", "posix"):
        desc = runtime_description(os_name)
        assert "sandboxed session workspace" in desc  # today's wording kept
        assert "decided per call" in desc
        assert "Docker" in desc
        assert "native runtime" in desc


def test_registered_tool_carries_computed_description(monkeypatch):
    """The INSTANCE platform.py registers advertises the per-OS truth — and
    ``spec()`` (what the model actually sees) reads that instance attribute,
    not the generic class default."""
    monkeypatch.setattr(os, "name", "nt")
    tool = SandboxedShellTool()
    assert tool.description == runtime_description("nt")
    assert tool.spec()["description"] == tool.description
    assert "cmd.exe" in tool.spec()["description"]

    monkeypatch.setattr(os, "name", "posix")
    assert "cmd.exe" not in SandboxedShellTool().description


def test_description_matches_this_machine():
    """Unpatched: registration on THIS machine describes THIS machine."""
    assert SandboxedShellTool().description == runtime_description(os.name)


def test_description_stays_next_to_the_scrub():
    """The env truth is sourced from native.py, beside scrubbed_env(), so the
    description and the scrub cannot drift apart silently."""
    assert scrubbed_env_description("nt") in runtime_description("nt")
    assert scrubbed_env_description("posix") in runtime_description("posix")


# ---------------------------------------------------------------------------
# 2. POSIX-guess hint
# ---------------------------------------------------------------------------


async def test_hint_fires_on_failed_posix_command(tmp_path, monkeypatch):
    """A failed ``mv`` on the Windows native runtime gets ONE appended hint
    line — and the real output is preserved above it, never rewritten."""
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(os, "name", "nt")
    fake = _CannedSandbox(
        returncode=1,
        stderr="'mv' is not recognized as an internal or external command",
    )
    tool, fake = _native_tool(monkeypatch, fake)

    result = await tool.execute({"command": "mv old.txt new.txt"}, ctx)

    assert result.ok is False
    assert "'mv' is a POSIX command" in result.output
    assert "rename_file/list_files" in result.output
    # The real stderr still reaches the model untouched, above the hint.
    assert "not recognized" in result.output
    assert result.output.index("not recognized") < result.output.index(
        "'mv' is a POSIX command"
    )


async def test_no_hint_on_successful_command(tmp_path, monkeypatch):
    """Failure-only: even a POSIX first token gets no hint when the command
    succeeded (some tool on PATH answered — hinting would be a lie)."""
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(os, "name", "nt")
    tool, _ = _native_tool(monkeypatch, _CannedSandbox(returncode=0, stdout="done"))

    result = await tool.execute({"command": "ls -la"}, ctx)

    assert result.ok is True
    assert "POSIX command" not in result.output


async def test_no_hint_on_non_posix_failure(tmp_path, monkeypatch):
    """A failure whose first token is not in the POSIX set stays unannotated —
    the hint is a targeted correction, not generic failure chrome."""
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(os, "name", "nt")
    tool, _ = _native_tool(
        monkeypatch, _CannedSandbox(returncode=1, stderr="some real failure")
    )

    result = await tool.execute({"command": "dir C:\\nope"}, ctx)

    assert result.ok is False
    assert "POSIX command" not in result.output
    assert "some real failure" in result.output


def test_hint_is_windows_only_and_token_exact():
    """Pure-function edges: nothing on POSIX (those commands exist there),
    first-token match only, quoted tokens normalized, empty command safe."""
    assert _posix_guess_hint("mv a b", os_name="posix") is None
    assert _posix_guess_hint("mv a b", os_name="nt") is not None
    assert _posix_guess_hint('"grep" -r pat .', os_name="nt") is not None
    # 'move' is a real cmd.exe builtin — must NOT match the 'mv' entry.
    assert _posix_guess_hint("move a b", os_name="nt") is None
    assert _posix_guess_hint("echo mv", os_name="nt") is None
    assert _posix_guess_hint("", os_name="nt") is None
    assert _posix_guess_hint("   ", os_name="nt") is None


# ---------------------------------------------------------------------------
# 3. Advisory dedupe (through the REGISTERED tool, v1175-style)
# ---------------------------------------------------------------------------


async def test_advisory_full_once_then_compact(tmp_path, monkeypatch):
    """First native-fallback result in a session renders the full paragraph;
    the second renders ONLY the compact marker — but still carries A marker
    (per-result truth preserved) and still carries the real output."""
    tool, _ = _native_tool(monkeypatch, _CannedSandbox(returncode=0, stdout="real out"))
    ctx = _ctx(tmp_path, session_id="sess-a")

    first = await tool.execute({"command": "echo 1"}, ctx)
    second = await tool.execute({"command": "echo 2"}, ctx)

    # First: the honest full paragraph.
    assert first.output.startswith("[warning]")
    assert _PARAGRAPH_TAIL in first.output
    assert first.data["confinement"] == "none"
    assert "confinement_warning" in first.data

    # Second: compact marker only — the paragraph does not repeat.
    assert second.output.startswith(_COMPACT_ADVISORY)
    assert _PARAGRAPH_TAIL not in second.output
    assert _NO_CONFINEMENT_WARNING not in second.output
    # Per-result truth is intact: marker in data, confinement still 'none',
    # and the command's real output still present.
    assert second.data["confinement"] == "none"
    assert second.data["confinement_warning"] == _COMPACT_ADVISORY
    assert "real out" in second.output


async def test_advisory_full_again_for_a_new_session(tmp_path, monkeypatch):
    """Dedupe is PER SESSION (ctx.session_id): a different session's first
    result gets the full paragraph even after another session consumed its
    own."""
    tool, _ = _native_tool(monkeypatch, _CannedSandbox(returncode=0, stdout="x"))

    await tool.execute({"command": "echo 1"}, _ctx(tmp_path, session_id="sess-a"))
    repeat = await tool.execute(
        {"command": "echo 2"}, _ctx(tmp_path, session_id="sess-a")
    )
    fresh = await tool.execute(
        {"command": "echo 3"}, _ctx(tmp_path, session_id="sess-b")
    )

    assert _PARAGRAPH_TAIL not in repeat.output
    assert fresh.output.startswith("[warning]")
    assert _PARAGRAPH_TAIL in fresh.output


async def test_advisory_memory_is_capped_and_evicts_lru(tmp_path, monkeypatch):
    """The session map is bounded (consult_tool's LRU idiom): beyond the cap
    the least-recently-seen session is evicted and — honestly — sees the full
    paragraph again on its next result."""
    monkeypatch.setattr(shell_tool_mod, "_MAX_ADVISED_SESSIONS", 2)
    tool, _ = _native_tool(monkeypatch, _CannedSandbox(returncode=0, stdout="x"))

    await tool.execute({"command": "c"}, _ctx(tmp_path, session_id="s-old"))
    await tool.execute({"command": "c"}, _ctx(tmp_path, session_id="s-mid"))
    await tool.execute({"command": "c"}, _ctx(tmp_path, session_id="s-new"))

    # Bounded: never more than the cap, and the oldest key is gone.
    assert len(tool._advised_sessions) <= 2
    assert "s-old" not in tool._advised_sessions
    assert list(tool._advised_sessions) == ["s-mid", "s-new"]

    # The evicted session is treated as new — full paragraph again.
    again = await tool.execute({"command": "c"}, _ctx(tmp_path, session_id="s-old"))
    assert again.output.startswith("[warning]")
    assert _PARAGRAPH_TAIL in again.output


async def test_advisory_state_is_instance_scoped(tmp_path, monkeypatch):
    """A fresh tool instance (fresh daemon / other tests' harnesses) starts
    clean — the memory lives on the registered instance, not module globals,
    so unrelated test files keep seeing first-result behaviour."""
    _, fake = _native_tool(monkeypatch, _CannedSandbox(returncode=0, stdout="x"))

    tool_a = SandboxedShellTool()
    tool_b = SandboxedShellTool()
    assert isinstance(tool_a._advised_sessions, OrderedDict)

    await tool_a.execute({"command": "c"}, _ctx(tmp_path, session_id="shared"))
    fresh = await tool_b.execute({"command": "c"}, _ctx(tmp_path, session_id="shared"))
    assert fresh.output.startswith("[warning]")
    assert _PARAGRAPH_TAIL in fresh.output
