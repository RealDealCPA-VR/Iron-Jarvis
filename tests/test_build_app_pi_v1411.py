"""v1.411.0 — Pi in Build's Code view: the user's REAL Pi (``pi --mode rpc``),
with its own tools, extensions, skills and settings ON; Iron Jarvis only hosts
it and turns every question Pi or an extension asks into a card.

Every turn replays a RECORDED Pi 1.3.0 session (masked live output,
``tests/fixtures/build_app_v1411``) through the fake harness process
(``tests/fixtures/fake_harness_v1409.py``) started by the real ``core.procs``
plumbing; the parser, routes and store are the real ones. Nothing here runs a
real Pi.

THE CONTRACT: ``dashboard/__tests__/fixtures/build-app-events-v1411.json`` is
written FROM THE REAL DAEMON fed those recordings; the dashboard decodes the
same file. Regenerate after an intended change with ``IJ_REGEN_BUILD_APP_PI=1``.
No new event kind: Pi's records are said in the v1.409.0 contract's types.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import fake_log, sent, starts
from tests._build_app_pi_helpers_v1411 import (
    FIX_PI,
    canonical,
    client,
    frames,
    new_session,
    run_pi,
    stop_all,
    until,
    use_fake_pi,
)

GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-events-v1411.json"

#: scenario -> (fixture, what the user does)
SCENARIOS: dict[str, tuple[str, list]] = {
    "pi_read_a_file": ("pi_read", []),
    "pi_extension_confirm_before_an_edit": ("pi_edit_confirm", [("answer", "allow", None)]),
    "pi_shell_command_streams_its_output": ("pi_bash", []),
    "pi_extension_select_before_a_command": (
        "pi_select", [("answer", "allow", {"Dangerous command: rm -rf ./nothing-here": "Allow"})]),
    "pi_extension_command_asks_for_input": ("pi_input_command", [("answer", "allow", {"Enter a value": "typed-by-test"})]),
    "pi_interrupt": ("pi_abort", [("interrupt",)]),
    "pi_provider_answers_an_error": ("pi_provider_error", []),
    "pi_not_signed_in_for_the_model": ("pi_no_sign_in", []),
}

#: Flags that would switch something of the user's own Pi OFF — never passed.
TOOL_OFF_FLAGS = (
    "--no-session", "--no-tools", "-nt", "--no-builtin-tools", "-nbt", "--tools", "-t", "--exclude-tools", "-xt",
    "--no-extensions", "-ne", "--no-skills", "-ns", "--no-prompt-templates", "-np", "--no-context-files", "-nc",
    "--no-mcp", "--offline", "--system-prompt", "--no-approve", "-na",
)

UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture(autouse=True)
def _fast_handled(monkeypatch):
    # An extension command that starts no run ends its turn after this grace.
    monkeypatch.setattr(P, "HANDLED_GRACE_S", 0.3)


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _canonical_items(events: list[dict]) -> list[dict]:
    """Item ids carry a per-process tag (and Pi's long tool-call ids): named
    item-N in order of first use, so the golden file is stable."""
    names: dict[str, str] = {}
    out = []
    for ev in events:
        if "item" in ev:
            names.setdefault(ev["item"], f"item-{len(names) + 1}")
            ev = {**ev, "item": names[ev["item"]]}
        out.append(ev)
    return out


async def _turn(c, sid: str, text: str = "do it", turn_id: str = "t1", **extra):
    return asyncio.ensure_future(
        c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id, **extra}))


# --------------------------------------------------------------------------- #
# THE CONTRACT
# --------------------------------------------------------------------------- #
async def test_the_golden_file_is_what_the_real_daemon_sends_for_pi(tmp_path, monkeypatch):
    got = {}
    for name, (fixture, actions) in SCENARIOS.items():
        evs, _app, _sid, work = await run_pi(tmp_path, monkeypatch, fixture, actions, name=name)
        got[name] = _canonical_items(canonical(evs, work))
    doc = {
        "_about": (
            "v1.411.0 Build Code view, Pi: the normalized events the REAL daemon sends for each scenario, "
            "produced by tests/test_build_app_pi_v1411.py from recorded Pi output (tests/fixtures/build_app_v1411, "
            "masked live runs of pi 1.3.0 on this PC). `at` is dropped; request ids are ask-N; item ids are item-N; "
            "every turn id is turn-1. Regenerate with IJ_REGEN_BUILD_APP_PI=1."
        ),
        "scenarios": got,
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("IJ_REGEN_BUILD_APP_PI") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(text.encode("utf-8"))
    on_disk = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert on_disk["scenarios"] == got, "the golden file is stale — IJ_REGEN_BUILD_APP_PI=1 after an intended change"


def test_every_pi_scenario_ends_once_and_uses_only_contract_types():
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
    for path in FIX_PI.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "\\Users\\VR" not in text and "100.66." not in text and "ij-pi-scratch" not in text, path.name
        doc = json.loads(text)
        assert doc["harness"] == "pi" and "pi 1.3.0" in doc["source"], path.name


# --------------------------------------------------------------------------- #
# What reaches Pi
# --------------------------------------------------------------------------- #
async def test_pi_starts_in_rpc_mode_on_its_own_saved_session_with_nothing_switched_off(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_read", log)
    monkeypatch.setenv("PI_SESSION_ID", "parent-session")
    monkeypatch.setenv("PI_CODING_AGENT", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-users-own-key-0000")
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        assert s["mode"] == "own", "a Pi session starts in Pi's own rules"
        evs = frames((await asyncio.wait_for(await _turn(c, s["id"]), 30)).text)
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
    await stop_all(app)
    start = starts(log)[0]
    argv = start["argv"]
    assert argv[:2] == ["--mode", "rpc"]
    assert argv[2] == "--session-id" and UUID_RX.match(argv[3]), "the id is minted BEFORE Pi starts"
    assert row["harness_session_id"] == argv[3], "the row names Pi's own saved session"
    assert not set(argv) & set(TOOL_OFF_FLAGS), argv
    assert start["cwd"] == str(work)
    assert "PI_SESSION_ID" not in start["env"] and "PI_CODING_AGENT" not in start["env"], \
        "a parent Pi session's markers never cross"
    assert start["env"].get("OPENAI_API_KEY") == "sk-users-own-key-0000", "the user's own keys are a terminal's"
    assert [m["type"] for m in sent(log)][:4] == ["get_state", "get_commands", "get_available_models", "prompt"]
    assert sent(log)[3]["message"] == "do it"
    assert evs[1]["type"] == "session" and evs[1]["harness"] == "pi" and evs[1]["mode"] == "own"


async def test_a_model_and_a_thinking_level_are_pis_own_flags_and_change_live(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_read", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi", model="openai-codex/gpt-6-luna", effort="low")
        await asyncio.wait_for(await _turn(c, s["id"]), 30)
        argv = starts(log)[0]["argv"]
        assert argv[argv.index("--model") + 1] == "openai-codex/gpt-6-luna"
        assert argv[argv.index("--thinking") + 1] == "low"
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"model": "openai-codex/gpt-6-astra", "effort": "high"})
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == {"model": "now", "effort": "now"}
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"model": "gpt-6-astra"})
        assert r.status_code == 409 and "provider/model" in r.json()["detail"]
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"effort": "ultra"})
        assert r.status_code == 400 and "off, minimal, low, medium, high, xhigh, max" in r.json()["detail"]
    await stop_all(app)
    ins = sent(log)
    assert {"type": "set_model", "provider": "openai-codex", "modelId": "gpt-6-astra"}.items() <= next(
        m for m in ins if m["type"] == "set_model").items()
    assert next(m for m in ins if m["type"] == "set_thinking_level")["level"] == "high"


async def test_an_extension_confirm_is_a_card_of_the_running_tool_and_only_the_users_answer_goes_back(
        app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_edit_confirm", log)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        task = await _turn(c, s["id"])
        ask = await until(lambda: (svc := S.service_for(app.state.platform)).live.get(s["id"])
                          and svc.live[s["id"]].asks and next(iter(svc.live[s["id"]].asks.values())))
        ev = ask.event
        assert ev["kind"] == "edit" and ev["title"] == "Edit hello.txt" and ev["path"] == "hello.txt"
        assert "+pear" in ev["diff"] and "-apple" in ev["diff"], "the card shows the change the extension asks about"
        assert ev["can_session"] is False and ev["once_only"] is True, "an extension asks every time"
        assert "Allow this change?" in ev["detail"]
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": ev["request_id"], "decision": "allow_session"})
        assert r.status_code == 400, "Pi offers no session-wide allow"
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": ev["request_id"], "decision": "allow"})
        assert r.status_code == 200
        await asyncio.wait_for(task, 30)
    await stop_all(app)
    ui = [m for m in sent(log) if m["type"] == "extension_ui_response"]
    assert ui == [{"type": "extension_ui_response", "id": "1a69dab6-3189-4d6e-8867-43409e392540", "confirmed": True}]


async def test_a_deny_is_confirmed_false_and_a_skip_is_cancelled(app, work, tmp_path, monkeypatch):
    log = tmp_path / "deny.log"
    use_fake_pi(monkeypatch, "pi_edit_confirm", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        task = await _turn(c, s["id"])
        rid = await until(lambda: svc.live.get(s["id"]) and next(iter(svc.live[s["id"]].asks), None))
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": rid, "decision": "deny"})
        await asyncio.wait_for(task, 30)
    await stop_all(app)
    assert [m for m in sent(log) if m["type"] == "extension_ui_response"][0]["confirmed"] is False
    app2 = create_app(str(tmp_path / "home2"))
    log2 = tmp_path / "skip.log"
    use_fake_pi(monkeypatch, "pi_input_command", log2)
    svc2 = S.service_for(app2.state.platform)
    async with client(app2) as c:
        s = await new_session(c, work, harness="pi")
        task = await _turn(c, s["id"])
        rid = await until(lambda: svc2.live.get(s["id"]) and next(iter(svc2.live[s["id"]].asks), None))
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": rid, "decision": "deny"})
        await asyncio.wait_for(task, 30)
    await stop_all(app2)
    ui = [m for m in sent(log2) if m["type"] == "extension_ui_response"]
    assert ui == [{"type": "extension_ui_response", "id": "3cfae087-1a68-4580-a935-26aa0f5edc2e", "cancelled": True}]


async def test_a_select_sends_the_chosen_label_and_an_input_the_typed_words(tmp_path, monkeypatch):
    _evs, _app, _sid, _work = await run_pi(
        tmp_path, monkeypatch, "pi_select",
        [("answer", "allow", {"Dangerous command: rm -rf ./nothing-here": "Allow"})], name="select")
    ui = [m for m in sent(tmp_path / "select" / "fake.log") if m["type"] == "extension_ui_response"]
    assert len(ui) == 1 and ui[0]["value"] == "Allow" and "cancelled" not in ui[0]
    _evs, _app, _sid, _work = await run_pi(
        tmp_path, monkeypatch, "pi_input_command",
        [("answer", "allow", {"Enter a value": "my words"})], name="input")
    ui = [m for m in sent(tmp_path / "input" / "fake.log") if m["type"] == "extension_ui_response"]
    assert ui == [{"type": "extension_ui_response", "id": "3cfae087-1a68-4580-a935-26aa0f5edc2e", "value": "my words"}]


async def test_an_answer_with_no_words_is_refused_before_anything_reaches_pi(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_input_command", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        task = await _turn(c, s["id"])
        rid = await until(lambda: svc.live.get(s["id"]) and next(iter(svc.live[s["id"]].asks), None))
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": rid, "decision": "allow", "answers": {"Enter a value": "   "}})
        assert r.status_code == 400 and "Choose an answer" in r.json()["detail"]
        assert rid in svc.live[s["id"]].asks, "the card is still waiting"
        assert not [m for m in sent(log) if m["type"] == "extension_ui_response"]
        await c.post(f"/build/app/sessions/{s['id']}/answer",
                     json={"request_id": rid, "decision": "allow", "answers": {"Enter a value": "ok"}})
        await asyncio.wait_for(task, 30)
    await stop_all(app)


async def test_stop_sends_pis_own_abort_and_the_turn_says_interrupted(tmp_path, monkeypatch):
    evs, _app, _sid, _work = await run_pi(tmp_path, monkeypatch, "pi_abort", [("interrupt",)], name="stop")
    assert [m["type"] for m in sent(tmp_path / "stop" / "fake.log")][-1] == "abort"
    assert evs[-1] == {**evs[-1], "type": "done", "stop": "interrupted"}


async def test_a_dialog_pi_timed_out_closes_its_card(app, work, tmp_path, monkeypatch):
    """A dialog with ``timeout`` is answered by Pi itself when it runs out; the
    card goes with it, and a late answer finds nothing."""
    doc = json.loads((FIX_PI / "pi_edit_confirm.json").read_text(encoding="utf-8"))
    steps = []
    for st in doc["steps"]:
        if st.get("await") == "ui":
            continue  # Pi does not wait: it resolved the dialog on its own
        if isinstance(st.get("out"), dict) and st["out"].get("method") == "confirm":
            st = {"out": {**st["out"], "timeout": 300}}
        steps.append(st)
    # ...and Pi holds the rest of the turn back until the timeout has passed.
    at = next(i for i, st in enumerate(steps) if isinstance(st.get("out"), dict)
              and st["out"].get("type") == "tool_execution_end")
    steps.insert(at, {"await": "command", "command": "get_session_stats"})
    path = tmp_path / "timeout.json"
    path.write_text(json.dumps({"harness": "pi", "source": "synthesized from pi_edit_confirm", "steps": steps}),
                    encoding="utf-8")
    use_fake_pi(monkeypatch, "pi_read", tmp_path / "fake.log")
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(path))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        task = await _turn(c, s["id"])
        rid = await until(lambda: svc.live.get(s["id"]) and next(iter(svc.live[s["id"]].asks), None))
        await until(lambda: rid not in svc.live[s["id"]].asks)
        r = await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": rid, "decision": "allow"})
        assert r.status_code == 404
        await svc.live[s["id"]].driver.write({"id": "x", "type": "get_session_stats"})
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    assert {"type": "resolved", "request_id": rid, "decision": "expired"}.items() <= next(
        e for e in evs if e["type"] == "resolved").items()


async def test_a_failure_says_pis_own_fix_never_another_tools_press(tmp_path, monkeypatch):
    evs, _app, _sid, _work = await run_pi(tmp_path, monkeypatch, "pi_no_sign_in", [], name="signin")
    err = next(e for e in evs if e["type"] == "error")
    assert err["fix_kind"] == "sign_in" and "/login" in err["fix"] and "Settings" not in err["fix"]
    assert evs[-1]["stop"] == "failed"
    evs, _app, _sid, _work = await run_pi(tmp_path, monkeypatch, "pi_provider_error", [], name="provider")
    err = next(e for e in evs if e["type"] == "error")
    assert "fix_kind" not in err and "No connected db" in err["technical"]
    assert evs[-1]["stop"] == "failed"
    limit = P.failure_event("429 Too Many Requests: usage limit reached")
    assert limit.get("fix_kind") != "account_limit", "Pi has no accounts to continue on"


def test_pi_words_say_its_own_rules_and_never_the_internal_names():
    for text in (*P.HIDDEN_MODES.values(), *P.MODE_LABELS.values(), P.SIGN_IN_FIX, P.LIMIT_FIX, P.OTHER_FIX,
                 W.PI_OWN_SIGN_IN, W.PI_CONTINUE):
        assert not re.search(r"\b(daemon|harness|app view)\b", text, re.I), text
    assert "without asking" in P.HIDDEN_MODES["full"] and "card" in P.HIDDEN_MODES["ask"]


# --------------------------------------------------------------------------- #
# The listing, modes and accounts
# --------------------------------------------------------------------------- #
async def test_pis_row_offers_its_own_rules_only_and_its_own_sign_in(app, work, tmp_path, monkeypatch):
    from iron_jarvis.core import procs

    spawned = []
    monkeypatch.setattr(procs, "spawn", lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(S, "binary_for", lambda h: ["x"])
    monkeypatch.setattr(P, "LAST_MODELS", [])
    async with client(app) as c:
        rows = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}
    pi = rows["pi"]
    assert pi["label"] == "Pi" and pi["installed"] is True
    assert pi["signed_in"] is None, "Pi signs in per provider: never a guessed verdict"
    assert pi["accounts"] == [] and pi["own_sign_in"] is True
    assert rows["claude-code"]["own_sign_in"] is False
    # v1.413.0: each mode says what it does (moved on purpose).
    assert pi["modes"] == [{"id": "own", "label": "Pi's own rules", "dangerous": False,
                            "about": "Pi asks nothing before a tool; only an extension of yours can ask."}]
    assert [h["id"] for h in pi["hidden_modes"]] == ["ask", "edits", "plan", "full"]
    assert all(h["reason"] for h in pi["hidden_modes"])
    assert pi["efforts"] == ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
    assert pi["models"] == [{"id": "", "label": "Pi's default"}]
    assert spawned == [], "a status read started a process"


async def test_create_takes_no_other_mode_and_no_account_for_pi(app, work):
    async with client(app) as c:
        for mode in ("ask", "edits", "plan"):
            r = await c.post("/build/app/sessions", json={"harness": "pi", "folder": str(work), "mode": mode})
            assert r.status_code == 400 and "not offered for Pi" in r.json()["detail"], mode
        r = await c.post("/build/app/sessions", json={"harness": "pi", "folder": str(work), "mode": "full",
                                                      "confirm": True})
        assert r.status_code == 400 and "without asking" in r.json()["detail"]
        r = await c.post("/build/app/sessions", json={"harness": "pi", "folder": str(work), "account": "p1"})
        assert r.status_code == 400 and r.json()["detail"] == W.PI_OWN_SIGN_IN
        r = await c.post("/build/app/sessions", json={"harness": "pi", "folder": str(work), "mode": "own"})
        assert r.status_code == 201 and r.json()["mode"] == "own" and r.json()["account"] == ""
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work), "mode": "own"})
        assert r.status_code == 400, "Pi's own rules is Pi's alone"
        sid = r.json() if r.status_code == 201 else None
        assert sid is None
        made = (await c.post("/build/app/sessions", json={"harness": "pi", "folder": str(work)})).json()
        r = await c.post(f"/build/app/sessions/{made['id']}/continue-on", json={})
        assert r.status_code == 409 and r.json()["detail"] in (W.PI_CONTINUE, W.NO_SESSION_YET)
        store.update(app.state.platform.engine, made["id"], harness_session_id=str(uuid.uuid4()))
        r = await c.post(f"/build/app/sessions/{made['id']}/continue-on", json={})
        assert r.status_code == 409 and r.json()["detail"] == W.PI_CONTINUE


async def test_the_slash_list_and_the_models_are_what_pi_said(app, work, tmp_path, monkeypatch):
    from iron_jarvis.hosted import claude as CL

    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    monkeypatch.setattr(P, "LAST_MODELS", [])
    use_fake_pi(monkeypatch, "pi_read", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        await asyncio.wait_for(await _turn(c, s["id"]), 30)
        cmds = (await c.get(f"/build/app/sessions/{s['id']}/commands")).json()["commands"]
        assert {"plan", "todos", "implement", "rpc-input"} <= {x["name"] for x in cmds}
        rows = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}
    await stop_all(app)
    ids = [m["id"] for m in rows["pi"]["models"]]
    assert ids[0] == "" and "openai-codex/gpt-6-luna" in ids and "local-models/fleet" in ids
    assert rows["pi"]["commands"] and rows["codex"]["commands"] == []
    # Kept for after a restart: a NEW service reads the list from disk.
    monkeypatch.setattr(P, "LAST_MODELS", [])
    saved = json.loads((Path(app.state.platform.config.home) / "hosted" / "pi_models.json").read_text(encoding="utf-8"))
    assert [m["id"] for m in saved["models"]] == ids[1:]
    assert [m["id"] for m in S.HostedService(app.state.platform)._models("pi")] == ids


async def test_pictures_go_as_pis_own_images_and_other_files_are_named(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_read", log)
    pic = work / "shot.png"
    pic.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    doc = work / "notes.md"
    doc.write_text("x", encoding="utf-8")
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        await asyncio.wait_for(await _turn(c, s["id"], files=[str(pic), str(doc)]), 30)
    await stop_all(app)
    prompt = next(m for m in sent(log) if m["type"] == "prompt")
    assert prompt["images"][0]["mimeType"] == "image/png" and prompt["images"][0]["type"] == "image"
    assert str(doc) in prompt["message"] and str(pic) not in prompt["message"]


async def test_a_second_process_resumes_the_same_pi_session(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake_pi(monkeypatch, "pi_read", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        await asyncio.wait_for(await _turn(c, s["id"]), 30)
        await svc.reap_once(now=svc.clock() + S.IDLE_S + 1)
        assert not svc.live[s["id"]].running
        await asyncio.wait_for(await _turn(c, s["id"], turn_id="t2"), 30)
    await stop_all(app)
    first, second = starts(log)[:2]
    assert first["argv"][3] == second["argv"][3], "the next start opens the SAME saved Pi session"


# --------------------------------------------------------------------------- #
# Pi's own saved sessions on disk
# --------------------------------------------------------------------------- #
def write_pi_session(root: Path, folder: Path, sid: str, first: str, *, name: str = "",
                     header_id: str | None = None, stamp: str = "2026-10-30T13-12-13-190Z") -> Path:
    """A Pi session file as Pi writes it under a ``sessionDir`` /
    PI_CODING_AGENT_SESSION_DIR: FLAT in that folder (session-manager.js;
    only the default ``~/.pi/agent/sessions`` has per-folder subfolders),
    matched to its project by the header's ``cwd``."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{stamp}_{sid}.jsonl"
    rows = [
        {"type": "session", "version": 3, "id": header_id or sid, "timestamp": "2026-10-30T13:12:13.190Z",
         "cwd": str(folder)},
        {"type": "message", "id": "a1", "parentId": None, "timestamp": "2026-10-30T13:12:20.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": first}]}},
    ]
    if name:
        rows.append({"type": "session_info", "id": "a2", "parentId": "a1", "timestamp": "2026-10-30T13:13:00.000Z",
                     "name": name})
    path.write_bytes(("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8"))
    return path


async def test_the_disk_listing_reads_pis_own_sessions_read_only(app, work, tmp_path, monkeypatch):
    root = tmp_path / "pi-sessions"
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    a = "01a1b2c3-1111-7222-8333-444444444444"
    b = "01a1b2c3-2222-7222-8333-444444444444"
    write_pi_session(root, work, a, "fix the parser")
    write_pi_session(root, work, b, "first words", name="Named session", stamp="2026-10-30T14-00-00-000Z")
    write_pi_session(root, tmp_path / "elsewhere", "01a1b2c3-3333-7222-8333-444444444444", "other folder")
    write_pi_session(root, work, "01a1b2c3-4444-7222-8333-444444444444", "header lies",
                     header_id="01a1b2c3-5555-7222-8333-444444444444")
    (root / "loose.jsonl").write_text("{}", encoding="utf-8")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    async with client(app) as c:
        rows = (await c.get(f"/build/app/sessions?folder={work}&disk=1")).json()["disk"]
        assert {(r["harness"], r["harness_session_id"], r["title"]) for r in rows} == {
            ("pi", a, "fix the parser"), ("pi", b, "Named session")}
        made = await new_session(c, work, harness="pi", harness_session_id=a)
        assert made["harness_session_id"] == a and made["title"] == "fix the parser" and made["mode"] == "own"
        r = await c.post("/build/app/sessions", json={"harness": "pi", "folder": str(work),
                                                      "harness_session_id": "01a1b2c3-5555-7222-8333-444444444444"})
        assert r.status_code == 404, "an id is matched against the listing, never joined into a path"
        r = await c.post("/build/app/sessions", json={"harness": "pi", "folder": str(work),
                                                      "harness_session_id": "..\\..\\x"})
        assert r.status_code == 404
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before, "read-only"


def test_pis_session_folder_follows_its_own_settings(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    assert P.sessions_dir() == agent / "sessions"
    (agent / "settings.json").write_text(json.dumps({"sessionDir": str(tmp_path / "s2")}), encoding="utf-8")
    assert P.sessions_dir() == tmp_path / "s2"
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "s3"))
    assert P.sessions_dir() == tmp_path / "s3"


# --------------------------------------------------------------------------- #
# Terminals, both ways
# --------------------------------------------------------------------------- #
async def test_open_in_terminal_types_pis_own_resume_line(app, work, tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import terminals as T

    launched = []

    def fake_launch(platform, body, *, accounts=None, command=None, claude_session_id=None, **kw):
        launched.append({"cli": body.cli, "cwd": body.cwd, "command": command, "sid": claude_session_id})
        return SimpleNamespace(id="term_pi")

    monkeypatch.setattr(T, "launch_pane", fake_launch)
    use_fake_pi(monkeypatch, "pi_read", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        await asyncio.wait_for(await _turn(c, s["id"]), 30)
        hid = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]["harness_session_id"]
        proc = svc.live[s["id"]].proc
        r = await c.post(f"/build/app/sessions/{s['id']}/open-terminal")
        assert r.json() == {"terminal_id": "term_pi"}
        assert not proc.alive, "one conversation, one process"
    await stop_all(app)
    assert launched == [{"cli": "pi", "cwd": str(work), "command": f"pi --session {hid}", "sid": None}]


class _PiPane:
    def __init__(self, cwd, tail, created=None):
        self.id = "pane-pi"
        self.cwd = str(cwd)
        self.shell = "pwsh.exe"
        self.agent_cli = "pi"
        self.resume_cli = None
        self.claude_session_id = None
        self.accounts = {}
        self.alive = True
        self.created_at = created or datetime.now(timezone.utc) - timedelta(minutes=5)
        self._tail = tail

    def output_tail(self):
        return self._tail


async def test_open_in_code_view_from_a_pi_terminal_finds_the_one_session_it_ran(app, work, tmp_path, monkeypatch):
    root = tmp_path / "pi-sessions"
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(root))
    panes: dict = {}
    monkeypatch.setattr(app.state.platform.terminals, "get", lambda pid: panes.get(pid))
    hid = "01a1b2c3-6666-7222-8333-444444444444"
    async with client(app) as c:
        panes["p"] = _PiPane(work, "PS C:\\work> ")
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 409 and "cannot tell which Pi conversation" in r.json()["detail"]
        write_pi_session(root, work, hid, "carry this on")
        assert S.can_open_in_code(panes["p"]) is True
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 201, r.text
        made = r.json()
        again = (await c.post("/build/app/from-terminal/p")).json()
    assert made["harness"] == "pi" and made["harness_session_id"] == hid and made["mode"] == "own"
    assert made["account"] == "" and again["reused"] is True and again["id"] == made["id"]


# --------------------------------------------------------------------------- #
# The bell
# --------------------------------------------------------------------------- #
async def test_a_waiting_pi_card_is_on_the_bell_by_name_only(app, work, tmp_path, monkeypatch):
    use_fake_pi(monkeypatch, "pi_edit_confirm", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    s = None
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        task = await _turn(c, s["id"])
        rid = await until(lambda: svc.live.get(s["id"]) and next(iter(svc.live[s["id"]].asks), None))
        task.cancel()  # nobody follows the turn any more
        await until(lambda: not svc.turn("t1").watched)
        rows = S.waiting_rows(app.state.platform)
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": rid, "decision": "deny"})
    await stop_all(app)
    assert rows == [{"id": f"build_app:{s['id']}", "kind": "chat", "thread_id": s["id"], "tool": "edit",
                     "requested_at": rows[0]["requested_at"], "mission_id": None, "project_id": None,
                     "surface": "build_app", "app_session": s["id"], "harness": "pi"}]


# --------------------------------------------------------------------------- #
# The parser's own pieces
# --------------------------------------------------------------------------- #
def _driver(emitted: list) -> P.PiDriver:
    async def write(obj):
        return None

    return P.PiDriver(write=write, emit=emitted.append, ask=lambda a: None, on_session_id=lambda s: None,
                      mode="own")


async def test_a_restarted_pi_never_joins_an_earlier_turns_reply():
    """Pi names no message id on the wire: a later PROCESS's first answer must
    not share an item with an earlier one's (the screen joins text by item)."""
    first, second = [], []
    for out in (first, second):
        d = _driver(out)
        d.on_message({"type": "message_start", "message": {"role": "assistant", "content": []}})
        d.on_message({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                                                          "delta": "hi"}})
    assert first[0]["item"] != second[0]["item"]


async def test_a_running_commands_output_is_redrawn_at_most_once_a_second():
    out: list = []
    d = _driver(out)
    d.on_message({"type": "tool_execution_start", "toolCallId": "c1", "toolName": "bash", "args": {"command": "x"}})
    for i in range(5):
        d.on_message({"type": "tool_execution_update", "toolCallId": "c1", "toolName": "bash",
                      "partialResult": {"content": [{"type": "text", "text": f"line {i}"}]}})
    assert [e["status"] for e in out] == ["running", "running"]
    assert out[1]["output"] == "line 0"


def test_the_driver_itself_refuses_a_session_wide_allow():
    """The service refuses first (the card offers none); the driver refuses too,
    before anything is sent."""
    from iron_jarvis.hosted.driver import Ask, AskRefused

    d = _driver([])
    for method in ("confirm", "select"):
        ask = Ask("ask_1", "approval" if method == "confirm" else "question", "edit", "pi-dialog-1",
                  {"method": method, "question": "Q?"})
        with pytest.raises(AskRefused):
            d.prepare_answer(ask, "allow_session", "", {"Q?": "A"})


async def test_iron_proxy_accounts_never_reach_pis_row(app, monkeypatch):
    from iron_jarvis.harness import build_accounts as BA

    monkeypatch.setattr(S, "binary_for", lambda h: ["x"])
    monkeypatch.setattr(BA, "snapshot", lambda svc: ([{"id": "p1"}], {}))
    monkeypatch.setattr(BA, "vendor_rows", lambda snap, vendor: [{"id": f"{vendor}-1", "name": "Work", "status": "ready"}])
    async with client(app) as c:
        rows = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}
    assert rows["pi"]["accounts"] == []
    assert rows["codex"]["accounts"] == [{"id": "codex-1", "name": "Work", "status": "ready"}]
