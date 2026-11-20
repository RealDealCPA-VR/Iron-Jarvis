"""Build › Code follow-up, item 1 (FU-1): an open of the agent's browser that
runs out of time says, in the daemon's log, HOW FAR the page got, and a
real-Edge test that fails on CI says what the runner was doing.

What the release run showed (Release 44745306821, job 136319029882): two
real-Edge pins failed four times in a row with "Opening 127.0.0.1:<port> did
not finish within 45 s." while every test of that shard ran four to eight
times slower than the same tests on the rerun's runner. The only clue to
WHICH step stopped was a connection the test's server accepted that never
carried a request. These pins make the next failure name the step: Edge
never started the request, the rules never let it go, the server never
answered, or the page never finished loading; with Edge's own CPU time and
how busy the PC's processors were. Never the address's query string.
"""

from __future__ import annotations

import http.server
import logging
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.harness import page_browser as PB

LOGGER = "ironjarvis.iron_jarvis.harness.page_browser"


#: Playwright's timeout, by its class NAME (``playwright_words`` reads it so;
#: the builtin of that name is left alone).
PlaywrightTimeout = type("TimeoutError", (Exception,), {})


def _backend(tmp_path) -> PB.EdgeBackend:
    return PB.EdgeBackend(edge="edge", work=tmp_path, guard=lambda u: PB.Verdict("ok"),
                          note=lambda k, t: None)


def _nav_request(url: str) -> Any:
    return SimpleNamespace(url=url, is_navigation_request=lambda: True,
                           frame=SimpleNamespace(parent_frame=None))


class _Route:
    def __init__(self) -> None:
        self.continued = False

    async def continue_(self) -> None:
        self.continued = True

    async def abort(self, _code: str = "") -> None:
        pass


class _Page:
    """A page whose ``goto`` plays the given part of a navigation through the
    backend's own handlers, then fails like Playwright."""

    def __init__(self, backend: PB.EdgeBackend, steps: "list[str]", exc: Exception) -> None:
        self.backend = backend
        self.steps = steps
        self.exc = exc

    def is_closed(self) -> bool:
        return False

    async def goto(self, url: str, wait_until: str = "", timeout: float = 0) -> None:
        b = self.backend
        frame = SimpleNamespace(parent_frame=None, url=url)
        req = _nav_request(url)
        for step in self.steps:
            if step == "route":
                await b._route(_Route(), req)
            elif step == "response":
                b._on_response(SimpleNamespace(status=200, url=url, request=SimpleNamespace(
                    method="GET", is_navigation_request=lambda: True, frame=frame)))
            elif step == "arrived":
                b._on_navigated(self, frame)
            elif step == "domcontentloaded":
                b._on_page_event("domcontentloaded")
        raise self.exc


def _slow_open_lines(caplog) -> "list[str]":
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.WARNING and "did not finish" in r.getMessage()]


@pytest.fixture(autouse=True)
def _facts_have_time(monkeypatch):
    """The process facts' bound is a product limit, not a speed bar for a
    loaded test machine: these pins give it room (the one pin about the
    bound sets its own)."""
    monkeypatch.setattr(PB, "SLOW_OPEN_FACTS_S", 60.0)


async def test_an_open_that_runs_out_of_time_logs_how_far_the_page_got(tmp_path, caplog, monkeypatch):
    """The request reached the rules and was let go, and then nothing came
    back: the log line says so (step by step, with times), with Edge's
    processes and how busy this PC's processors were — and the words the
    agent gets are unchanged."""
    readings = iter([(10.0, 100.0), (55.0, 150.0)])  # (busy, all) at the start, at the failure
    monkeypatch.setattr(PB, "_cpu_now", lambda: next(readings))
    b = _backend(tmp_path)
    b._page = _Page(b, ["route"], PlaywrightTimeout("Timeout 500ms exceeded."))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    with pytest.raises(PB.BrowserProblem) as info:
        await b.goto("http://127.0.0.1:5173/cart?token=abc123", 0.5)
    assert str(info.value) == "Opening 127.0.0.1:5173/cart did not finish within 0.5 s."
    lines = _slow_open_lines(caplog)
    assert len(lines) == 1, lines
    line = lines[0]
    assert "Edge asked the rules about the page at +" in line, line
    assert "the rules let it go at +" in line, line
    assert "the server never answered" in line, line
    assert "processors were 90% busy during the open" in line, line
    assert "the browser's processes (Edge and its driver): 0, 0.0 s of CPU" in line, line
    assert "abc123" not in line and "token" not in line, line
    # The trace is for one open only.
    assert b._trace is None


async def test_an_open_whose_request_never_started_says_edge_never_asked(tmp_path, caplog):
    """The CI failure's shape: the test's server saw a connection that never
    carried a request. When Edge never even asks the rules about the page,
    the line says the request never started — the stall is Edge's (or its
    driver's), not the rules', not the server's."""
    b = _backend(tmp_path)
    b._page = _Page(b, [], PlaywrightTimeout("Timeout 45000ms exceeded."))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    with pytest.raises(PB.BrowserProblem, match="did not finish within 45 s"):
        await b.goto("http://127.0.0.1:5173/", 45.0)
    (line,) = _slow_open_lines(caplog)
    assert "Edge never started the page's request (the rules were never asked)" in line, line


async def test_an_open_that_got_its_answer_says_which_later_step_did_not_come(tmp_path, caplog):
    """Answered, arrived, its HTML read, never finished loading: each step is
    named with its time, then the one that never came."""
    b = _backend(tmp_path)
    b._page = _Page(b, ["route", "response", "arrived", "domcontentloaded"],
                    PlaywrightTimeout("Timeout 500ms exceeded."))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    with pytest.raises(PB.BrowserProblem):
        await b.goto("http://127.0.0.1:5173/", 0.5)
    (line,) = _slow_open_lines(caplog)
    assert "the server answered (200) at +" in line, line
    assert "the page arrived at +" in line, line
    assert "its HTML was read at +" in line, line
    assert "it never finished loading" in line, line


async def test_an_open_that_fails_for_another_reason_logs_nothing_more(tmp_path, caplog):
    """A dev server that is not running is no mystery: no extra log line (the
    log stays calm), the agent's words as before."""
    b = _backend(tmp_path)
    b._page = _Page(b, ["route"], Exception("net::ERR_CONNECTION_REFUSED at http://127.0.0.1:5173/"))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    with pytest.raises(PB.BrowserProblem, match="nothing answered there"):
        await b.goto("http://127.0.0.1:5173/", 0.5)
    said = [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno >= logging.WARNING]
    assert said == [], said
    assert b._trace is None


async def test_the_line_is_written_when_the_process_facts_are_late_or_the_action_is_cut_off(
        tmp_path, caplog, monkeypatch):
    """On the starved machine this is for, reading Edge's processes can be
    slow too: past ``SLOW_OPEN_FACTS_S`` the line goes without them, and
    when the action's own bound cancels the open while they are read, the
    line is still written (the steps are the point)."""
    import asyncio

    started = threading.Event()
    release = threading.Event()

    def held_facts(self, trace):  # noqa: ANN001 — held until the test lets go
        started.set()
        release.wait(30)
        return "late facts"

    monkeypatch.setattr(PB.EdgeBackend, "_slow_open_facts", held_facts)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    try:
        # 1. Past the bound: the line goes without the facts.
        monkeypatch.setattr(PB, "SLOW_OPEN_FACTS_S", 0.2)
        b = _backend(tmp_path)
        b._page = _Page(b, ["route"], PlaywrightTimeout("Timeout 500ms exceeded."))
        with pytest.raises(PB.BrowserProblem):
            await b.goto("http://127.0.0.1:5173/", 0.5)
        (line,) = _slow_open_lines(caplog)
        assert "the server never answered" in line and "could not be read in time" in line, line
        assert "late facts" not in line

        # 2. The action's own bound cancels the open WHILE the facts are read
        # (what ``asyncio.timeout`` in ``_act`` does): the line is still written.
        caplog.clear()
        started.clear()
        monkeypatch.setattr(PB, "SLOW_OPEN_FACTS_S", 60.0)
        b2 = _backend(tmp_path)
        b2._page = _Page(b2, [], PlaywrightTimeout("Timeout 500ms exceeded."))
        task = asyncio.ensure_future(b2.goto("http://127.0.0.1:5173/", 0.5))
        assert await asyncio.to_thread(started.wait, 30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        (line,) = _slow_open_lines(caplog)
        assert "Edge never started the page's request" in line, line
        assert b2._trace is None
    finally:
        release.set()


def test_the_cpu_share_reads_the_processors_busy_time():
    """The PC's load over the open: busy time over all time, both deltas."""
    assert PB._busy_percent((10.0, 100.0), (55.0, 150.0)) == 90
    assert PB._busy_percent(None, (1.0, 2.0)) is None
    assert PB._busy_percent((1.0, 2.0), (1.0, 2.0)) is None  # no time passed


# --------------------------------------------------------------------------- #
# the real Edge: the trace is wired to Playwright's own events
# --------------------------------------------------------------------------- #

EDGE = PB.edge_executable()
REAL = pytest.mark.skipif(
    not EDGE or bool(PB.playwright_problem()) or sys.platform != "win32",
    reason="needs the installed Microsoft Edge and Playwright's driver (Windows)",
)


class _SlowSite:
    """``/`` answers at once; ``/slow`` holds its answer for ``hold_s``."""

    def __init__(self, hold_s: float) -> None:
        release = self.release = threading.Event()

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def handle_one_request(self) -> None:
                try:
                    super().handle_one_request()
                except OSError:
                    pass  # Edge went away while the answer was held

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/slow"):
                    release.wait(hold_s)
                body = b"<title>Shop</title>shop"
                if self.path.startswith("/held"):
                    # The HTML at once; its picture held: the load never ends.
                    body = b"<title>Held</title><p>held</p><img src='/slow.png'>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.release.set()
        self.srv.shutdown()
        self.srv.server_close()


@REAL
@pytest.mark.timeout(180)
async def test_real_edge_a_slow_answer_is_named_as_the_step_that_stopped(caplog):
    """Through the real Edge and Playwright: a page whose server holds its
    answer past the open's limit is logged as asked, let go, and never
    answered — so the route, the response and the arrival hooks are wired to
    Playwright's own events (a fake proves the words, this proves the
    wiring). A page that opens in time logs nothing."""
    caplog.set_level(logging.WARNING, logger=LOGGER)
    site = _SlowSite(hold_s=20.0)
    b = PB.AgentBrowsers()
    key = "build:slow-open"
    try:
        await b.open(key, site.base, preview=site.base)
        said = [r.getMessage() for r in caplog.records if r.name == LOGGER and "agent browser:" in r.getMessage()]
        assert said == [], said
        backend = b._sessions[key].backend
        with pytest.raises(PB.BrowserProblem, match="did not finish within 3 s"):
            await b.run(backend.goto(site.base + "slow?k=secret-ish", 3.0))
        (line,) = _slow_open_lines(caplog)
        assert "Edge asked the rules about the page at +" in line, line
        assert "the rules let it go at +" in line, line
        assert "the server never answered" in line, line
        assert "Edge started in " in line, line
        assert "msedge.exe" in line, line
        assert "the driver (and so Edge) runs at " in line and " priority" in line, line
        assert "secret-ish" not in line, line
        # A page that arrives and is read but whose picture never comes:
        # the later steps are wired to Playwright's own events too.
        caplog.clear()
        with pytest.raises(PB.BrowserProblem, match="did not finish within 3 s"):
            await b.run(backend.goto(site.base + "held", 3.0))
        (line,) = _slow_open_lines(caplog)
        assert "the server answered (200) at +" in line, line
        assert "the page arrived at +" in line, line
        assert "its HTML was read at +" in line, line
        assert "it never finished loading" in line, line
    finally:
        site.close()
        b.shutdown()


# --------------------------------------------------------------------------- #
# CI: a failing real-Edge test prints the runner's state
# --------------------------------------------------------------------------- #


def _hook_run(nodeid_name: str, failed: "bool | BaseException", when: str = "call") -> "list[tuple[str, str, str]]":
    import tests.conftest as conftest

    added: list[tuple[str, str, str]] = []
    item = SimpleNamespace(originalname=nodeid_name, name=nodeid_name,
                           add_report_section=lambda when, key, text: added.append((when, key, text)))
    raised = failed if isinstance(failed, BaseException) else AssertionError("red") if failed else None
    excinfo = None
    if raised is not None:
        try:
            raise raised
        except BaseException as exc:  # noqa: BLE001 — a real ExceptionInfo needs a traceback
            excinfo = pytest.ExceptionInfo.from_exception(exc)
    call = SimpleNamespace(when=when, excinfo=excinfo)
    gen = conftest.pytest_runtest_makereport(item, call)
    next(gen)
    with pytest.raises(StopIteration) as done:
        gen.send("the report")
    assert done.value.value == "the report"
    return added


def test_a_failing_real_edge_test_reports_what_the_runner_was_doing(monkeypatch):
    """The plan's "process lists on failure": a ``test_real_edge*`` test that
    fails gets a section naming the busiest programs on the machine, its
    processors and memory, and how many Edge / node / python processes ran —
    names only (CI logs are public), never command lines."""
    from tests import _runner_state

    monkeypatch.setattr(_runner_state, "SAMPLE_S", 0.05)
    added = _hook_run("test_real_edge_something", failed=True)
    assert len(added) == 1, added
    when, key, text = added[0]
    assert (when, key) == ("call", "machine")
    assert "processors:" in text and "memory:" in text, text
    assert "(pid 0)" not in text, text  # Windows' idle time is no program
    assert "busiest programs" in text, text
    assert "running: msedge.exe " in text and "python.exe " in text, text
    # Priorities: CI starts pytest below normal, so the section says which.
    assert "this test's process: " in text and " priority" in text, text
    # This test's own worker is counted (the count reads the processes).
    assert "python.exe 0" not in text, text


def test_only_a_failing_real_edge_test_gets_the_machine_section():
    assert _hook_run("test_real_edge_something", failed=False) == []
    assert _hook_run("test_something_else", failed=True) == []
    assert _hook_run("test_real_edge_something", failed=pytest.skip.Exception("no Edge here")) == []
    assert _hook_run("test_real_edge_something", failed=True, when="setup") == []


def test_the_runner_state_never_reads_a_command_line(monkeypatch):
    """A secret on some process's command line never reaches a public CI log:
    the runner's state reads names, CPU and memory, never a command line."""
    import psutil

    from tests import _runner_state

    read: list[int] = []

    def cmdline(self):  # noqa: ANN001
        read.append(self.pid)
        return []

    monkeypatch.setattr(_runner_state, "SAMPLE_S", 0.05)
    monkeypatch.setattr(psutil.Process, "cmdline", cmdline)
    text = _runner_state.runner_state()
    assert "busiest programs" in text
    assert read == []
