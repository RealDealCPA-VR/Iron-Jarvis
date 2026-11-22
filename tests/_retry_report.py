"""Every test that fails an attempt and is run again leaves that attempt's
words in the job log (build-out X-3).

CI runs a failed test once more before the gate goes red (``--reruns 1``,
pytest-rerunfailures, v1.445.1). ``tests.yml``, ``release.yml`` and
``linux-release.yml`` all run pytest the same way, from the repository's root
over ``tests/``, so this one hook serves the three, on Windows and on Linux.
pytest prints a report's traceback and captured output only for a FINAL
failure, and ``-rR`` names a retried test only (``RERUN <id>``, then
``PASSED <id>``): twice (X-1, X-2) a test that passed on its retry left
nothing of its failure in the log, and its cause had to be guessed.

:func:`pytest_terminal_summary_retries` (``tests/conftest.py`` imports it,
which registers it) ends a run in which any attempt was run again with the
block :data:`HEADING`:

* one part per attempt that failed and was run again (the report
  pytest-rerunfailures marks ``rerun``), headed by the test, the attempt, its
  phase and how the test ended: the failure's own words, the end of its
  traceback, then the last lines of each section that attempt captured itself
  (stdout, stderr, log). At most :data:`MOST_LINES` lines a part and
  :data:`MOST_WIDTH` characters a line, for at most :data:`MOST_ATTEMPTS`
  attempts (those of the tests that passed on a retry first: they have no
  other record; a test that failed again prints its last attempt itself);
* then the list of the tests that passed only on a retry
  (``scripts/ci_flaky_report.py`` names them on the run's page too, and says
  where this block is).

Never a frame's arguments or locals: a failure pytest prints gives each
frame's arguments (and, with ``-l``, its locals); this block reads only an
entry's source lines, its ``E`` lines and where it is. Every text is masked
before it is cut, by the shared mask (``iron_jarvis.detections.redact.mask``)
and the log's query redaction (``iron_jarvis.core.logging``); a text that
cannot be masked is left out (:data:`LEFT_OUT`), never printed as it is. Every
line of the block starts with :data:`MARK` (and a ``##[`` in it is broken), so
no line of it can be read as a GitHub workflow command, nor by
``ci_flaky_report.py`` as a result.

``tests/_real_edge_report.py`` keeps its own block for the ``test_real_edge*``
tests that passed on a retry (their sections and the machine's state); for
such a test this block gives the words and points there, instead of printing
the same sections twice.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from tests import _real_edge_report as EDGE

#: The heading of the end-of-run block (``scripts/ci_flaky_report.py`` names
#: it on the run's page: ``RETRY_HEADING``, pinned equal).
HEADING = "tests that failed an attempt and were run again: what that attempt said"
#: The most lines one attempt's part takes, its heading included.
MOST_LINES = 60
#: The most characters of one line (the rest is left out, and the line says so).
MOST_WIDTH = 300
#: The most attempts printed in one run (every test is still listed).
MOST_ATTEMPTS = 30
#: The failure's own words: their first lines.
MOST_WORDS = 5
#: The traceback: its last lines (where the failure is).
MOST_TRACE = 16
#: What starts every line of the block that is not a part's heading.
MARK = "| "
#: What stands for a text that could not be masked.
LEFT_OUT = "(a line is left out here: it could not be masked)"
#: The heading of the list of the tests that passed only on a retry.
LISTED = "passed only on a retry"

#: Lines masked beyond those printed, so a secret that spans lines (a private
#: key's block, about 50 lines) is masked whole even when its start is not
#: printed.
_MARGIN = 120
#: The mask reads whole lines, never a cut one (a cut could leave a piece of a
#: value it no longer knows). When the lines to mask hold more than
#: _MOST_MASKED characters, each line longer than _LONG_LINE is left out
#: instead, so its work stays bounded.
_MOST_MASKED = 2_000_000
_LONG_LINE = 4000


def _mask_function() -> "Callable[[str], str] | None":
    """The shared mask, then the log's query redaction; None when either
    cannot be loaded (then nothing is printed as it is)."""
    try:
        from iron_jarvis.core.logging import _redact_query_credentials
        from iron_jarvis.detections.redact import mask
    except Exception:  # noqa: BLE001 — then every text is left out
        return None

    def masked(text: str) -> str:
        out = mask(text)
        if not isinstance(out, str):
            raise TypeError("the mask answered no text")
        return _redact_query_credentials(out)

    return masked


def _bounded(window: "list[str]") -> "list[str]":
    if sum(len(ln) for ln in window) <= _MOST_MASKED:
        return window
    return [ln if len(ln) <= _LONG_LINE else f"(a line of {len(ln)} characters is left out)" for ln in window]


def _masked(text: Any, mask: "Callable[[str], str] | None", *, keep: int, last: bool) -> "tuple[list[str], int]":
    """The masked lines of *text*, at most *keep* of them (its last, or its
    first), and how many lines are left out; ``[LEFT_OUT]`` when it cannot be
    masked."""
    lines = str(text or "").splitlines()
    if not lines or keep <= 0:
        return [], len(lines)
    window = lines[-(keep + _MARGIN):] if last else lines[:keep + _MARGIN]
    outside = len(lines) - len(window)
    try:
        if mask is None:
            raise LookupError("no mask")
        shown = mask("\n".join(_bounded(window))).splitlines()
    except Exception:  # noqa: BLE001 — fail closed: never the text as it is
        return [LEFT_OUT], 0
    if len(shown) > keep:
        outside += len(shown) - keep
        shown = shown[-keep:] if last else shown[:keep]
    return shown, outside


def _plain(line: str) -> str:
    """*line* at most MOST_WIDTH characters, and never a legacy workflow
    command (GitHub reads ``##[name]`` anywhere in a line)."""
    line = str(line).replace("##[", "#-#[")
    if len(line) <= MOST_WIDTH:
        return line
    return f"{line[:MOST_WIDTH]} ({len(line) - MOST_WIDTH} more characters left out)"


def _one(text: Any, mask: "Callable[[str], str] | None") -> str:
    """One masked line (a test's name)."""
    shown, _ = _masked(str(text or "").replace("\n", " "), mask, keep=1, last=False)
    return _plain(shown[0] if shown else "")


def _words(longrepr: Any) -> str:
    """The failure's own words: pytest's crash line (``reprcrash.message``).
    A failure that is plain text (a string) is all traceback (:func:`_trace`)."""
    if longrepr is None or isinstance(longrepr, str):
        return ""
    if isinstance(longrepr, tuple):  # a skip's (path, line, words)
        return str(longrepr[-1]) if longrepr else ""
    crash = getattr(longrepr, "reprcrash", None)
    return str(getattr(crash, "message", "") or "")


def _trace(longrepr: Any) -> "list[str]":
    """The lines of the traceback pytest built for the failure, with no
    frame's arguments or locals: of each entry only its source lines with the
    failure's ``E`` lines (``lines``) and where it is (``reprfileloc``); never
    its ``reprfuncargs`` or ``reprlocals``. Plain text (what xdist sends for
    a failure that has no traceback, such as a missing fixture) is kept as it
    is: it holds no frame's values."""
    if isinstance(longrepr, str):
        return longrepr.splitlines()
    if longrepr is None or isinstance(longrepr, tuple):
        return []
    chain = longrepr.chain if hasattr(longrepr, "chain") else None
    if chain is None:
        tb = getattr(longrepr, "reprtraceback", None)
        if tb is None:
            # The same text xdist would send for it (above).
            return str(longrepr).splitlines() if hasattr(longrepr, "toterminal") else []
        chain = [(tb, None, None)]
    out: list[str] = []
    for tb, _crash, descr in chain:
        for entry in getattr(tb, "reprentries", None) or []:
            for line in getattr(entry, "lines", None) or []:
                out.extend(str(line).splitlines())
            loc = getattr(entry, "reprfileloc", None)
            if loc is not None:
                out.append(f"{loc.path}:{loc.lineno}: {loc.message}")
        extra = getattr(tb, "extraline", None)
        if extra:
            out.append(str(extra))
        if descr:
            out.extend(["", str(descr)])
    return out


def _own_sections(rep: Any, earlier: Any) -> "list[tuple[str, str]]":
    """The sections *rep*'s attempt captured itself. pytest keeps an item's
    sections across attempts (``Item._report_sections`` is never cleared), so
    a later attempt's report starts with the earlier report's sections, then
    the earlier attempt's teardown (which ran after that report was built)."""
    sections = [(str(t), str(c)) for t, c in (getattr(rep, "sections", None) or [])]
    if earlier is None:
        return sections
    before = [(str(t), str(c)) for t, c in (getattr(earlier, "sections", None) or [])]
    if sections[:len(before)] != before:
        return sections
    sections = sections[len(before):]
    if int(getattr(earlier, "rerun", 0) or 0) < int(getattr(rep, "rerun", 0) or 0):
        while sections and sections[0][0].endswith(" teardown"):
            sections.pop(0)
    return sections


def _sections(sections: "list[tuple[str, str]]", mask: "Callable[[str], str] | None", room: int) -> "list[str]":
    """Each section's title and its last lines in at most *room* lines, shared
    fairly: a short section gives what it does not use to the others."""
    fits = room // 2  # a title and at least one line each
    if not sections or fits <= 0:
        return []
    out: list[str] = []
    if len(sections) > fits:
        fits = (room - 1) // 2
        out.append(f"({len(sections) - fits} earlier sections left out)")
        sections = sections[len(sections) - fits:] if fits else []
        room -= 1
    if not sections:
        return out
    shown: list[tuple[str, list[str], int]] = []
    for title, content in sections:
        lines, gone = _masked(content, mask, keep=room - 1, last=True)
        shown.append((_one(title, mask), lines, gone))
    needs = [1 + len(lines) + (1 if gone else 0) for _, lines, gone in shown]
    shares = [0] * len(shown)
    left = room
    for k, i in enumerate(sorted(range(len(shown)), key=lambda i: needs[i])):
        shares[i] = min(needs[i], left // (len(shown) - k))
        left -= shares[i]
    for (title, lines, gone), need, share in zip(shown, needs, shares):
        out.append(f"-- {title} --")
        if share < need:
            kept = max(0, share - 2)
            gone += len(lines) - kept
            lines = lines[len(lines) - kept:] if kept else []
        if gone:
            out.append(f"({gone} earlier lines left out)")
        out.extend(lines)
    return out


def _attempt(rep: Any, earlier: Any, ended: str, edge: bool, mask: "Callable[[str], str] | None") -> "list[tuple[str, str]]":
    """One attempt's part: its heading, then at most MOST_LINES - 1 lines."""
    attempt = int(getattr(rep, "rerun", 0) or 0) + 1
    when = str(getattr(rep, "when", "") or "call")
    head = ("_", f"{_one(rep.nodeid, mask)} (attempt {attempt}, {when}; {ended})")
    body: list[str] = []
    try:
        longrepr = getattr(rep, "longrepr", None)
        words, _ = _masked(_words(longrepr), mask, keep=MOST_WORDS, last=False)
        trace, gone = _masked("\n".join(_trace(longrepr)), mask, keep=MOST_TRACE - 1, last=True)
        body += words
        if gone:
            body.append(f"({gone} earlier lines of the traceback left out)")
        body += trace
    except Exception as exc:  # noqa: BLE001 — the other attempts still print
        body.append(f"its words could not be read: {type(exc).__name__}")
    if edge:
        body.append(f'what it captured, and the machine\'s state: the block "{EDGE.HEADING}"')
    else:
        body += _sections(_own_sections(rep, earlier), mask, MOST_LINES - 1 - len(body))
    return [head, *[("|", _plain(line)) for line in body[:MOST_LINES - 1]]]


def _ids(stats: "dict[str, list[Any]]", *keys: str) -> "set[str]":
    return {str(getattr(r, "nodeid", "") or "") for key in keys for r in stats.get(key, [])}


def build(stats: "dict[str, list[Any]]") -> "list[tuple[str, str]]":
    """The block's lines, as ``(kind, text)``: ``"_"`` a part's heading,
    ``"|"`` a line of it. Empty when nothing was run again."""
    reruns = [r for r in stats.get("rerun", []) if getattr(r, "nodeid", None)]
    if not reruns:
        return []
    red = _ids(stats, "failed", "error")
    passed = _ids(stats, "passed") - red
    edge = {str(r.nodeid) for r in EDGE.passed_on_retry(stats)}
    mask = _mask_function()

    def ended(nodeid: str) -> str:
        if nodeid in red:
            return "it failed again"
        return "it passed on its retry" if nodeid in passed else "its retry did not pass"

    earlier: dict[str, Any] = {}
    parts: list[tuple[int, list[tuple[str, str]]]] = []
    for rep in reruns:
        nodeid = str(rep.nodeid)
        try:
            part = _attempt(rep, earlier.get(nodeid), ended(nodeid), nodeid in edge, mask)
        except Exception as exc:  # noqa: BLE001 — the other attempts still print
            part = [("_", f"{_one(nodeid, mask)} (attempt {int(getattr(rep, 'rerun', 0) or 0) + 1})"),
                    ("|", f"this attempt could not be written: {type(exc).__name__}")]
        earlier[nodeid] = rep
        parts.append((0 if nodeid in passed else 1, part))
    first = sorted(range(len(parts)), key=lambda i: parts[i][0])[:MOST_ATTEMPTS]
    out: list[tuple[str, str]] = []
    for i in sorted(first):
        out += parts[i][1]
    if len(parts) > len(first):
        out.append(("_", f"{len(parts) - len(first)} more failed attempts are not printed (the most is {MOST_ATTEMPTS})"))
    names = list(dict.fromkeys(str(r.nodeid) for r in reruns if str(r.nodeid) in passed))
    out.append(("_", f"{LISTED}: {len(names)}"))
    out += [("|", _one(name, mask)) for name in names]
    return out


@pytest.hookimpl(specname="pytest_terminal_summary")
def pytest_terminal_summary_retries(terminalreporter):
    """The block :data:`HEADING`; nothing when no attempt was run again (a
    local run never reruns). Never breaks the run."""
    tr = terminalreporter
    try:
        if not tr.stats.get("rerun"):
            return
        lines = build(tr.stats)
    except Exception as exc:  # noqa: BLE001 — the run's own result stands
        lines = [("|", f"the block could not be written: {type(exc).__name__}")]
    if not lines:
        return
    try:
        tr.write_sep("=", HEADING, yellow=True, bold=True)
        for kind, text in lines:
            if kind == "_":
                tr.write_sep("_", text)
            else:
                tr.write_line(MARK + text)
    except Exception:  # noqa: BLE001 — a closed terminal: nothing to write to
        pass
