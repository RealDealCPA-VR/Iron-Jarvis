"""v1.459.0: Build › Code's Compact press for Pi, OpenCode and Grok.

The Session tab's Context drop-down offered "Compact session" for Claude Code
and Codex only; Pi, OpenCode and Grok said "compacts by itself". Each of them
compacts on a "/compact" MESSAGE, so the press now sends that for them too:

* Pi — the Code view runs Pi's RPC ``compact`` for the typed command
  (``PiDriver.run_builtin``, v1.429.0);
* OpenCode — it does not LIST its compact, but its ACP side turns a message
  that is exactly "/compact" into its own summarize (read from opencode
  1.18.18's program and measured with a stand-in model server: one
  summarization request, the summary as the reply, ``end_turn``), so the `/`
  menu offers a compact row of Iron Jarvis's own;
* Grok — it lists ``compact`` in its own commands (recorded).

Antigravity also lists ``compact`` but was never measured here: it keeps the
"compacts by itself" words.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted.models import HostedSessionRecord


@pytest.fixture()
def svc(tmp_path):
    app = create_app(str(tmp_path / "home"))
    return S.service_for(app.state.platform)


def _row(harness: str):
    return HostedSessionRecord(id=f"s_{harness}", harness=harness, folder="")


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", ["pi", "opencode", "grok", "claude-code", "codex"])
async def test_the_press_sends_the_tools_own_compact(svc, harness):
    body = await svc.context_view(_row(harness), [])
    assert body["compact"] == {"how": "message", "text": "/compact"}


@pytest.mark.asyncio
async def test_a_tool_never_measured_keeps_its_words(svc):
    body = await svc.context_view(_row("antigravity"), [])
    assert body["compact"]["how"] == "" and "compacts by itself" in body["compact"]["why"]


def test_opencode_offers_its_compact_in_the_menu_once(svc, tmp_path):
    rows = svc.commands_for("opencode", folder=str(tmp_path))
    assert [r["name"] for r in rows] == ["compact"]
    assert "OpenCode's own compact" in rows[0]["description"]


def test_other_tools_do_not_get_a_made_up_compact_row(svc, tmp_path):
    for harness in ("grok", "antigravity", "claude-code"):
        assert "compact" not in [r["name"] for r in svc.commands_for(harness, folder=str(tmp_path))], harness
