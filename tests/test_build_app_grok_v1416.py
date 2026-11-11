"""v1.416.0 — Grok in Build's Code view: the user's REAL Grok (``grok agent
--no-leader stdio``, the Agent Client Protocol over stdio), with its own
tools, MCP servers, skills, hooks and config ON; Iron Jarvis only hosts it and
turns every permission question Grok asks into a card.

Every turn replays a RECORDED grok 1.0.70 session
(``tests/fixtures/build_app_v1416``: live runs through the real Code-view
service via a logging proxy, masked) through the fake harness process
(``tests/fixtures/fake_harness_v1409.py``) started by the real ``core.procs``
plumbing; the driver, routes and store are the real ones. Nothing here runs a
real Grok or calls a model.

THE CONTRACT: ``dashboard/__tests__/fixtures/build-app-events-v1416.json`` is
written FROM THE REAL DAEMON fed those recordings; the dashboard decodes the
same file. Regenerate after an intended change with
``IJ_REGEN_BUILD_APP_GROK=1``. No new event kind.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import grok as G
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask, AskRefused
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all, until

async def _one_event_at_a_time(_q, first):
    """v1.472.0: what a live viewer got before pieces were merged."""
    return [first], False


FIX = Path(__file__).parent / "fixtures" / "build_app_v1416"
GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-events-v1416.json"
MODEL = "grok-4.5"
EFFORT = "low"
HID = "01a19366-8cea-7361-97d2-84add5db6831"  # g_read / g_resume's own session id

#: scenario -> (fixture, mode, model, what the user does, files)
SCENARIOS: dict[str, tuple[str, str, str, list, bool]] = {
    "grok_reads_a_file": ("g_read", "ask", MODEL, [], False),
    "grok_asks_before_an_edit": ("g_edit_allow", "ask", MODEL, [("answer", "allow")], False),
    "grok_edits_allowed_for_the_session": ("g_edit_session", "ask", MODEL, [("answer", "allow_session")], False),
    "grok_command_denied": ("g_cmd_deny", "ask", MODEL, [("answer", "deny")], False),
    "grok_command_allowed_once": ("g_cmd_allow", "ask", MODEL, [("answer", "allow")], False),
    "grok_stop": ("g_cancel", "full", MODEL, [("interrupt",)], False),
    "grok_note_while_it_works": ("g_steer", "full", MODEL, [("steer", "Also end your reply with the word BANANA.")],
                                 False),
    "grok_auto": ("g_auto", "auto", MODEL, [], False),
    "grok_reads_a_picture": ("g_picture", "ask", "grok-4.7", [], True),
}


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def use_fake_grok(monkeypatch, scenario: str, log: Path) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / f"{scenario}.json"))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


def canonical(events: list[dict], work: Path) -> list[dict]:
    names: dict[str, str] = {}
    items: dict[str, str] = {}
    out = []
    for ev in events:
        ev = json.loads(json.dumps(ev).replace(json.dumps(str(work))[1:-1], r"C:\\work\\demo"))
        ev = {k: v for k, v in ev.items() if k != "at"}
        for key, table, prefix in (("request_id", names, "ask"), ("item", items, "item")):
            if ev.get(key):
                table.setdefault(ev[key], f"{prefix}-{len(table) + 1}")
                ev[key] = table[ev[key]]
        if ev.get("type") == "steer":
            ev["id"] = "note-1"
        if "turn" in ev:
            ev["turn"] = "turn-1"
        out.append(ev)
    return out


async def run_grok(tmp_path: Path, monkeypatch, name: str):
    fixture, mode, model, actions, picture = SCENARIOS[name]
    base = tmp_path / name
    work = base / "work"
    work.mkdir(parents=True)
    (work / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    log = base / "fake.log"
    use_fake_grok(monkeypatch, fixture, log)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    files = []
    if picture:
        (work / "word.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        files = [str(work / "word.png")]
    async with client(app) as c:
        body = {"mode": mode, "model": model, "effort": EFFORT}
        if mode == "full":
            body["confirm"] = True
        sid = (await new_session(c, work, harness="grok", **body))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn",
                                            json={"text": "do it", "turn_id": "t", "files": files}))
        for act in actions:
            if act[0] == "answer":
                ask = await until(lambda: next(iter(svc.live[sid].asks), None) if sid in svc.live else None)
                r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": act[1]})
                assert r.status_code == 200, r.text
            else:
                await until(lambda: svc.turn("t") and any(
                    e["type"] == "tool" and e.get("kind") == "command" for e in svc.turn("t").events))
                if act[0] == "interrupt":
                    r = await c.post(f"/build/app/sessions/{sid}/interrupt")
                else:
                    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": act[1]})
                assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
        live = svc.live.get(sid)
        still_running = bool(live and live.running)
    await stop_all(app)
    return evs, sid, work, log, still_running


# --------------------------------------------------------------------------- #
# THE CONTRACT
# --------------------------------------------------------------------------- #
async def test_the_golden_file_is_what_the_real_daemon_sends_for_grok(tmp_path, monkeypatch):
    # v1.472.0: the golden records each event as the tool sent it; a live
    # viewer's merging (timed, tests/test_build_app_live_merge_v1472.py) is
    # turned off here so the recording does not depend on timing.
    monkeypatch.setattr(S, "_gather_live", _one_event_at_a_time)
    got = {}
    for name in SCENARIOS:
        evs, _sid, work, _log, _r = await run_grok(tmp_path, monkeypatch, name)
        got[name] = canonical(evs, work)
    doc = {
        "_about": (
            "v1.416.0 Build Code view, Grok: the normalized events the REAL daemon sends for each scenario, "
            "produced by tests/test_build_app_grok_v1416.py from recorded Grok output "
            "(tests/fixtures/build_app_v1416, masked live runs of grok 1.0.70 `grok agent --no-leader stdio` on "
            "this PC). `at` is dropped; request ids are ask-N; item ids are item-N; every turn id is turn-1; a "
            "note's id is note-1. Regenerate with IJ_REGEN_BUILD_APP_GROK=1."
        ),
        "scenarios": got,
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("IJ_REGEN_BUILD_APP_GROK") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(text.encode("utf-8"))
    on_disk = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert on_disk["scenarios"] == got, "the golden file is stale — IJ_REGEN_BUILD_APP_GROK=1 after an intended change"


def test_every_grok_scenario_ends_once_and_uses_only_contract_types():
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(doc["scenarios"]) == set(SCENARIOS)
    for name, evs in doc["scenarios"].items():
        assert evs[0]["type"] == "user", name
        assert [e["type"] for e in evs].count("done") == 1 and evs[-1]["type"] == "done", name
        assert {e["type"] for e in evs} <= set(E.TYPES), name
        for e in evs:
            if e["type"] == "tool":
                assert e["kind"] in E.TOOL_KINDS, name
            if e["type"] == "approval":
                assert e["kind"] in E.APPROVAL_KINDS, name


def test_the_recordings_are_masked_and_carry_none_of_groks_own_notifications():
    for path in FIX.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for leak in ("\\Users\\VR", "/Users/VR", "100.66.", "spark", "conductest", "smithery", "api_key=",
                     "scratchpad", "hostname", "agentId", "signature"):
            assert leak not in text, (path.name, leak)
        doc = json.loads(text)
        assert doc["harness"] == "grok" and "grok 1.0.70" in doc["source"], path.name
        for step in doc["steps"]:
            method = (step.get("out") or {}).get("method") or ""
            assert not method.startswith("_x.ai/") or method == "_x.ai/session/interjection", (path.name, method)


# --------------------------------------------------------------------------- #
# What reaches Grok
# --------------------------------------------------------------------------- #
async def test_grok_starts_its_own_agent_over_stdio_never_a_shared_leader(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_grok(monkeypatch, "g_read", log)
    monkeypatch.setenv("XAI_API_KEY", "xai-users-own-key-0000")
    async with client(app) as c:
        s = await new_session(c, work, harness="grok", model=MODEL, effort=EFFORT)
        assert s["mode"] == "ask", "a Grok session starts in Ask first"
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "do it", "turn_id": "t"}), 30)).text)
        row = await until(lambda: store.get(app.state.platform.engine, s["id"]).harness_session_id and
                          store.get(app.state.platform.engine, s["id"]))
    await stop_all(app)
    start = starts(log)[0]
    assert start["argv"] == ["agent", "--no-leader", "stdio"]
    assert not {"--always-approve", "--yolo", "--leader"} & set(start["argv"])
    assert start["cwd"] == str(work)
    msgs = sent(log)
    assert [m.get("method") for m in msgs[:5]] == ["initialize", "session/new", "session/set_model",
                                                   "session/set_config_option", "session/prompt"]
    assert msgs[0]["params"]["clientCapabilities"] == {"fs": {"readTextFile": False, "writeTextFile": False},
                                                       "terminal": False}
    assert msgs[1]["params"] == {"cwd": str(work), "mcpServers": [],
                                 "_meta": {"yoloMode": False, "autoMode": False}}, \
        "Ask first is stated explicitly: the user's own config default (always-approve here) never decides"
    assert msgs[2]["params"]["modelId"] == MODEL
    assert msgs[3]["params"] == {"sessionId": row.harness_session_id, "configId": "reasoning_effort", "value": EFFORT}
    # v1.418.0: master's v1.417.0 puts a start notice before the session event.
    sess = next(e for e in evs if e["type"] == "session")
    assert sess["harness"] == "grok" and sess["model"] == MODEL
    tool = next(e for e in evs if e["type"] == "tool" and e["status"] == "done")
    assert tool["kind"] == "read" and tool["name"] == "read_file" and tool["title"] == "Read hello.txt"
    usage = next(e for e in evs if e["type"] == "usage")
    assert usage["cost_reported_by"] == "Grok" and usage["list_price"] is False
    assert usage["cost_usd"] == round(156028720 / G.USD_TICKS, 6) == 0.015603, "Grok's own USD ticks, 10^10 to the dollar"
    assert (usage["input"], usage["output"], usage["cache_read"]) == (39892, 45, 20096)
    assert usage["context"] == {"used": 20340, "window": 256000}
    assert usage["context"]["window"] == 256000


def test_each_mode_is_groks_own_session_posture():
    assert G.session_meta("ask") == {"yoloMode": False, "autoMode": False}
    assert G.session_meta("auto") == {"yoloMode": False, "autoMode": True}
    assert G.session_meta("full") == {"yoloMode": True, "autoMode": False}
    assert G.session_meta("anything-else") == {"yoloMode": False, "autoMode": False}


async def test_auto_and_full_reach_grok_as_its_own_meta(tmp_path, monkeypatch):
    _e, _s, _w, log, _r = await run_grok(tmp_path, monkeypatch, "grok_auto")
    new = next(m for m in sent(log) if m.get("method") == "session/new")
    assert new["params"]["_meta"] == {"yoloMode": False, "autoMode": True}
    _e, _s, _w, log, _r = await run_grok(tmp_path, monkeypatch, "grok_stop")
    new = next(m for m in sent(log) if m.get("method") == "session/new")
    assert new["params"]["_meta"] == {"yoloMode": True, "autoMode": False}


async def test_an_edit_card_shows_its_diff_and_allow_once_is_groks_own_option(tmp_path, monkeypatch):
    evs, _sid, _work, log, _r = await run_grok(tmp_path, monkeypatch, "grok_asks_before_an_edit")
    card = next(e for e in evs if e["type"] == "approval")
    assert card["kind"] == "edit" and card["diff"] and card["path"].endswith("notes.md")
    assert card["can_session"] is True and "once_only" not in card
    picked = [m["result"]["outcome"].get("optionId") for m in sent(log)
              if isinstance(m.get("result"), dict) and "outcome" in m["result"]]
    assert picked == ["allow-once"]
    assert evs[-1]["stop"] == "completed"


async def test_allow_for_this_session_is_only_groks_in_memory_edit_grant(tmp_path, monkeypatch):
    evs, _sid, _work, log, _r = await run_grok(tmp_path, monkeypatch, "grok_edits_allowed_for_the_session")
    picked = [m["result"]["outcome"].get("optionId") for m in sent(log)
              if isinstance(m.get("result"), dict) and "outcome" in m["result"]]
    assert picked == ["allow-edits-session"]
    assert [e["type"] for e in evs].count("approval") == 1, "Grok's own session grant covered the second write"


async def test_a_command_card_is_allow_once_only_and_a_deny_ends_the_reply_in_words(tmp_path, monkeypatch):
    evs, _sid, _work, log, _r = await run_grok(tmp_path, monkeypatch, "grok_command_allowed_once")
    card = next(e for e in evs if e["type"] == "approval")
    assert card["kind"] == "command" and card["command"] == "python --version"
    assert card["once_only"] is True and card["can_session"] is False, \
        "Grok SAVES its command 'always' for the project: never offered as Allow for this session"
    evs, _sid, _work, log, _r = await run_grok(tmp_path, monkeypatch, "grok_command_denied")
    picked = [m["result"]["outcome"].get("optionId") for m in sent(log)
              if isinstance(m.get("result"), dict) and "outcome" in m["result"]]
    assert picked == ["reject-once"]
    assert W.GROK_DECLINED_ENDS in [e.get("text") for e in evs if e["type"] == "notice"]
    assert evs[-1]["stop"] == "completed", "a Deny is not the user's Stop"


def _ask(options: list[tuple[str, str]]) -> Ask:
    return Ask("ask_1", "approval", "tool", 7, {"options": [{"optionId": i, "kind": k} for i, k in options]}, {})


def test_the_driver_never_picks_an_option_grok_saves():
    d = G.GrokDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    command = _ask([("always-allow", "allow_always"), ("allow-once", "allow_once"),
                    ("reject-once", "reject_once"), ("reject-always", "reject_always")])
    with pytest.raises(AskRefused):
        d.prepare_answer(command, "allow_session", "", None)
    assert d.prepare_answer(command, "deny", "", None)["result"]["outcome"]["optionId"] == "reject-once"
    assert d.prepare_answer(command, "allow", "", None)["result"]["outcome"]["optionId"] == "allow-once"
    only_saved_deny = _ask([("allow-once", "allow_once"), ("reject-always", "reject_always")])
    assert d.prepare_answer(only_saved_deny, "deny", "", None)["result"]["outcome"] == {"outcome": "cancelled"}
    edit = _ask([("allow-edits-session", "allow_always"), ("allow-once", "allow_once"), ("reject-once", "reject_once")])
    assert d.prepare_answer(edit, "allow_session", "", None)["result"]["outcome"]["optionId"] == "allow-edits-session"
    assert d.prepare_answer(edit, "allow_session", "", None)["id"] == 7


async def test_stop_is_groks_cancel_and_a_command_left_running_closes_grok(tmp_path, monkeypatch):
    from iron_jarvis.hosted import process as PR

    order: list[str] = []
    note, interrupt = PR.HarnessProcess.note_started, G.GrokDriver.interrupt

    def noted(self):
        order.append("noted")
        return note(self)

    async def told(self):
        order.append("cancel")
        return await interrupt(self)

    monkeypatch.setattr(PR.HarnessProcess, "note_started", noted)
    monkeypatch.setattr(G.GrokDriver, "interrupt", told)
    evs, _s, _w, log, still_running = await run_grok(tmp_path, monkeypatch, "grok_stop")
    assert order == ["noted", "cancel"], "what Grok started is read BEFORE it is told to stop"
    assert any(m.get("method") == "session/cancel" and "id" not in m for m in sent(log))
    assert evs[-1]["stop"] == "interrupted"
    assert W.GROK_STOP_CLOSED in [e.get("text") for e in evs if e["type"] == "notice"]
    assert still_running is False, "Grok's cancel leaves its command running: the process (and its Job) goes"
    rows = [e for e in evs if e["type"] == "tool"]
    assert rows[-1]["status"] == "failed" and rows[-1]["output"] == G.STOPPED_TOOL and rows[-1]["kind"] == "command"


async def test_a_note_while_it_works_is_groks_own_interjection_and_is_read(tmp_path, monkeypatch):
    evs, _s, _w, log, _r = await run_grok(tmp_path, monkeypatch, "grok_note_while_it_works")
    notes = [m for m in sent(log) if m.get("method") == "_x.ai/interject"]
    assert [n["params"]["text"] for n in notes] == ["Also end your reply with the word BANANA."]
    assert len([m for m in sent(log) if m.get("method") == "session/prompt"]) == 1, \
        "a second prompt would only be queued as a turn of its own"
    assert [e["state"] for e in evs if e["type"] == "steer"] == ["sent", "read"]
    assert "BANANA" in next(e["text"] for e in evs if e["type"] == "text_done" and "BANANA" in e["text"])
    assert evs[-1]["stop"] == "completed"


async def test_a_picture_goes_as_an_acp_image_block(tmp_path, monkeypatch):
    evs, _s, _w, log, _r = await run_grok(tmp_path, monkeypatch, "grok_reads_a_picture")
    prompt = next(m for m in sent(log) if m.get("method") == "session/prompt")["params"]["prompt"]
    assert prompt[0]["type"] == "text" and prompt[1]["type"] == "image" and prompt[1]["mimeType"] == "image/png"
    assert [e["text"] for e in evs if e["type"] == "text_done"] == ["ZEBRA"]


async def test_a_second_process_resumes_the_same_conversation_and_draws_no_replay(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_grok(monkeypatch, "g_read", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="grok", model=MODEL, effort=EFFORT)
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t1"}), 30)
        svc = S.service_for(app.state.platform)
        hid = await until(lambda: store.get(app.state.platform.engine, s["id"]).harness_session_id)
        assert hid == HID
        await svc._stop_process(svc.live[s["id"]])
        monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / "g_resume.json"))
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "y", "turn_id": "t2"}), 30)).text)
    await stop_all(app)
    second = sent(log)[5:]
    resumes = [m for m in second if m.get("method") == "session/resume"]
    assert len(resumes) == 1 and resumes[0]["params"]["sessionId"] == hid
    assert resumes[0]["params"]["_meta"] == {"yoloMode": False, "autoMode": False}, \
        "the posture is stated again on a resume"
    assert not [m for m in second if m.get("method") in ("session/new", "session/load", "session/set_model")]
    assert [e["text"] for e in evs if e["type"] == "text_done"] == ["apple"]
    assert evs[-1]["stop"] == "completed"


async def test_a_mode_change_reaches_a_new_process_before_the_next_message(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_grok(monkeypatch, "g_read", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="grok", model=MODEL, effort=EFFORT)
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t1"}), 30)
        await until(lambda: store.get(app.state.platform.engine, s["id"]).harness_session_id)
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "auto"})
        assert r.status_code == 200 and r.json()["applied"]["mode"] == "next", r.text
        svc = S.service_for(app.state.platform)
        assert svc.live[s["id"]].restart is True
        monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / "g_resume.json"))
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "y", "turn_id": "t2"}), 30)).text)
    await stop_all(app)
    assert evs[-1]["stop"] == "completed", evs
    assert len(starts(log)) == 2
    resume = next(m for m in sent(log) if m.get("method") == "session/resume")
    assert resume["params"]["_meta"] == {"yoloMode": False, "autoMode": True}


async def test_an_iron_proxy_account_is_its_grok_home(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_grok(monkeypatch, "g_read", log)
    acct = tmp_path / "grok-acct"
    acct.mkdir()
    row = store.create(app.state.platform.engine, harness="grok", folder=str(work), title="t", account="p-grok",
                       account_json=json.dumps({"id": "p-grok", "title": "Work", "home": str(acct)}),
                       model=MODEL, effort=EFFORT, mode="ask", harness_session_id="")
    async with client(app) as c:
        await asyncio.wait_for(c.post(f"/build/app/sessions/{row.id}/turn", json={"text": "x", "turn_id": "t"}), 30)
    await stop_all(app)
    assert starts(log)[0]["env"].get("GROK_HOME") == str(acct)


# --------------------------------------------------------------------------- #
# Its own saved sessions
# --------------------------------------------------------------------------- #
def _session(home: Path, folder: Path, sid: str, title: str, n: int = 3) -> Path:
    d = home / "sessions" / "C%3A%5Cencoded" / sid
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({
        "info": {"id": sid, "cwd": str(folder)}, "generated_title": title, "num_messages": n,
        "created_at": "2026-10-31T18:54:59Z", "updated_at": "2026-10-31T18:55:23Z",
    }), encoding="utf-8")
    return d / "summary.json"


def test_the_disk_listing_reads_groks_own_summaries_read_only(tmp_path, monkeypatch):
    home = tmp_path / "grokhome"
    work = tmp_path / "w"
    work.mkdir()
    a = _session(home, work, "01a19350-a988-7100-9f05-bdf7a1268fbd", "Create hello.txt")
    _session(home, tmp_path / "other", "01a19350-a988-7100-9f05-bdf7a1268fbe", "elsewhere")
    _session(home, work, "01a19350-a988-7100-9f05-bdf7a1268fbf", "empty", n=0)
    before = (a.read_bytes(), a.stat().st_mtime_ns)
    monkeypatch.setenv(G.HOME_ENV, str(home))
    got = S.disk_sessions("grok", S.default_home("grok"), str(work))
    assert sorted(g["id"] for g in got) == ["01a19350-a988-7100-9f05-bdf7a1268fbd",
                                            "01a19350-a988-7100-9f05-bdf7a1268fbf"]
    assert {g["title"] for g in got} == {"Create hello.txt", "empty"}
    assert G.has_messages(str(home), "01a19350-a988-7100-9f05-bdf7a1268fbd") is True
    assert G.has_messages(str(home), "01a19350-a988-7100-9f05-bdf7a1268fbf") is False
    assert (a.read_bytes(), a.stat().st_mtime_ns) == before, "Grok's own files are never written"


async def test_back_to_the_default_model_is_refused_once_the_conversation_has_messages(app, work, tmp_path, monkeypatch):
    home = tmp_path / "grokhome"
    _session(home, work, HID, "t")
    monkeypatch.setenv(G.HOME_ENV, str(home))
    row = store.create(app.state.platform.engine, harness="grok", folder=str(work), title="t", account="",
                       account_json="", model=MODEL, effort="", mode="ask", harness_session_id=HID)
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert view["keeps_last"] is True
        r = await c.patch(f"/build/app/sessions/{row.id}", json={"model": ""})
        assert r.status_code == 409 and r.json()["detail"] == W.GROK_KEEPS_LAST


async def test_open_in_terminal_types_groks_own_resume_line(app, work, monkeypatch):
    typed = {}

    def launch(platform, body, *, accounts=None, command="", **kw):
        typed.update(command=command, cwd=body.cwd, cli=body.cli)
        return SimpleNamespace(id="pane-1")

    import iron_jarvis.daemon.routes.terminals as T

    monkeypatch.setattr(T, "launch_pane", launch)
    row = store.create(app.state.platform.engine, harness="grok", folder=str(work), title="t", account="",
                       account_json="", model="", effort="", mode="ask", harness_session_id=HID)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/open-terminal")
        assert r.status_code == 200, r.text
        assert store.get(app.state.platform.engine, row.id).terminal_id == "pane-1"
    assert typed == {"command": f"grok --resume {HID}", "cwd": str(work), "cli": "grok"}
    assert S.typable_sid("grok", HID) and not S.typable_sid("grok", "x & calc")
    assert S.pane_harness(SimpleNamespace(agent_cli="grok")) == "grok"


# --------------------------------------------------------------------------- #
# The row, the program, the words
# --------------------------------------------------------------------------- #
async def test_groks_row_has_its_own_modes_and_reads_its_sign_in_without_a_process(app, tmp_path, monkeypatch):
    from iron_jarvis.core import procs

    home = tmp_path / "grokhome"
    home.mkdir()
    monkeypatch.setenv(G.HOME_ENV, str(home))
    spawned = []
    monkeypatch.setattr(procs, "spawn", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(S, "binary_for", lambda h: ["x"] if h == "grok" else None)
    async with client(app) as c:
        row = next(r for r in (await c.get("/build/app/harnesses")).json()["harnesses"] if r["id"] == "grok")
        assert row["signed_in"] is False, "no auth.json in Grok's home"
        (home / "auth.json").write_text(json.dumps({"https://cli-chat-proxy.grok.com": {"key": "k-secret"}}),
                                        encoding="utf-8")
        again = next(r for r in (await c.get("/build/app/harnesses")).json()["harnesses"] if r["id"] == "grok")
    assert row["label"] == "Grok" and row["installed"] is True and row["only_when_installed"] is True
    assert [m["id"] for m in row["modes"]] == ["ask", "auto", "full"]
    assert [m["id"] for m in row["modes"] if m["dangerous"]] == ["full"] and all(m["about"] for m in row["modes"])
    assert {h["id"] for h in row["hidden_modes"]} == {"edits", "plan"} and all(h["reason"] for h in row["hidden_modes"])
    assert row["own_sign_in"] is False and row["efforts"] == ["low", "medium", "high", "xhigh"]
    assert spawned == [], "a status read started a process"
    assert "k-secret" not in json.dumps(again)
    assert again["signed_in"] in (True, None)


async def test_create_refuses_a_mode_grok_cannot_honour_and_full_needs_two_presses(app, work):
    async with client(app) as c:
        for mode in ("edits", "plan"):
            r = await c.post("/build/app/sessions", json={"harness": "grok", "folder": str(work), "mode": mode})
            assert r.status_code == 400 and "not offered for Grok" in r.json()["detail"], r.text
        r = await c.post("/build/app/sessions", json={"harness": "grok", "folder": str(work), "mode": "full"})
        assert r.status_code == 400 and "twice" in r.json()["detail"]
        r = await c.post("/build/app/sessions", json={"harness": "grok", "folder": str(work), "effort": "max"})
        assert r.status_code == 400


def test_groks_own_notifications_are_never_drawn():
    seen = []
    d = G.GrokDriver(write=None, emit=seen.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.turn_open = True
    d.on_message({"jsonrpc": "2.0", "method": "_x.ai/mcp/servers_updated",
                  "params": {"mcpServers": [{"url": "https://x?api_key=SECRET"}]}})
    d.on_message({"jsonrpc": "2.0", "method": "_x.ai/settings/update", "params": {"tips": ["x"]}})
    # v1.441.0 (moved on purpose): the MCP list is read for its NAMES only
    # (``tool_setup.grok_servers``) — a server with no name adds nothing, and
    # its URL never reaches an event.
    assert [e["type"] for e in seen] == ["setup"] and seen[0]["mcp_servers"] == []
    assert "SECRET" not in repr(seen)


def test_a_failure_says_groks_own_fix_and_offers_no_other_tools_press():
    out = G.failure_event("Not logged in. Run grok login.")
    # v1.439.0 (moved on purpose): Grok's OWN Sign in press (provider
    # grok-cli), never another tool's.
    assert out["title"] == "Grok isn't signed in" and "Grok" in out["fix"]
    assert out["fix_kind"] == "sign_in" and out["provider"] == "grok-cli"
    assert G.failure_event("HTTP 429 Too Many Requests")["title"] == "Grok hit its usage limit"
    assert G.failure_event("boom")["title"] == "Grok's model answered with an error"


def test_the_listing_reads_iron_proxys_xai_accounts():
    from iron_jarvis.harness import build_accounts as BA

    snap = ([{"id": "g1", "title": "Work", "provider": "xai", "order": 2},
             {"id": "g0", "title": "Home", "provider": "xai", "order": 1},
             {"id": "g2", "title": "Off", "provider": "xai", "enabled": False},
             {"id": "c1", "title": "Claude", "provider": "anthropic"}],
            {"g1": {"status": "unauthenticated"}})
    assert BA.proxy_account_rows(snap, "xai") == [
        {"id": "g0", "name": "Home", "status": "ready"},
        {"id": "g1", "name": "Work", "status": "needs_sign_in"},
    ]


async def test_continue_on_another_account_is_for_claude_code_only(app, work):
    row = store.create(app.state.platform.engine, harness="grok", folder=str(work), title="t", account="",
                       account_json="", model="", effort="", mode="ask", harness_session_id=HID)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={})
        assert r.status_code == 409 and r.json()["detail"] == W.GROK_CONTINUE


# --------------------------------------------------------------------------- #
# A stop ends what the tool started, even a process outside its Job
# --------------------------------------------------------------------------- #
def _sleeper():
    import subprocess

    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _poll(fn, timeout: float = 15.0):
    import time

    end = time.monotonic() + timeout
    while True:
        got = fn()
        if got or time.monotonic() > end:
            return got
        time.sleep(0.1)


def test_descendants_and_kill_left_end_only_the_same_processes():
    import subprocess

    from iron_jarvis.hosted import process as PR

    parent = subprocess.Popen([sys.executable, "-c",
                               "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', "
                               "'import time; time.sleep(60)']); time.sleep(60)"])
    try:
        got = _poll(lambda: PR.descendants(parent.pid))
        assert got, "the grandchild is read"
        other = _sleeper()
        try:
            # A reused pid (another start time) is never touched.
            assert PR.kill_left([(other.pid, 1.0)]) == []
            assert other.poll() is None
        finally:
            other.kill()
        assert sorted(PR.kill_left(got)) == sorted(pid for pid, _ in got)
    finally:
        parent.kill()


def test_a_stop_ends_a_process_the_tool_started_outside_its_job(tmp_path, monkeypatch):
    """Measured: Grok runs each command as its own `pwsh -Command …` that is
    NOT a member of the Job Iron Jarvis puts Grok in, so a `ping` outlived the
    Job's close. The stop reads what the tool started FIRST and ends it after."""
    import psutil

    from iron_jarvis.hosted import process as PR

    escaped = _sleeper()
    try:
        made = psutil.Process(escaped.pid).create_time()
        monkeypatch.setattr(PR, "descendants", lambda pid: [(escaped.pid, made)])
        hp = PR.HarnessProcess([sys.executable, "-c", "import sys; sys.stdin.read()"], env=dict(os.environ),
                               cwd=str(tmp_path), name="t", ends_left_behind=True)
        loop = asyncio.new_event_loop()
        try:
            hp.start(loop, lambda m: None, lambda c: None)
            hp.stop()
        finally:
            loop.close()
        assert escaped.wait(timeout=10) is not None, "the process outside the Job ended with the stop"
    finally:
        if escaped.poll() is None:
            escaped.kill()
