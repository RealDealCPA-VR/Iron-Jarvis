"""v1.200.0 — pending mid-turn approvals are LISTABLE, so any surface can see
the ask.

The gap (docs/CONNECT-AUDIT-2026-08-22.md item 2): a job-origin agent run
(the Agents page posts origin ``job:agents``) genuinely pauses on an ask-tier
tool and publishes ``approval.requested`` — but only the chat stream renders
an approval card, so the job-poster never saw the question and the pause
timed out into a silent degrade.

The mechanism: ``GET /chat/approvals/pending`` walks the shared registry's
pending ids and reconstructs display metadata from the persisted
``approval.requested`` EventRecord rows, queried off the event loop — and
lists ONLY announced asks: a registry id with no event row is a chat-lane
mid-turn ask (SSE-only by design) whose card is already in front of the
user, and a bell row would mislabel it as an agent run. The
registry deliberately stores only ``{id: future}`` ("a second place secrets
could linger"), and this listing keeps the same posture: id, tool,
session_id, requested_at — NEVER args, even though the event payload carries
(redacted) args for the one chat card watching that stream.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from iron_jarvis.core.approvals import ChatApprovals
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


#: Every key the listing may carry, exactly. Pinned as a SET EQUALITY in the
#: tests below so a future edit adding args (or anything else riding the
#: event payload) goes red instead of silently widening the broadcast.
ALLOWED_KEYS = {"id", "tool", "session_id", "requested_at"}


def test_no_pending_approvals_is_an_honest_empty_list(tmp_path):
    client = _client(tmp_path)
    r = client.get("/chat/approvals/pending")
    assert r.status_code == 200
    assert r.json() == {"approvals": []}


def test_a_filed_request_is_listed_with_its_event_metadata(tmp_path):
    """File through the registry + publish the matching event (exactly what
    agents/runtime._pause_for_approval does) — the listing carries the tool
    name and the session id so the bell can render a real card."""
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def scenario():
        ap_id, fut = platform.approvals.request("shell", {"command": "x"})
        await platform.event_bus.publish(
            EventType.APPROVAL_REQUESTED,
            {"approval_id": ap_id, "tool": "shell",
             "args": {"command": "x"}, "timeout_s": 300},
            session_id="session_job",
        )
        r = client.get("/chat/approvals/pending")
        platform.approvals.pop(ap_id)  # tidy: don't leave an unresolved future
        return ap_id, r

    ap_id, r = asyncio.run(scenario())
    assert r.status_code == 200
    rows = r.json()["approvals"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == ap_id
    assert row["tool"] == "shell"
    assert row["session_id"] == "session_job"
    assert row["requested_at"], "the event row's timestamp must ride along"


def test_answered_and_popped_ids_vanish_from_the_list(tmp_path):
    """The registry is the source of PRESENCE: once the awaiter pops the id
    (answered, timed out, or the stream died), the listing must not resurrect
    it from the event log — the event row outlives the pause on purpose."""
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def scenario():
        ap_id, fut = platform.approvals.request("shell", {"command": "x"})
        await platform.event_bus.publish(
            EventType.APPROVAL_REQUESTED,
            {"approval_id": ap_id, "tool": "shell", "timeout_s": 300},
            session_id="session_job",
        )
        before = client.get("/chat/approvals/pending").json()["approvals"]
        # Answer through the SAME route the bell's buttons post, then pop the
        # way the awaiter's finally does.
        resp = client.post(f"/chat/approvals/{ap_id}", json={"decision": "once"})
        assert resp.status_code == 200
        platform.approvals.pop(ap_id)
        after = client.get("/chat/approvals/pending").json()["approvals"]
        return ap_id, before, after

    ap_id, before, after = asyncio.run(scenario())
    assert [row["id"] for row in before] == [ap_id]
    assert after == []


def test_args_never_appear_even_when_the_event_payload_carries_them(tmp_path):
    """THE defensive strip, mutation-pinned. The event payload legitimately
    carries (redacted) args for the chat card; this listing fans out to every
    dashboard page on a poll and must copy metadata key-by-key. Set equality
    on the keys means ANY widening — args included — goes red."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    marker = "SECRET-ARG-MARKER-2026"

    async def scenario():
        ap_id, fut = platform.approvals.request("write_file", None)
        await platform.event_bus.publish(
            EventType.APPROVAL_REQUESTED,
            {"approval_id": ap_id, "tool": "write_file",
             "args": {"path": marker, "content": marker},
             "timeout_s": 300},
            session_id="session_job",
        )
        r = client.get("/chat/approvals/pending")
        platform.approvals.pop(ap_id)
        return r

    r = asyncio.run(scenario())
    assert r.status_code == 200
    assert marker not in r.text, "argument payloads must never reach this list"
    for row in r.json()["approvals"]:
        assert set(row.keys()) == ALLOWED_KEYS


def test_a_pending_id_with_no_event_row_is_excluded_from_the_listing(tmp_path):
    """Only ANNOUNCED asks are listed. The chat STREAM lane files into the
    same registry but deliberately announces via its SSE frame only — no
    ``approval.requested`` event — because its one answering surface is the
    chat card already in front of the user. A metadata-less "unknown" row in
    the bell would mislabel the user's own question as an agent run and
    double-surface it on every page (reviewer-confirmed, v1.200.0). The
    runtime lane always publishes the event, so real agent asks are never
    dropped by this filter."""
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def scenario():
        ap_id, fut = platform.approvals.request("shell", {"command": "x"})
        # No event published — the chat-lane shape.
        r = client.get("/chat/approvals/pending")
        platform.approvals.pop(ap_id)
        return ap_id, r

    ap_id, r = asyncio.run(scenario())
    assert r.status_code == 200
    assert r.json() == {"approvals": []}


def test_a_chat_lane_ask_alongside_an_announced_one_lists_only_the_announced(tmp_path):
    """The discriminating case: one registry, two lanes. The runtime lane's
    ask (event published) is listed; the chat lane's ask (SSE-only, no event)
    is not — and never leaks the announced one's metadata either."""
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def scenario():
        chat_id, _f1 = platform.approvals.request("shell", {"command": "x"})
        agent_id, _f2 = platform.approvals.request("write_file", None)
        await platform.event_bus.publish(
            EventType.APPROVAL_REQUESTED,
            {"approval_id": agent_id, "tool": "write_file", "timeout_s": 300},
            session_id="session_job",
        )
        r = client.get("/chat/approvals/pending")
        platform.approvals.pop(chat_id)
        platform.approvals.pop(agent_id)
        return chat_id, agent_id, r

    chat_id, agent_id, r = asyncio.run(scenario())
    rows = r.json()["approvals"]
    assert [row["id"] for row in rows] == [agent_id]
    assert rows[0]["tool"] == "write_file"
    assert rows[0]["session_id"] == "session_job"
    assert chat_id not in r.text


def test_pending_ids_is_a_read_only_snapshot():
    """The new accessor (core/approvals.py): ids only, and mutating the
    returned list must not touch the registry."""

    async def scenario():
        ap = ChatApprovals()
        a, _ = ap.request("shell", None)
        b, _ = ap.request("write_file", None)
        ids = ap.pending_ids()
        assert set(ids) == {a, b}
        ids.clear()  # a snapshot — not the registry's own dict
        assert ap.pending_count() == 2
        ap.pop(a)
        assert ap.pending_ids() == [b]
        ap.pop(b)

    asyncio.run(scenario())
