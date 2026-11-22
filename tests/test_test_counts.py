"""22-a (R1.9): how many tests ran and passed, read from a test runner's OWN
summary (``harness/test_counts``).

Each case is the summary block the runner prints, as captured from its real
output (pytest, unittest, jest, vitest, mocha, go test -v, cargo test,
dotnet test, phpunit). A number is never guessed: output no runner's summary
explains, two different runners' summaries in one output, or ``go test``
without ``-v`` (it lists no passing test) give no number at all.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from iron_jarvis.harness import test_counts as TC

PYTEST_FAILED = textwrap.dedent("""\
    ============================= test session starts =============================
    platform win32 -- Python 3.12.4, pytest-8.3.2, pluggy-1.5.0
    rootdir: C:\\work\\shop
    collected 3 items

    test_limits.py .F.                                                       [100%]

    ================================== FAILURES ===================================
    _________________________________ test_burst __________________________________

        def test_burst():
    >       assert limit(5) == 4
    E       assert 5 == 4

    test_limits.py:9: AssertionError
    =========================== short test summary info ===========================
    FAILED test_limits.py::test_burst - assert 5 == 4
    ========================= 1 failed, 2 passed in 0.05s =========================
""")

PYTEST_QUIET = "..s.x\n4 passed, 1 skipped, 1 xfailed, 2 warnings in 1.31s\n"
PYTEST_NONE = "\n============================ no tests ran in 0.01s ============================\n"
PYTEST_ERROR = (
    "==================================== ERRORS ====================================\n"
    "ERROR collecting test_x.py\n"
    "=========================== short test summary info ============================\n"
    "ERROR test_x.py\n"
    "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
    "=============================== 1 error in 0.12s ===============================\n"
)

UNITTEST_FAILED = textwrap.dedent("""\
    .F.s
    ======================================================================
    FAIL: test_b (test_x.T.test_b)
    ----------------------------------------------------------------------
    Traceback (most recent call last):
      File "C:\\work\\test_x.py", line 8, in test_b
        self.assertEqual(1, 2)
    AssertionError: 1 != 2

    ----------------------------------------------------------------------
    Ran 4 tests in 0.002s

    FAILED (failures=1, skipped=1)
""")
UNITTEST_OK = "..\n----------------------------------------------------------------------\nRan 2 tests in 0.000s\n\nOK\n"

JEST = textwrap.dedent("""\
     PASS  src/sum.test.js
     FAIL  src/limit.test.js
      ● limit › stops at four

    Test Suites: 1 failed, 1 passed, 2 total
    Tests:       1 failed, 1 skipped, 3 passed, 5 total
    Snapshots:   0 total
    Time:        1.204 s
    Ran all test suites.
""")

VITEST = (
    " \x1b[32m✓\x1b[39m src/sum.test.ts \x1b[2m(2 tests)\x1b[22m 3ms\n"
    " \x1b[31m❯\x1b[39m src/limit.test.ts \x1b[2m(2 tests | 1 failed)\x1b[22m 5ms\n"
    "\n"
    "\x1b[2m Test Files \x1b[22m \x1b[1m\x1b[31m1 failed\x1b[39m\x1b[22m\x1b[2m | \x1b[22m\x1b[1m\x1b[32m1 passed\x1b[39m\x1b[22m\x1b[90m (2)\x1b[39m\n"
    "\x1b[2m      Tests \x1b[22m \x1b[1m\x1b[31m1 failed\x1b[39m\x1b[22m\x1b[2m | \x1b[22m\x1b[1m\x1b[32m3 passed\x1b[39m\x1b[22m\x1b[90m (4)\x1b[39m\n"
    "\x1b[2m   Start at \x1b[22m 10:02:11\n"
    "\x1b[2m   Duration \x1b[22m 512ms\n"
)

MOCHA = textwrap.dedent("""\

      limit
        ✔ allows four
        1) stops at five
        - waits (pending)


      1 passing (12ms)
      1 pending
      1 failing

      1) limit
           stops at five:
         AssertionError [ERR_ASSERTION]: 5 == 4
""")

GO_VERBOSE = textwrap.dedent("""\
    === RUN   TestAllow
    --- PASS: TestAllow (0.00s)
    === RUN   TestBurst
    === RUN   TestBurst/five
        limit_test.go:21: got 5, want 4
    --- FAIL: TestBurst (0.00s)
        --- FAIL: TestBurst/five (0.00s)
    === RUN   TestReset
    --- PASS: TestReset (0.00s)
    FAIL
    exit status 1
    FAIL\texample.com/shop/limit\t0.003s
""")
GO_QUIET = "ok  \texample.com/shop/limit\t0.002s\nok  \texample.com/shop/api\t0.004s\n"

CARGO = textwrap.dedent("""\
    running 3 tests
    test tests::allows ... ok
    test tests::burst ... FAILED
    test tests::slow ... ignored

    failures:

    ---- tests::burst stdout ----
    thread 'tests::burst' panicked at src/lib.rs:20:9

    test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s

       Doc-tests shop

    running 2 tests
    test src/lib.rs - limit (line 3) ... ok
    test src/lib.rs - reset (line 9) ... ok

    test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.21s
""")

DOTNET = (
    "  Determining projects to restore...\n"
    "  Shop.Tests -> C:\\work\\shop\\bin\\Debug\\net8.0\\Shop.Tests.dll\n"
    "Failed!  - Failed:     1, Passed:     6, Skipped:     1, Total:     8, Duration: 41 ms - Shop.Tests.dll (net8.0)\n"
    "Passed!  - Failed:     0, Passed:     3, Skipped:     0, Total:     3, Duration: 9 ms - Api.Tests.dll (net8.0)\n"
)
DOTNET9 = "Test summary: total: 5, failed: 2, succeeded: 3, skipped: 0, duration: 1.4s\nBuild failed with 2 error(s)\n"

PHPUNIT_OK = "PHPUnit 11.4.0 by Sebastian Bergmann and contributors.\n\n...    3 / 3 (100%)\n\nTime: 00:00.012, Memory: 8.00 MB\n\nOK (3 tests, 5 assertions)\n"
PHPUNIT_FAILED = (
    "PHPUnit 10.5.2 by Sebastian Bergmann and contributors.\n\n.FES   4 / 4 (100%)\n\n"
    "FAILURES!\nTests: 4, Assertions: 4, Errors: 1, Failures: 1, Skipped: 1.\n"
)


@pytest.mark.parametrize(
    ("output", "runner", "ran", "passed", "failed"),
    [
        (PYTEST_FAILED, "pytest", 3, 2, 1),
        (PYTEST_QUIET, "pytest", 5, 5, 0),
        (PYTEST_NONE, "pytest", 0, 0, 0),
        (PYTEST_ERROR, "pytest", 1, 0, 1),
        (UNITTEST_FAILED, "unittest", 3, 2, 1),
        (UNITTEST_OK, "unittest", 2, 2, 0),
        (JEST, "jest", 4, 3, 1),
        (VITEST, "vitest", 4, 3, 1),
        (MOCHA, "mocha", 2, 1, 1),
        (GO_VERBOSE, "go test", 3, 2, 1),
        (CARGO, "cargo test", 4, 3, 1),
        (DOTNET, "dotnet test", 10, 9, 1),
        (DOTNET9, "dotnet test", 5, 3, 2),
        (PHPUNIT_OK, "phpunit", 3, 3, 0),
        (PHPUNIT_FAILED, "phpunit", 3, 1, 2),
    ],
)
def test_each_runners_own_summary_gives_its_counts(output, runner, ran, passed, failed):
    got = TC.read_counts(output)
    assert got is not None, runner
    assert (got.runner, got.ran, got.passed, got.failed) == (runner, ran, passed, failed)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "Build succeeded.\n    0 Warning(s)\n    0 Error(s)\n",
        "All good: 14 passed checks, nothing to report\n",  # prose, not a summary line
        GO_QUIET,  # go test without -v never lists a passing test
        "> shop@1.0.0 test\n> node scripts/check.js\n\ndone\n",
        PYTEST_QUIET + UNITTEST_OK,  # two runners: which number? none is said
    ],
)
def test_output_no_runner_explains_gives_no_number(output):
    assert TC.read_counts(output) is None


def test_the_words_are_the_counts_and_nothing_more():
    assert TC.counts_words(TC.Counts("pytest", 14, 14, 0)) == "14 tests ran, 14 passed"
    assert TC.counts_words(TC.Counts("unittest", 1, 0, 1)) == "1 test ran, 0 passed"
    assert TC.counts_words(TC.Counts("pytest", 0, 0, 0)) == "no tests ran"


VITEST_PASSED = "      Tests  10 passed (10)\n"
VITEST_FAILED = "      Tests  2 failed | 3 passed (5)\n"


@pytest.mark.parametrize(
    "output",
    [
        # 22-a review: the SAME runner twice in one command (``vitest run &&
        # vitest run -c e2e``, ``pytest a; pytest b``, ``npm test
        # --workspaces``): the last run's count is never said as the whole
        # command's, and a failed first run is never hidden behind a pass.
        VITEST_FAILED + VITEST_PASSED,
        VITEST_PASSED + VITEST_FAILED,
        "1 failed, 9 passed in 0.10s\n4 passed in 0.01s\n",
        PYTEST_FAILED + PYTEST_QUIET,
        PYTEST_NONE + PYTEST_QUIET,
        JEST + "Tests:       2 passed, 2 total\n",
        UNITTEST_FAILED + UNITTEST_OK,
        MOCHA + "  4 passing (3ms)\n",
        PHPUNIT_FAILED + PHPUNIT_OK,
        DOTNET9 + "Test summary: total: 2, failed: 0, succeeded: 2, skipped: 0, duration: 0.4s\n",
    ],
)
def test_one_runner_run_twice_gives_no_number(output):
    assert TC.read_counts(output) is None


def test_the_runners_that_print_one_summary_per_part_are_added_up():
    """cargo (unit tests, doc tests), dotnet (one line per test project) and
    go test -v (each test's own line) print one summary per PART of one run:
    those are added up, inside the output read."""
    assert TC.read_counts(CARGO + CARGO) == TC.Counts("cargo test", 8, 6, 2)
    assert TC.read_counts(DOTNET + DOTNET) == TC.Counts("dotnet test", 20, 18, 2)


NOISE = ("x" * 200 + "\n") * 2_000  # longer than the tail


def test_only_the_end_of_a_long_output_is_parsed():
    assert len(NOISE) > TC.TAIL_CHARS
    got = TC.read_counts(NOISE + PYTEST_QUIET)
    assert got == TC.Counts("pytest", 5, 5, 0)
    # A long single run whose earlier part holds no summary keeps its count
    # (pytest -v names every test; none of those lines is a summary).
    verbose = "".join(f"tests/test_x.py::test_{i} PASSED [ 1%]\n" for i in range(3_000))
    assert TC.read_counts(verbose + "3000 passed in 4.20s\n") == TC.Counts("pytest", 3000, 3000, 0)


@pytest.mark.parametrize(
    "before",
    [
        # 22-a review: a summary cut off before the tail (an earlier run, or
        # an earlier part of a sum) gives no number, never a partial one.
        "Tests: 9, Assertions: 9.\n",
        "1 failed, 9 passed in 0.10s\n",
        "\x1b[31m1 failed\x1b[0m, \x1b[32m9 passed\x1b[0m\x1b[32m in 0.10s\x1b[0m\n",
        "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out\n",
        "Failed!  - Failed:     1, Passed:     6, Skipped:     1, Total:     8, Duration: 41 ms - A.dll\n",
        "=== RUN   TestBurst\n--- FAIL: TestBurst (0.00s)\n",
        "      Tests  2 failed | 3 passed (5)\n",
        "Tests:       1 failed, 3 passed, 4 total\n",
        "Ran 4 tests in 0.002s\n\nFAILED (failures=1)\n",
        "  1 passing (12ms)\n  1 failing\n",
    ],
)
def test_a_summary_cut_off_before_the_tail_gives_no_number(before):
    assert TC.read_counts(before + NOISE + PYTEST_QUIET) is None
    assert TC.read_counts(before + NOISE + CARGO) is None


def test_a_cut_inside_a_summary_line_is_never_read_as_a_summary():
    """The tail starts inside "1 failed, 9 passed in 0.10s": the part after
    the cut alone would read as "9 passed"."""
    rest = "9 passed in 0.10s\n" + ("z" * 99 + "\n") * 600
    rest += "w" * (TC.TAIL_CHARS - len(rest))
    assert len(rest) == TC.TAIL_CHARS
    # The same tail after a WHOLE line: its first line is read.
    assert TC.read_counts("start\n" + rest) == TC.Counts("pytest", 9, 9, 0)
    assert TC.read_counts("1 failed, " + rest) is None


def test_an_output_too_long_to_search_gives_no_number(monkeypatch):
    before_tail = len(NOISE) + 1 + len(PYTEST_QUIET) - TC.TAIL_CHARS
    monkeypatch.setattr(TC, "HEAD_CHARS", before_tail - 1_000)
    assert TC.read_counts(NOISE + "\n" + PYTEST_QUIET) is None
    monkeypatch.setattr(TC, "HEAD_CHARS", 16_000_000)
    assert TC.read_counts(NOISE + "\n" + PYTEST_QUIET) == TC.Counts("pytest", 5, 5, 0)


def test_hostile_lines_are_read_in_linear_time():
    """A summary-shaped line padded to the length limit, in a child process
    with a deadline: no reading here may backtrack."""
    code = (
        "from iron_jarvis.harness import test_counts as TC\n"
        "line = '1 passed, ' * 30000 + 'in'\n"
        "TC.read_counts(('=' * 5000 + ' ' + line + ' ' + '=' * 5000 + '\\n') * 4)\n"
        "TC.read_counts('Tests:' + ' ' * 60000 + '1 failed,' * 9000)\n"
        "TC.read_counts('test result: ok. ' + '1 passed; ' * 20000)\n"
        "TC.read_counts('Test summary: total: ' + '9' * 100000)\n"
        # The search of the part before the tail (22-a review), on look-alikes.
        "noise = ('x' * 200 + '\\n') * 400\n"
        "TC.read_counts('9' * 3_000_000 + ' passing (' + noise + '4 passed in 0.01s\\n')\n"
        "TC.read_counts('passed in ' + '9' * 3_000_000 + noise + '4 passed in 0.01s\\n')\n"
        "TC.read_counts('Tests: ' + ' ' * 3_000_000 + noise + '4 passed in 0.01s\\n')\n"
        "TC.read_counts('\\x1b[' + ';' * 3_000_000 + noise + '4 passed in 0.01s\\n')\n"
        "print('ok')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert done.stdout.strip() == "ok", done.stderr
