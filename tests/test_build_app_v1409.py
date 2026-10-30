"""v1.409.0 — Build's Code view: THE CONTRACT. The golden file
``dashboard/__tests__/fixtures/build-app-events-v1409.json`` is written FROM
THE REAL DAEMON (routes, service, parser) fed RECORDED harness output (masked
live runs of claude 2.1.311 / codex-cli 0.157.0 on this PC); the dashboard
decodes the same file. Regenerate after an intended change with
``IJ_REGEN_BUILD_APP=1``.

Also: the event log's bounds and merging, masking before a cut, and the
harnesses' own pieces the parser reads (Claude's TodoWrite plan, a Codex plan,
an Edit's diff).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, until, use_fake

GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-events-v1409.json"

#: scenario -> (fixture, harness, what the user does)
SCENARIOS: dict[str, tuple[str, str, list]] = {
    "claude_tool_approval_edit": ("claude_edit", "claude-code", [("answer", "allow", None)]),
    "claude_two_approvals": ("claude_allow", "claude-code", [("answer", "allow", None), ("answer", "allow_session", None)]),
    "claude_deny": ("claude_deny", "claude-code", [("answer", "deny", None)]),
    "claude_interrupt": ("claude_interrupt", "claude-code", [("interrupt",)]),
    "claude_ask_user_question": ("claude_ask", "claude-code", [("answer", "allow", ["Blue"])]),
    "claude_sign_in": ("claude_signin", "claude-code", []),
    "claude_account_limit_synthesized": ("claude_limit", "claude-code", []),
    "codex_command_and_file_change": ("codex_allow", "codex", [("answer", "allow", None), ("answer", "allow", None)]),
    "codex_deny": ("codex_deny", "codex", [("answer", "deny", None)]),
    "codex_interrupt": ("codex_interrupt", "codex", [("interrupt",)]),
    "codex_sign_in": ("codex_signin", "codex", []),
}


def canonical(events: list[dict], work: Path) -> list[dict]:
    """``at`` dropped, Iron Jarvis's own ids replaced by stable names, this
    run's folder written as the recordings' masked one."""
    names: dict[str, str] = {}
    out = []
    for ev in events:
        ev = json.loads(json.dumps(ev).replace(json.dumps(str(work))[1:-1], r"C:\\work\\demo"))
        ev = {k: v for k, v in ev.items() if k != "at"}
        rid = ev.get("request_id")
        if rid:
            names.setdefault(rid, f"ask-{len(names) + 1}")
            ev["request_id"] = names[rid]
        if "turn" in ev:
            ev["turn"] = "turn-1"
        out.append(ev)
    return out


async def run_scenario(tmp_path: Path, monkeypatch, name: str) -> list[dict]:
    fixture, harness, actions = SCENARIOS[name]
    base = tmp_path / name
    work = base / "work"
    work.mkdir(parents=True)
    use_fake(monkeypatch, fixture, base / "fake.log")
    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    if fixture == "claude_limit":
        rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(base / "acc"), "unset": []}
        (base / "acc").mkdir()
        row = store.create(app.state.platform.engine, harness=harness, folder=str(work), account="p1",
                           account_json=json.dumps(rec))
        sid = row.id
    else:
        async with client(app) as c:
            sid = (await new_session(c, work, harness=harness))["id"]
    async with client(app) as c:
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        done_asks: set[str] = set()
        for act in actions:
            if act[0] == "interrupt":
                if harness == "codex":
                    await until(lambda: svc.live.get(sid) and svc.live[sid].driver and svc.live[sid].driver.turn_id)
                else:
                    await until(lambda: svc.turn("t") and any(e["type"] == "text" for e in svc.turn("t").events))
                await c.post(f"/build/app/sessions/{sid}/interrupt")
                continue
            _, decision, answers = act
            ask = await until(lambda: next((a for a in (svc.live.get(sid).asks if svc.live.get(sid) else {})
                                            if a not in done_asks), None))
            done_asks.add(ask)
            r = await c.post(f"/build/app/sessions/{sid}/answer",
                             json={"request_id": ask, "decision": decision, "answers": answers})
            assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    return canonical(evs, work)


async def test_the_golden_file_is_what_the_real_daemon_sends(tmp_path, monkeypatch):
    got = {name: await run_scenario(tmp_path, monkeypatch, name) for name in SCENARIOS}
    doc = {
        "_about": (
            "v1.409.0 Build Code view: the normalized events the REAL daemon sends for each scenario, "
            "produced by tests/test_build_app_v1409.py from recorded harness output "
            "(tests/fixtures/build_app_v1409, masked live runs of claude 2.1.311 / codex-cli 0.157.0; "
            "claude_account_limit_synthesized is built from recorded frame shapes, see its fixture). "
            "`at` is dropped; request ids are ask-N; every turn id is turn-1. Regenerate with IJ_REGEN_BUILD_APP=1."
        ),
        "scenarios": got,
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("IJ_REGEN_BUILD_APP") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(text.encode("utf-8"))
    on_disk = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert on_disk["scenarios"] == got, "the golden file is stale — IJ_REGEN_BUILD_APP=1 after an intended change"


def test_every_golden_scenario_ends_once_and_uses_only_contract_types():
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, evs in doc["scenarios"].items():
        assert evs[0]["type"] == "user", name
        assert [e["type"] for e in evs].count("done") == 1 and evs[-1]["type"] == "done", name
        assert {e["type"] for e in evs} <= set(E.TYPES), name
        for e in evs:
            if e["type"] == "tool":
                assert e["kind"] in E.TOOL_KINDS and e["status"] in ("running", "done", "failed")
                assert len(e["input"]) <= E.INPUT_CHARS and len(e.get("output", "")) <= E.OUTPUT_CHARS
            if e["type"] == "approval":
                assert e["kind"] in E.APPROVAL_KINDS and isinstance(e["can_session"], bool)
            if e["type"] == "done":
                assert e["stop"] in E.STOPS
    sc = doc["scenarios"]
    assert any(e["type"] == "approval" and e.get("diff") for e in sc["claude_tool_approval_edit"])
    assert any(e["type"] == "tool" and e.get("diff") and e["status"] == "done" for e in sc["claude_tool_approval_edit"])
    assert any(e["type"] == "question" for e in sc["claude_ask_user_question"])
    assert sc["claude_interrupt"][-1]["stop"] == "interrupted" and sc["codex_interrupt"][-1]["stop"] == "interrupted"
    for k in ("claude_sign_in", "codex_sign_in"):
        assert [e["fix_kind"] for e in sc[k] if e["type"] == "error"] == ["sign_in"]
    assert [e.get("fix_kind") for e in sc["claude_account_limit_synthesized"] if e["type"] == "error"] == ["account_limit"]
    kinds = [e["kind"] for e in sc["codex_command_and_file_change"] if e["type"] == "approval"]
    assert kinds == ["command", "edit"]


# --------------------------------------------------------------------------- #
# pieces
# --------------------------------------------------------------------------- #
def test_text_is_masked_before_it_is_cut():
    key = "sk-ant-api03-" + "A" * 60
    cut = E.clip("x" * 10 + " " + key, 20)
    # Cut first, the mask no longer knows "sk-ant-a" for a key: a piece leaks.
    assert "sk-ant-a" not in cut and "AAAA" not in cut


def test_the_session_table_is_a_late_model_the_reconciler_sees():
    from iron_jarvis.core import db

    assert "..hosted.models" in db._LATE_MODEL_MODULES


def test_the_log_merges_deltas_and_drops_the_oldest_lines_saying_so(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "MAX_LINES", 5)
    log = E.EventLog(tmp_path, "s1")
    for ch in "abc":
        log.append({"type": "text", "item": "i", "delta": ch, "turn": "t"})
    log.append({"type": "text_done", "item": "i", "text": "abc", "turn": "t"})
    log.flush()
    events, total, dropped = log.read()
    assert [e["type"] for e in events] == ["text", "text_done"] and events[0]["delta"] == "abc"
    for i in range(8):
        log.append({"type": "notice", "text": str(i)})
        log.flush()
    events, total, dropped = log.read()
    assert total == 5 and dropped == 5 and events[-1]["text"] == "7"
    log.close()


def test_claude_todowrite_is_a_plan_and_an_edit_carries_its_diff():
    from iron_jarvis.hosted.claude import ClaudeDriver

    got = []
    d = ClaudeDriver(write=None, emit=got.append, ask=lambda a: got.append(a.event),
                     on_session_id=lambda s: None, mode="ask")
    d.on_message({"type": "assistant", "message": {"id": "m", "content": [{"type": "tool_use", "id": "t1",
                  "name": "TodoWrite", "input": {"todos": [
                      {"content": "read", "status": "completed", "activeForm": "Reading"},
                      {"content": "fix", "status": "in_progress", "activeForm": "Fixing"}]}}]}})
    plan = [e for e in got if e["type"] == "plan"][0]
    assert plan["steps"] == [{"text": "read", "status": "completed"}, {"text": "fix", "status": "in_progress"}]
    d.on_message({"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "Edit",
        "input": {"file_path": "C:\\w\\a.py", "old_string": "x = 1", "new_string": "x = 2"}}})
    appr = [e for e in got if e["type"] == "approval"][0]
    assert appr["kind"] == "edit" and "-x = 1" in appr["diff"] and "+x = 2" in appr["diff"]
    assert appr["can_session"] is False


def test_codex_plan_and_mode_table():
    from iron_jarvis.hosted import codex as X

    got = []
    d = X.CodexDriver(write=None, emit=got.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.on_message({"method": "turn/plan/updated", "params": {"plan": [
        {"step": "look", "status": "completed"}, {"step": "do", "status": "inProgress"}]}})
    assert got[0]["steps"] == [{"text": "look", "status": "completed"}, {"text": "do", "status": "in_progress"}]
    # Ask first must still let an allowed command RUN: on Windows only
    # danger-full-access runs one (measured), so the user's card is the gate.
    assert X.MODES["ask"][0] == "untrusted"
    assert X.MODES["full"] == ("never", "danger-full-access")
    if os.name == "nt":
        assert X.MODES["ask"][1] == "danger-full-access" and "edits" not in X.MODES


def test_the_dashboard_link_names_the_code_view():
    src = Path(S.__file__).read_text(encoding="utf-8")
    route = (Path(S.__file__).parents[1] / "daemon" / "routes" / "build_app.py").read_text(encoding="utf-8")
    assert "view=code&code=" in route and "view=app" not in route and "&app=" not in route
    assert "ACP" not in src and "ACP" not in route
