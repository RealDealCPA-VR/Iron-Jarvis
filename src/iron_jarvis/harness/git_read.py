"""READ-ONLY GIT for the team's reviewer (v1.421.0, Wave 1, A4).

The finish check's team reviewer and the CI-fix reviewer held only the
reading tools (``harness/helpers.REVIEW_TOOLS``: read_file, glob, grep,
list_files) and the owner's live review said so: "This session only has read
and search" — it could not see the change the way a person checks one
(``git diff``, ``git log``, ``git blame``). The owner's decision (2026-11-01):
the reviewer gets READ-ONLY commands, but not test runs (they write caches;
running the tests stays the finish check's job). read_file / glob / grep /
list_files already cover cat / ls / find / grep, so this module adds ONE
tool, ``git_read``, and it is git only.

WHAT IT RUNS: an argv, never a shell — ``git`` with one of :data:`ALLOWED`
(status, diff, log, show, blame, ls-files, rev-parse, describe, shortlog,
grep; branch and tag in LIST form only) and the model's own words after it,
each checked:

* REFUSED, in plain words: any other git command, any ``-c``, ``--output`` /
  ``-o`` (a file written), ``--ext-diff`` / ``--textconv`` (a program the
  folder names), ``-O`` / ``--open-files-in-pager`` (an order file / a pager
  program), ``--no-index`` (reads outside the repository), a signature
  shown (``--show-signature``, a ``%G`` / ``%(signature`` format: gpg is a
  program the folder's config can name), ``--exec`` / ``--upload-pack`` /
  ``--recurse-submodules``, a path that leaves the working folder (absolute,
  a drive, a network share, ``..``), and for ``branch`` / ``tag`` anything
  that is not a listing (a name with no list option CREATES one);
* A WORKER MAY HAVE WRITTEN ``.git/config`` — so nothing it says may run a
  program: ``--no-pager``, ``-c core.fsmonitor=false``, ``-c
  core.hooksPath=<the null device>``, ``-c log.showSignature=false``, ``-c
  protocol.allow=never`` (a partial clone's lazy fetch, an ``ext::``
  transport) plus ``GIT_NO_LAZY_FETCH=1``, ``--no-ext-diff --no-textconv``
  where the command takes them, ``--ignore-submodules=all`` for status and
  diff, and every FILTER DRIVER the repository's own config defines (local or
  worktree scope — a ``.gitattributes`` + ``filter.<x>.clean`` runs a program
  on ``git diff`` and ``git status``; measured) emptied with ``-c
  filter.<x>.clean= … required=false``. The user's own global filters (Git
  LFS) are left as they are;
* the environment is a Build session's git environment
  (``git.integration.git_env``: no terminal prompt, never another repository
  through ``GIT_DIR`` & co.) with ``GIT_OPTIONAL_LOCKS=0`` (a status never
  takes the index lock) and no ``GIT_EXTERNAL_DIFF``;
* through ``git.integration.run_git`` → ``core.procs`` (its own Job, bounded
  by :data:`GIT_READ_TIMEOUT_S`, the tree killed on a timeout), off the loop;
* the output MASKED (``detections.redact.mask``) and cut to
  :data:`OUTPUT_CHARS`, saying so.

Allow tier, READONLY (so replay-safe), on no roster: the review-only helper
arms it ALWAYS (``harness/helpers.helper_definition``), and nothing else
does. Its output is the folder's text another model wrote, so it is fenced
and scanned like a read file's (``returns_untrusted_content``)."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

#: The tool's name (``harness/helpers.REVIEW_TOOLS`` holds it).
NAME = "git_read"
#: The git commands it runs (branch / tag in list form only).
ALLOWED: tuple[str, ...] = (
    "status", "diff", "log", "show", "blame", "ls-files", "rev-parse", "describe",
    "shortlog", "grep", "branch", "tag",
)
#: The commands that take ``--no-ext-diff`` / ``--no-textconv``.
NO_EXT_DIFF = frozenset({"diff", "log", "show"})
NO_TEXTCONV = frozenset({"diff", "log", "show", "blame", "grep"})
#: The commands that take ``--ignore-submodules=all`` (a submodule's own
#: config is another repository's).
NO_SUBMODULES = frozenset({"status", "diff"})
#: Bounds.
GIT_READ_TIMEOUT_S = 60.0
OUTPUT_CHARS = 30_000
ERROR_CHARS = 2_000
MAX_ARGS = 60
MAX_ARG_CHARS = 1_000
#: At most this many filter drivers are emptied (a config naming more is
#: refused: something is off).
MAX_FILTERS = 20

#: Options refused for EVERY command: a file written, a program the folder
#: can name, a read outside the repository.
_REFUSED_EXACT = frozenset({
    "-c", "-o", "--output", "--ext-diff", "--textconv", "--no-index", "--show-signature",
    "--open-files-in-pager", "--exec", "--upload-pack", "--recurse-submodules",
})
_REFUSED_PREFIX = (
    "--output=", "--open-files-in-pager=", "--exec=", "--upload-pack=", "--recurse-submodules=",
    "--exec-path",
)
#: ``branch`` / ``tag``: the only options a LISTING takes (anything else
#: makes, moves, deletes, copies or verifies one).
_LIST_FLAGS: dict[str, frozenset[str]] = {
    "branch": frozenset({
        "-a", "--all", "-r", "--remotes", "-l", "--list", "-v", "-vv", "--verbose",
        "--show-current", "--contains", "--no-contains", "--merged", "--no-merged",
        "--points-at", "--sort", "--format", "--color", "--no-color", "--abbrev",
        "--no-abbrev", "--column", "--no-column", "-i", "--ignore-case", "--omit-empty",
    }),
    "tag": frozenset({
        "-l", "--list", "-n", "--contains", "--no-contains", "--merged", "--no-merged",
        "--points-at", "--sort", "--format", "--color", "--no-color", "--column",
        "--no-column", "-i", "--ignore-case", "--omit-empty",
    }),
}
#: The options that make a ``branch`` / ``tag`` call a listing (its other
#: words are then patterns, never a new name).
_LISTING = frozenset({
    "-l", "--list", "-a", "--all", "-r", "--remotes", "--contains", "--no-contains",
    "--merged", "--no-merged", "--points-at", "-n",
})
#: A format that SHOWS a signature runs gpg (a program the config names).
_SIGNATURE_FORMAT = re.compile(r"%G|%\(signature", re.IGNORECASE)
#: A drive ("C:\x", and a drive-relative "C:x") at the start of a word or of
#: an option's value.
_DRIVE = re.compile(r"(?:^|[=,])[A-Za-z]:")
#: Git takes an unambiguous SHORTENED long option (measured on 2.50: ``git
#: grep --open-files`` is ``--open-files-in-pager``), so a word that is the
#: start of a refused option is refused too.
_REFUSED_LONG = tuple(sorted(
    {o for o in _REFUSED_EXACT if o.startswith("--")} | {p.rstrip("=") for p in _REFUSED_PREFIX}
))


#: Real options that happen to start a refused one (an exact name wins over a
#: shortening: ``--text`` is diff's own "treat as text").
_REAL_SHORT = frozenset({"--text"})


def _shortened_refused(head: str) -> bool:
    return (head.startswith("--") and len(head) > 2 and head not in _REAL_SHORT
            and any(o.startswith(head) for o in _REFUSED_LONG))


def refusal(command: str, args: list[str]) -> str:
    """The sentence a call is refused with, or "". Pure."""
    if command not in ALLOWED:
        return (f"git_read runs only read-only git commands ({', '.join(ALLOWED)}); "
                f"\"{command or '(none)'}\" is not one of them.")
    if len(args) > MAX_ARGS:
        return f"git_read takes at most {MAX_ARGS} words after the command."
    list_flags = _LIST_FLAGS.get(command)
    listing = False
    positional = False
    for i, arg in enumerate(args):
        if not isinstance(arg, str):
            return "git_read takes its words as text."
        if "\x00" in arg or len(arg) > MAX_ARG_CHARS:
            return "git_read cannot use that word: it is too long or holds a NUL."
        head = arg.split("=", 1)[0]
        if (arg in _REFUSED_EXACT or head in _REFUSED_EXACT or arg.startswith(_REFUSED_PREFIX)
                or _shortened_refused(head)):
            return f"git_read does not take {head}: it can write a file, run a program or read outside this folder."
        if arg.startswith("-O") or (arg.startswith("-o") and not arg.startswith("--")):
            return f"git_read does not take {arg[:2]}: it can write a file or run a program."
        if arg.startswith("-c") and not arg.startswith("--"):
            return "git_read does not take -c: a setting can run a program."
        prev = args[i - 1] if i else ""
        is_format = head in ("--format", "--pretty") or prev in ("--format", "--pretty")
        if is_format and _SIGNATURE_FORMAT.search(arg):
            return "git_read does not show signatures: checking one runs a program the folder can name."
        if list_flags is not None and arg.startswith("-"):
            if head not in list_flags and not re.fullmatch(r"-n\d+", arg):
                return (f"git_read runs git {command} only to LIST (with --list and the filters a "
                        f"listing takes); {head} would change something.")
            if head in _LISTING or re.fullmatch(r"-n\d+", arg):
                listing = True
        elif list_flags is not None and prev not in ("--sort", "--format", "--contains", "--no-contains",
                                                     "--merged", "--no-merged", "--points-at",
                                                     "--abbrev", "--column", "--color"):
            positional = True
        problem = _path_problem(arg)
        if problem:
            return problem
    if list_flags is not None and positional and not listing:
        return (f"git {command} with a name makes a new {command}; git_read only lists them — add "
                "--list (the names are then patterns).")
    return ""


def _path_problem(arg: str) -> str:
    """A word that names a place OUTSIDE the working folder, by its text: an
    absolute path, a drive, a network share or a ``..`` step ("" when
    none). ``a..b`` (a range of commits) is no step. Pure."""
    value = arg.split("=", 1)[1] if (arg.startswith("--") and "=" in arg) else arg
    if not value:
        return ""
    if value.startswith(("\\\\", "//", "/", "\\")) or _DRIVE.search(value):
        return "git_read reads inside this folder only: give paths relative to it."
    if any(part == ".." for part in re.split(r"[\\/:=,]", value)):
        return "git_read reads inside this folder only: a path may not step up with \"..\"."
    return ""


def _split_command(raw: Any, args: list[str]) -> tuple[str, list[str]]:
    """``command`` as the model wrote it: "diff", or "git diff --stat" (the
    words after the command join ``args``). Never a shell — the words are
    split the way a command line would be."""
    text = " ".join(str(raw or "").split())
    if not text:
        return "", list(args)
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        words = text.split()
    if words and words[0].lower() == "git":
        words = words[1:]
    if not words:
        return "", list(args)
    return words[0].lower(), [*words[1:], *args]


def _repo_filters(cwd: Any, env: dict[str, str]) -> "list[str] | None":
    """The filter drivers the REPOSITORY's own config defines (local or
    worktree scope — what a worker can write), or None when the config could
    not be read. BLOCKING."""
    from ..git.integration import run_git

    got = run_git(["--no-pager", "config", "--show-scope", "--get-regexp", r"^filter\."], cwd,
                  timeout=15.0, env=env, identity=False, encoding="utf-8")
    scoped = True
    if got.code not in (0, 1) or got.timed_out:
        # An older git with no --show-scope: every filter is emptied.
        got = run_git(["--no-pager", "config", "--get-regexp", r"^filter\."], cwd,
                      timeout=15.0, env=env, identity=False, encoding="utf-8")
        scoped = False
        if got.code not in (0, 1) or got.timed_out:
            return None
    names: list[str] = []
    for line in got.out.splitlines():
        line = line.strip()
        if not line:
            continue
        if scoped:
            scope, _, line = line.partition("\t")
            if scope.strip() not in ("local", "worktree"):
                continue
        key = line.split(None, 1)[0] if line.split() else ""
        if not key.lower().startswith("filter.") or key.count(".") < 2:
            continue
        name = key[len("filter."):].rsplit(".", 1)[0]
        if name and name not in names:
            names.append(name)
    return names


def safe_argv(command: str, args: list[str], filters: list[str]) -> list[str]:
    """The whole argv after ``git`` (:func:`run` passes it to ``run_git``).
    Pure."""
    pre = [
        "--no-pager",
        "-c", "core.fsmonitor=false",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "log.showSignature=false",
        "-c", "protocol.allow=never",
        "-c", "core.quotepath=off",
        "-c", "color.ui=never",
    ]
    for name in filters:
        pre += ["-c", f"filter.{name}.clean=", "-c", f"filter.{name}.smudge=",
                "-c", f"filter.{name}.process=", "-c", f"filter.{name}.required=false"]
    safe: list[str] = []
    if command in NO_EXT_DIFF:
        safe.append("--no-ext-diff")
    if command in NO_TEXTCONV:
        safe.append("--no-textconv")
    if command in NO_SUBMODULES:
        safe.append("--ignore-submodules=all")
    return [*pre, command, *safe, *args]


def git_read_env() -> dict[str, str]:
    """A Build session's git environment for a READ (see the module doc)."""
    from ..git.integration import git_env

    env = git_env({"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"})
    for key in ("GIT_EXTERNAL_DIFF", "GIT_PAGER", "PAGER", "GIT_SSH_COMMAND", "GIT_ASKPASS"):
        env.pop(key, None)
    return env


def _masked(text: str) -> str:
    try:
        from ..detections.redact import mask

        return str(mask(text))
    except Exception:  # noqa: BLE001 — a mask that fails: the text is not shown
        return "[output withheld: it could not be checked for secrets]"


def run(cwd: Any, command: str, args: list[str]) -> ToolResult:
    """BLOCKING: one checked, read-only git call in ``cwd``."""
    from ..git.integration import run_git

    env = git_read_env()
    filters = _repo_filters(cwd, env)
    if filters is None:
        return ToolResult(ok=False, error="git_read could not read this folder's git settings, so it ran nothing.")
    if len(filters) > MAX_FILTERS:
        return ToolResult(ok=False, error="This folder's git settings name too many file filters; git_read ran nothing.")
    got = run_git(safe_argv(command, args, filters), cwd, timeout=GIT_READ_TIMEOUT_S, env=env,
                  identity=False, encoding="utf-8")
    label = f"git {command}"
    if got.timed_out:
        return ToolResult(ok=False, error=f"{label} did not finish within {int(GIT_READ_TIMEOUT_S)} seconds, "
                                          "so it was stopped.", data={"command": command})
    if got.code != 0:
        err = _masked(str(got.err or got.out or "").strip())
        if "not a git repository" in err.lower():
            return ToolResult(ok=False, error="This folder is not in a git repository.", data={"command": command})
        if len(err) > ERROR_CHARS:
            err = err[:ERROR_CHARS].rstrip() + " […]"
        return ToolResult(ok=False, error=f"{label} failed: {err or 'it gave no reason'}",
                          data={"command": command, "exit_code": got.code})
    out = _masked(str(got.out or ""))
    total = len(out)
    cut = total > OUTPUT_CHARS
    if cut:
        out = out[:OUTPUT_CHARS].rstrip() + (
            f"\n\n[cut: the first {OUTPUT_CHARS:,} of {total:,} characters were shown — narrow it "
            "with a path, -n, --stat or --name-only]"
        )
    return ToolResult(ok=True, output=out or "(git printed nothing)",
                      data={"command": command, "chars": total, "truncated": cut})


class GitReadTool(Tool):
    name = NAME
    description = (
        "Ask git about this folder, read-only: status, diff, log, show, blame, ls-files, "
        "rev-parse, describe, shortlog, grep, and branch / tag listings. `command` is the git "
        "command (\"diff\"), `args` its words ([\"--stat\", \"HEAD~1\"]). It never changes the "
        "repository, never writes a file and never runs a program; paths stay inside this folder. "
        "It does not run tests."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True  # a diff is text another model wrote
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": list(ALLOWED),
                "description": "The git command: " + ", ".join(ALLOWED) + ".",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The words after the command, one per item (paths relative to the folder).",
            },
        },
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw_args = args.get("args")
        if raw_args is None:
            words: list[str] = []
        elif isinstance(raw_args, list):
            words = [str(a) if not isinstance(a, str) else a for a in raw_args]
        elif isinstance(raw_args, str):
            try:
                words = shlex.split(raw_args, posix=True)
            except ValueError:
                words = raw_args.split()
        else:
            return ToolResult(ok=False, error="`args` is a list of words.")
        command, words = _split_command(args.get("command"), words)
        problem = refusal(command, words)
        if problem:
            return ToolResult(ok=False, error=problem, data={"command": command, "refused": True})
        cwd = getattr(ctx, "workspace", None)
        if cwd is None or not await asyncio.to_thread(lambda: os.path.isdir(str(cwd))):
            return ToolResult(ok=False, error="git_read has no folder to read here.")
        return await asyncio.to_thread(run, cwd, command, words)


def git_read_tools() -> list[Tool]:
    return [GitReadTool()]


__all__ = [
    "ALLOWED", "GIT_READ_TIMEOUT_S", "GitReadTool", "NAME", "OUTPUT_CHARS", "git_read_env",
    "git_read_tools", "refusal", "run", "safe_argv",
]
