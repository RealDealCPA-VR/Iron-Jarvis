"""The persistent-REPL tool (``tools/repl_tool.py``).

The tool is a shell over ``repl/session.py``'s registry, so these tests inject a
FAKE registry: they own the contract (``{"ok","stdout","stderr","result",
"error","truncated","restarted"}``) and nothing else, which is exactly what the
tool is allowed to depend on.

What is load-bearing here is the HONESTY of the shaping. A truncated capture
that reads as complete makes the model reason from a partial picture; a silent
namespace restart makes the next call's NameError look like the model's own
mistake; a summarised traceback cannot be debugged; and a created file that is
never reported never reaches the run's result card or the preview rail.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import pytest

from iron_jarvis.core.config import default_permissions
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.tools import repl_tool as _repl_mod
from iron_jarvis.tools.base import Reversibility, ToolContext
from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS, PermissionEngine
from iron_jarvis.tools.registry import ToolRegistry
from iron_jarvis.tools.repl_tool import DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, ReplTool


# --- the fake registry ------------------------------------------------------


def _payload(**over: Any) -> dict[str, Any]:
    """The exact dict shape ``ReplSession.execute`` promises."""
    base = {
        "ok": True, "stdout": "", "stderr": "", "result": "",
        "error": "", "truncated": False, "restarted": False,
    }
    base.update(over)
    return base


class FakeSession:
    """A ReplSession stand-in: returns a canned payload, optionally after a
    side effect (writing a file) so the created-path diff has something to see."""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        side_effect: Callable[[str], None] | None = None,
    ) -> None:
        self.payload = payload if payload is not None else _payload()
        self.side_effect = side_effect
        self.calls: list[tuple[str, float]] = []

    async def execute(self, code: str, timeout: float) -> dict[str, Any]:
        self.calls.append((code, timeout))
        if self.side_effect is not None:
            self.side_effect(code)
        return self.payload


class FakeRegistry:
    """session_id -> FakeSession, the mapping the real ReplRegistry provides."""

    def __init__(self, session: Any = None) -> None:
        self._session = session if session is not None else FakeSession()
        self.asked: list[str] = []

    def session(self, session_id: str) -> Any:
        self.asked.append(session_id)
        return self._session


class RegistryLevelFake:
    """The shape the REAL ``ReplRegistry`` exposes: one ``execute`` that takes
    the session id, CREATES the namespace on demand, and needs the workspace.
    Going through a ``get(session_id)`` lookup instead would return None on the
    first call of every session — i.e. the tool would never work."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else _payload()
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        session_id: str,
        code: str,
        *,
        workspace: Any,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        self.calls.append({
            "session_id": session_id, "code": code,
            "workspace": workspace, "timeout": timeout,
        })
        return self.payload

    def get(self, session_id: str) -> Any:
        return None  # nothing exists until execute() creates it


class ExplodingRegistry:
    def session(self, session_id: str) -> Any:
        raise RuntimeError("registry is wedged")


class ExplodingSession:
    async def execute(self, code: str, timeout: float) -> dict[str, Any]:
        raise RuntimeError("the worker died")


# --- helpers ---------------------------------------------------------------


def _ctx(tmp_path: Path) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="sess-1", agent_run_id="run-1",
        config=None, event_bus=None, engine=None,
    )


def _run(tool: ReplTool, args: dict[str, Any], ctx: ToolContext):
    return asyncio.run(tool.execute(args, ctx))


# --- identity --------------------------------------------------------------


def test_tool_identity_and_contract():
    tool = ReplTool(FakeRegistry())
    assert tool.name == "repl"
    assert tool.perm_key() == "repl"
    # Arbitrary Python can do anything — same fail-safe contract as run_code.
    assert tool.reversibility is Reversibility.IRREVERSIBLE
    # Its output may echo file/web text, so the runtime must fence it.
    assert tool.returns_untrusted_content is True
    assert tool.input_schema["required"] == ["code"]


def test_description_teaches_the_thing_that_makes_it_worth_using():
    """The description IS the feature: a model that doesn't know state persists,
    or prints whole objects, gets the context flood the REPL exists to prevent."""
    text = ReplTool().description.lower()
    assert "persist" in text                  # state survives between calls
    assert "print" in text                    # only print() returns anything
    assert "never print a whole" in text      # summarise, don't dump
    assert "_store_as" in text                # values another tool bound are here
    # And it stays SHORT: this is charged on every request of every session, so
    # a description that grows spends more context than the tool saves.
    assert len(ReplTool.description) < 1000, "the pitch, not an essay"


# --- happy path ------------------------------------------------------------


def test_printed_output_reaches_the_result(tmp_path):
    session = FakeSession(_payload(stdout="rows: 412\n"))
    res = _run(ReplTool(FakeRegistry(session)), {"code": "print(len(rows))"}, _ctx(tmp_path))
    assert res.ok, res.error
    assert "rows: 412" in res.output
    assert session.calls[0][0] == "print(len(rows))"


def test_default_timeout_is_used_and_a_caller_value_is_clamped(tmp_path):
    ctx = _ctx(tmp_path)
    session = FakeSession()
    _run(ReplTool(FakeRegistry(session)), {"code": "1"}, ctx)
    assert session.calls[-1][1] == DEFAULT_TIMEOUT_S
    _run(ReplTool(FakeRegistry(session)), {"code": "1", "timeout_s": 10_000}, ctx)
    assert session.calls[-1][1] == MAX_TIMEOUT_S


def test_stderr_and_repr_result_are_labelled(tmp_path):
    session = FakeSession(_payload(stdout="hi", stderr="a warning", result="41"))
    res = _run(ReplTool(FakeRegistry(session)), {"code": "x"}, _ctx(tmp_path))
    assert res.ok
    assert "hi" in res.output and "a warning" in res.output and "41" in res.output
    assert "[stderr]" in res.output


def test_a_silent_run_says_so_rather_than_returning_nothing(tmp_path):
    res = _run(ReplTool(FakeRegistry()), {"code": "x = 1"}, _ctx(tmp_path))
    assert res.ok
    assert "print" in res.output.lower()


def test_the_session_asked_for_is_this_sessions_namespace(tmp_path):
    reg = FakeRegistry()
    _run(ReplTool(reg), {"code": "1"}, _ctx(tmp_path))
    assert reg.asked == ["sess-1"]


# --- the registry-level door (what the real ReplRegistry exposes) -----------


def test_a_registry_level_execute_is_used_with_session_id_and_workspace(tmp_path):
    ctx = _ctx(tmp_path)
    reg = RegistryLevelFake(_payload(stdout="42\n"))
    res = _run(ReplTool(reg), {"code": "print(6*7)"}, ctx)
    assert res.ok, res.error
    assert "42" in res.output
    call = reg.calls[0]
    assert call["session_id"] == "sess-1"
    assert call["code"] == "print(6*7)"
    assert Path(call["workspace"]) == Path(ctx.workspace)
    assert call["timeout"] == DEFAULT_TIMEOUT_S


def test_the_real_registry_signature_still_matches_the_dispatcher():
    """Compatibility pin with ``repl/session.py``. If that module renames a
    parameter, this fails HERE rather than at runtime on the user's install —
    the tool dispatches by inspecting the signature. Skipped while the module
    hasn't landed, so this file never depends on another agent's file existing.
    """
    import inspect as _inspect

    session_mod = pytest.importorskip("iron_jarvis.repl.session")
    registry = getattr(session_mod, "ReplRegistry", None)
    if registry is None:  # pragma: no cover - contract not landed yet
        pytest.skip("ReplRegistry not exported yet")
    params = _inspect.signature(registry.execute).parameters
    assert "session_id" in params, "the dispatcher keys off session_id"
    assert "code" in params
    assert "workspace" in params
    assert "timeout" in params


# --- the exception path ----------------------------------------------------


TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "<repl>", line 1, in <module>\n'
    "    total = rows[9999]\n"
    "IndexError: list index out of range"
)


def test_a_raise_comes_back_as_the_real_traceback_verbatim(tmp_path):
    """Summarising it would leave the model guessing at the line and the type.
    The runtime shows ONLY `error` for a failed call, so it must live there."""
    session = FakeSession(_payload(ok=False, error=TRACEBACK, stdout="starting\n"))
    res = _run(ReplTool(FakeRegistry(session)), {"code": "rows[9999]"}, _ctx(tmp_path))
    assert res.ok is False
    assert TRACEBACK in res.error  # verbatim, not paraphrased
    assert "starting" in res.error  # what it printed before dying isn't lost


# --- truncation ------------------------------------------------------------


def test_truncated_output_says_so_explicitly(tmp_path):
    """A silently shortened result reads as complete and the model then reasons
    from a partial picture — this repo's standing rule."""
    session = FakeSession(_payload(stdout="line 1\nline 2\n", truncated=True))
    res = _run(ReplTool(FakeRegistry(session)), {"code": "print(rows)"}, _ctx(tmp_path))
    assert res.ok
    low = res.output.lower()
    assert "truncated" in low
    assert "partial" in low
    assert res.data["truncated"] is True


def test_truncation_is_reported_on_the_failure_path_too(tmp_path):
    session = FakeSession(_payload(ok=False, error=TRACEBACK, truncated=True))
    res = _run(ReplTool(FakeRegistry(session)), {"code": "boom"}, _ctx(tmp_path))
    assert res.ok is False
    assert "truncated" in res.error.lower()
    assert TRACEBACK in res.error


# --- restart ---------------------------------------------------------------


def test_a_restart_says_the_namespace_was_reset(tmp_path):
    """Without this the model's next NameError looks like its own mistake."""
    session = FakeSession(_payload(stdout="ok\n", restarted=True))
    res = _run(ReplTool(FakeRegistry(session)), {"code": "x"}, _ctx(tmp_path))
    assert res.ok
    low = res.output.lower()
    assert "namespace was reset" in low
    assert "gone" in low
    assert res.data["restarted"] is True


# --- created files ---------------------------------------------------------


def test_a_created_file_is_reported_as_an_absolute_path_that_exists(tmp_path):
    ctx = _ctx(tmp_path)

    def write(_code: str) -> None:
        (Path(ctx.workspace) / "out").mkdir(exist_ok=True)
        (Path(ctx.workspace) / "out" / "report.csv").write_text("a,b\n", encoding="utf-8")

    session = FakeSession(_payload(stdout="wrote it\n"), side_effect=write)
    res = _run(ReplTool(FakeRegistry(session)), {"code": "..."}, ctx)
    assert res.ok
    assert res.created_paths, "a file created by the code must be reported"
    (path,) = res.created_paths
    assert Path(path).is_absolute()
    assert Path(path).exists()
    assert Path(path).name == "report.csv"
    assert path in res.output  # the user is told WHERE, absolutely


def test_created_files_are_detected_on_the_registry_level_path_too(tmp_path):
    """The pre-run snapshot must be taken BEFORE the code runs, whichever door
    the call went through — otherwise the new file is already in `before`."""
    ctx = _ctx(tmp_path)

    class Writing(RegistryLevelFake):
        async def execute(self, session_id, code, *, workspace, timeout=30.0):
            (Path(workspace) / "made.txt").write_text("x", encoding="utf-8")
            return await super().execute(
                session_id, code, workspace=workspace, timeout=timeout
            )

    res = _run(ReplTool(Writing(_payload(stdout="ok\n"))), {"code": "..."}, ctx)
    assert res.ok, res.error
    assert res.created_paths and Path(res.created_paths[0]).name == "made.txt"


def test_pre_existing_files_are_not_claimed_as_created(tmp_path):
    ctx = _ctx(tmp_path)
    (Path(ctx.workspace) / "already.txt").write_text("old", encoding="utf-8")
    res = _run(ReplTool(FakeRegistry()), {"code": "1"}, ctx)
    assert res.ok
    assert not res.created_paths


def test_files_written_outside_the_workspace_are_not_reported(tmp_path):
    ctx = _ctx(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    def write(_code: str) -> None:
        (outside / "leaked.txt").write_text("nope", encoding="utf-8")

    session = FakeSession(_payload(stdout="done\n"), side_effect=write)
    res = _run(ReplTool(FakeRegistry(session)), {"code": "..."}, ctx)
    assert res.ok
    assert not res.created_paths
    assert "leaked.txt" not in res.output


def test_no_created_paths_are_reported_for_a_failed_run(tmp_path):
    """The code raised; whatever half-written file it left is not a result."""
    ctx = _ctx(tmp_path)

    def write(_code: str) -> None:
        (Path(ctx.workspace) / "partial.txt").write_text("half", encoding="utf-8")

    session = FakeSession(
        _payload(ok=False, error=TRACEBACK), side_effect=write
    )
    res = _run(ReplTool(FakeRegistry(session)), {"code": "..."}, ctx)
    assert res.ok is False
    assert not res.created_paths


# --- it never raises -------------------------------------------------------


@pytest.mark.parametrize(
    "registry",
    [
        None,                                  # not wired / not in this build
        FakeRegistry(session=object()),        # a session with no execute()
        ExplodingRegistry(),                   # lookup blows up
        FakeRegistry(session=ExplodingSession()),  # execution blows up
        FakeRegistry(session=FakeSession(payload="not a dict")),  # junk payload
        FakeRegistry(session=FakeSession(payload={})),  # empty payload
    ],
)
def test_the_tool_never_raises_whatever_the_registry_does(tmp_path, registry):
    res = _run(ReplTool(registry), {"code": "print(1)"}, _ctx(tmp_path))
    assert res.ok is False
    assert res.error  # and it always says WHY


def test_a_payload_missing_every_optional_key_still_shapes_a_result(tmp_path):
    """A namespace build that answers `{"ok": True}` and nothing else must not
    KeyError its way into "the daemon crashed" — every field is optional."""
    res = _run(
        ReplTool(FakeRegistry(FakeSession(payload={"ok": True}))),
        {"code": "x = 1"},
        _ctx(tmp_path),
    )
    assert res.ok
    assert "print" in res.output.lower()
    assert res.data == {"truncated": False, "restarted": False, "created": []}


def test_empty_code_is_rejected_without_touching_the_registry(tmp_path):
    reg = FakeRegistry()
    res = _run(ReplTool(reg), {"code": "   "}, _ctx(tmp_path))
    assert res.ok is False
    assert reg.asked == []


def test_the_registry_can_arrive_on_the_context(tmp_path):
    """``platform.repl`` is the wiring; a ctx-carried registry is the fallback
    so the tool works whichever seam lands first."""
    ctx = _ctx(tmp_path)
    session = FakeSession(_payload(stdout="via ctx\n"))
    ctx.repl = FakeRegistry(session)  # type: ignore[attr-defined]
    res = _run(ReplTool(), {"code": "1"}, ctx)
    assert res.ok
    assert "via ctx" in res.output


def test_a_lazily_provided_registry_is_called(tmp_path):
    session = FakeSession(_payload(stdout="lazy\n"))
    res = _run(
        ReplTool(lambda: FakeRegistry(session)), {"code": "1"}, _ctx(tmp_path)
    )
    assert res.ok
    assert "lazy" in res.output


# --- the permission gate, as REGISTERED -------------------------------------
#
# `perm_key() == "repl"` on its own proves nothing: the string only matters
# because the registry authorizes with it and the deny-floor is keyed by it. If
# the key ever drifts the assertions below still hold on the STRING while the
# gate silently stops applying, so these drive the real runtime path instead.


def _gated_ctx(tmp_path: Path) -> ToolContext:
    """A ctx the real ToolRegistry can record an invocation against."""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    engine = make_engine(str(tmp_path / "repl.db"))
    init_db(engine)
    return ToolContext(
        workspace=ws, session_id="sess-1", agent_run_id="run-1",
        config=None, event_bus=EventBus(), engine=engine,
    )


def test_the_tool_is_on_the_deny_floor_under_the_key_it_authorizes_with():
    key = ReplTool().perm_key()
    assert key == "repl"
    assert key in DENY_FLOOR_TOOLS, "a persistent interpreter is host-touching"
    assert default_permissions()[key] == "ask", "never allow-by-default"


def test_an_agent_definition_cannot_raise_repl_to_allow(tmp_path):
    """The deny-floor, driven through registry.invoke — not asserted on a
    constant. A dynamic agent whose definition says `repl: allow` must still be
    stopped, and the namespace must never be reached."""
    ctx = _gated_ctx(tmp_path)
    reg = FakeRegistry(FakeSession(_payload(stdout="pwned\n")))
    tool = ReplTool(reg)
    tools = ToolRegistry()
    tools.register(tool)
    perms = PermissionEngine(default_permissions())  # headless: no resolver

    res = asyncio.run(
        tools.invoke(
            "repl", {"code": "print(1)"}, ctx, perms,
            # Keyed off the tool's OWN key, so a drifted permission_key does not
            # let this pass for the wrong reason (an override under a name the
            # engine no longer resolves would simply miss).
            agent_overrides={tool.perm_key(): "allow"},
        )
    )
    assert res.ok is False
    assert "permission denied" in (res.error or "")
    assert reg.asked == [], "the code must not have run"


def test_a_per_task_session_grant_still_lets_repl_run(tmp_path):
    """The sanctioned door stays open — a deny-floor that also blocked the
    interactive grant would just make the tool unusable."""
    ctx = _gated_ctx(tmp_path)
    reg = FakeRegistry(FakeSession(_payload(stdout="42\n")))
    tools = ToolRegistry()
    tools.register(ReplTool(reg))
    perms = PermissionEngine(default_permissions())

    res = asyncio.run(
        tools.invoke(
            "repl", {"code": "print(6*7)"}, ctx, perms, session_allow=["repl"]
        )
    )
    assert res.ok, res.error
    assert "42" in res.output


# --- honesty: three bad facts at once ---------------------------------------


def test_a_restart_a_truncation_and_a_raise_are_ALL_reported(tmp_path):
    """The failure path shows ONLY `error`. If the restart or the truncation
    note lives in `output` alone, the model debugging the traceback never learns
    its variables are gone or that what it printed was clipped — and each of
    those silently re-reads as the model's own mistake."""
    session = FakeSession(
        _payload(
            ok=False, error=TRACEBACK, stdout="halfway\n",
            truncated=True, restarted=True,
        )
    )
    res = _run(ReplTool(FakeRegistry(session)), {"code": "boom"}, _ctx(tmp_path))
    assert res.ok is False
    low = (res.error or "").lower()
    assert "namespace was reset" in low          # the restart
    assert "truncated" in low                    # the clipping
    assert TRACEBACK in (res.error or "")        # the traceback, verbatim
    assert "halfway" in (res.error or "")        # what it printed first
    assert res.data["restarted"] is True and res.data["truncated"] is True


# --- created_paths: the ways a diff lies ------------------------------------


def test_a_modified_file_is_not_reported_as_created(tmp_path):
    """Overwriting a file the user already had is not a creation — reporting it
    would put it in the preview rail and arm an undo that DELETES it."""
    ctx = _ctx(tmp_path)
    existing = Path(ctx.workspace) / "ledger.csv"
    existing.write_text("old\n", encoding="utf-8")

    def rewrite(_code: str) -> None:
        existing.write_text("new, much longer content\n", encoding="utf-8")

    session = FakeSession(_payload(stdout="updated\n"), side_effect=rewrite)
    res = _run(ReplTool(FakeRegistry(session)), {"code": "..."}, ctx)
    assert res.ok
    assert not res.created_paths
    assert "ledger.csv" not in res.output


def test_a_file_created_then_deleted_in_the_same_run_is_not_reported(tmp_path):
    """A scratch file that no longer exists must not reach the result card —
    the preview rail would open a path with nothing behind it."""
    ctx = _ctx(tmp_path)

    def scratch(_code: str) -> None:
        tmp = Path(ctx.workspace) / "scratch.tmp"
        tmp.write_text("x", encoding="utf-8")
        tmp.unlink()
        (Path(ctx.workspace) / "kept.txt").write_text("y", encoding="utf-8")

    session = FakeSession(_payload(stdout="done\n"), side_effect=scratch)
    res = _run(ReplTool(FakeRegistry(session)), {"code": "..."}, ctx)
    assert res.ok
    assert [Path(p).name for p in (res.created_paths or [])] == ["kept.txt"]
    assert "scratch.tmp" not in res.output


def _make_dir_link(link: Path, target: Path) -> bool:
    """A directory symlink, or on Windows a JUNCTION (which needs no admin —
    and which ``os.walk(followlinks=False)`` descends into anyway, because a
    junction is not a symlink to ``DirEntry.is_symlink``). Returns success."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True, check=False,
            )
        else:
            os.symlink(str(target), str(link), target_is_directory=True)
    except Exception:  # noqa: BLE001 — unprivileged / unsupported
        return False
    return link.exists()


def test_a_junction_out_of_the_workspace_does_not_launder_a_file_into_created(
    tmp_path,
):
    """Windows specifically: a junction is NOT a symlink as far as os.walk is
    concerned, so the walk goes straight through it and every file on the other
    side looks like it lives in the workspace. Only real containment — resolving
    before comparing — stops the preview rail being pointed at C:/Windows."""
    ctx = _ctx(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if not _make_dir_link(Path(ctx.workspace) / "link", outside):
        pytest.skip("cannot create a directory link here")

    def write_outside(_code: str) -> None:
        (outside / "secret.txt").write_text("not yours", encoding="utf-8")

    session = FakeSession(_payload(stdout="done\n"), side_effect=write_outside)
    res = _run(ReplTool(FakeRegistry(session)), {"code": "..."}, ctx)
    assert res.ok
    assert not res.created_paths
    assert "secret.txt" not in res.output


def test_a_workspace_too_big_to_diff_SAYS_so_instead_of_reporting_nothing(
    tmp_path, monkeypatch
):
    """Silence reads as "no files were created". When the bounded scan gives up
    it must say the detection was skipped, or a real artifact is lost twice —
    once from the rail and once from what the model believes happened."""
    ctx = _ctx(tmp_path)
    for i in range(12):
        (Path(ctx.workspace) / f"f{i}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(_repl_mod, "_MAX_SCAN_ENTRIES", 5)

    def write(_code: str) -> None:
        (Path(ctx.workspace) / "made.csv").write_text("a\n", encoding="utf-8")

    session = FakeSession(_payload(stdout="ok\n"), side_effect=write)
    res = _run(ReplTool(FakeRegistry(session)), {"code": "..."}, ctx)
    assert res.ok
    assert not res.created_paths
    low = res.output.lower()
    assert "skipped" in low and "not listed" in low


# --- the event loop ---------------------------------------------------------


def test_no_part_of_the_workspace_diff_touches_the_event_loop(tmp_path, monkeypatch):
    """The v1.153.1 outage was the MainThread parked in ``pathlib.is_file``
    inside a tool, presenting to the user as "Daemon offline" for four hours.
    Both snapshots AND the diff (which stats + resolves up to _MAX_SCAN_ENTRIES
    paths) must run in a worker thread."""
    ctx = _ctx(tmp_path)
    threads: dict[str, list[bool]] = {"scan": [], "created": []}

    real_scan = ReplTool._scan
    real_created = ReplTool._created

    def spy_scan(base):
        threads["scan"].append(threading.current_thread() is threading.main_thread())
        return real_scan(base)

    def spy_created(before, after, workspace):
        threads["created"].append(
            threading.current_thread() is threading.main_thread()
        )
        return real_created(before, after, workspace)

    monkeypatch.setattr(ReplTool, "_scan", staticmethod(spy_scan))
    monkeypatch.setattr(ReplTool, "_created", staticmethod(spy_created))

    def write(_code: str) -> None:
        (Path(ctx.workspace) / "out.txt").write_text("x", encoding="utf-8")

    session = FakeSession(_payload(stdout="ok\n"), side_effect=write)
    res = _run(ReplTool(FakeRegistry(session)), {"code": "..."}, ctx)
    assert res.ok and res.created_paths

    assert len(threads["scan"]) == 2, "both the before and after snapshots run"
    assert not any(threads["scan"]), "a workspace walk on the loop freezes the app"
    assert threads["created"], "the diff ran"
    assert not any(threads["created"]), (
        "the diff stats and resolves every new path — off the loop, or a busy "
        "workspace parks the daemon in is_file() again"
    )
