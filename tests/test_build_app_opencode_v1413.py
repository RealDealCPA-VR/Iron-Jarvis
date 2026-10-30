"""v1.413.0 — OpenCode in Build's Code view: the user's REAL OpenCode
(``opencode acp``, the Agent Client Protocol over stdio), with its own tools,
agents, plugins, MCP servers and config ON; Iron Jarvis only hosts it and turns
every permission question OpenCode asks into a card.

Every turn replays a RECORDED opencode 1.18.18 session
(``tests/fixtures/build_app_v1413``: live runs through the real Code-view
service via a logging proxy, masked) through the fake harness process
(``tests/fixtures/fake_harness_v1409.py``) started by the real ``core.procs``
plumbing; the driver, routes and store are the real ones. Nothing here runs a
real OpenCode or calls a model.

THE CONTRACT: ``dashboard/__tests__/fixtures/build-app-events-v1413.json`` is
written FROM THE REAL DAEMON fed those recordings; the dashboard decodes the
same file. Regenerate after an intended change with
``IJ_REGEN_BUILD_APP_OPENCODE=1``. No new event kind.
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
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import opencode as O
from iron_jarvis.hosted import process as PR
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all, until

FIX_OC = Path(__file__).parent / "fixtures" / "build_app_v1413"
GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-events-v1413.json"
MODEL = "opencode/glm-5.3-flash-free"

#: scenario -> (fixture, mode, model, what the user does, files)
SCENARIOS: dict[str, tuple[str, str, str, list, bool]] = {
    "opencode_read_a_file": ("oc_read", "ask", MODEL, [], False),
    "opencode_asks_before_an_edit": ("oc_edit_allow", "ask", MODEL, [("answer", "allow")], False),
    "opencode_command_denied": ("oc_bash_deny", "ask", MODEL, [("answer", "deny")], False),
    "opencode_command_allowed_for_the_session": ("oc_bash_session", "ask", MODEL, [("answer", "allow_session")], False),
    "opencode_stop": ("oc_cancel", "full", MODEL, [("interrupt",)], False),
    "opencode_note_while_it_works": ("oc_steer", "full", MODEL, [("steer", "Also: end your reply with the word BANANA.")], False),
    "opencode_model_answers_an_error": ("oc_error", "ask", "lab/fleet", [], False),
    "opencode_reads_a_picture": ("oc_picture", "ask", MODEL, [], True),
    "opencode_plan_mode": ("oc_plan", "plan", MODEL, [], False),
}

#: Flags that would switch something of the user's own OpenCode off.
TOOL_OFF_FLAGS = ("--pure", "--agent", "--model", "-m", "--prompt", "--auto")


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def use_fake_oc(monkeypatch, scenario: str, log: Path) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX_OC / f"{scenario}.json"))
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


async def run_oc(tmp_path: Path, monkeypatch, name: str) -> tuple[list[dict], str, Path, Path]:
    fixture, mode, model, actions, picture = SCENARIOS[name]
    base = tmp_path / name
    work = base / "work"
    work.mkdir(parents=True)
    (work / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    log = base / "fake.log"
    use_fake_oc(monkeypatch, fixture, log)
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    files = []
    if picture:
        (work / "zebrafig.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        files = [str(work / "zebrafig.png")]
    async with client(app) as c:
        body = {"mode": mode, "model": model}
        if mode == "full":
            body["confirm"] = True
        sid = (await new_session(c, work, harness="opencode", **body))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn",
                                            json={"text": "do it", "turn_id": "t", "files": files}))
        for act in actions:
            if act[0] == "answer":
                ask = await until(lambda: next(iter(svc.live[sid].asks), None) if sid in svc.live else None)
                r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": act[1]})
                assert r.status_code == 200, r.text
            elif act[0] in ("interrupt", "steer"):
                await until(lambda: svc.turn("t") and any(
                    e["type"] == "tool" and e.get("kind") == "command" for e in svc.turn("t").events))
                if act[0] == "interrupt":
                    r = await c.post(f"/build/app/sessions/{sid}/interrupt")
                else:
                    r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": act[1]})
                assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    return evs, sid, work, log


# --------------------------------------------------------------------------- #
# THE CONTRACT
# --------------------------------------------------------------------------- #
async def test_the_golden_file_is_what_the_real_daemon_sends_for_opencode(tmp_path, monkeypatch):
    got = {}
    for name in SCENARIOS:
        evs, _sid, work, _log = await run_oc(tmp_path, monkeypatch, name)
        got[name] = canonical(evs, work)
    doc = {
        "_about": (
            "v1.413.0 Build Code view, OpenCode: the normalized events the REAL daemon sends for each scenario, "
            "produced by tests/test_build_app_opencode_v1413.py from recorded OpenCode output "
            "(tests/fixtures/build_app_v1413, masked live runs of opencode 1.18.18 `opencode acp` on this PC). "
            "`at` is dropped; request ids are ask-N; item ids are item-N; every turn id is turn-1; a note's id is "
            "note-1. Regenerate with IJ_REGEN_BUILD_APP_OPENCODE=1."
        ),
        "scenarios": got,
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("IJ_REGEN_BUILD_APP_OPENCODE") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(text.encode("utf-8"))
    on_disk = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert on_disk["scenarios"] == got, "the golden file is stale — IJ_REGEN_BUILD_APP_OPENCODE=1 after an intended change"


def test_every_opencode_scenario_ends_once_and_uses_only_contract_types():
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


def test_the_recordings_are_masked():
    for path in FIX_OC.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for leak in ("\\Users\\VR", "/Users/VR", "100.66.", "spark", "frontier", "1099", "scratchpad", "Lacerte"):
            assert leak not in text, (path.name, leak)
        doc = json.loads(text)
        assert doc["harness"] == "opencode" and "opencode 1.18.18" in doc["source"], path.name


# --------------------------------------------------------------------------- #
# What reaches OpenCode
# --------------------------------------------------------------------------- #
async def test_opencode_starts_its_own_acp_with_nothing_switched_off(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_oc(monkeypatch, "oc_read", log)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-users-own-key-0000")
    async with client(app) as c:
        s = await new_session(c, work, harness="opencode", model=MODEL)
        assert s["mode"] == "ask", "an OpenCode session starts in Ask first"
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "do it", "turn_id": "t"}), 30)).text)
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
    await stop_all(app)
    start = starts(log)[0]
    # (review, moved on purpose: OpenCode's internal server is pinned to loopback.)
    assert start["argv"] == list(O.ACP_ARGV), "the folder is the process's own; nothing of the user's is switched off"
    assert not set(start["argv"]) & set(TOOL_OFF_FLAGS)
    assert start["cwd"] == str(work)
    assert start["env"].get("OPENAI_API_KEY") == "sk-users-own-key-0000", "the user's own keys are a terminal's"
    msgs = sent(log)
    assert [m.get("method") for m in msgs[:4]] == ["initialize", "session/new", "session/set_config_option",
                                                   "session/prompt"]
    caps = msgs[0]["params"]["clientCapabilities"]
    assert caps == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}, \
        "Iron Jarvis is no editor: OpenCode uses its own tools"
    assert msgs[1]["params"] == {"cwd": str(work), "mcpServers": []}, "no MCP server is added (the user's own load)"
    assert msgs[2]["params"]["configId"] == "model" and msgs[2]["params"]["value"] == MODEL
    assert msgs[3]["params"]["prompt"] == [{"type": "text", "text": "do it"}]
    assert row["harness_session_id"].startswith("ses_"), "the row names OpenCode's own saved session"
    assert evs[1]["type"] == "session" and evs[1]["harness"] == "opencode"


async def test_ask_first_is_opencodes_own_permission_rules_for_this_process_only(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_oc(monkeypatch, "oc_read", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="opencode", model=MODEL)
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t"}), 30)
    await stop_all(app)
    perm = json.loads(starts(log)[0]["env"]["OPENCODE_PERMISSION"])
    assert perm["*"] == "ask"
    # (review, moved on purpose: reading a .env file still asks, OpenCode's own default.)
    assert all(perm[t] == "allow" for t in O.READ_ONLY_TOOLS if t != "read") and perm["read"] == O.READ_RULE
    assert "edit" not in perm or perm["edit"] == "ask"


async def test_an_edit_card_shows_its_diff_and_only_the_offered_option_goes_back(tmp_path, monkeypatch):
    evs, _sid, _work, log = await run_oc(tmp_path, monkeypatch, "opencode_asks_before_an_edit")
    card = next(e for e in evs if e["type"] == "approval")
    assert card["kind"] == "edit" and card["diff"] and card["path"].endswith("note.txt")
    assert card["can_session"] is True
    answers = [m for m in sent(log) if "result" in m]
    perm = [m for m in answers if isinstance(m["result"], dict) and "outcome" in m["result"]]
    assert perm == [{"jsonrpc": "2.0", "id": perm[0]["id"],
                     "result": {"outcome": {"outcome": "selected", "optionId": "once"}}}]
    assert evs[-1] == {**evs[-1], "type": "done", "stop": "completed"}


async def test_a_deny_is_opencodes_reject_and_allow_for_the_session_its_always(tmp_path, monkeypatch):
    _evs, _s, _w, log = await run_oc(tmp_path, monkeypatch, "opencode_command_denied")
    picked = [m["result"]["outcome"].get("optionId") for m in sent(log)
              if isinstance(m.get("result"), dict) and "outcome" in m["result"]]
    assert picked == ["reject"]
    evs, _s, _w, log = await run_oc(tmp_path, monkeypatch, "opencode_command_allowed_for_the_session")
    picked = [m["result"]["outcome"].get("optionId") for m in sent(log)
              if isinstance(m.get("result"), dict) and "outcome" in m["result"]]
    assert picked == ["always"], "one card; OpenCode's own always covered the second command"
    assert [e["type"] for e in evs].count("approval") == 1


async def test_stop_is_opencodes_own_cancel_and_the_turn_says_interrupted(tmp_path, monkeypatch):
    evs, _s, _w, log = await run_oc(tmp_path, monkeypatch, "opencode_stop")
    assert any(m.get("method") == "session/cancel" and "id" not in m for m in sent(log))
    assert evs[-1]["stop"] == "interrupted"


async def test_a_note_while_it_works_is_a_second_prompt_and_is_read_never_returned(tmp_path, monkeypatch):
    evs, _s, _w, log = await run_oc(tmp_path, monkeypatch, "opencode_note_while_it_works")
    prompts = [m for m in sent(log) if m.get("method") == "session/prompt"]
    assert len(prompts) == 2
    assert prompts[1]["params"]["prompt"] == [{"type": "text", "text": "Also: end your reply with the word BANANA."}]
    steers = [e["state"] for e in evs if e["type"] == "steer"]
    assert steers == ["sent", "read"], steers
    assert "returned" not in steers
    assert evs[-1]["stop"] == "completed"
    assert "BANANA" in next(e["text"] for e in evs if e["type"] == "text_done")


async def test_a_failure_says_opencodes_own_fix_never_another_tools_press(tmp_path, monkeypatch):
    evs, _s, _w, _log = await run_oc(tmp_path, monkeypatch, "opencode_model_answers_an_error")
    err = next(e for e in evs if e["type"] == "error")
    assert err["title"] == "OpenCode's model answered with an error"
    assert "No connected db" in err["technical"]
    assert err.get("fix_kind") not in ("account_limit",)
    assert evs[-1]["stop"] == "failed"
    signed_out = O.failure_event("Authentication required: run opencode auth login")
    assert signed_out["fix_kind"] == "sign_in" and "opencode auth login" in signed_out["fix"]


async def test_a_picture_goes_as_an_acp_image_block(tmp_path, monkeypatch):
    _evs, _s, work, log = await run_oc(tmp_path, monkeypatch, "opencode_reads_a_picture")
    prompt = next(m for m in sent(log) if m.get("method") == "session/prompt")["params"]["prompt"]
    assert prompt[0]["type"] == "text"
    assert prompt[1]["type"] == "image" and prompt[1]["mimeType"] == "image/png" and prompt[1]["data"]


async def test_plan_is_opencodes_plan_agent(tmp_path, monkeypatch):
    _evs, _s, _w, log = await run_oc(tmp_path, monkeypatch, "opencode_plan_mode")
    sets = [m["params"] for m in sent(log) if m.get("method") == "session/set_config_option"]
    assert {"configId": "mode", "value": "plan"} in [{k: p[k] for k in ("configId", "value")} for p in sets]


# --------------------------------------------------------------------------- #
# Permission rules: never widened
# --------------------------------------------------------------------------- #
def test_each_mode_is_a_permission_posture():
    ask, kept = O.permission_env("ask", {})
    assert ask["*"] == "ask" and kept == [] and ask["read"] == O.READ_RULE and "edit" not in ask
    edits, _ = O.permission_env("edits", {})
    assert edits["*"] == "ask" and edits["edit"] == "allow"
    full, _ = O.permission_env("full", {})
    assert full == {"*": "allow"}
    plan, _ = O.permission_env("plan", {})
    assert plan == {**ask, "edit": "deny"}, "(review, moved on purpose) Plan refuses every edit"


def test_a_stricter_rule_of_the_users_is_never_widened():
    """Measured: a project's "bash": "deny" with OPENCODE_PERMISSION={"*": "ask"}
    ASKED — OpenCode puts the environment's keys after the config's and the
    last match wins. So a stricter rule of the user's drops our "*"."""
    perm, kept = O.permission_env("ask", {"bash": "deny"})
    assert "*" not in perm and "bash" not in perm and kept == ["bash"]
    assert all(perm[t] == "ask" for t in O.ACTING_TOOLS if t != "bash")
    perm, kept = O.permission_env("full", {"bash": "ask", "webfetch": {"*": "allow", "evil.example": "deny"}})
    assert "*" not in perm and "bash" not in perm and "webfetch" not in perm
    assert kept == ["bash", "webfetch"]
    perm, kept = O.permission_env("ask", {"*": "deny"})
    assert "*" not in perm and kept == ["*"]
    # A looser rule of the user's: our "*" is stricter and lands last — nothing widened.
    perm, kept = O.permission_env("ask", {"bash": "allow"})
    assert perm["*"] == "ask" and kept == []
    # One rule for everything decides alone.
    assert O.permission_env("full", "ask") == (None, ["*"])


def test_the_users_own_config_is_read_never_written(tmp_path, monkeypatch):
    conf = tmp_path / "global"
    conf.mkdir()
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    (conf / "opencode.jsonc").write_text('// mine\n{"permission": {"webfetch": "deny",},}\n', encoding="utf-8")
    folder = tmp_path / "proj" / "sub"
    folder.mkdir(parents=True)
    (tmp_path / "proj" / "opencode.json").write_text('{"permission": {"bash": "deny"}}', encoding="utf-8")
    files = [conf / "opencode.jsonc", tmp_path / "proj" / "opencode.json"]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
    env, note = O.child_env({"PATH": "x"}, "ask", str(folder))
    perm = json.loads(env["OPENCODE_PERMISSION"])
    assert "bash" not in perm and "webfetch" not in perm and "*" not in perm
    assert "bash" in note and "webfetch" in note
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in files] == before
    # An OPENCODE_PERMISSION of the user's own counts as their rules too.
    env, _ = O.child_env({"OPENCODE_PERMISSION": '{"edit": "deny"}'}, "edits", str(folder))
    assert "edit" not in json.loads(env["OPENCODE_PERMISSION"])


async def test_a_mode_change_reaches_a_new_process_before_the_next_message(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_oc(monkeypatch, "oc_resume", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="opencode", model=MODEL, mode="full", confirm=True)
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t1"}), 30)
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "ask"})
        assert r.status_code == 200, r.text
        assert r.json()["applied"]["mode"] == "next"
        svc = S.service_for(app.state.platform)
        assert svc.live[s["id"]].restart is True
        monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX_OC / "oc_resume_2.json"))
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "y", "turn_id": "t2"}), 30)).text)
    await stop_all(app)
    assert evs[-1]["stop"] == "completed", evs
    rows = starts(log)
    assert len(rows) == 2
    assert json.loads(rows[0]["env"]["OPENCODE_PERMISSION"]) == {"*": "allow"}
    assert json.loads(rows[1]["env"]["OPENCODE_PERMISSION"])["*"] == "ask"


# --------------------------------------------------------------------------- #
# Its own saved sessions
# --------------------------------------------------------------------------- #
async def test_a_second_process_reopens_the_same_session_and_draws_no_replay(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_oc(monkeypatch, "oc_resume", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="opencode", model=MODEL, mode="full", confirm=True)
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t1"}), 30)
        svc = S.service_for(app.state.platform)
        hid = store.get(app.state.platform.engine, s["id"]).harness_session_id
        await svc._stop_process(svc.live[s["id"]])
        monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX_OC / "oc_resume_2.json"))
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "y", "turn_id": "t2"}), 30)).text)
    await stop_all(app)
    loads = [m for m in sent(log) if m.get("method") == "session/load"]
    assert len(loads) == 1 and loads[0]["params"]["sessionId"] == hid
    assert not [m for m in sent(log)[3:] if m.get("method") == "session/new"]
    texts = [e["text"] for e in evs if e["type"] == "text_done"]
    assert texts == ["KIWI"], "the reopened conversation's replay is not drawn again"
    assert "OK" not in [e.get("text") for e in evs]


def _make_db(folder: Path, rows: list[tuple]) -> Path:
    db = folder / "opencode.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE session (id text primary key, project_id text, parent_id text, directory text, "
                "title text, time_created integer, time_updated integer, time_archived integer)")
    con.execute("CREATE TABLE message (id text primary key, session_id text)")
    for r in rows:
        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?)", r)
    con.execute("INSERT INTO message VALUES ('msg_1', 'ses_aaaaaaaaaaaaaaaaaaaaaa')")
    con.commit()
    con.close()
    return db


def test_the_disk_listing_reads_opencodes_own_database_read_only(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    work = tmp_path / "w"
    work.mkdir()
    db = _make_db(data, [
        ("ses_aaaaaaaaaaaaaaaaaaaaaa", "p", None, str(work).replace("\\", "/"), "Fix the tests", 1, 2_000, None),
        ("ses_bbbbbbbbbbbbbbbbbbbbbb", "p", "ses_aaaaaaaaaaaaaaaaaaaaaa", str(work), "a helper", 1, 3_000, None),
        ("ses_cccccccccccccccccccccc", "p", None, str(work), "archived", 1, 4_000, 5),
        ("ses_dddddddddddddddddddddd", "p", None, str(tmp_path / "other"), "elsewhere", 1, 6_000, None),
    ])
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    monkeypatch.setenv(O.DATA_DIR_ENV, str(data))
    got = S.disk_sessions("opencode", S.default_home("opencode"), str(work))
    assert [g["id"] for g in got] == ["ses_aaaaaaaaaaaaaaaaaaaaaa"]
    assert got[0]["title"] == "Fix the tests"
    assert S.disk_sessions("opencode", str(data), str(work), since=3.0) == []
    assert O.has_messages(str(data), "ses_aaaaaaaaaaaaaaaaaaaaaa") is True
    assert O.has_messages(str(data), "ses_dddddddddddddddddddddd") is False
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before, "OpenCode's database is never written"


async def test_back_to_the_default_model_is_refused_once_the_conversation_has_messages(app, work, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    _make_db(data, [])
    monkeypatch.setenv(O.DATA_DIR_ENV, str(data))
    row = store.create(app.state.platform.engine, harness="opencode", folder=str(work), title="t", account="",
                       account_json="", model=MODEL, effort="", mode="ask",
                       harness_session_id="ses_aaaaaaaaaaaaaaaaaaaaaa")
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert view["keeps_last"] is True
        r = await c.patch(f"/build/app/sessions/{row.id}", json={"model": ""})
        assert r.status_code == 409 and r.json()["detail"] == W.OPENCODE_KEEPS_LAST


async def test_open_in_terminal_types_opencodes_own_resume_line(app, work, monkeypatch):
    typed = {}

    def launch(platform, body, *, accounts=None, command="", **kw):
        typed.update(command=command, cwd=body.cwd, cli=body.cli)
        return SimpleNamespace(id="pane-1")

    import iron_jarvis.daemon.routes.terminals as T

    monkeypatch.setattr(T, "launch_pane", launch)
    row = store.create(app.state.platform.engine, harness="opencode", folder=str(work), title="t", account="",
                       account_json="", model="", effort="", mode="ask",
                       harness_session_id="ses_e70eb48c6fferpq4ML7qasfQk1")
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/open-terminal")
        assert r.status_code == 200, r.text
    assert typed == {"command": "opencode --session ses_e70eb48c6fferpq4ML7qasfQk1", "cwd": str(work),
                     "cli": "opencode"}
    assert S.typable_sid("opencode", "ses_e70eb48c6fferpq4ML7qasfQk1")
    assert not S.typable_sid("opencode", "ses_x & calc")
    assert not S.typable_sid("claude-code", "ses_e70eb48c6fferpq4ML7qasfQk1")


# --------------------------------------------------------------------------- #
# The row, the program, the words
# --------------------------------------------------------------------------- #
async def test_opencodes_row_has_its_own_modes_and_its_own_sign_in(app, monkeypatch):
    from iron_jarvis.core import procs

    spawned = []
    monkeypatch.setattr(procs, "spawn", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(S, "binary_for", lambda h: ["x"] if h == "opencode" else None)
    async with client(app) as c:
        rows = (await c.get("/build/app/harnesses")).json()["harnesses"]
    oc = next(r for r in rows if r["id"] == "opencode")
    assert oc["label"] == "OpenCode" and oc["installed"] is True
    assert [m["id"] for m in oc["modes"]] == ["ask", "edits", "plan", "full"]
    assert [m["id"] for m in oc["modes"] if m["dangerous"]] == ["full"]
    assert all(m["about"] for m in oc["modes"])
    assert [h["id"] for h in oc["hidden_modes"]] == ["auto"] and oc["hidden_modes"][0]["reason"]
    assert oc["own_sign_in"] is True and oc["signed_in"] is None and oc["accounts"] == []
    assert "opencode auth login" in oc["sign_in_hint"]
    assert spawned == [], "a status read started a process"


async def test_create_takes_no_account_and_full_access_needs_two_presses(app, work):
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "opencode", "folder": str(work), "account": "p1"})
        assert r.status_code == 400 and r.json()["detail"] == W.OPENCODE_OWN_SIGN_IN
        r = await c.post("/build/app/sessions", json={"harness": "opencode", "folder": str(work), "mode": "full"})
        assert r.status_code == 400 and "twice" in r.json()["detail"]
        r = await c.post("/build/app/sessions", json={"harness": "opencode", "folder": str(work), "mode": "auto"})
        assert r.status_code == 400 and "not offered" in r.json()["detail"]


def test_npm_shim_is_skipped_for_the_program_it_starts(tmp_path):
    shim = tmp_path / "opencode.cmd"
    shim.write_text("@ECHO off\n", encoding="utf-8")
    assert PR.npm_program(str(shim)) == str(shim), "no program beside it: the shim as found"
    exe = tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert PR.npm_program(str(shim)) == str(exe)
    assert PR.npm_program(str(tmp_path / "opencode.exe")) == str(tmp_path / "opencode.exe")


def test_the_driver_only_ever_picks_an_option_opencode_offered():
    from iron_jarvis.hosted.driver import Ask, AskRefused

    drv = O.OpenCodeDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                           mode="ask")
    ask = Ask("ask_1", "approval", "bash", 7, {"options": [{"optionId": "once", "kind": "allow_once"},
                                                          {"optionId": "reject", "kind": "reject_once"}]})
    assert drv.prepare_answer(ask, "allow", "", None)["result"]["outcome"]["optionId"] == "once"
    assert drv.prepare_answer(ask, "deny", "", None)["result"]["outcome"]["optionId"] == "reject"
    with pytest.raises(AskRefused):
        drv.prepare_answer(ask, "allow_session", "", None)


async def test_a_note_opencode_took_is_never_handed_back_even_when_stopped():
    """A second ``session/prompt`` is in OpenCode's conversation once sent: a
    Stop before its loop reached it must not put it back in the box (it would
    be sent twice) — it is kept, and said."""
    wrote: list[dict] = []
    emitted: list[dict] = []
    read: list[str] = []

    async def write(obj):
        wrote.append(obj)

    drv = O.OpenCodeDriver(write=write, emit=emitted.append, ask=lambda a: None, on_session_id=lambda s: None,
                           mode="ask")
    drv.session_id = "ses_aaaaaaaaaaaaaaaaaaaaaa"
    drv.steer_read = read.append
    await drv.send_user("work", [])
    await drv.steer("note-1", "and this", [])
    prompts = [m for m in wrote if m.get("method") == "session/prompt"]
    assert len(prompts) == 2
    await drv.interrupt()
    assert wrote[-1] == {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": drv.session_id}}
    for m in prompts:
        drv.on_message({"jsonrpc": "2.0", "id": m["id"], "result": {"stopReason": "cancelled"}})
    await asyncio.sleep(0)
    assert drv.unread_steers() == [], "the note is in OpenCode's conversation: never handed back"
    assert any(e["type"] == "notice" and e["text"] == W.OPENCODE_NOTE_KEPT for e in emitted)
    assert emitted[-1] == {**emitted[-1], "type": "done", "stop": "interrupted"}


async def test_the_turn_waits_for_every_prompt_so_a_late_note_never_runs_unfollowed():
    wrote: list[dict] = []
    emitted: list[dict] = []

    async def write(obj):
        wrote.append(obj)

    drv = O.OpenCodeDriver(write=write, emit=emitted.append, ask=lambda a: None, on_session_id=lambda s: None,
                           mode="ask")
    drv.session_id = "ses_aaaaaaaaaaaaaaaaaaaaaa"
    await drv.send_user("work", [])
    await drv.steer("note-1", "and this", [])
    first, note = [m for m in wrote if m.get("method") == "session/prompt"]
    drv.on_message({"jsonrpc": "2.0", "id": first["id"], "result": {"stopReason": "end_turn"}})
    await asyncio.sleep(0)
    assert drv.turn_open and not any(e["type"] == "done" for e in emitted), \
        "the message's answer came first: the turn waits for the note's run"
    drv.on_message({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
        "sessionUpdate": "agent_message_chunk", "messageId": "m2", "content": {"type": "text", "text": "late"}}}})
    drv.on_message({"jsonrpc": "2.0", "id": note["id"], "result": {"stopReason": "end_turn"}})
    await asyncio.sleep(0)
    assert [e["type"] for e in emitted][-3:] == ["text", "text_done", "done"]
    assert drv.unread_steers() == []
