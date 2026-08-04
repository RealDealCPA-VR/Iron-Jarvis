"""Offline tests for PENDING PROMPTS (v1.137.0 messaging surfaces, Pair Q).

A workflow run parked at an ask gate becomes answerable from the phone: the
``workflow.waiting`` event registers a durable prompt per chat-enabled comm
identity (thread line + chunked send), a bare in-range numbered reply or an
explicit ``/answer <text>`` resolves it through the SAME atomic-claim answer
path as POST /workflows/runs/{id}/answer (first answer wins), and everything
else stays normal chat with a gentle reminder on the outbound copy only.
The answer path is INJECTED (a fake async callable, the ``chat_turn`` idiom)
— no model calls, no network; the real claim is proven against seeded run
records.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import InboundMessage, MockChannel, Notifier
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS, format_event
from iron_jarvis.comm.prompts import (
    ALREADY_ANSWERED_REPLY,
    ANSWER_USAGE_REPLY,
    NOTHING_WAITING_REPLY,
    PendingPromptStore,
    answer_parked_run,
    handle_workflow_waiting,
)
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import dumps, session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.workflows.models import WorkflowRunRecord


# --------------------------------------------------------------------------- #
# Fakes + seeds (the test_comm_full_chat idioms)
# --------------------------------------------------------------------------- #
class ChatMockChannel(MockChannel):
    """MockChannel with a receive leg + credentials, for full-chat tests."""

    supports_inbound = True

    def has_credentials(self) -> bool:  # no token needed offline
        return True


CHAT_CFG = {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]}


def _msg(text: str, sender: str = "777", update_id: int = 1) -> InboundMessage:
    return InboundMessage(
        sender_id=str(sender), text=text, update_id=update_id, reply_to=sender
    )


def _fake_turn(reply: str = "All good."):
    async def turn(platform, personas, body) -> dict[str, Any]:
        turn.calls.append(body)
        return {
            "reply": reply,
            "provider": "mock",
            "model": "m",
            "tools_used": [],
            "escalate": False,
            "escalate_reason": "",
        }

    turn.calls = []
    return turn


def _fake_answer(ok: bool = True, run_name: str = "gated"):
    """The injected answer path — records calls, wins or loses the claim."""

    async def answer(run_id: str, text: str) -> dict[str, Any]:
        answer.calls.append((run_id, text))
        if ok:
            return {"ok": True, "run_name": run_name}
        return {"ok": False, "status": "running"}

    answer.calls = []
    return answer


def _seed_run(engine, status: str = "waiting", question: str = "Go?") -> str:
    from iron_jarvis.workflows.engine import Step, step_to_dict

    steps = [
        step_to_dict(Step(name="Approve", kind="ask", message=question)),
        step_to_dict(Step(name="Send", kind="notify", message="Sent after: {{Approve}}")),
    ]
    rec = WorkflowRunRecord(
        workflow_name="gated",
        status=status,
        steps_json=dumps(steps),
        outputs_json="{}",
        session_ids_json="[]",
        waiting_json=(
            dumps({"index": 0, "step": "Approve", "question": question})
            if status == "waiting"
            else ""
        ),
    )
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
    return rec.id


def _run_status(engine, run_id: str) -> str:
    with session_scope(engine) as db:
        return db.get(WorkflowRunRecord, run_id).status


def _prompt_status(store: PendingPromptStore, prompt_id: str) -> str:
    from iron_jarvis.comm.models import PendingPromptRecord

    with session_scope(store.engine) as db:
        return db.get(PendingPromptRecord, prompt_id).status


def _poller(platform, ch, *, turn=None, answer=None, command_interpreter=None):
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    orch = Orchestrator(platform)
    tstore = CommThreadStore(platform.engine)
    pstore = PendingPromptStore(platform.engine)
    poller = InboundPoller(
        notifier,
        orch,
        platform.engine,
        event_bus=platform.event_bus,
        command_interpreter=command_interpreter,
        thread_store=tstore,
        chat_turn=turn if turn is not None else _fake_turn(),
        personas={},
        platform=platform,
        prompt_store=pstore,
        answer_run=answer,
    )
    return poller, orch, tstore, pstore, notifier


def _waiting_event(run_id: str, question: str = "Go?", **payload: Any) -> dict:
    return {
        "type": "workflow.waiting",
        "payload": {
            "run_id": run_id,
            "workflow": "gated",
            "step": "Approve",
            "question": question,
            **payload,
        },
    }


def _register(pstore, run_id, *, options=None, sender="777", thread_id=""):
    return pstore.register(
        "workflow_ask", run_id, "Go?", options or [], "tg", sender, thread_id
    )


# --------------------------------------------------------------------------- #
# store CRUD + supersede
# --------------------------------------------------------------------------- #
def test_store_register_newest_open_and_counts(platform):
    pstore = PendingPromptStore(platform.engine)
    assert pstore.newest_open("tg", "777") is None
    assert pstore.open_count("tg", "777") == 0

    rec = pstore.register(
        "workflow_ask", "run1", "Go?", ["Send it", "Hold"], "tg", "777", "th1"
    )
    assert rec is not None and rec.id.startswith("pp")
    assert rec.status == "open" and rec.decided_at is None
    assert rec.options_json == '["Send it", "Hold"]'

    got = pstore.newest_open("tg", "777")
    assert got is not None and got.id == rec.id
    assert pstore.open_count("tg", "777") == 1
    # Identity isolation: other senders/channels see nothing.
    assert pstore.newest_open("tg", "888") is None
    assert pstore.newest_open("slack", "777") is None


def test_store_register_supersedes_same_gate_same_identity(platform):
    pstore = PendingPromptStore(platform.engine)
    old = _register(pstore, "run1")
    new = _register(pstore, "run1")
    assert _prompt_status(pstore, old.id) == "superseded"
    assert pstore.newest_open("tg", "777").id == new.id
    assert pstore.open_count("tg", "777") == 1
    # A DIFFERENT run's prompt is untouched — both stay open.
    other = _register(pstore, "run2")
    assert pstore.open_count("tg", "777") == 2
    assert pstore.newest_open("tg", "777").id == other.id  # newest wins


def test_store_resolve_and_expire_markers(platform):
    pstore = PendingPromptStore(platform.engine)
    rec = _register(pstore, "run1")
    assert pstore.resolve(rec.id, "yes") is True
    assert _prompt_status(pstore, rec.id) == "answered"
    assert pstore.resolve(rec.id, "again") is False  # only OPEN prompts decide

    rec2 = _register(pstore, "run2")
    assert pstore.expire(rec2.id, status="superseded") is True
    assert _prompt_status(pstore, rec2.id) == "superseded"
    rec3 = _register(pstore, "run3")
    assert pstore.expire(rec3.id, status="bogus") is True  # coerced, not raised
    assert _prompt_status(pstore, rec3.id) == "expired"
    assert pstore.expire("missing", status="expired") is False
    assert pstore.open_count("tg", "777") == 0
    # Decided rows carry a decided_at stamp.
    from iron_jarvis.comm.models import PendingPromptRecord

    with session_scope(platform.engine) as db:
        assert db.get(PendingPromptRecord, rec.id).decided_at is not None


# --------------------------------------------------------------------------- #
# registration: the workflow.waiting handler
# --------------------------------------------------------------------------- #
def test_waiting_event_registers_prompt_appends_thread_and_sends(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    _p, _o, tstore, pstore, notifier = _poller(platform, ch)
    thread = tstore.resolve("tg", "777", "Val")
    run_id = _seed_run(platform.engine)

    out = handle_workflow_waiting(
        _waiting_event(run_id), store=pstore, notifier=notifier, thread_store=tstore
    )

    assert len(out) == 1 and out[0]["sent"] is True
    prompt = pstore.newest_open("tg", "777")
    assert prompt is not None
    assert prompt.kind == "workflow_ask" and prompt.ref_id == run_id
    assert prompt.question == "Go?" and prompt.thread_id == thread.id
    line = tstore.history_body(thread.id)[-1]
    assert line["role"] == "assistant"
    assert "Workflow 'gated' is waiting: Go?" in line["content"]
    assert "/answer" in line["content"]
    assert ch.sent and ch.sent[-1].startswith("Iron Jarvis: Workflow 'gated' is waiting")


def test_waiting_event_with_options_enumerates_numbered_picks(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    _p, _o, tstore, pstore, notifier = _poller(platform, ch)
    tstore.resolve("tg", "777", "Val")
    run_id = _seed_run(platform.engine)

    handle_workflow_waiting(
        _waiting_event(run_id, options=["Send it", "Hold"]),
        store=pstore, notifier=notifier, thread_store=tstore,
    )

    prompt = pstore.newest_open("tg", "777")
    assert prompt.options_json == '["Send it", "Hold"]'
    assert "1. Send it" in ch.sent[-1] and "2. Hold" in ch.sent[-1]
    assert "Reply with a number" in ch.sent[-1]


def test_waiting_event_skips_non_chat_unauthorized_and_empty(platform):
    # Channel A: chat-enabled but the identity's sender fell OFF the allowlist.
    # Channel B: identity exists but chat is off. Channel C: chat on, nobody
    # has ever talked (no identity rows). None of them register anything.
    a = ChatMockChannel({**CHAT_CFG, "allowed_senders": ["someone-else"]})
    b = ChatMockChannel({"inbound_enabled": True, "allowed_senders": ["777"]})
    c = ChatMockChannel(dict(CHAT_CFG))
    tstore = CommThreadStore(platform.engine)
    pstore = PendingPromptStore(platform.engine)
    notifier = Notifier()
    notifier.add_channel("a", a)
    notifier.add_channel("b", b)
    notifier.add_channel("c", c)
    tstore.resolve("a", "777", "Val")
    tstore.resolve("b", "777", "Val")
    run_id = _seed_run(platform.engine)

    out = handle_workflow_waiting(
        _waiting_event(run_id), store=pstore, notifier=notifier, thread_store=tstore
    )

    assert out == []
    for name in ("a", "b", "c"):
        assert pstore.newest_open(name, "777") is None
    assert a.sent == [] and b.sent == [] and c.sent == []


def test_waiting_event_twice_leaves_one_open_prompt(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    _p, _o, tstore, pstore, notifier = _poller(platform, ch)
    tstore.resolve("tg", "777", "Val")
    run_id = _seed_run(platform.engine)

    for _ in range(2):
        handle_workflow_waiting(
            _waiting_event(run_id), store=pstore, notifier=notifier, thread_store=tstore
        )

    assert pstore.open_count("tg", "777") == 1


def test_waiting_handler_ignores_other_events_and_bad_payloads(platform):
    pstore = PendingPromptStore(platform.engine)
    notifier = Notifier()
    assert handle_workflow_waiting(
        {"type": "workflow.completed", "payload": {"run_id": "x"}},
        store=pstore, notifier=notifier, thread_store=None,
    ) == []
    assert handle_workflow_waiting(
        {"type": "workflow.waiting", "payload": {}},
        store=pstore, notifier=notifier, thread_store=None,
    ) == []
    # A hostile shape must never raise (the bus survives any handler).
    assert handle_workflow_waiting(
        None, store=pstore, notifier=notifier, thread_store=None
    ) == []


def test_waiting_event_missing_question_falls_back_to_run_record(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    _p, _o, tstore, pstore, notifier = _poller(platform, ch)
    tstore.resolve("tg", "777", "Val")
    run_id = _seed_run(platform.engine, question="Ship the draft?")

    evt = _waiting_event(run_id)
    evt["payload"].pop("question")
    handle_workflow_waiting(evt, store=pstore, notifier=notifier, thread_store=tstore)

    assert pstore.newest_open("tg", "777").question == "Ship the draft?"


# --------------------------------------------------------------------------- #
# resolution: numbered pick via the injected atomic-claim answer path
# --------------------------------------------------------------------------- #
async def test_numbered_reply_resolves_via_answer_path(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer()
    poller, orch, tstore, pstore, _n = _poller(platform, ch, answer=answer)
    run_id = _seed_run(platform.engine)
    _register(pstore, run_id, options=["Send it", "Hold"])

    res = await poller._handle("tg", ch, _msg("2"))

    assert res["status"] == "answered" and res["sent"] is True
    assert answer.calls == [(run_id, "Hold")]  # the OPTION VALUE, not "2"
    prompt = res["prompt_id"]
    assert _prompt_status(pstore, prompt) == "answered"
    # The exchange landed on the thread: user pick + assistant echo.
    msgs = tstore.history_body(res["thread_id"])
    assert msgs[-2] == {"role": "user", "content": "2"}
    assert "→ Answered 'Go?' on run gated" in msgs[-1]["content"]
    assert "resuming" in msgs[-1]["content"]
    assert "→ Answered 'Go?' on run gated" in ch.sent[-1]
    assert orch.list_sessions() == []  # no session, no chat turn


async def test_claim_loser_gets_honest_reply_and_superseded(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer(ok=False)
    poller, _o, _t, pstore, _n = _poller(platform, ch, answer=answer)
    run_id = _seed_run(platform.engine)  # still "waiting" — the fake claim races
    rec = _register(pstore, run_id, options=["Send it"])

    res = await poller._handle("tg", ch, _msg("1"))

    assert res["status"] == "answer_superseded"
    assert answer.calls == [(run_id, "Send it")]
    assert _prompt_status(pstore, rec.id) == "superseded"
    assert ALREADY_ANSWERED_REPLY in ch.sent[-1]


async def test_out_of_range_integer_stays_chat(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("chatting")
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, turn=turn, answer=answer)
    run_id = _seed_run(platform.engine)
    rec = _register(pstore, run_id, options=["Send it", "Hold"])

    res = await poller._handle("tg", ch, _msg("9"))  # out of range
    assert res["status"] == "chat" and answer.calls == []
    assert _prompt_status(pstore, rec.id) == "open"
    assert "(A workflow is still waiting" in ch.sent[-1]  # re-pointed, not lost


async def test_optionless_integer_resolves_as_literal_answer(platform):
    # Workflow ask steps carry no options today; the park alert still promises
    # "reply with a number or /answer" — so a bare pure integer must resolve
    # the optionless gate with the integer ITSELF as the answer.
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("chatting")
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, turn=turn, answer=answer)
    run_id = _seed_run(platform.engine, question="How many clients?")
    rec = pstore.register(
        "workflow_ask", run_id, "How many clients?", [], "tg", "777", ""
    )

    res = await poller._handle("tg", ch, _msg("42"))

    assert res["status"] == "answered"
    assert answer.calls == [(run_id, "42")]  # the literal integer IS the answer
    assert _prompt_status(pstore, rec.id) == "answered"
    assert "→ Answered 'How many clients?'" in ch.sent[-1]
    assert turn.calls == []  # never became a chat turn


async def test_unicode_digit_lookalikes_stay_chat_without_crashing(platform):
    # "²" passes str.isdigit() but int("²") raises ValueError — the intercept
    # must use isdecimal and treat it as normal chat, never crash the pipeline.
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("chatting")
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, turn=turn, answer=answer)
    run_id = _seed_run(platform.engine)
    rec = _register(pstore, run_id)

    res = await poller._handle("tg", ch, _msg("²"))

    assert res["status"] == "chat" and answer.calls == []
    assert _prompt_status(pstore, rec.id) == "open"


# --------------------------------------------------------------------------- #
# free-form with an open prompt: normal turn + reminder on the phone copy only
# --------------------------------------------------------------------------- #
async def test_free_form_gets_reminder_suffix_on_outbound_only(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("Here's your answer.")
    poller, _o, tstore, pstore, _n = _poller(platform, ch, turn=turn, answer=_fake_answer())
    run_id = _seed_run(platform.engine)
    _register(pstore, run_id)

    res = await poller._handle("tg", ch, _msg("what's the weather like?"))

    assert res["status"] == "chat"
    assert len(turn.calls) == 1  # the NORMAL turn ran
    # Outbound copy carries the reminder…
    assert "Here's your answer." in ch.sent[-1]
    assert "(A workflow is still waiting: 'Go?'" in ch.sent[-1]
    assert "/answer" in ch.sent[-1]
    # …the thread does NOT.
    msgs = tstore.history_body(res["thread_id"])
    assert msgs[-1] == {"role": "assistant", "content": "Here's your answer."}
    assert pstore.open_count("tg", "777") == 1  # nothing resolved


async def test_no_reminder_without_open_prompt(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _o, _t, _p, _n = _poller(platform, ch, turn=_fake_turn("plain"))
    await poller._handle("tg", ch, _msg("hi"))
    assert ch.sent[-1] == "Iron Jarvis: plain"


# --------------------------------------------------------------------------- #
# /answer — explicit resolution, mid-conversation or not
# --------------------------------------------------------------------------- #
async def test_answer_command_resolves_free_text(platform):
    class UnknownInterpreter:
        async def interpret(self, text: str):
            return f"Unknown command '{text.split()[0]}'."

    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer()
    poller, _o, tstore, pstore, _n = _poller(
        platform, ch, answer=answer, command_interpreter=UnknownInterpreter()
    )
    run_id = _seed_run(platform.engine)
    rec = _register(pstore, run_id)

    res = await poller._handle("tg", ch, _msg("/answer yes, ship it"))

    # The poller intercepts BEFORE the grammar — no "Unknown command".
    assert res["status"] == "answered"
    assert answer.calls == [(run_id, "yes, ship it")]
    assert _prompt_status(pstore, rec.id) == "answered"
    assert "→ Answered 'Go?' on run gated" in ch.sent[-1]


async def test_answer_command_numeric_argument_maps_to_option(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, answer=answer)
    run_id = _seed_run(platform.engine)
    _register(pstore, run_id, options=["Send it", "Hold"])

    res = await poller._handle("tg", ch, _msg("/answer 1"))

    assert res["status"] == "answered"
    assert answer.calls == [(run_id, "Send it")]


async def test_answer_command_nothing_waiting_and_usage(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, answer=answer)

    res = await poller._handle("tg", ch, _msg("/answer yes"))
    assert res["status"] == "answer_none"
    assert NOTHING_WAITING_REPLY in ch.sent[-1]

    run_id = _seed_run(platform.engine)
    _register(pstore, run_id)
    res2 = await poller._handle("tg", ch, _msg("/answer", update_id=2))
    assert res2["status"] == "answer_usage"
    assert ANSWER_USAGE_REPLY in ch.sent[-1]
    assert answer.calls == []


# --------------------------------------------------------------------------- #
# expiry: a gate that un-parked elsewhere is never resolved or nagged about
# --------------------------------------------------------------------------- #
async def test_integer_for_unparked_run_gets_honest_reply_not_chat(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("just chat")
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, turn=turn, answer=answer)
    run_id = _seed_run(platform.engine, status="completed")  # answered elsewhere
    rec = _register(pstore, run_id, options=["Send it"])

    res = await poller._handle("tg", ch, _msg("1"))

    # The pick aimed at a dead gate: honest reply, NOT a chat-turn misfire
    # on the bare "1" (and never a resolution of the un-parked run).
    assert res["status"] == "answer_expired"
    assert answer.calls == [] and turn.calls == []
    assert _prompt_status(pstore, rec.id) == "expired"
    assert ALREADY_ANSWERED_REPLY in ch.sent[-1]
    assert "(A workflow is still waiting" not in ch.sent[-1]  # no stale nag

    # A CANCELLED run was not "answered from the desktop" — honest variant.
    run2 = _seed_run(platform.engine, status="cancelled")
    rec2 = _register(pstore, run2)
    res2 = await poller._handle("tg", ch, _msg("7", update_id=2))
    assert res2["status"] == "answer_expired"
    assert _prompt_status(pstore, rec2.id) == "expired"
    assert NOTHING_WAITING_REPLY in ch.sent[-1]
    assert turn.calls == []


async def test_answer_command_on_expired_run_says_nothing_waiting(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, answer=answer)
    run_id = _seed_run(platform.engine, status="cancelled")
    rec = _register(pstore, run_id)

    res = await poller._handle("tg", ch, _msg("/answer yes"))

    assert res["status"] == "answer_none"
    assert _prompt_status(pstore, rec.id) == "expired"
    assert answer.calls == []


# --------------------------------------------------------------------------- #
# commands + security posture around open prompts
# --------------------------------------------------------------------------- #
async def test_commands_still_work_with_open_prompt(platform):
    class FakeInterpreter:
        async def interpret(self, text: str):
            return "status: all good" if text.startswith("/status") else None

    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(
        platform, ch, answer=answer, command_interpreter=FakeInterpreter()
    )
    run_id = _seed_run(platform.engine)
    rec = _register(pstore, run_id, options=["Send it"])

    res = await poller._handle("tg", ch, _msg("/status"))

    assert res["status"] == "command"
    assert answer.calls == []  # a command is never an answer
    assert _prompt_status(pstore, rec.id) == "open"  # the gate still waits


async def test_unauthorized_sender_cannot_answer(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))  # allowlist = ["777"] only
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, answer=answer)
    run_id = _seed_run(platform.engine)
    rec = _register(pstore, run_id, options=["Send it"])

    for text in ("/answer yes", "1"):
        res = await poller._handle("tg", ch, _msg(text, sender="999"))
        assert res["status"] == "unauthorized"  # the allowlist ran FIRST

    assert answer.calls == []
    assert _prompt_status(pstore, rec.id) == "open"
    assert ch.sent == []  # strangers hear nothing


async def test_prompt_for_identity_a_not_answerable_by_identity_b(platform):
    cfg = {**CHAT_CFG, "allowed_senders": ["777", "888"]}
    ch = ChatMockChannel(cfg)
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, answer=answer)
    run_id = _seed_run(platform.engine)
    rec = _register(pstore, run_id, options=["Send it"], sender="777")

    res = await poller._handle("tg", ch, _msg("/answer yes", sender="888"))
    assert res["status"] == "answer_none"  # 888 has no prompt of their own
    res2 = await poller._handle("tg", ch, _msg("1", sender="888", update_id=2))
    assert res2["status"] == "chat"  # a bare pick is just chat for 888

    assert answer.calls == []
    assert _prompt_status(pstore, rec.id) == "open"  # 777's gate untouched


# --------------------------------------------------------------------------- #
# lifecycle: back-to-back parked runs + /new thread resets
# --------------------------------------------------------------------------- #
async def test_second_open_prompt_surfaces_after_first_resolves(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer()
    poller, _o, tstore, pstore, _n = _poller(platform, ch, answer=answer)
    run_a = _seed_run(platform.engine, question="Which client?")
    run_b = _seed_run(platform.engine, question="Ship it?")
    pa = pstore.register("workflow_ask", run_a, "Which client?", [], "tg", "777", "")
    time.sleep(0.005)  # order the created_at stamps deterministically
    pb = pstore.register("workflow_ask", run_b, "Ship it?", [], "tg", "777", "")
    assert pstore.newest_open("tg", "777").id == pb.id

    res = await poller._handle("tg", ch, _msg("/answer yes"))

    # The NEWEST prompt resolved…
    assert res["status"] == "answered" and res["run_id"] == run_b
    assert _prompt_status(pstore, pb.id) == "answered"
    # …and the phone copy points at the still-open OLDER gate (outbound only —
    # without this, the older prompt would sit invisible until the next chat).
    assert "→ Answered 'Ship it?'" in ch.sent[-1]
    assert "(A workflow is still waiting: 'Which client?'" in ch.sent[-1]
    assert "(A workflow is still waiting" not in (
        tstore.history_body(res["thread_id"])[-1]["content"]
    )
    # The older prompt is now the newest-open, answerable in turn.
    res2 = await poller._handle("tg", ch, _msg("/answer acme", update_id=2))
    assert res2["status"] == "answered" and res2["run_id"] == run_a
    assert _prompt_status(pstore, pa.id) == "answered"
    assert answer.calls == [(run_b, "yes"), (run_a, "acme")]
    assert "(A workflow is still waiting" not in ch.sent[-1]  # nothing left


async def test_prompt_survives_new_thread_reset(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    answer = _fake_answer()
    poller, _o, tstore, pstore, _n = _poller(platform, ch, answer=answer)
    old_thread = tstore.resolve("tg", "777", "Val")
    run_id = _seed_run(platform.engine)
    rec = _register(pstore, run_id, thread_id=old_thread.id)

    res_new = await poller._handle("tg", ch, _msg("/new"))
    assert res_new["status"] == "new_thread"

    # The prompt is IDENTITY-keyed, not thread-keyed: it still resolves after
    # the reset, and the exchange lands on the fresh thread /new minted.
    res = await poller._handle("tg", ch, _msg("/answer yes", update_id=2))
    assert res["status"] == "answered"
    assert _prompt_status(pstore, rec.id) == "answered"
    assert res["thread_id"] and res["thread_id"] != old_thread.id


# --------------------------------------------------------------------------- #
# the REAL answer path: atomic claim + resume (route semantics, engine-level)
# --------------------------------------------------------------------------- #
def test_concurrent_answers_exactly_one_claim(platform):
    # A REAL race: two threads fire the atomic claim simultaneously (the phone
    # poller vs the desktop route's identical CAS). Exactly one may win — a
    # double win would resume the tail TWICE (duplicate sessions, duplicate
    # notify sends). The sequential first-answer-wins test below proves the
    # logic; this one proves it under genuine thread concurrency.
    run_id = _seed_run(platform.engine)
    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []
    lock = threading.Lock()

    def contend(answer_text: str) -> None:
        async def go():
            barrier.wait()
            return await answer_parked_run(platform, None, None, run_id, answer_text)

        res = asyncio.run(go())
        with lock:
            results.append(res)

    threads = [
        threading.Thread(target=contend, args=(t,)) for t in ("yes", "no")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = [r for r in results if r.get("ok")]
    losers = [r for r in results if not r.get("ok")]
    assert len(wins) == 1 and len(losers) == 1  # exactly one resume, ever
    assert "resume" not in losers[0]
    assert losers[0]["status"] in ("resuming", "waiting")
    wins[0]["resume"].close()  # the single handed-out resume; not driven here
    assert _run_status(platform.engine, run_id) == "resuming"



async def test_answer_parked_run_claims_and_resumes(platform):
    run_id = _seed_run(platform.engine)

    res = await answer_parked_run(platform, None, None, run_id, "approved")

    assert res["ok"] is True and res["run_name"] == "gated"
    assert _run_status(platform.engine, run_id) == "resuming"  # claim landed
    # No spawner injected — the resume coroutine is handed back; drive it.
    final = await res["resume"]
    assert final.status == "completed"
    import json as _json

    outs = _json.loads(final.outputs_json)
    assert outs["Approve"]["summary"] == "User answered: approved"
    assert "approved" in outs["Send"]["summary"]


async def test_answer_parked_run_first_answer_wins(platform):
    run_id = _seed_run(platform.engine)
    spawned: list[Any] = []

    first = await answer_parked_run(
        platform, None, lambda rid, coro: spawned.append(coro), run_id, "yes"
    )
    second = await answer_parked_run(platform, None, None, run_id, "no")

    assert first["ok"] is True and len(spawned) == 1
    assert second["ok"] is False and second["status"] == "resuming"
    spawned[0].close()  # not driven in this test

    assert (await answer_parked_run(platform, None, None, "missing", "x")) == {
        "ok": False, "status": "missing",
    }
    assert (await answer_parked_run(platform, None, None, run_id, "  ")) == {
        "ok": False, "status": "empty_answer",
    }


# --------------------------------------------------------------------------- #
# daemon wiring: the bus handler + poller injection exist in create_app
# --------------------------------------------------------------------------- #
def test_app_wires_prompt_store_answer_path_and_bus_handler(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        app = client.app
        pstore = app.state.pending_prompt_store
        poller = app.state.inbound_poller
        assert isinstance(pstore, PendingPromptStore)
        assert poller.prompt_store is pstore
        assert poller.answer_run is not None

        # Publish the park event through the REAL bus: the registered handler
        # registers the prompt, appends the thread line, and pings the phone.
        platform = app.state.platform
        ch = ChatMockChannel(dict(CHAT_CFG))
        platform.notifier.add_channel("tg", ch)
        tstore = app.state.comm_thread_store
        thread = tstore.resolve("tg", "777", "Val")
        run_id = _seed_run(platform.engine)

        asyncio.run(
            platform.event_bus.publish(
                "workflow.waiting",
                {"run_id": run_id, "workflow": "gated", "step": "Approve",
                 "question": "Go?"},
            )
        )

        prompt = pstore.newest_open("tg", "777")
        assert prompt is not None and prompt.ref_id == run_id
        assert "Workflow 'gated' is waiting: Go?" in tstore.history_body(thread.id)[-1]["content"]
        assert any("Workflow 'gated' is waiting" in s for s in ch.sent)


# --------------------------------------------------------------------------- #
# notifier: phone-friendly line, and NO default double-send
# --------------------------------------------------------------------------- #
def test_format_event_workflow_waiting_line():
    line = format_event(
        {"type": "workflow.waiting",
         "payload": {"run_id": "wfr1", "workflow": "gated", "question": "Go?"}}
    )
    assert line == (
        "Workflow 'gated' needs you: Go? — reply with a number or "
        "/answer <text> from a chat-enabled destination."
    )


def test_workflow_waiting_not_in_default_alerts():
    # The engine already delivers the park question itself (v1.121 _deliver)
    # and the prompt handler sends the answerable copy — a default-alert
    # subscription would triple-send the same question. Pinned.
    assert "workflow.waiting" not in DEFAULT_ALERT_EVENTS


# --------------------------------------------------------------------------- #
# the command grammar fallback (no prompt machinery on the surface)
# --------------------------------------------------------------------------- #
async def test_command_grammar_answer_fallback(platform):
    from iron_jarvis.reflex import CommandInterpreter

    interp = CommandInterpreter(platform, Orchestrator(platform), None)
    assert await interp.interpret("/answer whatever") == NOTHING_WAITING_REPLY
    assert "/answer <text>" in await interp.interpret("/help")
