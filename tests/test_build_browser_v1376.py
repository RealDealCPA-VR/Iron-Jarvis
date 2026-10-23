"""v1.376.0 (IJ harness part 2, Phase 5) — the AGENT'S BROWSER.

The user's decision 5 (final): an invisible browser the agent drives — the
installed Edge, a throwaway profile — with its latest view in the Browser
pane. These pins hold ``harness/page_browser`` (one browser per session, its
life, its processes, the address rules, the thread) and ``harness/page_act``
(the four tools), with a FAKE browser for the fast pins and the real Edge
(``skipif`` when Edge or Playwright is missing) against a local
``http.server`` — never the network.

* LIFE: opened on first use, one per session (a helper uses its session's),
  closed on archive, delete, idle, daemon shutdown and Stop — and no driver or
  Edge process outlives any of them, a daemon KILLED included (the job).
* THE ADDRESS RULES on every navigation: the Browser pane's dev server and an
  address the user allowed open without asking, another local address asks,
  a public site is refused; a page that navigates or redirects itself
  somewhere the rules would not open is stopped there.
* Local only cards every open / click / typed entry; low trust refuses all.
* Typing into a password field, a payment field or a file picker, or typing
  text that reads as a key, is refused in words.
* ``page_read`` returns the console errors and failed requests since the last
  read — bounded, masked, then cleared.
* Each action is bounded, runs OFF the daemon's loop, and one at a time per
  session.
"""

from __future__ import annotations

import asyncio
import base64
import http.server
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.core import procs
from iron_jarvis.core.turns import TURNS
from iron_jarvis.harness import page_act as PA
from iron_jarvis.harness import page_browser as PB
from iron_jarvis.harness import page_shot as PS
from iron_jarvis.harness import preview as PV

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

DEV = "http://localhost:5173/"
CART = "http://localhost:5173/cart"
OTHER = "http://localhost:8020/admin"

#: The fake browser's world: address -> page.
PAGES: dict[str, dict[str, Any]] = {
    DEV: {
        "title": "Shop",
        "text": "Shop\nAdd to cart",
        "console": ["Uncaught TypeError: cart is undefined (localhost:5173, line 12)"],
        "failed": ["GET localhost:5173/api/cart → 500 Internal Server Error"],
        "buttons": {
            "Add to cart": ("goto", CART),
            "Admin": ("goto", OTHER),
            "Docs": ("goto", "https://example.com/docs"),
            "Log": ("console", "clicked log"),
        },
        "fields": {
            "Email": {"tag": "input", "type": "email", "autocomplete": "", "words": "email"},
            "Password": {"tag": "input", "type": "password", "autocomplete": "", "words": "password"},
            "Card": {"tag": "input", "type": "text", "autocomplete": "cc-number", "words": "card"},
            "Upload": {"tag": "input", "type": "file", "autocomplete": "", "words": "upload"},
            "Notes": {"tag": "textarea", "type": "", "autocomplete": "", "words": "notes"},
        },
    },
    CART: {"title": "Cart", "text": "Your cart: 1 item", "buttons": {}},
    OTHER: {"title": "Admin", "text": "Admin", "buttons": {}},
}


class FakeBackend:
    """Stands in for EdgeBackend: the same methods, an in-memory world, and a
    record of what the manager asked of it."""

    def __init__(self, *, edge, work, guard, note, background=False, extra_args=()):
        self.guard, self.note, self.work = guard, note, Path(work)
        self.started = self.closed = self.killed = False
        self._url = "about:blank"
        self.clicks: list[str] = []
        self.typed: list[tuple[Any, str, bool]] = []
        self.active = 0
        self.max_active = 0
        self.threads: list[str] = []
        self.hang: asyncio.Event | None = None
        self.block_s = 0.0
        self.delay_s = 0.0
        self.cancelled = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    def kill(self):
        self.killed = True
        return [424242]

    def pids(self):
        return [] if self.killed else [424242]

    def url(self):
        return self._url

    async def title(self):
        return PAGES.get(self._url, {}).get("title", "")

    async def quiet(self):
        return None

    async def settle(self):
        return None

    async def goto(self, url, timeout_s):
        if url not in PAGES:
            raise PB.BrowserProblem(f"Opening {PB.where(url)} failed: nothing answered there (ERR_CONNECTION_REFUSED).")
        self._url = url
        for line in PAGES[url].get("console", []):
            self.note("console", line)
        for line in PAGES[url].get("failed", []):
            self.note("failed", line)

    async def click(self, target, timeout_s):
        self.threads.append(threading.current_thread().name)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.block_s:
                time.sleep(self.block_s)  # BLOCKING on purpose: it must not be the daemon's loop
            if self.hang is not None:
                await self.hang.wait()
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            name = target if isinstance(target, str) else (target.get("name") or target.get("css"))
            action = PAGES.get(self._url, {}).get("buttons", {}).get(name)
            if action is None:
                raise PB.BrowserProblem(f"Nothing on the page matches {name!r}.")
            self.clicks.append(name)
            kind, arg = action
            if kind == "goto":
                verdict = self.guard(arg)
                if verdict.kind != "ok":
                    # The real browser: the route stops it, an error page shows.
                    self.note("blocked", f"{verdict.kind}|{arg}")
                    self._url = "chrome-error://chromewebdata/"
                else:
                    self._url = arg
            elif kind == "console":
                self.note("console", arg)
            return name
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.active -= 1

    async def field(self, target):
        facts = PAGES.get(self._url, {}).get("fields", {}).get(target)
        if facts is None:
            raise PB.BrowserProblem(f"Nothing on the page matches {target!r}.")
        return target, facts

    async def fill(self, loc, text, submit, timeout_s):
        self.typed.append((loc, text, submit))

    async def text(self):
        return PAGES.get(self._url, {}).get("text", "")

    async def elements(self):
        return [{"role": "button", "name": n} for n in PAGES.get(self._url, {}).get("buttons", {})]

    async def screenshot(self, *, jpeg):
        return b"\xff\xd8 fake jpeg" if jpeg else PNG_1X1


@pytest.fixture()
def fake(monkeypatch):
    """A fresh AgentBrowsers on the FAKE browser, put where the tools, the
    routes and the end hooks look (``page_browser.BROWSERS``)."""
    made: list[FakeBackend] = []

    def factory(**kw):
        backend = FakeBackend(**kw)
        made.append(backend)
        return backend

    inst = PB.AgentBrowsers(backend_factory=factory)
    inst.made = made  # type: ignore[attr-defined]
    monkeypatch.setattr(PB, "BROWSERS", inst)
    yield inst
    inst.shutdown()


def ctx(tmp_path: Path, session: str = "build:s1", turn: str = "") -> Any:
    return SimpleNamespace(
        session_id=session, turn_id=turn, workspace=tmp_path,
        config=SimpleNamespace(home=tmp_path / "home"),
    )


def preview(url: str):
    return PV.set_preview(url)


# --------------------------------------------------------------------------- #
# the address rules (pure)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url, allowed, shown, kind",
    [
        ("http://localhost:5173/cart", set(), "http://localhost:5173", "ok"),       # the pane's dev server
        ("http://127.0.0.1:5173/x", set(), "http://localhost:5173", "ok"),          # same server
        ("http://localhost:8020/", {"http://localhost:8020"}, "", "ok"),            # allowed earlier
        ("http://localhost:8020/", set(), "http://localhost:5173", "ask"),          # another local
        ("http://192.168.1.20:3000/", set(), "", "ask"),
        ("https://example.com/", {"https://example.com:443"}, "", "refuse"),        # public: never
        ("http://evil%2ecom/", set(), "", "refuse"),
        ("file:///C:/x.txt", set(), "", "refuse"),
        ("about:blank", set(), "", "ok"),
    ],
)
def test_the_address_rules(url, allowed, shown, kind):
    assert PB.judge(url, allowed, shown).kind == kind


def test_where_names_host_and_path_never_the_query():
    assert PB.where("http://localhost:5173/cart?token=abc#x") == "localhost:5173/cart"
    assert PB.where("http://localhost:5173/") == "localhost:5173"
    assert PB.where("chrome-error://chromewebdata/") == ""
    assert PB.normalize("localhost:5173/x") == "http://localhost:5173/x"


# --------------------------------------------------------------------------- #
# the tools: page_open's card, public refused, low trust, Local only
# --------------------------------------------------------------------------- #


async def test_page_open_asks_only_for_a_local_page_that_is_not_the_panes(tmp_path, fake):
    tool = PA.PageOpenTool()
    c = ctx(tmp_path)
    token = preview(DEV)
    try:
        same = await tool.spend_check({"url": "http://localhost:5173/cart"}, c)
        assert same is not None and same.ask is False
        other = await tool.spend_check({"url": OTHER}, c)
        assert other is not None and other.ask is True
        assert "localhost:8020/admin" in other.words and "pane" not in other.words
        assert await tool.spend_check({"url": "https://example.com"}, c) is None
        # Allowed once, the address stays allowed for this browser: no card.
        res = await tool.execute({"url": OTHER}, c)
        assert res.ok, res.error
        again = await tool.spend_check({"url": "http://localhost:8020/other"}, c)
        assert again is not None and again.ask is False
    finally:
        PV.reset_preview(token)


async def test_a_public_site_is_refused_in_words_and_no_browser_starts(tmp_path, fake):
    res = await PA.PageOpenTool().execute({"url": "https://example.com/"}, ctx(tmp_path))
    assert res.ok is False and "does not open public websites" in res.error
    assert fake.made == [] and fake.open_keys() == []


async def test_click_type_read_with_no_page_open_say_so(tmp_path, fake):
    for tool, args in ((PA.PageClickTool(), {"target": "x"}), (PA.PageTypeTool(), {"target": "x", "text": "y"}),
                       (PA.PageReadTool(), {})):
        res = await tool.execute(args, ctx(tmp_path))
        assert res.ok is False and res.error == PB.NO_PAGE
    assert fake.made == []  # nothing was started for them


def test_low_trust_refuses_every_page_tool(tmp_path):
    from iron_jarvis.core.trust import LOW_TRUST_DENY
    from iron_jarvis.harness import dialects as D
    from iron_jarvis.harness import mode as M
    from iron_jarvis.platform import build_platform

    assert {"page_open", "page_click", "page_type", "page_read"} <= LOW_TRUST_DENY
    platform = build_platform(str(tmp_path / "home"))
    full = M.plan_tools(platform.registry, platform.permissions, D.CLAUDE, family="claude", stream=True)
    assert {"page_open", "page_click", "page_type", "page_read"} <= full.gate
    low = M.plan_tools(platform.registry, platform.permissions, D.CLAUDE, family="claude", stream=True,
                       low_trust=True)
    assert not ({"page_open", "page_click", "page_type", "page_read"} & low.gate)


def test_local_only_cards_every_open_click_and_typed_entry(tmp_path):
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers import egress

    platform = build_platform(str(tmp_path / "home"))
    with egress.local_only_scope(True):
        for name in ("page_open", "page_click", "page_type"):
            assert egress.asks_first(platform.registry, name), name
        assert not egress.asks_first(platform.registry, "page_read")  # reads what is loaded
        token = PV.set_preview(DEV)
        try:
            line = PV.prompt_line({"page_screenshot", "page_open"})
        finally:
            PV.reset_preview(token)
    assert "page_open" in line and "asks the user first" in line and "without asking" not in line
    assert not egress.asks_first(platform.registry, "page_open")  # scope off


def test_the_permission_tiers(tmp_path):
    from iron_jarvis.core.config import default_permissions

    perms = default_permissions()
    assert perms["page_open"] == "ask"  # its spend check lifts the pane's page
    assert perms["page_click"] == perms["page_type"] == perms["page_read"] == "allow"


# --------------------------------------------------------------------------- #
# typing: never a secret
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "facts, text, want",
    [
        ({"type": "password"}, "x", PB.TYPE_REFUSED_PASSWORD),
        ({"type": "text", "autocomplete": "current-password"}, "x", PB.TYPE_REFUSED_PASSWORD),
        ({"type": "text", "words": "confirm password"}, "x", PB.TYPE_REFUSED_PASSWORD),
        ({"type": "text", "autocomplete": "cc-number"}, "4111", PB.TYPE_REFUSED_PAYMENT),
        ({"type": "text", "words": "card number"}, "4111", PB.TYPE_REFUSED_PAYMENT),
        ({"type": "text", "words": "cvc"}, "123", PB.TYPE_REFUSED_PAYMENT),
        ({"type": "file"}, "C:/x", PB.TYPE_REFUSED_FILE),
        ({"type": "text", "words": "notes"}, "sk-ant-api03-" + "a" * 40, PB.TYPE_REFUSED_SECRET),
        ({"type": "email", "words": "email"}, "a@b.test", ""),
        ({"type": "text", "words": "search"}, "red shoes", ""),
    ],
)
def test_typing_never_reaches_a_password_payment_or_file_field(facts, text, want):
    assert PB.typing_refusal(facts, text) == want


async def test_page_type_refuses_and_types_nothing(tmp_path, fake):
    c = ctx(tmp_path)
    token = preview(DEV)
    try:
        assert (await PA.PageOpenTool().execute({"url": DEV}, c)).ok
        for target in ("Password", "Card", "Upload"):
            res = await PA.PageTypeTool().execute({"target": target, "text": "hunter2"}, c)
            assert res.ok is False, target
        res = await PA.PageTypeTool().execute({"target": "Email", "text": "a@b.test", "submit": True}, c)
        assert res.ok, res.error
    finally:
        PV.reset_preview(token)
    assert fake.made[0].typed == [("Email", "a@b.test", True)]


def test_a_typed_secret_is_masked_in_the_ledger():
    safe = PA.PageTypeTool().redact_args({"target": "Notes", "text": "token=abcdef1234567890secret"})
    assert "abcdef1234567890secret" not in safe["text"]


# --------------------------------------------------------------------------- #
# life: one per session, closed on archive / delete / idle / shutdown / Stop
# --------------------------------------------------------------------------- #


async def test_one_browser_per_session_opened_on_first_use(tmp_path, fake):
    a, b = ctx(tmp_path, "build:a"), ctx(tmp_path, "build:b")
    token = preview(DEV)
    try:
        assert (await PA.PageOpenTool().execute({"url": DEV}, a)).ok
        assert (await PA.PageClickTool().execute({"target": "Log"}, a)).ok
        assert (await PA.PageOpenTool().execute({"url": CART}, a)).ok
        assert len(fake.made) == 1 and fake.made[0].started
        assert (await PA.PageOpenTool().execute({"url": DEV}, b)).ok
    finally:
        PV.reset_preview(token)
    assert len(fake.made) == 2 and sorted(fake.open_keys()) == ["build:a", "build:b"]


async def test_a_helper_uses_its_sessions_browser(tmp_path, fake):
    from iron_jarvis.harness import helpers as H

    parent = H.HelperParent(session_id="build:s1", folder="", provider="anthropic", model="m")
    token = H.HELPER_PARENT.set(parent)
    ptoken = preview(DEV)
    try:
        assert (await PA.PageOpenTool().execute({"url": DEV}, ctx(tmp_path, "helper-session-9"))).ok
    finally:
        PV.reset_preview(ptoken)
        H.HELPER_PARENT.reset(token)
    assert fake.open_keys() == ["build:s1"]


async def _open(fake, tmp_path, session="build:s1"):
    token = preview(DEV)
    try:
        res = await PA.PageOpenTool().execute({"url": DEV}, ctx(tmp_path, session))
    finally:
        PV.reset_preview(token)
    assert res.ok, res.error
    return fake.made[-1]


def test_archive_and_delete_close_the_sessions_browser(tmp_path, fake):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from tests.test_build_sessions_v1367 import new_session

    work = tmp_path / "work"
    work.mkdir()
    client = TestClient(create_app(str(tmp_path / "home")))
    one = new_session(client, work)["id"]
    two = new_session(client, work)["id"]
    b1 = asyncio.run(_open(fake, tmp_path, f"build:{one}"))
    b2 = asyncio.run(_open(fake, tmp_path, f"build:{two}"))
    r = client.post(f"/build/sessions/{one}/archive", json={})
    assert r.status_code == 200, r.text
    assert b1.killed and b1.closed and not fake.is_open(f"build:{one}")
    assert not b2.killed  # another session's browser is not touched
    r = client.delete(f"/build/sessions/{two}")
    assert r.status_code == 200, r.text
    assert b2.killed and fake.open_keys() == []
    # The pane still says where the agent was, and that its browser closed.
    view = fake.view(f"build:{one}")
    assert view["open"] is False and view["closed_reason"] == "ended" and view["where"] == "localhost:5173"


async def test_an_idle_browser_closes(tmp_path, fake, monkeypatch):
    monkeypatch.setenv(PB.IDLE_ENV, "2")
    backend = await _open(fake, tmp_path)
    # Wait for what is asserted (the kill lands a moment after the browser
    # stops being listed as open: the close kills, then says so).
    for _ in range(300):
        if backend.killed and (fake.view("build:s1") or {}).get("closed_reason"):
            break
        await asyncio.sleep(0.05)
    assert backend.killed and not fake.is_open("build:s1")
    assert fake.view("build:s1")["closed_reason"] == "idle"


async def test_daemon_shutdown_closes_every_browser_and_the_thread(tmp_path, fake):
    a = await _open(fake, tmp_path, "build:a")
    b = await _open(fake, tmp_path, "build:b")
    assert fake._host.running()
    await asyncio.to_thread(procs.end_all)
    assert a.killed and b.killed and fake.open_keys() == []
    assert not fake._host.running()


async def test_stop_during_an_action_ends_it_and_closes_the_browser(tmp_path, fake):
    backend = await _open(fake, tmp_path)
    backend.hang = asyncio.Event()  # never set: the click would wait forever
    handle = TURNS.register("t-stop-1376")
    try:
        token = preview(DEV)
        try:
            call = asyncio.ensure_future(PA.PageClickTool().execute(
                {"target": "Add to cart"}, ctx(tmp_path, turn="t-stop-1376"),
            ))
            for _ in range(100):
                if backend.active:
                    break
                await asyncio.sleep(0.02)
            assert backend.active == 1
            handle.stop()
            res = await asyncio.wait_for(call, 10)
        finally:
            PV.reset_preview(token)
    finally:
        TURNS.release("t-stop-1376", handle)
    assert res.ok is False and res.error == PB.STOPPED
    assert backend.killed and not fake.is_open("build:s1")
    assert backend.cancelled  # the action itself was ended, not left running


async def test_a_cancelled_call_closes_the_browser(tmp_path, fake):
    backend = await _open(fake, tmp_path)
    backend.hang = asyncio.Event()
    call = asyncio.ensure_future(PA.PageClickTool().execute({"target": "Add to cart"}, ctx(tmp_path)))
    for _ in range(100):
        if backend.active:
            break
        await asyncio.sleep(0.02)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    for _ in range(200):
        if backend.killed:
            break
        await asyncio.sleep(0.02)
    assert backend.killed and backend.cancelled


async def test_a_turn_that_ends_stopped_closes_the_browser(tmp_path, fake):
    """A Stop while the model is thinking (no action under way) still closes
    it: routes/build.recorded_turn, at the turn's end."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.routes.build import recorded_turn
    from tests.test_build_sessions_v1367 import new_session

    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(tmp_path / "home"))
    tid = new_session(app, work)["id"]
    backend = await _open(fake, tmp_path, f"build:{tid}")

    async def nothing():
        if False:  # pragma: no cover — a lane that was stopped yields no end frame
            yield ""

    async for _ in recorded_turn(app.state.platform.engine, tid, "t-x", nothing()):
        pass
    assert backend.killed and fake.view(f"build:{tid}")["closed_reason"] == "stopped"


# --------------------------------------------------------------------------- #
# a page that goes somewhere the rules would not open
# --------------------------------------------------------------------------- #


async def test_a_click_that_leaves_for_a_public_site_is_stopped_and_comes_back(tmp_path, fake):
    backend = await _open(fake, tmp_path)
    token = preview(DEV)
    try:
        res = await PA.PageClickTool().execute({"target": "Docs"}, ctx(tmp_path))
    finally:
        PV.reset_preview(token)
    assert res.ok
    assert "example.com/docs" in res.output and "does not open public websites" in res.output
    assert backend.url() == DEV  # back on the last page it was allowed on
    assert "example.com" in fake.view("build:s1")["note"]


async def test_a_click_to_another_local_address_is_stopped_with_the_way_to_ask(tmp_path, fake):
    await _open(fake, tmp_path)
    token = preview(DEV)
    try:
        res = await PA.PageClickTool().execute({"target": "Admin"}, ctx(tmp_path))
    finally:
        PV.reset_preview(token)
    assert "localhost:8020/admin" in res.output and "call page_open with that address" in res.output


async def test_an_action_on_a_page_the_rules_would_not_open_is_refused(tmp_path, fake):
    """Belt and braces under the route and the arrival guard: every action
    re-checks the page it acts on."""
    backend = await _open(fake, tmp_path)
    backend._url = "https://example.com/"  # a navigation that slipped past
    token = preview(DEV)
    try:
        res = await PA.PageClickTool().execute({"target": "Log"}, ctx(tmp_path))
        read = await PA.PageReadTool().execute({}, ctx(tmp_path))
    finally:
        PV.reset_preview(token)
    assert res.ok is False and "not an address it may act on" in res.error
    assert read.ok is False and backend.clicks == []


async def test_the_manager_itself_refuses_a_public_open_before_starting(fake):
    with pytest.raises(PB.BrowserProblem, match="does not open public websites"):
        await fake.open("build:x", "https://example.com/")
    assert fake.made == []


async def test_a_page_that_arrives_somewhere_refused_is_sent_away(tmp_path):
    """The arrival guard (``framenavigated``) — the backstop for a hop the
    route and the redirect guard did not see."""
    seen: list[tuple[str, str]] = []
    backend = PB.EdgeBackend(
        edge="edge", work=tmp_path, guard=lambda u: PB.judge(u, set(), "http://localhost:5173"),
        note=lambda k, t: seen.append((k, t)),
    )
    left: list[Any] = []

    class _Page:
        async def goto(self, url, timeout=None):
            left.append(url)

    class _Frame:
        parent_frame = None

        def __init__(self, url):
            self.url = url

    page = _Page()
    backend._on_navigated(page, _Frame("http://localhost:5173/fine"))
    backend._on_navigated(page, _Frame("https://example.com/far"))
    await backend.quiet()
    assert seen == [("blocked", "refuse|https://example.com/far")]
    assert left == ["about:blank"]


async def test_a_click_on_the_dev_server_goes_through(tmp_path, fake):
    await _open(fake, tmp_path)
    token = preview(DEV)
    try:
        res = await PA.PageClickTool().execute({"target": "Add to cart"}, ctx(tmp_path))
    finally:
        PV.reset_preview(token)
    assert res.ok and "Clicked \"Add to cart\"" in res.output and "localhost:5173/cart" in res.output


# --------------------------------------------------------------------------- #
# page_read: console errors, failed requests — bounded, masked, cleared
# --------------------------------------------------------------------------- #


async def test_page_read_returns_the_logs_since_the_last_read_then_clears(tmp_path, fake):
    backend = await _open(fake, tmp_path)
    backend.note("console", "boom Authorization: Bearer sk-ant-api03-" + "z" * 30)
    token = preview(DEV)
    try:
        res = await PA.PageReadTool().execute({}, ctx(tmp_path))
        again = await PA.PageReadTool().execute({}, ctx(tmp_path))
    finally:
        PV.reset_preview(token)
    assert res.ok
    assert "Uncaught TypeError: cart is undefined" in res.output
    assert "GET localhost:5173/api/cart → 500" in res.output
    assert "z" * 30 not in res.output  # masked
    assert 'button "Add to cart"' in res.output
    assert res.data["console"] and res.data["failed"]
    assert "Console errors since the last read: none." in again.output  # cleared by the first read


async def test_the_logs_are_bounded_and_say_what_fell_out(tmp_path, fake):
    backend = await _open(fake, tmp_path)
    for i in range(PB.MAX_LOG_LINES + 7):
        backend.note("console", f"error {i} " + "x" * 1000)
    token = preview(DEV)
    try:
        res = await PA.PageReadTool().execute({}, ctx(tmp_path))
    finally:
        PV.reset_preview(token)
    assert len(res.data["console"]) == PB.MAX_LOG_LINES
    assert all(len(line) <= PB.MAX_LINE_CHARS for line in res.data["console"])
    assert "older ones not kept" in res.output


# --------------------------------------------------------------------------- #
# bounded, off the loop, one at a time
# --------------------------------------------------------------------------- #


async def test_an_action_runs_off_the_daemons_loop(tmp_path, fake):
    backend = await _open(fake, tmp_path)
    backend.block_s = 0.6  # a BLOCKING call inside the action
    ticks = 0
    stop = asyncio.Event()

    async def beat():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.02)

    hb = asyncio.ensure_future(beat())
    token = preview(DEV)
    try:
        res = await PA.PageClickTool().execute({"target": "Log"}, ctx(tmp_path))
    finally:
        PV.reset_preview(token)
        stop.set()
        await hb
    assert res.ok
    assert backend.threads == ["ij-agent-browser"]
    assert ticks >= 10, ticks  # the loop kept running while the action blocked


async def test_one_action_at_a_time_per_session(tmp_path, fake):
    a = await _open(fake, tmp_path, "build:a")
    b = await _open(fake, tmp_path, "build:b")
    a.delay_s = b.delay_s = 0.3
    token = preview(DEV)
    try:
        await asyncio.gather(*(PA.PageClickTool().execute({"target": "Log"}, ctx(tmp_path, s))
                               for s in ("build:a", "build:a", "build:a", "build:b")))
    finally:
        PV.reset_preview(token)
    assert a.max_active == 1 and len(a.clicks) == 3
    assert b.clicks == ["Log"]


@pytest.mark.timeout(60)
async def test_a_wedged_action_is_ended_by_its_time_limit(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(PB, "ACTION_TIMEOUT_S", 0.2)
    monkeypatch.setattr(PB, "HARD_BOUND_EXTRA_S", 0.3)
    backend = await _open(fake, tmp_path)
    backend.hang = asyncio.Event()
    token = preview(DEV)
    try:
        started = time.monotonic()
        res = await PA.PageClickTool().execute({"target": "Add to cart"}, ctx(tmp_path))
    finally:
        PV.reset_preview(token)
    assert time.monotonic() - started < 5
    assert res.ok is False and "did not answer within 0.5 s" in res.error
    assert backend.killed  # closed rather than left running


# --------------------------------------------------------------------------- #
# page_screenshot looks at the agent's browser's page
# --------------------------------------------------------------------------- #


async def test_page_screenshot_sees_the_page_the_agent_is_on(tmp_path, fake):
    await _open(fake, tmp_path)
    tool = PS.PageScreenshotTool()
    c = ctx(tmp_path)
    check = await tool.spend_check({}, c)
    assert check is not None and check.ask is False
    res = await tool.execute({}, c)
    assert res.ok, res.error
    assert res.images and res.data["browser"] == "agent"
    saved = Path(res.data["file"])
    assert saved.is_file() and saved.parent == tmp_path / "home" / "page-screenshots"


async def test_page_screenshot_with_no_browser_and_no_url_says_what_to_do(tmp_path, fake):
    res = await PS.PageScreenshotTool().execute({}, ctx(tmp_path))
    assert res.ok is False and "page_open" in res.error


# --------------------------------------------------------------------------- #
# the pane's view and its routes
# --------------------------------------------------------------------------- #


def test_the_pane_reads_where_the_agent_is_and_its_picture(tmp_path, fake):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from tests.test_build_sessions_v1367 import new_session

    work = tmp_path / "work"
    work.mkdir()
    client = TestClient(create_app(str(tmp_path / "home")))
    sid = new_session(client, work)["id"]
    assert client.get(f"/build/sessions/{sid}/browser").json() == {"view": None}
    assert client.get(f"/build/sessions/{sid}/browser/shot").status_code == 404

    async def go():
        token = preview(DEV)
        try:
            assert (await PA.PageOpenTool().execute({"url": DEV}, ctx(tmp_path, f"build:{sid}"))).ok
            assert (await PA.PageClickTool().execute({"target": "Add to cart"}, ctx(tmp_path, f"build:{sid}"))).ok
        finally:
            PV.reset_preview(token)

    asyncio.run(go())
    view = client.get(f"/build/sessions/{sid}/browser").json()["view"]
    assert view["open"] is True and view["where"] == "localhost:5173/cart" and view["title"] == "Cart"
    assert view["shot"] is True and "url" not in view  # never the raw address
    shot = client.get(f"/build/sessions/{sid}/browser/shot")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/jpeg"
    assert client.get("/build/sessions/nope/browser").status_code == 404


# --------------------------------------------------------------------------- #
# words and arming
# --------------------------------------------------------------------------- #


def test_the_run_log_says_where_and_what_never_what_was_typed():
    from iron_jarvis.agents import mission

    assert mission.tool_line("page_open", {"url": "http://localhost:5173/cart?token=abc"}, True) == (
        "opened localhost:5173/cart in its browser"
    )
    assert mission.tool_line("page_click", {"target": "Add to cart"}, True) == "clicked “Add to cart” in its browser"
    line = mission.tool_line("page_type", {"target": "Email", "text": "a@b.test"}, True)
    assert line == "typed into “Email” in its browser" and "a@b.test" not in line
    assert mission.tool_line("page_read", {}, True) == "read the page in its browser"
    assert mission.do_words("page_open") == "open a page on your computer or network in its browser"


async def test_only_a_harness_turn_arms_the_agents_browser(tmp_path, monkeypatch):
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from iron_jarvis.platform import build_platform
    from tests.test_build_sessions_v1367 import Script
    from tests.test_harness_pictures_v1371 import _body

    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    for harness in (None, "auto"):
        model = Script([])
        monkeypatch.setattr(platform.router, "stream", model.stream)
        kw = {"harness": harness} if harness else {}
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS,
                                     _body(ws, "open my dev server and click the button"), **kw)
        _ = [c async for c in gen]
        tools = set(model.calls[0]["tools"])
        if harness is None:
            assert not (tools & {"page_open", "page_click", "page_type", "page_read"})
        else:
            assert {"page_open", "page_click", "page_type", "page_read"} <= tools


def test_the_doctor_says_when_edge_is_missing(monkeypatch):
    import importlib

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    monkeypatch.setattr(PB, "edge_executable", lambda: "")
    row = doctor.check_agent_browser()
    assert row["ok"] is False and "Microsoft Edge was not found" in row["detail"]
    assert row["name"] in __import__("iron_jarvis.onboarding.readiness", fromlist=["x"]).DEV_TOOLCHAIN_CHECKS


async def test_no_edge_is_said_in_words_by_every_tool(tmp_path, monkeypatch):
    inst = PB.AgentBrowsers()
    monkeypatch.setattr(PB, "BROWSERS", inst)
    monkeypatch.setattr(PB, "edge_executable", lambda: "")
    try:
        res = await PA.PageOpenTool().execute({"url": DEV}, ctx(tmp_path))
    finally:
        inst.shutdown()
    assert res.ok is False and res.error == PB.NO_EDGE


def test_a_stale_throwaway_folder_is_removed_and_a_fresh_one_kept(tmp_path):
    old = tmp_path / (PB.WORK_PREFIX + "old")
    new = tmp_path / (PB.WORK_PREFIX + "new")
    for d in (old, new):
        (d / "profile").mkdir(parents=True)
    past = time.time() - PB.STALE_WORK_S - 60
    os.utime(old, (past, past))
    assert PB._sweep_stale(folder=tmp_path) == 1
    assert not old.exists() and new.exists()


# --------------------------------------------------------------------------- #
# the REAL Edge (skipped when Edge or Playwright is missing)
# --------------------------------------------------------------------------- #

EDGE = PB.edge_executable()
REAL = pytest.mark.skipif(
    not EDGE or bool(PB.playwright_problem()) or sys.platform != "win32",
    reason="needs the installed Microsoft Edge and Playwright's driver (Windows)",
)

SITE = """<html><head><title>Shop</title></head><body>
<h1>Shop</h1><p id=out>nothing yet</p>
<button onclick="document.getElementById('out').textContent='added to cart'">Add to cart</button>
<button onclick="brokenThing()">Broken</button>
<label>Email <input id=email></label>
<label>Password <input type=password id=pw oninput="document.getElementById('pwlen').textContent='typed '+this.value.length"></label>
<p id=pwlen>typed 0</p>
<a href="/r">redirect away</a>
<a href="/r404">redirect to nothing</a>
<a href="PUBLIC/js-landed">public link</a>
<a href="OTHER/other">other app</a>
<script>console.error('boot error'); fetch('/missing')</script>
</body></html>"""


class _Site:
    """A local http.server (never the network) that records what it served."""

    def __init__(self, page: str = SITE) -> None:
        self.hits: list[str] = []
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                site.hits.append(self.path)
                if self.path in ("/r", "/r404"):
                    far = "/landed" if self.path == "/r" else "/nowhere"
                    self.send_response(302)
                    self.send_header("Location", f"http://public.test:{site.port}{far}")
                    self.end_headers()
                    return
                if self.path == "/":
                    body = (page.replace("PUBLIC", f"http://public.test:{site.port}")
                            .replace("OTHER", site.other)).encode()
                elif self.path in ("/landed", "/js-landed", "/other"):
                    body = b"<title>Far</title><h1>far side</h1>"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.other = ""
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _alive(pids: list[int]) -> list[int]:
    import psutil

    return [p for p in pids if psutil.pid_exists(p)]


async def _wait_dead(pids: list[int], seconds: float = 15.0) -> list[int]:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        left = _alive(pids)
        if not left:
            return []
        await asyncio.sleep(0.2)
    return _alive(pids)


@REAL
@pytest.mark.timeout(240)
async def test_real_edge_reads_the_console_and_failed_requests_and_leaves_nothing(tmp_path):
    other = _Site("<title>Other</title>other")
    site = _Site()
    site.other = f"http://localhost:{other.port}"
    browsers = PB.AgentBrowsers()
    browsers.extra_args = ("--host-resolver-rules=MAP public.test 127.0.0.1",)
    key = "build:real"
    try:
        opened = await browsers.open(key, site.base, preview=site.base)
        assert opened.title == "Shop" and "Add to cart" in opened.text
        pids = browsers.pids(key)
        import psutil

        names = {psutil.Process(p).name().lower() for p in _alive(pids)}
        assert "node.exe" in names and "msedge.exe" in names, names  # driver + Edge
        # Every process of the driver's tree is a member of the browser's
        # kill-on-close job (procs.adopt) — so nothing can outlive it.
        job = browsers._sessions[key].backend._job
        assert job is not None
        members = set(job.member_pids())
        tree = PB._tree_of(browsers._sessions[key].backend._driver)
        assert tree and set(tree) <= members, (tree, members)
        clicked = await browsers.click(key, "Add to cart", preview=site.base)
        assert clicked.did == 'Clicked "Add to cart"'
        await browsers.click(key, "Broken", preview=site.base)
        read = await browsers.read(key, preview=site.base)
        assert "added to cart" in read.text
        assert any("boot error" in c for c in read.console)
        assert any("brokenThing is not defined" in c for c in read.console)
        assert any("/missing" in f and "404" in f for f in read.failed)
        # Typing into the password field is refused; the field stays empty.
        with pytest.raises(PB.BrowserProblem, match="password"):
            await browsers.type(key, "Password", "hunter2", preview=site.base)
        assert "typed 0" in (await browsers.read(key, preview=site.base)).text
        # A JS navigation to a public site: stopped BEFORE its request left.
        js = await browsers.click(key, "public link", preview=site.base)
        assert js.blocked and "public.test" in js.blocked[0] and "/js-landed" not in site.hits
        assert js.url == site.base
        # A server redirect to a public site: the page never stays there.
        redirected = await browsers.click(key, "redirect away", preview=site.base)
        assert redirected.blocked and "public.test" in redirected.blocked[0]
        assert redirected.url == site.base
        # …also when the far page is an error page (the arrival guard alone
        # never sees an address there: the redirect's hop is judged).
        nowhere = await browsers.click(key, "redirect to nothing", preview=site.base)
        assert nowhere.blocked and "public.test" in nowhere.blocked[0]
        assert nowhere.url == site.base
        # Another address on this PC: stopped before its request, with the way to ask.
        hop = await browsers.click(key, "other app", preview=site.base)
        assert hop.blocked and "call page_open with that address" in hop.blocked[0]
        assert other.hits == []
        work = browsers._sessions[key].work  # the throwaway folder, in the SYSTEM temp
        assert Path(os.environ.get("TEMP", "")).resolve() in work.resolve().parents or work.exists()
        closed = await browsers.close(key, "test")
        assert closed  # (a renderer may have ended on its own already)
        assert await _wait_dead(pids) == []  # the driver, Edge, every helper process
        assert not work.exists()
    finally:
        browsers.shutdown()
        site.close()
        other.close()


@REAL
@pytest.mark.timeout(240)
async def test_real_edge_dies_with_a_daemon_that_is_killed(tmp_path):
    """The daemon KILLED (no clean-up of any kind): the job's only handle
    closes with the process and the kernel ends the driver and every Edge
    process — the user's own Edge is never touched (only these pids)."""
    import psutil

    site = _Site()
    script = tmp_path / "hold_browser.py"
    script.write_text(textwrap.dedent(f"""
        import asyncio, json, sys, time
        from iron_jarvis.harness import page_browser as PB

        async def main():
            b = PB.AgentBrowsers()
            await b.open("build:k", {site.base!r}, preview={site.base!r})
            print(json.dumps(b.pids("build:k")), flush=True)
            await asyncio.sleep(120)

        asyncio.run(main())
    """), encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        line = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 90)
        pids = json.loads(line)
        assert len(_alive(pids)) >= 3, pids  # the driver, Edge and its helpers
        psutil.Process(child.pid).kill()  # TerminateProcess: nothing runs on the way out
        child.wait(10)
        assert await _wait_dead(pids, 20) == []
    finally:
        if child.poll() is None:
            child.kill()
        site.close()
