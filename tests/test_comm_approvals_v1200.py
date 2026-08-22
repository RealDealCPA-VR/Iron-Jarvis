"""Offline tests for MID-RUN APPROVALS reaching the phone (v1.200.0).

CONNECT-AUDIT-2026-08-22 items 3 + 7. Before this, ``approval.requested``
never reached Telegram/notifications while its sibling ``workflow.waiting``
got multi-channel delivery and a phone-answerable flow — the phone stayed
silent for the whole 300s answer window. Now:

* the notifier alerts on ``approval.requested`` by default (and deliberately
  NOT on ``approval.resolved`` — answering is not news);
* the pending-prompt handler registers an approve/deny prompt per chat
  identity (thread line, NO second phone send — the notifier alert is the one
  phone copy), riding the SAME bus registration as ``workflow.waiting``;
* a reply of "approve"/"deny" (or 1/2, or /answer) resolves through
  ``platform.approvals`` — the SAME registry the dashboard bell writes to —
  and an expired request gets an honest "already timed out";
* Telegram media from an ALLOWED sender gets ONE honest transport notice
  before the offset advances past it (strangers/groups/bots still silence,
  no chat-thread entry, never a poll crash).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import InboundMessage, MockChannel, Notifier, TelegramChannel
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.models import CommIdentityRecord, InboundOffsetRecord
from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS, format_event
from iron_jarvis.comm.prompts import (
    APPROVAL_GONE_REPLY,
    APPROVAL_KIND,
    APPROVAL_USAGE_REPLY,
    PendingPromptStore,
    approval_question,
    handle_approval_requested,
    handle_workflow_waiting,
)
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import utcnow


# --------------------------------------------------------------------------- #
# Fakes + helpers (the test_comm_pending_prompts / test_comm_inbound idioms)
# --------------------------------------------------------------------------- #
class ChatMockChannel(MockChannel):
    """MockChannel with a receive leg + credentials, for full-chat tests."""

    supports_inbound = True

    def has_credentials(self) -> bool:  # no token needed offline
        return True


CHAT_CFG = {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]}


class FakeApprovals:
    """The registry seam — records resolves, wins or loses like the real one."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    def resolve(self, approval_id: str, decision: str) -> bool:
        self.calls.append((approval_id, decision))
        return self.ok


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


def _fake_answer():
    async def answer(run_id: str, text: str) -> dict[str, Any]:
        answer.calls.append((run_id, text))
        return {"ok": True, "run_name": "gated"}

    answer.calls = []
    return answer


def _poller(platform, ch, *, turn=None, answer=None):
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
        thread_store=tstore,
        chat_turn=turn if turn is not None else _fake_turn(),
        personas={},
        platform=platform,
        prompt_store=pstore,
        answer_run=answer,
    )
    return poller, orch, tstore, pstore, notifier


def _approval_event(approval_id: str = "apr_1", tool: str = "shell") -> dict:
    return {
        "type": "approval.requested",
        "payload": {
            "approval_id": approval_id,
            "tool": tool,
            "args": {},
            "timeout_s": 300,
        },
    }


def _register_approval(pstore, approval_id: str = "apr_1", sender: str = "777"):
    return pstore.register(
        APPROVAL_KIND,
        approval_id,
        approval_question("shell"),
        ["approve", "deny"],
        "tg",
        sender,
        "",
    )


def _prompt_status(pstore: PendingPromptStore, prompt_id: str) -> str:
    from iron_jarvis.comm.models import PendingPromptRecord

    with session_scope(pstore.engine) as db:
        return db.get(PendingPromptRecord, prompt_id).status


# --------------------------------------------------------------------------- #
# notifier: the alert exists by default, is phone-friendly, and routes
# --------------------------------------------------------------------------- #
def test_approval_requested_is_default_alert_and_resolved_is_not():
    # The asymmetry with workflow.waiting is deliberate: nothing else delivers
    # an approval pause (the runtime only publishes), so the notifier IS the
    # delivery. approval.resolved is not news — the user just acted.
    assert EventType.APPROVAL_REQUESTED in DEFAULT_ALERT_EVENTS
    assert EventType.APPROVAL_RESOLVED not in DEFAULT_ALERT_EVENTS


def test_format_event_approval_line_names_tool_and_both_answer_paths():
    line = format_event(_approval_event(tool="shell"))
    assert line == (
        "⏸ An agent is asking to use shell — approve from the "
        "dashboard bell, or reply here: approve / deny."
    )
    # A payload without a tool still reads like a sentence, not a KeyError.
    bare = format_event({"type": "approval.requested", "payload": {}})
    assert "a tool" in bare and "dashboard bell" in bare


def test_on_event_routes_approval_alert_to_channels():
    mock = MockChannel()
    notifier = Notifier()  # DEFAULT event set — nothing explicit
    notifier.add_channel("mock", mock)

    results = notifier.on_event(_approval_event(tool="run_workflow"))

    assert results is not None and results["mock"]["ok"]
    assert len(mock.sent) == 1 and "run_workflow" in mock.sent[0]
    # Resolving is NOT an alert (answering is not news).
    assert notifier.on_event(
        {"type": "approval.resolved",
         "payload": {"approval_id": "apr_1", "decision": "once"}}
    ) is None
    assert len(mock.sent) == 1


# --------------------------------------------------------------------------- #
# registration: prompt row + thread line, NO second phone send
# --------------------------------------------------------------------------- #
def test_approval_event_registers_prompt_and_thread_line_without_send(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    _p, _o, tstore, pstore, notifier = _poller(platform, ch)
    thread = tstore.resolve("tg", "777", "Val")

    # Through handle_workflow_waiting — the EXACT function daemon/app.py
    # registers on the bus — so the dispatch (no new daemon wiring) is pinned.
    out = handle_workflow_waiting(
        _approval_event(), store=pstore, notifier=notifier, thread_store=tstore
    )

    assert len(out) == 1 and out[0]["prompt_id"]
    prompt = pstore.newest_open("tg", "777")
    assert prompt is not None
    assert prompt.kind == APPROVAL_KIND and prompt.ref_id == "apr_1"
    assert prompt.options_json == '["approve", "deny"]'
    line = tstore.history_body(thread.id)[-1]
    assert line["role"] == "assistant"
    assert "asking to use 'shell'" in line["content"]
    assert "approve or deny" in line["content"]
    # NO phone send from the handler: the notifier's default alert is the one
    # phone copy (the inverse of the workflow arrangement — double-send is the
    # exact trap notifier.py's exclusion note documents).
    assert ch.sent == []


def test_approval_event_skips_non_chat_and_unauthorized(platform):
    a = ChatMockChannel({**CHAT_CFG, "allowed_senders": ["someone-else"]})
    b = ChatMockChannel({"inbound_enabled": True, "allowed_senders": ["777"]})
    tstore = CommThreadStore(platform.engine)
    pstore = PendingPromptStore(platform.engine)
    notifier = Notifier()
    notifier.add_channel("a", a)
    notifier.add_channel("b", b)
    tstore.resolve("a", "777", "Val")
    tstore.resolve("b", "777", "Val")

    out = handle_approval_requested(
        _approval_event(), store=pstore, notifier=notifier, thread_store=tstore
    )

    assert out == []
    assert pstore.newest_open("a", "777") is None
    assert pstore.newest_open("b", "777") is None


def test_approval_handler_ignores_bad_payloads_and_never_raises(platform):
    pstore = PendingPromptStore(platform.engine)
    notifier = Notifier()
    assert handle_approval_requested(
        {"type": "approval.requested", "payload": {}},
        store=pstore, notifier=notifier, thread_store=None,
    ) == []
    assert handle_approval_requested(
        None, store=pstore, notifier=notifier, thread_store=None
    ) == []


def test_approval_resolved_event_expires_every_open_prompt(platform):
    ch = ChatMockChannel({**CHAT_CFG, "allowed_senders": ["777", "888"]})
    _p, _o, tstore, pstore, notifier = _poller(platform, ch)
    a = _register_approval(pstore, sender="777")
    b = _register_approval(pstore, sender="888")

    # Published on EVERY pause ending — dashboard answer and runtime timeout
    # alike — so this IS the timeout hygiene for approval prompts.
    handle_workflow_waiting(
        {"type": "approval.resolved",
         "payload": {"approval_id": "apr_1", "decision": "timeout"}},
        store=pstore, notifier=notifier, thread_store=tstore,
    )

    assert _prompt_status(pstore, a.id) == "expired"
    assert _prompt_status(pstore, b.id) == "expired"
    assert pstore.newest_open("tg", "777") is None


# --------------------------------------------------------------------------- #
# resolution: approve / deny / numbered picks, one write path
# --------------------------------------------------------------------------- #
async def test_reply_approve_resolves_once_via_registry(platform):
    fake = FakeApprovals()
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, orch, tstore, pstore, _n = _poller(platform, ch)
    rec = _register_approval(pstore)

    res = await poller._handle("tg", ch, _msg("approve"))

    assert res["status"] == "approval_answered" and res["sent"] is True
    assert fake.calls == [("apr_1", "once")]  # "once", never "conversation"
    assert _prompt_status(pstore, rec.id) == "answered"
    assert "→ Approved" in ch.sent[-1]
    # The exchange landed on the thread: user word + assistant echo.
    msgs = tstore.history_body(res["thread_id"])
    assert msgs[-2] == {"role": "user", "content": "approve"}
    assert "→ Approved" in msgs[-1]["content"]
    assert orch.list_sessions() == []  # no session, no chat turn


async def test_reply_deny_resolves_deny(platform):
    fake = FakeApprovals()
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _o, _t, pstore, _n = _poller(platform, ch)
    rec = _register_approval(pstore)

    res = await poller._handle("tg", ch, _msg("Deny"))  # case-insensitive

    assert res["status"] == "approval_answered"
    assert fake.calls == [("apr_1", "deny")]
    assert _prompt_status(pstore, rec.id) == "answered"
    assert "→ Denied" in ch.sent[-1]


async def test_numbered_pick_maps_to_decision(platform):
    fake = FakeApprovals()
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _o, _t, pstore, _n = _poller(platform, ch)
    _register_approval(pstore)

    res = await poller._handle("tg", ch, _msg("2"))  # options: approve, deny

    assert res["status"] == "approval_answered"
    assert fake.calls == [("apr_1", "deny")]


async def test_answer_command_resolves_and_free_text_gets_usage(platform):
    fake = FakeApprovals()
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _o, _t, pstore, _n = _poller(platform, ch)
    rec = _register_approval(pstore)

    # Only the yes/no vocabulary decides a permission — free text is refused.
    res = await poller._handle("tg", ch, _msg("/answer ship it anyway"))
    assert res["status"] == "answer_usage"
    assert APPROVAL_USAGE_REPLY in ch.sent[-1]
    assert fake.calls == [] and _prompt_status(pstore, rec.id) == "open"

    res2 = await poller._handle("tg", ch, _msg("/answer yes", update_id=2))
    assert res2["status"] == "approval_answered"
    assert fake.calls == [("apr_1", "once")]


async def test_expired_approval_gets_honest_timeout_reply(platform):
    fake = FakeApprovals(ok=False)  # registry: unknown/expired/already answered
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _o, _t, pstore, _n = _poller(platform, ch)
    rec = _register_approval(pstore)

    res = await poller._handle("tg", ch, _msg("approve"))

    assert res["status"] == "approval_expired"
    assert fake.calls == [("apr_1", "once")]
    # Timeout hygiene: a failed resolve EXPIRES the prompt — it never lingers.
    assert _prompt_status(pstore, rec.id) == "expired"
    assert APPROVAL_GONE_REPLY in ch.sent[-1]


async def test_stale_approval_prompt_expires_on_look(platform):
    fake = FakeApprovals()
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _o, _t, pstore, _n = _poller(platform, ch)
    rec = _register_approval(pstore)
    # Outlive the runtime's 300s answer window (the belt for a missed
    # approval.resolved event).
    from iron_jarvis.comm.models import PendingPromptRecord

    with session_scope(platform.engine) as db:
        row = db.get(PendingPromptRecord, rec.id)
        row.created_at = utcnow() - timedelta(seconds=320)
        db.add(row)
        db.commit()

    res = await poller._handle("tg", ch, _msg("approve"))

    assert res["status"] == "approval_expired"
    assert fake.calls == []  # never even offered to the registry
    assert _prompt_status(pstore, rec.id) == "expired"
    assert APPROVAL_GONE_REPLY in ch.sent[-1]


async def test_bare_word_without_approval_prompt_stays_chat(platform):
    fake = FakeApprovals()
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn("chatting")
    answer = _fake_answer()
    poller, _o, _t, pstore, _n = _poller(platform, ch, turn=turn, answer=answer)

    # No prompt at all: "approve" is a normal chat turn.
    res = await poller._handle("tg", ch, _msg("approve"))
    assert res["status"] == "chat" and fake.calls == []
    assert len(turn.calls) == 1

    # A WORKFLOW gate open: free text still never resolves it (pinned rule) —
    # "approve" stays chat and the workflow prompt stays open.
    from iron_jarvis.core.db import dumps
    from iron_jarvis.workflows.engine import Step, step_to_dict
    from iron_jarvis.workflows.models import WorkflowRunRecord

    run = WorkflowRunRecord(
        workflow_name="gated", status="waiting",
        steps_json=dumps([step_to_dict(Step(name="A", kind="ask", message="Go?"))]),
        outputs_json="{}", session_ids_json="[]",
        waiting_json=dumps({"index": 0, "step": "A", "question": "Go?"}),
    )
    with session_scope(platform.engine) as db:
        db.add(run)
        db.commit()
        db.refresh(run)
    wf = pstore.register("workflow_ask", run.id, "Go?", [], "tg", "777", "")

    res2 = await poller._handle("tg", ch, _msg("approve", update_id=2))
    assert res2["status"] == "chat"
    assert fake.calls == [] and answer.calls == []
    assert _prompt_status(pstore, wf.id) == "open"


async def test_unauthorized_sender_cannot_approve(platform):
    fake = FakeApprovals()
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))  # allowlist = ["777"] only
    poller, _o, _t, pstore, _n = _poller(platform, ch)
    rec = _register_approval(pstore)

    for text in ("approve", "1", "/answer approve"):
        res = await poller._handle("tg", ch, _msg(text, sender="999"))
        assert res["status"] == "unauthorized"  # the allowlist ran FIRST

    assert fake.calls == []
    assert _prompt_status(pstore, rec.id) == "open"
    assert ch.sent == []  # strangers hear nothing


async def test_real_registry_end_to_end_same_write_path_as_dashboard(platform):
    # build_platform attached the REAL ChatApprovals — the same instance
    # POST /chat/approvals/{id} resolves. The phone reply must land on the
    # same pending future the paused run is awaiting.
    from iron_jarvis.core.approvals import ChatApprovals

    assert isinstance(platform.approvals, ChatApprovals)
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _o, _t, pstore, _n = _poller(platform, ch)

    approval_id, fut = platform.approvals.request("shell", {})
    _register_approval(pstore, approval_id=approval_id)
    res = await poller._handle("tg", ch, _msg("approve"))
    assert res["status"] == "approval_answered"
    assert fut.done() and fut.result() == "once"

    # Second pause, denied from the phone this time.
    apr2, fut2 = platform.approvals.request("shell", {})
    _register_approval(pstore, approval_id=apr2)
    res2 = await poller._handle("tg", ch, _msg("deny", update_id=2))
    assert res2["status"] == "approval_answered"
    assert fut2.done() and fut2.result() == "deny"

    # The awaiter popped its id (the real lifecycle) → a late reply is honest.
    platform.approvals.pop(apr2)
    rec3 = _register_approval(pstore, approval_id=apr2)
    res3 = await poller._handle("tg", ch, _msg("approve", update_id=3))
    assert res3["status"] == "approval_expired"
    assert _prompt_status(pstore, rec3.id) == "expired"
    assert APPROVAL_GONE_REPLY in ch.sent[-1]


# --------------------------------------------------------------------------- #
# F7: Telegram media honesty — one notice, allowed senders only, offset moves
# --------------------------------------------------------------------------- #
class FakeTelegram:
    """(url, params) transports honouring getUpdates offset semantics."""

    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = list(updates)
        self.sent: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        assert "getUpdates" in url
        offset = int(params.get("offset", 0) or 0)
        if offset:
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
        return {"ok": True, "result": list(self.updates)}

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert "sendMessage" in url
        self.sent.append(payload)
        return {"status_code": 200}


TG_CFG = {
    "token_secret": "tg_token",
    "chat_id": 777,
    "inbound_enabled": True,
    "allowed_senders": [777],
}


def _telegram(fake: FakeTelegram, config: dict[str, Any]) -> TelegramChannel:
    return TelegramChannel(
        config,
        http_post=fake.post,
        http_get=fake.get,
        secret_resolver=lambda n: "BOTTOKEN" if n == "tg_token" else None,
    )


def _text_update(update_id: int, sender: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "from": {"id": sender, "is_bot": False},
            "chat": {"id": sender},
        },
    }


def _media_update(
    update_id: int,
    sender: int,
    *,
    key: str = "photo",
    chat: int | None = None,
    is_bot: bool = False,
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            key: [{"file_id": "abc"}],
            "from": {"id": sender, "is_bot": is_bot},
            "chat": {"id": chat if chat is not None else sender},
        },
    }


def test_media_from_allowed_sender_gets_one_notice_offset_advances():
    fake = FakeTelegram(
        [
            _media_update(10, 777),                    # photo → ONE notice
            _media_update(11, 777, key="document"),    # same sender, same batch
            _media_update(12, 999),                    # stranger → silence
            _text_update(13, 777, "hello"),            # text still flows
        ]
    )
    ch = _telegram(fake, dict(TG_CFG))

    messages, next_offset = ch.poll(0)

    assert [m.text for m in messages] == ["hello"]
    assert next_offset == 14  # media confirmed too — never refetched
    assert len(fake.sent) == 1  # one notice per sender per batch, not per file
    assert fake.sent[0]["chat_id"] == 777
    assert "can't read photos" in fake.sent[0]["text"]
    assert "desktop app" in fake.sent[0]["text"]


def test_media_from_stranger_group_or_bot_stays_silent():
    fake = FakeTelegram(
        [
            _media_update(20, 999),                 # not on the allowlist
            _media_update(21, 777, chat=-4242),     # group chat → broadcast risk
            _media_update(22, 777, is_bot=True),    # loop protection
            {"update_id": 23, "message": {         # service update: nothing said
                "new_chat_members": [{"id": 5}],
                "from": {"id": 777, "is_bot": False},
                "chat": {"id": 777},
            }},
        ]
    )
    ch = _telegram(fake, dict(TG_CFG))

    messages, next_offset = ch.poll(0)

    assert messages == [] and next_offset == 24
    assert fake.sent == []  # fail-closed: silence for all of them


def test_empty_allowlist_notices_nobody():
    cfg = {**TG_CFG, "allowed_senders": []}
    fake = FakeTelegram([_media_update(30, 777)])
    ch = _telegram(fake, cfg)

    messages, next_offset = ch.poll(0)

    assert messages == [] and next_offset == 31
    assert fake.sent == []  # empty allowlist authorizes nobody, notice included


def test_malformed_updates_never_crash_the_poll():
    fake = FakeTelegram(
        [
            {"update_id": 40},                                   # no message at all
            {"update_id": 41, "message": {"photo": [], "from": "garbage"}},
            {"update_id": 42, "message": {"voice": {}, "from": {"id": 777},
                                          "chat": "not-a-dict"}},
            _text_update(43, 777, "still alive"),
        ]
    )
    ch = _telegram(fake, dict(TG_CFG))

    messages, next_offset = ch.poll(0)

    assert [m.text for m in messages] == ["still alive"]
    assert next_offset == 44  # the batch survived every malformed shape


def test_failed_notice_send_never_breaks_the_poll():
    class ExplodingPost:
        def __call__(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("network down")

    fake = FakeTelegram([_media_update(50, 777), _text_update(51, 777, "hi")])
    ch = TelegramChannel(
        dict(TG_CFG),
        http_post=ExplodingPost(),
        http_get=fake.get,
        secret_resolver=lambda n: "BOTTOKEN" if n == "tg_token" else None,
    )

    messages, next_offset = ch.poll(0)

    assert [m.text for m in messages] == ["hi"]  # best-effort: poll unharmed
    assert next_offset == 52


async def test_poller_persists_offset_past_media_and_mints_no_thread(platform):
    # The durable pipeline end-to-end: a media-only batch advances the stored
    # offset (never refetched after restart), sends the one notice, and leaves
    # ZERO conversation state — a transport notice is not a chat turn.
    fake = FakeTelegram([_media_update(60, 777)])
    ch = _telegram(fake, {**TG_CFG, "chat_enabled": True, "allowed_senders": ["777"]})
    poller, orch, _t, _p, _n = _poller(platform, ch)

    results = await poller.poll_once()

    assert results == []  # nothing surfaced to the handler
    with session_scope(platform.engine) as db:
        rec = db.get(InboundOffsetRecord, "tg")
        assert rec is not None and rec.offset == 61
        assert db.exec(select(CommIdentityRecord)).all() == []  # no thread entry
    assert len(fake.sent) == 1 and "can't read photos" in fake.sent[0]["text"]
    assert orch.list_sessions() == []
