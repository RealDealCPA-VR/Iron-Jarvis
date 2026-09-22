"""Native subprocess sandbox (§16).

Best-effort isolation: enforces ``timeout`` and, when ``modify_env == 'deny'``
(§17), a scrubbed minimal environment. Hard network/CPU/memory isolation
requires the Docker runtime — see :mod:`docker_runtime`.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

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
    """Kill ``proc`` AND every descendant (v1.228.0, RT3).

    ``subprocess.run(shell=True, timeout=...)`` killed only the shell
    (cmd.exe / sh): the command it had started kept running to completion,
    held the pipes open, and ``communicate()`` blocked until it exited on its
    own — measured 6 s for a 1 s timeout, marker file written after the
    "timeout". Windows: ``taskkill /T /F`` walks the process tree. POSIX: the
    child was started in its own session (``start_new_session=True``), so the
    whole process group is one ``killpg`` away. Best-effort — a kill that
    fails falls back to ``proc.kill()`` so the drain below can never hang on a
    dead handle.
    """
    try:
        if os.name == "nt":
            windir = os.environ.get("SystemRoot", r"C:\Windows")
            taskkill = os.path.join(windir, "System32", "taskkill.exe")
            if not os.path.exists(taskkill):
                taskkill = "taskkill"
            subprocess.run(
                [taskkill, "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 — fall through to the plain kill
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 — already gone
        pass


class NativeSandbox(Sandbox):
    """Run commands via ``subprocess.Popen`` on the host (§16, best-effort)."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()

    def available(self) -> bool:
        """Native execution is always available."""
        return True

    def run(
        self, command: str, *, cwd: Path, timeout: float | None = None
    ) -> SandboxResult:
        limit = timeout if timeout is not None else self.policy.timeout_s
        # NOT child_env (review): a shell line pipes and redirects, and
        # PYTHONIOENCODING=utf-8 would make `type x.csv | python ...` fail on
        # an ANSI byte and `python export.py > out.csv` write UTF-8 Excel
        # misreads. A Python child keeps its own page; `_as_text` judges it.
        env = scrubbed_env() if self.policy.modify_env == "deny" else None
        start = time.monotonic()
        # Popen + communicate(timeout) instead of subprocess.run (v1.228.0,
        # RT3): on a timeout the WHOLE tree is killed (see `_kill_tree`) and
        # the pipes are then drained, so the tool returns at ~timeout and the
        # command is genuinely gone — not merely reported as timed out while
        # it keeps running.
        popen_kw: dict = {}
        if os.name != "nt":
            popen_kw["start_new_session"] = True
        # BYTES, decoded by `_as_text` (v1.288.0, agents-01): `text=True` let
        # one undecodable byte empty the output. stdin=DEVNULL (agents-09):
        # the packaged daemon's own stdin is a pipe Electron never writes, so
        # an inherited stdin parked every prompting command (`set /p`,
        # `pause`, `del *.*`'s Y/N) for the full timeout; now it reads EOF at
        # once and its prompt text reaches the model.
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **popen_kw,
        )
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=15)
            except Exception:  # noqa: BLE001 — a wedged drain still returns
                out, err = b"", b""
            return SandboxResult(
                stdout=_as_text(out),
                stderr=_as_text(err) or f"timed out after {limit}s",
                returncode=-1,
                timed_out=True,
                duration_s=time.monotonic() - start,
            )
        return SandboxResult(
            stdout=_as_text(out),
            stderr=_as_text(err),
            returncode=proc.returncode,
            timed_out=False,
            duration_s=time.monotonic() - start,
        )
