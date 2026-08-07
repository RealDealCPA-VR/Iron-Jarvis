"""Context-window protection (v1.146.0).

What this replaces was ``messages[-30:]`` — a COUNT, standing in for a size.
The tests are organised around the two ways that failed:

* a small local window overflowing (the reported freeze), and
* a large window being wasted while the app pretended 30 was the right number.

The most important test in the file is
:func:`test_a_normal_conversation_on_a_normal_window_is_untouched`: this code
runs on every chat turn in the product, so "changes nothing when nothing needs
changing" is the property that keeps it safe to ship.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.context import estimate_tokens, plan_history
from iron_jarvis.context.budget import (
    DEFAULT_WINDOW,
    MIN_LAST_MESSAGE_CHARS,
    build_recap,
    output_reserve,
)
from iron_jarvis.daemon.app import create_app


def _msg(role: str, content: str):
    return SimpleNamespace(role=role, content=content)


def _convo(n: int, chars: int = 400):
    return [
        _msg("user" if i % 2 == 0 else "assistant", f"m{i} " + ("x" * chars))
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# (1) The estimator.
# --------------------------------------------------------------------------- #
def test_estimate_is_conservative_for_prose():
    """Erring high wastes a little window; erring low is the overflow this
    module exists to prevent."""
    text = "word " * 100  # 500 chars, ~100 real tokens
    assert 100 <= estimate_tokens(text) <= 200


def test_cjk_is_not_estimated_as_latin_prose():
    """~1 token per character, not ~4 — a 4x error that decides whether a turn
    fits an 8k window."""
    assert estimate_tokens("这是一段中文文本" * 10) > estimate_tokens("a" * 80)


def test_empty_text_is_free():
    assert estimate_tokens("") == 0


def test_output_reserve_is_bounded_at_both_ends():
    assert output_reserve(1000) >= 512      # a tiny window still gets room to reply
    assert output_reserve(1_000_000) <= 4096  # a huge one does not reserve a quarter


# --------------------------------------------------------------------------- #
# (2) The safe path: nothing needed, nothing done.
# --------------------------------------------------------------------------- #
def test_a_normal_conversation_on_a_normal_window_is_untouched():
    msgs = _convo(10, chars=300)
    plan = plan_history(msgs, window=128_000, system_text="short system")
    assert len(plan.messages) == 10
    assert plan.dropped == 0
    assert plan.recap == ""
    assert plan.note() == ""
    assert plan.suggest_larger is False
    assert [m["content"] for m in plan.messages] == [m.content for m in msgs]


def test_an_empty_conversation_is_not_a_crash():
    plan = plan_history([], window=8000, system_text="sys")
    assert plan.messages == [] and plan.note() == ""


def test_an_unknown_window_assumes_the_documented_default():
    plan = plan_history(_convo(4), window=None, system_text="")
    assert plan.window == DEFAULT_WINDOW


def test_the_message_count_cap_still_applies_on_a_huge_window():
    """The count is now only a pathological-replay guard (MAX_MESSAGES), not a
    size proxy: a client echoing 400 turns back must not cost 400 token
    estimates, however big the window."""
    from iron_jarvis.context.budget import MAX_MESSAGES

    plan = plan_history(_convo(400, chars=10), window=1_000_000, system_text="")
    assert len(plan.messages) == MAX_MESSAGES
    assert plan.dropped == 400 - MAX_MESSAGES


# --------------------------------------------------------------------------- #
# (3) The small-window path: the reported failure.
# --------------------------------------------------------------------------- #
def test_a_small_window_drops_oldest_and_says_so():
    plan = plan_history(_convo(30, chars=800), window=4000, system_text="sys")
    assert plan.dropped > 0
    assert len(plan.messages) < 30
    assert plan.recap.startswith("# Earlier in this conversation")
    assert "summarized to fit" in plan.note()
    # The NEWEST turn always survives — an answer to nothing is worthless.
    assert plan.messages[-1]["content"].startswith("m29")


def test_the_plan_actually_fits_the_window():
    """The whole point: system + history + reserve must land inside it."""
    system = "sys " * 200
    plan = plan_history(_convo(40, chars=900), window=6000, system_text=system)
    assert plan.used_tokens + output_reserve(6000) <= 6000


def test_a_single_oversized_message_is_clipped_not_dropped():
    huge = _msg("user", "z" * 200_000)
    plan = plan_history([huge], window=2000, system_text="sys")
    assert len(plan.messages) == 1
    assert plan.clipped_last is True
    assert plan.suggest_larger is True
    assert len(plan.messages[0]["content"]) >= MIN_LAST_MESSAGE_CHARS
    assert "clipped" in plan.note()


def test_a_system_prompt_that_eats_the_window_still_produces_a_turn():
    """Degenerate but reachable: a big profile + project + grounding on a tiny
    local model. Better a clipped question than a 500."""
    plan = plan_history(_convo(7), window=1200, system_text="s" * 40_000)
    assert len(plan.messages) == 1
    assert plan.suggest_larger is True
    # Whatever the newest message was — here the user's, as in a real turn.
    assert plan.messages[0]["role"] == "user"
    assert plan.messages[0]["content"].startswith("m6")


def test_stale_tool_output_is_trimmed_before_user_turns_are_dropped():
    msgs = [
        _msg("user", "question one"),
        _msg("tool", "OLD-TOOL-PAYLOAD " * 500),
        _msg("assistant", "answer one"),
        _msg("user", "question two"),
        _msg("assistant", "answer two"),
        _msg("user", "question three"),
    ]
    plan = plan_history(msgs, window=8000, system_text="sys")
    assert plan.tools_trimmed == 1
    assert not any("OLD-TOOL-PAYLOAD" in m["content"] for m in plan.messages)
    # ...and no USER turn was sacrificed to make room.
    assert plan.dropped == 0
    assert sum(1 for m in plan.messages if m["role"] == "user") == 3


# --------------------------------------------------------------------------- #
# (4) The recap: a summary that cannot invent.
# --------------------------------------------------------------------------- #
def test_the_recap_only_quotes_what_was_actually_said():
    dropped = [_msg("user", "Reconcile the Q3 vendor ledger"), _msg("assistant", "Done.")]
    recap = build_recap(dropped)
    assert "Reconcile the Q3 vendor ledger" in recap
    assert "You:" in recap and "Iron Jarvis:" in recap


def test_the_recap_says_it_is_partial():
    """Presenting a condensed record as the conversation itself would be the
    dishonest version of this feature."""
    assert "condensed" in build_recap([_msg("user", "hello there")])


def test_the_recap_is_bounded():
    recap = build_recap([_msg("user", "x" * 500) for _ in range(50)])
    assert len(recap) < 1200


def test_nothing_dropped_means_no_recap():
    assert build_recap([]) == ""


# --------------------------------------------------------------------------- #
# (5) Wired into the real chat lanes.
# --------------------------------------------------------------------------- #
def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _pin_window(client, tokens: int):
    r = client.put(
        "/settings", json={"values": {"model_context_windows": {"mock": tokens}}}
    )
    assert r.status_code == 200, r.text


def test_chat_reports_context_usage(tmp_path):
    client = _client(tmp_path)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    ctx = r.json()["context"]
    assert ctx["window"] > 0 and ctx["used"] > 0
    assert ctx["dropped"] == 0 and ctx["suggest_larger"] is False


def test_a_pinned_small_window_trims_a_long_chat_and_notes_it(tmp_path):
    client = _client(tmp_path)
    _pin_window(client, 3000)
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "y" * 900}
        for i in range(24)
    ]
    body = client.post("/chat", json={"messages": msgs}).json()
    assert body["context"]["dropped"] > 0
    assert body["context"]["recap"] is True
    assert "summarized to fit" in body["reply"]


def test_the_recap_reaches_the_system_prompt_not_a_fake_user_turn(tmp_path):
    """Injecting it as a message would put words in the user's mouth."""
    client = _client(tmp_path)
    _pin_window(client, 3000)
    seen: dict = {}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class=None):
        from iron_jarvis.providers.adapters.base import LLMResponse
        from iron_jarvis.providers.router import RouteResult

        seen["system"] = system
        seen["messages"] = messages
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    client.app.state.platform.router.complete = fake_complete
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "y" * 900}
        for i in range(24)
    ]
    client.post("/chat", json={"messages": msgs})
    assert "# Earlier in this conversation" in seen["system"]
    assert not any("Earlier in this conversation" in (m.content or "") for m in seen["messages"])


def test_the_stream_lane_plans_the_same_way(tmp_path):
    """Lock-step: the two lanes must never disagree about what fits."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    _pin_window(client, 3000)
    captured: dict = {}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        captured["system"] = system
        captured["messages"] = messages
        adapter = platform.providers.get(
            provider or platform.router.default_provider, model
        )
        async for frame in adapter.stream(system=system, messages=messages, tools=tools):
            if frame.get("type") == "final":
                yield {**frame, "provider": adapter.provider, "model": adapter.model}
            else:
                yield frame

    platform.router.stream = fake_stream
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "y" * 900}
        for i in range(24)
    ]
    r = client.post("/chat/stream", json={"messages": msgs})
    assert r.status_code == 200
    assert "# Earlier in this conversation" in captured["system"]
    assert len(captured["messages"]) < 24
    assert '"context"' in r.text  # the done frame carries the same shape


def test_a_broken_planner_degrades_to_the_old_slice_instead_of_500ing(tmp_path, monkeypatch):
    """A turn that runs with too much history is recoverable; a turn that 500s
    is not."""
    from iron_jarvis.daemon import chat_turn as mod

    monkeypatch.setattr(
        "iron_jarvis.context.plan_history",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    client = _client(tmp_path)
    r = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "still works"}]}
    )
    assert r.status_code == 200
    assert r.json()["reply"]
    assert mod is not None
