"""10-b (build-out plan R2.4): an approval on the phone has a preview and two
buttons, and a press answers it through the SAME registry the dashboard
bell writes to (``platform.approvals``): one write path, first answer wins.

A press is read only after the allowlist and the private-chat guard, so a
stranger's press (or one in a group) resolves nothing; a second press hears
"That was already answered"; a safety card (a commit, a push, a risky page
click) gets no message at all. Telegram's ``callback_query`` and Slack's
``block_actions`` both reach ``InboundPoller._handle``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iron_jarvis.comm import Notifier
from iron_jarvis.comm.models import PendingPromptRecord
from iron_jarvis.comm.prompts import (
    APPROVAL_KIND,
    FILE_SEND_KIND,
    FILE_SEND_OPTIONS,
    approval_button_data,
    button_data,
    handle_workflow_waiting,
    parse_button,
)
from iron_jarvis.comm.slack_socket import SlackSocketMode
from iron_jarvis.core.approvals import ChatApprovals
from iron_jarvis.core.db import session_scope
from tests._fakes.phone_back import (
    SLACK_DM,
    SLACK_USER,
    FakeSlackApi,
    FakeTelegramBack,
    press,
    slack,
    telegram_back,
)
from tests._fakes.phone_files import ME, poller, run

pytestmark = pytest.mark.usefixtures("no_network")

STRANGER = 999
ARGS = {"path": "Documents/Intake", "pattern": "*.pdf"}


def _event(approval_id: str, **extra) -> dict:
    return {"type": "approval.requested",
            "payload": {"approval_id": approval_id, "tool": "shell", "args": dict(ARGS),
                        "timeout_s": 300, **extra}}


def _status(platform, prompt_id: str) -> str:
    with session_scope(platform.engine) as db:
        return db.get(PendingPromptRecord, prompt_id).status


async def _ask(platform, poll):
    """A real approval pause: the registry's future, the prompt for every
    allowed identity (the daemon's bus handler), and the alert the notifier
    sends (with its buttons)."""
    approvals = ChatApprovals()
    platform.approvals = approvals
    approval_id, fut = approvals.request("shell", dict(ARGS), session_id="s1")
    event = _event(approval_id)
    regs = handle_workflow_waiting(event, store=poll.prompt_store, notifier=poll.notifier,
                                   thread_store=poll.thread_store)
    poll.notifier.engine = platform.engine
    poll.notifier.on_event(event)
    return approval_id, fut, regs


def _rig(platform, fake):
    # The alert goes to the destination's own chat (its chat_id).
    poll = poller(platform, telegram_back(fake, chat_id=ME))
    # The identity exists once the sender has talked to Jarvis here.
    poll.thread_store.resolve("tg", str(ME), "Val")
    return poll


def test_the_alert_has_a_preview_and_approve_and_deny_buttons(platform):
    fake = FakeTelegramBack([])
    poll = _rig(platform, fake)

    async def go():
        return await _ask(platform, poll)

    approval_id, _fut, regs = asyncio.run(go())

    assert len(regs) == 1  # one answerable prompt, for the allowed sender
    alert = fake.sent[-1]
    assert alert["text"] == ("⏸ An agent wants to run a command: path Documents/Intake, "
                             "pattern *.pdf — approve from the dashboard bell, or reply here: "
                             "approve / deny.")
    buttons = alert["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Approve", "Deny"]
    assert [b["callback_data"] for b in buttons] == [
        approval_button_data(approval_id, 1), approval_button_data(approval_id, 2)]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)  # Telegram's limit


def test_an_allowed_press_resolves_the_approval_through_the_registry(platform):
    fake = FakeTelegramBack([])
    poll = _rig(platform, fake)

    async def go():
        approval_id, fut, regs = await _ask(platform, poll)
        fake.updates = [press(5, approval_button_data(approval_id, 1), sender=ME)]
        res = await poll.poll_once()
        await poll.drain()
        # The run waiting on the registry carries on with the answer.
        decision = await asyncio.wait_for(fut, 2)
        return res, decision, regs

    res, decision, regs = asyncio.run(go())

    assert decision == "once"  # a phone answer only ever grants once
    assert res[0]["status"] == "approval_answered" and res[0]["pressed"] is True
    assert _status(platform, regs[0]["prompt_id"]) == "answered"
    assert fake.answered() == ["Approved by you"]
    assert fake.edits() == ["Approved by you"]
    # The changed button IS the reply: no second message about it.
    assert not any("Approved — the agent" in t for t in fake.texts)
    # The phone chat on the PC shows the press and the answer.
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    lines = [m["content"] for m in poll.thread_store.history_body(tid)]
    assert "Approve" in lines and "→ Approved — the agent is going ahead." in lines


def test_deny_by_button(platform):
    fake = FakeTelegramBack([])
    poll = _rig(platform, fake)

    async def go():
        approval_id, fut, _regs = await _ask(platform, poll)
        fake.updates = [press(5, approval_button_data(approval_id, 2), sender=ME)]
        await poll.poll_once()
        return await asyncio.wait_for(fut, 2)

    assert asyncio.run(go()) == "deny"
    assert fake.edits() == ["Denied by you"]


def test_a_strangers_press_resolves_nothing(platform):
    fake = FakeTelegramBack([])
    poll = _rig(platform, fake)

    async def go():
        approval_id, fut, regs = await _ask(platform, poll)
        fake.updates = [press(5, approval_button_data(approval_id, 1), sender=STRANGER)]
        res = await poll.poll_once()
        return res, fut, regs, approval_id

    res, fut, regs, approval_id = asyncio.run(go())

    assert res[0]["status"] == "unauthorized"
    assert not fut.done()
    assert approval_id in platform.approvals.pending_ids()
    assert _status(platform, regs[0]["prompt_id"]) == "open"
    assert fake.answered() == [] and fake.edits() == []  # strangers get silence


def test_a_press_in_a_group_resolves_nothing(platform):
    fake = FakeTelegramBack([])
    poll = _rig(platform, fake)

    async def go():
        approval_id, fut, _regs = await _ask(platform, poll)
        fake.updates = [press(5, approval_button_data(approval_id, 1), sender=ME, chat=-100123)]
        res = await poll.poll_once()
        return res, fut

    res, fut = asyncio.run(go())
    assert res[0]["status"] == "non_private" and not fut.done()


def test_a_second_press_answers_already_answered(platform):
    fake = FakeTelegramBack([])
    poll = _rig(platform, fake)

    async def go():
        approval_id, fut, _regs = await _ask(platform, poll)
        data = approval_button_data(approval_id, 1)
        fake.updates = [press(5, data, sender=ME)]
        await poll.poll_once()
        # The waiting run takes its answer and the pause ends (the runtime's
        # own clean-up).
        platform.approvals.pop(approval_id)
        fake.updates = [press(6, data, sender=ME)]
        second = await poll.poll_once()
        return second, await asyncio.wait_for(fut, 2)

    second, decision = asyncio.run(go())
    assert decision == "once"
    assert second[0]["status"] == "press_stale"
    assert fake.answered() == ["Approved by you", "That was already answered"]


def test_a_press_on_someone_elses_question_resolves_nothing(platform):
    fake = FakeTelegramBack([])
    poll = _rig(platform, fake)
    other = "888"
    poll.notifier.get("tg").config["allowed_senders"].append(other)
    rec = poll.prompt_store.register(FILE_SEND_KIND, '{"paths": [], "local_only": true}',
                                     "Send 1 file?", FILE_SEND_OPTIONS, "tg", other, "")
    fake.updates = [press(7, button_data(rec.id, 1), sender=ME)]

    res = run(poll)

    assert res[0]["status"] == "press_stale"
    assert _status(platform, rec.id) == "open"
    assert fake.answered() == ["That was already answered"]


def test_a_safety_card_gets_no_message_at_all(platform):
    fake = FakeTelegramBack([])
    poll = _rig(platform, fake)

    async def go():
        approvals = ChatApprovals()
        platform.approvals = approvals
        approval_id, _fut = approvals.request("git_push", {}, session_id="s1", once_only=True,
                                              once_reason="git")
        event = _event(approval_id, once_only=True, once_reason="git")
        regs = handle_workflow_waiting(event, store=poll.prompt_store, notifier=poll.notifier,
                                       thread_store=poll.thread_store)
        out = poll.notifier.on_event(event)
        return regs, out

    regs, out = asyncio.run(go())
    assert regs == [] and out == {}
    assert fake.sent == []


def test_a_destination_without_two_way_chat_gets_the_words_alone(platform):
    fake = FakeTelegramBack([])
    ch = telegram_back(fake, chat_enabled=False, chat_id=ME)
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    notifier.on_event(_event("apr_x"))
    assert "reply_markup" not in fake.sent[-1]


def test_slack_block_actions_press_resolves_and_updates_the_message(platform):
    api = FakeSlackApi()
    ch = slack(api)
    poll = poller(platform, ch, name="slack")
    poll.thread_store.resolve("slack", SLACK_USER, "")
    pump = SlackSocketMode(poll, poll.notifier, lambda n: None, lambda: {})

    async def go():
        approvals = ChatApprovals()
        platform.approvals = approvals
        approval_id, fut = approvals.request("shell", dict(ARGS), session_id="s1")
        event = _event(approval_id)
        handle_workflow_waiting(event, store=poll.prompt_store, notifier=poll.notifier,
                                thread_store=poll.thread_store)
        poll.notifier.engine = platform.engine
        poll.notifier.on_event(event)
        sent = api.posts[-1][1]
        envelope = {"type": "interactive", "envelope_id": "e1", "payload": {
            "type": "block_actions", "user": {"id": SLACK_USER}, "channel": {"id": SLACK_DM},
            "container": {"type": "message", "message_ts": "111.222", "channel_id": SLACK_DM},
            "message": {"ts": "111.222", "text": sent["text"]},
            "actions": [{"action_id": "ij_1", "value": approval_button_data(approval_id, 1)}],
        }}
        res = await pump.process_envelope("slack", envelope)
        return sent, res, await asyncio.wait_for(fut, 2)

    sent, res, decision = asyncio.run(go())

    assert [e["text"]["text"] for e in sent["blocks"][1]["elements"]] == ["Approve", "Deny"]
    assert sent["blocks"][0]["text"]["type"] == "plain_text"
    assert decision == "once" and res["status"] == "approval_answered"
    update = [p for m, p in api.posts if m == "chat.update"]
    assert update and update[0]["channel"] == SLACK_DM and update[0]["ts"] == "111.222"
    assert update[0]["blocks"][-1]["elements"][0]["text"] == "Approved by you"


def test_no_buttons_where_nobody_could_answer_them_yet(platform):
    # Two-way chat is on, but no allowed sender has talked to Jarvis here, so
    # no answerable prompt exists for a press: the words alone.
    fake = FakeTelegramBack([])
    notifier = Notifier()
    notifier.engine = platform.engine
    notifier.add_channel("tg", telegram_back(fake, chat_id=ME))
    notifier.on_event(_event("apr_x"))
    assert fake.sent and "reply_markup" not in fake.sent[-1]


def test_slack_without_socket_mode_gets_the_words_alone():
    # Through the Events API webhook a press would go nowhere: no buttons.
    api = FakeSlackApi()
    assert slack(api).supports_buttons is True
    plain = slack(api, app_token_secret="")
    assert plain.supports_buttons is False
    notifier = Notifier()
    notifier.add_channel("slack", plain)
    notifier.on_event(_event("apr_x"))
    assert "blocks" not in api.posts[-1][1]


def test_slack_press_from_a_stranger_resolves_nothing(platform):
    api = FakeSlackApi()
    ch = slack(api)
    poll = poller(platform, ch, name="slack")
    pump = SlackSocketMode(poll, poll.notifier, lambda n: None, lambda: {})

    async def go():
        approvals = ChatApprovals()
        platform.approvals = approvals
        approval_id, fut = approvals.request("shell", {}, session_id="s1")
        envelope = {"type": "interactive", "payload": {
            "type": "block_actions", "user": {"id": "U0STRANGER"}, "channel": {"id": "D0OTHER01"},
            "container": {"message_ts": "1.2"}, "message": {"ts": "1.2", "text": "x"},
            "actions": [{"value": approval_button_data(approval_id, 1)}]}}
        res = await pump.process_envelope("slack", envelope)
        return res, fut

    res, fut = asyncio.run(go())
    assert res["status"] == "unauthorized" and not fut.done()
    assert not [m for m, _p in api.posts if m == "chat.update"]


def test_a_press_with_no_message_under_it_is_never_taken(platform):
    fake = FakeTelegramBack([])
    ch = telegram_back(fake)
    upd = {"update_id": 9, "callback_query": {"id": "cq9", "from": {"id": ME, "is_bot": False},
                                              "inline_message_id": "x", "data": "ij:a:apr_1:1"}}
    fake.updates = [upd]
    messages, offset = ch.poll(0)
    assert messages == [] and offset == 10


def test_button_data_round_trips_and_refuses_anything_else():
    assert parse_button(button_data("pp_abc", 2)) == ("p", "pp_abc", 2)
    assert parse_button(approval_button_data("apr_1", 1)) == ("a", "apr_1", 1)
    for bad in ("ij:x", "", "hello", "ij:p::1", "ij:q:id:1", "ij:p:id:one", None, "ij:p:id:1:2"):
        assert parse_button(bad) is None
    assert APPROVAL_KIND == "approval"
    assert SimpleNamespace  # used by the fakes
