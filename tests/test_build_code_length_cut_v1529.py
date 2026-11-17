"""Owner decision 5 after the third fix plan (RB10's open half): a Build ›
Code reply that the coding tool ended at the MODEL'S LENGTH LIMIT is not a
finished reply.

Before: ``hosted/acp.py`` turned ACP's ``stopReason: "max_tokens"`` into the
stop "completed", so the session's own line said nothing, the bell and the
tray said "Your coding session finished" with its check mark, and the queue
sent the next waiting message as if the work were done. Claude Code's own
``stop_reason: "max_tokens"`` and Pi's ``stopReason: "length"`` read the
same way.

Now, as for a Chat › Code reply cut at its length (``routes/build.
finished_notice_outcome``: ``truncated`` is "failed"), the reply ends on the
stop "failed" with ``cut: "length"`` on its ``done``, a notice says it in
plain words, and the finished notice is "failed" ("Your coding session could
not finish"). A Stop pressed meanwhile is still a Stop.

The end-to-end case replays a RECORDED OpenCode session (``tests/fixtures/
build_app_v1413/oc_read.json``) through the real service, the real process
plumbing and the real ACP mapping, with only the prompt's answer changed to
``max_tokens``."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity as A
from iron_jarvis.hosted import claude as CL
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import news as NEWS
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.grok import GrokDriver
from iron_jarvis.hosted.opencode import OpenCodeDriver
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, stop_all

FIX_OC = Path(__file__).parent / "fixtures" / "build_app_v1413"
MODEL = "opencode/glm-5.3-flash-free"


def _kw(seen: list) -> dict:
    return {"write": None, "emit": seen.append, "ask": lambda a: None, "on_session_id": lambda s: None}


def _acp(tool: str, seen: list):
    if tool == "antigravity":
        return A.AntigravityDriver(**_kw(seen), mode="own")
    if tool == "grok":
        return GrokDriver(**_kw(seen), mode="ask")
    return OpenCodeDriver(**_kw(seen), mode="ask")


def _answer(d, result: dict, *, interrupting: bool = False) -> None:
    """The tool answers the message's ``session/prompt`` (the real handler)."""
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        d.turn_open = True
        d._interrupting = interrupting
        d._pending = {1: ""}
        d._message_prompt = 1
        d._latest = 1  # Antigravity's newest prompt
        fut.set_result(result)
        d._prompt_answered(1, fut)
    finally:
        loop.close()


def _done(seen: list) -> dict:
    dones = [e for e in seen if e["type"] == "done"]
    assert len(dones) == 1, seen
    return dones[0]


@pytest.mark.parametrize("tool", ["opencode", "grok", "antigravity"])
def test_an_acp_reply_cut_at_the_models_length_limit_is_not_finished(tool):
    seen: list[dict] = []
    d = _acp(tool, seen)
    _answer(d, {"stopReason": "max_tokens"})
    done = _done(seen)
    assert done["stop"] == "failed", done
    assert done.get("cut") == E.CUT_LENGTH
    # Said once, in plain words, before the reply's end.
    notices = [e["text"] for e in seen if e["type"] == "notice"]
    assert notices.count(W.OUTPUT_LIMIT_CUT) == 1, notices
    assert seen.index(next(e for e in seen if e["type"] == "notice")) < seen.index(done)
    # The bell, the tray and the phone: never "finished".
    assert NEWS.outcome_of(done["stop"]) == "failed"


@pytest.mark.parametrize("tool", ["opencode", "antigravity"])
def test_a_stop_pressed_before_the_length_cut_is_still_a_stop(tool):
    seen: list[dict] = []
    d = _acp(tool, seen)
    _answer(d, {"stopReason": "max_tokens"}, interrupting=True)
    done = _done(seen)
    assert done["stop"] == "interrupted" and "cut" not in done


@pytest.mark.parametrize("tool", ["opencode", "antigravity"])
def test_an_ordinary_end_has_no_cut(tool):
    seen: list[dict] = []
    d = _acp(tool, seen)
    _answer(d, {"stopReason": "end_turn"})
    done = _done(seen)
    assert done["stop"] == "completed" and "cut" not in done
    assert not [e for e in seen if e["type"] == "notice" and e["text"] == W.OUTPUT_LIMIT_CUT]


def test_the_next_message_starts_without_the_last_replys_cut():
    seen: list[dict] = []
    d = _acp("opencode", seen)
    _answer(d, {"stopReason": "max_tokens"})
    seen.clear()
    _answer(d, {"stopReason": "end_turn"})
    # (``send_user`` resets it; a second answer on the same driver must too.)
    assert _done(seen)["stop"] == "completed"


# --------------------------------------------------------------------------- #
# Claude Code: its result's own stop_reason
# --------------------------------------------------------------------------- #
def _claude_result(stop_reason: str, *, interrupting: bool = False) -> list[dict]:
    seen: list[dict] = []
    d = CL.ClaudeDriver(**_kw(seen), mode="ask")
    d.turn_open = True
    d._interrupting = interrupting
    d._on_result({"type": "result", "subtype": "success", "is_error": False, "stop_reason": stop_reason,
                  "terminal_reason": "completed", "result": "Here is the first half", "usage": {}})
    return seen


def test_a_claude_code_reply_cut_at_the_length_limit_is_not_finished():
    seen = _claude_result("max_tokens")
    done = _done(seen)
    assert done["stop"] == "failed" and done.get("cut") == E.CUT_LENGTH
    assert W.OUTPUT_LIMIT_CUT in [e["text"] for e in seen if e["type"] == "notice"]
    assert NEWS.outcome_of(done["stop"]) == "failed"


def test_a_claude_code_reply_that_ended_is_finished_as_before():
    done = _done(_claude_result("end_turn"))
    assert done["stop"] == "completed" and "cut" not in done
    assert done["result"] == "Here is the first half"


# --------------------------------------------------------------------------- #
# Pi: the last assistant message's stopReason
# --------------------------------------------------------------------------- #
def _pi(seen: list) -> P.PiDriver:
    d = P.PiDriver(**_kw(seen), mode="own")
    d.turn_open = True
    return d


def test_a_pi_run_whose_last_answer_was_cut_at_its_length_is_not_finished():
    seen: list[dict] = []
    d = _pi(seen)
    d._on_message_end({"role": "assistant", "stopReason": "length", "usage": {}})
    d._settled(False)
    done = _done(seen)
    assert done["stop"] == "failed" and done.get("cut") == E.CUT_LENGTH
    assert [e["text"] for e in seen if e["type"] == "notice"].count(W.OUTPUT_LIMIT_CUT) == 1


def test_a_pi_run_that_carried_on_after_a_cut_and_ended_is_finished():
    seen: list[dict] = []
    d = _pi(seen)
    d._on_message_end({"role": "assistant", "stopReason": "length", "usage": {}})
    d._on_message_end({"role": "assistant", "stopReason": "stop", "usage": {}})
    d._settled(False)
    done = _done(seen)
    assert done["stop"] == "completed" and "cut" not in done
    assert not [e for e in seen if e["type"] == "notice" and e["text"] == W.OUTPUT_LIMIT_CUT]


# --------------------------------------------------------------------------- #
# End to end: a recorded OpenCode reply, its answer changed to max_tokens
# --------------------------------------------------------------------------- #
def _scenario_cut_at_length(tmp_path: Path) -> Path:
    rec = json.loads((FIX_OC / "oc_read.json").read_text(encoding="utf-8"))
    last = rec["steps"][-1]["out"]
    assert last["result"]["stopReason"] == "end_turn", "the recording this test changes"
    last["result"]["stopReason"] = "max_tokens"
    out = tmp_path / "oc_read_max_tokens.json"
    out.write_text(json.dumps(rec), encoding="utf-8")
    return out


async def test_a_build_code_reply_cut_at_its_length_is_recorded_and_announced_as_not_finished(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    app = create_app(str(tmp_path / "home"))
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(_scenario_cut_at_length(tmp_path)))
    monkeypatch.setenv("IJ_FAKE_LOG", str(tmp_path / "fake.log"))
    # Every reply is "long" here, so the finished notice is published.
    monkeypatch.setattr(NEWS, "FINISH_NOTICE_AFTER_S", -1.0)
    got: list = []
    app.state.platform.event_bus.add_handler(got.append)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, harness="opencode", model=MODEL))["id"]
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{sid}/turn",
                                                    json={"text": "do it", "turn_id": "t"}), 30)).text)
        for _ in range(100):  # the notice is published after the done
            if any(getattr(e, "type", "") == EventType.BUILD_TURN_FINISHED for e in got):
                break
            await asyncio.sleep(0.05)
        turn = svc.turn("t")
    await stop_all(app)
    # The session's own record: not finished, and why, in plain words.
    done = evs[-1]
    assert done["type"] == "done" and done["stop"] == "failed", evs[-3:]
    assert done.get("cut") == E.CUT_LENGTH
    assert W.OUTPUT_LIMIT_CUT in [e.get("text") for e in evs if e["type"] == "notice"]
    assert turn is not None and turn.status == "failed"
    # The bell, the tray and the phone: "could not finish", never "finished".
    finished = [e for e in got if getattr(e, "type", "") == EventType.BUILD_TURN_FINISHED]
    assert len(finished) == 1, finished
    assert finished[0].payload["outcome"] == "failed"
