"""The ``;`` guard for a model's command that Windows cmd.exe runs (v1.386.0).

A model trained on bash writes ``rm -f server.port; python server.py``. cmd.exe
has no ``;`` operator: it hands the WHOLE rest of the line to ``rm`` (Git's
``rm.exe``, on the user's PATH in a Build session since v1.377.0), which reads
``-f``, ``server.port;``, ``python`` and ``server.py`` as file names and
DELETES ``server.py`` (the Phase 1 benchmark, ``process-http-server``, twice).
The same happens to cmd's own ``del`` / ``copy`` / ``mkdir`` (``;`` is one of
their argument separators), to ``git checkout -- a; python b`` (the second
file is overwritten from the index) and to an installer (``npm install x; npm
start`` installs packages named ``npm`` and ``start``).

:func:`semicolon_refusal` answers ONE plain sentence for such a command, "" for
any other, before anything runs. It reads the text the way cmd.exe runs it:

* only the FIRST line runs (``cmd /c`` stops at the first line break -- measured),
  so text after it is ignored;
* the line is split into commands at cmd's own operators -- ``&``, ``&&``,
  ``|``, ``||`` -- outside double quotes, ``^`` escaping the next character
  (cmd ignores single quotes);
* a command is judged by its PROGRAM (path, quotes and ``.exe`` removed),
  after a wrapper that hands the rest to another program (``xargs``, ``sudo``,
  ``env``, ``call``, ``start``...) and inside ``cmd /c "..."``;
* only a program of :data:`FILE_PROGRAMS` / :data:`CMD_FILE_COMMANDS` /
  :data:`GIT_FILE_SUBCOMMANDS` / :data:`INSTALLERS` (or an in-place ``sed -i``
  / ``perl -i``) counts, and only an UNQUOTED ``;`` with more words after it:
  for a program that parses its own arguments (``rm.exe``, ``git.exe``,
  ``npm``...) a ``;`` inside ``"..."`` or ``'...'`` is part of a word, and a
  ``;`` with no space after it stays inside one word (``a;b`` is one file
  name); for cmd's own built-ins (``del``, ``copy``, ``mkdir``...) only double
  quotes protect, and any ``;`` separates.

So ``set PATH=%PATH%;C:\\x``, ``findstr "a;b" f``, ``echo a;b``, ``python -c
"import x; print(1)"`` and ``sleep 2; cat x`` (harmless, if broken) all run.

HONEST LIMITS: this reads the command's text. A script file, an ``if`` /
``for`` body and a variable that expands to ``;`` are not read. It is a
guardrail against a careless model, not a sandbox. Where Git for Windows'
bash runs the command instead (the IJ harness's Claude dialect,
``sandbox/git_bash``), ``;`` separates commands and this guard does not apply.
"""

from __future__ import annotations

import re

#: POSIX programs (Git puts them on PATH) whose arguments name files they
#: delete, move, overwrite, create or change.
FILE_PROGRAMS = frozenset({
    "rm", "rmdir", "unlink", "shred", "mv", "cp", "ln", "install", "touch", "mkdir",
    "chmod", "chown", "chgrp", "truncate", "tee", "rsync", "tar", "dd", "patch",
})
#: In-place editors: a file program only with ``-i`` (``sed -i``, ``perl -pi``).
IN_PLACE_EDITORS = frozenset({"sed", "perl"})
#: cmd.exe's OWN file commands: cmd parses their arguments itself, so ``;``
#: separates them even with no space (``del a;b`` deletes both) and single
#: quotes are plain characters. (``mkdir`` / ``rmdir`` are cmd built-ins:
#: they win over Git's ``mkdir.exe`` / ``rmdir.exe`` in cmd.)
CMD_BUILTINS = frozenset({"del", "erase", "rd", "rmdir", "md", "mkdir", "move", "copy", "ren", "rename"})
#: The copy programs Windows ships (they split their own arguments).
WINDOWS_COPY_PROGRAMS = frozenset({"xcopy", "robocopy"})
CMD_FILE_COMMANDS = CMD_BUILTINS | WINDOWS_COPY_PROGRAMS
#: git commands whose arguments are files they delete, overwrite or put back.
GIT_FILE_SUBCOMMANDS = frozenset({"rm", "mv", "checkout", "restore", "clean", "reset", "stash"})
#: Package installers: the words after ``;`` become package names fetched
#: from a public registry (and their install scripts run).
INSTALLERS: dict[str, frozenset[str]] = {
    "npm": frozenset({"install", "i", "add", "ci", "uninstall", "remove", "rm", "un", "update", "up", "upgrade"}),
    "pnpm": frozenset({"install", "i", "add", "remove", "rm", "un", "uninstall", "update", "up"}),
    "yarn": frozenset({"install", "add", "remove", "upgrade", "up"}),
    "bun": frozenset({"install", "i", "add", "remove", "rm", "update"}),
    "pip": frozenset({"install", "uninstall", "download"}),
    "pip3": frozenset({"install", "uninstall", "download"}),
    "uv": frozenset({"add", "remove", "pip", "tool"}),
}
#: Programs that run ``python -m pip install x; y``.
_PYTHONS = frozenset({"python", "python3", "py"})
#: Wrappers that hand the rest of the command to another program.
_WRAPPERS = frozenset({"sudo", "env", "nohup", "time", "nice", "command", "exec", "xargs", "call", "start"})
#: Options of those wrappers that take the next word as their value.
_WRAPPER_VALUE_OPTS = frozenset({"-n", "-I", "-d", "-L", "-P", "-a", "-E", "-s", "-u"})
_GIT_VALUE_OPTS = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env",
                             "--exec-path", "--super-prefix"})
_EXT_RX = re.compile(r"\.(?:exe|com|cmd|bat)$", re.IGNORECASE)
#: A redirection word (``>out``, ``2>&1``, ``<in``) or a bare operator.
_REDIRECT_RX = re.compile(r"^\d?(?:>>?|<)")
_BARE_REDIRECTS = frozenset({">", ">>", "<", "1>", "2>", "1>>", "2>>"})
#: How deep ``cmd /c "cmd /c ..."`` is followed.
_MAX_DEPTH = 3


def refusal_sentence(program: str) -> str:
    """The ONE sentence the model reads (a failed tool result)."""
    return (
        f"This shell is Windows cmd.exe: ';' does not separate commands here, so `{program}` would "
        "also receive the words after it. Nothing was run; use '&&' between commands."
    )


def _first_line(command: str) -> str:
    """What ``cmd /c`` runs: the text up to the first line break."""
    return re.split(r"[\r\n]", command, maxsplit=1)[0]


def _segments(line: str) -> list[str]:
    """``line`` split at cmd.exe's own operators (``& && | ||``) outside double
    quotes; ``^`` escapes the next character outside quotes."""
    out: list[str] = []
    buf: list[str] = []
    quoted = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            quoted = not quoted
            buf.append(ch)
        elif not quoted and ch == "^" and i + 1 < len(line):
            buf.append(line[i + 1])
            i += 1
        elif not quoted and ch in "&|":
            out.append("".join(buf))
            buf = []
            if i + 1 < len(line) and line[i + 1] == ch:
                i += 1
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s for s in out if s.strip()]


def _words(text: str) -> list[tuple[str, int, int, bool]]:
    """``(word, start, end, was_quoted)`` for each word of ``text``: whitespace
    splits it, ``"..."`` keeps spaces inside a word (cmd's rule for finding a
    program); the word comes back without its double quotes."""
    out: list[tuple[str, int, int, bool]] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        word: list[str] = []
        quoted = False
        had_quote = False
        while i < n and (quoted or not text[i].isspace()):
            ch = text[i]
            if ch == '"':
                quoted = not quoted
                had_quote = True
            else:
                word.append(ch)
            i += 1
        out.append(("".join(word), start, i, had_quote))
    return out


def _program_name(word: str) -> str:
    name = re.split(r"[\\/]", word.strip())[-1].lower()
    return _EXT_RX.sub("", name)


def _leading(text: str) -> str:
    """``text`` without cmd's leading ``@`` and ``(``."""
    t = text.lstrip()
    while t[:1] in ("@", "("):
        t = t[1:].lstrip()
    return t


def _unquoted_semicolon(text: str, *, single_quotes: bool, needs_space: bool) -> bool:
    """Does ``text`` hold a ``;`` outside quotes with a word after it — after
    whitespace (a program that splits its own arguments: ``a;b`` stays one
    word), or anywhere (cmd's own built-ins: ``;`` separates)?"""
    # The last character that is a word (not space, and for a built-in not
    # another ';'): a ';' before it has words after it. One pass each way.
    last_word = -1
    for j in range(len(text) - 1, -1, -1):
        if not text[j].isspace() and (needs_space or text[j] != ";"):
            last_word = j
            break
    quote = ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch == '"' or (single_quotes and ch == "'"):
            quote = ch
            continue
        if ch != ";" or i >= last_word:
            continue
        if not needs_space or text[i + 1].isspace():
            return True
    return False


def _inner_of_cmd(text: str) -> str:
    """The command ``cmd /c <rest>`` runs: ``<rest>`` with one layer of
    surrounding double quotes removed (cmd's own /c rule for a quoted line)."""
    t = text.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1]
    return t


def _git_subcommand(args: list[str]) -> str:
    i = 0
    while i < len(args):
        a = args[i]
        if a in _GIT_VALUE_OPTS:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a.lower().rstrip(";")
    return ""


def _guarded(prog: str, args: list[str]) -> bool:
    """Is ``prog args`` a command whose arguments it acts on (files, packages)?"""
    if prog in FILE_PROGRAMS:
        return True
    if prog in IN_PLACE_EDITORS:
        return any(re.match(r"^-[A-Za-z]*i", a) or a.startswith("--in-place") for a in args)
    if prog == "git":
        return _git_subcommand(args) in GIT_FILE_SUBCOMMANDS
    if prog in INSTALLERS:
        sub = next((a.lower() for a in args if not a.startswith("-")), "")
        return sub.rstrip(";") in INSTALLERS[prog]
    if prog in _PYTHONS:
        low = [a.lower() for a in args]
        if "-m" in low:
            k = low.index("-m")
            if k + 1 < len(low) and low[k + 1] in ("pip", "pip3"):
                sub = next((a for a in low[k + 2:] if not a.startswith("-")), "")
                return sub.rstrip(";") in INSTALLERS["pip"]
    return False


def _judge(segment: str, depth: int) -> str:
    """The program one cmd command would feed a destructive ``;``, or ""."""
    text = _leading(segment)
    words = _words(text)
    i = 0
    while i < len(words):
        word, _start, end, was_quoted = words[i]
        if not was_quoted and _REDIRECT_RX.match(word):
            i += 2 if word in _BARE_REDIRECTS else 1
            continue
        prog = _program_name(word)
        if prog == "cmd":
            # `cmd /c "rm -f a; b"`: the inner command runs in cmd too.
            for k in range(i + 1, len(words)):
                opt = words[k][0]
                if opt.lower() in ("/c", "/k", "/r"):
                    if depth >= _MAX_DEPTH:
                        return ""
                    return _scan(_inner_of_cmd(text[words[k][2]:]), depth + 1)
                if not opt.startswith("/"):
                    break
            return ""
        if prog in _WRAPPERS:
            i += 1
            titled = False
            while i < len(words):
                w, _s, _e, q = words[i]
                if prog == "start" and q and not titled:
                    titled = True  # start "title" program ...
                    i += 1
                    continue
                if w.startswith("-") or (prog == "start" and w.startswith("/")):
                    i += 2 if w in _WRAPPER_VALUE_OPTS else 1
                    continue
                if prog == "env" and "=" in w and not w.startswith("="):
                    i += 1
                    continue
                break
            continue
        rest = text[end:]
        args = [w for w, _s, _e, _q in words[i + 1:]]
        if prog in CMD_BUILTINS:
            return prog if _unquoted_semicolon(rest, single_quotes=False, needs_space=False) else ""
        if prog in WINDOWS_COPY_PROGRAMS:
            return prog if _unquoted_semicolon(rest, single_quotes=False, needs_space=True) else ""
        if _guarded(prog, args) and _unquoted_semicolon(rest, single_quotes=True, needs_space=True):
            return prog
        return ""
    return ""


def _scan(line: str, depth: int = 0) -> str:
    for segment in _segments(_first_line(line)):
        prog = _judge(segment, depth)
        if prog:
            return prog
    return ""


def semicolon_refusal(command: object) -> str:
    """The refusal for a command whose unquoted ``;`` would feed a file
    program (or an installer) the words after it in cmd.exe, else "". Pure,
    cheap, never raises."""
    try:
        text = str(command or "")
        if ";" not in _first_line(text):
            return ""
        prog = _scan(text)
    except Exception:  # noqa: BLE001 — a guard that crashes must not stop a turn
        return ""
    return refusal_sentence(prog) if prog else ""


def is_interactive_cmd(command: object) -> bool:
    """Is ``command`` (what ``process_start`` started) an interactive cmd.exe
    reading its input as command lines (``cmd``, ``cmd /k ...``, never ``cmd
    /c ...``)? Then what ``process_write`` types into it is a cmd line too."""
    try:
        words = _words(_leading(_first_line(str(command or ""))))
        if not words or _program_name(words[0][0]) != "cmd":
            return False
        return not any(w.lower() == "/c" for w, _s, _e, _q in words[1:])
    except Exception:  # noqa: BLE001
        return False


__all__ = ["is_interactive_cmd", "refusal_sentence", "semicolon_refusal"]
