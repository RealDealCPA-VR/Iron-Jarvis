"""v1.351.0 integrate — Wave 1's seams, each joined ONCE through real code.

The five lanes pinned their own halves against shapes they wrote by hand.
This file joins them, and pins what the integrator added on top:

* THE CONTRACT FIXTURE — ``dashboard/__tests__/fixtures/wave1-seams-v13510.json``
  holds what the REAL daemon sends; ``dashboard/__tests__/wave1-seams-
  v13510.test.tsx`` feeds the same file through the real bell and the
  schedule words. A change to either side's shape fails one of the two.

  - ``chat_waiting_row``: G05 (lane 1C) lists a chat parked on the user with
    nobody watching on ``GET /chat/approvals/pending``; G03 (lane 1B) shows it
    in the bell, and the desktop watcher (``desktop/main.js``) toasts it.
  - ``run_verdicts``: G01 (lane 1A) writes a schedule row's ``last_status``
    from the run's ledger verdict; G02 counts the short ones (Status map,
    ``/ui/status-line``) and G03's bell row reads the SAME run's
    ``session.completed`` payload. One vocabulary: each case below is a real
    fire, and the row, the count and the payload are read off it.

* G14 (lane 1C): both chat lanes answer the same ``route`` shape, with
  ``on_this_network`` last.
* G18 (lane 1B): ``GET /sessions/{id}/result`` carries the receipt keys, and
  the dashboard's ``RunResult`` type names each of them (wave 2 renders them).
* The integrator's Emergency-stop doors (G13): a queued job is HELD, never
  failed; a goal's Run now is an honest refusal; Continue on a schedule-made
  run is a 409 sentence; the phone and a desktop reply into a phone thread
  hear the sentence instead of silence or a 500.
* The integrator's other notes: an agent run's summary after a user-made tool
  is not accused (G06 on the agent path); a mission whose worklist kept failed
  items is "completed_with_failures" (G09 — the bell and the screen agree);
  the Directory's Connect vaults an argument secret and its Disconnect removes
  it (G12); ``app_connect`` on a connected app names how to replace a token.

Regenerate the fixture after an INTENDED shape change:
``IJ_REGEN_WAVE1_SEAMS=1 uv run pytest tests/test_wave1_seams_v13510.py`` and
commit it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import (
    EMERGENCY_STOP_REFUSAL,
    EmergencyStopOn,
    Orchestrator,
)
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentState, AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import run_outcome as ro
from tests.test_desktop_reliability_v1249 import _lift, _run, requires_node
from tests.test_schedule_outcomes_v13510 import (
    _add_task,
    _expired_ask,
    _failed_write,
    _fake_run,
    _plant_on_create,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "wave1-seams-v13510.json"
REGEN = os.environ.get("IJ_REGEN_WAVE1_SEAMS") == "1"
#: The fixture's stand-in for the row's live timestamp (byte-stable file).
STAMP = "2026-10-18T09:00:00Z"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}


def _hold(key: str, real) -> None:
    """Compare ``real`` with the fixture's ``key`` — or write it (REGEN)."""
    data = _fixture()
    if REGEN:
        data[key] = real
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert key in data, f"run IJ_REGEN_WAVE1_SEAMS=1 once: the fixture has no {key!r}"
    assert real == data[key], (
        f"the daemon no longer sends what {FIXTURE.name}[{key!r}] says — if the "
        "change is intended, regenerate the fixture and update the dashboard side"
    )


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# (a) G05 -> G03: the waiting-chat row the route lists is the one the bell and
#     the desktop watcher read.
# --------------------------------------------------------------------------- #
def _waiting_chat_row(tmp_path) -> dict:
    """The REAL route's row for a chat parked on the user (an app asked it
    something) with no window following its turn — lane 1C's no-card door."""
    from iron_jarvis.core.turns import TURNS
    from iron_jarvis.daemon import durable_turns as dt

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    logs = dt.TurnLogs(platform.engine)
    platform.turn_logs = logs
    logs._logs["turn-seam"] = dt.TurnLog("turn-seam", "th-seam")
    entry = TURNS.new_thread_turn("th-seam")
    TURNS.enter_thread(entry)
    entry.park()
    try:
        r = client.get("/chat/approvals/pending")
        assert r.status_code == 200, r.text
        rows = [x for x in r.json()["approvals"] if x.get("kind") == "chat"]
    finally:
        TURNS.leave_thread(entry)
    assert len(rows) == 1, rows
    return rows[0]


def test_the_waiting_chat_row_is_the_shape_the_bell_reads(tmp_path):
    row = _waiting_chat_row(tmp_path)
    assert row["requested_at"].endswith("Z"), row
    _hold("chat_waiting_row", {**row, "requested_at": STAMP})
    # The bell's parser reads exactly these keys (dashboard side pinned by the
    # vitest against this same fixture): kind "chat", id, thread_id.
    bell = _source("dashboard/components/NotificationBell.tsx")
    assert 'if (r.kind !== "chat") return null;' in bell
    assert "r.thread_id" in bell


_PLAN_HARNESS = """
let tray = { setToolTip: () => {} };
const _trayDegraded = new Set();
__LIFTED__
const rows = JSON.parse(process.argv[2]);
const plan = planAskNotifications({}, rows, 0, false);
process.stdout.write(JSON.stringify(plan) + "\\n");
"""


@requires_node
def test_the_desktop_watcher_toasts_the_real_rows_waiting_chat(tmp_path):
    """The REAL route's row, as it is (live timestamp and all), through the
    desktop watcher lifted from main.js: one toast in the chat's words that
    opens THAT chat — never a job page."""
    row = _waiting_chat_row(tmp_path / "home")
    script = _PLAN_HARNESS.replace(
        "__LIFTED__", _lift("const ASK_WATCH_MS", "function planAskNotifications")
    )
    plan = _run(script, tmp_path, "seam13510", json.dumps([row]))
    assert plan["waiting"] == 1
    (toast,) = plan["notify"]
    assert toast["title"] == "A chat is waiting for your OK"
    assert toast["chat"] is True and toast["threadId"] == "th-seam"
    assert toast["sessionId"] == ""
    assert toast["body"] == "Click to open the chat."


# --------------------------------------------------------------------------- #
# (b) G01 -> G02/G03: one verdict vocabulary, read off real fires.
# --------------------------------------------------------------------------- #
#: (case name, what the ledger holds before the run, how the AGENT ends).
_CASES = [
    ("seam-clean", None, None),
    ("seam-needs-you", _expired_ask, None),
    ("seam-part-failed", _failed_write, None),
    ("seam-failed-asked", _expired_ask, (AgentState.FAILED, "Stopped at a folder it may not open.")),
    ("seam-failed", None, (AgentState.FAILED, "Could not reach the file server.")),
]
#: The ``session.completed`` keys the bell's verdict reads (runVerdict +
#: unattendedDoor): exactly what the daemon sent, absent keys left absent.
_BELL_KEYS = ("origin", "status", "ok", "outcome")


def _fire(tmp_path, monkeypatch, name, plant, agent) -> dict:
    app = create_app(str(tmp_path / name))
    client = TestClient(app)
    platform = app.state.platform
    if plant is not None:
        _plant_on_create(platform, "schedule:", plant)
    if agent is not None:
        monkeypatch.setattr(platform.orchestrator.runtime, "run", _fake_run(*agent))
    completed: list[dict] = []

    def _capture(ev) -> None:
        if ev.type == EventType.SESSION_COMPLETED:
            completed.append({"session_id": ev.session_id, "payload": dict(ev.payload or {})})

    platform.event_bus.add_handler(_capture)
    _add_task(client, name)
    row = client.post(f"/schedules/{name}/run").json()
    sid = row["last_session_id"]
    assert sid, row
    mine = [c["payload"] for c in completed if c["session_id"] == sid]
    assert len(mine) == 1, completed
    payload = mine[0]
    short = row["last_status"] in ro.SHORT_STATUSES
    # G02 reads the same row: the home line's count and the Status map star.
    assert client.get("/ui/status-line").json()["short_schedules"] == (1 if short else 0)
    nodes = {
        n["key"]: n
        for area in client.get("/ui/system-map").json()["areas"]
        for n in area["nodes"]
    }
    assert (nodes["schedules"]["attention"] is not None) is short, nodes["schedules"]
    return {
        "case": name,
        "completion": {k: payload[k] for k in _BELL_KEYS if k in payload},
        "last_status": row["last_status"],
        "short": short,
    }


def test_one_verdict_from_a_real_fire_reaches_the_row_the_count_and_the_bell(tmp_path, monkeypatch):
    real = [_fire(tmp_path, monkeypatch, *case) for case in _CASES]
    # The vocabulary, said once here so a reader of the fixture need not run it.
    assert [(r["case"], r["last_status"], r["short"]) for r in real] == [
        ("seam-clean", "ok", False),
        ("seam-needs-you", "needs_you", True),
        ("seam-part-failed", "partial", True),
        ("seam-failed-asked", "needs_you", True),
        ("seam-failed", "error", True),
    ]
    _hold("run_verdicts", real)


def test_the_dashboards_short_list_is_the_daemons():
    """``lib/scheduleOutcome.SHORT_SCHEDULE_STATUSES`` is the twin of
    ``run_outcome.SHORT_STATUSES`` (the legacy "failed" included), and every
    value the daemon writes has its own words on the dashboard."""
    src = _source("dashboard/lib/scheduleOutcome.ts")
    m = re.search(r"SHORT_SCHEDULE_STATUSES: readonly string\[\] = \[([^\]]*)\]", src)
    assert m, "SHORT_SCHEDULE_STATUSES is gone from lib/scheduleOutcome.ts"
    ts_short = set(re.findall(r'"([a-z_]+)"', m.group(1)))
    assert ts_short == set(ro.SHORT_STATUSES)
    words = src[src.index("const WORDS"): src.index("};", src.index("const WORDS"))]
    written = {ro.STATUS_OK, ro.STATUS_ERROR, ro.STATUS_NEEDS_YOU, ro.STATUS_PARTIAL,
               ro.STATUS_CANCELLED, ro.STATUS_SKIPPED, "missed", *ro.FAILED_STATUSES}
    for status in written:
        assert re.search(rf"^\s+{status}: \{{ label:", words, re.M), status


# --------------------------------------------------------------------------- #
# (c) G18: the receipt keys reach the dashboard's type.
# --------------------------------------------------------------------------- #
_RECEIPT_KEYS = ("provider", "model", "agent_name", "agent_type", "route_note", "on_this_network", "activity")


def test_the_session_receipt_keys_are_named_on_the_dashboard(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _add_task(client, "seam-receipt")
    sid = client.post("/schedules/seam-receipt/run").json()["last_session_id"]
    result = client.get(f"/sessions/{sid}/result").json()
    assert result["found"] is True
    for key in _RECEIPT_KEYS:
        assert key in result, key
    assert result["provider"] == "mock" and result["on_this_network"] is True
    assert isinstance(result["activity"], list) and result["route_note"] == ""
    card = _source("dashboard/components/chat/RunResultCard.tsx")
    body = card[card.index("export interface RunResult {"): card.index("\n}\n", card.index("export interface RunResult {"))]
    for key in _RECEIPT_KEYS:
        assert re.search(rf"^\s+{key}\?:", body, re.M), f"RunResult does not name {key}"


# --------------------------------------------------------------------------- #
# (d) G14: both chat lanes answer the same route shape.
# --------------------------------------------------------------------------- #
def test_both_chat_lanes_answer_the_same_route_shape(tmp_path):
    from tests.test_chat_lanes_v13510 import _stream_done

    body = {"messages": [{"role": "user", "content": "hello there"}]}
    with TestClient(create_app(str(tmp_path))) as client:
        post = client.post("/chat", json=body).json()["route"]
        stream = _stream_done(client, body)["route"]
    # The same keys (where "reasoning" sits differs, as it did before this
    # wave), and the egress flags — on_this_network the new one — last, in
    # the same order, with the same values: one rule, two lanes.
    assert set(post) == set(stream), (list(post), list(stream))
    tail = ["local_only", "allowed_once", "on_this_network"]
    assert list(post)[-3:] == list(stream)[-3:] == tail
    assert all(post[k] == stream[k] for k in tail), (post, stream)
    assert post["on_this_network"] is True, "the offline demo runs on this PC"


# --------------------------------------------------------------------------- #
# Emergency stop doors the integrator closed (G13).
# --------------------------------------------------------------------------- #
async def test_a_queued_job_is_held_with_the_sentence_never_failed(tmp_path):
    from iron_jarvis.assignments import AssignmentDispatcher
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    platform.orchestrator = Orchestrator(platform)
    dispatcher = AssignmentDispatcher(platform, interval_s=0.05)
    platform.assignment_dispatcher = dispatcher
    rec, _ = platform.assignments.create("builder", "rename the scans")

    platform.config.autonomy_kill_switch = True
    result = await dispatcher.tick()
    assert result["started"] == [] and result["held"] == 1, result
    row = platform.assignments.get(rec.id)
    assert row.status == "queued", row.status
    assert row.held_reason == EMERGENCY_STOP_REFUSAL
    assert int(row.failure_count or 0) == 0, "Emergency stop struck the job"
    assert platform.orchestrator.list_sessions() == [], "a session was created"

    platform.config.autonomy_kill_switch = False
    assert (await dispatcher.tick())["started"] == [rec.id], "the job did not start once the switch was off"
    await dispatcher.drain()


def test_a_goals_run_now_is_an_honest_refusal_not_a_500(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post(
            "/goals",
            json={
                "name": "inbox zero",
                "contract_text": "Create a file summarizing the task.",
                "budget": {"max_tokens": 50_000},
            },
        )
        assert r.status_code == 200, r.text
        gid = r.json()["goal"]["id"]
        client.app.state.platform.config.autonomy_kill_switch = True
        out = client.post(f"/goals/{gid}/run")
        assert out.status_code == 200, out.text
        body = out.json()
        assert body["ok"] is False and body["refused"] is True, body
        assert body["reason"] == EMERGENCY_STOP_REFUSAL
        assert body["state"] == "active", "the refusal must not trip or pause the goal"
        assert client.app.state.platform.orchestrator.list_sessions() == []


def test_a_spawn_that_would_start_unattended_work_answers_the_sentence_as_a_409(tmp_path):
    """A route door with no RuntimeError branch of its own (``POST
    /agents/{name}/spawn`` takes the caller's origin): the app's handler
    answers the switch's sentence as a 409 — never "internal error [err_…]:
    EmergencyStopOn: …"."""
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.config.autonomy_kill_switch = True
    r = client.post("/agents/builder/spawn", json={"task": "tidy the scans", "origin": "reflex:seam"})
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": EMERGENCY_STOP_REFUSAL, "code": "emergency_stop"}
    assert "EmergencyStopOn" not in r.text and "err_" not in r.text


def test_a_person_continuing_a_schedule_made_run_is_not_refused(tmp_path):
    """Continue is a PERSON on the run's page ("work you start keeps going"):
    ``continue_session`` builds its row itself (it never passes the
    unattended door), so the switch leaves it alone — the decision, pinned."""
    client = TestClient(create_app(str(tmp_path)))
    _add_task(client, "seam-continue")
    sid = client.post("/schedules/seam-continue/run").json()["last_session_id"]
    client.app.state.platform.config.autonomy_kill_switch = True
    r = client.post(f"/sessions/{sid}/continue", json={"message": "and the rest"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


async def test_the_phone_hears_the_sentence_instead_of_silence(platform):
    from tests.test_comm_inbound import FakeTelegram, _poller, _telegram, _update

    fake = FakeTelegram([_update(10, sender=777, text="do the thing")])
    ch = _telegram(fake, {"token_secret": "tg_token", "inbound_enabled": True, "allowed_senders": [777]})
    poller, orch = _poller(platform, ch)
    platform.config.autonomy_kill_switch = True

    results = await poller.poll_once()
    await poller.drain()

    assert [r["status"] for r in results] == ["emergency_stop"], results
    assert orch.list_sessions() == []
    assert [p["text"] for p in fake.sent] == [f"{poller.reply_prefix}{EMERGENCY_STOP_REFUSAL}"]


async def test_a_phone_chat_hand_off_is_refused_before_the_ack(platform):
    from iron_jarvis.comm.inbound import ESCALATE_ACK
    from tests.test_comm_full_chat import CHAT_CFG, ChatMockChannel, _chat_poller, _msg

    async def escalating(platform_, personas, body):
        return {"reply": "this needs an agent", "provider": "mock", "model": "m",
                "tools_used": [], "escalate": True, "escalate_reason": "multi-step"}

    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, orch, store = _chat_poller(platform, ch, escalating)
    platform.config.autonomy_kill_switch = True

    res = await poller._handle("tg", ch, _msg("build the report", update_id=1))
    await poller.drain()

    assert res["status"] == "chat_refused" and res["reason"] == EMERGENCY_STOP_REFUSAL, res
    assert orch.list_sessions() == []
    assert not any(ESCALATE_ACK in s for s in ch.sent), "the phone heard 'On it' first"
    assert any(EMERGENCY_STOP_REFUSAL in s for s in ch.sent), ch.sent
    msgs = store.history_body(res["thread_id"])
    assert msgs[-1] == {"role": "assistant", "content": EMERGENCY_STOP_REFUSAL}


def test_a_desktop_reply_that_hands_off_says_the_sentence_not_a_500(tmp_path):
    from iron_jarvis.comm.inbound import ESCALATE_ACK
    from tests.test_comm_full_chat import _wire_chat_channel

    async def escalating(platform_, personas, body):
        return {"reply": "needs an agent", "provider": "mock", "model": "m",
                "tools_used": [], "escalate": True, "escalate_reason": "real work"}

    with TestClient(create_app(str(tmp_path))) as client:
        store, _poller, ch = _wire_chat_channel(client, escalating)
        t = store.resolve("tg", "777", "Val")
        client.app.state.platform.config.autonomy_kill_switch = True

        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "build it"})

        assert r.status_code == 200, r.text
        data = r.json()
        assert data["escalate"] is False and data["reply"] == EMERGENCY_STOP_REFUSAL
        assert "session_id" not in data
        assert store.history_body(t.id)[-1] == {"role": "assistant", "content": EMERGENCY_STOP_REFUSAL}
        assert not any(ESCALATE_ACK in s for s in ch.sent)
        assert client.app.state.orchestrator.list_sessions() == []


def test_the_refusal_is_a_runtime_error_every_recording_door_already_catches():
    assert issubclass(EmergencyStopOn, RuntimeError)


# --------------------------------------------------------------------------- #
# G06 on the agent path: a user-made tool that ran may have changed the file.
# --------------------------------------------------------------------------- #
async def test_an_agent_summary_after_a_custom_tool_is_not_accused(tmp_path, monkeypatch):
    from iron_jarvis.core.models import ToolInvocation
    from tests.test_chat_lanes_v13510 import _custom_tool

    claim = "I updated notes.md with the new line."

    async def run_with(tool: str) -> str:
        app = create_app(str(tmp_path / tool))
        platform = app.state.platform
        orch = platform.orchestrator
        _custom_tool(platform, "bump_notes", "open('notes.md','w').write('new')")
        monkeypatch.setattr(orch.runtime, "run", _fake_run(AgentState.COMPLETED, claim))

        def plant(db, sid):
            db.add(ToolInvocation(session_id=sid, agent_run_id="run-x", tool=tool, ok=True,
                                  output="ok", reversibility="none"))

        _plant_on_create(platform, "", plant)
        session = await orch.create_session("put new in notes.md", AgentType.BUILDER)
        done = await orch.run_session(session.id)
        return done.summary or ""

    assert "did not change" not in await run_with("bump_notes")
    # The control: the same claim after a tool the registry does not know as
    # custom (and that names no file) still gets the note.
    assert "did not change" in await run_with("read_document")


# --------------------------------------------------------------------------- #
# G09: a mission whose worklist kept failed items did not get it all done.
# --------------------------------------------------------------------------- #
async def test_a_mission_whose_worklist_kept_failed_items_is_part_failed(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    orch = platform.orchestrator
    monkeypatch.setattr(orch.runtime, "run", _fake_run(AgentState.COMPLETED, "Done."))
    store = platform.worklist

    async def finish(origin: str | None, failed: bool) -> str | None:
        kwargs = {"origin": origin} if origin else {}
        session = await orch.create_session("rename the scans", AgentType.SUPERVISOR, **kwargs)
        board = store.root_session_for(session.id)
        store.add(board, [("a.pdf", "a"), ("b.pdf", "b")])
        store.finish(board, "a.pdf", status="done")
        if failed:
            store.finish(board, "b.pdf", status="failed", note="locked")
        else:
            store.finish(board, "b.pdf", status="done")
        done = await orch.run_session(session.id)
        assert done.status.value == "completed"
        return done.outcome

    assert await finish("job:mission", failed=True) == "completed_with_failures"
    assert await finish("job:mission", failed=False) == "completed"
    # Missions only: another door's run keeps the ledger's verdict alone.
    assert await finish(None, failed=True) == "completed"


# --------------------------------------------------------------------------- #
# G12: the Directory's Connect and Disconnect keep argument secrets in the vault.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
def test_the_directorys_connect_vaults_an_argument_secret_and_disconnect_removes_it(tmp_path, monkeypatch):
    import iron_jarvis.connectors as connectors_pkg
    from iron_jarvis.connectors import catalog, service
    from iron_jarvis.mcp.tools import unload_pack_tools

    fixture = str(ROOT / "tests" / "fixtures" / "argv_mcp_server_v13510.py")
    real = catalog.get_connector("postgres")
    swapped = dataclasses.replace(real, command=sys.executable, args=[fixture, "<connection>"])
    original = catalog.get_connector

    def _get(cid):
        return swapped if cid == "postgres" else original(cid)

    for mod in (catalog, service, connectors_pkg):
        monkeypatch.setattr(mod, "get_connector", _get)

    password = "dbPassw0rd-SEAM13510"
    conn = f"postgresql://me:{password}@db.example:5432/books"
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    try:
        r = client.post("/connectors/postgres/connect", json={"values": {"connection": conn}})
        assert r.status_code == 200, r.text
        assert r.json()["tools_loaded"] == 1, r.json()
        entry = next(s for s in platform.config.mcp_servers if s.get("name") == "postgres")
        assert entry["args"][-1] == "postgresql://me:***@db.example:5432/books"
        assert entry["arg_secrets"] == {"1": "mcp.postgres.arg.1"}
        assert platform.secrets.get("mcp.postgres.arg.1") == conn
        assert password not in (Path(platform.config.home) / "config.toml").read_text(encoding="utf-8")
        # The pack launched with the REAL value.
        argv = asyncio.run(platform.registry.get("mcp__postgres__argv").execute({}, None))
        assert conn in str(argv.output), argv.output

        assert client.delete("/connectors/postgres").status_code == 200
        assert platform.secrets.get("mcp.postgres.arg.1") in (None, ""), "the vault row stayed behind"
    finally:
        unload_pack_tools(platform.registry, "postgres")


# --------------------------------------------------------------------------- #
# G15: an app that is already connected says how to replace an expired token.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
def test_an_already_connected_app_names_how_to_replace_its_token(tmp_path, monkeypatch):
    from iron_jarvis.connectors import catalog
    from iron_jarvis.mcp.tools import unload_pack_tools
    from iron_jarvis.settings import records

    fixture = str(ROOT / "tests" / "fixtures" / "argv_mcp_server_v13510.py")
    real = catalog.get_connector("github")
    original = catalog.get_connector

    def swap(**changes):
        swapped = dataclasses.replace(real, command=sys.executable, args=[fixture], **changes)
        monkeypatch.setattr(catalog, "get_connector", lambda c: swapped if c == "github" else original(c))

    swap()
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    try:
        asyncio.run(records.AppConnectTool(p).execute({"app": "github"}, None))
        token = "ghp_SEAM13510abcdef0123456789NEVERSHOWN"
        assert client.post("/config/secret", json={"name": "app.github", "value": token}).json()["tools_loaded"] == 1
        out = asyncio.run(records.AppConnectTool(p).execute({"app": "github"}, None))
        assert out.ok and out.data["already_connected"] is True
        assert "If its token stopped working, config_secret with name app.github" in out.output
        assert token not in out.output

        # An app that takes no token says nothing about one.
        swap(fields=[])
        out = asyncio.run(records.AppConnectTool(p).execute({"app": "github"}, None))
        assert out.ok and out.data["already_connected"] is True, out.output
        assert "config_secret" not in out.output and "token" not in out.output
    finally:
        unload_pack_tools(p.registry, "github")


def test_the_seam_fixture_is_committed():
    """The dashboard half reads the file; a missing file is a silent pass there."""
    data = _fixture()
    assert {"chat_waiting_row", "run_verdicts"} <= set(data), sorted(data)
