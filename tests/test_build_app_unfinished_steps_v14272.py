"""v1.427.2 (a USER BUG, with a screenshot): Build › Code showed spinners on
steps for DAYS. On Oct 31 Claude Code's reply ended, and Claude Code went on
working ON ITS OWN (helper agents running tests, then a reply it started
itself). Iron Jarvis closed in the middle of that, and the session's log ended
with six ``tool`` rows still ``running``. The boot reconcile closed only a reply
the USER started (a ``user`` event with no ``done``), and nothing closed a
step when the tool's process ended with no reply open, so every later visit
drew six spinners on steps nothing ran.

Now a step is "running" only while the tool runs it: every step left open is
closed ``stopped`` ("did not finish") when the process ends, when Iron Jarvis
closes, and at the next start for a log an earlier version left open, and a
step the tool ran on its own gets ONE notice saying why. The log shapes below
are the real log's, with its commands left out.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import open_steps as OS
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.events import session_dir
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, until, use_fake


def _tool(item: str, status: str, at: str, *, turn: str = "", parent: str = "", title: str = "Run tests") -> dict:
    ev = {"type": "tool", "at": at, "item": item, "kind": "command", "name": "Bash",
          "title": title, "input": "{\"command\": \"…\"}", "status": status}
    if turn:
        ev["turn"] = turn
    if parent:
        ev["parent"] = parent
    return ev


def _the_users_log() -> list[dict]:
    """The end of the user's real log (2026-10-31): a reply the user asked
    for ENDED (``done`` completed); after it Claude Code worked on its own (no
    ``turn``) and the app closed with two steps still running."""
    t = "chat-turn-1"
    return [
        {"type": "session", "at": "2026-10-31T20:00:00Z", "harness": "claude-code", "model": "opus"},
        {"type": "user", "at": "2026-10-31T20:05:26Z", "text": "keep going until done", "turn": t},
        _tool("toolu_a", "running", "2026-10-31T20:06:00Z", turn=t),
        _tool("toolu_a", "done", "2026-10-31T20:06:05Z", turn=t),
        {"type": "text_done", "at": "2026-10-31T20:18:58Z", "item": "m1:t0", "text": "Five teams are building.", "turn": t},
        {"type": "done", "at": "2026-10-31T20:18:59Z", "stop": "completed", "turn": t},
        # Claude Code woke itself: nothing below carries a turn.
        _tool("toolu_b", "running", "2026-10-31T20:20:51Z"),
        _tool("toolu_c", "running", "2026-10-31T20:20:56Z", title="Run vitest"),
        _tool("toolu_b", "done", "2026-10-31T20:20:59Z"),
        {"type": "text_done", "at": "2026-10-31T20:21:00Z", "item": "m2:t0", "text": "Let me write a probe test."},
        _tool("toolu_d", "running", "2026-10-31T20:21:03Z", parent="toolu_helper"),
    ]


def _write(home: Path, sid: str, events: list[dict]) -> Path:
    d = session_dir(home, sid)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _last_status(events: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for ev in events:
        if ev.get("type") == "tool":
            out[ev["item"]] = ev["status"]
    return out


# -- the pure part ---------------------------------------------------------------
def test_the_users_log_closes_every_step_left_running_with_one_notice():
    rows = OS.close_in_tail(_the_users_log())
    tools = [r for r in rows if r["type"] == "tool"]
    assert [r["item"] for r in tools] == ["toolu_c", "toolu_d"], "only the two steps still running"
    assert all(r["status"] == OS.STOPPED for r in tools)
    assert tools[0]["title"] == "Run vitest" and tools[0]["kind"] == "command"
    assert tools[1]["parent"] == "toolu_helper", "a helper's step stays inside its helper"
    # Timed to when the record ended — never the boot's own clock (a receipt
    # or a carry-on reads these times).
    assert {r["at"] for r in rows} == {"2026-10-31T20:21:03Z"}
    notices = [r for r in rows if r["type"] == "notice"]
    assert notices == [{**notices[0], "text": W.STEPS_CUT_RESTART.format(label="Claude Code", steps="2 steps")}]
    assert "turn" not in notices[0] and all("turn" not in r for r in tools)


def test_a_cut_replys_own_steps_close_without_a_notice():
    """The cut reply's own error ("Iron Jarvis stopped while this was working")
    already says why: its steps close silently."""
    t = "t9"
    tail = [
        {"type": "user", "at": "2026-10-31T10:00:00Z", "text": "go", "turn": t},
        _tool("x1", "running", "2026-10-31T10:00:05Z", turn=t),
    ]
    rows = OS.close_in_tail(tail)
    assert [(r["type"], r["item"], r["status"]) for r in rows] == [("tool", "x1", "stopped")]


def test_nothing_left_open_means_nothing_written():
    tail = [_tool("a", "running", "2026-10-31T10:00:00Z"), _tool("a", "failed", "2026-10-31T10:00:01Z")]
    assert OS.close_in_tail(tail) == []
    assert OS.close_in_tail([]) == []
    assert OS.close_in_tail([{"type": "tool", "status": "running"}, "junk", None]) == []  # type: ignore[list-item]


def test_a_tool_that_never_reports_its_ends_is_bounded():
    steps: dict = {}
    for i in range(OS.MAX_OPEN + 50):
        OS.note(steps, _tool(f"s{i}", "running", "2026-10-31T10:00:00Z"))
    assert len(steps) == OS.MAX_OPEN and "s0" not in steps and f"s{OS.MAX_OPEN + 49}" in steps


def test_steps_words_say_one_and_many():
    assert W.steps_words(1) == "1 step" and W.steps_words(6) == "6 steps"


# -- the boot reconcile ----------------------------------------------------------
def test_the_boot_closes_the_steps_a_reply_the_tool_started_left_running(tmp_path):
    home = tmp_path / "home"
    path = _write(home, "app_x", _the_users_log())
    assert S.reconcile_cut_turns(home) == 0, "no reply of the user's was cut"
    events = _read(path)
    assert _last_status(events) == {"toolu_a": "done", "toolu_b": "done", "toolu_c": "stopped", "toolu_d": "stopped"}
    assert events[-1]["type"] == "notice" and "did not finish" in events[-1]["text"]
    assert not (session_dir(home, "app_x") / "cut.json").exists(), "nothing to carry on"
    # Once is enough: the next start finds nothing open.
    size = path.stat().st_size
    S.reconcile_cut_turns(home)
    assert path.stat().st_size == size


def test_a_cut_reply_gets_its_steps_closed_before_its_error_and_done(tmp_path):
    home = tmp_path / "home"
    t = "t1"
    path = _write(home, "app_y", [
        {"type": "user", "at": "2026-11-03T10:00:00Z", "text": "go", "turn": t},
        _tool("s1", "running", "2026-11-03T10:00:02Z", turn=t),
    ])
    assert S.reconcile_cut_turns(home) == 1
    tail = _read(path)[-3:]
    assert [(e["type"], e.get("status")) for e in tail] == [("tool", "stopped"), ("error", None), ("done", None)]
    assert tail[-1]["stop"] == "interrupted" and tail[-1]["turn"] == t
    assert (session_dir(home, "app_y") / "cut.json").exists(), "the carry-on record is still kept"


def test_a_session_the_code_host_still_runs_is_left_alone(tmp_path):
    home = tmp_path / "home"
    path = _write(home, "app_z", _the_users_log())
    before = path.read_bytes()
    S.reconcile_cut_turns(home, keep=["app_z"])
    assert path.read_bytes() == before, "its tool is still running those steps"


# -- live: the tool's process ends with steps open --------------------------------
@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _on_its_own(driver, tool_id: str, command: str = "npx vitest run") -> None:
    """Claude Code wakes itself after its reply and starts a command."""
    driver.on_message({"type": "assistant", "message": {"id": f"msg_{tool_id}", "role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command, "description": "Run the tests"}},
    ]}})


async def _events(c, sid) -> list[dict]:
    return (await c.get(f"/build/app/sessions/{sid}")).json()["events"]


async def test_when_the_tool_exits_on_its_own_its_open_steps_did_not_finish(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        frames((await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hi", "turn_id": "t1"})).text)
        live = svc.live[s["id"]]
        _on_its_own(live.driver, "toolu_self")
        assert _last_status(await _events(c, s["id"])).get("toolu_self") == "running"
        assert "toolu_self" in live.open_steps
        proc = live.proc
        # The process ends by itself (not through Iron Jarvis's own stop).
        await asyncio.to_thread(proc.stop)
        await until(lambda: live.proc is None)
        events = await _events(c, s["id"])
        assert _last_status(events)["toolu_self"] == "stopped"
        said = [e["text"] for e in events if e["type"] == "notice"]
        assert W.STEPS_CUT_STOPPED.format(label="Claude Code", steps="1 step") in said
        assert live.open_steps == {}
    await stop_all(app)


async def test_iron_jarvis_closing_closes_the_steps_too(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        frames((await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hi", "turn_id": "t1"})).text)
        live = svc.live[s["id"]]
        _on_its_own(live.driver, "toolu_self")
    await svc.shutdown()
    events = _read(session_dir(svc.home, s["id"]) / "events.jsonl")
    assert _last_status(events)["toolu_self"] == "stopped"
    assert W.STEPS_CUT_RESTART.format(label="Claude Code", steps="1 step") in [
        e.get("text") for e in events if e["type"] == "notice"]
    # The next start has nothing left to close.
    assert OS.close_in_tail(events) == []
    await stop_all(app)


async def test_a_step_that_finishes_is_never_closed(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        frames((await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hi", "turn_id": "t1"})).text)
        live = svc.live[s["id"]]
        _on_its_own(live.driver, "toolu_ok")
        live.driver.on_message({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_ok", "content": "3 passed"},
        ]}})
        assert live.open_steps == {}
        await asyncio.to_thread(live.proc.stop)
        await until(lambda: live.proc is None)
        events = await _events(c, s["id"])
        assert _last_status(events)["toolu_ok"] == "done"
        assert not [e for e in events if e["type"] == "notice" and "did not finish" in e.get("text", "")]
    await stop_all(app)


def test_a_session_the_code_host_kept_remembers_its_open_steps(tmp_path):
    """A tool the code host kept across a restart still runs these steps; once
    adopted, its later exit closes them like any other (``adopt.log_facts``)."""
    from iron_jarvis.hosted import adopt as ADOPT

    home = tmp_path / "home"
    _write(home, "app_k", _the_users_log())
    steps = ADOPT.log_facts(home, "app_k", "")["open_steps"]
    assert list(steps) == ["toolu_c", "toolu_d"]
    assert steps["toolu_d"]["parent"] == "toolu_helper"
