"""Check a code file right after a tool wrote it (v1.364.0, IJ harness).

Codex's system prompt tells its model not to re-read a file after
``apply_patch`` because a failed patch says so; Claude Code shows its model
diagnostics after an edit. The IJ harness does the cheap, honest part of
both: after ``write_file``, ``edit_file``, ``multi_edit``, ``apply_patch`` or
``notebook_edit`` writes a CODE file, a fast syntax check for its type runs
and ONE line rides the tool output:

* ``Check: the file parses.``
* ``Check: SyntaxError at line 12: invalid syntax``

Rules: never on the event loop (callers run :func:`check_file` in a worker
thread), bounded (a file over :data:`MAX_CHECK_BYTES` is skipped and says so;
``node --check`` goes through ``core.procs`` with a timeout), and NEVER fails
the write — a check that cannot run says nothing or says it was skipped.

``.py`` is compiled in memory (``compile``: no ``.pyc`` is written), ``.json``
/ ``.ipynb`` parsed, ``.toml`` read with ``tomllib``, and ``.js`` / ``.mjs`` /
``.cjs`` checked with ``node --check`` only when node is on PATH.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path

#: A bigger file is not checked (a generated bundle, a data dump).
MAX_CHECK_BYTES = 2_000_000
#: ``node --check`` is given this long.
NODE_TIMEOUT_S = 10.0
#: The message part of a failing line is cut to this.
_MSG_MAX = 160

PYTHON_SUFFIXES = frozenset({".py", ".pyw"})
JSON_SUFFIXES = frozenset({".json", ".ipynb"})
TOML_SUFFIXES = frozenset({".toml"})
JS_SUFFIXES = frozenset({".js", ".mjs", ".cjs"})
CODE_SUFFIXES = PYTHON_SUFFIXES | JSON_SUFFIXES | TOML_SUFFIXES | JS_SUFFIXES

PARSES = "Check: the file parses."


def is_code_file(path: str | Path) -> bool:
    """Whether a check exists for this file's type."""
    return Path(str(path)).suffix.lower() in CODE_SUFFIXES


def _cut(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= _MSG_MAX else flat[: _MSG_MAX - 1] + "…"


def _check_python(source: str, name: str) -> str:
    try:
        compile(source, name, "exec", dont_inherit=True)
    except SyntaxError as exc:
        where = f" at line {exc.lineno}" if exc.lineno else ""
        return f"Check: {type(exc).__name__}{where}: {_cut(exc.msg)}"
    except ValueError as exc:  # a NUL byte in the source
        return f"Check: the file does not parse: {_cut(str(exc))}"
    return PARSES


def _check_json(source: str) -> str:
    try:
        json.loads(source)
    except json.JSONDecodeError as exc:
        return f"Check: JSON error at line {exc.lineno}, column {exc.colno}: {_cut(exc.msg)}"
    return PARSES


def _check_toml(source: str) -> str:
    try:
        tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        return f"Check: TOML error: {_cut(str(exc))}"
    return PARSES


_NODE_LINE = re.compile(r":(\d+)\s*$")


def _check_js(path: Path) -> str | None:
    node = shutil.which("node")
    if not node:
        return None  # no node on PATH: no check, and no line about it
    from ..core import procs

    try:
        res = procs.run([node, "--check", str(path)], timeout=NODE_TIMEOUT_S)
    except OSError:
        return None
    if res.timed_out:
        return f"Check: skipped — node --check took over {NODE_TIMEOUT_S:g} s"
    if res.returncode == 0:
        return PARSES
    err = procs.text(res.stderr, encoding="utf-8")
    lines = [ln for ln in err.splitlines() if ln.strip()]
    lineno = ""
    if lines:
        m = _NODE_LINE.search(lines[0])
        if m:
            lineno = f" at line {m.group(1)}"
    msg = next((ln.strip() for ln in lines if re.match(r"^\w*Error\b", ln.strip())), "")
    if msg:
        kind, _, rest = msg.partition(":")
        return f"Check: {kind.strip()}{lineno}: {_cut(rest.strip() or msg)}"
    return f"Check: node --check failed{lineno}"


def check_file(path: str | Path) -> str | None:
    """The one check line for ``path``, or ``None`` when its type has no check
    (or node is missing for a ``.js`` file). BLOCKING; never raises."""
    p = Path(str(path))
    suffix = p.suffix.lower()
    if suffix not in CODE_SUFFIXES:
        return None
    try:
        size = p.stat().st_size
        if size > MAX_CHECK_BYTES:
            return f"Check: skipped — the file is over {MAX_CHECK_BYTES // 1_000_000} MB"
        if suffix in JS_SUFFIXES:
            return _check_js(p)
        raw = p.read_bytes()
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            source = raw.decode("utf-16")
        else:
            source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "Check: the file is not UTF-8 text, so it could not be checked"
    except OSError:
        return None
    except Exception:  # noqa: BLE001 — a check never fails the write
        return None
    try:
        if suffix in PYTHON_SUFFIXES:
            return _check_python(source, p.name)
        if suffix in JSON_SUFFIXES:
            return _check_json(source)
        if suffix in TOML_SUFFIXES:
            return _check_toml(source)
    except Exception:  # noqa: BLE001
        return None
    return None


def check_files(paths: list[tuple[str, str | Path]]) -> str | None:
    """ONE line for several written files (``apply_patch``): ``[(label,
    path), ...]``. Every file parses → the plain line (a count when more than
    one); otherwise each failing file named with its own message. ``None``
    when no file had a check. BLOCKING; never raises."""
    results: list[tuple[str, str]] = []
    for label, path in paths:
        line = check_file(path)
        if line is not None:
            results.append((label, line))
    if not results:
        return None
    bad = [(label, line) for label, line in results if line != PARSES]
    if not bad:
        if len(results) == 1:
            return PARSES
        return f"Check: all {len(results)} changed code files parse."
    parts = [f"{label}: {line[len('Check: '):]}" for label, line in bad]
    return "Check: " + "; ".join(parts)


async def check_line(path: str | Path) -> str | None:
    """:func:`check_file` off the event loop; ``None`` on any failure."""
    import asyncio

    try:
        return await asyncio.to_thread(check_file, path)
    except Exception:  # noqa: BLE001 — a check never fails the write
        return None


def with_check(output: str, line: str | None) -> str:
    """``output`` with the check line appended (unchanged when there is none)."""
    if not line:
        return output
    return f"{output}\n{line}" if output else line
