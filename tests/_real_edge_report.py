"""A real-Edge test that fails on CI says what the machine was doing, ALSO
when it passes on its retry (Build › Code follow-up, item 1).

Two hooks; ``tests/conftest.py`` imports them, which registers them:

* :func:`pytest_runtest_makereport` adds a "machine" section
  (``tests/_runner_state.py``) to every failing ``test_real_edge*`` call.
* :func:`pytest_terminal_summary` prints, at the end of the run, what every
  real-Edge attempt that failed and was run again saw (CI's ``--reruns 1``)
  when the test then did NOT fail: the failure's own words and the attempt's
  sections (the machine, the captured log, stderr, stdout). pytest prints a
  report's sections only for a final failure, and pytest-rerunfailures' ``-rR``
  names the test only (``RERUN <id>``, ``PASSED <id>``), so a pass on retry —
  the shape of two of the three real-Edge incidents (Release 44530239907 and
  44153829918) — otherwise left nothing in the log (FU-1 review).

A test that failed on both attempts is left out of that block: pytest keeps
an item's sections across attempts (``Item._report_sections`` is never
cleared), so its own failure report already carries both attempts' sections
(pinned in ``tests/test_build_browser_slow_open.py``).

Names only (the CI logs are public): the sections are what the test itself
captured, and the machine section prints no argument of a command line.
"""

from __future__ import annotations

from typing import Any

import pytest

#: A real-Edge test is named so: both hooks key on it.
PREFIX = "test_real_edge"
#: The most lines of one section printed for an attempt that was run again
#: (its end, where the failure is).
MOST_LINES = 200
#: The heading of the end-of-run block.
HEADING = "real-Edge tests that passed only when run again: what the failed attempt saw"


def is_real_edge(name: str) -> bool:
    """Whether a test's name (``originalname``, or a node id's last part)
    is a real-Edge test's."""
    return str(name or "").startswith(PREFIX)


def _name_of(nodeid: str) -> str:
    return str(nodeid or "").rsplit("::", 1)[-1]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """A real-Edge test (``test_real_edge*``) that FAILS reports what the
    machine was doing (``tests/_runner_state.py``: the busiest programs, the
    processors, memory, disk; names only). Release run 44745306821 failed
    two of them four times on a runner that ran every test four to eight
    times slower than the rerun's, and nothing named why. Added before the
    report is built, so the attempt's own report carries it (and a later
    attempt's report too). A skip is not a failure."""
    excinfo = call.excinfo
    if (
        call.when == "call"
        and excinfo is not None
        and is_real_edge(getattr(item, "originalname", "") or item.name)
        and not excinfo.errisinstance((pytest.skip.Exception, pytest.xfail.Exception))
    ):
        try:
            from tests._runner_state import runner_state

            text = runner_state()
        except Exception as exc:  # noqa: BLE001 — the test's own failure is the point
            text = f"the machine's state could not be read: {type(exc).__name__}"
        item.add_report_section("call", "machine", text)
    return (yield)


def passed_on_retry(stats: "dict[str, list[Any]]") -> "list[Any]":
    """The real-Edge attempts that failed and were run again, of the tests
    that did not fail in the end (a final failure prints its own sections)."""
    ended_red = {
        str(getattr(r, "nodeid", "") or "") for key in ("failed", "error") for r in stats.get(key, [])
    }
    return [
        r for r in stats.get("rerun", [])
        if is_real_edge(_name_of(r.nodeid)) and r.nodeid not in ended_red
    ]


def _crash(report: Any) -> str:
    """The failure's own words (its first five lines)."""
    longrepr = getattr(report, "longrepr", None)
    message = str(getattr(getattr(longrepr, "reprcrash", None), "message", "") or "")
    if not message and longrepr is not None:
        lines = str(longrepr).strip().splitlines()
        message = lines[-1] if lines else ""
    return "\n".join(message.splitlines()[:5])


def _tail(text: str) -> "list[str]":
    lines = str(text).rstrip("\n").splitlines()
    if len(lines) <= MOST_LINES:
        return lines
    return [f"({len(lines) - MOST_LINES} earlier lines left out)", *lines[-MOST_LINES:]]


def pytest_terminal_summary(terminalreporter):
    """The block :data:`HEADING`: one part per real-Edge attempt that failed,
    was run again, and whose test then did not fail. Nothing when there is
    none (a local run never reruns)."""
    tr = terminalreporter
    reports = passed_on_retry(tr.stats)
    if not reports:
        return
    tr.write_sep("=", HEADING, yellow=True, bold=True)
    for rep in reports:
        attempt = int(getattr(rep, "rerun", 0) or 0) + 1
        tr.write_sep("_", f"{rep.nodeid} (attempt {attempt}, {rep.when})")
        crash = _crash(rep)
        if crash:
            tr.write_line(crash)
        for title, content in rep.sections:
            tr.write_sep("-", str(title))
            for line in _tail(content):
                tr.write_line(line)
