"""v1.412.0 review — Build › Code's no-folder sessions, the effort chip and the
start composer's refusals.

Each test names the defect the independent review found:

* archive → unarchive of a no-folder session resumed in a DIFFERENT folder
  (archive dropped the empty own folder; Claude Code and Pi keep a
  conversation by the folder it ran in, so ``--resume`` lost it);
* a no-folder session whose own folder the user deleted failed with Windows'
  "The directory name is invalid", blaming the tool — it is made AGAIN at the
  same path (the v1.379.0 ``_make_again`` rule), and a folder whose parent is
  gone is refused in words;
* a first message that failed inside the turn (the tool not installed) kept
  its empty own folder;
* the effort chip never hid: the levels are now the MODEL's when known;
* every 400 landed on the folder chip: a refusal says which choice it is
  about (``field``);
* the guards of ``_drop_own_folder`` / ``_take_back_folder`` had no pin.

Every turn replays a RECORDED harness session through the fake harness
(``tests/_build_app_helpers_v1409``): real routes, store, process plumbing.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.providers import claude_models as cm
from tests._build_app_helpers_v1409 import client, frames, starts, stop_all, until, use_fake


@pytest.fixture()
def files_root(tmp_path) -> Path:
    return tmp_path / "chat files"


@pytest.fixture()
def app(tmp_path, files_root):
    a = create_app(str(tmp_path / "home"))
    # Never the user's Documents: the chat files folder is a scratch one.
    a.state.platform.config.chat_files_root = str(files_root)
    return a


def _made(files_root: Path) -> list[Path]:
    return sorted(p for p in files_root.iterdir() if p.is_dir()) if files_root.exists() else []


async def _create(c, harness: str = "claude-code", **extra) -> dict:
    r = await c.post("/build/app/sessions", json={"harness": harness, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _turn(c, sid: str, text: str, turn_id: str):
    return await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id})


async def _idle(app, sid: str) -> None:
    """The session's process goes (the idle reaper), so the next message
    starts a NEW one — the place a wrong folder would show."""
    svc = S.service_for(app.state.platform)
    await until(lambda: store.get(app.state.platform.engine, sid).harness_session_id)
    await svc.reap_once(now=svc.clock() + S.IDLE_S + 1)


# --------------------------------------------------------------------------- #
# 2. archive keeps the own folder; unarchive resumes in the SAME folder
# --------------------------------------------------------------------------- #


async def test_archive_then_unarchive_resumes_in_the_same_folder(app, files_root, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await _create(c)
        r = await _turn(c, s["id"], "fix the login page", "t1")
        assert r.status_code == 200, r.text
        made = _made(files_root)
        assert len(made) == 1
        await _idle(app, s["id"])
        assert (await c.post(f"/build/app/sessions/{s['id']}/archive")).status_code == 200
        # The folder is EMPTY (the fake writes nothing there) — and still kept.
        assert made[0].is_dir(), "archive removed the session's own folder"
        assert store.get(app.state.platform.engine, s["id"]).folder == str(made[0])
        assert (await c.post(f"/build/app/sessions/{s['id']}/unarchive")).status_code == 200
        r = await _turn(c, s["id"], "and the footer", "t2")
        assert r.status_code == 200, r.text
        # The conversation resumed in the folder it ran in, not a new one.
        cwds = [x["cwd"] for x in starts(log)]
        assert cwds == [str(made[0])] * 2, cwds
        assert _made(files_root) == made
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 3. a deleted own folder is made again at the same path
# --------------------------------------------------------------------------- #


async def test_a_removed_own_folder_is_made_again_at_the_same_path(app, files_root, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await _create(c)
        assert (await _turn(c, s["id"], "fix the login page", "t1")).status_code == 200
        made = _made(files_root)[0]
        await _idle(app, s["id"])
        shutil.rmtree(made)  # the user deleted it between messages
        r = await _turn(c, s["id"], "and the footer", "t2")
        assert r.status_code == 200, r.text
        assert frames(r.text)[-1]["type"] == "done"
        assert "directory name is invalid" not in r.text.lower()
        assert made.is_dir(), "the session's own folder was not made again"
        assert [x["cwd"] for x in starts(log)] == [str(made)] * 2
        assert store.get(app.state.platform.engine, s["id"]).folder == str(made)
    await stop_all(app)


async def test_an_own_folder_whose_parent_is_gone_is_refused_in_words(app, files_root, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    async with client(app) as c:
        s = await _create(c)
        gone = tmp_path / "unplugged drive" / "2026-10-30 fix it"
        store.update(app.state.platform.engine, s["id"], folder=str(gone))
        r = await _turn(c, s["id"], "and the footer", "t2")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert str(gone) in detail and "missing" in detail
        assert not gone.exists() and not gone.parent.exists(), "something was made where the folder above is gone"
    await stop_all(app)


async def test_a_chosen_folder_that_went_missing_says_so_not_the_tool(app, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    work = tmp_path / "work"
    work.mkdir()
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work)})
        s = r.json()
        work.rmdir()
        r = await _turn(c, s["id"], "hello", "t1")
        errors = [f for f in frames(r.text) if f.get("type") == "error"]
        assert errors, r.text
        said = errors[0]["title"] + " " + errors[0]["fix"]
        assert "missing" in said and str(work) in said, said
        assert "could not start" not in said and "directory name is invalid" not in said.lower()
        # A folder the user CHOSE is never made by Iron Jarvis.
        assert not work.exists()
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 9b. a first message whose tool never started takes the empty folder back
# --------------------------------------------------------------------------- #


async def test_a_first_message_whose_tool_is_not_installed_takes_its_folder_back(app, files_root, monkeypatch):
    monkeypatch.setattr(S, "binary_for", lambda harness: None)
    async with client(app) as c:
        s = await _create(c)
        r = await _turn(c, s["id"], "fix the login page", "t1")
        assert r.status_code == 200, r.text
        assert any(f.get("type") == "error" for f in frames(r.text))

        async def back():
            return _made(files_root) == [] and store.get(app.state.platform.engine, s["id"]).folder == ""

        await until(back, timeout=10)
        row = store.get(app.state.platform.engine, s["id"])
        assert row.no_folder is True and row.folder == ""
    await stop_all(app)


async def test_a_tool_that_ran_keeps_its_folder_even_when_the_turn_failed(app, files_root, tmp_path, monkeypatch):
    """The other side: once the tool started, the conversation belongs to the
    folder — a failed turn never takes it back."""
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await _create(c)
        assert (await _turn(c, s["id"], "fix the login page", "t1")).status_code == 200
        made = _made(files_root)
        assert len(made) == 1
        # The process goes first (Windows will not remove a running
        # program's working folder).
        await _idle(app, s["id"])
        await asyncio.to_thread(svc._take_back_folder, s["id"], str(made[0]), forget=False)
        # forget=False removes an EMPTY remade folder but never the path kept.
        assert not made[0].exists()
        assert store.get(app.state.platform.engine, s["id"]).folder == str(made[0])
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 11. the guards of _drop_own_folder and _take_back_folder
# --------------------------------------------------------------------------- #


async def test_delete_keeps_an_own_folder_another_session_works_in(app, files_root):
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await _create(c)
        _, fa = await asyncio.to_thread(svc._own_folder, a["id"], "shared one")
        # Another session chose that very folder (it is an ordinary folder).
        b = await _create(c, folder=fa)
        assert b["folder"] == fa
        assert (await c.delete(f"/build/app/sessions/{a['id']}")).status_code == 200
        assert Path(fa).is_dir(), "a folder another session works in was removed"
    await stop_all(app)


async def test_delete_never_removes_a_folder_outside_the_chat_files_folder(app, files_root, tmp_path):
    async with client(app) as c:
        a = await _create(c)
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        store.update(app.state.platform.engine, a["id"], folder=str(outside))
        assert (await c.delete(f"/build/app/sessions/{a['id']}")).status_code == 200
        assert outside.is_dir(), "an empty folder outside the chat files folder was removed"
    await stop_all(app)


async def test_delete_drops_an_empty_own_folder_nobody_else_uses(app, files_root):
    """The control for the two guards above: they are what kept the folder."""
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await _create(c)
        _, fa = await asyncio.to_thread(svc._own_folder, a["id"], "alone")
        assert (await c.delete(f"/build/app/sessions/{a['id']}")).status_code == 200
        assert not Path(fa).exists()
    await stop_all(app)


async def test_take_back_leaves_a_folder_a_running_turn_works_in(app, files_root):
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await _create(c)
    row, made = await asyncio.to_thread(svc._own_folder, s["id"], "busy")
    live = svc._live(row)
    turn = S.HostedTurn("t-busy", s["id"])
    live.turn = turn
    assert turn.running
    await asyncio.to_thread(svc._take_back_folder, s["id"], made)
    assert Path(made).is_dir(), "a folder a running turn works in was taken back"
    assert store.get(app.state.platform.engine, s["id"]).folder == made
    live.turn = None
    await asyncio.to_thread(svc._take_back_folder, s["id"], made)
    assert not Path(made).exists()  # the control: with no turn it goes


# --------------------------------------------------------------------------- #
# 7. the effort levels are the MODEL's when known
# --------------------------------------------------------------------------- #

ALL_FIVE = ["low", "medium", "high", "xhigh", "max"]
PICKER = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Opus",
     "description": "Opus 5.5", "supportsEffort": True, "supportedEffortLevels": ALL_FIVE},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku",
     "description": "Haiku 4.5", "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high"]},
    {"value": "claude-fable-5-1", "resolvedModel": "claude-fable-5-1", "displayName": "Fable",
     "description": "Fable 5.1", "supportsEffort": False, "supportedEffortLevels": ALL_FIVE},
]


@pytest.fixture()
def picker(monkeypatch):
    cm.reset()
    cm.set_home(None)
    out = json.dumps({
        "type": "control_response",
        "response": {"subtype": "success", "request_id": "ij-picker",
                     "response": {"models": PICKER, "account": {"email": "user@example.test", "subscriptionType": "Claude Max"}, "commands": []}},
    }) + "\n"
    monkeypatch.setattr(cm, "_run_handshake", lambda timeout: out)
    assert cm.discover(force=True)["error"] is None

    def boom(timeout):
        raise AssertionError("the claude CLI was started")

    monkeypatch.setattr(cm, "_run_handshake", boom)
    yield
    cm.reset()
    cm.set_home(None)


def test_model_efforts_tells_none_from_unknown(picker):
    assert cm.model_efforts("claude-fable-5-1") == ()
    assert cm.model_efforts("claude-haiku-4-5-20251001") == ("low", "medium", "high")
    assert cm.model_efforts("") == tuple(ALL_FIVE)  # the CLI's default row
    assert cm.model_efforts("claude-unknown-9") is None


async def test_the_claude_model_rows_carry_their_own_effort_levels(app, picker):
    async with client(app) as c:
        tools = (await c.get("/build/app/harnesses")).json()["harnesses"]
    claude = next(t for t in tools if t["id"] == "claude-code")
    rows = {m["id"]: m for m in claude["models"]}
    assert rows["claude-fable-5-1"]["efforts"] == []
    assert rows["claude-haiku-4-5-20251001"]["efforts"] == ["low", "medium", "high"]
    assert rows[""]["efforts"] == ALL_FIVE
    # Codex knows nothing per model: its rows carry no key (the tool's list).
    codex = next(t for t in tools if t["id"] == "codex")
    assert all("efforts" not in m for m in codex["models"])
    assert codex["efforts"] == list(S.EFFORTS["codex"])


async def test_an_effort_the_model_does_not_take_is_refused_on_the_effort_chip(app, picker, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={
            "harness": "claude-code", "folder": str(work), "model": "claude-fable-5-1", "effort": "high"})
        assert r.status_code == 400 and r.json()["field"] == "effort", r.text
        r = await c.post("/build/app/sessions", json={
            "harness": "claude-code", "folder": str(work), "model": "claude-haiku-4-5-20251001", "effort": "max"})
        assert r.status_code == 400 and r.json()["field"] == "effort"
        ok = await _create(c, folder=str(work), model="claude-haiku-4-5-20251001", effort="high")
        assert ok["effort"] == "high"
        # Moving to a model that takes no effort drops the kept one.
        r = await c.patch(f"/build/app/sessions/{ok['id']}", json={"model": "claude-fable-5-1"})
        assert r.status_code == 200, r.text
        assert r.json()["session"]["effort"] == ""
    await stop_all(app)


def test_a_pi_model_with_no_reasoning_takes_no_effort(tmp_path):
    rows = P.model_rows([
        {"provider": "ollama", "id": "llama3", "name": "Llama 3", "reasoning": False},
        {"provider": "anthropic", "id": "claude-x", "name": "Claude X", "reasoning": True},
        {"provider": "openai", "id": "gpt-x", "name": "GPT X"},
    ])
    by = {r["id"]: r for r in rows}
    assert by["ollama/llama3"]["efforts"] == []
    assert "efforts" not in by["anthropic/claude-x"] and "efforts" not in by["openai/gpt-x"]
    # Kept on disk and read back the same.
    path = tmp_path / "pi-models.json"
    P.save_models(path, rows)
    saved = P.LAST_MODELS[:]
    P.LAST_MODELS.clear()
    try:
        again = {r["id"]: r for r in P.cached_models(path)}
    finally:
        P.LAST_MODELS[:] = saved
    assert again["ollama/llama3"]["efforts"] == []
    assert S.efforts_for("pi", "ollama/llama3", rows) == ()
    assert S.efforts_for("pi", "anthropic/claude-x", rows) == S.EFFORTS["pi"]


# --------------------------------------------------------------------------- #
# 9a. a refusal says which choice it is about
# --------------------------------------------------------------------------- #


async def test_each_create_refusal_names_its_chip(app, tmp_path):
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": "relative/path"})
        assert r.status_code == 400 and r.json()["field"] == "folder", r.text
        r = await c.post("/build/app/sessions", json={"harness": "nope"})
        assert r.status_code == 400 and r.json()["field"] == "tool"
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "mode": "full"})
        assert r.status_code == 400 and r.json()["field"] == "mode"
        r = await c.post("/build/app/sessions", json={"harness": "codex", "effort": "turbo"})
        assert r.status_code == 400 and r.json()["field"] == "effort"
        r = await c.post("/build/app/sessions", json={"harness": "pi", "account": "p_work"})
        assert r.status_code == 400 and r.json()["field"] == "account"
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "model": "bad model!"})
        assert r.status_code == 400 and r.json()["field"] == "model"
        # `detail` stays the plain sentence (every older reader).
        assert isinstance(r.json()["detail"], str)
    await stop_all(app)


def test_the_words_exist():
    from iron_jarvis.hosted import words as W

    assert "{folder}" in W.NO_FOLDER_GONE and "{folder}" in W.FOLDER_MISSING
