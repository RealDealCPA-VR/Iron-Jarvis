"""v1.557.0: Build › Code's ``/`` list BEFORE a session exists, and the
``reading`` flag that lets a page look again.

The owner: "In the build > code area, the / commands for skills dont work.
These should be available at any point within this prompting window."

Read from the live app (v1.556.0): the start box had no list, the tools
listing gave Claude Code 0 commands, and a session's first look answered 0
while the folder's list was read in the background (90 a moment later, the
user's own skills among them). Claude Code's list was only ever read for a
session's folder, so with no folder there was nothing to show.

Every read here is the stand-in ``initialize`` answerer below, through the
real ``hosted.claude_catalog`` (``core.procs``), never a real Claude Code.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import claude_catalog as CCAT
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, new_session, stop_all

FAKE_INIT = r'''
import json, os, sys
line = sys.stdin.readline()
with open(os.environ["IJ_FAKE_INIT_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"cwd": os.getcwd()}) + "\n")
req = json.loads(line)
cmds = [{"name": "compact", "description": "Free up context", "argumentHint": "<focus>", "builtin": True},
        {"name": "my-skill", "description": "One of the user's own skills (user)", "argumentHint": ""}]
print(json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": req["request_id"],
                  "response": {"commands": cmds}}}), flush=True)
'''


@pytest.fixture
def work(tmp_path):
    p = tmp_path / "work"
    p.mkdir()
    return p


@pytest.fixture
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


@pytest.fixture
def fake_init(tmp_path, monkeypatch):
    """The background read is ON and answered by the stand-in."""
    script = tmp_path / "fake_claude_init.py"
    script.write_text(FAKE_INIT, encoding="utf-8")
    log = tmp_path / "init.log"
    monkeypatch.setenv("IJ_FAKE_INIT_LOG", str(log))
    monkeypatch.setattr(CCAT, "AUTO_PROBE", True)
    monkeypatch.setattr(CCAT, "_FAILED_AT", {})
    monkeypatch.setattr(C, "LAST_COMMANDS", {})
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(script)])
    return log


def _wait(pred, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _read_in(log: Path) -> list[Path]:
    if not log.exists():
        return []
    return [Path(json.loads(x)["cwd"]).resolve() for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def _names(state: dict) -> list[str]:
    return [r["name"] for r in state["commands"] if not r.get("note")]


def _known(folder) -> bool:
    return bool(C.LAST_COMMANDS.get(C.commands_key(C.HARNESS, str(folder)))) and not CCAT.reading(str(folder))


async def test_no_folder_chosen_reads_the_list_in_iron_jarviss_own_folder(app, fake_init):
    svc = S.service_for(app.state.platform)
    own = CCAT.no_folder_dir(svc.home)
    assert not own.exists()
    first = await asyncio.to_thread(svc.start_commands, "claude-code", "")
    # Nothing is known yet: the answer says it is being read, never "none".
    assert first == {"commands": [], "reading": True}
    assert await asyncio.to_thread(_wait, lambda: _known(own))
    after = await asyncio.to_thread(svc.start_commands, "claude-code", "")
    assert after["reading"] is False
    assert _names(after) == ["compact", "my-skill"], "Claude Code's own and the user's own skill"
    assert after["commands"][0]["argument_hint"] == "<focus>"
    assert _read_in(fake_init) == [own.resolve()], "one read, in Iron Jarvis's own folder"
    # Known now: asking again starts nothing.
    await asyncio.to_thread(svc.start_commands, "claude-code", "")
    assert len(_read_in(fake_init)) == 1


async def test_a_chosen_folder_gets_its_own_list(app, work, fake_init):
    svc = S.service_for(app.state.platform)
    assert (await asyncio.to_thread(svc.start_commands, "claude-code", str(work)))["reading"] is True
    assert await asyncio.to_thread(_wait, lambda: _known(work))
    after = await asyncio.to_thread(svc.start_commands, "claude-code", str(work))
    assert after["reading"] is False and "my-skill" in _names(after)
    assert _read_in(fake_init) == [work.resolve()]


@pytest.mark.parametrize("bad", ["gone", "relative\\path"])
async def test_a_folder_a_session_could_not_start_in_is_never_read(app, tmp_path, fake_init, bad):
    svc = S.service_for(app.state.platform)
    folder = str(tmp_path / "gone") if bad == "gone" else bad
    await asyncio.to_thread(svc.start_commands, "claude-code", folder)
    own = CCAT.no_folder_dir(svc.home)
    assert await asyncio.to_thread(_wait, lambda: _known(own))
    assert _read_in(fake_init) == [own.resolve()], "the no-folder list, never a read in that folder"
    assert not CCAT.reading(folder)


async def test_another_tool_starts_no_read_and_an_unknown_tool_is_refused(app, work, fake_init):
    svc = S.service_for(app.state.platform)
    got = await asyncio.to_thread(svc.start_commands, "codex", str(work))
    assert got["reading"] is False
    assert [r["name"] for r in got["commands"]] == ["compact"], "Codex's own compact, before its first start"
    time.sleep(0.2)
    assert _read_in(fake_init) == [] and C.LAST_COMMANDS == {}
    with pytest.raises(S.Refused) as said:
        svc.start_commands("notepad", "")
    assert said.value.status == 400


async def test_the_routes_say_the_list_and_whether_it_is_being_read(app, work, fake_init):
    async with client(app) as c:
        r = await c.get("/build/app/commands", params={"harness": "claude-code"})
        assert r.status_code == 200 and r.json() == {"commands": [], "reading": True}
        assert (await c.get("/build/app/commands", params={"harness": "notepad"})).status_code == 400
        assert (await c.get("/build/app/commands")).status_code == 400

        # A session's own answers carry it too: the view, and its `/commands`.
        s = await new_session(c, work)
        view = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        assert view["commands_reading"] is True
        assert await asyncio.to_thread(_wait, lambda: _known(work))
        listed = (await c.get(f"/build/app/sessions/{s['id']}/commands")).json()
        assert listed["reading"] is False and "my-skill" in _names(listed)
        view = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        assert view["commands_reading"] is False and "my-skill" in _names(view)
    await stop_all(app)


async def test_a_session_with_no_folder_lists_the_users_own_skills_before_its_first_message(app, fake_init):
    """It used to answer nothing until its first message made its folder."""
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "claude-code"})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        assert (await c.get(f"/build/app/sessions/{sid}/commands")).json()["reading"] is True
        assert await asyncio.to_thread(_wait, lambda: _known(CCAT.no_folder_dir(svc.home)))
        listed = (await c.get(f"/build/app/sessions/{sid}/commands")).json()
        assert listed["reading"] is False and _names(listed) == ["compact", "my-skill"]
    await stop_all(app)


async def test_with_the_background_read_off_nothing_is_read_and_nothing_is_said_to_be(app, work, tmp_path, monkeypatch):
    """The suite's own setting (tests/conftest.py): no read, so ``reading``
    is never left True with nothing coming."""
    monkeypatch.setattr(C, "LAST_COMMANDS", {})
    assert CCAT.AUTO_PROBE is False
    svc = S.service_for(app.state.platform)
    assert await asyncio.to_thread(svc.start_commands, "claude-code", str(work)) == {"commands": [], "reading": False}
    assert not CCAT.no_folder_dir(svc.home).exists()
