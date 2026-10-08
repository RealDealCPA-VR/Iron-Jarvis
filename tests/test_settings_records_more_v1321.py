"""Calm UI redesign — the rest of AUDIT §6.3 from chat, each with an Undo, and
"undo that" (the brief's must-work example).

Every test drives the REAL stream lane (``/chat/stream``), the real
permission gate (an ask-tier call pauses on the approval card and is answered
through ``/chat/approvals``) and the real Undo route (``POST /undo/{id}``,
found through the card's ``change_id``). Each Undo is checked against the
record's FULL stored row as it was before the change, not one field.

Records: webhooks (update / delete), sentinels (update / delete), goals
(update / drop — the goal engine keeps no delete, so a drop is the
"abandoned" status), reflex rules (create / update / delete), standing grants
(revoke — allow tier: it only narrows). "Undo that": ``config_undo`` reverses
the newest change the Settings ledger lists, or the one named, through the
same ``perform_undo`` the route runs.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.grants import args_hash
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import records
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done, _undo_card
from tests.test_settings_records_v1321 import _app, _approve_all, _card

PUBLIC = "https://93.184.215.14/hook"  # a literal public address: the SSRF check passes offline
OTHER = "https://93.184.215.15/other"


async def _never(data):  # pragma: no cover
    raise AssertionError(f"this call must not card: {data}")


def _finished(frames, tool):
    return [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished" and d.get("tool", d.get("name")) == tool]


def _ledger(app):
    return TestClient(app).get("/config/ledger").json()["changes"]


# Every Undo is compared against the FULL stored row (every column), read
# straight from the table — never through the snapshot under test, so a field
# the snapshot forgot shows up as a difference here.


def _row(rec):
    return rec.model_dump() if rec is not None else None


def _wh_row(p, slug):
    from sqlmodel import select

    from iron_jarvis.webhooks.models import WebhookRecord

    with session_scope(p.engine) as db:
        return _row(db.exec(select(WebhookRecord).where(WebhookRecord.slug == slug)).first())


# -------------------------------------------------------------- webhooks


@pytest.mark.asyncio
async def test_webhook_update_outbound_asks_applies_and_undo_restores_every_field(tmp_path):
    app = _app(
        tmp_path,
        [("webhook_update", {"slug": "deploys", "target_url": OTHER, "event_types": ["session.failed"], "enabled": False})],
    )
    p = app.state.platform
    p.outbound_webhooks.register("deploys", PUBLIC, ["session.completed"], secret_name="hook_key")
    before = _wh_row(p, "deploys")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("send the deploys webhook somewhere else"), await _approve_all(app, seen))
    assert seen == ["webhook_update"], "a webhook change must ask first"
    now = _wh_row(p, "deploys")
    assert now["target_url"] == OTHER and json.loads(now["event_types_json"]) == ["session.failed"] and now["enabled"] is False
    card = _card(frames, "Webhook changed")
    assert "(paused)" in card["new"]
    await _undo_card(app, card)
    assert _wh_row(p, "deploys") == before


@pytest.mark.asyncio
async def test_webhook_pause_inbound_stops_dispatch_and_undo_rearms_it(tmp_path):
    app = _app(tmp_path, [("webhook_update", {"slug": "gh", "enabled": False})])
    p = app.state.platform

    async def handler(body):
        return {"ok": True}

    p.inbound_webhooks.register("gh", handler)
    before = _wh_row(p, "gh")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("pause the gh webhook"), await _approve_all(app, seen))
    assert seen == ["webhook_update"]
    assert _wh_row(p, "gh")["enabled"] is False
    assert (await p.inbound_webhooks.dispatch("gh", {}))["rejected"] == "unknown_slug"
    await _undo_card(app, _card(frames, "Webhook changed"))
    assert _wh_row(p, "gh") == before
    assert (await p.inbound_webhooks.dispatch("gh", {}))["ok"] is True


@pytest.mark.asyncio
async def test_webhook_delete_undo_recreates_the_same_rows(tmp_path):
    app = _app(tmp_path, [("webhook_delete", {"slug": "deploys"}), ("webhook_delete", {"slug": "gh"})])
    p = app.state.platform
    p.outbound_webhooks.register("deploys", PUBLIC, ["session.completed", "session.failed"])

    async def handler(body):
        return {"ok": True}

    p.inbound_webhooks.register("gh", handler, secret_name="")
    out_before, in_before = _wh_row(p, "deploys"), _wh_row(p, "gh")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("delete both webhooks"), await _approve_all(app, seen))
    assert seen == ["webhook_delete", "webhook_delete"]
    assert _wh_row(p, "deploys") is None and _wh_row(p, "gh") is None
    cards = [c for c in _done(frames)["config_cards"] if c.get("title") == "Webhook removed"]
    assert len(cards) == 2
    for c in cards:
        await _undo_card(app, c)
    assert _wh_row(p, "deploys") == out_before
    assert _wh_row(p, "gh") == in_before
    assert (await p.inbound_webhooks.dispatch("gh", {}))["ok"] is True, "the inbound handler is live again"


# ------------------------------------------------------------- sentinels


def _seed_memory(p, name, state):
    from sqlmodel import select

    from iron_jarvis.sentinels.models import SentinelRecord

    with session_scope(p.engine) as db:
        row = db.exec(select(SentinelRecord).where(SentinelRecord.name == name)).first()
        row.last_state_json = json.dumps(state)
        db.add(row)
        db.commit()


@pytest.mark.asyncio
async def test_sentinel_pause_asks_and_undo_resumes_it(tmp_path):
    app = _app(tmp_path, [("sentinel_update", {"name": "downloads", "enabled": False})])
    p = app.state.platform
    p.sentinels.add("downloads", path=str(tmp_path), glob="*.pdf", task="file the new PDFs", risk="med")
    before = _row(p.sentinels.get("downloads"))
    seen: list[str] = []
    frames = await _drive_stream(app, _body("pause my downloads watcher"), await _approve_all(app, seen))
    assert seen == ["sentinel_update"]
    assert p.sentinels.get("downloads").enabled is False
    await _undo_card(app, _card(frames, "Sentinel changed"))
    assert _row(p.sentinels.get("downloads")) == before


@pytest.mark.asyncio
async def test_sentinel_delete_undo_recreates_it_with_its_memory(tmp_path):
    app = _app(tmp_path, [("sentinel_delete", {"name": "downloads"})])
    p = app.state.platform
    p.sentinels.add("downloads", path=str(tmp_path), task="file the new PDFs", enabled=False)
    _seed_memory(p, "downloads", {"seen": {"a.pdf": 1.5}})
    before = _row(p.sentinels.get("downloads"))
    seen: list[str] = []
    frames = await _drive_stream(app, _body("delete the downloads sentinel"), await _approve_all(app, seen))
    assert seen == ["sentinel_delete"] and p.sentinels.get("downloads") is None
    await _undo_card(app, _card(frames, "Sentinel removed"))
    after = _row(p.sentinels.get("downloads"))
    assert after == before
    assert json.loads(after["last_state_json"]) == {"seen": {"a.pdf": 1.5}}, "it must not re-fire for what it had seen"


# ----------------------------------------------------------------- goals


@pytest.mark.asyncio
async def test_goal_update_by_its_words_asks_and_undo_restores_the_goal(tmp_path):
    app = _app(
        tmp_path,
        [("goal_update", {"goal": "Keep my inbox under 20 unread", "status": "paused", "priority": 5, "autonomy_level": "act_low", "spend_budget": 900})],
    )
    p = app.state.platform
    g = p.intent.add_goal("keep my inbox under 20 unread", priority=2, category="mail")
    before = _row(p.intent.get_goal(g.id))
    seen: list[str] = []
    frames = await _drive_stream(app, _body("pause my inbox goal"), await _approve_all(app, seen))
    assert seen == ["goal_update"]
    rec = p.intent.get_goal(g.id)
    assert (rec.status, rec.priority, rec.autonomy_level, rec.spend_budget) == ("paused", 5, "act_low", 900)
    await _undo_card(app, _card(frames, "Goal changed"))
    assert _row(p.intent.get_goal(g.id)) == before


@pytest.mark.asyncio
async def test_goal_drop_marks_it_abandoned_and_undo_gives_its_status_back(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    g = p.intent.add_goal("ship the v2 docs")
    p.intent.update_goal(g.id, status="paused")
    before = _row(p.intent.get_goal(g.id))
    p.router.stream = _calls_stream([("goal_delete", {"goal": g.id})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("drop the docs goal"), await _approve_all(app, seen))
    assert seen == ["goal_delete"]
    assert p.intent.get_goal(g.id).status == "abandoned"
    await _undo_card(app, _card(frames, "Goal dropped"))
    assert _row(p.intent.get_goal(g.id)) == before


# ---------------------------------------------------------- reflex rules


@pytest.mark.asyncio
async def test_reflex_create_asks_and_undo_removes_the_rule(tmp_path):
    app = _app(
        tmp_path,
        [("reflex_create", {"name": "1099 intake", "source": "email", "match": "1099", "action": "session", "task_template": "file {text}"})],
    )
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("when an email about a 1099 happens, start a session"), await _approve_all(app, seen))
    assert seen == ["reflex_create"]
    (rule,) = p.reflex.list()
    assert (rule.name, rule.source, rule.match, rule.action, rule.task_template) == ("1099 intake", "email", "1099", "session", "file {text}")
    card = _card(frames, "Rule created")
    assert card["label"] == "Rule “1099 intake”"
    await _undo_card(app, card)
    assert p.reflex.list() == []


@pytest.mark.asyncio
async def test_reflex_create_refuses_what_the_route_refuses_with_its_words(tmp_path):
    app = _app(tmp_path, [("reflex_create", {"source": "webhook", "action": "workflow", "target": "Brief"})])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("make a reflex rule for my webhook"), await _approve_all(app, seen))
    assert p.reflex.list() == [] and _done(frames)["config_cards"] == []
    (fin,) = _finished(frames, "reflex_create")
    route = TestClient(app).post("/reflex/rules", json={"source": "webhook", "action": "workflow", "target": "Brief"})
    assert route.status_code == 400
    assert route.json()["detail"] in (fin.get("error") or fin.get("output") or "")


@pytest.mark.parametrize(
    "body",
    [
        {"source": "fax", "action": "session"},
        {"source": "comm", "action": "teleport"},
        {"source": "webhook", "match": " ", "action": "session"},
        {"source": "comm", "action": "remote_agent", "target": ""},
        {"source": "comm", "action": "workflow"},
    ],
)
def test_reflex_validation_is_the_routes_word_for_word(tmp_path, body):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/reflex/rules", json=body)
    assert r.status_code == 400
    full = {"match": "", "target": "", **body}
    assert records.validate_reflex(full["source"], full["match"], full["action"], full["target"]) == r.json()["detail"]
    assert records.validate_reflex("comm", "", "session", "") == ""


@pytest.mark.asyncio
async def test_reflex_update_and_delete_each_undo_exactly(tmp_path):
    app = _app(
        tmp_path,
        [
            ("reflex_update", {"rule": "nightly", "enabled": False, "project_id": "proj_x"}),
            ("reflex_delete", {"rule": "fax rule"}),
        ],
    )
    p = app.state.platform
    a = p.reflex.add(name="nightly", source="webhook", match="gh", action="workflow", target="Brief")
    b = p.reflex.add(name="fax rule", source="comm", match="invoice", action="session", task_template="t {text}", project_id="proj_y")
    p.reflex.mark_result(b.id, ok=True, detail="session:s1")
    a_before, b_before = _row(p.reflex.get(a.id)), _row(p.reflex.get(b.id))
    assert b_before["fire_count"] == 1
    seen: list[str] = []
    frames = await _drive_stream(app, _body("turn off the nightly rule and delete the fax rule"), await _approve_all(app, seen))
    assert seen == ["reflex_update", "reflex_delete"]
    rule = p.reflex.get(a.id)
    assert rule.enabled is False and rule.project_id == "proj_x"
    assert p.reflex.get(b.id) is None
    await _undo_card(app, _card(frames, "Rule changed"))
    await _undo_card(app, _card(frames, "Rule removed"))
    assert _row(p.reflex.get(a.id)) == a_before
    assert _row(p.reflex.get(b.id)) == b_before


# ------------------------------------------------------- standing grants


@pytest.mark.asyncio
async def test_grant_revoke_runs_without_a_card_and_undo_puts_the_grant_back(tmp_path):
    app = _app(tmp_path, [("grant_revoke", {"grant": "shell"})])
    p = app.state.platform
    git = {"command": "git status"}
    rec = p.grants.create("chat", "chat", "shell", args_hash("shell", git), "shell git status")
    before = _row(p.grants.get(rec.id))
    frames = await _drive_stream(app, _body("stop always allowing shell"), _never)
    assert p.grants.match([("chat", "chat")], "shell", git, touch=False) is None
    card = _card(frames, "Always-allow taken back")
    assert card["label"] == "shell git status"
    await _undo_card(app, card)
    assert _row(p.grants.get(rec.id)) == before
    hit = p.grants.match([("chat", "chat")], "shell", git, touch=False)
    assert hit is not None and hit.id == rec.id, "the next identical call is covered again"


def test_grant_revoke_names_every_match_when_a_tool_has_several(tmp_path):
    p = create_app(str(tmp_path)).state.platform
    one = p.grants.create("chat", "chat", "shell", args_hash("shell", {"command": "ls"}), "shell ls")
    two = p.grants.create("chat", "chat", "shell", args_hash("shell", {"command": "pwd"}), "shell pwd")
    gid, problem = records.resolve_grant(p, "shell")
    assert gid == "" and one.id in problem and two.id in problem
    assert records.resolve_grant(p, two.id) == (two.id, "")


# --------------------------------------------------------------- undo that


@pytest.mark.asyncio
async def test_undo_that_reverses_the_last_change_through_the_undo_route(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    cfg = p.config
    cfg.autonomy_dry_run = False
    p.router.stream = _calls_stream([("config_set", {"key": "autonomy_dry_run", "value": "on"})])
    await _drive_stream(app, _body("turn on dry run mode for autonomy"), _never)
    assert cfg.autonomy_dry_run is True
    (row,) = [c for c in _ledger(app) if c["tool"] == "config_set"]
    assert row["undoable"] and not row["undone"]

    p.router.stream = _calls_stream([("config_undo", {})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("undo that"), await _approve_all(app, seen))
    assert seen == ["config_undo"], "an undo is a change: it asks"
    assert cfg.autonomy_dry_run is False
    (row,) = [c for c in _ledger(app) if c["action_id"] == row["action_id"]]
    assert row["undone"] is True and row["undoable"] is False
    (fin,) = _finished(frames, "config_undo")
    assert fin["ok"] is True
    assert _done(frames)["config_cards"] == [], "an undo is not itself undoable here: no card"


@pytest.mark.asyncio
async def test_undo_by_action_id_reverses_that_change_and_not_the_newest(tmp_path):
    app = _app(tmp_path, [("schedule_delete", {"name": "weekly"}), ("schedule_delete", {"name": "nightly"})])
    p = app.state.platform
    sch = p.scheduler
    sch.add_task("weekly", "0 9 * * 1", kind="workflow", payload={"workflow": "report"})
    sch.add_task("nightly", "0 2 * * *", kind="workflow", payload={"workflow": "brief"})
    seen: list[str] = []
    await _drive_stream(app, _body("delete the weekly and nightly schedules"), await _approve_all(app, seen))
    assert sch.get("weekly") is None and sch.get("nightly") is None
    weekly = next(c for c in _ledger(app) if c["tool"] == "schedule_delete" and "weekly" in c["summary"])

    p.router.stream = _calls_stream([("config_undo", {"action_id": weekly["action_id"]})])
    seen.clear()
    await _drive_stream(app, _body("undo it"), await _approve_all(app, seen))
    assert seen == ["config_undo"]
    assert sch.get("weekly") is not None, "the named change was undone"
    assert sch.get("nightly") is None, "the newer one was left alone"
    rows = {c["action_id"]: c for c in _ledger(app)}
    assert rows[weekly["action_id"]]["undone"] is True


@pytest.mark.asyncio
async def test_undo_with_nothing_to_undo_says_so_plainly(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream([("config_undo", {})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("undo my last change"), await _approve_all(app, seen))
    assert seen == ["config_undo"]
    (fin,) = _finished(frames, "config_undo")
    assert fin["ok"] is False
    assert "There is no recent change that can still be undone." in (fin.get("error") or fin.get("output") or "")


# ---------------------------------------------------------------- arming


def test_the_new_record_tools_arm_by_the_record_the_message_names():
    rf = records.record_tools_for
    assert rf("pause the deploys webhook") == ["webhook_update", "webhook_delete"]
    assert rf("turn off my downloads watcher") == ["sentinel_update", "sentinel_delete"]
    assert rf("pause my inbox goal") == ["goal_list", "goal_update", "goal_delete"]
    assert rf("delete that reflex") == ["reflex_create", "reflex_update", "reflex_delete"]
    assert "reflex_create" in rf("when a client email happens, start an intake session")
    assert rf("stop always allowing shell") == ["grant_revoke"]
    assert rf("undo that") == ["config_undo"]
    assert rf("Undo the last change please") == ["config_undo"]
    # Negative control: ordinary work arms none of them.
    assert rf("summarize the K-1 I attached") == []
    assert rf("what should I do about the undoing of this contract?") == []


@pytest.mark.asyncio
async def test_the_stream_lane_shows_them_and_the_gate_keeps_their_tiers(tmp_path):
    from iron_jarvis.core.trust import LOW_TRUST_DENY

    app = create_app(str(tmp_path))
    fake = _calls_stream([])
    app.state.platform.router.stream = fake
    await _drive_stream(app, _body("undo that, then pause the gh webhook"), _never)
    assert {"config_undo", "webhook_update", "webhook_delete"} <= set(fake.tools[0])
    perms = app.state.platform.permissions
    for name, tier in {**records.RECORD_TOOL_TIERS, **records.CONFIG_UNDO_TIERS}.items():
        assert app.state.platform.registry.get(name) is not None, name
        assert perms.authorize(name, {}).allowed is (tier == "allow"), name
    assert perms.authorize("grant_revoke", {}).allowed is True
    ask_tier = {n for n, t in {**records.RECORD_TOOL_TIERS, **records.CONFIG_UNDO_TIERS}.items() if t == "ask"}
    assert ask_tier <= LOW_TRUST_DENY
    assert "grant_revoke" not in LOW_TRUST_DENY, "taking an always-allow away only narrows"
    assert {"webhook_add", "sentinel_add", "goal_add"} <= records.RECORD_CARD_TOOLS
    assert "config_undo" not in records.RECORD_CARD_TOOLS, "an undo is not on the ledger as a change"


# ------------------------------------------- the create tools gain an Undo


@pytest.mark.asyncio
async def test_webhook_add_from_chat_has_an_undo_and_a_reused_slug_comes_back(tmp_path):
    app = _app(
        tmp_path,
        [
            ("webhook_add", {"slug": "ingest"}),
            ("webhook_add", {"slug": "deploys", "direction": "outbound", "target_url": OTHER, "event_types": ["session.failed"]}),
        ],
    )
    p = app.state.platform
    p.outbound_webhooks.register("deploys", PUBLIC, ["session.completed"])
    deploys_before = _wh_row(p, "deploys")
    frames = await _drive_stream(app, {**_body("add an ingest webhook and repoint deploys"), "tools": ["webhook_add"]}, _never)
    assert _wh_row(p, "ingest") is not None
    assert (await p.inbound_webhooks.dispatch("ingest", {}))["ok"] is True
    assert _wh_row(p, "deploys")["target_url"] == OTHER
    cards = [c for c in _done(frames)["config_cards"] if c.get("title") == "Webhook created"]
    assert [c["key"] for c in cards] == ["webhook.ingest", "webhook.deploys"]
    for c in cards:
        await _undo_card(app, c)
    assert _wh_row(p, "ingest") is None
    assert (await p.inbound_webhooks.dispatch("ingest", {}))["rejected"] == "unknown_slug"
    assert _wh_row(p, "deploys") == deploys_before


@pytest.mark.asyncio
async def test_sentinel_add_from_chat_has_an_undo(tmp_path):
    app = _app(tmp_path, [("sentinel_add", {"name": "inbox", "path": str(tmp_path)})])
    p = app.state.platform
    frames = await _drive_stream(app, {**_body("watch this folder"), "tools": ["sentinel_add"]}, _never)
    assert p.sentinels.get("inbox") is not None
    await _undo_card(app, _card(frames, "Sentinel created"))
    assert p.sentinels.get("inbox") is None


@pytest.mark.asyncio
async def test_goal_add_from_chat_has_an_undo_named_after_the_write(tmp_path):
    app = _app(tmp_path, [("goal_add", {"text": "close the books by the 5th", "priority": 4})])
    p = app.state.platform
    frames = await _drive_stream(app, {**_body("remember this goal"), "tools": ["goal_add"]}, _never)
    (goal,) = p.intent.list_goals()
    card = _card(frames, "Goal created")
    assert card["key"] == f"goal.{goal.id}" and card["label"] == "Goal “close the books by the 5th”"
    await _undo_card(app, card)
    assert p.intent.list_goals() == []


@pytest.mark.asyncio
async def test_a_goal_the_autonomy_loop_already_used_is_abandoned_not_deleted(tmp_path):
    from iron_jarvis.motivation.models import ProposalRecord

    app = _app(tmp_path, [("goal_add", {"text": "tidy the downloads folder"})])
    p = app.state.platform
    frames = await _drive_stream(app, {**_body("remember this goal"), "tools": ["goal_add"]}, _never)
    (goal,) = p.intent.list_goals()
    with session_scope(p.engine) as db:
        db.add(ProposalRecord(goal_id=goal.id, title="sort files"))
        db.commit()
    await _undo_card(app, _card(frames, "Goal created"))
    assert p.intent.get_goal(goal.id).status == "abandoned"


@pytest.mark.asyncio
async def test_undo_that_takes_the_NEWEST_change_and_then_the_one_before_it(tmp_path):
    """Two changes: "undo that" reverses the later one only; said again, it
    skips the change already undone and reverses the earlier one."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    cfg = p.config
    cfg.autonomy_dry_run = False
    before_steps = cfg.max_agent_steps
    p.router.stream = _calls_stream([("config_set", {"key": "autonomy_dry_run", "value": "on"})])
    await _drive_stream(app, _body("turn on dry run mode for autonomy"), _never)
    p.router.stream = _calls_stream([("config_change", {"key": "max_agent_steps", "value": "37"})])
    seen: list[str] = []
    await _drive_stream(app, _body("change the max steps setting to 37"), await _approve_all(app, seen))
    assert cfg.autonomy_dry_run is True and cfg.max_agent_steps == 37

    p.router.stream = _calls_stream([("config_undo", {})])
    await _drive_stream(app, _body("undo that"), await _approve_all(app, seen))
    assert cfg.max_agent_steps == before_steps, "the newest change is the one undone"
    assert cfg.autonomy_dry_run is True, "the earlier change is untouched"

    p.router.stream = _calls_stream([("config_undo", {})])
    await _drive_stream(app, _body("undo that"), await _approve_all(app, seen))
    assert cfg.autonomy_dry_run is False, "an already-undone change is skipped; the one before it goes"
    assert cfg.max_agent_steps == before_steps
