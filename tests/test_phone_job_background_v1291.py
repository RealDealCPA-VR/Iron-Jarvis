"""v1.291.0 (deep review io-03) - a job started FROM the phone no longer stops
the phone from being read.

``InboundPoller._handle`` used to await ``orchestrator.run_session`` INLINE
(one-shot lane and the chat lane's escalation alike), and the lifespan's
``_inbound_loop`` awaits ``poll_once`` serially - so while a phone-started job
ran, no ``getUpdates`` happened. Since v1.231.0 (AE17) a ``comm:`` session may
PAUSE on an ask-tier tool and tell the phone "reply approve/deny" - but the
reply could not even be fetched until the 300 s ask clock expired into
``needs_you``. Same for "/status" or "/cancel" sent during a long job.

Now the session runs in a tracked task (``_spawn_delivery``): the ack goes
out inline, ``_handle`` returns at once with the ``session_id``, the summary
lands on the thread + the phone when the job ends, and the lifespan cancels
the live tasks at shutdown. These pins drive the REAL app factory's poller
(prompt store, thread store, approvals registry, runtime, orchestrator) with
a fake Telegram transport shaped like the review repro.
"""

from __future__ import annotations

import asyncio
import time
import types
from typing import Any

from fastapi.testclient import TestClient

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.outcome import OUTCOME_NEEDS_YOU
from iron_jarvis.agents.types import AgentType
from iron_jarvis.comm import InboundPoller, Notifier, TelegramChannel
from iron_jarvis.comm.inbound import (
    DROPPED_REPLY,
    ESCALATE_ACK,
    INTERRUPTED_REPLY,
    ONESHOT_ACK,
    RATE_LIMIT_REPLY,
    RATE_MAX_TURNS,
)
from iron_jarvis.comm.prompts import APPROVAL_KIND
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

SENDER = 42


# --------------------------------------------------------------------------- #
# the fake phone (the review repro's shape) + a scripted model
# --------------------------------------------------------------------------- #
class FakeTelegram:
    """getUpdates/sendMessage over a queue - counts every poll."""

    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = list(updates)
        self.sent: list[dict[str, Any]] = []
        self.poll_calls = 0

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self.poll_calls += 1
        offset = int(params.get("offset", 0) or 0)
        if offset:
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
        return {"ok": True, "result": list(self.updates)}

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(payload)
        return {"status_code": 200}

    def texts(self) -> list[str]:
        return [str(p.get("text") or "") for p in self.sent]


def _update(uid: int, text: str, sender: int = SENDER) -> dict[str, Any]:
    return {
        "update_id": uid,
        "message": {
            "text": text,
            "from": {"id": sender, "is_bot": False, "first_name": "V"},
            "chat": {"id": sender},
        },
    }


def _channel(fake: FakeTelegram, *, chat: bool, chat_id: bool = True) -> TelegramChannel:
    cfg: dict[str, Any] = {
        "token_secret": "tg",
        "inbound_enabled": True,
        "allowed_senders": [SENDER],
    }
    if chat_id:
        cfg["chat_id"] = SENDER  # outbound alerts (the approval question) reach the phone
    if chat:
        cfg["chat_enabled"] = True
    return TelegramChannel(
        cfg,
        http_post=fake.post,
        http_get=fake.get,
        secret_resolver=lambda n: "BOT" if n == "tg" else None,
    )


def _shell_then_done(final: str = "Renamed the client files."):
    """Round 1 calls the ask-tier ``shell`` tool; round 2 reports done."""
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id="c0", name="shell", arguments={"command": "mv a b"}),
            ])
        else:
            resp = LLMResponse(text=final)
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _hanging_stream():
    """A model call that never returns until cancelled."""

    async def fake_stream(**kw):
        await asyncio.Event().wait()
        yield  # pragma: no cover

    return fake_stream


async def _escalating(platform_, personas, body) -> dict[str, Any]:
    return {
        "reply": "this needs an agent",
        "provider": "mock",
        "model": "m",
        "tools_used": [],
        "escalate": True,
        "escalate_reason": "multi-step",
    }


def _row(engine, sid: str) -> Session:
    with session_scope(engine) as db:
        row = db.get(Session, sid)
        db.expunge(row)
        return row


async def _run_loop_until(poller: InboundPoller, stop, *, max_iters: int) -> None:
    """``daemon/app.py::_inbound_loop``'s shape with a short interval: poll,
    sleep, repeat - until ``stop()`` says so (bounded, never a wall-clock
    assertion: the pins below assert on structure, not time)."""
    for _ in range(max_iters):
        await poller.poll_once()
        if stop():
            return
        await asyncio.sleep(0.02)


# --------------------------------------------------------------------------- #
# THE pin: the phone's own "approve" resolves the ask of the job it started
# --------------------------------------------------------------------------- #
async def test_phone_approve_resolves_the_ask_of_the_job_the_phone_started(
    tmp_path, monkeypatch
):
    # The ask clock stays LONG relative to the loop: on the old code the poll
    # never runs while the job is parked, so the ask can only end ``timeout``.
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 8.0)
    monkeypatch.setattr(runtime_mod, "ATTENDED_APPROVAL_TIMEOUT_S", 8.0)
    app = create_app(str(tmp_path))
    platform = app.state.platform
    poller: InboundPoller = app.state.inbound_poller
    pstore = app.state.pending_prompt_store
    platform.router.stream = _shell_then_done()
    poller.chat_turn = _escalating  # the turn hands the job to a session
    poller.agent_type = AgentType.BUILDER  # its ``shell`` is ask-tier
    fake = FakeTelegram([_update(1, "rename the client files")])
    ch = _channel(fake, chat=True)
    platform.notifier.add_channel("tg", ch)

    published: list[dict[str, Any]] = []
    real_publish = platform.event_bus.publish

    async def spy(etype, payload=None, session_id=None, **kw):
        published.append({"type": etype, "payload": payload or {}, "session_id": session_id})
        return await real_publish(etype, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy

    state: dict[str, Any] = {"asked_at_poll": None, "answered": False}

    def _phone_answers_once_asked() -> bool:
        """The phone taps "approve" once the ask REACHED ITS ANSWER PATH (the
        pending-prompt row the "approve" word resolves through)."""
        prompt = pstore.newest_open("tg", str(SENDER))
        if prompt is not None and getattr(prompt, "kind", "") == APPROVAL_KIND:
            if not state["answered"]:
                state["asked_at_poll"] = fake.poll_calls
                state["answered"] = True
                fake.updates.append(_update(2, "approve"))
        return any(p["type"] == "approval.resolved" for p in published)

    await _run_loop_until(poller, _phone_answers_once_asked, max_iters=600)

    asked = [p for p in published if p["type"] == "approval.requested"]
    assert asked and asked[0]["payload"].get("tool") == "shell", published
    resolved = [p for p in published if p["type"] == "approval.resolved"]
    assert resolved, "the ask never ended"
    assert resolved[-1]["payload"].get("decision") == "once", (
        "the phone's 'approve' did not reach the parked job - the clock answered"
    )
    # The poll kept READING the phone while its own job was parked.
    assert state["answered"] and fake.poll_calls > state["asked_at_poll"]
    # The job finished on the answer - not ``needs_you`` - and said so.
    await poller.drain()
    sid = asked[0]["session_id"]
    row = _row(platform.engine, sid)
    assert row.origin == "comm:tg"
    assert row.status is SessionStatus.COMPLETED, row.status
    assert row.outcome != OUTCOME_NEEDS_YOU, row.outcome
    texts = fake.texts()
    assert any(ESCALATE_ACK in t for t in texts), texts  # the ack went out inline
    assert any("Renamed the client files." in t for t in texts), texts  # the summary landed
    # ... and on the thread (the desktop hears it via chat.thread_updated).
    store = app.state.comm_thread_store
    thread = store.resolve("tg", str(SENDER), "V")
    body = store.history_body(thread.id)
    assert body[-1]["role"] == "assistant" and "Renamed the client files." in body[-1]["content"]
    assert not poller._session_tasks  # nothing left behind


# --------------------------------------------------------------------------- #
# _handle returns at once; shutdown cancels the tracked task cleanly
# --------------------------------------------------------------------------- #
async def test_one_shot_returns_at_once_and_shutdown_cancels_the_run(platform):
    platform.router.stream = _hanging_stream()
    orch = Orchestrator(platform)
    fake = FakeTelegram([_update(1, "do the long thing")])
    ch = _channel(fake, chat=False)
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(notifier, orch, platform.engine, event_bus=platform.event_bus)

    results = await poller.poll_once()  # returns while the job is still RUNNING

    assert len(results) == 1 and results[0]["status"] == "handled", results
    sid = results[0]["session_id"]
    assert results[0]["sent"] is True
    assert fake.texts() == [f"Iron Jarvis: {ONESHOT_ACK}"]  # the ack, nothing else yet
    await asyncio.sleep(0)
    assert orch.get_session(sid).status is SessionStatus.ACTIVE
    assert len(poller._session_tasks) == 1
    # The marker covers DISPATCH only: the pass cleared it although the run lives on.
    assert poller._get_offset("tg") == 2
    with session_scope(platform.engine) as db:
        from iron_jarvis.comm.models import InboundOffsetRecord

        rec = db.get(InboundOffsetRecord, "tg")
        assert rec.inflight_update_id is None and not rec.inflight_chat_id

    # A second poll pass runs fine while the job is parked (the old code
    # never got here until the job ended).
    fake.updates.append(_update(2, "hi", sender=7))  # a stranger: refused, spawns nothing
    second = await poller.poll_once()
    assert [r["status"] for r in second] == ["unauthorized"], second

    assert poller.cancel_background() == 1  # what the lifespan shutdown calls
    await poller.drain()
    assert not poller._session_tasks
    assert orch.get_session(sid).status is SessionStatus.CANCELLED
    # A shutdown cancel is not a crash: no "I hit a problem" went to the phone.
    assert fake.texts() == [f"Iron Jarvis: {ONESHOT_ACK}"]


def test_lifespan_shutdown_cancels_the_pollers_background_sessions(tmp_path, monkeypatch):
    calls: list[int] = []
    real = InboundPoller.cancel_background

    def spy(self):
        n = real(self)
        calls.append(n)
        return n

    monkeypatch.setattr(InboundPoller, "cancel_background", spy)
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert calls == []
    assert calls == [0], "the lifespan shutdown must cancel the poller's session tasks"


# --------------------------------------------------------------------------- #
# a restart mid-job tells the phone (the marker covers dispatch only now)
# --------------------------------------------------------------------------- #
CRASH_TASK = "rename the client files in the Acme folder tonight"


def _marker(engine) -> tuple[Any, str]:
    """``(inflight_update_id, inflight_chat_id)`` of the ``tg`` offset row."""
    from iron_jarvis.comm.models import InboundOffsetRecord

    with session_scope(engine) as db:
        rec = db.get(InboundOffsetRecord, "tg")
        assert rec is not None
        return rec.inflight_update_id, rec.inflight_chat_id or ""


def _boot_and_crash_mid_job(tmp_path) -> str:
    """BOOT 1 of the CRASH shape: the phone starts a job, the model hangs,
    the daemon dies with NO graceful cancel - the row stays ACTIVE and the
    dispatch-only marker is already clear. Returns the session id."""
    app1 = create_app(str(tmp_path))
    platform1 = app1.state.platform
    poller1: InboundPoller = app1.state.inbound_poller
    platform1.router.stream = _hanging_stream()
    fake1 = FakeTelegram([_update(1, CRASH_TASK)])
    platform1.notifier.add_channel("tg", _channel(fake1, chat=False))

    async def _dispatch_then_die() -> str:
        results = await poller1.poll_once()
        assert results[0]["status"] == "handled", results
        sid = results[0]["session_id"]
        await asyncio.sleep(0)
        assert _row(platform1.engine, sid).status is SessionStatus.ACTIVE
        # A crash leaves no graceful cancel: drop the task, keep the row ACTIVE
        # (the poller's shutdown flag is a lifespan matter - never set here).
        live = list(poller1._session_tasks)
        for task in live:
            task.cancel()
        await asyncio.gather(*live, return_exceptions=True)
        assert poller1._shutting_down is False
        with session_scope(platform1.engine) as db:
            row = db.get(Session, sid)
            row.status = SessionStatus.ACTIVE
            row.finished_at = None
            db.add(row)
            db.commit()
        return sid

    sid = asyncio.run(_dispatch_then_die())
    # The phone heard "On it" (the app's notifier may add its own alert lines).
    assert fake1.texts()[0] == f"Iron Jarvis: {ONESHOT_ACK}", fake1.texts()
    assert _marker(platform1.engine) == (None, "")  # the old resend path has nothing to say
    return sid


def test_a_restart_mid_job_tells_the_phone_its_job_was_cut_off(tmp_path):
    """Before v1.291.0 the inline await kept the inflight marker set for the
    whole run, so the next boot's ``_recover_inflight`` told the chat to
    resend. The marker now clears at dispatch, so that path is silent about
    a job cut off mid-run: the boot reconcile settles the row and the poller
    tells the phone - to the originating private chat (the single allowed
    sender's id) - and puts the line on the sender's thread."""
    sid = _boot_and_crash_mid_job(tmp_path)

    # BOOT 2: same state home, the phone registered before the lifespan runs.
    app2 = create_app(str(tmp_path))
    fake2 = FakeTelegram([])
    app2.state.platform.notifier.add_channel("tg", _channel(fake2, chat=True))
    with TestClient(app2) as client:
        assert client.get("/health").json()["status"] == "ok"
        expected = INTERRUPTED_REPLY.format(
            task="rename the client files in the Acme folder tonight"
        )
        wire = f"Iron Jarvis: {expected}"
        for _ in range(2000):  # wait for the thing we assert (a tracked task)
            if any(p.get("text") == wire for p in fake2.sent):
                break
            time.sleep(0.005)
        health = client.get("/diagnostics").json()["background_loops"]
    assert health["notify_interrupted_comm"]["ok"] is True, health

    notices = [p for p in fake2.sent if p.get("text") == wire]
    assert len(notices) == 1, fake2.sent  # once, and only once
    # The originating private chat (= the single allowed sender's id, explicit
    # on the wire so an inbound-only channel with no chat_id also hears it).
    assert str(notices[0]["chat_id"]) == str(SENDER)
    row = _row(app2.state.platform.engine, sid)
    assert row.status is SessionStatus.FAILED and row.interrupted_at is not None
    # ... and the line is on the thread, so the desktop reads the same story.
    store = app2.state.comm_thread_store
    body = store.history_body(store.resolve("tg", str(SENDER), "").id)
    assert body and body[-1] == {"role": "assistant", "content": expected}, body

    # Boot 3 says nothing again: only rows THIS boot stamped are announced.
    app3 = create_app(str(tmp_path))
    fake3 = FakeTelegram([])
    app3.state.platform.notifier.add_channel("tg", _channel(fake3, chat=True))
    with TestClient(app3) as client:
        assert client.get("/health").json()["status"] == "ok"
        health = client.get("/diagnostics").json()["background_loops"]
    assert health["notify_interrupted_comm"]["ok"] is True
    assert not any("cut off by a restart" in str(p.get("text")) for p in fake3.sent), fake3.sent


# --------------------------------------------------------------------------- #
# a crashed background run is an honest reply, not a silent phone
# --------------------------------------------------------------------------- #
async def test_a_crashed_background_run_tells_the_phone(platform):
    class Boom:
        async def create_session(self, task, agent_type, **kw):
            return types.SimpleNamespace(id="s-boom", summary="", origin=kw.get("origin"))

        async def run_session(self, sid):
            await asyncio.sleep(0)
            raise RuntimeError("provider down")

    fake = FakeTelegram([_update(1, "summarize my day")])
    ch = _channel(fake, chat=False)
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(notifier, Boom(), platform.engine, event_bus=platform.event_bus)

    results = await poller.poll_once()
    assert results[0]["status"] == "handled" and results[0]["session_id"] == "s-boom"
    await poller.drain()

    assert fake.texts() == [
        f"Iron Jarvis: {ONESHOT_ACK}",
        "Iron Jarvis: I hit a problem: RuntimeError: provider down",
    ]


# --------------------------------------------------------------------------- #
# overlapping jobs from one sender count against the flood guard
# --------------------------------------------------------------------------- #
async def test_one_shot_lane_counts_against_the_rate_cap(platform):
    class Counting:
        def __init__(self) -> None:
            self.created = 0

        async def create_session(self, task, agent_type, **kw):
            self.created += 1
            return types.SimpleNamespace(id=f"s{self.created}", summary="ok")

        async def run_session(self, sid):
            return types.SimpleNamespace(id=sid, summary="done")

    orch = Counting()
    fake = FakeTelegram([])
    ch = _channel(fake, chat=False)
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(
        notifier, orch, platform.engine, event_bus=platform.event_bus, clock=lambda: 100.0
    )
    for i in range(RATE_MAX_TURNS):
        fake.updates.append(_update(i + 1, f"job {i}"))
    burst = await poller.poll_once()
    assert [r["status"] for r in burst] == ["handled"] * RATE_MAX_TURNS
    assert orch.created == RATE_MAX_TURNS

    fake.updates.append(_update(RATE_MAX_TURNS + 1, "one more"))
    res = await poller.poll_once()
    assert res == [{"channel": "tg", "status": "rate_limited", "sender": str(SENDER), "sent": True}]
    assert orch.created == RATE_MAX_TURNS  # no ninth session
    assert fake.texts()[-1] == f"Iron Jarvis: {RATE_LIMIT_REPLY}"
    await poller.drain()


# --------------------------------------------------------------------------- #
# one identity at a time: a delivery in flight and a new message never race
# --------------------------------------------------------------------------- #
async def test_messages_to_one_identity_are_serialized_against_its_delivery(platform):
    fake = FakeTelegram([])
    ch = _channel(fake, chat=False)
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(notifier, Orchestrator(platform), platform.engine)
    from iron_jarvis.comm.base import InboundMessage

    mine = InboundMessage(sender_id=SENDER, text="", update_id=5, reply_to=SENDER)
    lock = poller._identity_lock("tg", SENDER)
    async with lock:  # a summary delivery to this chat is mid-flight
        held = asyncio.create_task(poller._handle("tg", ch, mine))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not held.done(), "a message to a chat mid-delivery must wait its turn"
        # Another identity is not held up by this one's lock.
        other = InboundMessage(sender_id=7, text="", update_id=6, reply_to=7)
        assert await poller._handle("tg", ch, other) == {
            "channel": "tg", "status": "unauthorized", "sender": 7,
        }
    assert await held == {"channel": "tg", "status": "empty"}


# --------------------------------------------------------------------------- #
# a GRACEFUL restart mid-job (app update / close / `ironjarvis stop`) still
# tells the phone: the shutdown cancel re-arms the at-most-once marker
# --------------------------------------------------------------------------- #
def test_a_graceful_shutdown_mid_job_re_arms_the_marker_so_the_next_boot_tells_the_phone(
    tmp_path,
):
    """The lifespan's ``cancel_background`` unwinds ``run_session`` to
    CANCELLED with ``interrupted_at`` None, so the boot reconcile and
    ``notify_interrupted`` have nothing to say about it. HEAD told the phone
    here through the inflight marker (the inline await held it through the
    shutdown cancel -> ``DROPPED_REPLY`` on the next boot). The background
    task must keep that: the SHUTDOWN's cancel re-arms the marker; the next
    boot's ``_recover_inflight`` tells the originating chat to resend."""
    # BOOT 1: the REAL lifespan; the phone starts a job; the model hangs.
    app1 = create_app(str(tmp_path))
    platform1 = app1.state.platform
    poller1: InboundPoller = app1.state.inbound_poller
    platform1.router.stream = _hanging_stream()
    fake1 = FakeTelegram([_update(1, "do the long thing")])
    platform1.notifier.add_channel("tg", _channel(fake1, chat=False))
    with TestClient(app1) as client:
        assert client.get("/health").json()["status"] == "ok"
        results = client.portal.call(poller1.poll_once)
        assert results[0]["status"] == "handled", results
        sid = results[0]["session_id"]
        assert _marker(platform1.engine) == (None, "")  # dispatch done: clear
        assert client.get(f"/sessions/{sid}").json()["session"]["status"] == "active"
    # ... the context exit IS the graceful shutdown (an app update / `stop`).
    assert fake1.texts()[0] == f"Iron Jarvis: {ONESHOT_ACK}", fake1.texts()
    assert not any(DROPPED_REPLY in t for t in fake1.texts())
    row = _row(platform1.engine, sid)
    assert row.status is SessionStatus.CANCELLED and row.interrupted_at is None
    assert _marker(platform1.engine) == (1, str(SENDER)), "the shutdown must re-arm the marker"

    # BOOT 2: a fresh phone transport on the same state home.
    app2 = create_app(str(tmp_path))
    poller2: InboundPoller = app2.state.inbound_poller
    fake2 = FakeTelegram([])
    app2.state.platform.notifier.add_channel("tg", _channel(fake2, chat=False))
    with TestClient(app2) as client:
        assert client.get("/health").json()["status"] == "ok"
        passed = client.portal.call(poller2.poll_once)
        health = client.get("/diagnostics").json()["background_loops"]
    assert health["notify_interrupted_comm"]["ok"] is True, health
    assert passed and passed[0]["status"] == "dropped" and passed[0]["notified"] is True, passed
    wire = f"Iron Jarvis: {DROPPED_REPLY}"
    notices = [p for p in fake2.sent if p.get("text") == wire]
    assert len(notices) == 1 and str(notices[0]["chat_id"]) == str(SENDER), fake2.sent
    # One story only: the CANCELLED row is not also announced as "cut off".
    assert not any("cut off by a restart" in str(p.get("text")) for p in fake2.sent), fake2.sent
    assert _row(app2.state.platform.engine, sid).interrupted_at is None
    assert _marker(app2.state.platform.engine) == (None, "")  # cleared: said once


async def test_a_desktop_cancel_of_a_phone_job_does_not_re_arm_the_marker(platform):
    """The user's own Cancel (POST /sessions/{id}/cancel ->
    ``orchestrator.cancel_session``) cancels the SAME task as the shutdown
    does - but on purpose, with the daemon alive: no marker, no "please
    resend" on the next boot."""
    platform.router.stream = _hanging_stream()
    orch = Orchestrator(platform)
    fake = FakeTelegram([_update(1, "do the long thing")])
    notifier = Notifier()
    notifier.add_channel("tg", _channel(fake, chat=False))
    poller = InboundPoller(notifier, orch, platform.engine, event_bus=platform.event_bus)

    results = await poller.poll_once()
    sid = results[0]["session_id"]
    await asyncio.sleep(0)
    assert orch.get_session(sid).status is SessionStatus.ACTIVE
    assert len(poller._session_tasks) == 1

    orch.cancel_session(sid)  # the desktop's Cancel, while the task runs
    await poller.drain()
    assert orch.get_session(sid).status is SessionStatus.CANCELLED
    assert poller._shutting_down is False
    assert _marker(platform.engine) == (None, ""), "a desktop cancel must not re-arm the marker"
    assert fake.texts() == [f"Iron Jarvis: {ONESHOT_ACK}"]


# --------------------------------------------------------------------------- #
# the crash-restart notice reaches an INBOUND-ONLY channel (no chat_id set)
# --------------------------------------------------------------------------- #
def test_the_restart_notice_reaches_a_channel_with_no_chat_id_configured(tmp_path):
    """A Telegram channel set up inbound-only (allowed senders, no
    ``chat_id``) fails a bare ``send`` with "config needs `chat_id`". With
    exactly one allowed sender the originating private chat is knowable -
    its id IS the sender id (the fallback ``_set_offset`` uses) - so the
    notice goes there explicitly."""
    sid = _boot_and_crash_mid_job(tmp_path)

    app2 = create_app(str(tmp_path))
    fake2 = FakeTelegram([])
    ch2 = _channel(fake2, chat=True, chat_id=False)
    assert ch2.send("probe")["ok"] is False  # the bare send has nowhere to go
    app2.state.platform.notifier.add_channel("tg", ch2)
    expected = INTERRUPTED_REPLY.format(task=CRASH_TASK)
    wire = f"Iron Jarvis: {expected}"
    with TestClient(app2) as client:
        assert client.get("/health").json()["status"] == "ok"
        for _ in range(2000):  # wait for the thing we assert (a tracked task)
            if any(p.get("text") == wire for p in fake2.sent):
                break
            time.sleep(0.005)
        health = client.get("/diagnostics").json()["background_loops"]
    assert health["notify_interrupted_comm"]["ok"] is True, health
    notices = [p for p in fake2.sent if p.get("text") == wire]
    assert len(notices) == 1, fake2.sent
    assert str(notices[0]["chat_id"]) == str(SENDER)  # the originating private chat
    assert _row(app2.state.platform.engine, sid).status is SessionStatus.FAILED
    store = app2.state.comm_thread_store
    body = store.history_body(store.resolve("tg", str(SENDER), "").id)
    assert body and body[-1] == {"role": "assistant", "content": expected}, body


def test_resume_background_clears_the_shutdown_flag():
    """Review: the flag is per lifespan. A poller whose lifespan stopped once
    (cancel_background) and restarted must not treat a later desktop Cancel as
    a shutdown, or every cancelled phone job would re-arm the marker."""
    from iron_jarvis.comm import Notifier
    from iron_jarvis.comm.inbound import InboundPoller

    poller = InboundPoller(Notifier(), None, None)
    assert poller._shutting_down is False
    poller.cancel_background()
    assert poller._shutting_down is True
    poller.resume_background()
    assert poller._shutting_down is False


def test_rate_limit_reply_tells_the_phone_to_resend():
    """Review: the 9th job in a minute is discarded (the offset has advanced),
    so "pausing" alone reads as "I'll get to it"."""
    assert "again" in RATE_LIMIT_REPLY
