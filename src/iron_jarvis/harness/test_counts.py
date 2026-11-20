"""How many tests RAN and PASSED, read from a test runner's OWN summary
(22-a, R1.9: "Checked by Iron Jarvis: 14 tests ran, 14 passed.").

Iron Jarvis runs a folder's tests after a coding tool's reply
(``hosted/proof``) and says under the reply what happened. The count comes
from the summary lines the runner itself prints, never from a tool's words
and never estimated:

* pytest      ``1 failed, 2 passed in 0.05s`` (the ``=`` rule around it or not)
* unittest    ``Ran 4 tests in 0.002s`` and the ``OK`` / ``FAILED (…)`` after it
* jest        ``Tests:       1 failed, 3 passed, 4 total``
* vitest      ``Tests  1 failed | 3 passed (4)``
* mocha       ``2 passing (12ms)`` / ``1 failing`` / ``1 pending``
* go test     ``-v`` only: the top-level ``--- PASS:`` / ``--- FAIL:`` lines,
              exactly one per top-level ``=== RUN`` (without ``-v`` go lists
              no passing test, so there is no count)
* cargo test  every ``test result: …`` line, added up (unit tests, doc tests)
* dotnet test ``Test summary: total: …`` (.NET 9), else every
              ``Passed!/Failed!  - Failed: …`` line, added up
* phpunit     ``OK (3 tests, 5 assertions)`` / ``Tests: 4, Assertions: …``

NOT GUESSED. Output none of these explains gives None, and so does output
in which TWO different runners' summaries appear (``pytest && npm test``:
which number would be the answer?). The caller then says only whether the
command ended with an error (its exit code). Skipped, ignored, pending and
to-do tests did not run; an error is a failure; an expected failure
(``xfail``) is a pass, as each runner itself counts it.

Pure. Only the END of the output is read (:data:`TAIL_CHARS`) and a line
longer than :data:`LINE_CHARS` is skipped; every pattern is anchored with no
two repeats that can match the same text, so the time is linear in the input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Only this much of the END of the output is read (a summary is at the end).
TAIL_CHARS = 64_000
#: A longer line is never a summary line.
LINE_CHARS = 1_000

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class Counts:
    """One runner's summary: ``ran`` = ``passed`` + ``failed``."""

    runner: str
    ran: int
    passed: int
    failed: int


def counts_words(c: Counts) -> str:
    """"14 tests ran, 14 passed" — the count and nothing more."""
    if c.ran == 0:
        return "no tests ran"
    return f"{c.ran} test{'' if c.ran == 1 else 's'} ran, {c.passed} passed"


def _lines(output: str) -> list[str]:
    text = _ANSI.sub("", str(output or "")[-TAIL_CHARS:])
    return [ln.rstrip("\r") for ln in text.split("\n") if len(ln) <= LINE_CHARS]


def _items(text: str, sep: str) -> dict[str, int]:
    """``"1 failed, 2 passed"`` → ``{"failed": 1, "passed": 2}``."""
    out: dict[str, int] = {}
    for part in text.split(sep):
        bits = part.strip().split(" ", 1)
        if len(bits) == 2 and bits[0].isdigit():
            out[bits[1].strip().lower()] = out.get(bits[1].strip().lower(), 0) + int(bits[0])
    return out


# --------------------------------------------------------------------------- #
# pytest
# --------------------------------------------------------------------------- #
_PYTEST_WORDS = frozenset({
    "passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed", "deselected", "warning",
    "warnings", "rerun", "reruns",
})
_PYTEST = re.compile(r"^(\d+ [a-z]+(?:, \d+ [a-z]+)*) in \d+(?:\.\d+)?s(?: \(\d+:\d\d:\d\d\))?$")
_PYTEST_NONE = re.compile(r"^no tests ran in \d+(?:\.\d+)?s$")


def _pytest(lines: list[str]) -> "Counts | None":
    for line in reversed(lines):
        s = line.strip().strip("=").strip()
        if _PYTEST_NONE.match(s):
            return Counts("pytest", 0, 0, 0)
        m = _PYTEST.match(s)
        if m is None:
            continue
        got = _items(m.group(1), ",")
        if not set(got) <= _PYTEST_WORDS:
            continue
        passed = got.get("passed", 0) + got.get("xfailed", 0) + got.get("xpassed", 0)
        failed = got.get("failed", 0) + got.get("error", 0) + got.get("errors", 0)
        return Counts("pytest", passed + failed, passed, failed)
    return None


# --------------------------------------------------------------------------- #
# unittest
# --------------------------------------------------------------------------- #
_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in \d+(?:\.\d+)?s$")
_UNITTEST_END = re.compile(r"^(OK|FAILED)(?: \(([a-z =0-9,]*)\))?$")


def _unittest(lines: list[str]) -> "Counts | None":
    for i in range(len(lines) - 1, -1, -1):
        m = _UNITTEST_RAN.match(lines[i].strip())
        if m is None:
            continue
        total = int(m.group(1))
        for after in lines[i + 1:i + 4]:
            s = after.strip()
            if s == "NO TESTS RAN" and total == 0:
                return Counts("unittest", 0, 0, 0)
            e = _UNITTEST_END.match(s)
            if e is None:
                continue
            bits: dict[str, int] = {}
            for part in (e.group(2) or "").split(","):
                k, _, v = part.partition("=")
                if v.strip().isdigit():
                    bits[k.strip()] = int(v)
            ran = total - bits.get("skipped", 0)
            failed = bits.get("failures", 0) + bits.get("errors", 0) + bits.get("unexpected successes", 0)
            if ran < 0 or failed > ran:
                return None
            return Counts("unittest", ran, ran - failed, failed)
        return None
    return None


# --------------------------------------------------------------------------- #
# jest / vitest / mocha
# --------------------------------------------------------------------------- #
_JEST = re.compile(r"^Tests:\s+((?:\d+ [a-z]+, )*)(\d+) total$")
_VITEST = re.compile(r"^Tests\s+(\d+ [a-z]+(?: \| \d+ [a-z]+)*) \((\d+)\)$")
_MOCHA_PASSING = re.compile(r"^(\d+) passing \(\d+(?:\.\d+)?(?:ms|s|m|h)\)$")
_MOCHA_FAILING = re.compile(r"^(\d+) failing$")


def _jest(lines: list[str]) -> "Counts | None":
    for line in reversed(lines):
        m = _JEST.match(line.strip())
        if m is None:
            continue
        got = _items(m.group(1), ",")
        passed, failed = got.get("passed", 0), got.get("failed", 0)
        if passed + failed + got.get("skipped", 0) + got.get("todo", 0) != int(m.group(2)):
            return None
        return Counts("jest", passed + failed, passed, failed)
    return None


def _vitest(lines: list[str]) -> "Counts | None":
    for line in reversed(lines):
        m = _VITEST.match(line.strip())
        if m is None:
            continue
        got = _items(m.group(1), "|")
        passed, failed = got.get("passed", 0), got.get("failed", 0)
        return Counts("vitest", passed + failed, passed, failed)
    return None


def _mocha(lines: list[str]) -> "Counts | None":
    for i in range(len(lines) - 1, -1, -1):
        m = _MOCHA_PASSING.match(lines[i].strip())
        if m is None:
            continue
        passed = int(m.group(1))
        failed = 0
        for after in lines[i + 1:i + 4]:
            f = _MOCHA_FAILING.match(after.strip())
            if f is not None:
                failed = int(f.group(1))
                break
        return Counts("mocha", passed + failed, passed, failed)
    return None


# --------------------------------------------------------------------------- #
# go test -v / cargo test / dotnet test / phpunit
# --------------------------------------------------------------------------- #
_GO_RUN = re.compile(r"^=== RUN\s+([^\s/]+)$")
_GO_END = re.compile(r"^--- (PASS|FAIL|SKIP): ([^\s/]+) \(")


def _go(lines: list[str]) -> "Counts | None":
    runs = 0
    passed = failed = skipped = 0
    for line in lines:
        if _GO_RUN.match(line):
            runs += 1
            continue
        m = _GO_END.match(line)  # column 0 only: a subtest's line is indented
        if m is None:
            continue
        if m.group(1) == "PASS":
            passed += 1
        elif m.group(1) == "FAIL":
            failed += 1
        else:
            skipped += 1
    if runs == 0 or runs != passed + failed + skipped:
        return None  # not verbose, or a test with no result line (a panic)
    return Counts("go test", passed + failed, passed, failed)


_CARGO = re.compile(
    r"^test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; \d+ ignored; \d+ measured; "
    r"\d+ filtered out(?:; finished in \d+(?:\.\d+)?s)?$"
)


def _cargo(lines: list[str]) -> "Counts | None":
    passed = failed = 0
    seen = False
    for line in lines:
        m = _CARGO.match(line.strip())
        if m is not None:
            seen = True
            passed += int(m.group(1))
            failed += int(m.group(2))
    return Counts("cargo test", passed + failed, passed, failed) if seen else None


_DOTNET = re.compile(
    r"^(?:Passed|Failed)!\s+-\s+Failed:\s+(\d+), Passed:\s+(\d+), Skipped:\s+\d+, Total:\s+\d+(?:,[^\n]*)?$"
)
_DOTNET9 = re.compile(
    r"^Test summary: total: (\d+), failed: (\d+), succeeded: (\d+), skipped: \d+(?:,[^\n]*)?$", re.IGNORECASE
)


def _dotnet(lines: list[str]) -> "Counts | None":
    for line in reversed(lines):
        m = _DOTNET9.match(line.strip())
        if m is not None:
            failed, passed = int(m.group(2)), int(m.group(3))
            return Counts("dotnet test", passed + failed, passed, failed)
    passed = failed = 0
    seen = False
    for line in lines:
        m = _DOTNET.match(line.strip())
        if m is not None:
            seen = True
            failed += int(m.group(1))
            passed += int(m.group(2))
    return Counts("dotnet test", passed + failed, passed, failed) if seen else None


_PHPUNIT_OK = re.compile(r"^OK \((\d+) tests?, \d+ assertions?\)$")
_PHPUNIT = re.compile(r"^Tests: (\d+), Assertions: \d+((?:, [A-Za-z ]+: \d+)*)\.$")


def _phpunit(lines: list[str]) -> "Counts | None":
    for line in reversed(lines):
        s = line.strip()
        m = _PHPUNIT_OK.match(s)
        if m is not None:
            n = int(m.group(1))
            return Counts("phpunit", n, n, 0)
        m = _PHPUNIT.match(s)
        if m is None:
            continue
        bits: dict[str, int] = {}
        for part in m.group(2).split(","):
            k, _, v = part.partition(":")
            if v.strip().isdigit():
                bits[k.strip().lower()] = int(v)
        ran = int(m.group(1)) - bits.get("skipped", 0) - bits.get("incomplete", 0)
        failed = bits.get("errors", 0) + bits.get("failures", 0)
        if ran < 0 or failed > ran:
            return None
        return Counts("phpunit", ran, ran - failed, failed)
    return None


_READERS = (_pytest, _unittest, _jest, _vitest, _mocha, _go, _cargo, _dotnet, _phpunit)


def read_counts(output: str) -> "Counts | None":
    """The counts ONE runner's own summary gives, or None (no summary, or
    summaries of two different runners). Pure; never raises."""
    try:
        lines = _lines(output)
        found = [c for c in (read(lines) for read in _READERS) if c is not None]
    except Exception:  # noqa: BLE001 — an unreadable output gives no number
        return None
    if len(found) != 1:
        return None
    return found[0]


__all__ = ["Counts", "LINE_CHARS", "TAIL_CHARS", "counts_words", "read_counts"]
