"""v1.359.0 integrate — Wave 9 joined: the seams between lanes 9A, 9B and 9C,
and the lanes' follow-ups the integrator applied.

THE SEAMS
(a) the run words (9A) and the dashboard's words (9B) pass the one retired-word
    list (``dashboard/__tests__/helpers/retiredWords.ts``, read by the
    v1.358.0 Python scan), and every registered built-in is worded in the run
    log — worked, failed, refused and waiting — never by its spaced id;
(b) the Connect door (9C) is the Directory's door: the same words and the
    same address ``app_connect``'s own fix door uses, and that address is the
    Directory's (the dashboard's relocated ``/marketplace``);
(c) Emergency stop (9A) leaves NO unattended run of any kind running or
    parked, while a person's work keeps going — and the kill route's count is
    its own (the ``config.changed`` watcher leaves the route's write alone).

THE FOLLOW-UPS (lane notes)
- the Schedules row of a fire whose run raised reads the run's own sentence
  (platform.py, the 9A patch) — pinned in test_run_words_and_stop_v13590;
- Emergency stop's row says Emergency stop stopped it;
- the router's technical text is worded for a run inside a run;
- a connected app's tool row on GET /sessions/{id}/result carries the
  footnote's name;
- the demo is named once on the task door and in the browser sidebar;
- "no notion" / "slack in the schedule" are the words, not the apps.

Driven through the real code (real app factory, real registry, real helpers).
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import mission, task_door
from iron_jarvis.agents.orchestrator import (
    EMERGENCY_STOP_STOPPED,
    LEGACY_STOPPED_SUMMARY,
    PERSON_STARTED_OPTION,
    STOPPED_BEFORE_START,
    STOPPED_SUMMARY,
    emergency_stop_covers,
)
from iron_jarvis.browser.panel import DEMO_SHORT_NOTICE, PanelTurns
from iron_jarvis.connectors import app_names
from iron_jarvis.connectors.catalog import get_connector
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import (
    AgentRun,
    AgentType,
    PermissionMode,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon import doors
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import failure_words as fw
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.mock import DEMO_OPENING
from iron_jarvis.scheduling import run_outcome as ro
from tests.test_integrate_wave8_v13580 import _park, _schedule_row
from tests.test_run_words_and_stop_v13590 import _waiting_by_hand
from tests.test_vocabulary_beyond_dashboard_v13580 import _hits

_ROOT = Path(__file__).resolve().parents[1]
_BASE = datetime(2026, 10, 19, 9, 0, 0)


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def _wait(pred, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


# =========================================================================== #
# (c) Emergency stop: nothing unattended keeps running or waiting
# =========================================================================== #
#: One live row per kind of door that starts work on its own, plus the
#: person's work that must keep going.
_UNATTENDED = (
    "schedule:nightly",
    "goal:g1",
    "workflow:monthly",
    "reflex:inbox",
    "comm:telegram",
    "assignment:a1",
    "autonomy",
    "remote:desk",
)
_ATTENDED = ("chat", "job:mission", "project:p1", "user", "", "self_dev")


def test_emergency_stop_leaves_no_unattended_run_running_or_parked(tmp_path):
    """THE SEAM: every unattended door's live run is stopped, the scheduled
    workflow parked on a question is stopped and its row says Emergency stop
    did it, and a person's work (chat, an objective, a project task, a run
    they started, a self-development run, a workflow step their Run press
    started, a workflow they ran that waits for them) keeps going."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        platform = client.app.state.platform
        parked = _park(client)
        mine = _waiting_by_hand(client)
        ids: dict[str, str] = {}
        with session_scope(platform.engine) as db:
            for origin in (*_UNATTENDED, *_ATTENDED):
                row = Session(task=f"work from {origin or 'a person'}", origin=origin,
                              status=SessionStatus.ACTIVE)
                db.add(row)
                db.flush()
                ids[origin] = row.id
            step = Session(
                task="the person's workflow step",
                origin="workflow:solo",
                status=SessionStatus.ACTIVE,
                options_json=json.dumps({PERSON_STARTED_OPTION: True}),
            )
            db.add(step)
            db.commit()
            step_id = step.id
        assert all(emergency_stop_covers(o) for o in _UNATTENDED)
        assert not any(emergency_stop_covers(o) for o in _ATTENDED)

        r = client.post("/autonomy/kill", json={"enabled": True})
        assert r.status_code == 200, r.text
        # The route stopped every one of them itself, before it answered.
        assert r.json()["stopped"] == len(_UNATTENDED) + 1, r.json()

        orch = platform.orchestrator
        for origin in _UNATTENDED:
            got = orch.get_session(ids[origin])
            assert got.status is SessionStatus.CANCELLED, (origin, got.status)
            assert got.summary.startswith(EMERGENCY_STOP_STOPPED), (origin, got.summary)
        for origin in _ATTENDED:
            assert orch.get_session(ids[origin]).status is SessionStatus.ACTIVE, origin
        assert orch.get_session(step_id).status is SessionStatus.ACTIVE, "a person's Run"

        run = client.get(f"/workflows/runs/{parked}").json()
        assert run["status"] == "cancelled", run
        row = _schedule_row(client, "fire-gated")
        assert (row["last_status"], row["last_detail"]) == ro.workflow_stopped_waiting(
            "gated", by_emergency_stop=True
        )
        assert "Emergency stop" in row["last_detail"]
        assert client.get(f"/workflows/runs/{mine}").json()["status"] == "waiting"
        assert client.get("/ui/status-line").json()["short_schedules"] == 0

        # NOTHING unattended is left: no live session of an unattended door
        # (save a person's own Run), no unattended workflow waiting.
        with session_scope(platform.engine) as db:
            live = [
                s for s in db.query(Session).all()
                if s.status in (SessionStatus.ACTIVE, SessionStatus.QUEUED)
            ]
        left = [
            s.origin for s in live
            if emergency_stop_covers(s.origin)
            and not json.loads(s.options_json or "{}").get(PERSON_STARTED_OPTION)
        ]
        assert left == [], left
        waiting = client.get("/workflows/runs", params={"status": "waiting"}).json()["runs"]
        assert [w["id"] for w in waiting] == [mine], waiting


def test_the_kill_route_stops_once_and_the_watcher_leaves_its_write(tmp_path):
    """The lane-9A review's race: POST /autonomy/kill stops unattended work
    itself, then the config writer's ``config.changed`` ran the SAME stop on a
    bus worker thread; whichever won the compare-and-set took the count, so
    the route could answer ``stopped: 0``. The watcher now leaves a write
    whose actor is the kill route. Deterministic: the bus runs its sync
    handlers in registration order in one hop, so a recorder added after the
    platform's watcher has seen an event only once the watcher has.
    MUTATION: drop the actor guard in ``platform._emergency_stop_watch`` ->
    the kill route's write calls ``stop_unattended`` twice (red). CONTROL: a
    Settings write (another actor) is still stopped by the watcher."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        platform = client.app.state.platform
        orch = platform.orchestrator
        calls: list[str] = []
        real = orch.stop_unattended

        def counting(*a, **kw):
            calls.append(threading.current_thread().name)
            return real(*a, **kw)

        orch.stop_unattended = counting
        seen: list[dict] = []

        def recorder(ev) -> None:
            if getattr(ev, "type", "") == EventType.CONFIG_CHANGED:
                seen.append(dict(getattr(ev, "payload", None) or {}))

        platform.event_bus.add_handler(recorder)

        def kill_events() -> list[dict]:
            return [p for p in seen if "autonomy_kill_switch" in (p.get("keys") or [])]

        assert client.post("/autonomy/kill", json={"enabled": True}).status_code == 200
        assert _wait(lambda: len(kill_events()) == 1), seen
        assert kill_events()[0].get("actor") == "autonomy_page"
        assert len(calls) == 1, ("the route alone stopped work", calls)

        # Release (the watcher stops nothing when the switch is off), then
        # engage from the Settings row: THAT write is the watcher's to stop.
        assert client.post("/autonomy/kill", json={"enabled": False}).status_code == 200
        assert _wait(lambda: len(kill_events()) == 2), seen
        assert len(calls) == 1, calls
        r = client.put("/settings/values", json={"values": {"autonomy_kill_switch": True}})
        assert r.status_code == 200, r.text
        assert _wait(lambda: len(kill_events()) == 3), seen
        assert len(calls) == 2, ("the watcher stopped the Settings write's work", calls)


def test_emergency_stops_row_names_it_and_a_cancel_press_does_not():
    """The row of a workflow Emergency stop stopped says so (the run's notes
    already did); the Workflows page's Cancel keeps the v1.358.0 sentence."""
    status, said = ro.workflow_stopped_waiting("gated", by_emergency_stop=True)
    assert status == ro.STATUS_CANCELLED
    assert said == (
        "Emergency stop was turned on, so your workflow “gated” was stopped before you answered."
    )
    assert ro.workflow_stopped_waiting("gated") == (
        ro.STATUS_CANCELLED,
        "Your workflow “gated” was stopped before you answered.",
    )


# =========================================================================== #
# (a) every built-in is worded; the words pass the retired-word scan
# =========================================================================== #
@pytest.fixture(scope="module")
def builtins(tmp_path_factory) -> list[str]:
    platform = build_platform(str(tmp_path_factory.mktemp("wave9-registry")))
    names = sorted(platform.registry._tools)
    out = [n for n in names if not n.startswith(("mcp__", "custom:"))]
    assert len(out) >= 120, "anti-vacuity: the real registry"
    return out


def test_every_registered_builtin_is_worded_in_the_run_log(builtins):
    """The lane-9A review's follow-up 5: ~45 ask-tier built-ins had words in
    no table, so their run-log lines read "used assign work", "tried to use
    assign work…", "needed your OK to use assign work". Every registered
    built-in is now worded (one of the tables), so no line — worked, failed,
    refused or waiting — spaces out its id. A NEW built-in fails here until
    it gets words in mission._RUN_TOOL_WORDS (and lib/runWords AGENT_TOOLS).
    MUTATION: drop "assign_work" from _RUN_TOOL_WORDS -> red."""
    # ASK_WORDS words the card and the refusal only; the log's own lines
    # need one of these tables (app_connect and generate_media had none).
    worded = (
        set(mission._TOOL_WORDS) | set(mission._RUN_TOOL_WORDS) | set(mission._STEP_KIND)
        | set(mission._BROWSER_TOOLS)
    )
    missing = [n for n in builtins if n not in worded]
    assert missing == [], missing
    for name in builtins:
        if "_" not in name:
            continue  # a one-word id IS a word ("notify", "recall")
        spaced = name.replace("_", " ")
        ok, failed, do = (
            mission.tool_line(name, {}, True),
            mission.tool_line(name, {}, False),
            mission.do_words(name),
        )
        # The leak's shape ("used assign work", "tried app connect and it
        # failed", "use assign work") — a natural phrase that happens to
        # contain the words ("added a goal" / "goal addition") is not one.
        assert not ok.startswith(f"used {spaced}"), (name, ok)
        assert not failed.startswith(f"tried {spaced} "), (name, failed)
        assert do != f"use {spaced}", (name, do)
        for line in (ok, failed, do):
            assert "_" not in line, (name, line)


def test_every_new_verb_reads_in_the_present(builtins):
    """``do_words`` turns the log's past line into what the call set out to
    do; a first word missing from ``_PRESENT`` would fall back to "use <its
    spaced id>". Every did-phrase's verb is in the table."""
    for name, (did, _noun) in mission._RUN_TOOL_WORDS.items():
        head = did.split(" ", 1)[0]
        assert head in mission._PRESENT, (name, did)
    assert mission.do_words("assign_work") == "queue a task for a teammate"
    # Readiness 12A (G22/G24, moved on purpose): the card's own words (mission.card_words).
    assert mission.do_words("sentinel_update") == "pause or resume a watcher"
    assert mission.do_words("pane_send") == "type into a terminal"


def test_the_waves_words_pass_the_retired_word_scan():
    """THE SEAM (a), Python half: the run log's tables, the Emergency stop
    row, the demo notes, the router's run words and the stopped sentences
    use none of VOCABULARY.md's retired words (the dashboard's own list,
    parsed by the v1.358.0 scan). The dashboard half is vocabulary.test.ts."""
    texts: list[str] = []
    for did, noun in mission._RUN_TOOL_WORDS.values():
        texts += [did, noun]
    texts += list(mission.ASK_WORDS.values())
    texts += [
        ro.workflow_stopped_waiting("gated", by_emergency_stop=True)[1],
        task_door.DEMO_ANSWER_SHORT_NOTE,
        DEMO_SHORT_NOTICE,
        STOPPED_SUMMARY,
        STOPPED_BEFORE_START,
        EMERGENCY_STOP_STOPPED,
    ]
    with fw.run_surface():
        texts += [router_mod._another_model(), router_mod._retry_words()]
    hits = [(t, _hits(t)) for t in texts if _hits(t)]
    assert hits == [], hits


def test_the_routers_technical_text_is_worded_for_where_it_is_read(tmp_path):
    """The lane-9A notes' follow-up 3: the router's refusal said "pick
    another model for this chat" inside a run too (only its folded technical
    text; the headline already followed the surface). A chat keeps its words
    byte for byte. MUTATION: ``_another_model`` ignores the surface -> red."""
    platform = build_platform(str(tmp_path))
    router = platform.router
    chat = str(router._unavailable_error("ollama", False))
    assert chat.endswith(" Bring that endpoint back up, or pick another model for this chat, and retry.")
    with fw.run_surface():
        run = str(router._unavailable_error("ollama", False))
        timeout = str(router._unavailable_error("ollama", False, kind="timeout"))
    assert "this chat" not in run and "this chat" not in timeout
    assert run.endswith(" Bring that endpoint back up, or choose another model in Settings, and run it again.")
    assert timeout.endswith("or choose another model in Settings, and run it again.")


def test_an_app_tools_row_carries_the_footnotes_name_through_the_real_result_route(tmp_path):
    """The lane-9A review's follow-up 3: the run card's "What it did" said
    "Used gmail: search messages" two lines above the daemon's "Step by step"
    "Gmail › search messages". The ledger row now carries the footnote's name
    (``mission.app_tool_display``) and lib/runWords prefers it; every other
    row keeps its exact ``{tool, count}`` shape."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        platform = client.app.state.platform
        sid = "sess_wave9_app"
        with session_scope(platform.engine) as db:
            db.add(Session(id=sid, task="find the invoice", agent_type=AgentType.BUILDER,
                           status=SessionStatus.COMPLETED, created_at=_BASE,
                           finished_at=_BASE + timedelta(minutes=1)))
            db.add(AgentRun(id=f"run_{sid}", session_id=sid, created_at=_BASE))
            for i, (tool, ok) in enumerate(
                [("mcp__gmail__search_messages", True), ("mcp__gmail__search_messages", False),
                 ("read_file", True)]
            ):
                db.add(ToolInvocation(
                    session_id=sid, agent_run_id=f"run_{sid}", tool=tool, args_json="{}",
                    verdict=PermissionMode.ALLOW, ok=ok, output="" if ok else "boom",
                    created_at=_BASE + timedelta(seconds=i),
                ))
            db.commit()
        result = client.get(f"/sessions/{sid}/result").json()
        used = {r["tool"]: r for r in result["tools_used"]}
        assert used["mcp__gmail__search_messages"]["name"] == mission.app_tool_display(
            "mcp__gmail__search_messages"
        ) == "Gmail › search messages"
        assert set(used["read_file"]) == {"tool", "count"}, used["read_file"]
        failed = {r["tool"]: r for r in result["tools_failed"]}
        assert failed["mcp__gmail__search_messages"]["name"] == "Gmail › search messages"


def test_the_run_pages_old_stopped_words_are_the_python_constants():
    """The run page reads a stopped run stored before v1.359.0 ("Session
    cancelled by the user.") as the shared stopped sentence. Its table is
    pinned to the daemon's constants, so the two cannot drift."""
    src = _read("dashboard/components/sessions/runPageWords.ts")
    assert f'export const STOPPED_SUMMARY = "{STOPPED_SUMMARY}";' in src
    assert f'export const STOPPED_BEFORE_START = "{STOPPED_BEFORE_START}";' in src
    assert f'"{LEGACY_STOPPED_SUMMARY.lower()}": STOPPED_SUMMARY' in src
    assert '"cancelled while queued (never started).": STOPPED_BEFORE_START' in src


# =========================================================================== #
# the demo is named once on every surface it reaches
# =========================================================================== #
def test_the_task_door_names_the_demo_once():
    """Lane-9A review follow-up 4: a demo answer that already opens with the
    demo's own sentence gets the short note; any other demo answer keeps the
    full one (MUTATION: always the full note -> the first assert is red)."""
    opened = SimpleNamespace(status="completed", provider="mock",
                             summary=f"{DEMO_OPENING} It handed the task to a helper.")
    msg = task_door._reply_message(opened)
    assert msg.endswith(task_door.DEMO_ANSWER_SHORT_NOTE), msg
    assert msg.lower().count("demo model") == 1, msg
    plain = SimpleNamespace(status="completed", provider="mock", summary="All subtasks complete.")
    assert task_door._reply_message(plain).endswith(task_door.DEMO_ANSWER_NOTE)


def test_the_sidebar_names_the_demo_once():
    route = {"reason": "mock", "provider": "mock", "model": "mock-1"}
    assert PanelTurns._route_notice(route, f"{DEMO_OPENING} Hello.") == DEMO_SHORT_NOTICE
    full = PanelTurns._route_notice(route)
    assert full.startswith("Answered by the demo model"), full
    assert PanelTurns._route_notice(route, "A scripted hello.") == full


# =========================================================================== #
# (b) the Connect door is the Directory's door
# =========================================================================== #
def _directory_address() -> str:
    """The Directory's address, read off the dashboard's relocated map (the
    ``/marketplace`` alias a Settings link uses)."""
    src = _read("dashboard/lib/relocated.ts")
    m = re.search(
        r'"/marketplace":\s*\{\s*base:\s*"([^"]+)",\s*extra:\s*\{\s*section:\s*"([^"]+)"\s*\}',
        src,
    )
    assert m, "lib/relocated.ts names the Directory"
    return f"{m.group(1)}?section={m.group(2)}"


def test_the_connect_door_is_app_connects_door_at_the_directorys_address():
    """THE SEAM (b): the door 9C adds for a named app that is not connected
    is byte for byte the fix door ``app_connect`` leaves for that app, at the
    Directory's address — one door, one address, one set of words."""
    drive = get_connector("google_drive")
    added = doors.with_connect_door([], [{"id": drive.id, "name": drive.name}], ())
    refused = SimpleNamespace(ok=False, error="needs sign-in", data={}, output="")
    fix = doors.failure_door_for("app_connect", {"app": "google_drive"}, refused)
    assert added == [fix] == [{"href": doors._APPS_FIX_HREF, "label": "Connect Google Drive in Settings"}]
    assert doors._APPS_FIX_HREF == _directory_address()
    assert doors.connect_label([drive.name]) == fix["label"]


@pytest.mark.parametrize(
    "text, named",
    [
        ("I have no notion what to write for the intro", []),
        ("I don't have the faintest notion", []),
        ("there is slack in the schedule this week", []),
        ("we still have some slack in the plan", []),
        ("is Slack connected?", ["slack"]),
        ("post the summary to Slack", ["slack"]),
        ("search my Notion for the brief", ["notion"]),
        ("any Notion pages about Q3?", ["notion"]),
    ],
)
def test_a_word_that_is_not_the_app_offers_no_door(text, named):
    """The lane-9C review's known limit: the shared matcher read "no notion"
    and "slack in the schedule" as the apps, and since this wave a not
    connected app puts a Connect door under the reply. MUTATION: drop the
    new look-behinds in ``_NOT_THE_APP`` -> the first rows are red."""
    assert app_names.apps_named(text) == named
    rows = [{"id": i, "name": i.title(), "state": "not_connected"} for i in ("notion", "slack")]
    offered = [o["id"] for o in app_names.connect_offers(text, rows)]
    assert set(offered) <= set(named), (text, offered)
