"""v1.441.0 — Build › Code's daemon parity, the review's findings P1-P5, X3 and
X4, each pinned red first on v1.439.0's code.

P1. A mode picked on the chip while Claude Code's plan card waits is the
    session's, and approving the plan never overwrites it — never into Full
    access either. READ from claude 2.1.316's own bundle (ExitPlanModeV2Tool):
    Claude Code goes back to its ``prePlanMode`` only while it is STILL in
    plan mode.
P2. ``/model opus`` (sonnet, haiku, a ``[1m]`` form, the picker's own value)
    resolves for Claude Code; a name nothing resolves is sent to Claude Code
    as typed (v1.438.0), never refused.
P3. Claude Code's ``/`` list is kept per FOLDER (a project's own commands are
    in it — measured on claude 2.1.316, see ``hosted/claude.py``); before it
    has been read for the folder nothing is answered here, so a command of
    the user's own named ``status`` reaches Claude Code.
P4. A rename while the tool is stopped reaches it once it starts again.
P5. A typed ``/export`` points at the Code view's own Export as Markdown.
X3. The closed-window toast names Grok and Antigravity.
X4. The from-terminal refusal and the chat tools' words name all six tools.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as CL
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import typed as T
from iron_jarvis.hosted import words as W
from iron_jarvis.providers import claude_models as CM
from tests._build_app_helpers_v1409 import client, frames, new_session, sent, stop_all, until, use_fake

#: The picker claude 2.1.316 answers ``initialize`` with (the review's
#: scratch check, check_model.py).
LIVE_PICKER = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet",
     "description": "Sonnet 5.5 · Efficient for routine tasks"},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku",
     "description": "Haiku 4.5 · Fastest for quick answers"},
]


@pytest.fixture
def work(tmp_path):
    p = tmp_path / "work"
    p.mkdir()
    return p


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def live_catalog(monkeypatch):
    import json

    cat = CM._parse(json.dumps({"type": "control_response", "response": {"subtype": "success", "response": {
        "models": LIVE_PICKER, "account": {"subscriptionType": "max", "email": "x@example.com"}}}}))
    monkeypatch.setattr(CM, "_known", lambda: cat)
    monkeypatch.setattr(CM, "catalog", lambda refresh=False: cat)
    return cat


# --------------------------------------------------------------------------- #
# P1. the plan card and the chip
# --------------------------------------------------------------------------- #
async def _plan_card_then(app, work, tmp_path, monkeypatch, *, start: str, picked: str | None,
                          decision: str = "allow") -> str:
    monkeypatch.setenv("IJ_FAKE_CLAUDE_MODES", "1")
    use_fake(monkeypatch, "claude_plan_v1417", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    extra = {"mode": start, "confirm": True} if start == "full" else {"mode": start}
    async with client(app) as c:
        sid = (await new_session(c, work, **extra))["id"]
        if start != "plan":
            r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "plan"})
            assert r.status_code == 200, r.text
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        rid = await until(lambda: next(iter(svc.live.get(sid).asks), None) if svc.live.get(sid) else None)
        if picked is not None:
            body = {"mode": picked, "confirm": True} if picked == "full" else {"mode": picked}
            r = await c.patch(f"/build/app/sessions/{sid}", json=body)
            assert r.status_code == 200, r.text
        r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": rid, "decision": decision})
        assert r.status_code == 200, r.text
        await asyncio.wait_for(task, 30)
        row = store.get(app.state.platform.engine, sid)
        drv = svc.live[sid].driver
        assert drv is None or drv.mode == row.mode, (drv.mode, row.mode)
    await stop_all(app)
    return row.mode


async def test_a_mode_picked_while_the_plan_card_waits_wins(app, work, tmp_path, monkeypatch):
    assert await _plan_card_then(app, work, tmp_path, monkeypatch, start="plan", picked="edits") == "edits"


async def test_approving_a_plan_never_puts_the_session_back_in_full_access(app, work, tmp_path, monkeypatch):
    """Full access -> Plan (the card names Full access as where it goes back)
    -> the user picks Ask first while the card waits -> Allow: Ask first."""
    assert await _plan_card_then(app, work, tmp_path, monkeypatch, start="full", picked="ask") == "ask"


async def test_with_no_pick_the_approved_plan_still_leaves_plan(app, work, tmp_path, monkeypatch):
    assert await _plan_card_then(app, work, tmp_path, monkeypatch, start="plan", picked=None) == "ask"


def test_the_driver_reads_the_way_back_at_the_answer():
    asks: list = []
    d = CL.ClaudeDriver(write=None, emit=lambda ev: None, ask=asks.append, on_session_id=lambda s: None,
                        mode="full")
    d.pre_plan_mode = ""
    d.mode = "plan"
    d.pre_plan_mode = "full"
    d._on_control_request({"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "ExitPlanMode", "input": {"plan": "p"}}})
    ask = asks[-1]
    assert ask.data["leaves_plan_to"] == "full"
    d.mode = "ask"  # the chip, while the card waited
    assert d.plan_exit_mode(ask, "allow") == ""
    d.answered(ask, "allow")
    assert d.mode == "ask"


# --------------------------------------------------------------------------- #
# P2. /model with Claude Code's own names
# --------------------------------------------------------------------------- #
def _rows(cat) -> list[dict]:
    return [{"id": "", "label": "Claude Code's default"}] + [
        {"id": m["id"], "label": m["label"]} for m in cat["models"]]


@pytest.mark.parametrize("typed, want", [
    ("opus", "claude-opus-5-5"), ("Sonnet", "claude-sonnet-5-5"), ("haiku", "claude-haiku-4-5-20251001"),
    ("sonnet[1m]", "claude-sonnet-5-5"), ("Opus 5.5", "claude-opus-5-5"), ("claude-sonnet-5-5", "claude-sonnet-5-5"),
    ("default", ""), ("sonnet-9", None),
])
def test_claude_codes_own_model_names_resolve(live_catalog, typed, want):
    assert T.resolve_claude_model(typed, _rows(live_catalog)) == want


def test_before_the_catalog_is_read_a_name_resolves_to_nothing(monkeypatch):
    monkeypatch.setattr(CM, "_known", lambda: {"models": []})
    assert T.resolve_claude_model("opus", [{"id": "", "label": "Claude Code's default"}]) is None


async def test_model_opus_typed_sets_the_chip(app, work, tmp_path, monkeypatch, live_catalog):
    monkeypatch.setattr(CM, "model_efforts", lambda model: None)
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "edits"})
        # Bounded: sent to Claude Code instead (the bug), this recording has
        # no reply for it.
        r = await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                          json={"text": "/model sonnet", "turn_id": "t2"}), 15)
        assert r.status_code == 200, r.text
        assert frames(r.text)[-1]["stop"] == "completed"
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert row["model"] == "claude-sonnet-5-5"
    await stop_all(app)


async def test_a_model_name_nothing_resolves_goes_to_claude_code(app, work, tmp_path, monkeypatch):
    monkeypatch.setattr(CM, "_known", lambda: {"models": []})
    monkeypatch.setattr(CM, "catalog", lambda refresh=False: {"models": []})
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "/model opus", "turn_id": "t1"})
        assert r.status_code == 200, r.text
        users = [m for m in sent(log) if m.get("type") == "user"]
        assert users and "/model opus" in str(users[0])
    await stop_all(app)


# --------------------------------------------------------------------------- #
# P3. the `/` list per folder
# --------------------------------------------------------------------------- #
def _open_claude(folder: str, commands: list[dict]) -> CL.ClaudeDriver:
    d = CL.ClaudeDriver(write=None, emit=lambda ev: None, ask=lambda a: None, on_session_id=lambda s: None,
                        mode="ask", cwd=folder)

    async def control(subtype, **kw):
        return {"commands": commands}

    d.control = control  # type: ignore[method-assign]
    asyncio.run(d.open(session_id="s", resume=False))
    return d


def test_each_folder_keeps_its_own_list_and_the_bare_list_holds_claude_codes_own(app, tmp_path, monkeypatch):
    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _open_claude(str(a), [{"name": "deploy-a", "description": "Ship (project)"},
                          {"name": "compact", "description": "Compact", "builtin": True}])
    svc = S.service_for(app.state.platform)
    names_a = [r["name"] for r in svc.commands_for("claude-code", folder=str(a)) if "note" not in r]
    names_b = [r["name"] for r in svc.commands_for("claude-code", folder=str(b)) if "note" not in r]
    assert names_a == ["deploy-a", "compact"]
    assert names_b == ["compact"], "another project's command never shows"
    assert [r["name"] for r in svc.commands_for("claude-code") if "note" not in r] == ["compact"]


def test_an_older_claude_code_that_marks_no_builtin_keeps_its_whole_list(monkeypatch):
    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    _open_claude("C:\\w", [{"name": "init", "description": "Set up"}])
    assert [c["name"] for c in CL.LAST_COMMANDS["claude-code"]] == ["init"]


async def test_a_users_own_status_command_reaches_claude_code_before_its_list_is_read(app, work, tmp_path, monkeypatch):
    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    async with client(app) as c:
        s = await new_session(c, work)
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "/status now", "turn_id": "t1"})
        assert r.status_code == 200, r.text
        users = [m for m in sent(log) if m.get("type") == "user"]
        assert users and "/status now" in str(users[0])
    await stop_all(app)


def test_the_terminal_only_check_reads_only_this_folders_list(app, tmp_path, monkeypatch):
    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _open_claude(str(a), [{"name": "status", "description": "Mine (project)"},
                          {"name": "compact", "description": "Compact", "builtin": True}])
    assert S._own_commands("claude-code", None, str(b)) == []
    assert T.typed_builtin("claude-code", "/status", S._own_commands("claude-code", None, str(a))) is None
    _open_claude(str(b), [{"name": "compact", "description": "Compact", "builtin": True}])
    assert T.typed_builtin("claude-code", "/status", S._own_commands("claude-code", None, str(b))) == ("status", "")


# --------------------------------------------------------------------------- #
# P4. a rename while stopped
# --------------------------------------------------------------------------- #
async def test_a_rename_while_stopped_reaches_claude_code_once_it_starts(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"title": "Fix the cart"})
        assert r.status_code == 200
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        hid = svc.live[s["id"]].driver.session_id

        def renames():
            return [m["request"] for m in sent(log) if m.get("type") == "control_request"
                    and (m.get("request") or {}).get("subtype") == "rename_session"]

        got = await until(renames)
        assert got == [{"subtype": "rename_session", "title": "Fix the cart", "source": "host", "session_id": hid}]
        await asyncio.sleep(0.3)
    assert len(renames()) == 1, "told once"
    await stop_all(app)


# --------------------------------------------------------------------------- #
# P5. /export
# --------------------------------------------------------------------------- #
def test_a_typed_export_points_at_export_as_markdown():
    words = T.refusal("claude-code", "export")
    assert "Export as Markdown" in words and "terminal" not in words.lower()


# --------------------------------------------------------------------------- #
# X3. the closed-window toast
# --------------------------------------------------------------------------- #
def test_the_tray_names_every_code_view_tool(tmp_path):
    import tests.test_chat_lanes_v13510 as L

    if shutil.which("node") is None:
        pytest.skip("no node")
    for harness in S.HARNESSES if hasattr(S, "HARNESSES") else ("claude-code", "codex", "pi", "opencode",
                                                                  "grok", "antigravity"):
        row = {"id": f"build_app:app_{harness}", "kind": "chat", "thread_id": f"app_{harness}", "tool": "Bash",
               "requested_at": "x", "mission_id": None, "project_id": None, "surface": "build_app",
               "app_session": f"app_{harness}", "harness": harness}
        note = L._plan([{"approvals": [row], "now": 0}], tmp_path)[0]["notify"][0]
        assert note["title"] == f"{W.label(harness)} is waiting for your OK", (harness, note["title"])


# --------------------------------------------------------------------------- #
# X4. all six tools named
# --------------------------------------------------------------------------- #
def test_the_from_terminal_refusal_and_the_chat_tools_name_every_tool():
    from iron_jarvis.settings import records_build as RB

    labels = [W.label(h) for h in ("claude-code", "codex", "pi", "opencode", "grok", "antigravity")]
    for text in (W.NOT_A_CODING_TERMINAL, RB.BuildFolderShowTool.description, RB._VIEW_REFUSAL):
        assert all(label in text for label in labels), text
