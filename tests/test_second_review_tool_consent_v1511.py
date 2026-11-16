"""v1.511.0 — consent in the service that runs other coding tools is exactly
what the user was shown (second review, group 11 part D: A2, A5, A12, H7, H9).

* A2: Claude Code's "Allow for this session" stays in THIS session — its own
  suggestion names ``localSettings`` (the folder's settings file, every later
  session); the answer says ``session``.
* A5: a helper never starts in a mode looser than the session that hands the
  work over; the card names the tool.
* A12: a stored "load them" press never passes ``--approve`` over the user's
  later "do not trust" for the folder.
* H7: a PATCH a later field refuses leaves the tool in its old mode.
* H9: a branch never starts in Full access, nor with Pi's folder press.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import branching as BR
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import handoff as HO
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import pi_trust as PT
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask, ControlRefused
from iron_jarvis.tools.base import ToolContext
from tests._build_app_helpers_v1409 import client, new_session, starts, stop_all
from tests._build_app_pi_helpers_v1411 import use_fake_pi

FIX = Path(__file__).parent / "fixtures" / "build_app_v1409"
ALL_TOOLS = ("claude-code", "codex", "pi", "opencode", "grok", "antigravity")


# --------------------------------------------------------------------------- #
# A2: "Allow for this session" is kept for this session only
# --------------------------------------------------------------------------- #
def _recorded_suggestions() -> list[dict[str, Any]]:
    """Claude Code's own ``permission_suggestions`` from the recorded exchange."""
    doc = json.loads((FIX / "claude_all_commands_v1417.json").read_text(encoding="utf-8"))
    found: list[list[dict[str, Any]]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if "permission_suggestions" in x:
                found.append(x["permission_suggestions"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(doc)
    assert found, "the recorded exchange carries Claude Code's suggestion"
    return found[0]


def _driver(mode: str = "ask") -> tuple[C.ClaudeDriver, list[Ask]]:
    asks: list[Ask] = []

    async def write(obj):
        return None

    drv = C.ClaudeDriver(write=write, emit=lambda ev: None, ask=asks.append, on_session_id=lambda s: None,
                         mode=mode, model="", effort="", cwd="")
    return drv, asks


def _card(drv: C.ClaudeDriver, asks: list[Ask], suggestions: list[dict[str, Any]],
          tool: str = "PowerShell") -> Ask:
    drv._on_control_request({"type": "control_request", "request_id": "req-1", "request": {
        "subtype": "can_use_tool", "tool_name": tool,
        "input": {"command": "Get-ChildItem -Force"} if tool == "PowerShell" else {"file_path": "C:/w/a.txt",
                                                                                   "content": "x"},
        "permission_suggestions": suggestions,
    }})
    assert asks, "the card was raised"
    return asks[-1]


def _sent_permissions(drv: C.ClaudeDriver, ask: Ask) -> list[dict[str, Any]]:
    out = drv.prepare_answer(ask, "allow_session", "", None)
    return out["response"]["response"]["updatedPermissions"]


def test_the_recorded_suggestion_for_the_folders_settings_goes_back_for_this_session_only():
    recorded = _recorded_suggestions()
    assert recorded[0]["destination"] == "localSettings", "the recording is what Claude Code really sends"
    before = copy.deepcopy(recorded)
    drv, asks = _driver()
    ask = _card(drv, asks, recorded)
    sent = _sent_permissions(drv, ask)
    assert [p["destination"] for p in sent] == ["session"]
    # The rule itself is unchanged: only where Claude Code keeps it.
    assert sent[0]["rules"] == [{"toolName": "PowerShell", "ruleContent": "Get-ChildItem *"}]
    assert sent[0]["type"] == "addRules" and sent[0]["behavior"] == "allow"
    # The card's words are true as written.
    assert "for the rest of this session" in ask.event["detail"]
    # Claude Code's own object is never changed.
    assert recorded == before


def test_nothing_reaches_the_answer_with_a_destination_other_than_this_session():
    raw = [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "npm test:*"}], "behavior": "allow",
         "destination": "projectSettings"},
        {"type": "replaceRules", "rules": [{"toolName": "Read"}], "behavior": "allow",
         "destination": "userSettings"},
        {"type": "removeRules", "rules": [{"toolName": "Bash", "ruleContent": "rm:*"}], "behavior": "deny",
         "destination": "localSettings"},
        {"type": "addDirectories", "directories": ["C:/other"], "destination": "localSettings"},
        {"type": "somethingNew", "destination": "localSettings", "what": "unknown"},
        {"type": "addRules", "rules": "not a list", "behavior": "allow", "destination": "localSettings"},
    ]
    kept = C.session_suggestions(raw)
    assert [k["type"] for k in kept] == ["addRules", "replaceRules", "removeRules", "addDirectories"], \
        "a suggestion whose shape is not known is dropped, never sent as it came"
    assert {k["destination"] for k in kept} == {"session"}
    drv, asks = _driver()
    sent = _sent_permissions(drv, _card(drv, asks, raw))
    assert sent and all(p["destination"] == "session" for p in sent)
    # An ask stored with the old shape is answered for this session too.
    old = Ask("ask_old", "approval", "PowerShell", "req-9",
              {"input": {}, "suggestions": [{"type": "addRules", "rules": [{"toolName": "Bash"}],
                                              "behavior": "allow", "destination": "localSettings"}]})
    stored = copy.deepcopy(old.data)
    assert [p["destination"] for p in _sent_permissions(drv, old)] == ["session"]
    assert old.data == stored, "the stored suggestion is never changed"


def test_only_unknown_suggestions_left_means_allow_once_only():
    drv, asks = _driver()
    ask = _card(drv, asks, [{"type": "somethingNew", "destination": "localSettings"}])
    assert ask.event.get("once_only") is True and ask.event["can_session"] is False


def test_a_set_mode_suggestion_is_treated_as_before():
    edits = [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    assert C.session_suggestions(edits) == edits
    assert C.suggested_mode(C.session_suggestions(edits)) == "edits"
    # Full access and Auto are never a side effect of an allow.
    assert C.session_suggestions([{"type": "setMode", "mode": "bypassPermissions", "destination": "session"}]) == []
    assert C.session_suggestions([{"type": "setMode", "mode": "auto", "destination": "session"}]) == []
    drv, asks = _driver()
    sent = _sent_permissions(drv, _card(drv, asks, edits, tool="Write"))
    assert sent == edits


# --------------------------------------------------------------------------- #
# A5: a helper is never looser than the session handing it work
# --------------------------------------------------------------------------- #
def test_every_mode_of_every_tool_has_its_strictness_written_down():
    for harness in ALL_TOOLS:
        for mode in S.modes_for(harness):
            assert mode in HO.MODE_STRICTNESS[harness], f"{harness} {mode}"
        for mode in HO.MODE_STRICTNESS[harness]:
            assert mode in S.MODE_ORDER
    assert "edits" in HO.MODE_STRICTNESS["codex"], "Codex's edits (off Windows) is in the table too"
    assert HO.MODE_STRICTNESS["pi"]["own"] == HO.DOES_NOT_ASK
    assert HO.MODE_STRICTNESS["antigravity"]["plan"] != HO.READS_ONLY, "Antigravity's plan can change files"


def test_a_session_on_ask_first_never_hands_work_to_a_tool_that_cannot_ask():
    with pytest.raises(S.Refused) as got:
        HO.helper_mode("claude-code", "ask", "pi")
    assert str(got.value) == ("Pi cannot ask before it changes files, and this session is on Ask first. "
                              "Hand this to a tool that can ask, or change this session's mode.")
    with pytest.raises(S.Refused) as got:
        HO.helper_mode("claude-code", "ask", "antigravity")
    assert str(got.value).startswith("Antigravity cannot ask before it changes files")
    # A tool that can ask takes it in a mode that asks.
    assert HO.helper_mode("claude-code", "ask", "codex") == "ask"
    assert HO.helper_mode("codex", "ask", "grok") == "ask"
    assert HO.helper_mode("claude-code", "ask", "claude-code") == "ask"


def test_a_session_on_plan_hands_work_only_to_a_mode_that_reads():
    assert HO.helper_mode("claude-code", "plan", "codex") == "plan"
    assert HO.helper_mode("claude-code", "plan", "opencode") == "plan"
    for want in ("pi", "grok", "antigravity"):
        with pytest.raises(S.Refused) as got:
            HO.helper_mode("claude-code", "plan", want)
        assert "has no mode that only reads and plans, and this session is on Plan" in str(got.value)


def test_auto_edits_never_hands_commands_to_a_tool_that_runs_them_unasked():
    with pytest.raises(S.Refused) as got:
        HO.helper_mode("claude-code", "edits", "pi")
    assert str(got.value).startswith("Pi cannot ask before it runs a command, and this session is on Auto edits.")
    assert HO.helper_mode("claude-code", "edits", "antigravity") == "edits"
    assert HO.helper_mode("claude-code", "edits", "codex") in ("edits", "ask")  # Windows has no Codex edits
    assert HO.helper_mode("claude-code", "edits", "grok") == "ask"


def test_a_session_that_already_runs_unasked_gives_the_helper_its_default_never_full_access():
    assert HO.helper_mode("claude-code", "auto", "pi") == "own"
    assert HO.helper_mode("claude-code", "auto", "grok") == "auto"
    assert HO.helper_mode("claude-code", "full", "claude-code") == "ask"
    assert HO.helper_mode("grok", "full", "antigravity") == "own"
    for want in ALL_TOOLS:
        for parent in ALL_TOOLS:
            for mode in S.modes_for(parent):
                try:
                    got = HO.helper_mode(parent, mode, want)
                except S.Refused:
                    continue
                assert got != "full"
                assert HO.MODE_STRICTNESS[want][got] <= HO.MODE_STRICTNESS[parent][mode], (parent, mode, want, got)


def test_the_hand_off_card_names_the_tool_it_will_start():
    assert W.mcp_call_title("iron-jarvis-code", "code_handoff_start", {"tool": "pi", "task": "Fix the report"}) == (
        'Hand work to Pi: "Fix the report"')
    assert W.mcp_call_title("iron-jarvis-code", "code_handoff_start", {"tool": "Codex"}) == "Hand work to Codex"
    assert C.tool_title("mcp__iron-jarvis-code__code_handoff_start", {"tool": "grok", "task": "Tidy"}) == (
        'Hand work to Grok: "Tidy"')
    assert W.mcp_call_title("iron-jarvis-code", "code_handoff_start", {"task": "Tidy"}) == (
        'Hand work to a helper on this same coding tool: "Tidy"')


async def test_the_refusal_is_the_hand_offs_answer_and_no_helper_is_made(tmp_path: Path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        async with client(app) as c:
            parent = (await new_session(c, work))["id"]  # Claude Code, Ask first
            r = await c.patch(f"/build/app/sessions/{parent}", json={"capabilities": {"handoff": True}})
            assert r.status_code == 200, r.text
        assert (await svc._row(parent)).mode == "ask"
        monkeypatch.setattr(svc, "harnesses", lambda **_: [
            {"id": "pi", "label": "Pi", "installed": True, "signed_in": True},
            {"id": "claude-code", "label": "Claude Code", "installed": True, "signed_in": True}])
        made: list[dict] = []

        async def create(body):
            made.append(dict(body))
            raise AssertionError("no helper is made")

        monkeypatch.setattr(svc, "create", create)
        p = app.state.platform
        ctx = ToolContext(workspace=work, session_id=f"mcp:code:{parent}", agent_run_id="", config=p.config,
                          event_bus=p.event_bus, engine=p.engine)
        r = await p.registry.get(HO.TOOL_START).execute({"tool": "pi", "task": "Tidy the report"}, ctx)
        assert not r.ok
        assert r.error == ("Pi cannot ask before it changes files, and this session is on Ask first. "
                           "Hand this to a tool that can ask, or change this session's mode.")
        assert made == [] and store.helpers_of(p.engine, parent) == []
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# H7: a PATCH a later field refuses changes nothing, the tool included
# --------------------------------------------------------------------------- #
class _Tool:
    """A running coding tool's driver: what it was told, and what it refuses."""

    def __init__(self, *, refuse_model: bool = False, refuse_mode_back: bool = False) -> None:
        self.mode = "ask"
        self.modes: list[str] = []
        self.models: list[str] = []
        self.effort = ""
        self.refuse_model = refuse_model
        self.refuse_mode_back = refuse_mode_back

    async def set_mode(self, mode: str) -> str:
        if self.modes and self.refuse_mode_back:
            raise ControlRefused("the tool did not change its mode")
        self.modes.append(mode)
        self.mode = mode
        return "now"

    async def set_model(self, model: str) -> str:
        if self.refuse_model:
            raise ControlRefused("Claude Code did not change its model")
        self.models.append(model)
        return "now"

    def refresh_ask(self, ask):
        return None


async def _running_session(tmp_path: Path, tool: _Tool):
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(tmp_path), mode="ask")
    live = svc._live(row)
    live.proc = SimpleNamespace(alive=True)
    live.driver = tool
    return app, svc, row, live


async def test_a_valid_mode_with_a_bad_model_leaves_the_tool_and_the_row_as_they_were(tmp_path: Path):
    tool = _Tool()
    app, svc, row, live = await _running_session(tmp_path, tool)
    try:
        with pytest.raises(S.Refused) as got:
            await svc.patch(row.id, {"mode": "edits", "model": "no;such model"})
        assert "model" in str(got.value)
        assert tool.modes == [] and tool.mode == "ask", "the tool never heard the mode"
        assert (await svc._row(row.id)).mode == "ask"
    finally:
        live.proc = None
        await stop_all(app)


async def test_a_model_the_tool_refuses_puts_the_mode_back(tmp_path: Path):
    tool = _Tool(refuse_model=True)
    app, svc, row, live = await _running_session(tmp_path, tool)
    try:
        with pytest.raises(S.Refused) as got:
            await svc.patch(row.id, {"mode": "edits", "model": "sonnet"})
        assert str(got.value) == "Claude Code did not change its model"
        assert tool.modes == ["edits", "ask"] and tool.mode == "ask", "the mode went back"
        assert (await svc._row(row.id)).mode == "ask"
    finally:
        live.proc = None
        await stop_all(app)


async def test_a_mode_that_cannot_go_back_is_said_and_the_row_tells_the_truth(tmp_path: Path):
    tool = _Tool(refuse_model=True, refuse_mode_back=True)
    app, svc, row, live = await _running_session(tmp_path, tool)
    try:
        with pytest.raises(S.Refused) as got:
            await svc.patch(row.id, {"mode": "edits", "model": "sonnet"})
        assert str(got.value) == ("Claude Code did not change its model Claude Code could not be put back to "
                                  "Ask first, so it works in Auto edits until you change the mode.")
        assert tool.mode == "edits"
        assert (await svc._row(row.id)).mode == "edits", "the row says the mode the tool works in"
    finally:
        live.proc = None
        await stop_all(app)


async def test_a_good_patch_still_changes_the_mode_and_the_model(tmp_path: Path):
    tool = _Tool()
    app, svc, row, live = await _running_session(tmp_path, tool)
    try:
        out = await svc.patch(row.id, {"mode": "edits", "model": "sonnet"})
        assert out["applied"]["mode"] == "now" and tool.modes == ["edits"] and tool.models == ["sonnet"]
        got = await svc._row(row.id)
        assert got.mode == "edits" and got.model == "sonnet"
    finally:
        live.proc = None
        await stop_all(app)


# --------------------------------------------------------------------------- #
# A12: a press never outlives the user's later "do not trust"
# --------------------------------------------------------------------------- #
@pytest.fixture()
def pi_scratch(monkeypatch, tmp_path):
    agent = tmp_path / "pi-agent"
    agent.mkdir()
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(P, "HANDLED_GRACE_S", 0.3)
    real_exists = PT._exists
    root = os.path.normcase(str(tmp_path))
    monkeypatch.setattr(PT, "_exists", lambda p: os.path.normcase(str(p)).startswith(root) and real_exists(p))
    PT.forget()
    yield {"agent": agent}
    PT.forget()


async def test_a_folder_declined_after_the_press_starts_pi_without_approve_and_says_so(
        tmp_path: Path, monkeypatch, pi_scratch):
    work = tmp_path / "work"
    (work / ".pi" / "extensions").mkdir(parents=True)
    (work / ".pi" / "extensions" / "hello.ts").write_text("export default function (pi) {}\n", encoding="utf-8")
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_read", log)
    app = create_app(str(tmp_path / "home"))
    try:
        async with client(app) as c:
            s = await new_session(c, work, harness="pi")
            r = await c.patch(f"/build/app/sessions/{s['id']}", json={"load_folder_resources": True})
            assert r.status_code == 200, r.text
            # Later, in Pi's own terminal, the user says "do not trust".
            (pi_scratch["agent"] / "trust.json").write_text(json.dumps({str(work): False}), encoding="utf-8")
            await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                          json={"text": "do it", "turn_id": "t1"}), 30)
            view = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        assert "--approve" not in starts(log)[0]["argv"], "a declined folder is never second-guessed"
        assert view["session"]["load_folder_resources"] is False, "the box shows what is true"
        notes = [e["text"] for e in view["events"] if e["type"] == "notice"]
        said = W.PI_FOLDER_DECLINED_AT_START.format(things="extensions")
        assert notes.count(said) == 1
    finally:
        await stop_all(app)


async def test_any_other_reading_keeps_the_press(tmp_path: Path, monkeypatch, pi_scratch):
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        row = store.create(app.state.platform.engine, harness="pi", folder=str(tmp_path), mode="own",
                           load_folder_resources=True)
        live = svc._live(row)
        for state in (PT.UNDECIDED, PT.TRUSTED, PT.UNKNOWN, PT.NONE):
            monkeypatch.setattr(PT, "check", lambda folder, fresh=False, env=None, s=state: {"state": s,
                                                                                             "found": []})
            got = await svc._pi_folder_at_start(live, row)
            assert got.load_folder_resources is True and live.pi_folder_note == ""
        assert (await svc._row(row.id)).load_folder_resources is True
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# H9: a branch never starts in Full access or with Pi's folder press
# --------------------------------------------------------------------------- #
def _svc_for_rows(tmp_path: Path):
    app = create_app(str(tmp_path / "home"))
    return app, S.service_for(app.state.platform)


async def test_a_branch_of_a_full_access_session_starts_on_the_tools_default_and_says_so(tmp_path: Path):
    app, svc = _svc_for_rows(tmp_path)
    try:
        caps = json.dumps({"browser": True})
        row = store.create(svc.engine, harness="claude-code", folder=str(tmp_path), mode="full",
                           capabilities_json=caps)
        made = BR._make_row(svc, row, "", [], 0, None, "")
        assert made.mode == "ask", "Full access is two presses of the user's own, never copied"
        assert made.capabilities_json == caps, "the Iron Jarvis tools are the conversation's own"
        notes: list[str] = []
        BR._make_row(svc, row, "", [], 0, None, "", notes)
        assert "It starts on Ask first, not Full access: turn that on again here if you want it." in notes
        assert BR.FORK_KEEPS_TOOLS in notes
    finally:
        await stop_all(app)


async def test_a_branch_of_pi_does_not_carry_the_folder_press(tmp_path: Path):
    app, svc = _svc_for_rows(tmp_path)
    try:
        row = store.create(svc.engine, harness="pi", folder=str(tmp_path), mode="own", load_folder_resources=True)
        made = BR._make_row(svc, row, "", [], 0, None, "")
        assert made.load_folder_resources is False and made.mode == "own"
        notes: list[str] = []
        BR._make_row(svc, row, "", [], 0, None, "", notes)
        assert notes == [BR.FORK_PI_FOLDER_ASKED]
    finally:
        await stop_all(app)


async def test_other_modes_are_carried_and_nothing_is_said(tmp_path: Path):
    app, svc = _svc_for_rows(tmp_path)
    try:
        row = store.create(svc.engine, harness="antigravity", folder=str(tmp_path), mode="full")
        assert BR._make_row(svc, row, "", [], 0, None, "").mode == "own"
        for harness, mode in (("claude-code", "edits"), ("codex", "plan"), ("antigravity", "own")):
            row = store.create(svc.engine, harness=harness, folder=str(tmp_path), mode=mode)
            notes: list[str] = []
            made = BR._make_row(svc, row, "", [], 0, None, "", notes)
            assert made.mode == mode and notes == []
        row = store.create(svc.engine, harness="antigravity", folder=str(tmp_path), mode="full")
        notes = []
        BR._make_row(svc, row, "", [], 0, None, "", notes)
        assert notes == ["It starts on Antigravity's own rules, not Full access: turn that on again here if you "
                         "want it."]
    finally:
        await stop_all(app)
