"""Next leaps 40-a (master plan R2.18, idea 40 "Rewind the day"): Your day.

A seeded day comes back grouped by where each change came from (a run, a
schedule's run, chat, a settings page), newest first, each row with its
local hour and time, and what cannot be undone (a message sent, commands
run) in its own list, never offered an Undo. A key is named, never shown.
Before and after is read on this PC: a file's difference, a setting's two
values, a key's "a value is saved (never shown)". A change whose
before-copy is gone says so and offers no Undo.

Through the real daemon (``create_app``): the real write tool through the
registry, the settings page's own writer (``PUT /settings``), the key card
(``POST /config/secret``), and ``GET /changes/day`` /
``GET /changes/{id}/before-after``. The day is pinned by stamping the rows'
times (an injected clock, never a sleep).
"""

from __future__ import annotations

import json
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, ToolInvocation, UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import records_day as RD
from iron_jarvis.settings import records_history as H
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_chat_history_tools_v14350 import SECRET


def _local_utc(day, h: int, m: int = 0, s: int = 0) -> datetime:
    """*day* at h:m:s on this PC's clock, as the ledger's naive UTC."""
    return datetime.combine(day, dtime(h, m, s)).astimezone().astimezone(timezone.utc).replace(tzinfo=None)


def _app(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.permissions = PermissionEngine({**p.config.permissions, "write_file": "allow"})
    return app, p


def _run(p, task: str, *, at: datetime, origin: str = "user_task", options: dict | None = None, ws: Path) -> str:
    with session_scope(p.engine) as db:
        s = Session(task=task, origin=origin, options_json=json.dumps(options or {}), workspace_path=str(ws))
        s.created_at = at
        db.add(s)
        db.commit()
        return s.id


async def _write(p, ws: Path, sid: str, path: str, content: str) -> str:
    ws.mkdir(parents=True, exist_ok=True)
    ctx = ToolContext(workspace=ws, session_id=sid, agent_run_id=f"r-{sid}", config=p.config,
                      event_bus=p.event_bus, engine=p.engine)
    res = await p.registry.invoke("write_file", {"path": path, "content": content}, ctx, p.permissions)
    assert res.ok, res.error
    with session_scope(p.engine) as db:
        return db.exec(
            select(ToolInvocation.id).where(ToolInvocation.tool == "write_file").order_by(ToolInvocation.created_at.desc())
        ).first()


def _stamp(p, ids: list[str], at: datetime) -> None:
    """Pin each row's time: *at*, then a second apart, in the order given."""
    with session_scope(p.engine) as db:
        for i, aid in enumerate(ids):
            inv = db.get(ToolInvocation, aid)
            inv.created_at = at + timedelta(seconds=i)
            db.add(inv)
        db.commit()


def _sent(p, sid: str, tool: str, at: datetime, *, journal: bool = False) -> str:
    """A call that left this PC; ``journal``: an old row that kept a journal
    entry anyway (it is still never a change with an Undo)."""
    with session_scope(p.engine) as db:
        inv = ToolInvocation(session_id=sid, agent_run_id="r", tool=tool, ok=True, output="sent",
                             reversibility="irreversible", created_at=at)
        db.add(inv)
        if journal:
            db.add(UndoJournal(action_id=inv.id, session_id=sid, tool=tool, kind="other", reversible=True))
        db.commit()
        return inv.id


def _newest(p, tool: str) -> str:
    with session_scope(p.engine) as db:
        return db.exec(
            select(ToolInvocation.id).where(ToolInvocation.tool == tool).order_by(ToolInvocation.created_at.desc())
        ).first()


async def _seed(tmp_path):
    app, p = _app(tmp_path)
    client = TestClient(app)
    day = RD.today_local()
    ws = tmp_path / "reports"
    ws.mkdir()
    (ws / "report.txt").write_bytes(b"first draft\n")
    run = _run(p, "Tidy the quarterly reports", at=_local_utc(day, 15, 0), ws=ws)
    w1 = await _write(p, ws, run, "report.txt", "second draft\n")
    w2 = await _write(p, ws, run, "summary.txt", "the summary\n")
    sched_ws = tmp_path / "backups"
    nightly = _run(p, "back up the books", at=_local_utc(day, 2, 0), origin="schedule", ws=sched_ws,
                   options={"report_from": {"kind": "schedule", "name": "Nightly backup", "id": "t1"}})
    w3 = await _write(p, sched_ws, nightly, "backup.log", "ok\n")
    up = Path(p.config.home) / "uploads"
    w4 = await _write(p, up, "chat", "notes.txt", "a note\n")
    old = await _write(p, up, "chat", "old.txt", "from yesterday\n")
    r = client.put("/settings", json={"values": {"chat_followups": True}})
    assert r.status_code == 200, r.text
    setting = _newest(p, "update_settings")
    p.connections._prober = lambda provider, cred: (True, f"{provider} reachable")
    r = client.post("/config/secret", json={"name": "connection.openai", "value": SECRET})
    assert r.status_code == 200, r.text
    key = _newest(p, "set_secret")
    _stamp(p, [w3], _local_utc(day, 2, 5))
    _stamp(p, [w1, w2], _local_utc(day, 15, 2))
    _stamp(p, [w4, setting, key], _local_utc(day, 16, 10))
    _stamp(p, [old], _local_utc(day - timedelta(days=1), 11, 0))
    sent = _sent(p, run, "notify", _local_utc(day, 15, 4), journal=True)
    cmds = [_sent(p, run, "shell", _local_utc(day, 15, 3, s)) for s in range(3)]
    return SimpleSeed(app, p, client, day, ws, run, nightly, dict(w1=w1, w2=w2, w3=w3, w4=w4, old=old,
                                                                   setting=setting, key=key, sent=sent, cmds=cmds))


class SimpleSeed:
    def __init__(self, app, p, client, day, ws, run, nightly, ids):
        self.app, self.p, self.client, self.day, self.ws = app, p, client, day, ws
        self.run, self.nightly, self.ids = run, nightly, ids


@pytest.mark.asyncio
async def test_a_seeded_day_comes_back_grouped_with_what_cannot_be_undone_apart(tmp_path):
    s = await _seed(tmp_path)
    r = s.client.get(f"/changes/day?date={s.day.isoformat()}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == s.day.isoformat() and body["today"] is True
    groups = {g["source"]["name"]: g for g in body["groups"]}
    assert set(groups) == {"Tidy the quarterly reports", "Nightly backup", "In chat", "On a settings page"}
    assert groups["Tidy the quarterly reports"]["source"]["kind"] == "run"
    assert groups["Tidy the quarterly reports"]["source"]["href"] == f"/sessions/{s.run}"
    assert groups["Nightly backup"]["source"]["kind"] == "schedule"
    assert groups["In chat"]["source"]["kind"] == "chat"
    # Newest source first, newest row first, each with its hour on this PC.
    assert [g["source"]["name"] for g in body["groups"]][0] in ("In chat", "On a settings page")
    run_rows = groups["Tidy the quarterly reports"]["rows"]
    assert [r["target"] for r in run_rows] == ["summary.txt", "report.txt"]
    assert all(r["hour"] == 15 and r["clock"].startswith("3:02") for r in run_rows), run_rows
    assert all(r["state"] == "can" and r["has_before_after"] for r in run_rows)
    targets = [r["target"] for g in body["groups"] for r in g["rows"]]
    assert "notes.txt" in targets and "old.txt" not in targets, "yesterday's change is yesterday's"
    settings_rows = groups["On a settings page"]["rows"]
    assert {r["kind"] for r in settings_rows} == {"setting"}
    assert any("chat_followups" in r["what"] for r in settings_rows)
    assert any("connection.openai" in r["what"] for r in settings_rows), "a key is named"
    assert not any(r["what"].startswith("used ") for r in settings_rows), settings_rows
    # What left the PC or ran: its own list, never an Undo, folded by kind.
    cannot = {c["what"]: c for c in body["cannot"]}
    assert set(cannot) == {"sent a notification", "ran 3 commands"}, cannot
    assert all(c["state"] == "cannot" and c["why"] for c in body["cannot"])
    assert "left this PC" in cannot["sent a notification"]["why"]
    assert "command" in cannot["ran 3 commands"]["why"] and cannot["ran 3 commands"]["count"] == 3
    assert cannot["sent a notification"]["source"]["name"] == "Tidy the quarterly reports"
    assert not any(r["id"] == s.ids["sent"] for g in body["groups"] for r in g["rows"])
    # A secret's value never appears.
    assert SECRET not in r.text and SECRET not in json.dumps(body)
    # Yesterday holds yesterday's change only.
    y = s.client.get(f"/changes/day?date={(s.day - timedelta(days=1)).isoformat()}").json()
    assert [r["target"] for g in y["groups"] for r in g["rows"]] == ["old.txt"] and y["cannot"] == []
    assert s.client.get("/changes/day?date=yesterday-ish").status_code == 422


@pytest.mark.asyncio
async def test_before_and_after_a_file_a_setting_and_a_key(tmp_path):
    s = await _seed(tmp_path)
    r = s.client.get(f"/changes/{s.ids['w1']}/before-after")
    assert r.status_code == 200, r.text
    (item,) = r.json()["items"]
    assert item["name"] == "report.txt" and item["text"] is True
    assert "-first draft" in item["diff"] and "+second draft" in item["diff"], item["diff"]
    assert item["before"]["size"] == len("first draft\n") and item["now"]["modified"]
    created = s.client.get(f"/changes/{s.ids['w2']}/before-after").json()["items"][0]
    assert created["before"]["exists"] is False and "+the summary" in created["diff"]
    (setting,) = s.client.get(f"/changes/{s.ids['setting']}/before-after").json()["items"]
    assert setting["name"] == "Suggest follow-up questions"
    assert (setting["before"]["words"], setting["now"]["words"]) == ("off", "on")
    key = s.client.get(f"/changes/{s.ids['key']}/before-after")
    assert SECRET not in key.text
    (k,) = key.json()["items"]
    assert k["name"] == "the key connection.openai" and "never shown" in k["now"]["words"]
    sent = s.client.get(f"/changes/{s.ids['sent']}/before-after").json()
    assert sent["items"] == [] and "no copy" in sent["note"]
    assert s.client.get("/changes/tool_nope/before-after").status_code == 404


@pytest.mark.asyncio
async def test_a_secret_written_into_a_file_is_masked_in_its_difference(tmp_path):
    app, p = _app(tmp_path)
    ws = tmp_path / "ws"
    aid = await _write(p, ws, "chat", "settings.env", f"API_KEY={SECRET}\n")
    r = TestClient(app).get(f"/changes/{aid}/before-after")
    assert r.status_code == 200 and SECRET not in r.text
    assert any(line.startswith("+API_KEY=") for line in r.json()["items"][0]["diff"])


@pytest.mark.asyncio
async def test_a_change_whose_copy_is_gone_says_so_and_offers_no_undo(tmp_path):
    """The risk the plan names: how long before-copies are kept decides how
    far back Undo works. A row whose copy was cleared reads "can't be undone:
    no copy of how it was is kept any more", in the day and in its before."""
    app, p = _app(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    big = "x" * 20_000 + "\n"
    (ws / "big.txt").write_text(big, encoding="utf-8")
    aid = await _write(p, ws, "chat", "big.txt", "small now\n")
    with session_scope(p.engine) as db:
        ref = db.get(UndoJournal, aid).pre_ref
    assert ref, "a big before-copy is kept as a file under undo/"
    client = TestClient(app)
    row = [r for g in client.get("/changes/day?date=today").json()["groups"] for r in g["rows"]][0]
    assert row["state"] == "can"
    (Path(p.config.home) / "undo" / ref).unlink()
    row = [r for g in client.get("/changes/day?date=today").json()["groups"] for r in g["rows"]][0]
    assert row["state"] == "cannot" and row["why"] == H.NO_COPY_KEPT
    item = client.get(f"/changes/{aid}/before-after").json()["items"][0]
    assert item["before"]["kept"] is False


@pytest.mark.asyncio
async def test_a_file_that_is_not_text_shows_its_size_and_time_only(tmp_path):
    app, p = _app(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pic.bin").write_bytes(b"\x00\x01\x02" * 50)
    aid = await _write(p, ws, "chat", "pic.bin", "now text\n")
    (item,) = TestClient(app).get(f"/changes/{aid}/before-after").json()["items"]
    assert item["text"] is False and "diff" not in item and item["before"]["size"] == 150


@pytest.mark.asyncio
async def test_cant_be_undone_says_true_reasons_and_leaves_out_reads_and_bookkeeping(tmp_path):
    """Review (40-a): what stayed on this PC never reads "it left this PC" (a
    line typed into a terminal or a running program), and a read, a status
    check or Iron Jarvis's own team notes are no change at all, so they are
    not under "Can't be undone" (the ledger calls an undeclared tool
    irreversible). A connected app's tool is judged by its first word."""
    app, p = _app(tmp_path)
    day = RD.today_local()
    run = _run(p, "Start the build", at=_local_utc(day, 10, 0), ws=tmp_path / "ws")
    tools = ("pane_send", "process_write", "notify", "blackboard_read", "pixio_status",
             "message_agent", "blackboard_post", "notebook", "assign_work")
    for i, tool in enumerate(tools):
        _sent(p, run, tool, _local_utc(day, 10, 5, i))
    body = TestClient(app).get("/changes/day?date=today").json()
    why = {c["tool"]: c["why"] for c in body["cannot"]}
    assert set(why) == {"pane_send", "process_write", "notify"}, why
    assert "typed into a terminal on this PC" in why["pane_send"], why
    assert "on this PC" in why["process_write"], why
    assert not any("left this PC" in why[t] for t in ("pane_send", "process_write")), why
    assert why["notify"] == RD.SENT_WHY
    kinds = {c["tool"]: c["kind"] for c in body["cannot"]}
    assert kinds == {"pane_send": "ran", "process_write": "ran", "notify": "sent"}, kinds
    assert RD.cannot_why("mcp__slack__send_message") == RD.SENT_WHY
    assert RD.cannot_why("mcp__chat__postMessage") == RD.SENT_WHY
    assert RD.cannot_why("mcp__gmail__search_messages") == RD.KEEPS_NO_UNDO
    assert RD.cannot_why("shell") == RD.COMMAND_WHY


def test_the_day_is_this_pcs_local_day():
    day = RD.parse_day("2026-03-08")
    start, end = RD.day_bounds(day)
    local_start = start.replace(tzinfo=timezone.utc).astimezone()
    assert (local_start.date(), local_start.hour, local_start.minute) == (day, 0, 0)
    assert timedelta(hours=22) <= end - start <= timedelta(hours=26)
    assert RD.parse_day("today") == RD.today_local()
    assert RD.parse_day("2026-02-30") is None and RD.parse_day("next week") is None


def test_a_setting_whose_name_looks_like_a_secret_is_hidden():
    assert RD._value_words("smtp_password", "not-for-the-screen") == "hidden (it looks like a secret)"
    assert RD._value_words("chat_followups", True) == "on" and RD._value_words("x", None) == "not set"
