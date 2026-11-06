"""v1.439.0 review: defects the independent review found in the Build › Code
parity lane (each pin red on the lane's own commit).

1. A rename reached the running tool BEFORE the PATCH was saved: a PATCH a
   later field refused (Full access with no confirm) left the session's name
   unchanged while Claude Code had already been renamed.
2. A rename is fire-and-forget: a tool that never answers never delays it.
3. A typed ``/model`` on a running Claude Code said the switch TWICE (Claude
   Code's own "now answers with …" line and Iron Jarvis's).
4. A Claude Code built-in that needs its terminal, sent as a NOTE, said "send
   it once Claude Code has finished" — and then it would only be refused.
   It says its own words.
5. OpenCode's kept ``/`` lists are read on the harnesses route's worker
   thread while the loop may keep another folder's list: read through a copy.
6. ``/model <name>`` that names two models is refused, never a guess.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as CL
from iron_jarvis.hosted import opencode as O
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import typed as T
from iron_jarvis.hosted.events import EventLog
from tests._build_app_helpers_v1409 import client, frames, new_session, sent, stop_all, until, use_fake


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _fresh_lists(monkeypatch):
    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    monkeypatch.setattr(O, "LAST_MODELS", [])
    monkeypatch.setattr(O, "LAST_DEFAULT", [])


def _renames(log: Path) -> list[dict]:
    return [m["request"] for m in sent(log) if m.get("type") == "control_request"
            and (m.get("request") or {}).get("subtype") == "rename_session"]


def _notices(app, sid: str) -> list[str]:
    svc = S.service_for(app.state.platform)
    live = svc.live.get(sid)
    evs = (live.log if live is not None else EventLog(svc.home, sid)).read(5000)[0]
    return [e["text"] for e in evs if e.get("type") == "notice"]


async def test_a_refused_patch_renames_nothing_the_tool_included(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        before = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["title"]
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"title": "Refused name", "mode": "full"})
        assert r.status_code == 400
        assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["title"] == before
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"title": "Saved name"})
        assert r.status_code == 200
        got = await until(lambda: _renames(log))
        await asyncio.sleep(0.3)  # any late rename of the refused PATCH would be here by now
        assert [g["title"] for g in _renames(log)] == ["Saved name"], got
    await stop_all(app)


async def test_a_rename_the_tool_never_answers_never_delays_the_patch(app, work):
    svc = S.service_for(app.state.platform)
    asked: list[str] = []

    class Hangs:
        async def rename(self, title):
            asked.append(title)
            await asyncio.Event().wait()  # never answers

    async with client(app) as c:
        s = await new_session(c, work)
        live = svc._live(store.get(app.state.platform.engine, s["id"]))
        live.proc = SimpleNamespace(alive=True)
        live.driver = Hangs()
        r = await asyncio.wait_for(c.patch(f"/build/app/sessions/{s['id']}", json={"title": "Quick"}), 10)
        assert r.status_code == 200 and r.json()["session"]["title"] == "Quick"
        await until(lambda: asked)
        live.driver = None
        live.proc = None
    await stop_all(app)


async def test_a_typed_model_on_a_running_claude_code_is_said_once(app, work, tmp_path, monkeypatch):
    from iron_jarvis.providers import claude_models

    monkeypatch.setattr(claude_models, "catalog", lambda refresh=False: {"models": [
        {"id": "haiku", "label": "Haiku 5.5"}, {"id": "opus", "label": "Opus 5.5"}]})
    monkeypatch.setattr(claude_models, "model_efforts", lambda model: None)
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        # (the recording answers set_permission_mode first, then set_model
        # with {"model": "claude-haiku-5-5"} — the answer Claude Code's own
        # notice line is made from)
        await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "edits"})
        before = len(_notices(app, s["id"]))
        evs = frames((await c.post(f"/build/app/sessions/{s['id']}/turn",
                                   json={"text": "/model haiku", "turn_id": "t2"})).text)
        assert evs[-1]["stop"] == "completed"
        said = _notices(app, s["id"])[before:]
        model_lines = [n for n in said if "haiku" in n.lower()]
        assert model_lines == ["Claude Code now uses Haiku 5.5."], said
        # The driver says a chip's switch again afterwards.
        assert svc.live[s["id"]].driver.say_model_switch is True
    await stop_all(app)


async def test_a_terminal_only_built_in_as_a_note_says_its_own_words(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn",
                                            json={"text": "x", "turn_id": "t1"}))
        await until(lambda: svc.live.get(s["id"]) and svc.live[s["id"]].asks)
        r = await c.post(f"/build/app/sessions/{s['id']}/steer", json={"text": "/login"})
        assert r.status_code == 400 and r.json()["detail"] == T.CLAUDE_TERMINAL_ONLY["login"]
        r = await c.post(f"/build/app/sessions/{s['id']}/steer", json={"text": "/permissions"})
        assert r.status_code == 400 and r.json()["detail"] == T.CLAUDE_TERMINAL_ONLY["permissions"]
        ask = next(iter(svc.live[s["id"]].asks))
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": ask, "decision": "deny"})
        await asyncio.wait_for(task, 30)
    await stop_all(app)


def test_opencodes_kept_lists_are_read_through_a_copy(monkeypatch):
    class Racy(dict):
        """Another folder's list lands (on the loop) while the harnesses route
        reads the lists on a worker thread: iterating the live dict raises,
        as a real dict does when it changes size mid-iteration."""

        def items(self):
            size = len(self)
            for k, v in list(super().items()):
                yield k, v
                dict.__setitem__(self, "opencode@late", [{"name": "late"}])
                if len(self) != size:
                    raise RuntimeError("dictionary changed size during iteration")

    kept = Racy({"opencode@a": [{"name": "init"}, {"name": "x"}], "opencode@b": [{"name": "init"}]})
    monkeypatch.setattr(CL, "LAST_COMMANDS", kept)
    assert [c["name"] for c in S._kept_commands(O.HARNESS, "")] == ["init"]


def test_a_model_name_two_models_share_is_refused_never_guessed():
    rows = [{"id": "", "label": "OpenCode's default"},
            {"id": "lab/opus", "label": "Opus"}, {"id": "cloud/opus", "label": "Opus"}]
    assert T.resolve_model("opus", rows) is None
    assert T.resolve_model("lab/opus", rows) == "lab/opus"
    assert T.resolve_model("default", rows) == ""
