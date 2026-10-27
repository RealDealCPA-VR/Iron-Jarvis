"""v1.400.0 review — the team tier, attacked.

Pinned here (each red before its fix, or a property the review proved):

* a test that loads a hidden check as a module compiles it IN MEMORY: with
  bytecode on (CI's setting), ``edge_installed()`` left
  ``frontier-inventory-filter/check/__pycache__/*.pyc`` in the source tree;
* the patch task is the same task on a checkout that turned every file into
  CRLF (``core.autocrlf=true``, GitHub's Windows runners): ``git apply``
  refuses exactly the same four files' hunks and places the rest, so an agent
  there meets the very task an LF checkout gives;
* every team task's visible files carry no hidden answer: no file of the
  agent's copy names a test of a hidden check, holds the canary or a hidden
  file's text (the tripwire's own rules), the port's expected values (the
  ones the reference gives on its edge cases) appear nowhere the agent can
  read, the audit key's events are not listed together anywhere, and no
  task's copy is a git repository (no history to dig an answer out of).
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

from iron_jarvis.core import procs
from iron_jarvis.harness_bench.catalog import HIDDEN_CANARY
from iron_jarvis.harness_bench.setup_step import prepare_workspace
from tests.test_harness_bench_v1363 import TASKS

TEAM = sorted(t.id for t in TASKS.values() if t.tier == "team")
PATCH = "incoming/0001-Add-loyalty-tiers.patch"
PATCH_CONFLICTS = {"README.md", "orderdesk/cli.py", "orderdesk/invoice.py", "orderdesk/shipping.py"}


def _pycs(task_id: str, stem: str) -> list[Path]:
    cache = TASKS[task_id].check_dir / "__pycache__"
    return sorted(cache.glob(f"{stem}.*.pyc")) if cache.is_dir() else []


def _tidy(task_id: str, stem: str) -> None:
    """Remove what a failing run of these pins (or a mutation) leaves: the
    .pyc, and the __pycache__ folder when nothing else is in it."""
    for leftover in _pycs(task_id, stem):
        leftover.unlink()
    cache = TASKS[task_id].check_dir / "__pycache__"
    if cache.is_dir() and not any(cache.iterdir()):
        cache.rmdir()


def test_edge_installed_leaves_no_bytecode_of_the_hidden_check(monkeypatch):
    """With bytecode ON (CI writes it; this PC's environment turns it off,
    which is why the leak never showed here), loading the browser task's
    hidden check to ask whether Edge is installed writes nothing beside it."""
    from tests.test_harness_bench_v1363 import edge_installed

    stem = "test_hidden_inventory_page"
    _tidy("frontier-inventory-filter", stem)  # an earlier run's (the leak this pins)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    try:
        edge_installed()
        assert _pycs("frontier-inventory-filter", stem) == []
    finally:
        _tidy("frontier-inventory-filter", stem)


@pytest.mark.parametrize("task_id", TEAM)
def test_the_team_pins_load_the_hidden_checks_without_bytecode(task_id, monkeypatch):
    from tests.test_harness_bench_team_tasks_v1400 import _load_check

    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    for path in sorted(TASKS[task_id].check_dir.glob("*.py")):
        _tidy(task_id, path.stem)
        try:
            _load_check(task_id, path.name)
            assert _pycs(task_id, path.stem) == [], path.name
        finally:
            _tidy(task_id, path.stem)


def _crlf(work: Path) -> Path:
    for path in work.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    return work


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to apply the patch")
def test_the_patch_task_is_the_same_task_on_a_crlf_checkout(tmp_path):
    """core.autocrlf=true turns the patch AND its targets into CRLF together:
    git apply then refuses the same four files and places every other hunk."""
    task = TASKS["team-review-patch"]
    now = _crlf(prepare_workspace(task, tmp_path / "now"))
    assert b"\r\n" in (now / PATCH).read_bytes()
    assert procs.run(["git", "apply", "--check", PATCH], cwd=now, timeout=60).returncode != 0
    tried = procs.run(["git", "apply", "--reject", PATCH], cwd=now, timeout=60)
    failed = set(re.findall(r"patch failed: (\S+):\d+", procs.text(tried.stderr, "utf-8")))
    assert failed == PATCH_CONFLICTS, failed
    for new in ("orderdesk/credit.py", "orderdesk/points.py", "orderdesk/refunds.py", "test_loyalty.py"):
        assert (now / new).is_file(), new


#: Words that appear in a hidden check but must never appear in what the
#: agent can read — the hidden checks' own test names and private markers.
def _hidden_test_names(task_id: str) -> set[str]:
    names = set()
    for path in TASKS[task_id].check_dir.glob("*.py"):
        names |= set(re.findall(r"def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8")))
    return names


@pytest.mark.parametrize("task_id", TEAM)
def test_the_agents_copy_carries_no_hidden_answer(task_id, tmp_path):
    task = TASKS[task_id]
    work = prepare_workspace(task, tmp_path / "w")
    assert not (work / ".git").exists() and not any(p.name == ".git" for p in work.rglob(".git"))
    texts = {p.relative_to(work).as_posix(): p.read_text(encoding="utf-8", errors="replace")
             for p in work.rglob("*") if p.is_file()}
    everything = "\n".join(texts.values()) + "\n" + task.prompt
    assert HIDDEN_CANARY not in everything
    assert not [p for p in work.rglob("*") if p.suffix in (".pyc", ".pyo", ".rej", ".orig")]
    # The hidden checks' test names (the patch's test_loyalty.py names are its
    # own, and the visible tests are the project's: only the HIDDEN names).
    visible_names = set(re.findall(r"def (test_[a-z0-9_]+)", everything))
    hidden_only = _hidden_test_names(task_id) - visible_names
    for rel, text in texts.items():
        leaked = sorted(n for n in hidden_only if re.search(rf"\b{n}\b", text))
        assert not leaked, (rel, leaked)


def _load(task_id: str, name: str):
    from tests.test_harness_bench_team_tasks_v1400 import _load_check

    return _load_check(task_id, name)


def test_the_ports_edge_case_answers_are_nowhere_the_agent_reads(tmp_path):
    """The reference's results on its edge cases (what JavaScript really
    gives: half-up toFixed, UTF-16 order, the whole component kept on a bad
    escape) are only in the hidden check — never in the copy, the prompt or
    a comment of the reference."""
    task = TASKS["team-port-js"]
    check = _load("team-port-js", "test_hidden_port.py")
    work = prepare_workspace(task, tmp_path / "w")
    readable = "\n".join(p.read_text(encoding="utf-8") for p in work.rglob("*") if p.is_file()) + task.prompt
    telling = [
        ("format_bytes", "2560", "3 KiB"), ("format_bytes", "1280", "1.3 KiB"), ("format_bytes", "1152", "1.13 KiB"),
        ("parse_query", "caf%C3%A9%", None), ("parse_query", "%ED%A0%80", None), ("wrap_text", " par", None),
    ]
    for _fn, given, answer in telling:
        if answer is not None:
            assert not re.search(rf"{re.escape(given)}[^\n]*{re.escape(answer)}", readable), (given, answer)
        else:
            assert given not in readable, given
    assert len(check.FORMAT_BYTES) > 30 and len(check.PARSE_QUERY) > 30


def test_the_audit_key_is_not_written_down_in_the_copy(tmp_path):
    """No file of the audit task's copy lists events next to the functions
    that write them: a write's event appears where it is WRITTEN (or as a
    constant), never in a table of answers. Measured as: no file holds more
    than a quarter of the hidden key's events."""
    task = TASKS["team-audit-events"]
    expected = _load("team-audit-events", "test_hidden_audit_events.py").EXPECTED
    events = {e for _f, _fn, e in expected}
    work = prepare_workspace(task, tmp_path / "w")
    for path in work.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert sum(e in text for e in events) <= len(events) // 4, path.relative_to(work)
