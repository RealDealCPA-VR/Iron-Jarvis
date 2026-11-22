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

import contextlib
import http.server
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
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

    def __init__(self, backend: PB.EdgeBackend, steps: "list[str]", exc: Exception,
                 late_s: float = 0.0, began_early_s: float = 0.0) -> None:
        self.backend = backend
        self.steps = steps
        self.exc = exc
        #: How long after the open's limit the driver reports its failure:
        #: the open's own clock (its trace's start) is moved back by the
        #: limit and this much, never slept through.
        self.late_s = late_s
        #: How long the open took before the driver was asked (reading the
        #: processors' time): the open's start alone is moved back, and by
        #: the limit too (the driver reports right at its limit).
        self.began_early_s = began_early_s

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
        trace = b._trace
        if self.late_s or self.began_early_s:
            trace.t0 -= trace.limit_s + self.late_s
            trace.opened -= trace.limit_s + self.late_s + self.began_early_s
        raise self.exc


def _slow_open_lines(caplog) -> "list[str]":
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.WARNING and "did not finish" in r.getMessage()]


#: The product's own bound, read before any pin widens it.
PRODUCT_FACTS_S = PB.SLOW_OPEN_FACTS_S


@pytest.fixture(autouse=True)
def _facts_have_time(monkeypatch):
    """The process facts' bound is a product limit, not a speed bar for a
    loaded test machine: these pins give it room (the pins about the bound
    set their own)."""
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
        # 1. Past the bound: the line goes without the facts. (The fake
        # driver fails at once, well inside its 45 s: the whole 0.2 s is the
        # facts', however long the start's reading took on a loaded PC.)
        monkeypatch.setattr(PB, "SLOW_OPEN_FACTS_S", 0.2)
        b = _backend(tmp_path)
        b._page = _Page(b, ["route"], PlaywrightTimeout("Timeout 45000ms exceeded."))
        with pytest.raises(PB.BrowserProblem):
            await b.goto("http://127.0.0.1:5173/", 45.0)
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


async def test_the_facts_never_hold_an_open_more_than_their_bound_past_its_own_limit(
        tmp_path, caplog, monkeypatch):
    """FU-1 review: the facts are read inside the open's hard bound
    (``OPEN_TIMEOUT_S + HARD_BOUND_EXTRA_S``), so they may use at most
    ``SLOW_OPEN_FACTS_S`` past the open's OWN limit. A driver that reports
    its time limit that late or later leaves them no time at all: the open
    raises at once (its words unchanged, never the hard bound's "did not
    answer ... so it was closed"), and the line says how late the report
    came. A driver that reports on time still gets the facts."""
    read: list[int] = []

    def facts(self, trace):  # noqa: ANN001
        read.append(1)
        return "the facts"

    monkeypatch.setattr(PB.EdgeBackend, "_slow_open_facts", facts)
    monkeypatch.setattr(PB, "SLOW_OPEN_FACTS_S", 2.0)
    caplog.set_level(logging.WARNING, logger=LOGGER)

    b = _backend(tmp_path)
    b._page = _Page(b, ["route"], PlaywrightTimeout("Timeout 45000ms exceeded."), late_s=3.0)
    with pytest.raises(PB.BrowserProblem) as info:
        await b.goto("http://127.0.0.1:5173/", 45.0)
    assert str(info.value) == "Opening 127.0.0.1:5173 did not finish within 45 s."
    assert read == [], "the facts were read past their bound"
    (line,) = _slow_open_lines(caplog)
    assert "the server never answered" in line, line
    over = re.compile(r"the browser's processes were not read: the open was (\d+\.\d) s past its limit "
                      r"when it was reported")
    late = over.search(line)
    assert late and float(late.group(1)) >= 3.0, line
    assert "the facts" not in line, line

    # Reading the processors' time at the start counts too: the bound runs
    # from when the open began, not from when the driver was asked.
    caplog.clear()
    b1 = _backend(tmp_path)
    b1._page = _Page(b1, ["route"], PlaywrightTimeout("Timeout 45000ms exceeded."), began_early_s=3.0)
    with pytest.raises(PB.BrowserProblem):
        await b1.goto("http://127.0.0.1:5173/", 45.0)
    assert read == [], "the facts were read past their bound (the start's reading not counted)"
    (line,) = _slow_open_lines(caplog)
    late = over.search(line)
    assert late and float(late.group(1)) >= 3.0, line

    # The control: reported on time, the facts are read (the bound widened:
    # on a loaded PC reading the processors' time alone can take a second).
    monkeypatch.setattr(PB, "SLOW_OPEN_FACTS_S", 60.0)
    caplog.clear()
    b2 = _backend(tmp_path)
    b2._page = _Page(b2, ["route"], PlaywrightTimeout("Timeout 45000ms exceeded."))
    with pytest.raises(PB.BrowserProblem):
        await b2.goto("http://127.0.0.1:5173/", 45.0)
    assert read == [1]
    (line,) = _slow_open_lines(caplog)
    assert line.endswith("the facts."), line


def test_the_facts_bound_keeps_most_of_the_hard_bounds_margin():
    """The 10 s the hard bound allows past an open's limit is for a driver
    that reports late; the facts take at most a fifth of it."""
    assert 0 < PRODUCT_FACTS_S <= PB.HARD_BOUND_EXTRA_S / 5


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
    wiring). A page that opens in time logs nothing.

    Each open gets 10 s and the held answers 60 s: the timeout stays certain,
    and the steps before it (several Edge round trips) have room on a runner
    that runs several times slower than this PC (FU-1 third review)."""
    limit_s = 10.0
    caplog.set_level(logging.WARNING, logger=LOGGER)
    site = _SlowSite(hold_s=60.0)
    b = PB.AgentBrowsers()
    key = "build:slow-open"
    try:
        await b.open(key, site.base, preview=site.base)
        said = [r.getMessage() for r in caplog.records if r.name == LOGGER and "agent browser:" in r.getMessage()]
        assert said == [], said
        backend = b._sessions[key].backend
        with pytest.raises(PB.BrowserProblem, match=f"did not finish within {limit_s:g} s"):
            await b.run(backend.goto(site.base + "slow?k=secret-ish", limit_s))
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
        with pytest.raises(PB.BrowserProblem, match=f"did not finish within {limit_s:g} s"):
            await b.run(backend.goto(site.base + "held", limit_s))
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
    names only (CI logs are public), never a command line's arguments."""
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
    assert "with no living parent, started since this run began: msedge.exe " in text, text
    # Priorities: CI starts pytest below normal, so the section says which.
    assert "this test's process: " in text and " priority" in text, text
    # This test's own worker is counted (the count reads the processes).
    assert "python.exe 0" not in text, text


def test_only_a_failing_real_edge_test_gets_the_machine_section():
    assert _hook_run("test_real_edge_something", failed=False) == []
    assert _hook_run("test_something_else", failed=True) == []
    assert _hook_run("test_real_edge_something", failed=pytest.skip.Exception("no Edge here")) == []
    assert _hook_run("test_real_edge_something", failed=True, when="setup") == []


def _posix_name() -> Any:
    """psutil's own ``Process.name`` as it runs on Linux and macOS: a name of
    15 characters or more (where the kernel cuts it) is finished from the
    first word of the program's command line. Run here on this machine's own
    processes, so a Windows run proves the Linux gate's path too."""
    import types

    import psutil

    names = dict(vars(psutil))
    names.update(POSIX=True, WINDOWS=False)
    return types.FunctionType(psutil.Process.name.__code__, names, "name")


#: Stands for an argument on some program's command line (a secret, say):
#: plain words, nothing shaped like a credential.
_AN_ARGUMENT = "--sample-argument-that-must-stay-out-of-the-log"


@pytest.mark.parametrize("names", ["this platform's", "posix"])
def test_no_argument_of_a_command_line_reaches_the_runner_state(monkeypatch, names):
    """A secret on some process's command line never reaches a public CI log:
    the runner's state prints names, CPU and memory, never an argument. On
    Linux psutil's own ``name()`` reads a command line to finish a name the
    kernel cut at 15 characters (every runner has such programs) and keeps
    the file name of its first word only; so the promise is that no ARGUMENT
    is printed, on this platform's path and on Linux's."""
    import psutil

    from tests import _runner_state

    real = psutil.Process.cmdline
    asked: list[int] = []

    def cmdline(self):  # noqa: ANN001
        asked.append(self.pid)
        try:
            first = list(real(self))[:1]
        except Exception:  # noqa: BLE001 — not ours to read: the argument alone
            first = []
        return [*first, _AN_ARGUMENT]

    monkeypatch.setattr(_runner_state, "SAMPLE_S", 0.05)
    monkeypatch.setattr(psutil.Process, "cmdline", cmdline)
    if names == "posix":
        monkeypatch.setattr(psutil.Process, "name", _posix_name())
    try:
        text = _runner_state.runner_state()
    finally:
        psutil.process_iter.cache_clear()  # no Process keeps a name the stand-in made
    assert "busiest programs" in text, text
    assert _AN_ARGUMENT not in text, text
    if names == "posix":
        # Linux's path was really taken: some name was finished from a command line.
        assert asked, "no program here has a name of 15 characters or more"


# --------------------------------------------------------------------------- #
# The machine section: what an Edge an earlier test left looks like
# (FU-1 fourth review)
# --------------------------------------------------------------------------- #


def test_edge_and_node_are_counted_by_their_exact_names():
    """``msedgewebview2.exe`` (Windows' web view, inside many programs) and
    ``msedge_proxy.exe`` are not Edge: a match by the first letters counted
    them as msedge.exe (12 of 137 on the PC this was measured on)."""
    from tests._runner_state import _counted

    for name in ("msedge.exe", "MSEDGE.EXE", "msedge"):
        assert _counted(name) == "msedge.exe", name
    for name in ("node.exe", "Node.exe", "node"):
        assert _counted(name) == "node.exe", name
    for name in ("python.exe", "python3", "python3.12", "pythonw.exe"):
        assert _counted(name) == "python.exe", name
    for name in ("msedgewebview2.exe", "msedgewebview2", "msedge_proxy.exe", "identity_helper.exe",
                 "node_repl.exe", "nodemon.exe", "?"):
        assert _counted(name) == "", name


class _Stand:
    """A stand-in process for :func:`tests._runner_state.runner_state`: its
    name, parent and start, and its CPU seconds at each read (``None``: gone
    by that read)."""

    def __init__(self, pid: int, name: str, *, parent: Any, born: float,
                 cpu: "tuple[float | None, ...]" = (0.0, 0.0)) -> None:
        self.pid = pid
        self.info = {"name": name}
        self._parent = parent
        self._born = born
        self._cpu = list(cpu)

    def cpu_times(self) -> Any:
        import psutil

        user = self._cpu.pop(0) if len(self._cpu) > 1 else self._cpu[0]
        if user is None:
            raise psutil.NoSuchProcess(self.pid)
        return SimpleNamespace(user=user, system=0.0)

    def memory_info(self) -> Any:
        return SimpleNamespace(rss=64 * 2**20)

    def nice(self) -> int:
        return 32 if sys.platform == "win32" else 0

    def parent(self) -> Any:
        return self._parent

    def create_time(self) -> float:
        return self._born


def test_an_edge_or_driver_with_no_living_parent_is_named_and_a_live_one_is_not(monkeypatch):
    """CI runs a shard with ``-n auto``, so the counts hold the Edge of every
    real-Edge test running on another worker (about 11 processes each), and
    one of them can be among the busiest programs: a count is no leftover.
    What is one: an msedge.exe or node.exe with no living parent, started
    since this run began and still there after the sample. The busiest
    programs say which of them it is. One that started before the failing
    test was left by an earlier test; one that started during it outlived
    the failing test's own close."""
    import psutil

    from tests import _runner_state

    now = time.time()
    run_began, test_began = now - 100.0, now - 10.0
    living = SimpleNamespace(pid=4_000_000)

    def stands() -> "list[_Stand]":
        return [
            _Stand(4_000_001, "msedge.exe", parent=living, born=now),  # a live test's Edge
            _Stand(4_000_002, "msedge.exe", parent=None, born=now, cpu=(0.0, 0.9)),  # this test's, left running, busy
            _Stand(4_000_008, "msedge.exe", parent=None, born=now - 50.0),  # an earlier test's, left running
            _Stand(4_000_003, "msedgewebview2.exe", parent=None, born=now),  # not Edge
            _Stand(4_000_004, "msedge_proxy.exe", parent=None, born=now),  # not Edge
            _Stand(4_000_005, "node.exe", parent=None, born=now - 200.0),  # started before this run
            _Stand(4_000_006, "node.exe", parent=None, born=now, cpu=(0.0, None)),  # being closed: gone a moment later
            _Stand(4_000_007, "node.exe", parent=living, born=now - 50.0),  # a live test's driver
        ]

    monkeypatch.setattr(_runner_state, "SAMPLE_S", 0.05)
    monkeypatch.setattr(_runner_state, "_run_began", lambda proc: run_began)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(stands()))
    text = _runner_state.runner_state()
    assert "running: msedge.exe 3, node.exe 3, python.exe 0" in text, text
    assert "with no living parent, started since this run began: msedge.exe 2, node.exe 0\n" in text, text
    rows = [line for line in text.splitlines() if "(pid " in line]
    assert [line for line in rows if line.endswith(", no living parent")] == [
        line for line in rows if "(pid 4000002)" in line or "(pid 4000008)" in line
    ], text
    # Told the failing attempt's start, it says which of them an earlier test left.
    text = _runner_state.runner_state(test_began=test_began)
    assert (
        "with no living parent, started since this run began: msedge.exe 2, node.exe 0; "
        "of them, started before this test: msedge.exe 1, node.exe 0\n"
    ) in text, text


def test_the_machine_section_is_told_when_the_failing_attempt_began(monkeypatch):
    """The hook keeps each real-Edge attempt's setup start and hands it to the
    machine section, so an Edge an earlier test left is told from one the
    failing test left itself. A rerun's setup starts the clock again."""
    import tests.conftest as conftest
    from tests import _runner_state

    told: "list[float | None]" = []
    monkeypatch.setattr(_runner_state, "runner_state", lambda test_began=None: told.append(test_began) or "state")
    try:
        raise AssertionError("red")
    except AssertionError as exc:
        red = pytest.ExceptionInfo.from_exception(exc)
    item = SimpleNamespace(originalname="test_real_edge_x", name="test_real_edge_x", stash={},
                           add_report_section=lambda *a: None)
    for when, start, excinfo in (("setup", 100.0, None), ("call", 101.0, red),
                                 ("setup", 200.0, None), ("call", 201.0, red)):
        gen = conftest.pytest_runtest_makereport(item, SimpleNamespace(when=when, start=start, excinfo=excinfo))
        next(gen)
        with pytest.raises(StopIteration):
            gen.send("the report")
    assert told == [100.0, 200.0]


def test_the_run_began_when_the_oldest_python_above_this_process_started():
    """An xdist worker's own start is not the run's: its controller (and on
    Windows the venv's launcher above it) started first, and a worker started
    again after a crash would otherwise miss an Edge the crashed one left."""
    from tests import _runner_state as RS

    def proc(name: str, born: float, parents: "tuple[Any, ...]" = ()) -> Any:
        return SimpleNamespace(name=lambda: name, create_time=lambda: born, parents=lambda: list(parents))

    above = (proc("python.exe", 50.0), proc("python3.12", 20.0), proc("pytest.exe", 10.0), proc("python.exe", 5.0))
    assert RS._run_began(proc("python.exe", 90.0, above)) == 20.0
    assert RS._run_began(proc("python.exe", 90.0)) == 90.0


def test_on_linux_an_orphan_handed_to_pid_1_has_no_living_parent(monkeypatch):
    """Linux and macOS give an orphan to pid 1; Windows gives it to no one."""
    from tests import _runner_state as RS

    now = time.time()
    handed = _Stand(4_000_010, "msedge", parent=SimpleNamespace(pid=1), born=now)
    kept = _Stand(4_000_011, "msedge", parent=SimpleNamespace(pid=4_000_000), born=now)
    monkeypatch.setattr(RS, "_WINDOWS", False)
    assert RS._left_behind(handed, since=now - 1)
    assert not RS._left_behind(kept, since=now - 1)
    assert not RS._left_behind(handed, since=now + 1)  # started before the run: not this run's
    monkeypatch.setattr(RS, "_WINDOWS", True)
    assert not RS._left_behind(handed, since=now - 1)


@pytest.mark.skipif(sys.platform != "win32", reason="the real-Edge tests run on Windows, where an orphan has no parent")
def test_a_real_process_whose_parent_exited_has_no_living_parent():
    """psutil's own answer on real processes: a program whose parent exited
    has no living parent; a child of this test and this test's own process
    have one (a live test's Edge has its driver, the driver its worker)."""
    import subprocess

    import psutil

    from tests import _runner_state as RS

    since = RS._run_began(psutil.Process())
    exe = getattr(sys, "_base_executable", "") or sys.executable
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hand_off = (
        "import subprocess, sys; d = subprocess.DEVNULL; "
        "p = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(120)'], "
        "stdin=d, stdout=d, stderr=d); print(p.pid)"
    )
    child = subprocess.Popen([exe, "-I", "-c", "import time; time.sleep(120)"], **quiet)
    orphan = None
    try:
        out = subprocess.run([exe, "-I", "-c", hand_off], capture_output=True, text=True, timeout=60, check=True)
        orphan = psutil.Process(int(out.stdout.strip()))
        assert RS._left_behind(orphan, since)
        assert not RS._left_behind(psutil.Process(child.pid), since)
        assert not RS._left_behind(psutil.Process(), since)
    finally:
        for proc in (orphan, child):
            with contextlib.suppress(Exception):
                proc.kill()
                proc.wait(10)


# --------------------------------------------------------------------------- #
# CI: a real-Edge test that passes only when run again still says what its
# failed attempt saw (FU-1 review)
# --------------------------------------------------------------------------- #

_ROOT = Path(__file__).resolve().parents[1]


def test_the_suites_conftest_registers_both_real_edge_hooks(pytestconfig):
    """The hooks live in ``tests/_real_edge_report.py``; the suite's
    conftest importing them is what registers them."""
    from tests import _real_edge_report as R

    for hook, ours in (("pytest_runtest_makereport", R.pytest_runtest_makereport),
                       ("pytest_terminal_summary", R.pytest_terminal_summary)):
        impls = [impl.function for impl in getattr(pytestconfig.hook, hook).get_hookimpls()]
        assert ours in impls, f"{hook} is not registered"


_RERUN_SAMPLE = '''\
import asyncio, os, pathlib

from iron_jarvis.harness import page_browser as PB

MARKS = pathlib.Path(os.environ["ONCE_MARKS"])
Timeout = type("TimeoutError", (Exception,), {})


class _Page:
    def is_closed(self):
        return False

    async def goto(self, url, wait_until="", timeout=0):
        raise Timeout("Timeout 45000ms exceeded.")


def _an_open_that_runs_out_of_time(tmp):
    b = PB.EdgeBackend(edge="edge", work=tmp, guard=lambda u: PB.Verdict("ok"), note=lambda k, t: None)
    b._page = _Page()
    asyncio.run(b.goto("http://127.0.0.1:5173/shop", 45.0))


def _first_time(name):
    mark = MARKS / name
    if mark.exists():
        return False
    mark.write_text("x")
    return True


def test_real_edge_fails_once_then_passes(tmp_path):
    if _first_time("edge"):
        _an_open_that_runs_out_of_time(tmp_path)


def test_real_edge_fails_twice(tmp_path):
    _an_open_that_runs_out_of_time(tmp_path)


def test_real_edge_never_fails():
    pass


def test_something_else_fails_once_then_passes():
    if _first_time("other"):
        raise AssertionError("an ordinary flake")
'''


def _section(text: str, heading: str) -> str:
    """The part of a pytest run's output under a ``=== heading ===`` line,
    up to the next such line."""
    lines = text.splitlines()
    heading_line = re.compile(r"^=+ .+ =+$")
    (start,) = [i for i, ln in enumerate(lines) if heading_line.match(ln) and f" {heading} " in ln]
    rest = lines[start + 1:]
    end = next((i for i, ln in enumerate(rest) if heading_line.match(ln)), len(rest))
    return "\n".join(rest[:end])


@pytest.mark.timeout(300)
def test_a_real_edge_test_that_passes_on_its_retry_prints_what_its_failed_attempt_saw(tmp_path, monkeypatch):
    """The shape of two of the three real-Edge incidents: a test fails once
    and passes when CI runs it again. pytest prints a report's sections only
    for a final failure, and ``-rR`` names the test only, so the machine
    section and the product's own slow-open line never reached the log. Run
    with CI's own flags (xdist, ``--reruns 1``, ``-rR``) and the suite's own
    hooks: the run ends with a block that gives the failed attempt's words,
    its machine section and the product's line — for the real-Edge test that
    passed on retry only (one that failed twice prints both attempts in its
    own failure; an ordinary test is not a real-Edge test)."""
    import subprocess

    sys.path.insert(0, str(_ROOT / "scripts"))
    try:
        import ci_flaky_report as flaky_report
    finally:
        sys.path.pop(0)
    from tests import _real_edge_report as R

    run = tmp_path / "run"
    marks = tmp_path / "marks"
    run.mkdir()
    marks.mkdir()
    (run / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    # Registered exactly as tests/conftest.py registers them.
    (run / "conftest.py").write_text(
        "from tests._real_edge_report import pytest_runtest_makereport, pytest_terminal_summary  # noqa: F401\n",
        encoding="utf-8")
    (run / "test_sample.py").write_text(_RERUN_SAMPLE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_ROOT), env.get("PYTHONPATH", "")) if p)
    env["ONCE_MARKS"] = str(marks)
    env["PYTHONIOENCODING"] = "utf-8"
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "-n", "1",
         "--reruns", "1", "--rerun-except", "pytest-timeout", "-rR", "test_sample.py"],
        cwd=run, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=240, stdin=subprocess.DEVNULL,
    )
    text = res.stdout + res.stderr
    assert res.returncode == 1, "the test red twice must fail the run:\n" + text

    block = _section(text, R.HEADING)
    once = "test_sample.py::test_real_edge_fails_once_then_passes"
    assert f"{once} (attempt 1, call)" in block, block
    # The failed attempt's own words, its machine section, the product's line.
    assert "BrowserProblem: Opening 127.0.0.1:5173/shop did not finish within 45 s." in block, block
    assert "Captured machine call" in block and "busiest programs" in block, block
    # pytest's own item and call: the hook kept the attempt's setup start.
    assert "; of them, started before this test: msedge.exe " in block, block
    assert "agent browser: Opening 127.0.0.1:5173/shop did not finish within 45 s. " \
           "Edge never started the page's request" in block, block
    assert block.count("busiest programs") == 1, block
    # Only that one: an ordinary flake and a test red twice are not in it.
    assert "test_something_else" not in block and "an ordinary flake" not in block, block
    assert "test_real_edge_fails_twice" not in block and "never_fails" not in block, block

    # The test red twice: its own failure carries BOTH attempts' machine
    # sections (pytest keeps an item's sections across attempts) — why the
    # block can leave it out.
    failures = _section(text, "FAILURES")
    assert "test_real_edge_fails_twice" in failures, failures
    assert failures.count("Captured machine call") == 2, failures

    # CI's own report of the tests that passed only on retry still reads
    # the run right (the block adds no line it would take for a result).
    passed_on_retry, red = flaky_report.retried(text)
    assert sorted(passed_on_retry) == [once, "test_sample.py::test_something_else_fails_once_then_passes"], text
    assert red == ["test_sample.py::test_real_edge_fails_twice"], text
