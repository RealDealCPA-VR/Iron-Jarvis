"""v1.367.0 (IJ harness Phase 4; the Phase 3 review's finding 5): a folder's
plugin CHECK runs under EVERY dialect.

A check ran as ``shell`` through the turn's armed-set gate — but the GPT
dialect arms ``process_start`` (Codex's ``exec_command``) and no ``shell``,
so under every GPT model each check was refused by the gate and the model was
told to run it itself. Now the check runs through the command tool the turn
REALLY arms (``chat_turn.check_carrier``): ``process_start`` with
``until_exit``, polled with ``process_read`` to the check's timeout and
stopped past it — every call gated, permissioned and ledgered as
"plugin check". Driven through the real Build turn and the real process
tools; only the model is scripted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import check_carrier
from iron_jarvis.harness import process as P
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, wire

GPT = ("openai", "gpt-5.1")
PATCH = "*** Begin Patch\n*** Update File: app.py\n@@\n-x = 1\n+x = 2\n*** End Patch"


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _plugins(ws: Path, command: str, timeout_s: int | None = None) -> None:
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True, exist_ok=True)
    extra = f"\ntimeout_s = {timeout_s}" if timeout_s else ""
    (hd / "plugins.toml").write_text(
        f'[[checks]]\nfiles = ["*.py"]\ncommand = "{command}"{extra}\n', encoding="utf-8"
    )


def _checks(app) -> list[tuple[str, bool]]:
    with session_scope(app.state.platform.engine) as db:
        return [(r.tool, r.ok) for r in db.exec(select(ToolInvocation)) if r.called_as == "plugin check"]


async def _gpt_turn(app, work, command: str, turn_id: str, *, granted: list[str], timeout_s=None):
    _plugins(work, command, timeout_s)
    sid = new_session(app, work, provider=GPT[0], model=GPT[1])["id"]
    S.update(app.state.platform.engine, sid, setup={"granted_tools": granted})
    script = wire(app, Script([[ToolCall(id="p1", name="apply_patch", arguments={"input": PATCH})], "Done."],
                              who=GPT))
    status, raw = await open_turn(app, sid, {"text": "set x to 2", "turn_id": turn_id}, timeout=60)
    assert status == 200 and dict(frames_of(raw)).get("done"), raw[-400:]
    assert (work / "app.py").read_text(encoding="utf-8") == "x = 2\n"
    tool_msgs = [m for m in script.calls[1]["messages"] if m.role == "tool"]
    return sid, script, tool_msgs[0].content


def test_the_carrier_is_the_command_tool_the_turn_arms():
    assert check_carrier({"shell", "process_start"}) == "shell"
    assert check_carrier({"process_start", "process_read"}) == "process_start"
    assert check_carrier({"read_file"}) == "shell"
    assert check_carrier(()) == "shell"


async def test_under_the_gpt_dialect_a_check_runs_through_process_start(app, work):
    sid, script, content = await _gpt_turn(app, work, "echo checked", "bc-gpt", granted=["process_start"])
    assert "exec_command" in script.calls[0]["tools"] and "shell" not in script.calls[0]["tools"]
    assert "Check `echo checked` (passed):" in content and "checked" in content
    assert "Check not run" not in content
    assert ("process_start", True) in _checks(app)
    with session_scope(app.state.platform.engine) as db:
        row = next(r for r in db.exec(select(ToolInvocation)) if r.called_as == "plugin check")
        assert row.session_id == f"build:{sid}"
    assert P.TABLE.running(f"build:{sid}") == [], "the check's program ended"


async def test_a_failing_check_says_failed(app, work):
    _sid, _script, content = await _gpt_turn(app, work, "exit 3", "bc-fail", granted=["process_start"])
    assert "Check `exit 3` (failed):" in content and "Exited with code 3" in content


async def test_a_check_past_its_timeout_is_stopped(app, work):
    sleeper = 'python -c \\"import time; time.sleep(30)\\"'
    sid, _script, content = await _gpt_turn(app, work, sleeper, "bc-slow", granted=["process_start"], timeout_s=2)
    assert "(failed)" in content and "did not finish within 2 s" in content
    assert ("process_stop", True) in _checks(app)
    assert P.TABLE.running(f"build:{sid}") == []


async def test_without_the_users_grant_the_check_still_asks_nobody(app, work):
    """The carrier changes, the rule does not: a check runs on its own only
    where the user already allowed the command tool beyond one call."""
    _sid, script, content = await _gpt_turn(app, work, "echo checked", "bc-ask", granted=[])
    assert "Check not run (running it needs the user's approval)" in content
    assert ("process_start", False) in _checks(app)
