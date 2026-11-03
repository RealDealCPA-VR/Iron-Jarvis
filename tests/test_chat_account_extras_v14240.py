"""v1.424.0 (review): a chat's chosen ACCOUNT pins its extra model calls too.

A chat put on one Iron-Proxy account (``ChatBody.account``) pinned its TURNS,
but two more model calls the chat makes on the same model leased Iron-Proxy's
first free account — a login the user did not choose:

* ``POST /chat/followups`` (the "next questions" chips, written by the model
  that wrote the reply);
* ``POST /chat/compact`` (the user's "Summarize" press on a long chat).

Both now take ``account`` and run under the SAME rule as a turn
(``iron_proxy.choice.pin_for``): pinned to that account, or refused in words
(follow-ups: no suggestions, a reason — never a 500; compaction: the turn's
409) — never another login. No account: exactly as before.
"""

from __future__ import annotations

import pytest

from iron_jarvis.iron_proxy import choice
from tests.test_account_choice_v14240 import CLAUDE, only_pinned, unpinned, wire
from tests.test_build_accounts_v1368 import Behaviour, enable, isolate, make_fake, picks
from tests.test_build_sessions_v1367 import asgi


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


def _conversation(n: int = 12) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"line {i} about the plan"})
    return out


async def test_followups_run_on_the_chats_chosen_account(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour(['["What next?"]']))
    app.state.platform.config.chat_followups = True
    fake.queries.clear()
    status, out = await asgi(app, "POST", "/chat/followups", {
        "messages": _conversation(2), **CLAUDE, "account": "c-spare",
    })
    assert status == 200, out
    assert beh.leases and all(lease == ("claude-cli", "c-spare") for lease in beh.leases), beh.leases
    only_pinned(fake, "c-spare")


async def test_followups_with_an_account_iron_proxy_cannot_use_make_no_call(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour(['["What next?"]']))
    app.state.platform.config.chat_followups = True
    fake.queries.clear()
    status, out = await asgi(app, "POST", "/chat/followups", {
        "messages": _conversation(2), **CLAUDE, "account": "x-plus",
    })
    assert status == 200, out
    assert out["suggestions"] == [] and "is a Codex account" in out["reason"]
    assert beh.attempts == [] and picks(fake) == []


async def test_followups_with_no_account_lease_as_before(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour(['["What next?"]']))
    app.state.platform.config.chat_followups = True
    fake.queries.clear()
    status, out = await asgi(app, "POST", "/chat/followups", {"messages": _conversation(2), **CLAUDE})
    assert status == 200, out
    assert beh.leases == [("claude-cli", "c-work")]
    unpinned(fake)


async def test_a_manual_summary_runs_on_the_chats_chosen_account(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    fake.queries.clear()
    await asgi(app, "POST", "/chat/compact", {
        "messages": _conversation(), **CLAUDE, "account": "c-spare",
    })
    assert beh.leases and all(lease == ("claude-cli", "c-spare") for lease in beh.leases), beh.leases
    only_pinned(fake, "c-spare")


async def test_a_manual_summary_on_an_account_it_cannot_use_is_a_409_in_words(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    fake.queries.clear()
    status, out = await asgi(app, "POST", "/chat/compact", {
        "messages": _conversation(), **CLAUDE, "account": "x-plus",
    })
    assert status == 409 and "is a Codex account" in out["detail"]
    assert beh.attempts == [] and picks(fake) == []


async def test_a_manual_summary_with_iron_proxy_off_and_an_account_is_refused(app, monkeypatch):
    beh = wire(app, monkeypatch, Behaviour())
    status, out = await asgi(app, "POST", "/chat/compact", {
        "messages": _conversation(), **CLAUDE, "account": "c-work",
    })
    assert status == 409 and out["detail"] == choice.OFF_WORDS
    assert beh.attempts == []


# --------------------------------------------------------------------------- #
# the chat's hand-off to an agent (POST /sessions) and its follow-up
# --------------------------------------------------------------------------- #


async def test_a_chats_hand_off_runs_on_the_chats_account(app, fake, monkeypatch):
    """A chat on one account hands the work to an agent (the escalation's
    ``POST /sessions``): the agent's run leases THAT account, every call; a
    follow-up (``continue``) keeps it."""
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    fake.queries.clear()
    status, view = await asgi(app, "POST", "/sessions", {
        "task": "Write the notes.", "agent_type": "builder", **CLAUDE, "wait": True,
        "origin": "chat", "account": "c-spare",
    })
    assert status == 200, view
    assert beh.leases and all(lease == ("claude-cli", "c-spare") for lease in beh.leases), beh.leases
    only_pinned(fake, "c-spare")
    beh.leases.clear()
    fake.queries.clear()
    status, more = await asgi(app, "POST", f"/sessions/{view['id']}/continue", {
        "message": "And a summary.", "wait": True,
    })
    assert status == 200, more
    assert beh.leases and all(lease == ("claude-cli", "c-spare") for lease in beh.leases), beh.leases
    only_pinned(fake, "c-spare")


async def test_a_hand_off_on_an_account_it_cannot_use_is_a_409_before_any_session(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    fake.queries.clear()
    status, out = await asgi(app, "POST", "/sessions", {
        "task": "Write the notes.", "agent_type": "builder", **CLAUDE, "wait": True,
        "origin": "chat", "account": "x-plus",
    })
    assert status == 409 and "is a Codex account" in out["detail"]
    assert beh.attempts == [] and picks(fake) == []
    status, rows = await asgi(app, "GET", "/sessions")
    assert rows["sessions"] == []


async def _ended(app, sid: str, bound_s: float = 60.0) -> str:
    """Wait until the hand-off's own run has ENDED (v1.427.1: the release
    gate's slower runner was still running it after the fixed 0.5 s this test
    used to sleep, so the follow-up was refused "A follow-up is already
    running" — a wait on the thing asserted, never on a clock). A bound is a
    hang detector, not a speed bar."""
    import asyncio
    import time

    deadline = time.monotonic() + bound_s
    while True:
        _, view = await asgi(app, "GET", f"/sessions/{sid}")
        state = ((view or {}).get("session") or {}).get("status")
        if state not in ("active", "queued"):
            return str(state)
        assert time.monotonic() < deadline, f"the hand-off never ended ({state})"
        await asyncio.sleep(0.05)


async def test_a_hand_off_whose_account_went_off_since_fails_in_words_never_another_login(
    app, fake, monkeypatch
):
    import asyncio

    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    status, view = await asgi(app, "POST", "/sessions", {
        "task": "Write the notes.", "agent_type": "builder", **CLAUDE, "wait": False,
        "origin": "chat", "account": "c-spare",
    })
    assert status == 200, view
    await _ended(app, view["id"])
    beh.leases.clear()
    fake.profiles["c-spare"]["enabled"] = False
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    fake.queries.clear()
    status, more = await asgi(app, "POST", f"/sessions/{view['id']}/continue", {
        "message": "And a summary.", "wait": True,
    })
    assert status == 200, more
    assert more["status"] == "failed" and "turned off" in (more.get("summary") or ""), more
    assert ("claude-cli", "c-work") not in beh.leases and picks(fake) == []


async def test_a_hand_off_with_no_account_leases_as_before(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    fake.queries.clear()
    status, view = await asgi(app, "POST", "/sessions", {
        "task": "Write the notes.", "agent_type": "builder", **CLAUDE, "wait": True, "origin": "chat",
    })
    assert status == 200, view
    assert beh.leases and beh.leases[0] == ("claude-cli", "c-work")
    unpinned(fake)


# --------------------------------------------------------------------------- #
# a schedule's account against its custom agent's own account
# --------------------------------------------------------------------------- #


async def _agent(app, name: str, **extra) -> None:
    status, body = await asgi(app, "POST", "/agents", {
        "name": name, "system_prompt": "You help.", "tools": ["read_file"], **CLAUDE, **extra,
    })
    assert status in (200, 201), body


async def test_a_schedules_account_never_loses_quietly_to_its_agents_own(app, fake, monkeypatch):
    """The schedule names account A; its custom agent runs on its OWN
    account B. Before, the save passed and every fire ran on B (the agent's
    scope armed its own pin inside the schedule's) — an account the schedule
    did not choose. Now the conflict is refused in words when saved, and at
    a fire (the agent's account set since)."""
    import asyncio

    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    await _agent(app, "scout", account="c-spare")
    status, out = await asgi(app, "POST", "/schedules", {
        "name": "rounds", "cron": "0 9 * * *", "kind": "task",
        "payload": {"task": "Say hi.", "agent_type": "scout", "account": "c-work"},
    })
    assert status == 409 and "runs on its own account" in out["detail"], out
    # The SAME account as the agent's own: fine.
    status, out = await asgi(app, "POST", "/schedules", {
        "name": "same", "cron": "0 9 * * *", "kind": "task",
        "payload": {"task": "Say hi.", "agent_type": "scout", "account": "c-spare"},
    })
    assert status == 200, out
    # Saved while the agent had no account; the agent's account set since: the
    # fire is refused in words — no session, no lease.
    await _agent(app, "helper")
    status, out = await asgi(app, "POST", "/schedules", {
        "name": "later", "cron": "0 9 * * *", "kind": "task",
        "payload": {"task": "Say hi.", "agent_type": "helper", "account": "c-work"},
    })
    assert status == 200, out
    status, _ = await asgi(app, "PATCH", "/agents/helper", {"account": "c-spare"})
    assert status == 200
    fake.queries.clear()
    status, ran = await asgi(app, "POST", "/schedules/later/run", {})
    assert ran["last_status"] == "error" and "runs on its own account" in ran["last_detail"], ran
    assert beh.attempts == [] and picks(fake) == []
    await asyncio.sleep(0)
