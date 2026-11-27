"""Phone history goes through the planner (north-star fix plan 6.8, v1.506.0).

The hard rule (``CLAUDE.md``, "History is budgeted, never sliced"; the area
entry in ``docs/rules/context.md``): a chat's history is fitted to the
answering model's window by ``context.budget.plan_history`` (reached through
``chat_turn._plan_context``), never cut at a fixed ``messages[-N:]``. The phone
lanes cut a conversation to its last 30 messages BEFORE the planner ever saw
it (``comm/inbound.py`` and ``POST /comm/threads/{id}/send`` both called
``CommThreadStore.history_body(tid, limit=30)``, whose loop was
``for m in msgs[-limit:]``), so on a model with a large window the phone
forgot what the desktop chat would have kept, and the planner's honest note
("N earlier messages are summarised") never counted what the slice threw away.

Both lanes are driven here with the REAL chat turn (the demo model), and the
planner's own input and output are read by wrapping ``context.plan_history``:

* the first message of a long thread reaches the planner's input, on both
  lanes;
* on a small-window model the plan still fits: no error, the newest message
  kept last, the window not exceeded, and only user/assistant lines (a phone
  thread stores no tool rounds, so there is no tool unit to split; the planner
  keeps one whole when there is, ``tests/test_context_budget*``).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm import InboundMessage, MockChannel
from iron_jarvis.daemon.app import create_app

EARLY = "EARLY-MARK-6-8: the client's year end is the last day of March."
N_SEEDED = 80  # more than the old slice (30) and the planner's MAX_MESSAGES (60)


class _PhoneChannel(MockChannel):
    supports_inbound = True

    def has_credentials(self) -> bool:
        return True


CFG = {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]}


def _spy_planner(monkeypatch) -> list[dict[str, Any]]:
    """Record every plan_history call: its input messages, window and plan."""
    import iron_jarvis.context as ctx

    real = ctx.plan_history
    calls: list[dict[str, Any]] = []

    def spy(messages, **kw):
        plan = real(messages, **kw)
        calls.append({"input": list(messages), "window": kw.get("window"), "plan": plan})
        return plan

    monkeypatch.setattr(ctx, "plan_history", spy)
    return calls


def _content(m) -> str:
    return str(m.get("content") if isinstance(m, dict) else getattr(m, "content", ""))


def _seed(store, tid: str) -> None:
    """A long phone conversation whose FIRST line is the one to remember."""
    for i in range(N_SEEDED):
        role = "user" if i % 2 == 0 else "assistant"
        text = EARLY if i == 0 else f"line {i}: " + ("words about the ledger " * 25)
        store.append(tid, role, text)


def _app(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    app = client.app
    ch = _PhoneChannel(dict(CFG))
    app.state.platform.notifier.add_channel("tg", ch)
    return client, app, ch


async def _inbound_turn(app, ch, text: str) -> dict[str, Any]:
    store = app.state.comm_thread_store
    poller = app.state.inbound_poller
    tid = store.resolve("tg", "777", "Val").id
    _seed(store, tid)
    return await poller._handle(
        "tg", ch, InboundMessage(sender_id="777", text=text, update_id=1, reply_to="777")
    )


async def test_the_inbound_lane_hands_the_whole_thread_to_the_planner(tmp_path, monkeypatch):
    client, app, ch = _app(tmp_path)
    calls = _spy_planner(monkeypatch)
    res = await _inbound_turn(app, ch, "what is the year end?")
    assert res["status"] == "chat", res
    assert calls, "the phone turn never reached the planner"
    seen = calls[-1]["input"]
    assert any(EARLY in _content(m) for m in seen), "the early message was sliced off"
    assert len(seen) > 30
    assert _content(seen[-1]) == "what is the year end?"


def test_the_desktop_send_lane_hands_the_whole_thread_to_the_planner(tmp_path, monkeypatch):
    client, app, ch = _app(tmp_path)
    store = app.state.comm_thread_store
    tid = store.resolve("tg", "777", "Val").id
    _seed(store, tid)
    calls = _spy_planner(monkeypatch)
    r = client.post(f"/comm/threads/{tid}/send", json={"text": "and the year end?"})
    assert r.status_code == 200, r.text
    assert calls, "the desktop reply never reached the planner"
    seen = calls[-1]["input"]
    assert any(EARLY in _content(m) for m in seen), "the early message was sliced off"
    assert len(seen) > 30
    assert _content(seen[-1]) == "and the year end?"


@pytest.mark.parametrize("window", [12_000, 20_000])
async def test_a_small_window_still_gets_a_plan_that_fits(tmp_path, monkeypatch, window):
    client, app, ch = _app(tmp_path)
    platform = app.state.platform
    platform.config.model_context_windows = {platform.config.default_provider: window}
    calls = _spy_planner(monkeypatch)
    res = await _inbound_turn(app, ch, "the newest question")
    assert res["status"] == "chat", res
    assert ch.sent, "nothing reached the phone"
    call = calls[-1]
    plan = call["plan"]
    assert call["window"] == window
    assert any(EARLY in _content(m) for m in call["input"])
    # It fits, and what fell off is accounted for, not silently gone.
    assert plan.used_tokens <= plan.window == window
    assert plan.dropped > 0 and plan.recap
    # The newest message is kept, last and whole.
    # (Release 7: the final plan also carries what the app adds beside the
    # newest message, as its ``rider``; the person's words are untouched.)
    newest = plan.messages[-1]
    assert (newest["role"], newest["content"]) == ("user", "the newest question")
    assert set(newest) <= {"role", "content", "rider"}
    assert not plan.clipped_last
    # Only user/assistant lines: no orphaned tool result can reach the model.
    assert {m["role"] for m in plan.messages} <= {"user", "assistant"}
    # The kept lines are the NEWEST ones, in order (a contiguous tail).
    kept = [m["content"] for m in plan.messages]
    flat = [_content(m) for m in call["input"]]
    assert flat[-len(kept):] == kept
