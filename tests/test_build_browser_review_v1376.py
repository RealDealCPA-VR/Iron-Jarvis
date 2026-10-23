"""v1.376.0 (IJ harness part 2, Phase 5) REVIEW — the agent's browser, each
defect the review found pinned red-first.

Real-Edge pins run against a local ``http.server`` (never the network) and
are skipped when Edge or Playwright's driver is missing; the rest use the
fake browser of ``tests/test_build_browser_v1376.py``.
"""

from __future__ import annotations

import asyncio
import http.server
import sys
import threading
import time
from typing import Any

import pytest

from iron_jarvis.harness import page_browser as PB

EDGE = PB.edge_executable()
REAL = pytest.mark.skipif(
    not EDGE or bool(PB.playwright_problem()) or sys.platform != "win32",
    reason="needs the installed Microsoft Edge and Playwright's driver (Windows)",
)


class Site:
    """A local http.server that serves ``pages`` (path -> html) and records
    every request it got (``hits``: (host header, path))."""

    def __init__(self, pages: "dict[str, str]", redirects: "dict[str, str] | None" = None) -> None:
        self.hits: list[tuple[str, str]] = []
        site = self
        self.pages = pages
        self.redirects = dict(redirects or {})

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _serve(self) -> None:
                site.hits.append((str(self.headers.get("Host") or ""), self.path))
                path = self.path.split("?", 1)[0]
                if path in site.redirects:
                    self.send_response(302)
                    self.send_header("Location", site.fill(site.redirects[path]))
                    self.end_headers()
                    return
                html = site.pages.get(path)
                if html is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = site.fill(html).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _serve
            do_POST = _serve

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def fill(self, text: str) -> str:
        return text.replace("PUBLIC", f"public.test:{self.port}").replace("PORT", str(self.port))

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _browsers() -> PB.AgentBrowsers:
    b = PB.AgentBrowsers()
    b.extra_args = ("--host-resolver-rules=MAP public.test 127.0.0.1",)
    return b


# --------------------------------------------------------------------------- #
# typing: a LABEL is not a way round the password refusal
# --------------------------------------------------------------------------- #

LABEL_PAGE = """<html><head><title>Sign in</title></head><body>
<p id=out>typed nothing</p>
<label for=pw id=lab>Secret</label>
<input type=password id=pw oninput="document.getElementById('out').textContent='typed into pw'">
<label id=wrap>Wrap <input type=password id=pw2 oninput="document.getElementById('out').textContent='typed into pw2'"></label>
<span id=inner>inside</span>
<label id=deep>Deep <span id=deepspan>here</span> <input type=password id=pw3 oninput="document.getElementById('out').textContent='typed into pw3'"></label>
<label id=fine for=email>Email</label><input id=email>
</body></html>"""


@REAL
@pytest.mark.timeout(180)
async def test_real_edge_typing_through_a_label_of_a_password_field_is_refused():
    """Playwright's fill() FOLLOWS A LABEL to its control (``retarget(...,
    'follow-label')``): a css target naming the <label> of a password field,
    or a label wrapping one, or a span inside such a label, typed the text
    into the password field while the refusal read the LABEL's facts (no
    type, no words) and let it through."""
    site = Site({"/": LABEL_PAGE})
    b = _browsers()
    key = "build:label"
    try:
        await b.open(key, site.base, preview=site.base)
        for target in ({"css": "#lab"}, {"css": "#wrap"}, {"css": "#deepspan"}):
            with pytest.raises(PB.BrowserProblem, match="password"):
                await b.type(key, target, "hunter2", preview=site.base)
        read = await b.read(key, preview=site.base)
        assert "typed nothing" in read.text, read.text
        # A label of an ordinary field still types into it.
        done = await b.type(key, {"css": "#fine"}, "a@b.test", preview=site.base)
        assert done.did == "Typed"
    finally:
        b.shutdown()
        site.close()


# --------------------------------------------------------------------------- #
# dialogs: the agent's own navigation leaves a page that asks before leaving
# --------------------------------------------------------------------------- #

DIRTY_PAGE = """<html><head><title>Form</title></head><body>
<p id=out>clean</p>
<input id=dirty placeholder="Notes" oninput="window.onbeforeunload=function(e){e.preventDefault();e.returnValue='sure?';return 'sure?'}">
<button onclick="document.getElementById('out').textContent = confirm('Really delete?') ? 'confirmed' : 'kept'">Ask</button>
</body></html>"""


@REAL
@pytest.mark.timeout(180)
async def test_real_edge_a_page_that_asks_before_leaving_does_not_trap_the_agent():
    """A page with unsaved changes (``onbeforeunload``) answered by DISMISS
    cancelled the agent's own page_open, which then failed with words about a
    download. The agent asked to go: the browser leaves and says the page
    asked. A confirm() is still answered Cancel (never yes for the user)."""
    site = Site({"/": DIRTY_PAGE, "/two": "<title>Two</title>two"})
    b = _browsers()
    key = "build:dirty"
    try:
        await b.open(key, site.base, preview=site.base)
        await b.click(key, "Ask", preview=site.base)
        read = await b.read(key, preview=site.base)
        assert "kept" in read.text
        assert any("Really delete?" in d and "Cancel" in d for d in read.dialogs), read.dialogs
        await b.type(key, "Notes", "half-written", preview=site.base)
        moved = await b.open(key, site.base + "two", preview=site.base)
        assert moved.title == "Two" and moved.url.endswith("/two")
        read = await b.read(key, preview=site.base)
        assert any("before leaving" in d for d in read.dialogs), read.dialogs
    finally:
        b.shutdown()
        site.close()


# --------------------------------------------------------------------------- #
# a browser whose Edge was closed (by the user, a crash) starts again
# --------------------------------------------------------------------------- #


@REAL
@pytest.mark.timeout(180)
async def test_real_edge_a_browser_whose_edge_died_is_replaced_on_the_next_open():
    """Edge killed under the agent: every later page_open raised a raw
    TargetClosedError and the session had no browser until it idled out
    (10 minutes). Now the dead browser is closed (its job, its folder) and
    page_open starts a fresh one, saying so."""
    import psutil

    site = Site({"/": "<title>Shop</title>shop"})
    b = _browsers()
    key = "build:dead"

    async def kill_edge() -> "tuple[list[int], Any]":
        old = b.pids(key)
        work = b._sessions[key].work
        main = [
            p for p in old
            if psutil.pid_exists(p)
            and psutil.Process(p).name().lower() == "msedge.exe"
            and "--type=" not in " ".join(psutil.Process(p).cmdline())
        ]
        assert main, old
        psutil.Process(main[0]).kill()
        for _ in range(100):
            if not psutil.pid_exists(main[0]):
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        return old, work

    async def none_left(old: "list[int]") -> None:
        left = [p for p in old if psutil.pid_exists(p) and p not in b.pids(key)]
        deadline = time.monotonic() + 15
        while left and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            left = [p for p in left if psutil.pid_exists(p)]
        assert left == []

    try:
        await b.open(key, site.base, preview=site.base)
        # 1. The next call is page_open: a fresh browser, and it says so.
        old, work = await kill_edge()
        fresh = await b.open(key, site.base, preview=site.base)
        assert fresh.title == "Shop"
        assert any("fresh one was opened" in line for line in fresh.blocked), fresh.blocked
        assert b._sessions[key].work != work and not work.exists()
        await none_left(old)
        # 2. The next call reads: the dead browser is closed, in words.
        old, work = await kill_edge()
        with pytest.raises(PB.BrowserProblem, match="had stopped"):
            await b.read(key, preview=site.base)
        assert not b.is_open(key) and not work.exists()
        await none_left(old)
        again = await b.open(key, site.base, preview=site.base)
        assert again.title == "Shop"
    finally:
        b.shutdown()
        site.close()


# --------------------------------------------------------------------------- #
# Local only: nothing the agent's browser does reaches a public address
# --------------------------------------------------------------------------- #

LEAKY_PAGE = """<html><head><title>Leaky</title>
<script src="http://PUBLIC/cdn.js"></script>
</head><body><p id=o>waiting</p>
<img src="http://PUBLIC/pixel.png">
<script>
fetch('http://PUBLIC/api?typed=secret-ish').catch(function () {});
navigator.sendBeacon('http://PUBLIC/beacon', 'what the agent typed');
try { new WebSocket('ws://PUBLIC/ws'); } catch (e) {}
fetch('/local-api').then(function (r) { return r.text(); })
  .then(function (t) { document.getElementById('o').textContent = t; });
</script>
<a href="/hop">a server redirect to a public site</a>
</body></html>"""


def _public_hits(site: Site) -> list[tuple[str, str]]:
    return [h for h in site.hits if h[0].startswith("public.test")]


@REAL
@pytest.mark.timeout(240)
async def test_real_edge_local_only_reaches_no_public_address_at_all():
    """DECISION (review, item 2b): outside Local only a page's own requests
    go where the page sends them (a CDN script, its API) — like any browser
    and the Browser pane. Under LOCAL ONLY nothing the agent's browser does
    reaches a public address: it runs behind a proxy that refuses everything
    outside this PC and the user's network, so a sub-resource, a fetch, a
    beacon, a WebSocket and a server redirect's next hop never leave."""
    from iron_jarvis.providers import egress

    pages = {"/": LEAKY_PAGE, "/local-api": "local answer"}
    site = Site(pages, redirects={"/hop": "http://PUBLIC/landed?carried=1"})
    b = _browsers()
    key = "build:local"
    try:
        # Outside Local only the page's requests go out (the decision's
        # other half — and the proof the page really makes them).
        await b.open(key, site.base, preview=site.base)
        await asyncio.sleep(1.0)
        assert {"/cdn.js", "/api?typed=secret-ish", "/beacon"} <= {p for _, p in _public_hits(site)}
        await b.close(key, "test")
        site.hits.clear()
        with egress.local_only_scope(True):
            opened = await b.open(key, site.base, preview=site.base)
            await asyncio.sleep(1.5)
            read = await b.read(key, preview=site.base)
            assert "local answer" in read.text  # this PC still answers
            hop = await b.click(key, "a server redirect to a public site", preview=site.base)
        assert opened.title == "Leaky"
        assert _public_hits(site) == [], site.hits
        assert hop.url.rstrip("/") == site.base.rstrip("/")
        assert any("Local only" in line for line in read.failed), read.failed
    finally:
        b.shutdown()
        site.close()


async def test_a_browser_started_outside_local_only_starts_again_inside_it(tmp_path, monkeypatch):
    """The proxy is chosen when Edge starts, so a browser that was opened
    outside Local only is never used inside it (and back): it is closed and
    the next page_open starts one for the turn's setting, saying so."""
    from iron_jarvis.providers import egress
    from tests.test_build_browser_v1376 import DEV, FakeBackend

    made: list[Any] = []

    def factory(**kw):
        backend = FakeBackend(**{k: v for k, v in kw.items() if k != "local_only"})
        backend.local_only = bool(kw.get("local_only"))
        made.append(backend)
        return backend

    inst = PB.AgentBrowsers(backend_factory=factory)
    try:
        await inst.open("build:s", DEV, preview=DEV)
        assert made[-1].local_only is False
        with egress.local_only_scope(True):
            # The next call opens: a browser for Local only, and it says so.
            res = await inst.open("build:s", DEV, preview=DEV)
            assert made[-1].local_only is True and made[0].killed
            assert any("Local only was turned on" in line for line in res.blocked), res.blocked
            await inst.read("build:s", preview=DEV)  # the same browser now
        assert len(made) == 2
        # Local only off again; the next call reads: closed, in words.
        with pytest.raises(PB.BrowserProblem, match="Local only was turned off"):
            await inst.read("build:s", preview=DEV)
        assert made[1].killed and not inst.is_open("build:s")
        await inst.open("build:s", DEV, preview=DEV)
        assert len(made) == 3 and made[-1].local_only is False
    finally:
        inst.shutdown()


def test_the_local_only_proxy_lets_through_exactly_the_private_addresses():
    """The proxy's bypass list is built FROM egress's one definition of
    "this PC or your network" (its address blocks and name suffixes), so the
    two can never drift."""
    from iron_jarvis.providers import egress

    rules = PB.local_only_bypass().split(";")
    for net in egress._PRIVATE_NETS:
        assert str(net) in rules, net
    for suffix in egress.PRIVATE_SUFFIXES:
        assert "*" + suffix in rules, suffix
    assert {"localhost", "*.localhost", "<local>"} <= set(rules)
