"""v1.439.0: Build › Code parity fixes on the daemon side.

1. Grok's ``/always-approve`` and ``/auto`` are never offered or sent (they
   would change what Grok runs without asking, outside Iron Jarvis's cards).
2. An ACP command keeps its argument hint (OpenCode's ``/review`` "commit").
3. OpenCode's kept ``/`` list is its FOLDER's; its model rows are grouped by
   provider.
4. Codex's own warnings, deprecations and a hook that did not go well are one
   masked notice each (a hook that ran and said nothing: none).
5. A rename reaches a RUNNING Claude Code (``rename_session``) or Codex
   (``thread/name/set``), in the background.
6. ``/model`` and ``/effort`` typed for Claude Code or Grok set the chips; a
   Claude Code built-in that needs its terminal is a note row and is answered
   in words, never sent to the model.
7. "Grok isn't signed in" carries the Sign in press (``grok-cli``).

Every end-to-end case replays RECORDED harness output through
``tests/fixtures/fake_harness_v1409.py`` (the real process plumbing, driver,
service and routes); nothing here starts a real CLI or calls a model.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as CL
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import grok as G
from iron_jarvis.hosted import opencode as O
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import typed as T
from iron_jarvis.hosted.events import EventLog
from tests._build_app_helpers_v1409 import client, fake_log, frames, new_session, sent, starts, stop_all, until, use_fake

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
SEAM = HERE.parent / "dashboard" / "__tests__" / "fixtures" / "build-app-parity-v1439.json"


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
    """Every test starts with no `/` list and no model list kept."""
    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    monkeypatch.setattr(O, "LAST_MODELS", [])
    monkeypatch.setattr(O, "LAST_DEFAULT", [])


def _recorded(path: Path, kind: str) -> dict:
    """The first recorded ``session/update`` of ``kind`` in a scenario."""
    for step in json.loads(path.read_text(encoding="utf-8"))["steps"]:
        update = (((step.get("out") or {}).get("params") or {}).get("update") or {})
        if update.get("sessionUpdate") == kind:
            return update
    raise AssertionError(f"no {kind} in {path.name}")


def _events(app, sid: str) -> list[dict]:
    svc = S.service_for(app.state.platform)
    live = svc.live.get(sid)
    return (live.log if live is not None else EventLog(svc.home, sid)).read(5000)[0]


def _notices(app, sid: str) -> list[str]:
    return [e["text"] for e in _events(app, sid) if e.get("type") == "notice"]


async def _say(c, sid: str, text: str, turn_id: str):
    return await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id})


# --------------------------------------------------------------------------- #
# 1. Grok's approval commands
# --------------------------------------------------------------------------- #
def test_groks_menu_drops_its_approval_commands_and_offers_model_and_effort(app):
    drv = G.GrokDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    drv._on_update(_recorded(FIX / "build_app_v1416" / "g_read.json", "available_commands_update"))
    assert "always-approve" in [c["name"] for c in drv.commands], "the recording advertises it"
    names = [c["name"] for c in S.service_for(app.state.platform).commands_for(G.HARNESS)]
    assert "always-approve" not in names and "auto" not in names
    assert names[:2] == ["model", "effort"] and "compact" in names


async def test_a_typed_grok_approval_command_is_refused_before_anything_starts(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)  # any script: nothing may start
    async with client(app) as c:
        s = await new_session(c, work, harness="grok")
        for text in ("/always-approve on", "/AUTO", "/auto on please"):
            r = await _say(c, s["id"], text, f"t-{text}")
            assert r.status_code == 400 and "mode chip" in r.json()["detail"], text
    assert not starts(log) and not sent(log)
    await stop_all(app)


def test_an_approval_command_typed_as_a_note_is_refused_in_the_same_words():
    assert T.typed_builtin("grok", "/always-approve on", []) == ("always-approve", "on")
    assert T.refusal("grok", "auto") == T.GROK_APPROVAL_REFUSAL
    # Claude Code's own /auto is not Grok's.
    assert T.typed_builtin("claude-code", "/auto", []) is None


# --------------------------------------------------------------------------- #
# 2 + 3. ACP hints, OpenCode per folder, OpenCode groups
# --------------------------------------------------------------------------- #
def _oc(cwd: Path) -> O.OpenCodeDriver:
    return O.OpenCodeDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                            mode="ask", cwd=str(cwd))


def test_an_acp_command_keeps_its_argument_hint(app, work):
    drv = _oc(work)
    drv._on_update(_recorded(FIX / "build_app_v1413" / "oc_read.json", "available_commands_update"))
    review = next(c for c in drv.commands if c["name"] == "review")
    assert review["argument_hint"] == "commit"
    assert "argument_hint" not in next(c for c in drv.commands if c["name"] == "init")
    rows = S.service_for(app.state.platform).commands_for(O.HARNESS, folder=str(work))
    assert next(c for c in rows if c["name"] == "review")["argument_hint"] == "commit"
    long = {"sessionUpdate": "available_commands_update",
            "availableCommands": [{"name": "x", "description": "d", "input": {"hint": "h" * 200}}]}
    drv._on_update(long)
    assert len(drv.commands[0]["argument_hint"]) == 80


def test_opencodes_commands_are_kept_per_folder(app, tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    svc = S.service_for(app.state.platform)

    def said(folder: Path, names: list[str]) -> None:
        _oc(folder)._on_update({"sessionUpdate": "available_commands_update",
                                "availableCommands": [{"name": n, "description": n} for n in names]})

    said(a, ["init", "review", "deploy-a"])
    # One folder known: nothing of it shows anywhere else.
    assert [r["name"] for r in svc.commands_for(O.HARNESS, folder=str(a))] == ["init", "review", "deploy-a"]
    assert svc.commands_for(O.HARNESS, folder=str(b)) == []
    assert svc.commands_for(O.HARNESS) == []
    said(b, ["init", "review", "lint-b"])
    # The folder's own list; another folder (or none) sees only what every
    # folder had.
    assert [r["name"] for r in svc.commands_for(O.HARNESS, folder=str(b))] == ["init", "review", "lint-b"]
    assert [r["name"] for r in svc.commands_for(O.HARNESS, folder=str(c))] == ["init", "review"]
    listed = next(h for h in svc.harnesses() if h["id"] == O.HARNESS)["commands"]
    assert [r["name"] for r in listed] == ["init", "review"]
    # The same folder in other capitals and separators is the same folder.
    assert "deploy-a" in [r["name"] for r in svc.commands_for(O.HARNESS, folder=str(a).upper() + os.sep)]
    # Other tools keep one list.
    assert CL.commands_key("grok", str(a)) == "grok"


def test_opencodes_model_rows_are_grouped_by_provider_in_a_stable_order(tmp_path):
    options = [{"id": "model", "currentValue": "zeta/one", "options": [
        {"value": "zeta/one", "name": "Zeta/One"}, {"value": "anthropic/b", "name": "Anthropic/B"},
        {"value": "zeta/two", "name": "Zeta/Two"}, {"value": "anthropic/a", "name": "Anthropic/A"},
        {"value": "plain", "name": "Plain"}]}]
    rows = O.model_rows(options)
    assert [(r["id"], r.get("group")) for r in rows] == [
        ("plain", None), ("anthropic/b", "anthropic"), ("anthropic/a", "anthropic"),
        ("zeta/one", "zeta"), ("zeta/two", "zeta")]
    # A list kept by an older Iron Jarvis (no groups) is read with them.
    path = tmp_path / "opencode_models.json"
    path.write_text(json.dumps({"models": [{"id": "zeta/one", "label": "Zeta/One"},
                                           {"id": "lab/fleet", "label": "Lab/Fleet"}]}), encoding="utf-8")
    got, _ = O.cached_models(path)
    assert [(r["id"], r["group"]) for r in got] == [("lab/fleet", "lab"), ("zeta/one", "zeta")]


# --------------------------------------------------------------------------- #
# 4. Codex's own warnings and hooks
# --------------------------------------------------------------------------- #
async def test_codexs_warnings_and_a_failed_hook_are_one_masked_notice_each(app, work, tmp_path, monkeypatch):
    from tests import _build_app_helpers_v1409 as H

    monkeypatch.setattr(H, "FIX", FIX / "build_app_v1439")
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_notices", log)
    async with client(app) as c:
        row = store.create(app.state.platform.engine, harness="codex", folder=str(work),
                           harness_session_id="01a189c4-9316-7d30-b03e-8a6c95d7bacd")
        evs = frames((await _say(c, row.id, "ready?", "t1")).text)
        assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
        notices = _notices(app, row.id)
    said = [n for n in notices if n.startswith("Codex")]
    assert said == [
        "Codex found a problem in its settings (C:\\Users\\me\\.codex\\config.toml): Unknown key "
        "`model_reasoning` in config.toml. Did you mean `model_reasoning_effort`? api_key=***.",
        "Codex warns: Heads up: Long threads and multiple compactions can cause the model to be less accurate.",
        "Codex says something is going away: The `experimental_instructions_file` setting is deprecated. "
        "Use `model_instructions_file` instead.",
        "Codex's postToolUse hook failed: exit code 2. lint.sh exited 2.",
    ], notices
    assert not any("sk-abcdef" in n for n in notices), "a key must be masked"
    assert not any("excludeTurns" in n or "hydration" in n for n in notices), "Iron Jarvis's own call: logged"
    assert not any("sub-agent" in n for n in notices), "another thread's warning"
    await stop_all(app)


def test_a_hook_that_ran_quietly_says_nothing_and_one_that_warned_says_so():
    def hook(status, entries, msg=None, thread="t"):
        return {"threadId": thread, "run": {"status": status, "entries": entries, "eventName": "stop",
                                            "statusMessage": msg}}

    assert X.codex_notice("hook/completed", hook("completed", []), "t") == ""
    assert X.codex_notice("hook/completed", hook("completed", [{"kind": "feedback", "text": "ok"}]), "t") == ""
    assert X.codex_notice("hook/completed", hook("completed", [{"kind": "warning", "text": "slow"}]), "t") == \
        "Codex's stop hook ran: slow."
    assert X.codex_notice("hook/completed", hook("blocked", [{"kind": "stop", "text": "no"}]), "t") == \
        "Codex's stop hook blocked the step: no."
    assert X.codex_notice("hook/completed", hook("failed", [], thread="other"), "t") == ""
    assert X.codex_notice("hook/completed", hook("running", []), "t") == ""
    assert X.codex_notice("warning", {"message": "  "}, "t") == ""
    assert X.codex_notice("configWarning", {}, "t") == ""


# --------------------------------------------------------------------------- #
# 5. A rename reaches the running tool
# --------------------------------------------------------------------------- #
async def test_a_rename_reaches_a_running_claude_code_and_never_a_stopped_one(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"], "ready?", "t1")
        hid = svc.live[s["id"]].driver.session_id
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"title": "Fix the cart"})
        assert r.status_code == 200 and r.json()["session"]["title"] == "Fix the cart"

        def renames():
            return [m["request"] for m in sent(log) if m.get("type") == "control_request"
                    and (m.get("request") or {}).get("subtype") == "rename_session"]

        got = await until(renames)
        assert got == [{"subtype": "rename_session", "title": "Fix the cart", "source": "host", "session_id": hid}]
        # The same name again, or an empty one: nothing more is sent.
        await c.patch(f"/build/app/sessions/{s['id']}", json={"title": "Fix the cart"})
        await c.patch(f"/build/app/sessions/{s['id']}", json={"title": "   "})
        await c.post(f"/build/app/sessions/{s['id']}/archive")
        await c.post(f"/build/app/sessions/{s['id']}/unarchive")
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"title": "Stopped now"})
        assert r.status_code == 200 and r.json()["session"]["title"] == "Stopped now"
    assert len(renames()) == 1
    await stop_all(app)


async def test_a_rename_reaches_a_running_codex_as_thread_name_set(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_resume", log)
    async with client(app) as c:
        hid = "01a189c4-9316-7d30-b03e-8a6c95d7bacd"
        row = store.create(app.state.platform.engine, harness="codex", folder=str(work), harness_session_id=hid)
        await _say(c, row.id, "ready?", "t1")
        r = await c.patch(f"/build/app/sessions/{row.id}", json={"title": "Cart work"})
        assert r.status_code == 200

        def named():
            return [m["params"] for m in sent(log) if m.get("method") == "thread/name/set"]

        assert await until(named) == [{"threadId": hid, "name": "Cart work"}]
    await stop_all(app)


async def test_a_rename_the_tool_never_answers_never_delays_or_fails_it(app, work):
    svc = S.service_for(app.state.platform)
    gate = []

    class Slow:
        async def rename(self, title):
            gate.append(title)
            raise RuntimeError("the tool refused")

    async with client(app) as c:
        s = await new_session(c, work)
        live = svc._live(store.get(app.state.platform.engine, s["id"]))
        live.proc = SimpleNamespace(alive=True)  # "running", with a driver that refuses
        live.driver = Slow()
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"title": "Any name"})
        assert r.status_code == 200 and r.json()["session"]["title"] == "Any name"
        await until(lambda: gate)
        assert gate == ["Any name"]
        live.driver = None
        live.proc = None
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 6. Typed built-ins for Claude Code and Grok
# --------------------------------------------------------------------------- #
@pytest.fixture()
def claude_catalog(monkeypatch):
    from iron_jarvis.providers import claude_models

    monkeypatch.setattr(claude_models, "catalog", lambda refresh=False: {"models": [
        {"id": "haiku", "label": "Haiku 5.5"}, {"id": "opus[1m]", "label": "Opus 5.5 (1M)"}]})
    monkeypatch.setattr(claude_models, "model_efforts", lambda model: None)


async def test_model_and_effort_typed_for_claude_code_set_the_chips(app, work, tmp_path, monkeypatch, claude_catalog):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"], "ready?", "t1")
        # (the recording answers set_permission_mode first, then set_model)
        await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "edits"})
        users_before = len([m for m in sent(log) if m.get("type") == "user"])
        evs = frames((await _say(c, s["id"], "/model Haiku 5.5", "t2")).text)
        assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
        assert {"subtype": "set_model", "model": "haiku"} in [
            m["request"] for m in sent(log) if m.get("type") == "control_request"]
        evs = frames((await _say(c, s["id"], "/effort HIGH", "t3")).text)
        assert evs[-1]["stop"] == "completed"
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert row["model"] == "haiku" and row["effort"] == "high"
        assert row["title"] != "/model Haiku 5.5", "a typed setting never names the session"
        # No user message reached Claude Code for either.
        assert len([m for m in sent(log) if m.get("type") == "user"]) == users_before
        notices = _notices(app, s["id"])
        assert "Claude Code now uses Haiku 5.5." in notices
        assert "Claude Code's effort level is now high from your next message." in notices
        # Unknown names and levels are refused in words, before anything.
        r = await _say(c, s["id"], "/model sonnet-9", "t4")
        assert r.status_code == 400 and "does not list a model called sonnet-9" in r.json()["detail"]
        r = await _say(c, s["id"], "/effort turbo", "t5")
        assert r.status_code == 400 and '"turbo" is not an effort Claude Code takes' in r.json()["detail"]
        r = await _say(c, s["id"], "/model", "t6")
        assert r.status_code == 400 and r.json()["detail"] == T.MODEL_NEEDS_NAME
        evs = frames((await _say(c, s["id"], "/effort auto", "t7")).text)
        assert evs[-1]["stop"] == "completed"
        assert (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["effort"] == ""
    await stop_all(app)


async def test_a_terminal_only_claude_built_in_is_answered_in_words_never_sent(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"], "ready?", "t1")
        users = len([m for m in sent(log) if m.get("type") == "user"])
        r = await _say(c, s["id"], "/login", "t2")
        assert r.status_code == 400 and "Sign in beside Claude Code" in r.json()["detail"]
        r = await _say(c, s["id"], "/permissions", "t3")
        assert r.status_code == 400 and "mode chip" in r.json()["detail"]
        assert len([m for m in sent(log) if m.get("type") == "user"]) == users
        rows = (await c.get(f"/build/app/sessions/{s['id']}/commands")).json()["commands"]
        login = next(r for r in rows if r["name"] == "login")
        assert login["note"] == T.CLAUDE_TERMINAL_ONLY["login"]
        # Claude Code's own list is never shadowed by a note row.
        own = {r["name"] for r in rows if "note" not in r}
        assert own and not (own & {r["name"] for r in rows if "note" in r})
    await stop_all(app)


async def test_a_typed_built_in_is_never_a_note_while_claude_code_works(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        task = asyncio.ensure_future(_say(c, s["id"], "x", "t1"))
        await until(lambda: svc.live.get(s["id"]) and svc.live[s["id"]].asks)
        for text in ("/model haiku",):
            r = await c.post(f"/build/app/sessions/{s['id']}/steer", json={"text": text})
            assert r.status_code == 400, text
            assert r.json()["detail"] == T.NOT_A_NOTE.format(label="Claude Code")
        ask = next(iter(svc.live[s["id"]].asks))
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": ask, "decision": "deny"})
        await asyncio.wait_for(task, 30)
    await stop_all(app)


async def test_a_grok_approval_command_as_a_note_is_refused_in_the_mode_chip_words(app, work):
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="grok")
        live = svc._live(store.get(app.state.platform.engine, s["id"]))
        live.turn = S.HostedTurn("t1", s["id"])  # a reply "running" (nothing started)
        r = await c.post(f"/build/app/sessions/{s['id']}/steer", json={"text": "/always-approve on"})
        assert r.status_code == 400 and r.json()["detail"] == T.GROK_APPROVAL_REFUSAL
        r = await c.post(f"/build/app/sessions/{s['id']}/steer", json={"text": "/model grok-4.7"})
        assert r.status_code == 400 and r.json()["detail"] == T.NOT_A_NOTE.format(label="Grok")
        live.turn = None
    await stop_all(app)


def test_a_name_in_claude_codes_own_list_is_claude_codes():
    """MEASURED on claude 2.1.316: config and doctor ARE in its own list, so
    they are never in the table; a later Claude Code that lists a name the
    table holds runs it itself."""
    assert "config" not in T.CLAUDE_TERMINAL_ONLY and "doctor" not in T.CLAUDE_TERMINAL_ONLY
    assert T.typed_builtin("claude-code", "/status", []) == ("status", "")
    assert T.typed_builtin("claude-code", "/status", [{"name": "status", "description": "x"}]) is None
    # A note row (ours) is not Claude Code's own.
    assert T.typed_builtin("claude-code", "/status", [{"name": "status", "note": "n"}]) == ("status", "")
    rows = T.menu_rows("claude-code", [{"name": "status", "description": "Claude Code's own"}])
    assert [r for r in rows if r["name"] == "status"] == [{"name": "status", "description": "Claude Code's own"}]
    # Nothing is said before Claude Code has said its own list.
    assert T.menu_rows("claude-code", []) == []
    assert T.typed_builtin("claude-code", "plain words", []) is None
    assert T.typed_builtin("codex", "/model x", []) is None


async def test_model_and_effort_typed_for_grok_set_the_chips_without_a_start(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    monkeypatch.setattr(G, "cached_models", lambda path, home=None: (
        [{"id": "grok-4.7", "label": "Grok 4.7", "efforts": ["low", "high"]},
         {"id": "grok-4.5", "label": "Grok 4.5", "efforts": []}], "grok-4.7"))
    async with client(app) as c:
        s = await new_session(c, work, harness="grok")
        evs = frames((await _say(c, s["id"], "/model grok-4.7", "t1")).text)
        assert evs[-1]["stop"] == "completed"
        evs = frames((await _say(c, s["id"], "/effort low", "t2")).text)
        assert evs[-1]["stop"] == "completed"
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert row["model"] == "grok-4.7" and row["effort"] == "low"
        assert _notices(app, s["id"])[-2:] == ["Grok now uses Grok 4.7.", "Grok's effort level is now low."]
        r = await _say(c, s["id"], "/effort max", "t3")
        assert r.status_code == 400 and "low, high" in r.json()["detail"]
    assert not starts(log), "no Grok process is started for a setting"
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 7. Grok's sign-in press
# --------------------------------------------------------------------------- #
def test_grok_not_signed_in_carries_its_own_sign_in_press():
    ev = G.failure_event("Error: Not logged in. Run `grok login`.")
    assert ev["fix_kind"] == "sign_in" and ev["provider"] == "grok-cli"
    assert "Sign in" in ev["fix"] and "terminal" in ev["fix"]
    # Other failures carry no sign-in press.
    assert G.failure_event("boom").get("fix_kind") is None
    assert G.failure_event("HTTP 429 Too Many Requests")["fix_kind"] == "account_limit"


def test_the_dashboard_seam_is_what_the_daemon_sends():
    """``dashboard/__tests__/build-app-parity-v1439.test.tsx`` draws these."""
    got = {"grok_sign_in": {k: v for k, v in G.failure_event("Not logged in. Run grok login.").items() if k != "at"}}
    text = json.dumps(got, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("IJ_REGEN_BUILD_APP_PARITY") == "1":
        SEAM.write_bytes(text.encode("utf-8"))
    assert json.loads(SEAM.read_text(encoding="utf-8")) == got, "stale: IJ_REGEN_BUILD_APP_PARITY=1"
    assert fake_log  # (helpers imported for the end-to-end cases above)
