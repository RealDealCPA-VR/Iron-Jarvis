"""Native subprocess sandbox (§16).

Best-effort isolation: enforces ``timeout`` and, when ``modify_env == 'deny'``
(§17), a scrubbed minimal environment. Hard network/CPU/memory isolation
requires the Docker runtime — see :mod:`docker_runtime`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..core import procs
from .base import Sandbox, SandboxResult
from .policy import SandboxPolicy


def _fallback_codecs() -> tuple[str, ...]:
    """The code pages a command's non-UTF-8 output may be in, OEM first.

    Windows: the OEM page (``GetOEMCP``, 437/850 on a US/Western box) -- what
    cmd.exe's built-ins (``dir``, ``echo``) write into a pipe -- AND the ANSI
    page (``GetACP``, 1252) -- what ``type`` of an Excel "CSV (Comma
    delimited)" or a legacy accounting export prints. Elsewhere: the locale's
    preferred encoding."""
    if os.name == "nt":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            oem, ansi = f"cp{k32.GetOEMCP()}", f"cp{k32.GetACP()}"
            return (oem,) if oem == ansi else (oem, ansi)
        except Exception:  # noqa: BLE001 -- no kernel32: fall to the locale
            pass
    import locale

    return (locale.getpreferredencoding(False) or "utf-8",)


_NON_ASCII = re.compile(r"[^\x00-\x7f]")
#: What OEM 437 makes of ANSI's curly quotes, dashes and tilde (0x91-0x98).
_FROM_ANSI_PUNCT = frozenset("ùûæÆòÿ")
#: What OEM 850 (a Western-European box) makes of ANSI's Ñ, í, è, ç, ë and Ó
#: (0xD1/0xED/0xE8/0xE7/0xEB/0xD3): Latin letters, but Icelandic ones.
_FROM_ANSI_850 = frozenset("ÐÝÞþÙË")
#: The judge reads at most this much of an output; the winner decodes it all.
_JUDGE_BYTES = 256 * 1024


def _plausibility(text: str) -> float:
    """How much *text* reads like words, judged by WORD SHAPE (v1.288.0).

    Counting accented letters alone was not enough: the two pages swap
    letters for punctuation in BOTH directions. ANSI's curly quotes and
    dashes (0x91-0x97, all over Excel CSVs and Word-typed memos) are the
    letters "æÆôöòûù" in OEM 437, and OEM's "ö" (0x94, "Björn") is ANSI's
    closing quote. What tells them apart is where the character sits:

    * a Latin letter inside a word is a spelled word (+1; half at a word's
      edge, and half again for "ß", Latin Extended-A and the letters OEM makes
      of ANSI's dashes/quotes/tilde, "ùûæÆòÿ" -- the wrong page's favourites);
    * a Latin letter with no letter beside it is a dash or quote read wrong
      (" – " -> " û ") (-1); so is a lower-case accent in a camel hump
      ("JohnáSmith" is a no-break space read wrong), a CAPITAL accent (or
      þ/ð) inside a lower-case word ("JosÚ", "PÚrez", "Franþois", "ZoÙ":
      OEM 850's reading of ANSI's é/í/ç/ë) and a run of one accent ("ÄÄÄ" is
      OEM box drawing read as ANSI); OEM 850's Ð/Ý/Þ/þ/Ù/Ë weigh half, so
      all-caps "MUÑOZ" beats "MUÐOZ";
    * punctuation or a symbol BETWEEN two letters is a letter read wrong
      ("Bj”rn") (-1); Greek/maths -- what OEM makes of ANSI's accented
      letters -- never belongs (-1), and box drawing belongs only beside more
      box drawing (``tree``) -- a run of it with a letter at BOTH ends
      ("N┌╤EZ": OEM 437's reading of two adjacent ANSI capitals) is a word
      read wrong (-1 each; ``tree`` draws at a line's start or after spaces);
    * a replacement character is a byte the page does not define (-2), and a
      no-break space is a space;
    * typography where typography goes is a sign of the RIGHT page (+1): an
      opening quote before a word, a closing quote after one, an apostrophe
      inside one ("O’Brien", "TY’25": +1.5), a dash or bullet between spaces,
      a dash between two words or numbers (Word's "--" autocorrect,
      "Jan–Mar"), an ellipsis after a word, "§" before a number, "®"/"™"
      after a name.
    """
    score = 0.0
    last = len(text) - 1
    box_end, box_wordy = 0, False  # the box-drawing run being walked, measured once
    for m in _NON_ASCII.finditer(text):
        i = m.start()
        ch = text[i]
        if ch == "\ufffd":
            score -= 2
            continue
        if ch == "\xa0":  # a no-break space pasted from the web: a space
            continue
        prev = text[i - 1] if i > 0 else " "
        nxt = text[i + 1] if i < last else " "
        left, right = prev.isalpha(), nxt.isalpha()
        o = ord(ch)
        if 0xC0 <= o <= 0x24F and o not in (0xD7, 0xF7):  # Latin letters, no x/÷
            if prev == ch or nxt == ch:
                score -= 1  # "ÄÄÄ": a repeated accent is drawing, not a word
            elif left and right and ch.islower() and prev.islower() and nxt.isupper():
                score -= 1  # "JohnáSmith", "JanûMar": a camel hump is not a word
            elif (ch.isupper() or ch in "þð") and (
                prev.islower() or (left and nxt.islower())
            ):
                score -= 1  # "JosÚ", "PÚrez", "Franþois": a capital inside a lower-case word is a hump too
            else:
                rare = (
                    ch == "ß"
                    or 0x100 <= o <= 0x17F
                    or ch in _FROM_ANSI_PUNCT
                    or ch in _FROM_ANSI_850
                )
                weight = 0.5 if rare else 1.0
                if left or right:
                    score += weight * (1.0 if (left and right) else 0.5)
                else:
                    score -= 1
        elif 0x2500 <= o <= 0x259F:  # box drawing / shading
            if i >= box_end:  # the run's first char: measure it once
                box_end = i + 1
                while box_end <= last and 0x2500 <= ord(text[box_end]) <= 0x259F:
                    box_end += 1
                box_wordy = left and box_end <= last and text[box_end].isalpha()
            if box_wordy:
                score -= 1  # "N┌╤EZ": a drawing with a letter at both ends is a word read wrong
            else:
                boxy = 0x2500 <= ord(prev) <= 0x259F or 0x2500 <= ord(nxt) <= 0x259F
                score += 1 if boxy else -1
        elif 0x370 <= o <= 0x3FF or 0x2200 <= o <= 0x22FF or 0x2300 <= o <= 0x23FF:
            score -= 1
        elif ch in "‘“":
            if not prev.isalnum() and nxt.isalnum():
                score += 1
            elif left and right:
                score -= 1
        elif ch in "’”":
            if prev.isalnum() and nxt.isalnum():
                score += 1.5 if ch == "’" else -1  # an apostrophe; a quote is not
            elif prev.isalnum() and not nxt.isalnum():
                score += 1
        elif ch in "–—•":
            if prev.isspace() and nxt.isspace():
                score += 1
            elif prev.isalnum() and nxt.isalnum():
                score += 1 if ch != "•" else -1
        elif ch == "…":
            if left and not right:
                score += 1
            elif left and right:
                score -= 1
        elif ch == "§":
            score += 1 if (nxt.isspace() or nxt.isdigit()) else 0
        elif ch in "®™":
            score += 1 if prev.isalnum() else 0
        elif ch in "ºª":
            score -= 0 if prev.isdigit() else 1
        elif left and right:
            score -= 1
    return score


def _decode_fallback(value: bytes) -> str:
    """Decode bytes that are NOT valid UTF-8 (v1.288.0, agents-01).

    Every candidate page decodes with ``errors="replace"`` (never raises, never
    empties) and the one that reads most like words (:func:`_plausibility`)
    wins; a tie keeps the OEM page, which is what cmd's own built-ins write
    into a pipe. Measured, not assumed: ``dir`` into a pipe uses the OEM page
    whatever ``chcp`` says (and ``chcp`` in a child flips the DAEMON's shared
    console too), so the page cannot be fixed at the source and has to be
    judged here. Measured by ``scripts/measure_codepage_judge.py`` (client
    names, all-caps too, in dir/csv/memo/tree lines, written in BOTH pages of
    each pair): on the US pair (OEM 437 + ANSI 1252) 1133/1154 exact, the
    losses all OEM lines whose ONLY accent is ambiguous ("ß" reads "á", a
    final "à" reads "…"); on the Western-European pair (OEM 850 + ANSI 1252)
    1262/1346, the extra losses letter-for-letter swaps that spell a word
    BOTH ways, which shape cannot split: ANSI "BJÖRN"/"SØREN"/"FRANÇOIS"/
    "Çelik" read "BJÍRN"/"SÏREN"/"FRANÃOIS"/"Ãelik" (the bytes of OEM
    "GARCÍA"/"NAÏVE"/"JOÃO"), and, the price of reading ANSI "Óscar"/"José"
    right, OEM "NOËL"/"PIÙ"/"Ýmir" read "NOÓL"/"PIë"/"ímir" and ANSI
    "Sigþór" reads "Sig■¾r" (þ is a lower-case letter the hump rule treats
    as a capital). Only the first :data:`_JUDGE_BYTES` are judged, so
    a 5 MB output costs the same as a 256 KB one."""
    sample = value[:_JUDGE_BYTES]  # judge a bounded sample; decode the whole
    best: str | None = None
    best_score = float("-inf")
    for codec in _fallback_codecs():
        try:
            score = _plausibility(sample.decode(codec, "replace"))
        except LookupError:  # a page Python has no codec for
            continue
        if score > best_score:  # strictly greater: a tie keeps the earlier (OEM)
            best, best_score = codec, score
    return value.decode(best or "utf-8", "replace")


def _as_text(value: object) -> str:
    """Coerce subprocess stdout/stderr (bytes | str | None) to text -- the ONE
    decode for every command's output (v1.288.0, agents-01).

    ``text=True`` decoded pipes with the locale codec (cp1252 here) INSIDE
    ``communicate()``'s reader thread: one byte it cannot map (cmd's OEM 0x90
    for "É", UTF-8's 0x8D inside "Í") killed that thread, stdout came
    back ``""`` with returncode 0, and the shell reported an EMPTY folder as a
    success. Now bytes are captured and decoded here: strict UTF-8 first
    (``type`` of a UTF-8 file, most modern tools), else the OEM or ANSI page
    (:func:`_decode_fallback`) with ``errors="replace"`` -- a decode can never
    again empty the output."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return _decode_fallback(value)
    return str(value)


def child_env(base: dict[str, str] | None) -> dict[str, str]:
    """The environment a command runs with: ``base`` (``None`` = inherit the
    daemon's) plus ``PYTHONIOENCODING=utf-8`` unless one is already set
    (v1.288.0, agents-01).

    With bytes decoded by :func:`_as_text`, a Python child writing its piped
    stdout in the ANSI page (cp1252: "é" = 0xE9) fails the strict UTF-8 read
    and rides the OEM-vs-ANSI guess of :func:`_decode_fallback`. Asking Python
    children for UTF-8 keeps them on the exact strict path (and a character
    cp1252 lacks, like "→", no longer crashes their ``print``). Stdio only:
    ``open()`` defaults are untouched.

    ARGV COMMANDS ONLY (``run_code``, ``custom:*``): their stdin is DEVNULL and
    their stdout is our pipe. NEVER the ``shell`` tool: a shell line pipes and
    redirects, where a forced UTF-8 breaks ``type x.csv | python`` on an ANSI
    byte and makes ``python export.py > out.csv`` write UTF-8 Excel misreads."""
    env = dict(os.environ if base is None else base)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def scrubbed_env() -> dict[str, str]:
    """Minimal environment for ``modify_env == 'deny'`` (§17).

    Drops every inherited variable but keeps the bare minimum required to launch
    an interpreter: on Windows ``SystemRoot``/``COMSPEC``/``PATHEXT`` + a small
    System32 PATH; on POSIX a minimal PATH. The running interpreter's directory
    is prepended so a bare ``python`` still resolves.
    """
    env: dict[str, str] = {}
    if os.name == "nt":
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        for key in ("SystemRoot", "COMSPEC", "PATHEXT"):
            val = os.environ.get(key)
            if val:
                env[key] = val
        env.setdefault("SystemRoot", windir)
        base_path = os.pathsep.join([os.path.join(windir, "System32"), windir])
    else:
        base_path = "/usr/bin:/bin"
    py_dir = str(Path(sys.executable).parent) if sys.executable else ""
    env["PATH"] = (py_dir + os.pathsep + base_path) if py_dir else base_path
    return env


# --------------------------------------------------------------------------- #
# a Build session's commands: the user's own tools (v1.377.0)
# --------------------------------------------------------------------------- #
#
# THE USER'S DECISION (2026-10-23, IJ harness part 2 Phase 6): in a Build
# session, the agent's ``shell`` and program tools get the user's normal PATH
# and home folder, so ``git``, ``node``, ``npm`` and ``python`` run as they do
# in the user's own terminal and ``git commit`` uses their own identity;
# SECRET VARIABLES ARE STILL STRIPPED; chat, agents and every unattended run
# keep :func:`scrubbed_env` exactly as before. WHICH run is a Build session is
# decided by ``harness/command_env`` (keyed on the session); THIS is only what
# the environment is.

#: Words that mark a variable whose VALUE is a secret, read in its NAME
#: (upper-cased): a key, a token, a password, a session or cookie, a private
#: key, a credential. ``ASKPASS``: a program that answers git's / ssh's
#: password prompt for whatever started the app (Claude Code sets one) — a
#: prompt the user never sees.
SECRET_NAME_WORDS: tuple[str, ...] = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PASSPHRASE", "CREDENTIAL",
    "SESSION", "COOKIE", "PRIVATE", "ASKPASS",
)
#: ``AUTH`` anywhere in a name (``OAUTH``, ``AUTHENTICATION``,
#: ``AUTHORIZATION``, ``SSH_AUTH_SOCK``, ``NPM_CONFIG__AUTH``) — but never
#: ``AUTHOR`` (``GIT_AUTHOR_NAME`` / ``GIT_AUTHOR_EMAIL`` are the user's own
#: commit identity, the very thing this environment exists to carry).
_AUTH_RX = re.compile(r"AUTH(?!OR(?:S|ED)?(?:_|$))")
#: A whole ``_``-separated part that is a secret's name: a personal access
#: token (``AZURE_DEVOPS_EXT_PAT``), a DSN (it carries a key).
SECRET_NAME_PARTS: frozenset[str] = frozenset({"PAT", "DSN"})
#: Every variable of these families is dropped: model and cloud vendors'
#: keys and endpoints, the forges' tokens, this app's own internals (its
#: install token among them), Iron-Proxy's, Playwright's (the app's own
#: browser driver), a parent Claude Code session's settings and the
#: tracing / packaging internals of the processes that started the daemon.
DROP_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_", "AZURE_", "AWS_", "GH_", "GITHUB_",
    "HF_", "HUGGING_FACE_", "HUGGINGFACE_", "IRONJARVIS_", "IRON_PROXY_", "PLAYWRIGHT_",
    "CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "SENTRY", "_PYI", "_MEIPASS", "PYINSTALLER_",
)
#: Exact names dropped: Electron's own (``ELECTRON_RUN_AS_NODE`` makes every
#: Electron app — VS Code's ``code`` too — run as plain node; the crash
#: handler's pipe), the dev daemon's own virtual environment (``uv run`` sets
#: it; the user's terminal has none), a parent's trace context.
DROP_NAMES: frozenset[str] = frozenset({
    "ELECTRON_RUN_AS_NODE", "CHROME_CRASHPAD_PIPE_NAME", "VIRTUAL_ENV",
    "UV_RUN_RECURSION_DEPTH", "BAGGAGE",
})


def secret_name(name: str) -> bool:
    """Is ``name`` a variable :func:`build_env` drops (a secret, a vendor's,
    this app's own, a parent's)? Case-insensitive, as Windows is. Never
    reads a value."""
    up = str(name or "").upper()
    if not up:
        return True
    if up in DROP_NAMES or any(up.startswith(p) for p in DROP_PREFIXES):
        return True
    if any(word in up for word in SECRET_NAME_WORDS) or _AUTH_RX.search(up):
        return True
    return any(part in SECRET_NAME_PARTS for part in re.split(r"[^A-Z0-9]+", up))


def _own_bin_dirs() -> list[str]:
    """Folders on PATH that belong to THIS app, not the user: a dev daemon's
    own virtual environment (``uv run`` puts its Scripts first — ``python``
    would be Iron Jarvis's, with Iron Jarvis's packages) and a frozen
    daemon's bundle. Normalised for comparing."""
    out: list[str] = []
    try:
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                out.append(os.path.normcase(os.path.normpath(meipass)))
        elif sys.prefix != getattr(sys, "base_prefix", sys.prefix):
            out.append(os.path.normcase(os.path.normpath(
                os.path.join(sys.prefix, "Scripts" if os.name == "nt" else "bin"))))
    except Exception:  # noqa: BLE001 — nothing of ours named: PATH as it is
        return []
    return out


def _user_path(path: str) -> str:
    own = _own_bin_dirs()
    if not own:
        return path
    kept = []
    for entry in str(path or "").split(os.pathsep):
        norm = os.path.normcase(os.path.normpath(entry)) if entry.strip() else ""
        if norm and any(norm == o or norm.startswith(o.rstrip("\\/") + os.sep) for o in own):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def build_env(base: "dict[str, str] | None" = None) -> dict[str, str]:
    """The environment a BUILD SESSION's commands run with (v1.377.0, the
    user's decision): the daemon's own environment (``base``; ``None`` = this
    process's — the desktop hands the daemon the user's) with every variable
    :func:`secret_name` matches removed — secrets by their NAME, the vendors'
    and this app's own (its install token), Iron-Proxy's, Playwright's,
    Electron's, a parent Claude Code session's markers
    (``drop_claude_session_markers``) and the git variables that would point
    every git call at ANOTHER repository (``git.integration._ELSEWHERE_VARS``,
    what ``git_env`` drops). PATH loses only this app's own folders (a dev
    daemon's virtual environment). Plus ``GIT_TERMINAL_PROMPT=0``, as every
    Build git call has: no git waits on a password typed into a terminal
    nobody sees (a credential WINDOW the user's own git opens is allowed).
    Everything else — PATH, HOME / USERPROFILE / HOMEDRIVE / HOMEPATH,
    APPDATA / LOCALAPPDATA, TEMP, the user's own git / npm / pip settings —
    stays, so git, node, npm, pnpm, python, pip and uv find their tools,
    config and sign-ins. NEVER logs or returns a value it dropped."""
    from ..core.claude_markers import drop_claude_session_markers
    from ..git.integration import _ELSEWHERE_VARS

    source = os.environ if base is None else base
    env = {str(k): str(v) for k, v in source.items() if not secret_name(str(k))}
    drop_claude_session_markers(env)
    elsewhere = {v.upper() for v in _ELSEWHERE_VARS}
    for name in [k for k in env if k.upper() in elsewhere]:
        env.pop(name, None)
    for name in [k for k in env if k.upper() == "PATH"]:
        env[name] = _user_path(env[name])
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


#: The tools a coding session usually reaches for, in the order the
#: description names them.
BUILD_TOOLS: tuple[str, ...] = ("git", "gh", "node", "npm", "npx", "pnpm", "yarn", "python", "py",
                                "pip", "uv")
#: How long :func:`build_env_tools` remembers what it found.
BUILD_TOOLS_TTL_S = 60.0
_TOOLS_CACHE: dict[str, Any] = {}


def _store_alias(found: str) -> bool:
    """A Microsoft Store "App execution alias" (``…\\WindowsApps\\python.exe``):
    it OPENS THE STORE when Python is not installed — not a tool the user has."""
    norm = found.replace("/", "\\").lower()
    return "\\microsoft\\windowsapps\\" in norm and Path(found).name.lower().startswith("python")


def build_env_tools(env: "dict[str, str] | None" = None) -> tuple[str, ...]:
    """Which of :data:`BUILD_TOOLS` resolve on the Build environment's PATH
    (``shutil.which`` with that PATH), plus ``"posix"`` when ``ls`` does too
    (Git's Unix tools on PATH). BLOCKING (a stat per folder and extension):
    callers on the event loop go through ``asyncio.to_thread``; remembered
    :data:`BUILD_TOOLS_TTL_S` per PATH. Never raises (nothing found = ())."""
    import shutil
    import time as _time

    try:
        env = build_env() if env is None else env
        path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
        hit = _TOOLS_CACHE.get(path)
        now = _time.monotonic()
        if hit is not None and now - hit[0] < BUILD_TOOLS_TTL_S:
            return hit[1]
        found: list[str] = []
        for name in (*BUILD_TOOLS, "ls"):
            where = shutil.which(name, path=path)
            if not where or _store_alias(where):
                continue
            found.append("posix" if name == "ls" else name)
        out = tuple(found)
        _TOOLS_CACHE.clear()  # one PATH at a time is enough
        _TOOLS_CACHE[path] = (now, out)
        return out
    except Exception:  # noqa: BLE001 — unknown is "found nothing", never a claim
        return ()


def build_env_description(tools: "tuple[str, ...] | list[str]", os_name: str | None = None) -> str:
    """Model-facing truth about a Build session's commands (v1.377.0): the
    user's own PATH and home folder, the tools REALLY found on it (``tools``
    from :func:`build_env_tools` — never a promise of one that is not there),
    their git identity and sign-ins, and that secret variables are removed.
    Lives beside :func:`build_env` for the reason :func:`scrubbed_env_description`
    lives beside :func:`scrubbed_env`: the words and the truth must not drift."""
    names = [t for t in tools if t != "posix"]
    found = (
        f"Found on it: {', '.join(names)}." if names
        else "None of git, node, npm or python was found on it."
    )
    who = (
        " Their git identity, settings and sign-ins apply (a commit is theirs)."
        if "git" in names else ""
    )
    if (os_name or os.name) == "nt":
        posix = (
            "" if "posix" in tools else
            " No POSIX tools (mv/ls/cp/rm/cat/grep do not resolve)."
        )
        return (
            "Windows cmd.exe with the user's own PATH and home folder, as in their own "
            f"terminal. {found}{who} Secret variables (keys, tokens, passwords) are removed."
            f"{posix} Prefer the built-in tools (rename_file, list_files, grep, "
            "read_document/write_document) for file work."
        )
    return (
        "POSIX sh with the user's own PATH and home folder, as in their own terminal. "
        f"{found}{who} Secret variables (keys, tokens, passwords) are removed. Prefer the "
        "built-in tools (rename_file, list_files, grep, read_document/write_document) for "
        "file work."
    )


def host_os_line(system: str | None = None) -> str:
    """One model-facing line naming the OS this install runs on (v1.228.0,
    audit T6). ``platform.system()`` by default; on Windows it also says what
    a command will and will not resolve, because a tool authored around
    POSIX ``mv`` on the dev box (Git's ``mv.EXE`` is on ITS PATH) died 22/22
    on the packaged install. Lives beside :func:`scrubbed_env_description`
    for the same reason it does: the words and the truth must not drift."""
    import platform as _platform

    name = system or _platform.system() or os.name
    if name.lower().startswith("win"):
        return (
            "Windows (cmd.exe; no POSIX mv/ls/cp/rm/cat/grep — use the "
            "built-in file tools or Windows commands)"
        )
    return f"{name} (POSIX shell)"


def scrubbed_env_description(os_name: str | None = None) -> str:
    """Model-facing truth about what :func:`scrubbed_env` leaves resolvable
    (v1.205.0).

    A live task burned 14 failed shell calls guessing ``mv``/``python``/
    ``powershell`` because nothing told the model what the scrubbed native
    environment actually contains. This one-liner is baked into the shell
    tool's registered description; it lives NEXT TO ``scrubbed_env`` so the
    description and the scrub cannot drift apart silently. Deliberately
    conservative (dev-mode ``python`` sometimes resolves via the interpreter
    dir; a packaged install's does not) — never overpromise.
    """
    if (os_name or os.name) == "nt":
        return (
            "Windows cmd.exe with a minimal scrubbed PATH — no python, no "
            "powershell, no POSIX tools (mv/ls/cp/rm/cat/grep do not "
            "resolve); py.exe is available. Prefer the built-in tools "
            "(rename_file, list_files, grep, read_document/write_document) "
            "for file work."
        )
    return (
        "POSIX sh with a minimal scrubbed PATH (/usr/bin:/bin) — system "
        "utilities resolve, but user-installed tools, shell profiles, and "
        "virtualenvs do not. Prefer the built-in tools (rename_file, "
        "list_files, grep, read_document/write_document) for file work."
    )


def _kill_tree(proc: "subprocess.Popen[bytes]") -> None:
    """Kill ``proc`` AND every descendant (v1.228.0, RT3; v1.333.0: the one
    implementation is :func:`iron_jarvis.core.procs.kill_tree`).

    ``subprocess.run(shell=True, timeout=...)`` killed only the shell
    (cmd.exe / sh): the command it had started kept running to completion,
    held the pipes open, and ``communicate()`` blocked until it exited on its
    own -- measured 6 s for a 1 s timeout, marker file written after the
    "timeout". Kept under this name because other modules import it (a child
    started outside ``procs`` gets the plain ``taskkill /T /F`` / ``killpg``).
    """
    procs.kill_tree(proc)


class NativeSandbox(Sandbox):
    """Run commands on the host through ``core.procs`` (§16, best-effort)."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()

    def available(self) -> bool:
        """Native execution is always available."""
        return True

    def run(
        self, command: str, *, cwd: Path, timeout: float | None = None,
        on_spawn: Any = None, cancelled: Any = None, build: bool = False,
    ) -> SandboxResult:
        """``on_spawn`` / ``cancelled`` (v1.372.0): ``procs.run``'s seams,
        passed by the shell tool so a STOPPED call kills its command's tree
        instead of letting it run to its timeout. Omitted: unchanged.
        ``build`` (v1.377.0): the call belongs to a BUILD SESSION
        (``harness/command_env`` decided it, keyed on the session) — under
        ``modify_env = "deny"`` it runs with :func:`build_env` (the user's
        PATH and home, secrets removed) instead of :func:`scrubbed_env`."""
        limit = timeout if timeout is not None else self.policy.timeout_s
        # NOT child_env (review): a shell line pipes and redirects, and
        # PYTHONIOENCODING=utf-8 would make `type x.csv | python ...` fail on
        # an ANSI byte and `python export.py > out.csv` write UTF-8 Excel
        # misreads. A Python child keeps its own page; `_as_text` judges it.
        if self.policy.modify_env == "deny":
            env = build_env() if build else scrubbed_env()
        else:
            env = None
        start = time.monotonic()
        # `procs.run` (v1.333.0; Popen + communicate(timeout) since v1.228.0,
        # RT3): on a timeout the WHOLE tree is killed and the pipes are then
        # drained for at most 15 s, so the tool returns at ~timeout and the
        # command is genuinely gone -- not merely reported as timed out while
        # it keeps running. BYTES, decoded by `_as_text` (v1.288.0,
        # agents-01): `text=True` let one undecodable byte empty the output.
        # stdin=DEVNULL (agents-09): the packaged daemon's own stdin is a pipe
        # Electron never writes, so an inherited stdin parked every prompting
        # command (`set /p`, `pause`, `del *.*`'s Y/N) for the full timeout.
        # keep_windows: a `start notepad` the user asked for is a window they
        # now have and is never stopped. On a timeout the members that show
        # nothing are stopped at once; a command that FINISHED keeps what it
        # left running (a dev server started on purpose) until the session
        # that ran it ends (`procs.owned_by` in the shell tool,
        # `procs.end_session`). no_window=False: the shell keeps the console
        # it always had.
        res = procs.run(
            command,
            shell=True,
            cwd=cwd,
            env=env,
            timeout=limit,
            no_window=False,
            keep_windows=True,
            **({"on_spawn": on_spawn} if on_spawn is not None else {}),
            **({"cancelled": cancelled} if cancelled is not None else {}),
        )
        if res.timed_out:
            return SandboxResult(
                stdout=_as_text(res.stdout),
                stderr=_as_text(res.stderr) or f"timed out after {limit}s",
                returncode=-1,
                timed_out=True,
                duration_s=time.monotonic() - start,
            )
        return SandboxResult(
            stdout=_as_text(res.stdout),
            stderr=_as_text(res.stderr),
            returncode=res.returncode,
            timed_out=False,
            duration_s=time.monotonic() - start,
        )
