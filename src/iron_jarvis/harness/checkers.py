"""THE PROJECT'S OWN CHECKERS AFTER AN EDIT (IJ harness part 3, Phase 3 —
``docs/IJ-HARNESS-PLAN-3.md``, the user's decision 7: "the project's OWN
linters and type checkers when they are installed — ruff, mypy, eslint, tsc;
no bundled language servers").

``harness/verify`` puts one line on every code write: "Check: the file
parses." That proves the file is not broken text, not that it type-checks.
When the session's folder ALREADY HAS a checker — its config file in the
folder AND the program resolvable (the project's ``.venv`` / ``venv`` or
``node_modules/.bin``, else the PATH the session's commands run with) — the
checker runs ONCE per reply, at the finish check (v1.420.0, C3.3 — the
user's decision D1), on every file the reply changed, and its findings come
back in the finish note the model answers before the reply ends:

    ## The project's checkers
    Check (mypy): 1 problem in app.py
    app.py:3: error: Incompatible return value type (got "str", expected "int")

Before v1.420.0 they ran after EACH round that edited a file (seconds to tens
of seconds per editing round, ``tsc`` over the whole project every time, a
card per round): a 3-round editing reply now runs each checker once.

THE SAFETY DECISION. A checker EXECUTES PROJECT CODE more often than not: a
program from the project's own ``node_modules`` or ``.venv`` is the project's
code; ESLint loads ``eslint.config.js`` (JavaScript) and its plugins; mypy
imports its configured plugins; a project's ``tsc``. Telling those apart from
the harmless cases (a global ruff over a TOML config) is a classification a
wrong guess would turn into project code run with nobody asked. So there is
ONE rule, for every checker: a checker run IS A COMMAND, run exactly as if
the model had run it —

* it is a call of the turn's own command tool (``shell``; ``process_start``
  under Codex's dialect, which arms no ``shell``) ADDED TO THE FINISH
  CHECK'S ROUND, after its test run (:meth:`ProjectCheckers.schedule_finish`,
  called by ``FinishCheck.prepare``), so it passes everything a model's
  command passes: the armed-set gate, the card the session's posture puts up
  (Ask first asks; "Allow for this chat" covers it like any command), Local
  only's "Allow once" card, the git guard and the project-command rule (the
  spend check), the permission engine in the REAL ``registry.invoke``, the
  ledger (``called_as`` :data:`CALLED_AS`) and the tool deadline;
* it NEEDS A PERSON (Phase 3 review, :meth:`ProjectCheckers.needs_a_person`):
  the model never chose to run it, so Accept edits' / Auto's per-call rules
  never cover it — it cards unless a grant wider than one call covers the
  command tool (the permission table, "Allow for this chat" / "for this
  session", a standing grant). v1.417.0 (C1.3, the user's decision D1): EXCEPT
  under "Don't ask" (yolo) — the user's standing "never ask me", and Iron
  Jarvis's own checker after an edit is not a new risk — where it runs with no
  card. (A folder's own ``plugins.toml`` check and a project's command file
  keep their card there: the v1.386.1 and v1.381.0 rules.) The finish
  round's calls share ONE card (v1.247.0) — under Local only each still cards
  alone ("Allow once"), as every command there does;
* low trust never schedules one (and a call tainted low mid-round is refused
  by the lane like any command);
* the POST lane arms no ask-tier command tool, so there a checker runs only
  where the user's permissions already allow the command tool;
* nothing is ever installed: no ``npx``, no ``uvx``, no ``pip`` — only a
  program that already exists; a checker whose program cannot be found does
  not run and says nothing (the folder behaves as one without checkers).

A folder with no checkers (or none installed) behaves EXACTLY as before: no
call is added, the note has no checkers section (pinned). A folder whose
``finish.toml`` turns the finish check off runs no checker either: they are
part of that check now.

WHAT RUNS (never a fix, never a cache written into the folder):

* ``ruff check --no-fix --no-cache --force-exclude --output-format concise
  <files>`` — ``ruff.toml``, ``.ruff.toml`` or ``[tool.ruff]`` in
  ``pyproject.toml``; ``--no-fix`` because a config may say ``fix = true``;
* ``mypy --follow-imports=silent --cache-dir <the null device>
  --no-color-output --no-error-summary <files>`` — ``mypy.ini``, ``.mypy.ini``,
  ``[tool.mypy]`` or ``[mypy]`` in ``setup.cfg``; the changed files only
  (imports are read for their types, never checked themselves);
* ``eslint --format json <files>`` — ``eslint.config.*`` or ``.eslintrc*``;
* ``tsc --noEmit --pretty false -p tsconfig.json`` — the WHOLE project (tsc
  ignores tsconfig.json when given files, so a per-file run would check
  against the wrong settings), reported for the changed files only — and
  ONLY when a changed file is TypeScript and the project holds at most
  :data:`TSC_MAX_FILES` TypeScript files (counted with the project map's
  pruning); a bigger one is skipped and said ("project too large for a quick
  check"). A project that set ``incremental`` gets its own ``.tsbuildinfo``
  written, as its own ``tsc --noEmit`` would.

BOUNDED: the checkers SHARE :data:`CHECKERS_BUDGET_S` — each run's time
limit is what is left of it when the run starts, and the one that does not
finish (cut, or not started once the budget is spent) is NAMED "not finished
in time" in the note and on the record's ``checkers.late``, never a silent
miss; a file whose name a command line
cannot carry safely (``"%^&|<>!``, control characters, a part starting with
``-``) is not checked; the findings are cut to :data:`MAX_FINDING_LINES`
lines / :data:`MAX_FINDINGS_CHARS` characters and the cut is said.

MIRROR NOTE (lock-step): ``daemon/chat_turn.run_chat_turn`` and the stream
lane in ``daemon/routes/chat.py`` call this module at the same seams
(``FinishCheck.prepare`` hands the calls out; ``prepare`` at invoke; ``take``
when a call settles; the finish note and record read :meth:`ProjectCheckers.
note_text` / :meth:`ProjectCheckers.record`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Iterable

log = logging.getLogger(__name__)

#: The budget's clock (a seam: tests move it by hand).
_clock = time.monotonic

#: What the ledger row of a checker run says (``ToolInvocation.called_as``).
CALLED_AS = "project check"
#: The checker calls' ids (never a model's).
CALL_ID_PREFIX = "ij-check-"
#: One checker run's time limit before v1.420.0 (kept for older callers; the
#: checkers now share :data:`CHECKERS_BUDGET_S`).
TIMEOUT_S = 60
#: v1.420.0 (C3.3, decision D1): ALL of a reply's checkers together get this
#: many seconds — each run's time limit is what is left when it starts; the
#: one that does not finish is cut and named "not finished in time".
CHECKERS_BUDGET_S = 15
#: A run is not started with less than this left of the budget.
MIN_RUN_S = 1.0
#: ``tsc`` checks the WHOLE project: run only when it holds at most this many
#: TypeScript files (counted with the project map's pruning).
TSC_MAX_FILES = 400
#: ...counted within this long, else not run.
TSC_COUNT_DEADLINE_S = 1.5
#: ...and waited for at most this much longer: the count checks its deadline
#: only between folders, so a listing that stalls (a folder on a share whose
#: server went away) is given up on here and left to end on its thread
#: (review: the wait had no bound and held the finish round with it).
TSC_COUNT_GRACE_S = 2.0
TSC_TOO_LARGE = "project too large for a quick check"
TSC_NOT_COUNTED = "the project's TypeScript files could not be counted quickly"
#: ``process_start`` waits at most this long a call (the process tools' cap).
PROCESS_WAIT_MS = 30_000
#: The most files one run is handed (a command line has a length limit —
#: cmd.exe's is 8,191 characters); the rest are named as not checked.
MAX_FILES_PER_RUN = 50
#: The findings shown per checker run.
MAX_FINDING_LINES = 20
MAX_FINDINGS_CHARS = 2_000
MAX_LINE_CHARS = 300
#: A bigger config file is not read for its ``[tool.x]`` table.
MAX_CONFIG_BYTES = 256 * 1024

PY_SUFFIXES = frozenset({".py", ".pyi"})
JS_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})
TS_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})
#: Characters a command line cannot carry safely (cmd.exe expands ``%`` and
#: ``!`` even inside quotes).
_UNSAFE = re.compile(r"[\"%^&|<>!`$\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Checker:
    """One checker the folder has: its config AND its program."""

    name: str
    #: What the command line names: a path relative to the folder (the
    #: project's own program) or the FULL path of the one found on the
    #: session's PATH (Phase 3 review: never the bare name — cmd.exe looks in
    #: the current folder first, so a project's own ``ruff.bat`` stood in for
    #: the PATH's ruff behind a card that read "ruff check …").
    program: str
    #: "project" (``.venv`` / ``venv`` / ``node_modules``) | "path"
    where: str
    #: The config file (relative) that turned it on.
    config: str
    suffixes: frozenset[str]

    @property
    def project_wide(self) -> bool:
        return self.name == "tsc"

    def argv(self, files: list[str]) -> list[str]:
        if self.name == "ruff":
            return [self.program, "check", "--no-fix", "--no-cache", "--force-exclude",
                    "--output-format", "concise", *files]
        if self.name == "mypy":
            null = "nul" if os.name == "nt" else "/dev/null"
            # v1.420.0 (C3.3): the changed files only — imports are read for
            # their types, never followed into a check of their own.
            return [self.program, "--follow-imports=silent", "--cache-dir", null, "--no-color-output",
                    "--no-error-summary", *files]
        if self.name == "eslint":
            return [self.program, "--format", "json", *files]
        if self.name == "tsc":
            return [self.program, "--noEmit", "--pretty", "false", "-p", self.config]
        raise ValueError(self.name)


# --------------------------------------------------------------------------- #
# finding them — never installing, never running anything
# --------------------------------------------------------------------------- #


def _read_small(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _toml_has(path: Path, *keys: str) -> bool:
    """Does the TOML file have the table ``keys`` (``tool.ruff``)? A parse,
    never more."""
    text = _read_small(path)
    if not text:
        return False
    try:
        import tomllib

        data: Any = tomllib.loads(text)
    except Exception:  # noqa: BLE001 — an unreadable config turns nothing on
        return False
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return False
        data = data[key]
    return True


def _ini_has(path: Path, section: str) -> bool:
    text = _read_small(path)
    return bool(text) and re.search(rf"(?m)^\s*\[{re.escape(section)}\]\s*$", text) is not None


def _config_for(name: str, folder: Path) -> str:
    """The config file (relative) that turns ``name`` on in ``folder``, or ""."""
    if name == "ruff":
        for f in ("ruff.toml", ".ruff.toml"):
            if (folder / f).is_file():
                return f
        return "pyproject.toml" if _toml_has(folder / "pyproject.toml", "tool", "ruff") else ""
    if name == "mypy":
        for f in ("mypy.ini", ".mypy.ini"):
            if (folder / f).is_file():
                return f
        if _toml_has(folder / "pyproject.toml", "tool", "mypy"):
            return "pyproject.toml"
        return "setup.cfg" if _ini_has(folder / "setup.cfg", "mypy") else ""
    if name == "eslint":
        for ext in ("js", "mjs", "cjs", "ts", "mts", "cts"):
            if (folder / f"eslint.config.{ext}").is_file():
                return f"eslint.config.{ext}"
        for f in (".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yaml", ".eslintrc.yml"):
            if (folder / f).is_file():
                return f
        return ""
    if name == "tsc":
        return "tsconfig.json" if (folder / "tsconfig.json").is_file() else ""
    return ""


def _project_program(name: str, folder: Path, kind: str) -> str:
    """The project's OWN copy of ``name`` (relative to the folder), or ""."""
    if kind == "python":
        candidates = (
            [f"{env}/Scripts/{name}.exe" for env in (".venv", "venv")]
            if os.name == "nt" else [f"{env}/bin/{name}" for env in (".venv", "venv")]
        )
    else:
        candidates = [f"node_modules/.bin/{name}.cmd"] if os.name == "nt" else [f"node_modules/.bin/{name}"]
    for rel in candidates:
        if (folder / rel).is_file():
            return rel.replace("/", "\\") if os.name == "nt" else "./" + rel
    return ""


_SPECS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("ruff", "python", PY_SUFFIXES),
    ("mypy", "python", PY_SUFFIXES),
    ("eslint", "node", JS_SUFFIXES),
    ("tsc", "node", TS_SUFFIXES),
)


def detect(folder: Any, *, path_env: "str | None" = None) -> list[Checker]:
    """The checkers ``folder`` HAS: a config file there AND the program — the
    project's own (``.venv``/``venv``/``node_modules/.bin``), else found on
    ``path_env`` (the PATH the session's commands run with). BLOCKING (stats,
    small config reads, a PATH lookup). Never raises; never installs or runs
    anything."""
    out: list[Checker] = []
    try:
        base = Path(str(folder)).resolve()
        if not base.is_dir():
            return out
    except (OSError, ValueError):
        return out
    for name, kind, suffixes in _SPECS:
        try:
            config = _config_for(name, base)
            if not config:
                continue
            program = _project_program(name, base, kind)
            where = "project"
            if not program:
                found = shutil.which(name, path=path_env)
                if found is None:
                    continue  # not installed: never installed for it
                program, where = str(found), "path"
                if _UNSAFE.search(program):
                    # A program path a command line cannot carry as plain
                    # data (cmd.exe expands `%`, runs on at `&`): not used.
                    continue
            out.append(Checker(name=name, program=program, where=where, config=config, suffixes=suffixes))
        except Exception:  # noqa: BLE001 — one checker's oddity never hides the others
            log.debug("checker %s: detection failed", name, exc_info=True)
    return out


def _session_path() -> "str | None":
    """The PATH a Build session's commands run with (``sandbox/native.
    build_env`` — the user's own)."""
    try:
        # v1.398.0 (night shift N1, decision 3): in a session on its own
        # branch, the MAIN folder's .venv / node_modules/.bin come first —
        # the PATH the session's commands really run with.
        from ..sandbox.native import build_turn_env

        env = build_turn_env()
        key = next((k for k in env if k.upper() == "PATH"), "PATH")
        return env.get(key)
    except Exception:  # noqa: BLE001
        return os.environ.get("PATH")


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #


def safe_arg(arg: str) -> bool:
    """Can a command line carry ``arg`` as plain data? (No shell
    metacharacter, no control character, no part read as an option.)"""
    if not arg or _UNSAFE.search(arg):
        return False
    return not any(part.startswith("-") for part in re.split(r"[\\/]", arg) if part)


def command_line(argv: list[str]) -> str:
    """``argv`` as ONE command for the platform's own shell (cmd.exe on
    Windows: every argument with a space double-quoted — :func:`safe_arg`
    already refused anything quoting cannot make plain; POSIX: ``shlex``)."""
    if os.name == "nt":
        return " ".join(f'"{a}"' if (" " in a or "\t" in a) else a for a in argv)
    return shlex.join(argv)


# --------------------------------------------------------------------------- #
# reading what a checker said
# --------------------------------------------------------------------------- #

_ADVISORY = re.compile(r"^\s*\[(?:warning\] sandbox:|native runtime)")


def _norm(p: str) -> str:
    p = str(p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lower() if os.name == "nt" else p


def _starts_with_file(line: str, files: set[str]) -> str:
    """The checked file a finding line starts with ("" for none)."""
    head = _norm(line[:600])
    for f in files:
        if head.startswith(f) and len(head) > len(f) and head[len(f)] in ":(":
            return f
    return ""


def _eslint_findings(text: str, files: set[str], folder: Path) -> "tuple[list[str], int] | None":
    """ESLint's ``--format json`` → (finding lines, problems in other files),
    or None when the output is not that JSON (a config error: shown raw)."""
    start = text.find("[")
    if start < 0:
        return None
    try:
        data = json.loads(text[start:])
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    lines: list[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        fp = str(entry.get("filePath") or "")
        try:
            rel = os.path.relpath(fp, str(folder)).replace("\\", "/")
        except ValueError:
            rel = fp
        for msg in entry.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            words = str(msg.get("message") or "")
            if words.startswith("File ignored"):
                continue
            sev = "error" if msg.get("severity") == 2 else "warning"
            rule = msg.get("ruleId") or ""
            lines.append(f"{rel}:{msg.get('line', 0)}:{msg.get('column', 0)}: {sev} {words}"
                         + (f" ({rule})" if rule else ""))
    return lines, 0


def findings(checker: Checker, output: str, files: list[str], folder: Path) -> tuple[list[str], int, bool]:
    """``(lines about the checked files, problems found in OTHER files,
    output understood)``. Pure apart from nothing; never raises."""
    wanted = {_norm(f) for f in files}
    text = "\n".join(ln for ln in str(output or "").splitlines() if not _ADVISORY.match(ln))
    if checker.name == "eslint":
        got = _eslint_findings(text, wanted, folder)
        if got is None:
            return [], 0, False
        return got[0], got[1], True
    mine: list[str] = []
    others = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if _starts_with_file(line, wanted):
            mine.append(line.rstrip())
            continue
        if checker.name in ("mypy", "tsc") and re.search(r"(?::\d+: error:|\): error TS\d+:)", line):
            others += 1
    return mine, others, True


def _problems(checker: Checker, lines: list[str]) -> int:
    if checker.name == "mypy":
        return sum(1 for ln in lines if ": error:" in ln) or len(lines)
    return len(lines)


# --------------------------------------------------------------------------- #
# the per-turn object the lanes hold
# --------------------------------------------------------------------------- #


@dataclass
class _Pending:
    checker: Checker
    #: the changed files this checker reads, as handed out
    files: list[str]
    #: the files the run really got (those still there at its start)
    ran_files: list[str] = field(default_factory=list)
    #: changed files past :data:`MAX_FILES_PER_RUN` (named as not checked)
    left_out: int = 0
    #: v1.420.0: not finished inside the checkers' shared budget — not
    #: started, because the budget was used up before its turn
    late: bool = False


def _count_typescript(folder: Path, cap: int, deadline_s: float) -> "int | None":
    """How many TypeScript files ``folder`` holds — pruned like the project
    map and ``list_files`` (heavy folders such as ``node_modules`` and
    ``dist``, every ``.``-folder, no link or junction followed) — stopping
    once past ``cap``. None: not counted inside ``deadline_s``. BLOCKING."""
    from ..tools.builtins import _SKIP_DIRS

    started = _clock()
    count = 0
    stack = [folder]
    while stack:
        here = stack.pop()
        try:
            with os.scandir(here) as it:
                children = list(it)
        except OSError:
            continue
        for child in children:
            try:
                if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                    continue
                if child.is_dir(follow_symlinks=False):
                    if child.name not in _SKIP_DIRS and not child.name.startswith("."):
                        stack.append(Path(child.path))
                    continue
            except OSError:
                continue
            if PurePosixPath(child.name).suffix.lower() in TS_SUFFIXES:
                count += 1
                if count > cap:
                    return count
        if _clock() - started > deadline_s:
            return None
    return count


class ProjectCheckers:
    """The checkers of ONE turn (built by :func:`for_turn`).

    v1.420.0 (C3.3, the user's decision D1): they run ONCE per reply, in the
    finish check's round, over every file the reply changed
    (:meth:`schedule_finish`, called by ``FinishCheck.prepare``) — never
    after each editing round — sharing ONE budget of
    :data:`CHECKERS_BUDGET_S`; their findings go into the finish note
    (:meth:`note_text`) and its record (:meth:`record`)."""

    def __init__(self, folder: Path, checkers: list[Checker], carrier: str, harness_turn: Any = None) -> None:
        self.folder = Path(folder)
        self.checkers = list(checkers)
        self.carrier = carrier
        self.harness = harness_turn
        self._pending: dict[str, _Pending] = {}
        self._n = 0
        #: what each run did, for the record and the pins
        self.runs: list[dict[str, Any]] = []
        #: v1.420.0: handed out once per reply — never again
        self.scheduled = False
        #: the checkers that were not handed out, and why ({"checker", "why"})
        self.skipped: list[dict[str, str]] = []
        #: each settled run's text for the finish note, in order
        self.texts: list[str] = []
        #: when the reply's first checker started (the budget's clock)
        self._budget_start: "float | None" = None

    # -- which files are checked -------------------------------------------- #

    def _rel(self, raw: Any) -> str:
        """``raw`` as a path relative to the folder, "" when outside it or
        not a path a command line can carry. PURE (string work only — it
        runs on the event loop, so no ``resolve``; the folder was resolved
        off the loop when the turn was built)."""
        try:
            p = Path(str(raw))
            if not p.is_absolute():
                p = self.folder / p
            full = os.path.normcase(os.path.normpath(str(p)))
            base = os.path.normcase(os.path.normpath(str(self.folder)))
            rel = os.path.relpath(full, base)
        except (OSError, ValueError):
            return ""
        if rel == os.curdir or rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            return ""
        # (the CASE the model wrote, not normcase's lower case)
        rel = os.path.relpath(os.path.normpath(str(p)), os.path.normpath(str(self.folder)))
        out = PurePosixPath(*Path(rel).parts).as_posix()
        if not out or out == "." or not safe_arg(out):
            return ""
        return out

    # -- the finish round --------------------------------------------------- #

    async def schedule_finish(self, changed: Iterable[Any], *, low_trust: bool = False) -> list[Any]:
        """The checker calls of the reply's FINISH ROUND: ONE call per
        checker that reads one of ``changed`` (every file the reply changed —
        the journal's and the folder comparison's, as the finish check knows
        them). [] when nothing applies (no checker, no file one reads, low
        trust) or they were handed out already this reply. ``tsc`` only when
        a changed file is TypeScript AND the project holds at most
        :data:`TSC_MAX_FILES` of them (counted off the loop); otherwise it is
        skipped and SAID."""
        if self.scheduled or low_trust or not self.checkers:
            return []
        self.scheduled = True
        rels: list[str] = []
        for raw in changed or ():
            rel = self._rel(raw) if raw else ""
            if rel and rel not in rels:
                rels.append(rel)
        if not rels:
            return []
        from ..providers.adapters.base import ToolCall

        out: list[Any] = []
        for checker in self.checkers:
            files = [f for f in rels if PurePosixPath(f).suffix.lower() in checker.suffixes]
            if not files:
                continue
            if checker.name == "tsc":
                try:
                    n = await asyncio.wait_for(
                        asyncio.to_thread(_count_typescript, self.folder, TSC_MAX_FILES, TSC_COUNT_DEADLINE_S),
                        TSC_COUNT_DEADLINE_S + TSC_COUNT_GRACE_S,
                    )
                except Exception:  # noqa: BLE001 — not counted (or not in time): not run
                    n = None
                if n is None:
                    self.skipped.append({"checker": "tsc", "why": TSC_NOT_COUNTED})
                    self.texts.append(f"Check (tsc) skipped: {TSC_NOT_COUNTED}.")
                    continue
                if n > TSC_MAX_FILES:
                    self.skipped.append({"checker": "tsc", "why": TSC_TOO_LARGE})
                    self.texts.append(
                        f"Check (tsc) skipped: {TSC_TOO_LARGE} (more than {TSC_MAX_FILES} TypeScript files)."
                    )
                    continue
            self._n += 1
            cid = f"{CALL_ID_PREFIX}{self._n}"
            call = ToolCall(id=cid, name=self.carrier,
                            arguments=self._args(checker, files[:MAX_FILES_PER_RUN], CHECKERS_BUDGET_S))
            self._pending[cid] = _Pending(checker=checker, files=files)
            if self.harness is not None:
                try:
                    self.harness.mark_called_as(cid, CALLED_AS)
                except Exception:  # noqa: BLE001 — a double without the method
                    pass
            out.append(call)
        return out

    def _args(self, checker: Checker, files: list[str], seconds: float) -> dict[str, Any]:
        command = command_line(checker.argv(files))
        if self.carrier == "process_start":
            return {"command": command, "until_exit": True,
                    "wait_ms": int(min(seconds * 1000, PROCESS_WAIT_MS))}
        return {"command": command, "timeout_s": seconds}

    def owns(self, call: Any) -> bool:
        return str(getattr(call, "id", "") or "") in self._pending

    def needs_a_person(self, call: Any, *, posture: str = "") -> bool:
        """Must this call be allowed by a PERSON (a card, or a grant wider
        than one call — the permission table, "Allow for this chat", a
        standing grant) — never by auto-approve's per-call grant? True for
        every checker run (Phase 3 review): it is a command the APP wrote,
        running a program the PROJECT supplies or configures (its ``.venv``
        / ``node_modules`` copy, an ESLint JS config, a mypy plugin), which
        the model never chose to run. The same rule as the finish check's folder
        command (``finish.FinishCheck.needs_a_person``, v1.386.1) and a
        folder's plugin checks (``chat_turn.harness_check_runner``, v1.366.0).
        The stream lane reads it beside the finish check's (MIRROR NOTE: the
        POST lane never auto-grants a call, so it needs no twin).

        v1.417.0 (C1.3, decision D1): False under "Don't ask" (``posture``
        "yolo") — the person's own standing answer covers Iron Jarvis's own
        checker run; every other posture as before."""
        if str(posture or "") == "yolo":
            return False
        return self.owns(call)

    def checker_name(self, call: Any) -> str:
        """The checker a call of this round runs ("ruff"), else ""."""
        p = self._pending.get(str(getattr(call, "id", "") or ""))
        return str(getattr(getattr(p, "checker", None), "name", "") or "") if p is not None else ""

    def note(self, call: Any, *, ran: bool) -> None:
        """A model call of a round settled. v1.420.0: the checkers read the
        reply's whole change at its end, so nothing per call is kept (the
        lanes' call is harmless)."""
        return None

    async def prepare_async(self, call: Any) -> str:
        """:meth:`prepare` OFF the event loop (it stats the files). THE ONE
        the lanes call."""
        if not self.owns(call):
            return ""
        return await asyncio.to_thread(self.prepare, call)

    def _remaining(self) -> float:
        if self._budget_start is None:
            self._budget_start = _clock()
        return CHECKERS_BUDGET_S - (_clock() - self._budget_start)

    def prepare(self, call: Any) -> str:
        """Right before the checker call runs: "" — run it (its files narrowed,
        in place, to those still there, and its time limit to what is LEFT of
        the checkers' shared :data:`CHECKERS_BUDGET_S`); else why it does not
        run (the call is then answered without running). BLOCKING (a stat per
        file): the lanes call :meth:`prepare_async`."""
        pend = self._pending.get(str(getattr(call, "id", "") or ""))
        if pend is None:
            return ""
        changed = [f for f in pend.files if (self.folder / f).is_file()]
        if not changed:
            return "the files it checks are gone, so it was not run"
        left = self._remaining()
        if left < MIN_RUN_S:
            pend.late = True
            return f"not finished in time: the checkers' {CHECKERS_BUDGET_S:g} s were used up before it started"
        pend.left_out = max(0, len(changed) - MAX_FILES_PER_RUN)
        changed = changed[:MAX_FILES_PER_RUN]
        pend.ran_files = changed
        args = getattr(call, "arguments", None)
        if isinstance(args, dict):
            args.clear()
            args.update(self._args(pend.checker, changed, round(min(left, CHECKERS_BUDGET_S), 1)))
        return ""

    def target(self, call: Any) -> str:
        """The id of an edit call whose result the findings ride on — none
        since v1.420.0: they go into the finish note (:meth:`note_text`)."""
        return ""

    async def take(
        self,
        call: Any,
        result: Any,
        crash: str = "",
        *,
        stop: "Callable[[str, dict[str, Any]], Any] | None" = None,
    ) -> str:
        """The checker call settled: its text, kept for the finish note
        (:attr:`texts`) — "" = nothing to say."""
        pend = self._pending.get(str(getattr(call, "id", "") or ""))
        if pend is None:
            return ""
        text = await self._take(call, pend, result, crash, stop)
        if text:
            self.texts.append(text)
        return text

    @staticmethod
    def _late_text(name: str) -> str:
        return (
            f"Check ({name}) not finished in time: the project's checkers share "
            f"{CHECKERS_BUDGET_S:g} s, so it was stopped."
        )

    async def _take(
        self, call: Any, pend: _Pending, result: Any, crash: str,
        stop: "Callable[[str, dict[str, Any]], Any] | None",
    ) -> str:
        name = pend.checker.name
        record: dict[str, Any] = {"checker": name, "call": str(getattr(call, "id", "") or ""),
                                  "files": list(pend.ran_files or pend.files)}
        self.runs.append(record)
        if result is None:
            if pend.late:
                record["verdict"] = "late"
                return self._late_text(name)
            if not pend.ran_files:
                record["verdict"] = "not_run"
                record["why"] = _cut(crash, 200)
                return f"Check ({name}) was not run: {_cut(crash, 200)}." if crash else ""
            record["verdict"] = "error"
            return f"Check ({name}) could not run: {_cut(crash, 200)}"
        data = dict(getattr(result, "data", None) or {})
        error = str(getattr(result, "error", "") or "")
        output = str(getattr(result, "output", "") or "")
        if self._refused(result, data):
            record["verdict"] = "not_run"
            record["why"] = _cut(error or output, 200)
            return f"Check ({name}) was not run: {_cut(error or output, 300)}"
        if call.name == "process_start":
            handle = str(data.get("id") or "")
            if data.get("running"):
                if handle and stop is not None:
                    try:
                        await stop("process_stop", {"id": handle})
                    except Exception:  # noqa: BLE001
                        pass
                record["verdict"] = "late"
                return self._late_text(name)
            code = data.get("exit_code")
        else:
            if data.get("timed_out"):
                record["verdict"] = "late"
                return self._late_text(name)
            code = data.get("returncode")
        record["exit_code"] = code
        # Parsed OFF the loop: a checker's output can be thousands of lines.
        lines, others, understood = await asyncio.to_thread(
            findings, pend.checker, output, pend.ran_files or pend.files, self.folder,
        )
        ran_ok = code in (0, 1) or (name == "tsc" and code == 2)
        if not ran_ok or not understood:
            record["verdict"] = "error"
            head = "\n".join(output.strip().splitlines()[:6])
            # Review: the PROGRAM's words go UNDER the head line, where the
            # lane's fence (scan + taint) reads them — never on the head line.
            return f"Check ({name}) could not run (exit {code}):\n{_cut(head or error, 600)}"
        files = pend.ran_files or pend.files
        where = ", ".join(files[:5]) + (f" and {len(files) - 5} more" if len(files) > 5 else "")
        other = (
            f" ({others} in other files, not shown)" if others else ""
        )
        if pend.left_out:
            other += (
                f" ({pend.left_out} more changed file{'' if pend.left_out == 1 else 's'} not checked: "
                f"one run takes at most {MAX_FILES_PER_RUN})"
            )
        if not lines and name == "ruff" and "No Python files found" in output:
            # --force-exclude honoured the project's own `exclude`: nothing was
            # checked, and "no problems" would be a claim about a file ruff
            # never looked at.
            record["verdict"] = "excluded"
            return f"Check (ruff): {where} is excluded by the project's own ruff settings, so it was not checked."
        if not lines:
            record["verdict"] = "clean"
            if code not in (0, None) and not others and name != "eslint":
                # It said "failed" and named no line we could read: say so.
                head = "\n".join(output.strip().splitlines()[:6])
                record["verdict"] = "error"
                return f"Check ({name}) reported a problem it did not tie to a line:\n{_cut(head, 600)}"
            return f"Check ({name}): no problems in {where}{other}."
        record["verdict"] = "problems"
        count = _problems(pend.checker, lines)
        record["problems"] = count
        shown: list[str] = []
        used = 0
        for line in lines[:MAX_FINDING_LINES]:
            line = _cut(line, MAX_LINE_CHARS)
            if used + len(line) + 1 > MAX_FINDINGS_CHARS:
                break
            shown.append(line)
            used += len(line) + 1
        more = len(lines) - len(shown)
        tail = f"\n… {more} more line{'' if more == 1 else 's'} not shown" if more else ""
        plural = "" if count == 1 else "s"
        return f"Check ({name}): {count} problem{plural} in {where}{other}\n" + "\n".join(shown) + tail

    # -- what the finish check reads ---------------------------------------- #

    @property
    def used(self) -> bool:
        """Did the checkers have anything to do this reply (a run handed out,
        or one skipped and said)?"""
        return bool(self._pending or self.skipped)

    async def note_text(self, fence: "Callable[[str], Awaitable[str]] | None" = None) -> str:
        """The finish note's "## The project's checkers" body: each checker's
        head line as Iron Jarvis wrote it, and the findings under it — the
        PROGRAM's text (it quotes the project's files), so fenced and scanned
        by the lane's own fence (``fence``). "" when there is nothing."""
        out: list[str] = []
        for text in self.texts:
            head, _, rest = str(text).partition("\n")
            rest = rest.strip("\n")
            if rest and fence is not None:
                rest = str(await fence(rest))
            out.append(head + ("\n" + rest if rest else ""))
        return "\n".join(out)

    def record(self) -> dict[str, Any]:
        """The finish record's ``checkers``: ``ran`` (checkers that read the
        files and said what they found), ``skipped`` (``{checker, why}``: not
        run — a project too large for tsc, a declined card, a refusal, a run
        that could not start) and ``late`` (not finished inside the shared
        budget)."""
        ran: list[str] = []
        late: list[str] = []
        skipped = [dict(s) for s in self.skipped]
        for r in self.runs:
            name, verdict = str(r.get("checker") or ""), str(r.get("verdict") or "")
            if verdict in ("clean", "problems", "excluded"):
                ran.append(name)
            elif verdict == "late":
                late.append(name)
            else:
                why = str(r.get("why") or "") or ("it could not run" if verdict == "error" else "it was not run")
                skipped.append({"checker": name, "why": _cut(why, 200)})
        return {"ran": ran, "skipped": skipped, "late": late}

    @staticmethod
    def _refused(result: Any, data: dict[str, Any]) -> bool:
        """The call ran no program (a refusal: a card declined, the gate, the
        `;` guard, low trust's sandbox) — no exit status exists."""
        if getattr(result, "ok", False):
            return False
        if data.get("refused") or data.get("confinement") == "refused" or data.get("low_trust"):
            return True
        if "returncode" in data or "exit_code" in data or "running" in data:
            return False
        return True


def _cut(text: Any, n: int) -> str:
    flat = str(text or "").strip()
    return flat if len(flat) <= n else flat[: n - 1] + "…"


async def for_turn(folder: Any, *, harness_turn: Any) -> "ProjectCheckers | None":
    """The checkers of a Build session's turn in ``folder`` — None when the
    folder has none installed, or the turn has no command tool to run them
    with (the POST lane where the user did not allow one). Found OFF THE
    LOOP; never raises."""
    try:
        from ..daemon.chat_turn import check_carrier

        gate = set(getattr(getattr(harness_turn, "plan", None), "gate", ()) or ())
        carrier = check_carrier(gate)
        if carrier not in gate:
            return None
        path_env = await asyncio.to_thread(_session_path)
        found = await asyncio.to_thread(detect, folder, path_env=path_env)
        if not found:
            return None
        base = await asyncio.to_thread(lambda: Path(str(folder)).resolve())
    except Exception:  # noqa: BLE001 — checkers never break a turn
        log.warning("project checkers could not be found", exc_info=True)
        return None
    return ProjectCheckers(base, found, carrier, harness_turn)


def describe(checkers: Iterable[Checker]) -> str:
    """"ruff (.venv), mypy (PATH)" — for logs and receipts."""
    return ", ".join(f"{c.name} ({c.where})" for c in checkers)


__all__ = [
    "CALLED_AS", "CALL_ID_PREFIX", "CHECKERS_BUDGET_S", "Checker", "MAX_FINDING_LINES",
    "MAX_FINDINGS_CHARS", "ProjectCheckers", "TIMEOUT_S", "TSC_MAX_FILES", "TSC_TOO_LARGE", "command_line", "describe", "detect", "findings", "for_turn",
    "safe_arg",
]
