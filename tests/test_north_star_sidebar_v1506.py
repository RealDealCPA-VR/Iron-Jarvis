"""North-star plan 6.10: THE BROWSER SIDEBAR SAYS WHAT BELONGS IN THE MAIN
WINDOW (v1.506.0).

A sidebar turn is cut to the ``browser_*`` tools (the tool ceiling,
``browser/panel.browser_tool_ceiling``), and nothing told the model so: asked
to set a schedule or read a file there, it had no tool and no sentence, and
said "I can't". Now a ceiling turn's system prompt carries one fixed line,
``browser/panel.CEILING_LINE``: what the sidebar does, and that anything else is
done in the Iron Jarvis window. It joins BEFORE the history planner (its cost
is priced), and only a turn with a ceiling gets it.
"""

from __future__ import annotations

import pytest

from iron_jarvis.browser import panel as BP
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from tests.test_build_sessions_v1367 import Script

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]


async def _turn(tmp_path, monkeypatch, *, ceiling: bool, family: bool = True) -> tuple[Script, list[str]]:
    platform = build_platform(str(tmp_path / "home"))
    script = Script(["ok."])
    monkeypatch.setattr(platform.router, "stream", script.stream)
    monkeypatch.setattr(platform.router, "complete", script.complete)
    priced: list[str] = []
    real = CT._plan_context

    def spy(d, body, system, *a, **kw):
        priced.append(system)
        return real(d, body, system, *a, **kw)

    monkeypatch.setattr(CT, "_plan_context", spy)
    monkeypatch.setattr(chat_routes, "_plan_context", spy, raising=False)
    fam = BP.browser_tool_ceiling(platform) if ceiling else None
    body = ChatBody(messages=[{"role": "user", "content": "make a schedule that runs every weekday at 8am"}],
                    auto_tools=True)
    gen = await chat_routes.stream_chat_turn(
        platform, BUILTIN_PERSONAS, body, tool_ceiling=fam, arm_family=fam if family else None,
    )
    async for _ in gen:
        pass
    return script, priced


@pytest.mark.asyncio
async def test_a_sidebar_turn_is_told_what_belongs_in_the_iron_jarvis_window(tmp_path, monkeypatch):
    script, priced = await _turn(tmp_path, monkeypatch, ceiling=True)
    assert BP.CEILING_LINE in script.calls[0]["system"], script.calls[0]["system"][-800:]
    assert priced and BP.CEILING_LINE in priced[0], "joined before the history planner"


@pytest.mark.asyncio
async def test_a_turn_with_no_ceiling_has_no_such_line(tmp_path, monkeypatch):
    script, _priced = await _turn(tmp_path, monkeypatch, ceiling=False)
    assert BP.CEILING_LINE not in script.calls[0]["system"]


@pytest.mark.asyncio
async def test_a_coding_attempts_ceiling_is_no_sidebar(tmp_path, monkeypatch):
    """``harness/attempts`` cuts a coding attempt with a ceiling too, and arms
    no family: it is not the sidebar and must not be told it is."""
    script, _priced = await _turn(tmp_path, monkeypatch, ceiling=True, family=False)
    assert BP.CEILING_LINE not in script.calls[0]["system"]


def test_the_line_names_the_window_and_says_what_the_sidebar_does():
    line = BP.CEILING_LINE
    assert "Iron Jarvis window" in line and "browser" in line
    assert "sidebar" in line
