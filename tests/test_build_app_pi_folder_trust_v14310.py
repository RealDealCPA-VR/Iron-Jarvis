"""v1.431.0 — a Pi folder's OWN extensions, skills and settings in Build › Code.

Pi (1.3.0) loads a folder's own ``.pi`` resources only when the folder is
trusted. Its terminal ASKS; RPC mode (how Build › Code runs Pi) cannot, so
with no decision Pi SKIPS them silently (``docs/security.md``). Iron Jarvis
now reads whether a Pi session's folder is in that state (``hosted/pi_trust``,
read-only, Pi's own rules from ``dist/core/trust-manager.js`` +
``project-trust.js``), SAYS it in the session, and offers ONE press that
starts Pi with ``--approve`` for that session. Nothing loads without the
press; a decision the user already gave Pi (a saved "do not trust", or
``defaultProjectTrust: never``) is never second-guessed.

Every test points Pi's agent folder AND the home folder at a scratch folder:
nothing here reads the developer's real ``~/.pi``. Every Pi is the fake
harness replaying a RECORDED pi 1.3.0 session (``build_app_v1411/pi_read``).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import pi_trust as PT
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import client, frames, new_session, starts, stop_all
from tests._build_app_pi_helpers_v1411 import use_fake_pi


@pytest.fixture(autouse=True)
def scratch(monkeypatch, tmp_path):
    """Pi's agent folder and the home folder are scratch folders, and no
    answer is kept between tests."""
    agent = tmp_path / "pi-agent"
    agent.mkdir()
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(P, "HANDLED_GRACE_S", 0.3)
    # The temp folder sits under the developer's real home, whose own
    # ~/.agents/skills Pi WOULD count for a folder below it once HOME points
    # elsewhere: nothing outside this test's scratch folder is ever looked at.
    real_exists = PT._exists
    root = os.path.normcase(str(tmp_path))
    monkeypatch.setattr(PT, "_exists", lambda p: os.path.normcase(str(p)).startswith(root) and real_exists(p))
    PT.forget()
    yield {"agent": agent, "home": home}
    PT.forget()


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    return folder


def _with_extension(folder: Path) -> Path:
    ext = folder / ".pi" / "extensions"
    ext.mkdir(parents=True)
    (ext / "hello.ts").write_text("export default function (pi) {}\n", encoding="utf-8")
    return folder


def _env(scratch) -> dict[str, str]:
    return {"PI_CODING_AGENT_DIR": str(scratch["agent"]), "HOME": str(scratch["home"])}


def _state(folder: Path, scratch) -> str:
    return PT.check(str(folder), env=_env(scratch), fresh=True)["state"]


def _trust(scratch, data: dict) -> None:
    (scratch["agent"] / "trust.json").write_text(json.dumps(data), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Which folders Pi would have asked about (trust-manager.js)
# --------------------------------------------------------------------------- #
def test_a_folder_with_nothing_of_its_own_or_a_bare_pi_folder_needs_nothing(work, scratch):
    assert _state(work, scratch) == PT.NONE
    (work / ".pi").mkdir()
    (work / ".pi" / "notes.txt").write_text("x", encoding="utf-8")
    assert _state(work, scratch) == PT.NONE, "a bare .pi folder needs no decision (Pi's own rule)"


@pytest.mark.parametrize("name,words", [
    ("extensions", "extensions"), ("skills", "skills"), ("prompts", "prompt templates"),
    ("themes", "themes"), ("mcp.json", "MCP servers"), ("SYSTEM.md", "system prompt"),
    ("APPEND_SYSTEM.md", "system prompt"), ("settings.json", "settings"),
])
def test_each_of_pis_protected_resources_makes_the_folder_undecided(work, scratch, name, words):
    target = work / ".pi" / name
    if "." in name:
        target.parent.mkdir(parents=True)
        target.write_text("{}", encoding="utf-8")
    else:
        target.mkdir(parents=True)
    got = PT.check(str(work), env=_env(scratch), fresh=True)
    assert got == {"state": PT.UNDECIDED, "found": [words]}


def test_a_parents_pi_folder_does_not_count_but_a_parents_agents_skills_does(tmp_path, scratch):
    parent = tmp_path / "repo"
    child = parent / "pkg" / "app"
    child.mkdir(parents=True)
    _with_extension(parent)
    _with_extension(parent / "pkg")
    assert _state(child, scratch) == PT.NONE, "Pi reads .pi/* in the working folder ONLY"
    (parent / ".agents" / "skills").mkdir(parents=True)
    assert PT.check(str(child), env=_env(scratch), fresh=True) == {"state": PT.UNDECIDED, "found": ["skills"]}


def test_the_users_own_agents_skills_never_counts(scratch):
    home = scratch["home"]
    (home / ".agents" / "skills").mkdir(parents=True)
    inside = home / "projects" / "app"
    inside.mkdir(parents=True)
    assert _state(inside, scratch) == PT.NONE, "~/.agents/skills is the user's own, not the folder's"
    assert _state(home, scratch) == PT.NONE


# --------------------------------------------------------------------------- #
# Pi's decision order: saved decision (nearest) -> defaultProjectTrust -> ask
# --------------------------------------------------------------------------- #
def test_a_saved_decision_for_the_folder_or_a_parent_decides_nearest_first(tmp_path, work, scratch):
    _with_extension(work)
    assert _state(work, scratch) == PT.UNDECIDED
    _trust(scratch, {str(work): True})
    assert _state(work, scratch) == PT.TRUSTED
    _trust(scratch, {str(tmp_path): True})
    assert _state(work, scratch) == PT.TRUSTED, "a parent's saved decision applies"
    _trust(scratch, {str(tmp_path): True, str(work): False})
    assert _state(work, scratch) == PT.DECLINED, "the closest decision wins"
    _trust(scratch, {str(work): None})
    assert _state(work, scratch) == PT.UNDECIDED, "null is no decision"


def test_pis_keys_are_exact_strings_so_another_spelling_is_no_decision(work, scratch):
    _with_extension(work)
    other = str(work).swapcase()
    if other == str(work):
        pytest.skip("no letters to change")
    _trust(scratch, {other: True})
    assert _state(work, scratch) == PT.UNDECIDED, "Pi looks keys up exactly: a key it cannot find is no decision"


@pytest.mark.parametrize("value,state", [
    ("always", PT.TRUSTED), ("never", PT.DECLINED), ("ask", PT.UNDECIDED), ("sometimes", PT.UNDECIDED),
])
def test_default_project_trust_decides_when_nothing_is_saved(work, scratch, value, state):
    _with_extension(work)
    (scratch["agent"] / "settings.json").write_text(json.dumps({"defaultProjectTrust": value}), encoding="utf-8")
    assert _state(work, scratch) == state
    _trust(scratch, {str(work): False})
    assert _state(work, scratch) == PT.DECLINED, "a saved decision comes before the default"


def test_a_trust_list_pi_could_not_read_is_unknown_and_offers_nothing(work, scratch):
    _with_extension(work)
    (scratch["agent"] / "trust.json").write_text("{not json", encoding="utf-8")
    assert _state(work, scratch) == PT.UNKNOWN
    (scratch["agent"] / "trust.json").write_text("﻿" + json.dumps({str(work): True}), encoding="utf-8")
    assert _state(work, scratch) == PT.TRUSTED, "a BOM is fine (Pi strips it)"
    assert PT.view(str(work), False, env=_env(scratch)) is None


def test_a_network_folder_is_never_touched(monkeypatch, scratch):
    touched: list[str] = []
    real = os.lstat

    def spy(path, *a, **kw):
        touched.append(str(path))
        if str(path).replace("/", "\\").startswith("\\\\"):
            raise OSError("never opened here")  # recorded, never reached
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "lstat", spy)
    got = PT.check("\\\\evil-host\\share\\repo", env=_env(scratch), fresh=True)
    assert got["state"] == PT.UNKNOWN
    assert not [t for t in touched if t.replace("/", "\\").startswith("\\\\")], touched


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows junction")
def test_a_folder_reached_through_a_junction_is_keyed_by_its_target_like_pi(tmp_path, scratch):
    real = _with_extension(tmp_path / "real-repo")
    link = tmp_path / "linked"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("could not make a junction here")
    _trust(scratch, {str(real): True})
    assert _state(link, scratch) == PT.TRUSTED, "Pi keys trust.json by realpath (the junction's target)"


# --------------------------------------------------------------------------- #
# What the session view says
# --------------------------------------------------------------------------- #
def test_the_view_offers_only_for_an_undecided_folder(work, scratch):
    _with_extension(work)
    (work / ".pi" / "skills").mkdir()
    got = PT.view(str(work), False, env=_env(scratch))
    assert got["offer"] is True and got["loaded"] is False and got["state"] == PT.UNDECIDED
    assert got["line"] == W.PI_FOLDER_SKIPPED.format(things="extensions and skills")
    assert got["title"] == W.PI_FOLDER_TITLE
    _trust(scratch, {str(work): False})
    PT.forget()
    assert PT.view(str(work), False, env=_env(scratch)) is None, "the user's own 'do not trust' is never second-guessed"
    _trust(scratch, {str(work): True})
    PT.forget()
    assert PT.view(str(work), False, env=_env(scratch)) is None, "Pi already loads them: nothing to say"


def test_a_session_that_loads_them_says_so_and_offers_nothing_more(work, scratch):
    _with_extension(work)
    got = PT.view(str(work), True, env=_env(scratch))
    assert got["loaded"] is True and got["offer"] is False
    assert got["line"] == W.PI_FOLDER_LOADED.format(things="extensions")


def test_the_words_list_what_was_found_in_plain_words():
    assert PT.found_words(["extensions"]) == "extensions"
    assert PT.found_words(["extensions", "skills", "settings"]) == "extensions, skills and settings"
    assert PT.found_words([]) == W.PI_FOLDER_THINGS


# --------------------------------------------------------------------------- #
# The press, end to end (the fake Pi)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def _turn(c, sid: str, turn_id: str = "t1"):
    return await asyncio.wait_for(
        c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": turn_id}), 30)


async def test_without_the_press_pi_never_gets_approve_and_the_view_offers_it(app, work, tmp_path, monkeypatch):
    _with_extension(work)
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_read", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        view = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        assert view["pi_folder"]["offer"] is True
        assert view["session"]["load_folder_resources"] is False
        await _turn(c, s["id"])
    await stop_all(app)
    assert "--approve" not in starts(log)[0]["argv"], "nothing of the folder's loads without the press"


async def test_the_press_starts_pi_with_approve_for_this_session_only(app, work, tmp_path, monkeypatch, scratch):
    _with_extension(work)
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_read", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"load_folder_resources": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session"]["load_folder_resources"] is True
        assert body["pi_folder"]["loaded"] is True and body["pi_folder"]["offer"] is False
        await _turn(c, s["id"])
        other = await new_session(c, work, harness="pi")
        assert (await c.get(f"/build/app/sessions/{other['id']}")).json()["pi_folder"]["offer"] is True, \
            "another session of the same folder is not trusted by this press"
    await stop_all(app)
    assert "--approve" in starts(log)[0]["argv"]
    assert not (scratch["agent"] / "trust.json").exists(), "nothing is written to Pi's own trust list"


async def test_pressing_while_pi_runs_restarts_it_on_the_same_conversation(app, work, tmp_path, monkeypatch):
    _with_extension(work)
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_read", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        await _turn(c, s["id"])
        assert svc.live[s["id"]].running
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"load_folder_resources": True})
        assert r.status_code == 200, r.text
        assert svc.live[s["id"]].restart is True, "Pi reads a folder's resources at start only"
        view = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        notes = [e["text"] for e in view["events"] if e["type"] == "notice"]
        assert W.PI_FOLDER_LOAD_NOW.format(things="extensions") in notes
        evs = frames((await _turn(c, s["id"], turn_id="t2")).text)
        assert evs[-1]["type"] == "done"
        # Turned off again: the next start goes back to Pi's own rule.
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"load_folder_resources": False})
        assert r.status_code == 200 and r.json()["session"]["load_folder_resources"] is False
        await _turn(c, s["id"], turn_id="t3")
    await stop_all(app)
    first, second, third = starts(log)[:3]
    assert "--approve" not in first["argv"] and "--approve" in second["argv"] and "--approve" not in third["argv"]
    assert first["argv"][3] == second["argv"][3] == third["argv"][3], "the SAME saved Pi conversation"


@pytest.mark.parametrize("setup,detail", [
    ("nothing", W.PI_FOLDER_NOTHING),
    ("declined", W.PI_FOLDER_DECLINED),
    ("trusted", W.PI_FOLDER_ALREADY),
    ("unreadable", W.PI_FOLDER_CANNOT_TELL),
])
async def test_the_press_is_refused_where_there_is_nothing_to_ask(app, work, scratch, setup, detail):
    if setup != "nothing":
        _with_extension(work)
    if setup == "declined":
        _trust(scratch, {str(work): False})
    if setup == "trusted":
        _trust(scratch, {str(work): True})
    if setup == "unreadable":
        (scratch["agent"] / "trust.json").write_text("[", encoding="utf-8")
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"load_folder_resources": True})
        assert r.status_code == 409 and r.json()["detail"] == detail
        assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["load_folder_resources"] is False
    await stop_all(app)


async def test_only_a_pi_session_takes_the_press(app, work):
    _with_extension(work)
    async with client(app) as c:
        s = await new_session(c, work, harness="claude-code")
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"load_folder_resources": True})
        assert r.status_code == 409 and r.json()["detail"] == W.PI_FOLDER_NOT_PI
        assert "pi_folder" not in (await c.get(f"/build/app/sessions/{s['id']}")).json()
    await stop_all(app)


# --------------------------------------------------------------------------- #
# THE CONTRACT with the dashboard
# --------------------------------------------------------------------------- #
#: What the real daemon sends as ``pi_folder``: the dashboard decodes this very
#: file (``dashboard/__tests__/build-app-pi-folder-v1431.test.tsx``).
#: Regenerate after an intended change with ``IJ_REGEN_BUILD_APP_PI_FOLDER=1``.
GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-pi-folder-v1431.json"


async def test_the_golden_file_is_what_the_real_daemon_sends(app, work, scratch):
    _with_extension(work)
    (work / ".pi" / "skills").mkdir()
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        offered = (await c.get(f"/build/app/sessions/{s['id']}")).json()["pi_folder"]
        loaded = (await c.patch(f"/build/app/sessions/{s['id']}", json={"load_folder_resources": True})).json()
        back = (await c.patch(f"/build/app/sessions/{s['id']}", json={"load_folder_resources": False})).json()
    await stop_all(app)
    doc = {
        "_about": (
            "v1.431.0 Build Code view, Pi: the session view's `pi_folder` as the REAL daemon sends it for a "
            "folder holding .pi/extensions and .pi/skills, then the answers to the press and to turning it off. "
            "Produced by tests/test_build_app_pi_folder_trust_v14310.py; regenerate with "
            "IJ_REGEN_BUILD_APP_PI_FOLDER=1."
        ),
        "offered": offered,
        "loaded": {"pi_folder": loaded["pi_folder"], "applied": loaded["applied"]},
        "back": {"pi_folder": back["pi_folder"], "applied": back["applied"]},
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("IJ_REGEN_BUILD_APP_PI_FOLDER") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(text.encode("utf-8"))
    on_disk = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert {k: on_disk[k] for k in ("offered", "loaded", "back")} == {k: doc[k] for k in ("offered", "loaded", "back")}, \
        "the golden file is stale — IJ_REGEN_BUILD_APP_PI_FOLDER=1 after an intended change"
