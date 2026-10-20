"""v1.359.0 (wave 9, lane 9A): a run says what happened in words, and
Emergency stop stops a workflow that waits on the person.

The last remainders of the product-readiness audit for runs:

(1) G19 remainder — a refused call read "Builder tried write file, a tool it
    was not given" (a built-in's id with the underscore spaced) two rows below
    "Builder needed your OK to save a file". It now says what the call set out
    to do in the log's own words: "tried to save a file".
(2) The run log named an app's tool by its pack id ("used gmail: search
    messages") while the chat footnote named it by the catalog ("Gmail ›
    search messages"). One form now: the footnote's, from the footnote's own
    helper (``chat_turn.app_tool_words``).
(3) A stopped run's summary read "Session cancelled by the user." — the
    engine's words. It is the shared stopped sentence now (the chat page's
    own fallback, ``chat_delivery.STOPPED_SENTENCE``), neutral because the
    same summary goes home to ANOTHER computer for a task it sent.
(4) G13 remainder — Emergency stop cancelled live unattended runs but not a
    workflow PARKED on a question (no live session), so it kept waiting and
    its schedule row kept "Needs you". Now it is stopped, its notes say why,
    and its row settles through the v1.358.0 ``settle_stopped_waiting`` path;
    a run a person started stays.
(5) A scheduled run that failed on a model timeout read "…Press Retry, or
    pick another model for this chat." on its Schedules row. Inside a run the
    fix says what to do there ("Run it again, or choose another model in
    Settings."); the chat keeps its sentence byte for byte.
(6) The demo model's reply opened "Done. Wrote RESULT.md summarizing the
    task." — read as finished work. It says it is the demo, in the canon's
    words, and claims a file only when its step really saved one.

Driven through the real code: the real app factory and routes, the real
registry's refusals, the real orchestrator, the real mock adapter.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import chat_delivery, mission
from iron_jarvis.agents.orchestrator import (
    EMERGENCY_STOP_STOPPED,
    LEGACY_STOPPED_SUMMARY,
    STOPPED_BEFORE_START,
    STOPPED_SUMMARY,
    Orchestrator,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType, Session, SessionStatus
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import failure_words as fw
from iron_jarvis.providers.adapters import mock as demo
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.scheduling import run_outcome as ro
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.workflows.engine import STARTED_BY_PERSON
from iron_jarvis.workflows.models import WorkflowRunRecord
from tests.test_integrate_wave8_v13580 import _park, _schedule_row

_ROOT = Path(__file__).resolve().parents[1]
_BASE = datetime(2026, 10, 19, 9, 0, 0)


# =========================================================================== #
# (1) a refusal says what the call set out to do, never a built-in's id
# =========================================================================== #
def _seed_run(engine, sid: str) -> None:
    with session_scope(engine) as db:
        db.add(
            Session(
                id=sid,
                task="tidy the client folder",
                agent_type=AgentType.BUILDER,
                provider="anthropic",
                model="claude-sonnet-4-6",
                status=SessionStatus.COMPLETED,
                created_at=_BASE,
                finished_at=_BASE + timedelta(minutes=1),
            )
        )
        db.add(AgentRun(id=f"run_{sid}", session_id=sid, created_at=_BASE))
        db.commit()


def test_the_receipt_says_a_refused_builtin_in_the_logs_own_words(tmp_path):
    """THE ACCEPTANCE, through the real registry and the read the run card's
    "Step by step" fold shows (``GET /sessions/{id}/result``). MUTATION: put
    back ``words.format(t=tool_own_name(tool))`` with the old table -> "tried
    write file" (red)."""
    client = TestClient(create_app(str(tmp_path / "home")))
    p = client.app.state.platform
    sid = "sess_refusals"
    _seed_run(p.engine, sid)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id=sid, agent_run_id=f"run_{sid}", config=p.config,
        event_bus=p.event_bus, engine=p.engine,
    )

    async def go():
        # The roster gate ("not armed") and the repeated-failure breaker
        # ("refused") — neither asked anyone.
        await p.registry.invoke(
            "write_file", {"path": "a.md", "content": "x"}, ctx, p.permissions,
            allowed_names={"read_file"},
        )
        await p.registry.invoke(
            "read_file", {"path": "missing.md"}, ctx, p.permissions,
            deny_reason="repeated-failure breaker — `read_file` has now failed 3 times",
            deny_label="refused",
        )

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()

    r = client.get(f"/sessions/{sid}/result")
    assert r.status_code == 200, r.text
    texts = [a["text"] for a in r.json()["activity"]]
    assert texts == [
        "Builder tried to save a file, but it was not given that tool, so nothing ran",
        "Builder kept failing to read a file, so it was stopped from trying again",
    ], texts
    seen = " ".join(texts)
    for leak in ("write file", "read file", "write_file", "read_file", " — "):
        assert leak not in seen, (leak, texts)


#: Every built-in the run log words: the agent tools, the work line's kinds,
#: the browser tools and the table's own (``_TOOL_WORDS``).
def _builtins() -> list[str]:
    ids = (
        set(mission._TOOL_WORDS)
        | set(mission._RUN_TOOL_WORDS)
        | set(mission._STEP_KIND)
        | set(mission._BROWSER_TOOLS)
    )
    return sorted(ids)


@pytest.mark.parametrize("kind", ["not armed", "refused", "low trust", "a label nobody knows"])
def test_no_refusal_line_names_a_builtin_by_its_id(kind):
    """Anti-vacuity: > 60 built-ins, every refusal grammar. MUTATION: drop
    the ``_RUN_TOOL_WORDS`` / kind branch in ``do_words`` -> "use history
    search" (red)."""
    tools = _builtins()
    assert len(tools) > 60
    rows = [
        (f"i{i}", "s1", tool, "{}", False, "ask", False, _BASE + timedelta(seconds=i))
        for i, tool in enumerate(tools)
    ]
    lines, _last = mission.tool_activity(rows, {f"i{i}": kind for i in range(len(tools))}, {"s1": "Builder"})
    assert len(lines) == len([t for t in tools if t not in mission._QUIET_TOOLS])
    for line in lines:
        text = line["text"]
        assert "_" not in text, text
        assert line["tone"] == "warn", line
    for tool, line in zip([t for t in tools if t not in mission._QUIET_TOOLS], lines):
        spaced = tool.replace("_", " ")
        if spaced in mission.do_words(tool):
            # A word the log itself uses ("search the web" for web_search)
            # is fine; the id read as a phrase after "use" is the leak.
            assert not mission.do_words(tool).startswith(f"use {spaced}"), (tool, line)


def test_do_words_reads_the_approval_card_first_then_the_logs_line():
    assert mission.do_words("write_file") == "save a file" == mission.ask_words("write_file")
    assert mission.do_words("shell") == "run a command"
    assert mission.do_words("history_search") == "search your chats"
    assert mission.do_words("read_file") == "read a file"
    assert mission.do_words("list_files") == "look in a folder"
    assert mission.do_words("browser_click") == "use your browser"
    assert mission.do_words("consult") == "ask a teammate for advice"
    # A custom tool has no other name: said by its own.
    assert mission.do_words("rename_real_file") == "use rename real file"
    # Every first word the tables use turns into the present (pinned so a new
    # table phrase cannot fall back to "use <id>" silently).
    for words in (*mission._RUN_TOOL_WORDS.values(), *mission._KIND_WORDS.values()):
        assert mission._present_phrase(words[0]), words


# =========================================================================== #
# (2) one form for an app's tool: the chat footnote's
# =========================================================================== #
@pytest.mark.parametrize(
    "tool, shown",
    [
        ("mcp__gmail__search_messages", "Gmail › search messages"),
        ("mcp__github__listOpenIssues", "GitHub › list open issues"),
        ("mcp__google_drive__search_files", "Google Drive › search files"),
        # An app the catalog does not know keeps the pack's own name.
        ("mcp__tracker__get_PR", "tracker › get PR"),
    ],
)
def test_the_run_log_names_an_app_tool_the_way_the_chat_footnote_does(tool, shown):
    """MUTATION: return the pack words in ``tool_own_name`` ("gmail: search
    messages") -> red."""
    app, words = chat_turn.app_tool_words(tool)
    assert f"{app} › {words}" == shown
    assert shown in chat_turn.refusal_footnotes([(tool, "not armed")])
    assert mission.tool_line(tool, {}, True) == f"used {shown}"
    assert mission.tool_line(tool, {}, False) == f"tried {shown} and it failed"
    assert mission.ask_words(tool) == f"use {shown}", "the ask line too, never 'use mcp  gmail …'"
    rows = [("i1", "s1", tool, "{}", False, "ask", False, _BASE)]
    (line,), _ = mission.tool_activity(rows, {"i1": "not armed"}, {"s1": "Builder"})
    assert line["text"] == f"Builder tried to use {shown}, but it was not given that tool, so nothing ran"


# =========================================================================== #
# (3) a stopped run reads the shared stopped sentence
# =========================================================================== #
async def test_a_stopped_run_reads_the_shared_stopped_sentence_where_it_is_shown(tmp_path):
    """MUTATION: put "Session cancelled by the user." back as the default ->
    red. The same words reach the chat a chat-started run was asked in."""
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    assert STOPPED_SUMMARY == chat_delivery.STOPPED_SENTENCE == "The agent was stopped before it finished."
    assert STOPPED_BEFORE_START == chat_delivery.STOPPED_BEFORE_START_SENTENCE
    assert LEGACY_STOPPED_SUMMARY == "Session cancelled by the user."

    # No live task: cancel_session writes the row itself.
    s1 = await orch.create_session("tidy the folder", AgentType.BUILDER)
    got = orch.cancel_session(s1.id)
    assert got.status is SessionStatus.CANCELLED
    assert got.summary == STOPPED_SUMMARY
    # The finalizer a cancelled task unwinds into.
    s2 = await orch.create_session("tidy the folder", AgentType.BUILDER)
    await orch._finalize_cancelled(s2)
    row = orch.get_session(s2.id)
    assert row.summary == STOPPED_SUMMARY
    assert chat_delivery.content_for_session(row) == STOPPED_SUMMARY
    for leak in ("Session", "cancelled", "by the user"):
        assert leak not in row.summary, leak


# =========================================================================== #
# (4) Emergency stop stops a workflow parked on a question
# =========================================================================== #
def _waiting_by_hand(client: TestClient) -> str:
    """A workflow a PERSON ran (the Run button) that parks on its question."""
    r = client.post(
        "/workflows",
        json={"name": "solo", "steps": [{"name": "Ask", "kind": "ask", "message": "Go?"}]},
    )
    assert r.status_code < 300, r.text
    run = client.post("/workflows/run", json={"name": "solo"}).json()
    deadline = time.monotonic() + 30
    while client.get(f"/workflows/runs/{run['id']}").json()["status"] != "waiting":
        assert time.monotonic() < deadline, "the hand-started run never parked"
        time.sleep(0.05)
    return run["id"]


def test_emergency_stop_stops_a_parked_scheduled_workflow_and_its_row_says_stopped(tmp_path):
    """THE ACCEPTANCE: a schedule fired a workflow that waits on the person;
    the person engages Emergency stop. MUTATION: drop the
    ``_stop_waiting_workflows`` call in ``stop_unattended`` -> the run keeps
    waiting and the row keeps "Needs you" (red)."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        parked = _park(client)
        mine = _waiting_by_hand(client)
        assert _schedule_row(client, "fire-gated")["last_status"] == ro.STATUS_NEEDS_YOU
        assert client.get("/ui/status-line").json()["short_schedules"] == 1

        r = client.post("/autonomy/kill", json={"enabled": True})
        assert r.status_code == 200, r.text
        assert r.json()["kill_switch"] is True
        assert isinstance(r.json()["stopped"], int), r.json()
        # v1.359.0 review: the route stops what it finds AND the platform's
        # config.changed watcher (a bus worker thread) runs the same
        # stop_unattended for this same write. Whichever reaches the parked
        # run first stops it (compare-and-set) and settles its row, so the
        # route's own count can read 0 and the row can settle a moment after
        # the answer (reproduced by holding the route's call 0.5 s; it failed
        # 1 run in ~40 here). Wait for the ROW, the thing asserted;
        # ``test_only_a_waiting_unattended_run_is_stopped...`` pins that
        # stop_unattended returns the run it stopped.
        deadline = time.monotonic() + 10
        row = _schedule_row(client, "fire-gated")
        while row["last_status"] != ro.STATUS_CANCELLED and time.monotonic() < deadline:
            time.sleep(0.05)
            row = _schedule_row(client, "fire-gated")

        run = client.get(f"/workflows/runs/{parked}").json()
        assert run["status"] == "cancelled", run
        assert run["waiting_json"] == "" and run["finished_at"], run
        assert EMERGENCY_STOP_STOPPED in json.loads(run["notes_json"]), "its page says why"
        row = _schedule_row(client, "fire-gated")
        assert (row["last_status"], row["last_detail"]) == ro.workflow_stopped_waiting("gated")
        assert client.get("/ui/status-line").json()["short_schedules"] == 0
        # Nothing left to answer: the stopped run refuses an answer.
        assert client.post(f"/workflows/runs/{parked}/answer", json={"answer": "yes"}).status_code == 409

        # A run a PERSON started keeps waiting for them (v1.352.0's rule).
        assert client.get(f"/workflows/runs/{mine}").json()["status"] == "waiting"


def test_the_settings_row_stops_a_parked_workflow_too(tmp_path):
    """Every door that flips the switch goes through the ConfigWriter, whose
    ``config.changed`` the platform watches (``stop_unattended`` on a worker
    thread) — the Settings row, Ctrl K, a chat change. MUTATION as above."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        parked = _park(client)
        r = client.put("/settings/values", json={"values": {"autonomy_kill_switch": True}})
        assert r.status_code == 200, r.text
        # The watcher runs on a bus worker thread: the run is stopped first,
        # then its row is settled, so wait for the ROW (the thing asserted),
        # not for the run's status.
        deadline = time.monotonic() + 20
        row = _schedule_row(client, "fire-gated")
        while row["last_status"] != ro.STATUS_CANCELLED and time.monotonic() < deadline:
            time.sleep(0.05)
            row = _schedule_row(client, "fire-gated")
        assert (row["last_status"], row["last_detail"]) == ro.workflow_stopped_waiting("gated")
        assert client.get(f"/workflows/runs/{parked}").json()["status"] == "cancelled"


def test_only_a_waiting_unattended_run_is_stopped_and_an_answer_that_won_is_left(tmp_path):
    """The compare-and-set: a run the person's answer already claimed
    (``resuming``) is not touched here (its live session is the other loop's
    to stop), a person's waiting run stays, and the call is idempotent."""
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    with session_scope(p.engine) as db:
        rows = {
            "theirs": WorkflowRunRecord(workflow_name="nightly", status="waiting", waiting_json='{"index": 0}'),
            "mine": WorkflowRunRecord(
                workflow_name="brief", status="waiting", started_by=STARTED_BY_PERSON, waiting_json='{"index": 0}'
            ),
            "answered": WorkflowRunRecord(workflow_name="nightly", status="resuming"),
        }
        for rec in rows.values():
            db.add(rec)
        db.commit()
        ids = {k: rec.id for k, rec in rows.items()}

    stopped = orch.stop_unattended()

    assert ids["theirs"] in stopped and ids["mine"] not in stopped and ids["answered"] not in stopped
    with session_scope(p.engine) as db:
        got = {k: db.get(WorkflowRunRecord, v) for k, v in ids.items()}
        assert got["theirs"].status == "cancelled" and got["theirs"].waiting_json == ""
        assert json.loads(got["theirs"].notes_json) == [EMERGENCY_STOP_STOPPED]
        assert got["mine"].status == "waiting"
        assert got["answered"].status == "resuming"
    assert orch.stop_unattended() == [], "nothing is stopped twice"


def test_a_run_parked_before_the_person_mark_existed_is_stopped_too(tmp_path):
    """v1.359.0 review: on an UPGRADED install the schema reconcile adds
    ``started_by`` nullable, so a workflow a schedule parked before v1.352.0
    holds NULL there — and parked runs survive restarts by design. The engine
    reads NULL as "" (unattended); SQL's ``NULL != 'person'`` is NULL, so the
    first cut left that run waiting under Emergency stop. Driven through a
    real upgrade (the column dropped and re-added by ``build_platform``).
    MUTATION: drop the ``started_by IS NULL`` arm -> it keeps waiting (red)."""
    from sqlalchemy import text

    home = str(tmp_path / "home")
    p = build_platform(home)
    with session_scope(p.engine) as db:
        rec = WorkflowRunRecord(workflow_name="old-nightly", status="waiting", waiting_json='{"index": 0}')
        db.add(rec)
        db.commit()
        rid = rec.id
    with p.engine.connect() as conn:
        conn.execute(text("ALTER TABLE workflowrunrecord DROP COLUMN started_by"))
        conn.commit()
    p.engine.dispose()

    upgraded = build_platform(home)
    with upgraded.engine.connect() as conn:
        on_disk = conn.execute(
            text("SELECT started_by FROM workflowrunrecord WHERE id = :i"), {"i": rid}
        ).scalar_one()
    assert on_disk is None, "anti-vacuity: the row really reads NULL, as on an upgraded install"

    stopped = Orchestrator(upgraded).stop_unattended()

    assert rid in stopped, stopped
    with session_scope(upgraded.engine) as db:
        got = db.get(WorkflowRunRecord, rid)
        assert got.status == "cancelled" and got.waiting_json == ""
        assert json.loads(got.notes_json) == [EMERGENCY_STOP_STOPPED]
    upgraded.engine.dispose()


# =========================================================================== #
# (5) a run's failure says what to do where a run is read
# =========================================================================== #
RUN_TIMEOUT = "Claude Code didn't answer in time. Run it again, or choose another model in Settings."
CHAT_TIMEOUT_FIX = "Press Retry, or pick another model for this chat."


def _claude_timeout() -> BaseException:
    return transport._Timeouts(SimpleNamespace(idle_timeout_s=300, total_timeout_s=3600), 0.0).error(False)


def _timed_out_schedule(tmp_path, monkeypatch) -> tuple[TestClient, dict, list[str]]:
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    orch = app.state.platform.orchestrator
    seen: list[str] = []

    async def times_out(session, agent_def, *a, **kw):
        seen.append(fw.current_surface())
        raise _claude_timeout()

    monkeypatch.setattr(orch.runtime, "run", times_out)
    r = client.post(
        "/schedules",
        json={"name": "nightly", "cron": "0 17 * * 5", "kind": "task", "payload": {"task": "Write the digest."}},
    )
    assert r.status_code == 200, r.text
    body = client.post("/schedules/nightly/run").json()
    assert body["last_status"] == ro.STATUS_ERROR, body
    return client, body, seen


def test_a_scheduled_run_that_timed_out_says_what_to_do_where_a_run_is_read(tmp_path, monkeypatch):
    """The run's own summary (its run page, the bell, the phone, the chat
    line and a task door's reply all print it). MUTATION: drop
    ``surface=SURFACE_RUN`` in ``plain_reason`` -> "...for this chat" (red);
    drop ``run_surface()`` in ``run_session`` -> the surface seen inside the
    run reads "chat" (red)."""
    client, body, seen = _timed_out_schedule(tmp_path, monkeypatch)
    got = client.get(f"/sessions/{body['last_session_id']}").json()["session"]
    assert got["summary"] == f"The run failed: {RUN_TIMEOUT}", got["summary"]
    assert "this chat" not in got["summary"] and "Press Retry" not in got["summary"]
    assert seen == [fw.SURFACE_RUN], "every failure inside a run is worded for a run"
    assert fw.current_surface() == fw.SURFACE_CHAT, "and the run's surface ends with it"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "needs the integrator's platform.py edit (lane 9A notes): a fire whose run RAISED "
        "records str(exc), the raw 'claude-cli: CLI timed out ...', instead of the run's own "
        "summary. Once the edit lands this XPASSes: remove this mark."
    ),
)
def test_the_schedules_row_reads_the_runs_own_sentence(tmp_path, monkeypatch):
    """THE ACCEPTANCE on the Schedules row itself."""
    _client, body, _seen = _timed_out_schedule(tmp_path, monkeypatch)
    assert body["last_detail"] == f"The run failed: {RUN_TIMEOUT}", body


def test_the_chat_keeps_its_own_sentence_on_both_lanes(tmp_path, monkeypatch):
    """The control: the same failure in a chat turn keeps the chat's fix,
    byte for byte (both lanes, lock-step)."""
    client = TestClient(create_app(str(tmp_path / "home")))
    platform = client.app.state.platform
    exc = _claude_timeout()

    async def fail(**kw):
        raise exc

    async def fail_stream(**kw):
        raise exc
        yield  # pragma: no cover

    monkeypatch.setattr(platform.router, "complete", fail)
    monkeypatch.setattr(platform.router, "stream", fail_stream)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502
    assert r.json()["fix"] == CHAT_TIMEOUT_FIX
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    frames = [
        json.loads("".join(ln[6:] for ln in block.splitlines()[1:] if ln.startswith("data: ")))
        for block in r.text.split("\n\n")
        if block.strip().startswith("event: error")
    ]
    assert frames and frames[0]["fix"] == CHAT_TIMEOUT_FIX


def _failure(*, kind: str = "", provider: str = "", status: int | None = None, partial: bool = False,
             retry_in: float = 0.0, cli: str = "") -> ProviderError:
    exc = ProviderError("it failed", status_code=status)
    if kind:
        exc.kind = kind  # type: ignore[attr-defined]
    if provider:
        exc.failed_provider = provider  # type: ignore[attr-defined]
    if partial:
        exc.partial = True  # type: ignore[attr-defined]
    if retry_in:
        exc.retry_in = retry_in  # type: ignore[attr-defined]
    if cli:
        exc.cli = cli  # type: ignore[attr-defined]
    return exc


#: Every recognised shape -> the chat's fix (UNCHANGED since v1.358.0).
_CHAT_FIXES = [
    (dict(kind="signed_out", provider="anthropic", cli="claude-cli"),
     f"Press Sign in beside Claude Code in {fw.ACCOUNTS_PLACE}, then retry."),
    (dict(kind="answered_error", provider="custom"),
     "Check it, or pick another model for this chat, and retry. Iron Jarvis won't switch to a"
     " different AI without asking. You can allow that in Settings."),
    (dict(kind="cooldown", provider="openai", retry_in=30), "Try again in 30 s, or pick another model for this chat."),
    (dict(kind="timeout", provider="openai"), "Press Retry, or pick another model for this chat."),
    (dict(kind="timeout", provider="ollama"),
     "Give it time to finish loading, or pick another model for this chat, and retry."),
    (dict(kind="interrupted", provider="openai", partial=True), "The reply above is incomplete. Press Retry."),
    (dict(kind="interrupted", provider="openai"),
     "Press Retry. If it keeps happening, check your internet connection or pick another model for this chat."),
    (dict(kind="unreachable", provider="ollama"), "Bring it back up, or pick another model for this chat, and retry."),
    (dict(kind="unreachable", provider="openai"),
     "Check your internet connection, or pick another model for this chat, and retry."),
    (dict(provider="claude-cli", status=401), f"Press Sign in beside Claude Code in {fw.ACCOUNTS_PLACE}, then retry."),
    (dict(provider="openrouter", status=401), f"Update it in {fw.ACCOUNTS_PLACE}."),
    (dict(provider="openai", status=429), "Try again shortly, or pick another model for this chat."),
    (dict(provider="openai", status=400), "Retry, or pick another model for this chat."),
]


@pytest.mark.parametrize("shape, chat_fix", _CHAT_FIXES)
def test_every_fix_has_a_chat_form_and_a_run_form(shape, chat_fix):
    exc = _failure(**shape)
    title_chat, fix_chat = fw.classify(exc, surface=fw.SURFACE_CHAT)
    title_run, fix_run = fw.classify(exc, surface=fw.SURFACE_RUN)
    assert fix_chat == chat_fix, "the chat keeps its sentence byte for byte"
    assert fw.classify(exc) == (title_chat, fix_chat), "outside a run the default is the chat"
    assert title_run == title_chat, "the headline never changes"
    for chat_only in ("this chat", "Press Retry", "The reply above"):
        assert chat_only not in fix_run, (shape, fix_run)
    assert (
        "un it again" in fix_run or fix_run == f"Update it in {fw.ACCOUNTS_PLACE}."
    ), (shape, fix_run)
    with fw.run_surface():
        assert fw.classify(exc) == (title_run, fix_run)
        assert fw.friendly_error(exc)["fix"] == fix_run
    assert fw.plain_reason(exc) == f"{title_run}. {fix_run}", "a run summary is always the run's"


def test_the_generic_fix_follows_the_surface_too():
    odd = ProviderError("anthropic: something new went wrong")
    assert fw.friendly_error(odd)["fix"] == fw.GENERIC_FIX
    with fw.run_surface():
        assert fw.friendly_error(odd)["fix"] == fw.GENERIC_FIX_RUN
    assert "this chat" not in fw.GENERIC_FIX_RUN


# =========================================================================== #
# (6) the demo model says it is the demo
# =========================================================================== #
def _demo_words_ts() -> str:
    return (_ROOT / "dashboard" / "lib" / "demoWords.ts").read_text(encoding="utf-8").replace("\r\n", "\n")


def _ts_const(name: str) -> str:
    m = re.search(rf'^export const {name} = "([^"]+)";$', _demo_words_ts(), re.M)
    assert m, name
    return m.group(1)


def test_the_demo_names_itself_in_the_canons_words():
    assert demo.DEMO_MODEL == _ts_const("DEMO_MODEL") == "demo model"
    assert demo.DEMO_REPLIES == _ts_const("DEMO_REPLIES") == "a scripted demo, not real answers"
    assert demo.DEMO_OPENING == "This is the demo model: a scripted demo, not real answers."
    for line in (demo.DEMO_SAVED_LINE, demo.DEMO_NOT_SAVED_LINE, demo.DEMO_HANDED_OFF_LINE,
                 demo.DEMO_NOTHING_DONE_LINE):
        assert line.startswith(demo.DEMO_OPENING), line
        assert not re.search(r"\bmock\b|offline|simulated|subagent", line, re.I), line
        assert not line.startswith("Done") and "Wrote" not in line, line


def _tools(*names: str) -> list[dict]:
    return [{"name": n, "description": "", "input_schema": {"type": "object"}} for n in names]


async def _demo(*names: str, tool_reply: LLMMessage | None = None):
    msgs = [LLMMessage(role="user", content="tidy my notes")]
    if tool_reply is not None:
        msgs.append(tool_reply)
    return await demo.MockLLMAdapter().complete(system="", messages=msgs, tools=_tools(*names))


async def test_the_demo_claims_a_file_only_when_its_step_saved_one():
    """MUTATION: answer ``DEMO_SAVED_LINE`` without checking the step ->
    a refused write reads "saved RESULT.md" (red)."""
    first = await _demo("write_file")
    assert [c.name for c in first.tool_calls] == ["write_file"]
    saved = await _demo(
        "write_file", tool_reply=LLMMessage(role="tool", name="write_file", content="wrote 80 bytes to RESULT.md")
    )
    assert saved.text == demo.DEMO_SAVED_LINE
    refused = await _demo(
        "write_file",
        tool_reply=LLMMessage(role="tool", name="write_file", content="permission denied: not armed"),
    )
    assert refused.text == demo.DEMO_NOT_SAVED_LINE and "saved RESULT.md" not in refused.text
    nothing = await _demo()
    assert nothing.text == demo.DEMO_NOTHING_DONE_LINE
    assert "Wrote" not in nothing.text and "RESULT.md" not in nothing.text
    handed = await _demo("delegate", tool_reply=LLMMessage(role="tool", name="delegate", content="ok"))
    assert handed.text == demo.DEMO_HANDED_OFF_LINE


def test_a_fresh_install_run_reads_as_the_demo_on_its_page(tmp_path):
    """Through the real routes on a fresh install (whose default IS the demo):
    the run really saves its file, and its summary says it is the demo."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        r = client.post("/sessions", json={"task": "write RESULT.md", "wait": True})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"
        sid = r.json()["id"]
        got = client.get(f"/sessions/{sid}").json()["session"]
        assert got["summary"] == demo.DEMO_SAVED_LINE
        workspace = Path(got["workspace_path"])
        written = (workspace / "RESULT.md").read_text(encoding="utf-8")
        assert demo.DEMO_MODEL in written and "MockLLM" not in written
