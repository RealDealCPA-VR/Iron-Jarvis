"""v1.433.0 review (Wave 1, group C): the words the browser SIDEBAR now shows
must be true where they are read.

A5/A7 made the sidebar show the chat lane's own end-of-turn words (the
``done`` frame's ``reply``) and its failure line (``title. fix``). Those words
were written for the chat PAGE, which has a Retry button and an Edit press on
every message; the sidebar has neither. So "Press Retry" — the generic fix, a
timeout's, a dropped connection's, and the empty-answer sentence — and a
declined conversation's "edit an earlier message" told the user to press a
button that is not there. ``failure_words.sidebar_surface`` (armed by the
panel around its turn) words them for the sidebar; the chat's own words are
unchanged byte for byte.

No wall-clock assertions: every wait is the harness's bounded budget.
"""

from __future__ import annotations

from typing import Any

from iron_jarvis.browser import protocol as P
from iron_jarvis.providers.adapters.base import LLMResponse
from tests._fakes.panel_harness import RealApp


def _said(events: list[tuple[str, dict]]) -> str:
    """Every word the sidebar painted: answer deltas, steps, errors."""
    return " ".join(
        str(p.get("text") or "")
        for e, p in events
        if e in (P.PANEL_EVENT_DELTA, P.PANEL_EVENT_ERROR, P.PANEL_EVENT_TOOL)
    )


def test_a_failure_in_the_sidebar_never_names_a_retry_button(tmp_path, monkeypatch):
    """A provider failure nothing recognises: the chat's fix is "Press Retry.
    If it happens again, pick another model for this chat." The sidebar has no
    Retry button. MUTATION: the panel does not arm the sidebar's surface ->
    "Press Retry" again."""
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()

        async def fake_stream(*args: Any, **kw: Any):
            raise RuntimeError("the socket closed under us")
            yield  # pragma: no cover — a generator

        app.platform.router.stream = fake_stream
        app.panel.send(P.PANEL_ACTION_SEND, text="what is on this page")
        app.panel.wait_for(P.PANEL_EVENT_ERROR, "the turn never failed")
        events = app.panel.events()

    words = _said(events)
    assert "Retry" not in words, words
    assert "Send your message again" in words, words


def test_an_empty_answer_in_the_sidebar_never_names_a_retry_button(tmp_path, monkeypatch):
    """The model answered nothing and ran no tool: the lane's reply says so —
    and on the chat page tells the user to "Press Retry". Since A5 that reply
    is shown in the sidebar. MUTATION: drop the sidebar wording in
    ``_no_text_reply`` -> "Press Retry" in the sidebar's answer."""
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()

        async def fake_stream(*args: Any, **kw: Any):
            yield {"type": "final", "response": LLMResponse(text="", usage={}),
                   "provider": "mock", "model": "mock"}

        app.platform.router.stream = fake_stream
        app.panel.send(P.PANEL_ACTION_SEND, text="what is on this page")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        events = app.panel.events()

    words = _said(events)
    assert "empty answer" in words, words
    assert "Retry" not in words, words
    assert "Send your message again" in words, words


def test_a_declined_conversation_in_the_sidebar_names_its_own_way_out(tmp_path, monkeypatch):
    """A safety refusal of the whole conversation: on the chat page the way
    out is "Start a new chat, or edit an earlier message"; the sidebar's
    messages have no Edit, its header has New conversation. MUTATION: drop the
    sidebar's REFUSAL_WAY_OUT entry -> the chat's words in the sidebar."""
    from iron_jarvis.providers.adapters.base import ProviderRefusal

    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()

        async def fake_stream(*args: Any, **kw: Any):
            raise ProviderRefusal(
                "declined", provider="anthropic", whole_conversation=True,
            )
            yield  # pragma: no cover — a generator

        app.platform.router.stream = fake_stream
        app.panel.send(P.PANEL_ACTION_SEND, text="what is on this page")
        app.panel.wait_for(P.PANEL_EVENT_ERROR, "the turn never failed")
        events = app.panel.events()

    words = _said(events)
    assert "edit an earlier message" not in words, words
    assert "New conversation" in words, words


def test_outside_the_sidebar_the_chats_words_are_unchanged():
    """The sidebar's words are its own; the chat page's stay byte for byte."""
    from iron_jarvis.daemon.chat_turn import _no_text_reply
    from iron_jarvis.providers import failure_words as fw

    assert fw.current_surface() == fw.SURFACE_CHAT
    assert fw.press_retry() == "Press Retry"
    words = fw.friendly_error(RuntimeError("boom"))
    assert words["fix"] == fw.GENERIC_FIX == (
        "Press Retry. If it happens again, pick another model for this chat."
    )
    assert _no_text_reply([], "").endswith("Press Retry, or pick a different model.")
    token = fw.set_sidebar_surface()
    try:
        assert fw.current_surface() == fw.SURFACE_SIDEBAR
        assert fw.friendly_error(RuntimeError("boom"))["fix"] == fw.GENERIC_FIX_SIDEBAR
        assert _no_text_reply([], "").endswith("Send your message again, or pick a different model.")
    finally:
        fw.reset_surface(token)
    assert fw.current_surface() == fw.SURFACE_CHAT


# --------------------------------------------------------------------------- #
# A panel REOPENED mid-turn
# --------------------------------------------------------------------------- #
def _reopened_panel_bubbles(events: list[tuple[str, dict]]) -> list[str]:
    """The answer bubbles a panel shows when it was opened (empty) mid-turn:
    the sidepanel's own rules (``paintHistory`` paints the live answer as the
    bubble being written; a delta extends it; ``reset`` removes it; a step,
    the end and an error end it)."""
    bubbles: list[str | None] = []
    streaming: int | None = None
    painted = False
    for event, payload in events:
        text = str(payload.get("text") or "")
        if event == P.PANEL_EVENT_HISTORY and not painted:
            painted = True
            live = payload.get("live") or {}
            answer = str(live.get("answer") or "")
            if answer:
                bubbles.append(answer)
                streaming = len(bubbles) - 1
            continue
        if not painted:
            continue
        if event == P.PANEL_EVENT_DELTA:
            if streaming is None:
                bubbles.append("")
                streaming = len(bubbles) - 1
            bubbles[streaming] = (bubbles[streaming] or "") + text
        elif event == P.PANEL_EVENT_RESET:
            if streaming is not None:
                bubbles[streaming] = None
            streaming = None
        elif event in (P.PANEL_EVENT_TOOL, P.PANEL_EVENT_DONE, P.PANEL_EVENT_ERROR):
            streaming = None
        elif event == P.PANEL_EVENT_STATE and payload.get("running") is not True:
            streaming = None
    return [b for b in bubbles if b is not None]


def test_a_panel_reopened_mid_turn_shows_the_reply_on_its_own_not_glued_on(tmp_path):
    """The user closed the sidebar after the model said a few words and
    started a step, and reopened it: the live answer ("Let me read the
    page.") is painted as the bubble being written. The model then wrote
    nothing after its step, so the lane's reply rides only in `done`. It must
    not be glued onto the live bubble ("…page.The model finished…"): the
    daemon's idea of that bubble follows what the history frame painted.
    MUTATION: drop the history branch in `emit` -> one glued bubble."""
    import asyncio
    from types import SimpleNamespace

    from iron_jarvis.browser.panel import PanelTurns
    from iron_jarvis.daemon import chat_turn

    sent: list[tuple[str, dict]] = []

    class Conn:
        async def send(self, frame: dict) -> None:
            sent.append((str(frame.get("event") or ""), dict(frame.get("payload") or {})))

    turns = PanelTurns(SimpleNamespace(browser=None))
    turns._turn_text, turns._remembered, turns._answer = "q?", False, []
    conn = Conn()
    reply = chat_turn._no_text_reply(["browser_read_page"], "Page title: Taxes 2026")

    async def go() -> None:
        await turns._translate(conn, "token", {"text": "Let me read the page."})
        await turns._translate(conn, "tool_call", {"name": "browser_read_page", "status": "started"})
        await turns._translate(
            conn, "tool_call", {"name": "browser_read_page", "status": "finished", "ok": True}
        )
        # The panel is reopened (an `open`): the history frame, live.
        turns._task = SimpleNamespace(done=lambda: False)
        await turns.emit(conn, P.PANEL_EVENT_HISTORY, turns.history_view())
        turns._task = None
        await turns._translate(conn, "done", {"reply": reply, "route": {"reason": "explicit"}})

    asyncio.run(go())
    bubbles = _reopened_panel_bubbles(sent)
    assert bubbles, sent
    assert bubbles[-1] == reply, bubbles
    assert not any(b != reply and b.endswith(reply) for b in bubbles), bubbles


def test_a_panel_that_stayed_open_through_a_tab_switch_still_reads_right(tmp_path):
    """The control for the case above: the same `open` arrives on every tab
    switch, to a panel that never closed (its transcript is not empty, so it
    paints nothing from the history frame). Its bubbles must still end as
    the turn's words: the first round's line, then the reply — once."""
    import asyncio
    from types import SimpleNamespace

    from iron_jarvis.browser.panel import PanelTurns

    sent: list[tuple[str, dict]] = []

    class Conn:
        async def send(self, frame: dict) -> None:
            sent.append((str(frame.get("event") or ""), dict(frame.get("payload") or {})))

    turns = PanelTurns(SimpleNamespace(browser=None))
    turns._turn_text, turns._remembered, turns._answer = "q?", False, []
    conn = Conn()
    reply = "Done, the form is filled.\n\n_Note: x._"

    async def go() -> None:
        await turns._translate(conn, "token", {"text": "Let me look."})
        await turns._translate(conn, "tool_call", {"name": "browser_read_page", "status": "started"})
        turns._task = SimpleNamespace(done=lambda: False)
        await turns.emit(conn, P.PANEL_EVENT_HISTORY, turns.history_view())
        turns._task = None
        await turns._translate(conn, "token", {"text": "Done, the form is filled."})
        await turns._translate(conn, "done", {"reply": reply, "route": {"reason": "explicit"}})

    asyncio.run(go())
    # An open panel: the history frame paints nothing; everything else applies.
    bubbles: list[str | None] = []
    streaming: int | None = None
    for event, payload in sent:
        if event == P.PANEL_EVENT_DELTA:
            if streaming is None:
                bubbles.append("")
                streaming = len(bubbles) - 1
            bubbles[streaming] = (bubbles[streaming] or "") + str(payload.get("text") or "")
        elif event == P.PANEL_EVENT_RESET:
            if streaming is not None:
                bubbles[streaming] = None
            streaming = None
        elif event in (P.PANEL_EVENT_TOOL, P.PANEL_EVENT_DONE):
            streaming = None
    assert [b for b in bubbles if b is not None] == ["Let me look.", reply], bubbles
    assert turns._history[-1] == {"role": "assistant", "content": reply}


def test_a_file_the_sidebar_cannot_make_is_never_sent_to_a_tools_menu(tmp_path, monkeypatch):
    """The user asked the sidebar for a document; the model wrote none and
    claimed it did. The lane's two honesty notes (both shown in the sidebar
    since A5) tell the user to turn tools on "in the chat's Tools menu" — the
    sidebar has no Tools menu, and its turn can never arm a document tool
    (its ceiling is the browser tools). MUTATION: drop the sidebar wording in
    either note -> "Tools menu" in the sidebar."""
    with RealApp(tmp_path, monkeypatch, access="read_only") as app:
        app.pair()

        async def fake_stream(*args: Any, **kw: Any):
            text = "Done. I saved it as `summary.docx`."
            yield {"type": "text", "text": text}
            yield {"type": "final", "response": LLMResponse(text=text, usage={}),
                   "provider": "mock", "model": "mock"}

        app.platform.router.stream = fake_stream
        app.panel.send(P.PANEL_ACTION_SEND, text="create a document summarizing this page")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        events = app.panel.events()

    words = _said(events)
    assert "summary.docx" in words and "does not exist" in words, words
    assert "no file was" in words, words
    assert "Tools menu" not in words, words
    assert words.count("can't write documents") == 2, words
