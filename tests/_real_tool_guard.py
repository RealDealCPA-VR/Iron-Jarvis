"""No test starts a REAL coding tool (v1.507.0).

``hosted.service`` finds the program of a coding tool (Claude Code, Codex, …)
through ONE name, ``service.binary_for``. Tests swap it for the recorded
stand-in (``_build_app_helpers_v1409.use_fake``). A test that undid that swap
before its service had stopped (``monkeypatch.undo()`` in a ``finally``) let
a start still in flight look the program up again — and on a PC with Claude
Code installed that started the owner's real ``claude``, signed in, with
their own extensions (found 2026-11-16: four of them, left running by a local
run). GitHub's runners have no coding tool installed, so the suite never saw
it there.

``guard`` wraps the lookup for the whole test session: a program that really
exists on this PC and is not inside the test tree or a temporary folder is
answered "not installed" (None), exactly as on GitHub. A test's own stand-in
(a path under the tests folder or a temp folder, or a path that does not
exist) passes through untouched.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

Lookup = Callable[[str], "list[str] | None"]


def safe_roots() -> list[Path]:
    """Where a test's stand-in program may live: the tests folder and the
    temporary folders pytest writes under."""
    roots = [TESTS_DIR, Path(tempfile.gettempdir())]
    for var in ("TMP", "TEMP", "TMPDIR", "RUNNER_TEMP"):
        raw = os.environ.get(var)
        if raw:
            roots.append(Path(raw))
    out: list[Path] = []
    for root in roots:
        try:
            out.append(root.resolve())
        except OSError:
            continue
    return out


def is_real_program(exe: str, roots: Sequence[Path]) -> bool:
    """True when ``exe`` is a file that exists on this PC outside every root
    in ``roots``: a real installed program, never a test's stand-in."""
    try:
        path = Path(exe)
        if not path.is_file():
            return False
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root)
            return False
        except ValueError:
            continue
    return True


def guard(original: Lookup, roots: Sequence[Path] | None = None) -> Lookup:
    """``original`` with every real installed program answered None."""

    def guarded(harness: str) -> list[str] | None:
        argv = original(harness)
        if not argv:
            return argv
        if is_real_program(str(argv[0]), safe_roots() if roots is None else roots):
            return None
        return argv

    guarded.__wrapped__ = original  # type: ignore[attr-defined]
    return guarded
