"""v1.441.0 (lane g1) REVIEW: the defects the independent review found in the
Session tab's live controls, each pinned red first.

* A live effort change while a reply RUNS: re-measured on Claude Code 2.1.316
  (scratch ``CLAUDE_CONFIG_DIR``, a stand-in Anthropic upstream, the change
  sent while a ``can_use_tool`` card was open): ``get_settings`` already said
  ``high``, but the running reply's NEXT request still carried
  ``output_config.effort: "medium"``; the user's next message carried
  ``"high"``. So the words must say "from your next message", never "now".
* Codex's terminal reads are Iron Jarvis's OWN requests: their answers must
  never count as hearing from the tool, or a forgotten dev server keeps the
  session alive past the v1.418.0 two silent hours, for ever.
* Two presses of Stop on one Claude Code task (re-measured: a second
  ``stop_task`` of the same id answers ``{}`` again) must send one stop and
  say it once; a task being stopped is not listed.
* Codex's own refusal of a stale id is said in Iron Jarvis's words.
* The Stop route never waits longer than the list bound on a slow tool.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import session_controls as SC
from iron_jarvis.hosted.driver import ControlRefused
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, use_fake
from tests.test_session_controls_v14410 import Peer, claude_ok, claude_settings, codex_peer


def _task(d: Any, sub: str, tid: str, **extra: Any) -> None:
    d.on_message({"type": "system", "subtype": sub, "task_id": tid, **extra})


# --------------------------------------------------------------------------- #
# live effort while a reply runs
# --------------------------------------------------------------------------- #


async def test_an_effort_change_during_a_reply_says_it_applies_from_the_next_message():
    p = Peer(C.ClaudeDriver, claude_settings("high"))
    p.driver.turn_open = True
    # No restart is needed (Claude Code took it), but the reply running now
    # keeps its effort (measured): the words must not say "now".
    assert await p.driver.set_effort("high") == "now"
    (said,) = p.notices()
    assert "next message" in said and "now works" not in said, said


async def test_an_effort_change_between_replies_says_now_and_names_extra_in_plain_words():
    p = Peer(C.ClaudeDriver, claude_settings("xhigh"))
    assert await p.driver.set_effort("xhigh") == "now"
    (said,) = p.notices()
    assert "xhigh" not in said and "extra" in said.lower(), said


# --------------------------------------------------------------------------- #
# Codex: Iron Jarvis's own reads are not the tool speaking
# --------------------------------------------------------------------------- #


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


async def test_codex_terminal_reads_never_count_as_hearing_from_the_tool(tmp_path, work, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_signin", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="codex")
        frames((await c.post(f"/build/app/sessions/{s['id']}/turn",
                             json={"text": "ready?", "turn_id": "t1"})).text)
        live = svc.live[s["id"]]
        await asyncio.sleep(0.3)  # the read after the turn's end has come back
        old = svc.clock() - 3 * 60 * 60
        live.last_heard = old
        # The fake answers the list request plainly (a REAL reply frame from
        # the process, through the service's own on_message).
        rows = await live.driver._read_terminals()
        assert rows == []
        assert live.last_heard == old, "Iron Jarvis's own terminal read stamped the tool as heard from"
        # Anything else the tool says still counts.
        await live.driver.request("thread/list", {})
        assert live.last_heard > old
    await stop_all(app)


def test_the_quiet_hook_is_wired_where_kept_sessions_are_adopted_too():
    """The code host's adopt path has its own on_message: it must ask the
    same hook (a source pin; the adopt path is driven by the host tests)."""
    src = Path(SC.__file__).with_name("adopt.py").read_text(encoding="utf-8")
    assert "heard_from(" in src


# --------------------------------------------------------------------------- #
# Claude Code: two presses of Stop
# --------------------------------------------------------------------------- #


async def test_a_second_stop_of_the_same_claude_task_sends_nothing_and_is_not_listed():
    p = Peer(C.ClaudeDriver, claude_ok())
    _task(p.driver, "task_started", "bz1", task_type="local_bash", description="Long ping",
          is_backgrounded=True)
    # The notification has not come yet (a press racing the answer).
    p.answer = lambda msg: None

    async def first() -> str:
        return await p.driver.stop_background("bz1")

    t = asyncio.ensure_future(first())
    await asyncio.sleep(0)
    with pytest.raises(ControlRefused, match="already stopping"):
        await p.driver.stop_background("bz1")
    assert [r for r in p.requests() if r.get("subtype") == "stop_task"] == [{"subtype": "stop_task", "task_id": "bz1"}]
    assert await p.driver.background_work() == []
    t.cancel()


# --------------------------------------------------------------------------- #
# Codex: a stale id in plain words
# --------------------------------------------------------------------------- #


async def test_codex_refusing_a_stale_terminal_id_is_said_in_plain_words():
    p = Peer(X.CodexDriver, codex_peer({
        "thread/backgroundTerminals/terminate": RuntimeError("invalid background terminal process id")}))
    p.driver.session_id = "thr1"
    with pytest.raises(ControlRefused) as exc:
        await p.driver.stop_background("999")
    assert str(exc.value) == X.TERMINAL_ALREADY_ENDED


# --------------------------------------------------------------------------- #
# the Stop route is bounded
# --------------------------------------------------------------------------- #


async def test_the_stop_route_does_not_wait_on_a_slow_list(monkeypatch):
    monkeypatch.setattr(SC, "LIST_TIMEOUT_S", 0.2)

    class Slow:
        harness = "codex"

        async def background_work(self):
            await asyncio.sleep(30)

        async def stop_background(self, work_id):
            return "npm run dev"

    emitted: list[dict[str, Any]] = []
    live = SimpleNamespace(driver=Slow(), running=True, turn=None)
    row = SimpleNamespace(id="s1", harness="codex")

    async def _row(sid):
        return row

    svc = SimpleNamespace(_row=_row, live={"s1": live}, _emit=lambda lv, ev: emitted.append(ev))
    out = await asyncio.wait_for(SC.stop_background(svc, "s1", "1"), timeout=5)
    assert out["title"] == "npm run dev"
    assert emitted and "npm run dev" in emitted[0]["text"]


# --------------------------------------------------------------------------- #
# the builder's Codex claims no pin held (a mutation of each stayed green)
# --------------------------------------------------------------------------- #


async def test_the_terminal_follow_never_writes_a_read_while_a_turn_is_open(monkeypatch):
    from tests.test_session_controls_v14410 import MEASURED_LIST

    monkeypatch.setattr(X, "TERMINALS_POLL_S", 0.02)
    p = Peer(X.CodexDriver, codex_peer({"thread/backgroundTerminals/list": MEASURED_LIST}))
    p.driver.session_id = "thr1"
    p.driver.turn_open = True
    p.driver._arm_terminal_poll()
    await asyncio.sleep(0.2)
    assert [r for r in p.requests() if r.get("method") == "thread/backgroundTerminals/list"] == []
    p.driver.turn_open = False
    for _ in range(100):
        if p.driver.background_tasks == 1:
            break
        await asyncio.sleep(0.01)
    assert p.driver.background_tasks == 1
    for t in list(p.driver._bg):
        t.cancel()


async def test_codex_terminals_end_with_its_process():
    p = Peer(X.CodexDriver, codex_peer({}))
    p.driver._terminals = [{"id": "1", "title": "npm run dev", "kind": "command", "can_stop": True}]
    p.driver._terminal_count = 1
    p.driver.on_exit()
    assert p.driver.background_tasks == 0 and p.driver._terminals == []
