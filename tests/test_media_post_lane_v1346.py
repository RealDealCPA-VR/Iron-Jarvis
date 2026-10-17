"""v1.346.0 (post-lane spend) — the POST lane refuses a priced generation it
cannot ask about.

THE USER'S RULE: ask before EVERY spend. The stream lane asks with a card
(`routes/chat.py` `_spend`); ``POST /chat`` (`run_chat_turn`) has no card and
"every armed tool is its own grant", so a raw API caller sending
``tools: ["generate_media"]`` with a PAID prompt used to run the generation
with nobody asked. Now the lane consults the same ``spend_checks_for`` and a
verdict with ``ask`` True is REFUSED through the registry's deny path — the
tool is never started, the model-facing tool message reads ``NOT RUN: <the
quote's words> — this lane cannot ask for approval; …``, a ledger row and a
``tool.denied`` event exist, and the reply footnotes it. A FREE verdict runs
exactly as before. POST-lane-only by design (the stream lane cards instead).

Driven through the REAL app and the REAL ``registry.invoke`` with the offline
``FakeProvider`` (the harness of ``test_generate_media_tool_v1346``); the
model is the only double.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    REFUSAL_FOOTNOTE_WORDS,
    SPEND_NOT_ASKED_FOOTNOTE,
    SPEND_NOT_ASKED_LABEL,
    refusal_label,
    repeated_call_refusal,
    run_chat_turn,
    spend_not_asked_invoke_kwargs,
)
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.media.jobs import media_markdown
from iron_jarvis.tools.base import SpendCheck

from tests.test_generate_media_tool_v1346 import (
    MAKE,
    PAID_WORDS,
    TOOL,
    _call,
    _ledger,
    _PostRouter,
    _wire,
)

FOOTNOTE = REFUSAL_FOOTNOTE_WORDS[SPEND_NOT_ASKED_FOOTNOTE]
TAIL = "this lane cannot ask for approval; use the chat page (POST /chat/stream)"


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


class _RecordingRouter(_PostRouter):
    """The POST lane's router double that also keeps every round's messages,
    so the model-facing ``role="tool"`` content can be read."""

    def __init__(self, calls, final="Done."):
        super().__init__(calls, final=final)
        self.seen: list[list[Any]] = []

    async def complete(self, **kw):
        self.seen.append(list(kw.get("messages") or []))
        return await super().complete(**kw)


def _tool_messages(router: _RecordingRouter) -> list[Any]:
    """The ``role="tool"`` messages the SECOND completion was shown."""
    assert len(router.seen) >= 2, "the lane never asked the model for its answer"
    return [m for m in router.seen[1] if getattr(m, "role", "") == "tool"]


def _denied_events(platform, name=TOOL) -> list[Any]:
    return [
        e for e in list(platform.event_bus.history)
        if e.type == "tool.denied" and (e.payload or {}).get("tool") == name
    ]


def _post(platform, text: str) -> dict:
    body = ChatBody(messages=[{"role": "user", "content": text}], tools=[TOOL], thread_id="chat_post")
    return asyncio.run(run_chat_turn(platform, {}, body))


# --------------------------------------------------------------------------- #
# the lane
# --------------------------------------------------------------------------- #


def test_a_paid_prompt_is_refused_without_running_and_the_refusal_is_everywhere_it_should_be(app):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    platform.router = router = _RecordingRouter([_call("a paid cat")], final="Done.")
    res = _post(platform, MAKE)
    # The tool was armed for the model and quoted, but NEVER started.
    assert TOOL in router.tools[0]
    assert provider.quotes == 1
    assert provider.starts == [], "a priced generation ran with nobody asked"
    # The model-facing tool content carries the refusal in the quote's words.
    (tool_msg,) = _tool_messages(router)
    assert tool_msg.name == TOOL
    assert tool_msg.content.startswith(f"{SPEND_NOT_ASKED_LABEL}: {PAID_WORDS} — {TAIL}")
    assert "make the request one the service does for free" in tool_msg.content
    # The reply footnotes it and nothing was made.
    assert f"_Note: {TOOL} could not run ({FOOTNOTE})._" in res["reply"]
    assert "![" not in res["reply"]
    assert res["documents"] == []
    assert TOOL not in res["tools_used"]
    # The ledger row + the tool.denied event exist for the refusal (the same
    # path a declined card takes — never a call that silently did not happen).
    rows = _ledger(platform)
    assert len(rows) == 1 and rows[0].ok is False
    assert PAID_WORDS in rows[0].output and TAIL in rows[0].output
    (ev,) = _denied_events(platform)
    assert ev.payload["kind"] == SPEND_NOT_ASKED_LABEL
    assert PAID_WORDS in ev.payload["reason"]


def test_a_free_prompt_runs_exactly_as_before_and_the_reply_holds_the_image(app):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    platform.router = router = _RecordingRouter([_call("a free cat")], final="Done.")
    res = _post(platform, MAKE)
    assert len(provider.starts) == 1 and provider.starts[0][1] == "image"
    pngs = [p for p in res["documents"] if p.endswith(".png")]
    assert pngs and Path(pngs[0]).is_file()
    assert res["reply"] == f"Done.\n\n{media_markdown('image', pngs[0])}"
    assert TOOL in res["tools_used"]
    (tool_msg,) = _tool_messages(router)
    assert "free (Maker: 4 left today)" in tool_msg.content
    assert SPEND_NOT_ASKED_LABEL not in tool_msg.content
    rows = _ledger(platform)
    assert len(rows) == 1 and rows[0].ok is True and "free (Maker" in rows[0].output
    assert _denied_events(platform) == []
    # The quote was paid ONCE: execute reused the spend check's estimate.
    assert provider.quotes == 1


def test_a_paid_and_a_free_call_in_one_round_are_judged_each_on_its_own(app):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    platform.router = router = _RecordingRouter(
        [_call("a paid cat", cid="c1"), _call("a free dog", cid="c2")], final="Done."
    )
    res = _post(platform, MAKE)
    assert len(provider.starts) == 1, "the paid call ran beside the free one"
    paid, free = _tool_messages(router)
    assert paid.tool_call_id == "c1" and paid.content.startswith(f"{SPEND_NOT_ASKED_LABEL}:")
    assert free.tool_call_id == "c2" and "free (Maker" in free.content
    assert res["reply"].count(FOOTNOTE) == 1
    assert len([p for p in res["documents"] if p.endswith(".png")]) == 1


# --------------------------------------------------------------------------- #
# the helper + the footnote vocabulary
# --------------------------------------------------------------------------- #


def test_the_invoke_kwargs_and_the_footnote_label_agree():
    paid = SpendCheck(ask=True, words=PAID_WORDS, cost_note="7 credits")
    kw = spend_not_asked_invoke_kwargs(paid)
    assert kw["deny_label"] == SPEND_NOT_ASKED_LABEL
    assert kw["deny_reason"].startswith(f"{PAID_WORDS} — {TAIL}")
    # The registry formats ``f"{label}: {reason}"``; the footnote reads it back
    # — and the repeated-call breaker's own ``NOT RUN:`` sentence still is NOT
    # a footnoted refusal (the v1.344.0 pin), told apart by the tail.
    assert refusal_label(f"{kw['deny_label']}: {kw['deny_reason']}") == SPEND_NOT_ASKED_FOOTNOTE
    assert refusal_label(repeated_call_refusal(TOOL, 2)) == ""
    assert refusal_label("permission denied: no") == "permission denied"
    # Free, no opinion, or an unknown shape: the common path, byte-identical.
    assert spend_not_asked_invoke_kwargs(SpendCheck(ask=False, words="x", cost_note="free")) == {}
    assert spend_not_asked_invoke_kwargs(None) == {}
    # A verdict with no words still refuses in words.
    assert "would spend money" in spend_not_asked_invoke_kwargs(SpendCheck(ask=True, words="", cost_note=""))["deny_reason"]
