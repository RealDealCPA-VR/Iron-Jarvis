"""v1.439.0 — Google Antigravity (``agy``) in Build's Code view: the user's REAL
Antigravity (``agy acp``, the Agent Client Protocol over stdio), with its own
tools, skills and settings ON; Iron Jarvis only hosts it, turns every question
it asks into a card and reads its failures out of its own words and log.

Every turn replays a RECORDED agy 1.5.2 session
(``tests/fixtures/build_app_v1439``: runs through the real Code-view service via
a logging proxy, a scratch home, every model call answered by a LOCAL stand-in
Gemini API — nothing reached Google, no sign-in; masked) through the fake
harness process (``tests/fixtures/fake_harness_v1409.py``) started by the real
``core.procs`` plumbing; the driver, routes and store are the real ones.

THE CONTRACT: ``dashboard/__tests__/fixtures/build-app-events-v1439.json`` is
written FROM THE REAL DAEMON fed those recordings. Regenerate after an intended
change with ``IJ_REGEN_BUILD_APP_AGY=1``. No new event kind.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity as A
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask, AskRefused
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all, until

FIX = Path(__file__).parent / "fixtures" / "build_app_v1439"
GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-events-v1439.json"
RESUMED = "fa0048f0-6480-4831-bb4d-767c9955dc6a"  # a_resume's own conversation id

#: scenario -> (fixture, create body, what the user does, a picture?)
SCENARIOS: dict[str, tuple[str, dict, list, bool]] = {
    "antigravity_replies": ("a_reply", {}, [], False),
    "antigravity_command_allowed_once": ("a_cmd_allow", {}, [("answer", "allow", None)], False),
    "antigravity_command_denied_ends_the_reply": ("a_cmd_deny", {}, [("answer", "deny", None)], False),
    "antigravity_changes_files_without_a_card": ("a_write_own", {}, [], False),
    "antigravity_auto_edits_still_asks_before_a_command": ("a_edits_cmd", {"mode": "edits"},
                                                            [("answer", "allow", None)], False),
    "antigravity_question_answered": ("a_question", {}, [("answer", "allow", {"Which color?": "Red"})], False),
    "antigravity_question_typed": ("a_question_other", {}, [("answer", "allow", {"Which color?": "Green, please"})],
                                   False),
    "antigravity_question_skipped": ("a_question_skip", {}, [("answer", "deny", None)], False),
    "antigravity_note_while_it_works": ("a_steer", {}, [("steer", "also say banana")], False),
    "antigravity_stop": ("a_cancel", {"mode": "full", "confirm": True}, [("interrupt",)], False),
    "antigravity_failed_reply": ("a_fail", {}, [], False),
    "antigravity_retries_the_model": ("a_retry", {}, [], False),
    "antigravity_model_and_level": ("a_model", {"model": "gemini-3.8-flash", "effort": "high"}, [], False),
    "antigravity_picture": ("a_picture", {}, [], True),
    "antigravity_plan": ("a_plan", {"mode": "plan"}, [], False),
    "antigravity_full_access": ("a_full", {"mode": "full", "confirm": True}, [], False),
    "antigravity_not_signed_in": ("a_no_signin", {}, [], False),
}


def use_fake(monkeypatch, scenario: str, log: Path) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / f"{scenario}.json"))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


def canonical(events: list[dict], work: Path) -> list[dict]:
    items: dict[str, str] = {}
    asks: dict[str, str] = {}
    out = []
    for ev in events:
        text = json.dumps(ev, ensure_ascii=False)
        text = text.replace(json.dumps(str(work))[1:-1], "<WORK>")
        ev = {k: v for k, v in json.loads(text).items() if k != "at"}
        for key, table, prefix in (("request_id", asks, "ask"), ("item", items, "item")):
            if ev.get(key):
                table.setdefault(ev[key], f"{prefix}-{len(table) + 1}")
                ev[key] = table[ev[key]]
        if ev.get("type") == "steer":
            ev["id"] = "note-1"
        if "turn" in ev:
            ev["turn"] = "turn-1"
        out.append(ev)
    return out


async def run_agy(tmp_path: Path, monkeypatch, name: str, *, text: str = "do it", before=None):
    fixture, body, actions, picture = SCENARIOS[name]
    base = tmp_path / name
    work = base / "work"
    work.mkdir(parents=True)
    (work / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    log = base / "fake.log"
    use_fake(monkeypatch, fixture, log)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    files = []
    if picture:
        (work / "word.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        files = [str(work / "word.png")]
    async with client(app) as c:
        sid = (await new_session(c, work, harness="antigravity", **body))["id"]
        if before is not None:
            before(app, sid)
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn",
                                            json={"text": text, "turn_id": "t", "files": files}))
        for act in actions:
            if act[0] == "answer":
                ask = await until(lambda: next(iter(svc.live[sid].asks), None) if sid in svc.live else None)
                r = await c.post(f"/build/app/sessions/{sid}/answer",
                                 json={"request_id": ask, "decision": act[1], "answers": act[2]})
                assert r.status_code == 200, r.text
            elif act[0] == "interrupt":
                await until(lambda: svc.turn("t") and any(
                    e["type"] == "tool" and e.get("kind") == "command" for e in svc.turn("t").events))
                r = await c.post(f"/build/app/sessions/{sid}/interrupt")
                assert r.status_code == 200, r.text
            else:
                await until(lambda: svc.turn("t") and svc.turn("t").running)
                r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": act[1]})
                assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
    await stop_all(app)
    return evs, sid, work, log, view, app


def _picked(log: Path) -> list:
    return [m["result"]["outcome"].get("optionId") for m in sent(log)
            if isinstance(m.get("result"), dict) and "outcome" in m["result"]]


def _form_answers(log: Path) -> list:
    return [m["result"] for m in sent(log)
            if isinstance(m.get("result"), dict) and "action" in m["result"]]


# --------------------------------------------------------------------------- #
# THE CONTRACT
# --------------------------------------------------------------------------- #
async def test_the_golden_file_is_what_the_real_daemon_sends_for_antigravity(tmp_path, monkeypatch):
    got = {}
    for name in SCENARIOS:
        evs, _sid, work, _log, _view, _app = await run_agy(tmp_path, monkeypatch, name)
        got[name] = canonical(evs, work)
    doc = {
        "_about": (
            "v1.439.0 Build Code view, Antigravity: the normalized events the REAL daemon sends for each "
            "scenario, produced by tests/test_build_app_antigravity_v1439.py from recorded agy 1.5.2 output "
            "(tests/fixtures/build_app_v1439, `agy acp` driven through the real service, a local stand-in "
            "model server, masked). `at` is dropped; request ids are ask-N; item ids are item-N; every turn "
            "id is turn-1; a note's id is note-1; the folder is <WORK>. Regenerate with IJ_REGEN_BUILD_APP_AGY=1."
        ),
        "scenarios": got,
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("IJ_REGEN_BUILD_APP_AGY") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(text.encode("utf-8"))
    on_disk = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert on_disk["scenarios"] == got, "the golden file is stale — IJ_REGEN_BUILD_APP_AGY=1 after an intended change"


def test_every_scenario_ends_once_and_uses_only_contract_types():
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(doc["scenarios"]) == set(SCENARIOS)
    for name, evs in doc["scenarios"].items():
        assert evs[0]["type"] == "user", name
        assert [e["type"] for e in evs].count("done") == 1 and evs[-1]["type"] == "done", name
        assert {e["type"] for e in evs} <= set(E.TYPES), name
        for e in evs:
            if e["type"] == "tool":
                assert e["kind"] in E.TOOL_KINDS, name
                assert e["status"] != "running" or e is not evs[-2], name
            if e["type"] == "approval":
                assert e["kind"] in E.APPROVAL_KINDS and e["once_only"] is True, name
            if e["type"] == "text":
                assert "⚠" not in e["delta"], "Antigravity's failure lines are never reply text"


def test_the_recordings_are_masked():
    # Antigravity's own recordings (a_*): this folder also holds other lanes'
    # fixtures, whose already-masked paths ("C:\Users\me\AppData…") are fine.
    assert len(list(FIX.glob("a_*"))) >= 18
    for path in FIX.glob("a_*"):
        text = path.read_text(encoding="utf-8")
        for leak in ("\\Users\\VR", "/Users/VR", "scratchpad", "AppData"):
            assert leak not in text, (path.name, leak)
        if path.suffix == ".json":
            doc = json.loads(text)
            assert doc["harness"] == "antigravity" and "agy 1.5.2" in doc["source"], path.name


# --------------------------------------------------------------------------- #
# What reaches Antigravity
# --------------------------------------------------------------------------- #
async def test_antigravity_starts_its_own_acp_with_its_own_log_and_no_telemetry(tmp_path, monkeypatch):
    evs, sid, work, log, view, app = await run_agy(tmp_path, monkeypatch, "antigravity_replies")
    start = starts(log)[0]
    expected_log = str(Path(app.state.platform.config.home) / "hosted" / sid / A.LOG_NAME)
    assert start["argv"] == ["acp", "--disable-telemetry", "--log-file", expected_log]
    assert A.FULL_FLAG not in start["argv"]
    assert start["cwd"] == str(work)
    msgs = sent(log)
    assert [m.get("method") for m in msgs[:3]] == ["initialize", "session/new", "session/prompt"]
    assert msgs[0]["params"]["clientCapabilities"] == {
        "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False, "elicitation": {"form": {}}}, \
        "its question form is declared (without it Antigravity prints the question and ends the turn)"
    assert not any(m.get("method") == "authenticate" for m in msgs), "Iron Jarvis never chooses its sign-in"
    assert msgs[1]["params"] == {"cwd": str(work), "mcpServers": []}
    sess = next(e for e in evs if e["type"] == "session")
    assert sess["harness"] == "antigravity" and sess["model"] == "gemini-3.1-pro" and sess["mode"] == "own"
    assert [e["delta"] for e in evs if e["type"] == "thinking"] == ["Let me think."]
    usage = next(e for e in evs if e["type"] == "usage")
    assert (usage["input"], usage["output"]) == (120, 7) and "cost_usd" not in usage
    assert "cost_reported_by" not in usage and usage["list_price"] is False
    assert evs[-1]["stop"] == "completed"


async def test_each_mode_is_antigravitys_own_and_full_access_is_its_flag(tmp_path, monkeypatch):
    _e, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_auto_edits_still_asks_before_a_command")
    modes = [m["params"] for m in sent(log) if m.get("method") == "session/set_mode"]
    assert [p["modeId"] for p in modes] == ["accept-edits"]
    _e, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_plan")
    assert [m["params"]["modeId"] for m in sent(log) if m.get("method") == "session/set_mode"] == ["plan"]
    _e, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_full_access")
    assert starts(log)[0]["argv"][-1] == A.FULL_FLAG
    assert not [m for m in sent(log) if m.get("method") == "session/set_mode"], "Full access is its default mode"


async def test_a_mode_change_is_live_except_to_or_from_full_access():
    calls: list[dict] = []

    async def write(obj):
        calls.append(obj)

    d = A.AntigravityDriver(write=write, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                            mode="own")
    d.session_id = "s1"
    d.request = lambda method, params=None, timeout=None: _record(calls, method, params)
    assert await d.set_mode("edits") == "now" and calls[-1] == {"method": "session/set_mode",
                                                               "params": {"sessionId": "s1", "modeId": "accept-edits"}}
    n = len(calls)
    assert await d.set_mode("full") == "next" and d.restart_needed is True and len(calls) == n
    assert "--dangerously-skip-permissions" in d.argv(session_id="", resume=False)


async def _record(calls, method, params):
    calls.append({"method": method, "params": params})
    return {}


async def test_the_model_and_its_level_are_set_live_and_each_model_keeps_its_own_levels(tmp_path, monkeypatch):
    evs, sid, _w, log, _v, app = await run_agy(tmp_path, monkeypatch, "antigravity_model_and_level")
    sets = [m["params"] for m in sent(log) if m.get("method") == "session/set_config_option"]
    assert [(p["configId"], p["value"]) for p in sets] == [("model", "gemini-3.8-flash"), ("thought_level", "high")]
    assert next(e for e in evs if e["type"] == "session")["model"] == "gemini-3.8-flash"
    kept = json.loads((Path(app.state.platform.config.home) / "hosted" / "antigravity_models.json")
                      .read_text(encoding="utf-8"))
    levels = {r["id"]: r["efforts"] for r in kept["models"]}
    assert levels["gemini-3.1-pro"] == ["low", "high"] and levels["gemini-3.8-flash"] == ["low", "medium", "high"]
    assert kept["default"] == "gemini-3.1-pro"
    # The chip has the list before any process runs (Iron Jarvis's own copy).
    A.LAST_MODELS.clear()
    svc = S.service_for(app.state.platform)
    rows = svc._models_with_efforts("antigravity")
    assert rows[0]["id"] == "" and rows[0]["efforts"] == ["low", "high"], "the default row takes its model's levels"
    assert {r["id"]: r.get("efforts") for r in rows[1:]}["gemini-4-argon"] == ["low", "medium", "high"]
    assert S.efforts_for("antigravity", "gemini-3.1-pro", rows) == ("low", "high")


def test_model_rows_read_each_models_levels_from_its_own_ids():
    doc = json.loads((FIX / "a_reply.json").read_text(encoding="utf-8"))
    res = next(s["out"]["result"] for s in doc["steps"]
               if isinstance(s.get("out"), dict) and isinstance(s["out"].get("result"), dict)
               and "configOptions" in s["out"]["result"])
    rows, current, level = A.model_rows(res["configOptions"], res["models"])
    assert current == "gemini-3.1-pro" and level == "low"
    assert [r["id"] for r in rows] == ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
                                       "gemini-3.1-pro"]
    assert {r["id"]: r["efforts"] for r in rows}["gemini-3.1-pro"] == ["low", "high"]


# --------------------------------------------------------------------------- #
# Cards and questions
# --------------------------------------------------------------------------- #
async def test_a_command_card_is_allow_once_only_and_a_deny_ends_the_reply_in_words(tmp_path, monkeypatch):
    evs, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_command_allowed_once")
    card = next(e for e in evs if e["type"] == "approval")
    assert card["kind"] == "command" and card["command"] == "echo hello-from-agy"
    assert card["title"] == "Run echo hello-from-agy"
    assert card["once_only"] is True and card["can_session"] is False, "Antigravity offers no session-wide allow"
    assert _picked(log) == ["allow"]
    done = [e for e in evs if e["type"] == "tool" and e["status"] == "done"]
    assert done and "hello-from-agy" in done[-1]["output"]
    evs, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_command_denied_ends_the_reply")
    assert _picked(log) == ["deny"]
    assert W.ANTIGRAVITY_DECLINED_ENDS in [e.get("text") for e in evs if e["type"] == "notice"]
    assert evs[-1]["stop"] == "completed", "a Deny is not the user's Stop"


def _ask(options):
    return Ask("ask_1", "approval", "run_command", "7", {"options": [{"optionId": i, "kind": k} for i, k in options]},
               {})


def test_the_driver_never_offers_a_session_wide_allow():
    d = A.AntigravityDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                            mode="own")
    ask = _ask([("allow", "allow_once"), ("deny", "reject_once")])
    with pytest.raises(AskRefused):
        d.prepare_answer(ask, "allow_session", "", None)
    assert d.prepare_answer(ask, "allow", "", None)["result"]["outcome"] == {"outcome": "selected", "optionId": "allow"}
    assert d.prepare_answer(ask, "deny", "", None)["result"]["outcome"]["optionId"] == "deny"
    assert d.prepare_answer(ask, "deny", "", None)["id"] == "7"


async def test_files_change_without_a_card_and_the_diff_is_its_own_input(tmp_path, monkeypatch):
    evs, _s, _w, _log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_changes_files_without_a_card")
    assert not [e for e in evs if e["type"] == "approval"], "measured: Antigravity's default changes files unasked"
    rows = [e for e in evs if e["type"] == "tool" and e["status"] == "done"]
    write, edit = rows
    assert write["kind"] == "write" and write["title"] == "Write note.txt" and write["path"] == "<WORK>/note.txt"
    assert "+hi" in write["diff"]
    assert edit["kind"] == "edit" and edit["title"] == "Edit hello.txt"
    assert "-apple" in edit["diff"] and "+pear" in edit["diff"]
    assert next(m for m in S.mode_rows("antigravity") if m["id"] == "own")["about"].count("without asking") == 1


async def test_a_question_is_a_card_and_its_answer_is_the_forms_own(tmp_path, monkeypatch):
    evs, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_question_answered")
    q = next(e for e in evs if e["type"] == "question")
    assert q["questions"] == [{"question": "Which color?", "options": [{"label": "(Recommended) Blue"},
                                                                       {"label": "Red"}], "multi": False}]
    assert _form_answers(log) == [{"action": "accept", "content": {"q1": "2"}}]
    _e, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_question_typed")
    assert _form_answers(log) == [{"action": "accept", "content": {"q1_other": "Green, please"}}]
    evs, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_question_skipped")
    assert _form_answers(log) == [{"action": "decline"}]
    assert evs[-1]["stop"] == "completed", "a skipped question lets Antigravity go on (it read 'User Skipped')"


def test_the_question_form_maps_labels_and_typed_answers():
    qs = A.form_questions({"properties": {
        "q1": {"title": "Which color?", "oneOf": [{"const": "1", "title": "Blue"}, {"const": "2", "title": "Red"}]},
        "q1_other": {"title": "Other"},
        "q2": {"title": "Size?", "oneOf": [{"const": "1", "title": "S"}]},
    }})
    assert [q["key"] for q in qs] == ["q1", "q2"] and qs[0]["other"] == "q1_other" and qs[1]["other"] == ""
    assert A.form_answer(qs, {"Which color?": "Red", "Size?": "S"}) == {"q1": "2", "q2": "1"}
    assert A.form_answer(qs, ["Teal", None]) == {"q1_other": "Teal"}
    with pytest.raises(AskRefused):
        A.form_answer(qs, {"Size?": "XL"})
    with pytest.raises(AskRefused):
        A.form_answer(qs, {})


# --------------------------------------------------------------------------- #
# Notes, Stop, failures
# --------------------------------------------------------------------------- #
async def test_a_note_is_a_second_prompt_read_on_its_echo_and_the_latest_answer_ends_the_turn(tmp_path, monkeypatch):
    evs, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_note_while_it_works")
    prompts = [m for m in sent(log) if m.get("method") == "session/prompt"]
    assert [p["params"]["prompt"][0]["text"] for p in prompts] == ["do it", "also say banana"]
    states = [e["state"] for e in evs if e["type"] == "steer"]
    assert states == ["sent", "read"]
    assert [e["type"] for e in evs].count("done") == 1 and evs[-1]["stop"] == "completed"
    assert next(e for e in evs if e["type"] == "usage")["input"] == 120, "the LATEST prompt's answer carries usage"


def test_a_superseded_prompt_does_not_end_the_turn():
    seen: list[dict] = []
    d = A.AntigravityDriver(write=None, emit=seen.append, ask=lambda a: None, on_session_id=lambda s: None,
                            mode="own")
    loop = asyncio.new_event_loop()
    try:
        first, second = loop.create_future(), loop.create_future()
        d.turn_open = True
        d._pending = {1: "", 2: "note"}
        d._message_prompt, d._latest = 1, 2
        first.set_result({"stopReason": "end_turn"})
        d._prompt_answered(1, first)
        assert d.turn_open and not [e for e in seen if e["type"] == "done"]
        second.set_result({"stopReason": "end_turn", "usage": {"inputTokens": 5, "outputTokens": 1}})
        d._prompt_answered(2, second)
        assert not d.turn_open and seen[-1] == {**seen[-1], "type": "done", "stop": "completed"}
    finally:
        loop.close()


async def test_stop_is_its_cancel_and_a_step_it_said_no_more_about_is_drawn_stopped(tmp_path, monkeypatch):
    evs, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_stop")
    assert any(m.get("method") == "session/cancel" and "id" not in m for m in sent(log))
    assert evs[-1]["stop"] == "interrupted"
    rows = [e for e in evs if e["type"] == "tool"]
    assert rows[-1]["status"] == "failed" and rows[-1]["kind"] == "command"


async def test_full_access_never_leaves_a_step_spinning(tmp_path, monkeypatch):
    evs, _s, _w, _log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_full_access")
    rows = [e for e in evs if e["type"] == "tool"]
    assert rows[-1]["status"] == "done" and rows[-1]["output"] == A.UNREPORTED_TOOL, \
        "measured: in Full access its command row got no end — drawn ended, its result said not reported"


async def test_a_failed_reply_is_a_failure_naming_antigravity_with_its_own_logs_reason(tmp_path, monkeypatch):
    def plant_log(app, sid):
        d = Path(app.state.platform.config.home) / "hosted" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / A.LOG_NAME).write_bytes((FIX / "a_fail_agy-acp.log").read_bytes())

    evs, _s, _w, _log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_failed_reply", before=plant_log)
    err = next(e for e in evs if e["type"] == "error")
    assert err["title"] == "Antigravity's sign-in was refused"
    assert "API key not valid" in err["technical"] and "missing project file" not in err["technical"]
    assert not [e for e in evs if e["type"] == "text"], "the ⚠ line is never the reply's text"
    assert evs[-1]["stop"] == "failed"


async def test_with_no_log_the_failure_still_names_antigravity(tmp_path, monkeypatch):
    evs, _s, _w, _log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_failed_reply")
    err = next(e for e in evs if e["type"] == "error")
    assert err["title"] == "Antigravity's model answered with an error"
    assert "Agent execution terminated" in err["technical"] and evs[-1]["stop"] == "failed"


async def test_a_retry_is_a_notice_never_reply_text(tmp_path, monkeypatch):
    evs, _s, _w, _log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_retries_the_model")
    notices = [e["text"] for e in evs if e["type"] == "notice"]
    assert any(n.startswith("Antigravity is trying the model again (attempt 1)") and "503" in n for n in notices)
    assert [e["text"] for e in evs if e["type"] == "text_done"] == ["ok after retry"]
    assert evs[-1]["stop"] == "completed"


def test_the_log_reason_is_its_last_error_about_the_model_call(tmp_path):
    assert "API key not valid" in A.log_reason(FIX / "a_fail_agy-acp.log")
    assert A.log_reason(tmp_path / "missing.log") == ""
    (tmp_path / "noise.log").write_text("E1106 10:50:32.990890 230 projects.go:204] missing project file\n",
                                        encoding="utf-8")
    assert A.log_reason(tmp_path / "noise.log") == ""


async def test_not_signed_in_says_so_and_how_and_ends_the_reply_failed(tmp_path, monkeypatch):
    evs, _s, _w, log, _v, _a = await run_agy(tmp_path, monkeypatch, "antigravity_not_signed_in")
    err = next(e for e in evs if e["type"] == "error")
    assert W.ANTIGRAVITY_SIGN_IN in err["title"] and "run agy" in err["title"]
    assert evs[-1]["stop"] == "failed", "the reason is said, never 'Stopped: the coding tool was closed'"
    assert W.STOPPED_PLAIN not in [e.get("text") for e in evs]
    assert not any(m.get("method") == "authenticate" for m in sent(log))


# --------------------------------------------------------------------------- #
# Saved conversations, terminal, the row
# --------------------------------------------------------------------------- #
_SCHEMA = (
    "CREATE TABLE `conversation_summaries` (`conversation_id` text,`title` text NOT NULL DEFAULT \"\","
    "`preview` text NOT NULL DEFAULT \"\",`step_count` integer NOT NULL DEFAULT 0,"
    "`last_modified_time` datetime NOT NULL,`workspace_uris` text NOT NULL,`status` text NOT NULL DEFAULT \"\","
    "`source` text NOT NULL DEFAULT \"\",`project_id` text NOT NULL DEFAULT \"\",`agent_name` text NOT NULL DEFAULT \"\","
    "`parent_conversation_id` text NOT NULL DEFAULT \"\",`nesting_depth` integer NOT NULL DEFAULT 0,"
    "`battle_id` text NOT NULL DEFAULT \"\",`winning_conversation_id` text NOT NULL DEFAULT \"\","
    "`not_fully_idle` numeric NOT NULL DEFAULT false,`killed` numeric NOT NULL DEFAULT false,"
    "`last_user_input_time` datetime NOT NULL,`last_user_input_step_index` integer NOT NULL DEFAULT -1,"
    "`app_data_dir` text NOT NULL DEFAULT \"\",`raw_summary` blob,`group_id` text NOT NULL DEFAULT \"\","
    "PRIMARY KEY (`conversation_id`))"
)


def _db(home: Path, rows: list[tuple]) -> Path:
    """Antigravity's summaries table exactly as agy 1.5.2 made it (measured)."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / "conversation_summaries.db"
    con = sqlite3.connect(path)
    con.execute(_SCHEMA)
    for cid, title, folder, depth, stamp in rows:
        uri = "file:///" + str(folder).replace("\\", "/").replace(" ", "%20")
        con.execute("INSERT INTO conversation_summaries (conversation_id, title, preview, last_modified_time, "
                    "workspace_uris, nesting_depth, last_user_input_time) VALUES (?,?,?,?,?,?,?)",
                    (cid, title, title, stamp, json.dumps([uri]), depth, stamp))
    con.commit()
    con.close()
    return path


def test_saved_conversations_are_read_only_top_level_and_matched_by_folder(tmp_path, monkeypatch):
    home = tmp_path / "agyhome"
    work = tmp_path / "my work"
    work.mkdir()
    db = _db(home, [
        ("11111111-1111-4111-8111-111111111111", "Fix the build", work, 0, "2026-11-06 15:52:07.5440542+00:00"),
        ("22222222-2222-4222-8222-222222222222", "a helper's", work, 1, "2026-11-06 15:53:07.5+00:00"),
        ("33333333-3333-4333-8333-333333333333", "elsewhere", tmp_path / "other", 0, "2026-11-06 15:54:07+00:00"),
        ("44444444-4444-4444-8444-444444444444", "newer", work, 0, "2026-11-06 16:00:00+00:00"),
    ])
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    monkeypatch.setenv(A.HOME_ENV, str(home))
    got = S.disk_sessions("antigravity", S.default_home("antigravity"), str(work))
    assert [g["id"] for g in got] == ["44444444-4444-4444-8444-444444444444", "11111111-1111-4111-8111-111111111111"]
    assert got[1]["title"] == "Fix the build" and got[1]["harness"] == "antigravity"
    since = S.disk_sessions("antigravity", str(home), str(work), since=got[0]["mtime"] - 1)
    assert [g["id"] for g in since] == ["44444444-4444-4444-8444-444444444444"]
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before, "Antigravity's own database is never written"
    assert A.disk_sessions(tmp_path / "nothing", str(work)) == []


async def test_a_saved_conversation_reopens_with_resume_and_its_title(tmp_path, monkeypatch):
    home = tmp_path / "agyhome"
    work = tmp_path / "work"
    work.mkdir()
    _db(home, [(RESUMED, "Probe title", work, 0, "2026-11-06 15:55:34+00:00")])
    monkeypatch.setenv(A.HOME_ENV, str(home))
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "a_resume", log)
    app = create_app(str(tmp_path / "ijhome"))
    async with client(app) as c:
        s = await new_session(c, work, harness="antigravity", harness_session_id=RESUMED)
        assert s["title"] == "Probe title"
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "again", "turn_id": "t"}), 30)).text)
    await stop_all(app)
    msgs = sent(log)
    assert msgs[1]["method"] == "session/resume" and msgs[1]["params"]["sessionId"] == RESUMED, \
        "reopened WITHOUT replaying the conversation"
    assert [e["text"] for e in evs if e["type"] == "text_done"] == ["resumed ok"]


async def test_open_in_terminal_types_antigravitys_own_conversation_line(tmp_path, monkeypatch):
    typed = {}

    def launch(platform, body, *, accounts=None, command="", **kw):
        typed.update(command=command, cwd=body.cwd, cli=body.cli)
        return SimpleNamespace(id="pane-1")

    import iron_jarvis.daemon.routes.terminals as T

    monkeypatch.setattr(T, "launch_pane", launch)
    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "w"
    work.mkdir()
    row = store.create(app.state.platform.engine, harness="antigravity", folder=str(work), title="t", account="",
                       account_json="", model="", effort="", mode="own", harness_session_id=RESUMED)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/open-terminal")
        assert r.status_code == 200, r.text
    assert typed == {"command": f"agy --conversation {RESUMED}", "cwd": str(work), "cli": "agy"}
    assert S.pane_harness(SimpleNamespace(agent_cli="agy")) == "antigravity"


async def test_the_row_signs_in_by_itself_lists_only_when_installed_and_has_its_own_modes(tmp_path, monkeypatch):
    from iron_jarvis.core import procs

    app = create_app(str(tmp_path / "home"))
    spawned = []
    monkeypatch.setattr(procs, "spawn", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(S, "binary_for", lambda h: ["x"] if h == "antigravity" else None)
    async with client(app) as c:
        row = next(r for r in (await c.get("/build/app/harnesses")).json()["harnesses"] if r["id"] == "antigravity")
        work = tmp_path / "w"
        work.mkdir()
        r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(work), "account": "a1"})
        assert r.status_code == 400 and r.json()["detail"] == W.ANTIGRAVITY_OWN_SIGN_IN
        for mode in ("ask", "auto"):
            r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(work), "mode": mode})
            assert r.status_code == 400 and "not offered for Antigravity" in r.json()["detail"], r.text
        r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(work), "mode": "full"})
        assert r.status_code == 400 and "twice" in r.json()["detail"]
        r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(work)})
        assert r.status_code == 201 and r.json()["mode"] == "own", "a new session starts on Antigravity's own rules"
    await stop_all(app)
    assert row["label"] == "Antigravity" and row["installed"] is True and row["only_when_installed"] is True
    assert row["own_sign_in"] is True and row["signed_in"] is None and row["accounts"] == []
    assert row["sign_in_hint"] == W.SIGN_IN_HINTS["antigravity"]
    assert [m["id"] for m in row["modes"]] == ["own", "edits", "plan", "full"]
    assert row["modes"][0]["label"] == "Antigravity's own rules"
    assert [m["id"] for m in row["modes"] if m["dangerous"]] == ["full"] and all(m["about"] for m in row["modes"])
    assert {h["id"] for h in row["hidden_modes"]} == {"ask", "auto"} and all(h["reason"] for h in row["hidden_modes"])
    assert "strict" in next(h["reason"] for h in row["hidden_modes"] if h["id"] == "ask")
    assert spawned == [], "a status read started a process"


def test_antigravity_is_no_iron_jarvis_provider_and_no_iron_proxy_vendor():
    from iron_jarvis.providers import subscription_clis as SUBS

    assert "antigravity" not in W.PROXY and "antigravity" not in W.PROVIDERS
    assert "antigravity" not in SUBS.HARNESS_TO_PROVIDER and "agy" not in SUBS.SIGN_IN_BINARIES.values()
    assert "antigravity" in S.OWN_SIGN_IN and "antigravity" in S.ONLY_WHEN_INSTALLED


def test_the_program_is_found_where_googles_installer_puts_it(tmp_path, monkeypatch):
    from iron_jarvis.terminals import ai_clis

    local = tmp_path / "local"
    (local / "agy" / "bin").mkdir(parents=True)
    exe = local / "agy" / "bin" / "agy.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(ai_clis.shutil, "which", lambda name: None)
    if os.name == "nt":
        assert ai_clis._find("agy") == str(exe)
    assert ai_clis.launch_entry("agy")["command"] == "agy"


def test_a_title_is_kept_only_for_an_untitled_session():
    titled: list[str] = []
    d = A.AntigravityDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                            mode="own")
    d.on_title = titled.append
    d.on_message({"jsonrpc": "2.0", "method": "session/update",
                  "params": {"update": {"sessionUpdate": "session_info_update", "title": "Probe title"}}})
    assert titled == ["Probe title"]
