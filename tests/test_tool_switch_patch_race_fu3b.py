"""FU-3b — a change of the model, effort or mode racing a switch of the
coding tool (item 3b of ``docs/BUILD-CODE-FOLLOW-UP-PLAN-2026-11-20.md``;
``hosted/service.py`` ``patch``, ``hosted/tool_switch.py`` ``switch``).

``patch`` checks a model, effort or mode against the row it read and writes
it onto the row with waits in between, and the switch did not wait for it:
what was picked for the earlier tool (a model name is checked for its shape
only) could land on the NEW tool's row, and the switch parked the earlier
tool without it. Now:

* a change sent while a switch holds its claim is refused at once, in words,
  and the row the switch writes has the new tool's own values;
* a change already under way when the switch starts lands on the EARLIER
  tool first (the switch waits for it), so the switch parks it with that
  tool and the new tool's row never carries it — proven by order: the
  switch is seen WAITING for the change, never by two wall times;
* a new name for the session is the session's, not the tool's: never held up.

Real routes and service; Claude Code and Codex replay their recordings.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import tool_switch as TS
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import FIX, client, new_session, stop_all, until

TWO = Path(__file__).parent / "fixtures" / "fake_two_tools_v1545.py"
#: A model of Claude Code's (the earlier tool's) list.
PICKED = "claude-opus-5-5"
REFUSED = W.CLAIMED.format(what=TS.CLAIM)


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def homes(tmp_path, monkeypatch) -> Path:
    """The tools' own homes are scratch folders (never this PC's)."""
    for name, var in (("claude-home", "CLAUDE_CONFIG_DIR"), ("codex-home", "CODEX_HOME")):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(var, str(tmp_path / name))
    return tmp_path


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _two_tools(monkeypatch, tmp_path: Path, app) -> None:
    """Claude Code and Codex (as ``test_tool_switch_session_v1545``): each
    replays its own recording; every tool counts as installed."""
    fresh = json.loads((FIX / "codex_resume.json").read_text(encoding="utf-8"))
    for step in fresh["steps"]:
        if step.get("await") == "request" and step.get("method") == "thread/resume":
            step["method"] = "thread/start"
    (tmp_path / "codex_start.json").write_text(json.dumps(fresh), encoding="utf-8")
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(TWO), harness])
    monkeypatch.setenv("IJ_FAKE_SCENARIO_CLAUDE_CODE", str(FIX / "claude_resume.json"))
    monkeypatch.setenv("IJ_FAKE_SCENARIO_CODEX", str(tmp_path / "codex_start.json"))
    monkeypatch.setenv("IJ_FAKE_LOG", str(tmp_path / "fake.log"))
    svc = S.service_for(app.state.platform)
    rows = [{"id": h, "label": W.label(h), "installed": True, "signed_in": None} for h in S.HARNESSES]
    monkeypatch.setattr(svc, "harnesses", lambda **kw: [dict(r) for r in rows])


async def _turn(c, sid: str, text: str, tid: str) -> None:
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": tid})
    assert r.status_code == 200, r.text


def _switch(c, sid: str, tool: str):
    return c.post(f"/build/app/sessions/{sid}/switch-tool", json={"tool": tool, "confirm": True})


def _patch(c, sid: str, body: dict):
    return c.patch(f"/build/app/sessions/{sid}", json=body)


def _row(app, sid: str):
    return store.get(app.state.platform.engine, sid)


def _settings(row) -> tuple[str, str, str, str]:
    return (row.harness, row.model or "", row.effort or "", row.mode or "")


async def test_a_change_sent_while_the_switch_runs_is_refused_in_words(app, work, homes, tmp_path, monkeypatch):
    _two_tools(monkeypatch, tmp_path, app)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        await _turn(c, sid, "Say ready", "t1")
        before = _settings(_row(app, sid))
        assert before[0] == "claude-code"
        # The switch stops in its middle: its claim is held, the old tool is
        # being stopped, the new row is not written yet.
        held, go = asyncio.Event(), asyncio.Event()
        real_stop = svc._stop_process

        async def slow_stop(live, **kw):
            if kw.get("why") == TS.CLAIM:
                held.set()
                await go.wait()
            return await real_stop(live, **kw)

        monkeypatch.setattr(svc, "_stop_process", slow_stop)
        switching = asyncio.ensure_future(_switch(c, sid, "codex"))
        await asyncio.wait_for(held.wait(), 30)

        # Each tool setting is refused AT ONCE (it never waits for the
        # switch's end to be checked against the new tool), in words.
        for body in ({"model": PICKED}, {"effort": "high"}, {"mode": "plan"},
                     {"load_folder_resources": True}, {"model": PICKED, "title": "Both at once"}):
            r = await asyncio.wait_for(_patch(c, sid, body), 10)
            assert r.status_code == 409, (body, r.text)
            assert r.json()["detail"] == REFUSED
        assert _settings(_row(app, sid)) == before
        assert _row(app, sid).title != "Both at once"  # nothing of a refused change is saved
        # A name is the session's, not the tool's: it is never held up.
        r = await asyncio.wait_for(_patch(c, sid, {"title": "Floats"}), 10)
        assert r.status_code == 200, r.text

        go.set()
        r = await asyncio.wait_for(switching, 30)
        assert r.status_code == 200, r.text
        plan = r.json()["plan"]
        row = _row(app, sid)
        # The new tool's row has the new tool's own values, nothing picked for the old one.
        assert _settings(row) == ("codex", plan["model"], plan["effort"], plan["mode"])
        assert row.model != PICKED and row.title == "Floats"
        assert TS.read(svc.home, sid)["parked"]["claude-code"]["model"] == before[1]
        # Once the switch has ended a change is checked against Codex and lands there.
        r = await _patch(c, sid, {"model": "gpt-5.5"})
        assert r.status_code == 200, r.text
        assert _settings(_row(app, sid))[:2] == ("codex", "gpt-5.5")
        assert svc.live[sid].claim == ""
    await stop_all(app)


async def test_a_change_under_way_lands_on_the_earlier_tool_before_the_switch_reads_the_row(
        app, work, homes, tmp_path, monkeypatch):
    _two_tools(monkeypatch, tmp_path, app)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        await _turn(c, sid, "Say ready", "t1")
        # (No process: the change goes to the row only, "from the next message".)
        await svc._stop_process(svc.live[sid])
        assert _row(app, sid).model != PICKED

        # The change is checked and is now being WRITTEN (its row write is
        # held here until the test lets it go)…
        let_go = threading.Event()
        real_update = store.update

        def held_update(engine, key, **fields):
            if fields.get("model") == PICKED and "harness" not in fields:
                let_go.wait(30)
            return real_update(engine, key, **fields)

        monkeypatch.setattr(S.store, "update", held_update)
        # (The test watches the session's gate: a switch that WAITS for it is seen.)
        waiting = asyncio.Event()

        class Seen(asyncio.Lock):
            async def acquire(self):
                if self.locked():
                    waiting.set()
                return await super().acquire()

        gate = Seen()
        svc._tool_gates[sid] = gate  # (kept by this test for its whole run)
        try:
            patching = asyncio.ensure_future(_patch(c, sid, {"model": PICKED}))
            await until(gate.locked)
            # …when the switch is pressed.
            switching = asyncio.ensure_future(_switch(c, sid, "codex"))
            seen = asyncio.ensure_future(waiting.wait())
            # The switch either waits for the change (seen) or, as before,
            # goes ahead and ends while the change is still being written.
            await asyncio.wait({seen, switching}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
        finally:
            let_go.set()
        r = await asyncio.wait_for(patching, 30)
        assert r.status_code == 200, r.text
        r = await asyncio.wait_for(switching, 30)
        assert r.status_code == 200, r.text
        seen.cancel()
        assert waiting.is_set(), "the switch went ahead while a change of the tool's model was being written"
        plan = r.json()["plan"]
        row = _row(app, sid)
        # Never the earlier tool's pick on the new tool's row…
        assert _settings(row) == ("codex", plan["model"], plan["effort"], plan["mode"])
        assert row.model != PICKED
        # …and the earlier tool keeps it: switching back starts it there.
        assert TS.read(svc.home, sid)["parked"]["claude-code"]["model"] == PICKED
    await stop_all(app)
