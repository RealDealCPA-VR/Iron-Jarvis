"""Second review, group 16 part D: the browser tools stay off the event loop
and say what went wrong in plain words (v1.516.0; findings B13, B12).

B13. ``web_action`` (the isolated browser's click and type tool) did its
     approval-queue work (find a standing approval, spend it, file a request,
     deny, approve) as plain SQLite calls ON the event loop, so a busy
     database froze the whole app for as long as the write waited. Every one
     of those calls now runs on a worker thread, bounded by the database's own
     busy timeout (the browser add-on's door, ``browser/tools``, already did).
     A cancel while the card waits still denies the row, from a task of its
     own, off the loop.

B12. An exception's own text reached the screen: the browser sidebar's
     failure line ("Iron Jarvis could not finish that: <exception>") and three
     of the isolated browser's tool errors ("action failed: …", "extract
     failed: …", "vision call failed: …"). Each is now a plain sentence
     (``core.failures.plain_failure``): a sentence the app wrote itself (an
     element that is not on the page) is kept, anything else is logged and
     replaced by the plain line.

The approval card's hidden typed text (v1.515.0 stand-in) is worded by the
Browser page itself: ``dashboard/__tests__/browser-hidden-typed-v1516.test.tsx``.

Offline: the module's FakeBrowser, a temp database, no daemon.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import select

import iron_jarvis.computeruse.models  # noqa: F401  (register tables before init_db)
from iron_jarvis.computeruse import (
    ApprovalQueue,
    ComputerUsePolicy,
    CUContext,
    FakeBrowser,
    computeruse_tools,
)
from iron_jarvis.computeruse import tools as cu_tools
from iron_jarvis.computeruse.base import ComputerUseError
from iron_jarvis.computeruse.models import ApprovalRequest
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.tools.base import ToolContext

TYPED = "test-pass-value"

LOGIN = {
    "https://example.com/login": {
        "text": "Sign in to your account",
        "a11y": [
            {"role": "heading", "name": "Sign in"},
            {"role": "button", "name": "Delete account"},
        ],
        "fields": [{"selector": "#password", "type": "password", "name": "Password"}],
    }
}


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "cu.db"))
    init_db(e)
    return e


class _ThreadRecordingQueue(ApprovalQueue):
    """The real queue, noting which thread ran each database call."""

    def __init__(self, engine) -> None:
        super().__init__(engine)
        self.calls: list[tuple[str, int]] = []

    def _note(self, name: str) -> None:
        self.calls.append((name, threading.get_ident()))

    def create_request(self, *a, **kw):
        self._note("create_request")
        return super().create_request(*a, **kw)

    def approve(self, request_id):
        self._note("approve")
        return super().approve(request_id)

    def deny(self, request_id):
        self._note("deny")
        return super().deny(request_id)

    def consume(self, request_id):
        self._note("consume")
        return super().consume(request_id)

    def approved_unconsumed(self, *a, **kw):
        self._note("approved_unconsumed")
        return super().approved_unconsumed(*a, **kw)


def _policy(**kw: Any) -> ComputerUsePolicy:
    base: dict[str, Any] = dict(
        enabled=True,
        domain_allowlist=["example.com"],
        action_allowlist=["navigate", "read", "extract", "screenshot", "wait", "type", "click"],
    )
    base.update(kw)
    return ComputerUsePolicy(**base)


def _ctx(engine, tmp_path, run: str = "r") -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id=run,
        config=None, event_bus=None, engine=engine,
    )


def _rows(engine) -> list[ApprovalRequest]:
    with session_scope(engine) as db:
        return list(db.exec(select(ApprovalRequest)))


def _on_loop(queue: _ThreadRecordingQueue, loop_thread: int) -> list[str]:
    return [name for name, ident in queue.calls if ident == loop_thread]


# --------------------------------------------------------------------------- #
# B13: the approval queue's database work is off the event loop
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("granted", [False, True])
async def test_b13_a_card_files_and_answers_its_row_off_the_loop(engine, tmp_path, granted):
    queue = _ThreadRecordingQueue(engine)

    def resolver(req, ctx, *, name, args):
        return granted

    cu = CUContext(policy=_policy(), browser=FakeBrowser(LOGIN), approvals=queue,
                   approval_resolver=resolver)
    tools = {t.name: t for t in computeruse_tools(cu)}
    await cu.browser.navigate("https://example.com/login")
    loop_thread = threading.get_ident()
    res = await tools["web_action"].execute(
        {"kind": "type", "css": "#password", "value": TYPED}, _ctx(engine, tmp_path)
    )
    assert res.ok is granted, res.error
    names = [n for n, _ in queue.calls]
    expected = (
        ["approved_unconsumed", "create_request", "approve", "consume"]
        if granted
        else ["approved_unconsumed", "create_request", "deny"]
    )
    assert names == expected
    assert _on_loop(queue, loop_thread) == [], (
        f"database work ran on the event loop: {_on_loop(queue, loop_thread)}"
    )
    assert [r.status for r in _rows(engine)] == ["consumed" if granted else "denied"]


async def test_b13_a_standing_approval_is_found_and_spent_off_the_loop(engine, tmp_path):
    queue = _ThreadRecordingQueue(engine)
    cu = CUContext(policy=_policy(), browser=FakeBrowser(LOGIN), approvals=queue,
                   approval_resolver=None)
    tools = {t.name: t for t in computeruse_tools(cu)}
    ctx = _ctx(engine, tmp_path)
    await cu.browser.navigate("https://example.com/login")
    first = await tools["web_action"].execute({"kind": "click", "role": "button", "name": "Delete account"}, ctx)
    assert first.ok is False
    row = _rows(engine)[0]
    with session_scope(engine) as db:  # a person answers it on the Browser page
        db.add(ApprovalRequest(run_id="r", action_json=row.action_json,
                               reason=row.reason, status="approved"))
        db.commit()
    queue.calls.clear()
    loop_thread = threading.get_ident()
    again = await tools["web_action"].execute({"kind": "click", "role": "button", "name": "Delete account"}, ctx)
    assert again.ok is True, again.error
    assert [n for n, _ in queue.calls] == ["approved_unconsumed", "consume"]
    assert _on_loop(queue, loop_thread) == []
    assert cu.browser.clicks, "the approved click ran"


async def test_b13_a_cancel_while_the_card_waits_still_denies_the_row_off_the_loop(engine, tmp_path):
    queue = _ThreadRecordingQueue(engine)

    async def resolver(req, ctx, *, name, args):
        raise asyncio.CancelledError()

    cu = CUContext(policy=_policy(), browser=FakeBrowser(LOGIN), approvals=queue,
                   approval_resolver=resolver)
    tools = {t.name: t for t in computeruse_tools(cu)}
    await cu.browser.navigate("https://example.com/login")
    loop_thread = threading.get_ident()
    with pytest.raises(asyncio.CancelledError):
        await tools["web_action"].execute(
            {"kind": "type", "css": "#password", "value": TYPED}, _ctx(engine, tmp_path)
        )
    # The denial runs from a task of its own; wait for exactly that.
    pending = list(cu_tools._DETACHED_DENIALS)
    assert pending, "the denial was not handed to a task of its own"
    await asyncio.gather(*pending)
    assert [r.status for r in _rows(engine)] == ["denied"]
    assert "deny" in [n for n, _ in queue.calls]
    assert _on_loop(queue, loop_thread) == []
    assert cu.browser.typed == []


# --------------------------------------------------------------------------- #
# B12: plain words instead of an exception's text
# --------------------------------------------------------------------------- #

ENGINE_TEXT = "Locator.click: Timeout 30000ms exceeded.\n=== logs ===\nwaiting for locator('#go')"


class _LibraryError(Exception):
    """Stands in for a browser library's own error (not this package's words)."""


class _FailingBrowser(FakeBrowser):
    async def click(self, selector, *, fallback=False):
        raise _LibraryError(ENGINE_TEXT)

    async def type(self, selector, value):
        raise _LibraryError(ENGINE_TEXT)

    async def extract(self, selector):
        raise _LibraryError(ENGINE_TEXT)


def _engine_free(text: str) -> None:
    for word in ("Locator", "Timeout 30000ms", "=== logs", "_LibraryError", "Error:", "\n"):
        assert word not in text, f"{word!r} reached the words a person reads: {text!r}"


async def test_b12_a_failed_click_or_typing_says_so_in_plain_words(engine, tmp_path):
    cu = CUContext(policy=_policy(), browser=_FailingBrowser(LOGIN), approvals=ApprovalQueue(engine))
    tools = {t.name: t for t in computeruse_tools(cu)}
    ctx = _ctx(engine, tmp_path)
    await cu.browser.navigate("https://example.com/login")
    click = await tools["web_action"].execute({"kind": "click", "role": "heading", "name": "Sign in"}, ctx)
    assert click.ok is False
    assert click.error == "That click could not be made on the page."
    typed = await tools["web_action"].execute({"kind": "type", "css": "#search", "value": "a report"}, ctx)
    assert typed.ok is False
    assert typed.error == "That text could not be typed on the page."
    _engine_free(click.error + typed.error)


async def test_b12_a_failed_read_of_one_element_says_so_in_plain_words(engine, tmp_path):
    cu = CUContext(policy=_policy(), browser=_FailingBrowser(LOGIN), approvals=ApprovalQueue(engine))
    tools = {t.name: t for t in computeruse_tools(cu)}
    await cu.browser.navigate("https://example.com/login")
    res = await tools["web_extract"].execute({"role": "heading", "name": "Sign in"}, _ctx(engine, tmp_path))
    assert res.ok is False
    assert res.error == "That part of the page could not be read."
    _engine_free(res.error)


async def test_b12_a_wait_that_ran_out_is_said_by_its_kind_never_its_text(engine, tmp_path):
    """Lead's review: the one fact the model can act on survives the plain
    line, read from the error's KIND: a page that did not answer in time is
    not "the element is wrong". The library's own text still never shows."""

    class TimeoutError_(Exception):  # a library's own class, named like Playwright's
        pass

    TimeoutError_.__name__ = "TimeoutError"

    class _Slow(FakeBrowser):
        async def click(self, selector, *, fallback=False):
            raise TimeoutError_(ENGINE_TEXT)

        async def extract(self, selector):
            raise TimeoutError("timed out")  # the built-in kind

    cu = CUContext(policy=_policy(), browser=_Slow(LOGIN), approvals=ApprovalQueue(engine))
    tools = {t.name: t for t in computeruse_tools(cu)}
    ctx = _ctx(engine, tmp_path)
    await cu.browser.navigate("https://example.com/login")
    click = await tools["web_action"].execute({"kind": "click", "role": "heading", "name": "Sign in"}, ctx)
    assert click.error == "That click could not be made on the page. The page did not answer in time."
    read = await tools["web_extract"].execute({"role": "heading", "name": "Sign in"}, ctx)
    assert read.error == "That part of the page could not be read. The page did not answer in time."
    _engine_free(click.error + read.error)


async def test_b12_the_apps_own_sentence_is_kept_behind_the_plain_line(engine, tmp_path):
    """An element that is not on the page is the app's own words (FakeBrowser's
    ``UnknownSelector``, as the real browser raises): the model still learns
    which element was missing."""
    cu = CUContext(policy=_policy(), browser=FakeBrowser(LOGIN), approvals=ApprovalQueue(engine))
    tools = {t.name: t for t in computeruse_tools(cu)}
    await cu.browser.navigate("https://example.com/login")
    res = await tools["web_action"].execute(
        {"kind": "click", "role": "button", "name": "Not there"}, _ctx(engine, tmp_path)
    )
    assert res.ok is False
    assert res.error.startswith("That click could not be made on the page (no element for ")
    assert res.error.endswith(").")


async def test_b12_a_failed_look_says_so_in_plain_words(engine, tmp_path):
    async def complete(**kw):
        raise _LibraryError("HTTP 500 {'error': {'type': 'overloaded_error'}}")

    cu = CUContext(policy=_policy(), browser=FakeBrowser(LOGIN), approvals=ApprovalQueue(engine),
                   router_resolver=lambda: SimpleNamespace(complete=complete))
    tools = {t.name: t for t in computeruse_tools(cu)}
    await cu.browser.navigate("https://example.com/login")
    res = await tools["web_look"].execute({}, _ctx(engine, tmp_path))
    assert res.ok is False
    assert res.error == "The model could not look at the page. Try again, or pick a model that can read pictures."
    for word in ("HTTP 500", "overloaded_error", "{'error'"):
        assert word not in res.error


def _run_failing_panel_turn(exc: BaseException) -> list[dict]:
    from iron_jarvis.browser import protocol as P
    from iron_jarvis.browser.panel import PanelTurns
    import iron_jarvis.daemon.chat_stream as chat_stream

    turns = PanelTurns(SimpleNamespace(browser=None))
    frames: list[dict] = []

    class _Conn:
        async def send(self, frame: dict) -> None:
            frames.append(frame)

    async def failing_lane(*a, **kw):
        async def gen():
            yield "event: token\ndata: {\"text\": \"half\"}\n\n"
            raise exc

        return gen()

    original = chat_stream.stream_chat_turn
    chat_stream.stream_chat_turn = failing_lane
    try:
        async def drive():
            conn = _Conn()
            turns._conn = conn
            turns._turn_text, turns._remembered, turns._answer = "q?", False, []
            await turns._run(conn, "q?", "panel_test")

        asyncio.run(drive())
    finally:
        chat_stream.stream_chat_turn = original
    return [f for f in frames if f.get("event") == P.PANEL_EVENT_ERROR]


def _frame_text(frame: dict) -> str:
    payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else frame
    return str(payload.get("text") or "")


def test_b12_the_sidebar_failure_line_is_plain_words():
    errors = _run_failing_panel_turn(KeyError("choices"))
    assert len(errors) == 1, errors
    text = _frame_text(errors[0])
    assert text == "Iron Jarvis could not finish that.", errors[0]
    assert "choices" not in json.dumps(errors[0])
    assert "KeyError" not in json.dumps(errors[0])


def test_b12_the_sidebar_keeps_a_sentence_the_app_wrote():
    errors = _run_failing_panel_turn(ComputerUseError("The agent browser is turned off."))
    assert len(errors) == 1, errors
    assert _frame_text(errors[0]) == "Iron Jarvis could not finish that: The agent browser is turned off."
