"""Every test that fails an attempt and is run again leaves that attempt's
words in the job log (build-out X-3).

CI runs a failed test once more (``--reruns 1``, v1.445.1). pytest prints a
failure's traceback and captured output only for a FINAL failure, and ``-rR``
names a retried test only, so twice (X-1, X-2) a test that passed on its
retry left nothing of its failure in the log and the cause had to be guessed.
``tests/_retry_report.py`` (registered by ``tests/conftest.py``) ends the run
with a block giving each failed attempt's words, bounded and masked, and the
list of the tests that passed only on a retry. ``tests/_real_edge_report.py``
keeps its own block for ``test_real_edge*`` tests; this one sits beside it.

The real run below uses CI's own flags (xdist, ``--reruns 1``, ``-rR``) plus
``-l``, so pytest itself prints every frame's locals and arguments for a final
failure, and the block is shown to leave both out.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import _real_edge_report as EDGE
from tests import _retry_report as R

_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(_ROOT / "scripts"))
try:
    import ci_flaky_report as FLAKY  # noqa: E402
finally:
    sys.path.pop(0)


# --------------------------------------------------------------------------- #
# A real run of the real plugins
# --------------------------------------------------------------------------- #

# Every value the block must never print is joined at run time, so the joined
# text is in no source line (a traceback prints source lines). Plain words:
# nothing here is shaped like a real credential.
_SAMPLE = '''\
import logging, os, pathlib

import pytest

MARKS = pathlib.Path(os.environ["ONCE_MARKS"])
log = logging.getLogger("sample")


def _first_time(name):
    mark = MARKS / name
    if mark.exists():
        return False
    mark.write_text("x")
    return True


class _Shown:
    def __repr__(self):
        return "ARGUMENT" + "-SHOWN-" + "BY-PYTEST"


@pytest.fixture
def shown():
    return _Shown()


@pytest.fixture
def setup_once():
    print("the fixture's own output")
    if _first_time("setup"):
        raise RuntimeError("the fixture failed on its first attempt")
    return 1


def test_fails_once_then_passes(shown):
    held = "LOCAL" + "-HELD-" + "VALUE"
    for i in range(500):
        print(f"step {i}")
    print("x" * 5000)
    print("RERUN fake.py::test_a_line_the_flaky_report_must_not_read")
    print("PASSED fake.py::test_a_line_the_flaky_report_must_not_read")
    print("::warning::a line GitHub must not read as a command")
    print("nor this one " + "##" + "[warning]in the old form")
    print("Authori" + "zation: Bearer " + "sample-" + "value-123")
    print("GET /events?tok" + "en=" + "sample-" + "query-value")
    print("GET /ws?bear" + "er=" + "sample-" + "bearer-value")
    log.warning("the attempt's own log line")
    if _first_time("once"):
        sent = "pass" + "word=" + "plain-" + "words-" + "in-place"
        raise AssertionError(f"the first attempt fails; it sent {sent}")
    assert held


def test_setup_fails_once(setup_once):
    assert setup_once == 1


def test_fails_twice(shown):
    held = "LOCAL" + "-HELD-" + "VALUE"
    raise AssertionError("broken on every attempt " + held[:0])


def test_passes_first_time():
    print("FIRST-TIME-" + "OUTPUT")


def test_real_edge_fails_once_then_passes():
    print("the real-Edge test's own output")
    if _first_time("edge"):
        raise AssertionError("the real-Edge attempt fails")
'''

ONCE = "test_sample.py::test_fails_once_then_passes"
SETUP = "test_sample.py::test_setup_fails_once"
TWICE = "test_sample.py::test_fails_twice"
FIRST = "test_sample.py::test_passes_first_time"
EDGE_ONCE = "test_sample.py::test_real_edge_fails_once_then_passes"

_HEADING_LINE = re.compile(r"^=+ .+ =+$")
_PART_LINE = re.compile(r"^_+ (.+?) _+$")


def _registration() -> str:
    """The lines of ``tests/conftest.py`` that register both reports, so the
    sample run registers them exactly as the suite does."""
    text = (_ROOT / "tests" / "conftest.py").read_bytes().decode("utf-8").replace("\r\n", "\n")
    lines = [ln for ln in text.splitlines()
             if ln.startswith(("from tests._real_edge_report import", "from tests._retry_report import"))]
    assert len(lines) == 2, lines
    return "\n".join(lines) + "\n"


def _run(tmp_path: Path, *select: str) -> "subprocess.CompletedProcess[str]":
    run = tmp_path / "run"
    marks = tmp_path / "marks"
    run.mkdir()
    marks.mkdir()
    (run / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (run / "conftest.py").write_text(_registration(), encoding="utf-8")
    (run / "test_sample.py").write_text(_SAMPLE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_ROOT), env.get("PYTHONPATH", "")) if p)
    env["ONCE_MARKS"] = str(marks)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "-n", "1",
         "--reruns", "1", "--rerun-except", "pytest-timeout", "-rR", "-l", *select, "test_sample.py"],
        cwd=run, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=240, stdin=subprocess.DEVNULL,
    )


def _section(text: str, heading: str) -> list[str]:
    """The lines under a ``=== heading ===`` line, up to the next such line."""
    lines = text.splitlines()
    (start,) = [i for i, ln in enumerate(lines) if _HEADING_LINE.match(ln) and f" {heading} " in ln]
    rest = lines[start + 1:]
    end = next((i for i, ln in enumerate(rest) if _HEADING_LINE.match(ln)), len(rest))
    return rest[:end]


def _parts(block: list[str]) -> dict[str, list[str]]:
    """Each ``___ title ___`` part of the block: its heading line, then its lines."""
    parts: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in block:
        m = _PART_LINE.match(line)
        if m:
            current = parts.setdefault(m.group(1), [])
        if current is not None:
            current.append(line)
    return parts


def _part(parts: dict[str, list[str]], nodeid: str) -> list[str]:
    (title,) = [t for t in parts if t.startswith(f"{nodeid} (attempt ")]
    return parts[title]


@pytest.mark.timeout(300)
def test_a_test_that_passes_on_its_retry_prints_what_its_failed_attempt_said(tmp_path):
    res = _run(tmp_path)
    text = res.stdout + res.stderr
    assert res.returncode == 1, "the test red twice must fail the run:\n" + text

    block = _section(text, R.HEADING)
    joined = "\n".join(block)
    parts = _parts(block)

    # Each failed attempt that was run again has its own part, headed by the
    # test, the attempt, its phase and how the test ended.
    once = _part(parts, ONCE)
    assert once[0].strip("_ ") == f"{ONCE} (attempt 1, call; it passed on its retry)", once[0]
    setup = _part(parts, SETUP)
    assert setup[0].strip("_ ") == f"{SETUP} (attempt 1, setup; it passed on its retry)", setup[0]
    twice = _part(parts, TWICE)
    assert twice[0].strip("_ ") == f"{TWICE} (attempt 1, call; it failed again)", twice[0]
    # A test that passed the first time is in no part and nowhere in the block.
    assert "test_passes_first_time" not in joined and "FIRST-TIME-OUTPUT" not in joined, joined

    once_text = "\n".join(once)
    # The failure's own words, masked.
    assert f"{R.MARK}AssertionError: the first attempt fails; it sent password=***" in once, once_text
    # The end of its traceback: the line that raised, and where.
    assert any(ln.startswith(R.MARK) and "raise AssertionError(f\"the first attempt fails" in ln for ln in once), once_text
    assert any(re.search(r"test_sample\.py:\d+: AssertionError$", ln) for ln in once), once_text
    # The last lines of what it captured, the earliest left out.
    assert f"{R.MARK}step 499" in once and f"{R.MARK}step 0" not in once, once_text
    assert f"{R.MARK}the fixture's own output" in setup, "\n".join(setup)
    assert f"{R.MARK}RuntimeError: the fixture failed on its first attempt" in setup, "\n".join(setup)
    assert any("Captured log call" in ln for ln in once) and any(
        "the attempt's own log line" in ln for ln in once), once_text
    # Masked by the shared mask and the log's query redaction, before any cut.
    assert f"{R.MARK}Authorization: Bearer ***" in once, once_text
    # Only the log's query redaction knows this one.
    assert f"{R.MARK}GET /ws?bearer=REDACTED" in once, once_text
    for raw in ("plain-words-in-place", "sample-value-123", "sample-query-value", "sample-bearer-value"):
        assert raw not in joined, raw
    # Never a frame's arguments or locals, though pytest prints both (-l).
    assert "ARGUMENT-SHOWN-BY-PYTEST" not in joined and "LOCAL-HELD-VALUE" not in joined, joined
    failures = "\n".join(_section(text, "FAILURES"))
    assert "ARGUMENT-SHOWN-BY-PYTEST" in failures and "LOCAL-HELD-VALUE" in failures, failures

    # Bounded: at most MOST_LINES lines a part (its heading included), every
    # line at most MOST_WIDTH characters and the note that it was cut.
    for title, lines in parts.items():
        if "(attempt " in title:
            assert len(lines) <= R.MOST_LINES, (title, len(lines))
    assert max(len(ln) for ln in block) <= len(R.MARK) + R.MOST_WIDTH + 40, max(block, key=len)[:400]
    assert any("more characters left out" in ln for ln in once), once_text

    # Every line of the block is marked: no line of it can be read as a
    # GitHub workflow command, and none as a result by the flaky report.
    for line in block:
        assert _PART_LINE.match(line) or line.startswith(R.MARK), line
    assert not [ln for ln in text.splitlines() if ln.strip().startswith("::")], text
    assert f"{R.MARK}nor this one #-#[warning]in the old form" in once and "##[" not in text, once_text
    flaky, red = FLAKY.retried(text)
    assert sorted(flaky) == sorted([ONCE, SETUP, EDGE_ONCE]), text
    assert red == [TWICE], text

    # The run's own list of the tests that passed only on a retry.
    (listed,) = [t for t in parts if t.startswith("passed only on a retry")]
    assert listed == "passed only on a retry: 3", listed
    assert sorted(ln[len(R.MARK):] for ln in parts[listed][1:]) == sorted([ONCE, SETUP, EDGE_ONCE])

    # The real-Edge block is kept, and this block points at it instead of
    # printing the real-Edge test's sections a second time.
    edge_block = "\n".join(_section(text, EDGE.HEADING))
    assert f"{EDGE_ONCE} (attempt 1, call)" in edge_block and "busiest programs" in edge_block, edge_block
    edge = _part(parts, EDGE_ONCE)
    assert f"{R.MARK}AssertionError: the real-Edge attempt fails" in edge, "\n".join(edge)
    assert any(EDGE.HEADING in ln for ln in edge) and "busiest programs" not in joined, "\n".join(edge)


@pytest.mark.timeout(300)
def test_a_run_where_nothing_was_run_again_prints_nothing_more(tmp_path):
    res = _run(tmp_path, "-k", "passes_first_time")
    text = res.stdout + res.stderr
    assert res.returncode == 0, text
    assert "1 passed" in text, text
    assert R.HEADING not in text and EDGE.HEADING not in text, text
    assert not [ln for ln in text.splitlines() if ln.startswith(R.MARK.strip())], text


def test_the_suites_conftest_registers_the_retry_report(pytestconfig):
    impls = [impl.function for impl in pytestconfig.hook.pytest_terminal_summary.get_hookimpls()]
    assert R.pytest_terminal_summary_retries in impls, "the retry report is not registered"
    assert EDGE.pytest_terminal_summary in impls, "the real-Edge report must stay registered"


def test_the_run_page_says_where_the_words_are():
    real = "RERUN a.py::t\nPASSED a.py::t\n1 passed, 1 rerun in 1.0s\n"
    assert f'"{R.HEADING}"' in FLAKY.report(real, "shard 1")
    assert FLAKY.RETRY_HEADING == R.HEADING


# --------------------------------------------------------------------------- #
# The block itself, from reports built here
# --------------------------------------------------------------------------- #


class _Arg:
    def __repr__(self) -> str:
        return "ARG" + "-REPR-" + "MARK"


def _failed(nodeid: str, *, when: str = "call", rerun: int = 0, sections=()) -> SimpleNamespace:
    """A rerun report whose failure carries a frame's arguments and locals."""

    def frame(argument):
        kept = "LOCAL" + "-IN-" + "FRAME"
        raise AssertionError("it sent " + "pass" + "word=" + "plain-" + "words " + kept[:0])

    try:
        frame(_Arg())
    except AssertionError as exc:
        info = pytest.ExceptionInfo.from_exception(exc)
    longrepr = info.getrepr(showlocals=True, funcargs=True, style="long")
    return SimpleNamespace(nodeid=nodeid, when=when, rerun=rerun, outcome="rerun",
                           longrepr=longrepr, sections=list(sections))


def _passed(nodeid: str) -> SimpleNamespace:
    return SimpleNamespace(nodeid=nodeid, when="call", outcome="passed")


def _texts(stats) -> list[str]:
    return [text for kind, text in R.build(stats) if kind == "|"]


def test_the_block_never_prints_a_frames_arguments_or_locals():
    rep = _failed("a.py::t")
    full = str(rep.longrepr)
    assert "ARG-REPR-MARK" in full and "LOCAL-IN-FRAME" in full, "the probe must carry both"
    texts = "\n".join(_texts({"rerun": [rep], "passed": [_passed("a.py::t")]}))
    assert "ARG-REPR-MARK" not in texts and "LOCAL-IN-FRAME" not in texts, texts
    assert "AssertionError: it sent password=***" in texts and "plain-words" not in texts, texts
    assert "raise AssertionError(" in texts, texts


def test_a_line_that_cannot_be_masked_is_left_out(monkeypatch):
    rep = _failed("a.py::t", sections=[("Captured stdout call", "pass" + "word=" + "plain-words\n")])
    stats = {"rerun": [rep], "passed": [_passed("a.py::t")]}

    def broken(text):
        raise RuntimeError("the mask broke")

    monkeypatch.setattr(R, "_mask_function", lambda: broken)
    texts = _texts(stats)
    assert texts and "plain-words" not in "\n".join(texts) and "password" not in "\n".join(texts), texts
    assert R.LEFT_OUT in texts, texts
    # The mask could not even be loaded: the same.
    monkeypatch.setattr(R, "_mask_function", lambda: None)
    texts = _texts(stats)
    assert "plain-words" not in "\n".join(texts) and R.LEFT_OUT in texts, texts


def test_each_attempt_is_bounded_and_so_is_the_number_printed():
    noisy = [("Captured stdout call", "".join(f"out {i} " + "y" * 900 + "\n" for i in range(400))),
             ("Captured log call", "".join(f"log {i}\n" for i in range(300))),
             ("Captured stderr call", "one line\n")]
    reruns = [_failed(f"a.py::t{i}", sections=noisy) for i in range(R.MOST_ATTEMPTS + 5)]
    stats = {"rerun": reruns, "passed": [_passed(r.nodeid) for r in reruns]}
    out = R.build(stats)
    heads = [i for i, (kind, text) in enumerate(out) if kind == "_" and "(attempt " in text]
    assert len(heads) == R.MOST_ATTEMPTS, len(heads)
    ends = heads[1:] + [next(i for i, (kind, text) in enumerate(out) if i > heads[-1] and kind == "_")]
    for start, end in zip(heads, ends):
        part = out[start:end]
        assert len(part) <= R.MOST_LINES, len(part)
        # Every section keeps its title and its own last lines.
        titles = [text for _, text in part if text.startswith("-- Captured")]
        assert len(titles) == 3, titles
        assert "one line" in [text for _, text in part]
    assert all(len(text) <= R.MOST_WIDTH + 40 for _, text in out), max(out, key=lambda x: len(x[1]))
    assert any("5 more failed attempts are not printed" in text for _, text in out), out[-12:]
    # Every test is still named in the list of those that passed on a retry.
    listed = out[[i for i, (kind, text) in enumerate(out) if text.startswith("passed only on a retry")][0] + 1:]
    assert [text for _, text in listed] == [r.nodeid for r in reruns]


def test_past_the_most_attempts_a_test_that_passed_on_its_retry_is_printed_first():
    """A test that failed again also gets pytest's own FAILURES section; one
    that passed on its retry has only this block, so it is never the one left
    out, even when it comes last."""
    red = [_failed(f"a.py::red{i}") for i in range(R.MOST_ATTEMPTS)]
    flaky = _failed("a.py::flaky")
    stats = {"rerun": [*red, flaky], "failed": [SimpleNamespace(nodeid=r.nodeid) for r in red],
             "passed": [_passed(flaky.nodeid)]}
    out = R.build(stats)
    heads = [text for kind, text in out if kind == "_" and "(attempt " in text]
    assert len(heads) == R.MOST_ATTEMPTS, heads
    assert "a.py::flaky (attempt 1, call; it passed on its retry)" in heads, heads
    assert sum(text.endswith("; it failed again)") for text in heads) == R.MOST_ATTEMPTS - 1, heads
    assert ("_", f"1 more failed attempts are not printed (the most is {R.MOST_ATTEMPTS})") in out, out[-6:]
    assert out[-2:] == [("_", "passed only on a retry: 1"), ("|", "a.py::flaky")], out[-2:]


def test_nothing_run_again_prints_nothing():
    assert R.build({"passed": [_passed("a.py::t")]}) == []
    assert R.build({}) == []


def test_a_later_attempt_prints_only_what_it_captured_itself():
    first = _failed("a.py::t", rerun=0, sections=[("Captured stdout call", "attempt one said this\n")])
    second = _failed("a.py::t", rerun=1, sections=[("Captured stdout call", "attempt one said this\n"),
                                                   ("Captured stdout call", "attempt two said that\n")])
    out = R.build({"rerun": [first, second], "passed": [_passed("a.py::t")]})
    heads = [i for i, (kind, text) in enumerate(out) if kind == "_" and "(attempt " in text]
    later = [text for _, text in out[heads[1]:]]
    assert "attempt two said that" in later and "attempt one said this" not in later, later
    assert out[heads[1]][1] == "a.py::t (attempt 2, call; it passed on its retry)", out[heads[1]]
    # The earlier attempt's teardown ran after its report was built: it is
    # that attempt's, not the later one's (--reruns 2: CI runs 1).
    torn = ("Captured stdout teardown", "attempt one tore down\n")
    second.sections = [*first.sections, torn, ("Captured stdout setup", "attempt two set up\n"),
                       ("Captured stdout call", "attempt two said that\n")]
    out = R.build({"rerun": [first, second], "passed": [_passed("a.py::t")]})
    later = [text for _, text in out[[i for i, (kind, text) in enumerate(out) if "(attempt 2" in text][0]:]]
    assert "attempt one tore down" not in later and "attempt two set up" in later, later
    # Its own teardown that failed is its own (a rerun report of the same attempt).
    torn_rep = _failed("a.py::t", when="teardown", rerun=0, sections=[*first.sections, torn])
    out = R.build({"rerun": [first, torn_rep], "passed": [_passed("a.py::t")]})
    part = [text for _, text in out[[i for i, (kind, text) in enumerate(out) if "(attempt 1, teardown" in text][0]:]]
    assert "attempt one tore down" in part and "attempt one said this" not in part, part


def test_a_failure_that_is_plain_text_prints_its_last_lines():
    """xdist sends a failure with no traceback (a missing fixture) as text."""
    text = "".join(f"line {i}\n" for i in range(40)) + "E       fixture 'nothere' not found\n" + \
        "a.py:3\n"
    rep = SimpleNamespace(nodeid="a.py::t", when="setup", rerun=0, outcome="rerun", longrepr=text, sections=[])
    texts = _texts({"rerun": [rep], "passed": [_passed("a.py::t")]})
    assert "E       fixture 'nothere' not found" in texts and "a.py:3" in texts, texts
    assert "line 0" not in texts and any("earlier lines of the traceback left out" in t for t in texts), texts


def test_a_huge_line_is_masked_whole_or_left_out_never_cut_before_the_mask():
    value = "pass" + "word=" + "z" * (R._LONG_LINE + 10)
    small = _failed("a.py::small", sections=[("Captured stdout call", value + "\n")])
    texts = _texts({"rerun": [small], "passed": [_passed("a.py::small")]})
    assert "password=***" in texts and not any("zzzz" in t for t in texts), texts
    # More to mask than the bound: a long line is left out, said so; a short one still prints.
    filler = "".join(f"filler {i} " + "q" * 15000 + "\n" for i in range(150))
    big = _failed("a.py::big", sections=[("Captured stdout call", filler + value + "\nthe end\n")])
    texts = _texts({"rerun": [big], "passed": [_passed("a.py::big")]})
    assert f"(a line of {len(value)} characters is left out)" in texts and "the end" in texts, texts
    assert not any("zzzz" in t for t in texts), texts


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_sep(self, sep, title, **markup):
        self.lines.append(f"{sep * 3} {title} {sep * 3}")

    def write_line(self, line, **markup):
        self.lines.append(line)


def test_a_report_that_cannot_be_read_does_not_hide_the_others():
    class _Broken:
        @property
        def chain(self):
            raise RuntimeError("a report pytest never builds")

    broken = SimpleNamespace(nodeid="a.py::broken", when="call", rerun=0, outcome="rerun",
                             longrepr=_Broken(), sections=[])
    good = _failed("a.py::good")
    out = R.build({"rerun": [broken, good], "passed": [_passed("a.py::broken"), _passed("a.py::good")]})
    texts = [text for _, text in out]
    assert "its words could not be read: RuntimeError" in texts, texts
    assert "AssertionError: it sent password=***" in texts, texts


def test_the_hook_never_breaks_the_run(monkeypatch):
    def broken(stats):
        raise RuntimeError("the block broke")

    monkeypatch.setattr(R, "build", broken)
    tr = _Writer()
    tr.stats = {"rerun": [_failed("a.py::t")]}
    R.pytest_terminal_summary_retries(tr)
    assert tr.lines == [f"=== {R.HEADING} ===", f"{R.MARK}the block could not be written: RuntimeError"], tr.lines
    # And nothing at all when nothing was run again.
    monkeypatch.undo()
    tr = _Writer()
    tr.stats = {"passed": [_passed("a.py::t")]}
    R.pytest_terminal_summary_retries(tr)
    assert tr.lines == []
