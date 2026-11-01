"""v1.422.0 — Codex and OpenCode carried across an Iron Jarvis restart too
(``hosted/adopt.py``), end to end, from RECORDED sessions
(``tests/fixtures/build_app_v1409/codex_resume.json``,
``build_app_v1413/oc_read.json``) replayed by the fake harness and re-joined
into two messages here:

* idle: the next message goes to the SAME process (one start in all);
* mid-reply: Iron Jarvis dies while the reply is written; afterwards the reply
  ends once — for OpenCode on the prompt's ANSWER, a wait the driver re-arms
  after the restart (``AcpDriver.after_restore``; Grok shares it), for Codex on
  its ``turn/completed`` line, with the turn's id taken from an answer that
  came after the restart (``CodexDriver.after_restore``).

Pi's prompt wait and Claude Code's held reply end are pinned at the driver
(``test_code_host_v14220``); Claude Code end to end in
``test_code_host_adopt_v14220`` and the real-daemon rehearsal.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import adopt as ADOPT
from tests._build_app_helpers_v1409 import client, frames, new_session, until
from tests._code_host_helpers_v14220 import (
    boot,
    config_home,
    crash,
    events_of,
    fake_rows,
    host_session,
    kept,
    use_fake,
)

pytestmark = [pytest.mark.skipif(sys.platform != "win32", reason="the product's restart path is Windows"),
              pytest.mark.timeout(240)]

FIX09 = Path(__file__).parent / "fixtures" / "build_app_v1409"
FIX13 = Path(__file__).parent / "fixtures" / "build_app_v1413"


@pytest.fixture
def hosts(monkeypatch):
    yield from host_session(monkeypatch)


def _codex(pause: float) -> tuple[str, list[dict]]:
    steps = json.loads((FIX09 / "codex_resume.json").read_text(encoding="utf-8"))["steps"]
    out = []
    for s in steps:
        s = json.loads(json.dumps(s))
        if s.get("await") == "request" and s.get("method") == "thread/resume":
            s["method"] = "thread/start"  # a new session starts its thread
        out.append(s)
    start = out[: next(i for i, s in enumerate(out) if s.get("method") == "turn/start")]
    turn = out[len(start):]
    for s in turn:
        if isinstance(s.get("out"), dict) and "id" in s["out"] and "result" in s["out"]:
            s["reply_to"] = "turn/start"
    second = json.loads(json.dumps(turn))
    done = next(i for i, s in enumerate(turn) if isinstance(s.get("out"), dict)
                and s["out"].get("method") == "item/completed"
                and (s["out"].get("params") or {}).get("item", {}).get("type") == "agentMessage")
    first = turn[:done] + [{"sleep": pause}] + turn[done:] if pause else turn
    return "codex", start + first + second


def _opencode(pause: float) -> tuple[str, list[dict]]:
    steps = json.loads((FIX13 / "oc_read.json").read_text(encoding="utf-8"))["steps"]
    at = next(i for i, s in enumerate(steps) if s.get("method") == "session/prompt")
    start, turn = steps[:at], steps[at:]
    turn = json.loads(json.dumps(turn))
    turn[-1]["reply_to"] = "session/prompt"  # the prompt's answer, under the id it was asked with
    second = json.loads(json.dumps(turn))
    answer = len(turn) - 1
    first = turn[:answer] + [{"sleep": pause}] + turn[answer:] if pause else turn
    return "opencode", start + first + second


SCENARIOS = {"codex": _codex, "opencode": _opencode}
#: The model each recording was made with (OpenCode sets it on its session).
MODELS = {"codex": None, "opencode": "opencode/glm-5.3-flash-free"}


def _text(evs: list[dict]) -> str:
    return "".join(str(e.get("delta") or "") for e in evs if e["type"] == "text")


async def _app(hosts, tmp_path: Path, monkeypatch, tool: str, pause: float):
    harness, steps = SCENARIOS[tool](pause)
    scenario = tmp_path / f"{tool}.json"
    scenario.write_text(json.dumps({"harness": harness, "steps": steps}), encoding="utf-8")
    root, log, work = tmp_path / "root", tmp_path / "fake.log", tmp_path / "work"
    work.mkdir()
    hosts.home(config_home(root))
    hosts.logs.append(log)
    use_fake(monkeypatch, scenario, log)
    return create_app(str(root)), root, log, work, harness


def _new(tool: str) -> dict:
    return {"model": MODELS[tool]} if MODELS[tool] else {}


@pytest.mark.parametrize("tool", sorted(SCENARIOS))
async def test_an_idle_session_answers_on_the_same_process(hosts, tmp_path, monkeypatch, tool):
    app, root, log, work, harness = await _app(hosts, tmp_path, monkeypatch, tool, 0)
    async with client(app) as c:
        sid = (await new_session(c, work, harness=harness, **_new(tool)))["id"]
        first = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
    assert first[-1]["stop"] == "completed", first
    pid = app.state.platform.hosted.live[sid].proc.pid
    await kept(app, sid)
    crash(app)
    app2 = await boot(root)
    assert app2.state.platform.hosted.live[sid].proc.pid == pid
    async with client(app2) as c2:
        second = frames((await asyncio.wait_for(
            c2.post(f"/build/app/sessions/{sid}/turn", json={"text": "two", "turn_id": "t2"}), 60)).text)
    assert second[-1]["stop"] == "completed", second
    assert not [e for e in second if e["type"] == "notice" and "start" in str(e.get("text", "")).lower()]
    assert len([r for r in fake_rows(log) if "argv" in r]) == 1, "nothing was started again"
    await ADOPT.shutdown(app2.state.platform, keep=False)


@pytest.mark.parametrize("tool", sorted(SCENARIOS))
async def test_a_reply_mid_way_ends_once_after_the_restart(hosts, tmp_path, monkeypatch, tool):
    app, root, log, work, harness = await _app(hosts, tmp_path, monkeypatch, tool, 4.0)
    home = config_home(root)
    c = client(app)
    sid = (await new_session(c, work, harness=harness, **_new(tool)))["id"]
    follow = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t1"}))
    svc = app.state.platform.hosted
    await until(lambda: svc.turn("t1") and any(e["type"] in ("text", "thinking", "tool") for e in svc.turn("t1").events))
    before = _text(svc.turn("t1").events)
    await kept(app, sid)
    crash(app)
    follow.cancel()
    await c.aclose()
    app2 = await boot(root)
    svc2 = app2.state.platform.hosted
    assert svc2.turn("t1") is not None and svc2.turn("t1").running
    async with client(app2) as c2:
        rest = frames((await asyncio.wait_for(c2.get(f"/build/app/sessions/{sid}/turns/t1/stream"), 60)).text)
        assert rest[-1]["type"] == "done" and rest[-1]["stop"] == "completed", rest
        if tool == "codex":
            assert svc2.live[sid].driver.turn_id, "Stop and a note can name the turn again"
        nxt = frames((await asyncio.wait_for(
            c2.post(f"/build/app/sessions/{sid}/turn", json={"text": "two", "turn_id": "t2"}), 60)).text)
    assert nxt[-1]["stop"] == "completed", "and the session goes on"
    logged = [e for e in events_of(home, sid, app2) if e.get("turn") == "t1"]
    assert len([e for e in logged if e["type"] == "done"]) == 1, "its end is written once"
    assert _text(logged).startswith(before) and _text(logged).count(before or "\0") <= 1, "nothing shown twice"
    assert len([r for r in fake_rows(log) if "argv" in r]) == 1
    await ADOPT.shutdown(app2.state.platform, keep=False)
