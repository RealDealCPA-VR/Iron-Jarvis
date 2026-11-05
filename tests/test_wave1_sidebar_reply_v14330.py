"""v1.433.0 (Wave 1, group C — A5, A6, A7): what the browser sidebar SAYS at
the end of a turn.

A5. The chat lane's ``done`` frame carries the turn's answer as ``reply``. The
panel read a ``text`` key the frame never had, so everything that rides ONLY
in ``done`` — the sentence for a model that wrote nothing after its tools, the
refusal footnotes, the stopped / language / context notes, the honesty notes —
never reached the sidebar, which painted "finished without an answer". Two old
tests fed the translator a ``{"text": ...}`` frame, which is how it survived;
the cases here drive the REAL lane's frame.

A6. The lane sends ``reset`` to take back words it streamed (a reply rewritten
into the user's language, a tool call written as text after a cut-off). The
panel dropped it, so both versions showed and were remembered.

A7. The lane's error frame says the failure in plain words (``title`` +
``fix``); the sidebar showed only the raw ``detail``.

No wall-clock assertions: every wait is the harness's bounded budget.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.browser import panel as panel_mod
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.panel import (
    PanelTurns,
    failure_sentence,
    settle_answer,
    without_lane_demo_note,
)
from iron_jarvis.daemon import chat_turn
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import DEMO_OPENING
from tests._fakes.panel_harness import _BROWSER_SENTENCE, RealApp

#: The old sentence a turn with no streamed words was given.
_EMPTY = "finished this turn without an answer"


class _Conn:
    """Records every panel event, and what the conversation held when it left."""

    def __init__(self, turns: PanelTurns) -> None:
        self.turns = turns
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.history_at: dict[str, list[dict[str, str]]] = {}

    async def send(self, frame: dict) -> None:
        event = str(frame.get("event") or "")
        self.sent.append((event, dict(frame.get("payload") or {})))
        self.history_at.setdefault(event, [dict(r) for r in self.turns._history])

    def texts(self, event: str) -> list[str]:
        return [str(p.get("text") or "") for e, p in self.sent if e == event]

    def names(self) -> list[str]:
        return [e for e, _ in self.sent]


def _turns() -> tuple[PanelTurns, _Conn]:
    turns = PanelTurns(SimpleNamespace(browser=None))
    turns._turn_text, turns._remembered, turns._answer = "q?", False, []
    return turns, _Conn(turns)


def _drive(turns: PanelTurns, conn: _Conn, frames: list[tuple[str, dict]]) -> None:
    async def go() -> None:
        for event, data in frames:
            await turns._translate(conn, event, data)

    asyncio.run(go())


def _remembered(turns: PanelTurns) -> str:
    rows = [r for r in turns._history if r["role"] == "assistant"]
    return rows[-1]["content"] if rows else ""


# --------------------------------------------------------------------------- #
# The rule, as a table
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "shown, final, want",
    [
        ("Hello", "Hello", (False, "")),
        ("Hello", "Hello\n\n_Note: x._", (False, "\n\n_Note: x._")),
        ("", "Whole reply.", (False, "Whole reply.")),
        ("  Hello", "Hello and more", (False, " and more")),
        ("Hello  ", "Hello", (False, "")),
        ("   ", "Hello", (True, "Hello")),
        ("Bonjour", "Hello.", (True, "Hello.")),
        ("Hello world", "Hello", (True, "Hello")),
    ],
)
def test_settle_answer_table(shown: str, final: str, want: tuple[bool, str]) -> None:
    assert settle_answer(shown, final) == want


def test_failure_sentence_is_the_chats_rule() -> None:
    assert failure_sentence("Claude Code couldn't answer", "Press Send again.") == (
        "Claude Code couldn't answer. Press Send again."
    )
    assert failure_sentence("It stopped.", "") == "It stopped."
    assert failure_sentence("", "a fix with no title") == ""


# --------------------------------------------------------------------------- #
# A5: the reply rides home
# --------------------------------------------------------------------------- #
def test_words_that_ride_only_in_done_are_added_to_the_answer() -> None:
    """MUTATION: read `data.get("text")` again -> the footnote never shows."""
    turns, conn = _turns()
    streamed = "The page is about tax forms."
    footnote = "\n\n_Note: Jarvis couldn't click a button (not turned on for this chat)._"
    _drive(turns, conn, [
        ("token", {"text": "The page is "}),
        ("token", {"text": "about tax forms."}),
        ("done", {"reply": streamed + footnote, "route": {"reason": "explicit"}}),
    ])
    assert conn.texts(P.PANEL_EVENT_DELTA) == ["The page is ", "about tax forms.", footnote]
    assert P.PANEL_EVENT_RESET not in conn.names()
    # The conversation remembers the reply the user read, BEFORE `done` leaves.
    assert conn.history_at[P.PANEL_EVENT_DONE][-1] == {"role": "assistant", "content": streamed + footnote}
    assert conn.texts(P.PANEL_EVENT_DONE) == [streamed + footnote]


def test_a_reply_that_was_never_streamed_is_said_not_the_empty_sentence() -> None:
    """The model wrote nothing after its tools: the lane's own sentence (which
    names what the tool returned) is the answer, not "finished without an
    answer". MUTATION: the old `_delta_seen` gate -> the empty sentence."""
    turns, conn = _turns()
    reply = chat_turn._no_text_reply(["browser_read_page"], "Page title: Taxes 2026")
    _drive(turns, conn, [
        ("tool_call", {"name": "browser_read_page", "status": "finished", "ok": True}),
        ("done", {"reply": reply, "route": {"reason": "explicit"}}),
    ])
    assert conn.texts(P.PANEL_EVENT_DELTA) == [reply]
    assert _EMPTY not in " ".join(conn.texts(P.PANEL_EVENT_DELTA))
    assert _remembered(turns) == reply


def test_a_reply_that_does_not_continue_the_bubble_clears_it_first() -> None:
    """A rewrite the lane did NOT keep (the original reply stands): the bubble
    shows the rewrite, the reply is something else — clear, then say it."""
    turns, conn = _turns()
    _drive(turns, conn, [
        ("token", {"text": "Bonjour, voici la page."}),
        ("done", {"reply": "Hello, here is the page.", "route": {"reason": "explicit"}}),
    ])
    names = conn.names()
    assert names.index(P.PANEL_EVENT_RESET) < len(names) - 1
    after = names.index(P.PANEL_EVENT_RESET)
    tail = [p["text"] for e, p in conn.sent[after:] if e == P.PANEL_EVENT_DELTA]
    assert tail == ["Hello, here is the page."]
    assert _remembered(turns) == "Hello, here is the page."


def test_the_route_notice_comes_after_the_answer_and_the_demo_is_named_once() -> None:
    """The lane appends its demo note ("Choose a model ABOVE" — the chat's
    picker); the sidebar says the demo in its own line instead, after the
    answer (a `tool` frame ends the panel's bubble). MUTATION: drop
    `without_lane_demo_note` -> the fix is said twice."""
    turns, conn = _turns()
    answer = f"{DEMO_OPENING} Hello."
    reply = answer + chat_turn.demo_note([], answer)
    assert reply != answer, "the lane's note is not where this test expects it"
    _drive(turns, conn, [
        ("token", {"text": answer}),
        ("done", {"reply": reply, "route": {"reason": "mock", "provider": "mock", "model": "mock"}}),
    ])
    names = conn.names()
    assert names[-2:] == [P.PANEL_EVENT_TOOL, P.PANEL_EVENT_DONE], names
    assert conn.texts(P.PANEL_EVENT_DELTA) == [answer]
    every = " ".join(conn.texts(P.PANEL_EVENT_DELTA) + conn.texts(P.PANEL_EVENT_TOOL))
    assert every.count("Choose a model") == 1, every
    assert _remembered(turns) == answer


def test_every_lane_demo_note_is_taken_out() -> None:
    for note in panel_mod._lane_demo_notes():
        assert without_lane_demo_note("Hi." + note) == "Hi."
    assert len(panel_mod._lane_demo_notes()) == 3


# --------------------------------------------------------------------------- #
# A6: a reset takes words back
# --------------------------------------------------------------------------- #
def test_the_lanes_reset_clears_the_answer_and_is_passed_on() -> None:
    """MUTATION: ignore `reset` again -> both versions in the answer."""
    turns, conn = _turns()
    _drive(turns, conn, [
        ("token", {"text": "Bonjour, "}),
        ("token", {"text": "<tool_call>"}),
        ("reset", {"reason": "language"}),
    ])
    resets = [p for e, p in conn.sent if e == P.PANEL_EVENT_RESET]
    assert resets == [{"reason": "language"}]
    assert turns._answer == []
    # A panel reopened now is shown only what is still being said.
    turns._task = SimpleNamespace(done=lambda: False)
    assert turns.history_view()["live"]["answer"] == ""
    turns._task = None
    _drive(turns, conn, [
        ("token", {"text": "Hello."}),
        ("done", {"reply": "Hello.\n\n_Note: rewritten in English._", "route": {"reason": "explicit"}}),
    ])
    assert _remembered(turns) == "Hello.\n\n_Note: rewritten in English._"
    assert "Bonjour" not in _remembered(turns)


def test_a_reset_that_drops_a_few_words_keeps_the_rest() -> None:
    turns, conn = _turns()
    _drive(turns, conn, [
        ("token", {"text": "Kept. "}),
        ("token", {"text": "dropped"}),
        ("reset", {"reason": "retry", "drop": len("dropped")}),
    ])
    after = conn.names().index(P.PANEL_EVENT_RESET)
    assert [p["text"] for e, p in conn.sent[after:] if e == P.PANEL_EVENT_DELTA] == ["Kept. "]
    assert turns._answer == ["Kept. "]


# --------------------------------------------------------------------------- #
# A7: a failure in plain words
# --------------------------------------------------------------------------- #
def test_the_lanes_error_frame_says_title_and_fix_and_folds_the_technical_text() -> None:
    """The frame the lane really sends (`chat_turn.error_payload`). MUTATION:
    show `detail` again -> the raw text."""
    turns, conn = _turns()
    payload = chat_turn.error_payload(
        SimpleNamespace(platform=None),
        SimpleNamespace(provider="", pane_id=""),
        RuntimeError("ReadError: the socket closed"),
    )
    assert payload.get("title") and payload.get("technical"), payload
    _drive(turns, conn, [("token", {"text": "Looking"}), ("error", payload)])
    [sent] = [p for e, p in conn.sent if e == P.PANEL_EVENT_ERROR]
    assert sent["text"] == failure_sentence(payload["title"], payload.get("fix"))
    assert sent["text"] != payload["detail"]
    assert sent["technical"] == payload["technical"]
    # What was read is remembered before the error leaves.
    assert conn.history_at[P.PANEL_EVENT_ERROR][-1] == {"role": "assistant", "content": "Looking"}


def test_an_older_error_frame_reads_as_before() -> None:
    turns, conn = _turns()
    _drive(turns, conn, [("error", {"detail": "Iron Jarvis could not reach that page."})])
    [sent] = [p for e, p in conn.sent if e == P.PANEL_EVENT_ERROR]
    assert sent == {"text": "Iron Jarvis could not reach that page."}


# --------------------------------------------------------------------------- #
# Through the REAL lane
# --------------------------------------------------------------------------- #
def _record_lane_frames(monkeypatch) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    real = panel_mod.parse_sse

    def recording(chunk: str):
        out = real(chunk)
        seen.extend(out)
        return out

    monkeypatch.setattr(panel_mod, "parse_sse", recording)
    return seen


def _shown_answer(events: list[tuple[str, dict]]) -> str:
    """The words of the LAST answer bubble, by the panel's own rules."""
    words: list[str] = []
    for event, payload in events:
        if event == P.PANEL_EVENT_DELTA:
            words.append(str(payload.get("text") or ""))
        elif event in (P.PANEL_EVENT_TOOL, P.PANEL_EVENT_RESET):
            # The route notice is the line AFTER the answer; it ends no answer.
            if event == P.PANEL_EVENT_TOOL and payload.get("name") == "route":
                continue
            words = []
    return "".join(words)


def test_through_the_real_lane_a_model_that_wrote_nothing_after_its_tools(tmp_path, monkeypatch):
    """The real chat lane, the real pairing socket, a model that calls a tool
    and then writes nothing (even when asked once more). The lane's reply —
    which streamed no token at all — is what the sidebar shows and remembers.
    """
    lane = _record_lane_frames(monkeypatch)
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        app.stub_tools()
        calls = {"n": 0}

        async def fake_stream(*args: Any, **kw: Any):
            calls["n"] += 1
            if calls["n"] == 1:
                resp = LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="browser_read_page", arguments={})],
                    usage={},
                )
            else:
                resp = LLMResponse(text="", usage={})
            yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

        app.platform.router.stream = fake_stream
        app.panel.send(P.PANEL_ACTION_SEND, text=_BROWSER_SENTENCE)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        events = app.panel.events()

    [done] = [d for e, d in lane if e == "done"]
    assert "reply" in done and "text" not in done, sorted(done)
    want = without_lane_demo_note(done["reply"])
    assert want.strip() and _EMPTY not in want
    assert _shown_answer(events) == want
    deltas = " ".join(str(p.get("text") or "") for e, p in events if e == P.PANEL_EVENT_DELTA)
    assert _EMPTY not in deltas


def test_through_the_real_lane_a_language_rewrite_takes_the_first_words_back(tmp_path, monkeypatch):
    """The lane rewrites a reply that left the user's language and sends
    `reset` before the rewrite's words. The sidebar empties its bubble and
    shows only the rewrite; the conversation never holds the first version."""
    from iron_jarvis.daemon.routes import chat as chat_routes

    monkeypatch.setattr(
        chat_routes, "_language_leak",
        lambda platform, text, user_text: "en" if "Bonjour" in (text or "") else "",
    )
    lane = _record_lane_frames(monkeypatch)
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()
        calls = {"n": 0}

        async def fake_stream(*args: Any, **kw: Any):
            calls["n"] += 1
            text = "Bonjour tout le monde." if calls["n"] == 1 else "Hello everyone."
            yield {"type": "text", "text": text}
            yield {"type": "final", "response": LLMResponse(text=text, usage={}),
                   "provider": "mock", "model": "mock"}

        app.platform.router.stream = fake_stream
        app.panel.send(P.PANEL_ACTION_SEND, text="say hello to everyone")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        events = app.panel.events()
        # A reopened panel is replayed what the conversation remembers.
        seen = len([1 for e, _ in events if e == P.PANEL_EVENT_HISTORY])
        app.panel.send(P.PANEL_ACTION_OPEN)
        replay = app.panel.wait_for(P.PANEL_EVENT_HISTORY, "open never replayed", nth=seen + 1)

    assert any(e == "reset" for e, _ in lane), "the lane sent no reset: the case drove nothing"
    names = [e for e, _ in events]
    assert P.PANEL_EVENT_RESET in names
    [done] = [d for e, d in lane if e == "done"]
    want = without_lane_demo_note(done["reply"])
    assert want.startswith("Hello everyone."), want
    shown = _shown_answer(events)
    assert shown == want and "Bonjour" not in shown
    assert replay["turns"][-1] == {"role": "assistant", "text": want}, replay
    assert "Bonjour" not in " ".join(t["text"] for t in replay["turns"])
