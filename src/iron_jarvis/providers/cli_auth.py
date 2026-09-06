"""Sign-in truth for the subscription CLIs (v1.234.0).

THE LIVE REPORT. A user's chat turn failed with::

    claude-cli: CLI exited 1: {"type":"result","subtype":"success","is_error":
    true,...,"result":"Not logged in · Please run /login",...

Two defects stacked. (1) Newer Claude Code builds exit 1 when signed out AND
print the result JSON to stdout; the adapter honoured the exit code before it
parsed anything, so the user saw 400 characters of JSON instead of the one
sentence inside it. (2) ``available("claude-cli")`` meant "the binary is on
disk" — so Connections, the switcher and /health all read connected, and a
keyless ``anthropic`` inherited a CLI that could not answer. The user found
out on their first message.

This module is the ONE place that knows what "signed in" means for a CLI:

* :func:`cli_failure_message` turns a failed CLI run (exit code, stdout,
  stderr) into the sentence the user can act on — the result JSON is parsed
  FIRST, and a sign-in refusal becomes the exact remedy for that CLI.
* :class:`CliAuthProbe` asks the CLI itself (``claude auth status``,
  ``codex login status``), caches the verdict, refreshes it OFF the event
  loop, and lets an adapter that just hit a sign-in refusal mark the CLI
  signed out at once (so the next availability check is honest without
  waiting for the cache to expire).

Inconclusive is not signed-out: an older CLI without the status subcommand,
a timeout, or an unparseable answer yields ``None`` and the provider stays
available — the run then decides, exactly like the router's pre-probe. Only
a CLI that SAID it is signed out turns the row amber.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

#: Provider id -> the CLI binary it runs through.
CLI_BINARIES: dict[str, str] = {"claude-cli": "claude", "codex-cli": "codex"}

#: The remedy, per CLI, in the words the user needs (the Handbook and the
#: Connections row say the same thing — one source).
SIGN_IN_FIX: dict[str, str] = {
    "claude": (
        "Claude Code isn't signed in. Open a terminal, run `claude`, then "
        "`/login`, then click Test on Connections."
    ),
    "codex": (
        "Codex isn't signed in. Open a terminal, run `codex login`, then "
        "click Test on Connections."
    ),
}

#: Phrases a CLI uses to say "you are not signed in". Matched case-insensitively
#: against the CLI's own words. Deliberately narrow: a token count or an id
#: never contains these, and an API error ("overloaded") must NOT be
#: relabelled as a sign-in problem.
_SIGN_IN_PATTERNS = (
    r"not logged in",
    r"please run /login",
    r"login required",
    r"not signed in",
    r"run `?codex login`?",
    r"please (?:log|sign) ?in",
    r"authentication required",
)
_SIGN_IN_RE = re.compile("|".join(_SIGN_IN_PATTERNS), re.IGNORECASE)

#: Wall clock for one status probe. The CLIs are Node/Rust processes that
#: answer in ~1 s; a wedged one must not hold a thread for long.
PROBE_TIMEOUT_S = 12.0


def is_sign_in_refusal(text: str) -> bool:
    """True when *text* (a CLI's own words) says the user is not signed in."""
    return bool(text) and _SIGN_IN_RE.search(text) is not None


def _result_text(stdout: str) -> tuple[str | None, bool]:
    """``(result, is_error)`` from a ``--output-format json`` payload, or
    ``(None, False)`` when stdout is not that shape."""
    raw = (stdout or "").strip()
    if not raw.startswith("{"):
        return None, False
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 — not JSON after all
        return None, False
    if not isinstance(data, dict):
        return None, False
    result = data.get("result")
    return (str(result).strip() if result is not None else ""), bool(data.get("is_error"))


def cli_failure_message(
    provider: str, binary: str, code: int, stdout: str, stderr: str
) -> str:
    """The sentence for a CLI run that failed (non-zero exit or ``is_error``).

    Order matters and is the fix for the live report: the result JSON is
    read FIRST, so a CLI that both exits 1 and prints ``{"is_error": true,
    "result": "Not logged in · Please run /login"}`` yields the sentence,
    not the dump. A recognised sign-in refusal becomes :data:`SIGN_IN_FIX`
    for that CLI, with the CLI's own words kept in parentheses so the
    original phrasing is never lost from a log.
    """
    result, is_error = _result_text(stdout)
    if result is not None and (is_error or code != 0):
        detail = result or "CLI error"
    else:
        detail = (stderr or stdout or "").strip()
    detail = detail[:400]
    if is_sign_in_refusal(detail):
        fix = SIGN_IN_FIX.get(binary, f"{binary} isn't signed in — sign in via its own CLI.")
        return f"{provider}: {fix} (the CLI said: {detail[:120]})"
    if result is not None and (is_error or code != 0):
        return f"{provider}: {detail}"
    return f"{provider}: CLI exited {code}: {detail}"


@dataclass
class CliAuthStatus:
    """One probe verdict. ``signed_in`` is ``None`` when inconclusive."""

    binary: str
    installed: bool
    signed_in: bool | None
    detail: str = ""
    checked_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "installed": self.installed,
            "signed_in": self.signed_in,
            "detail": self.detail,
        }


def _run_probe(argv: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603 — argv list, no shell
        argv,
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_S,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _which(binary: str) -> str | None:
    try:
        from ..terminals.ai_clis import _find

        return _find(binary)
    except Exception:  # noqa: BLE001 — degraded env keeps the plain probe
        import shutil

        return shutil.which(binary)


def parse_claude_status(code: int, stdout: str, stderr: str) -> tuple[bool | None, str]:
    """``claude auth status`` prints JSON with ``loggedIn``. Anything else
    (an older CLI printing usage, a crash) is inconclusive."""
    raw = (stdout or "").strip()
    start = raw.find("{")
    if start >= 0:
        try:
            data = json.loads(raw[start:])
            if isinstance(data, dict) and "loggedIn" in data:
                ok = bool(data.get("loggedIn"))
                method = str(data.get("authMethod") or "")
                return ok, (f"signed in via {method}" if ok and method else ("signed in" if ok else "not signed in"))
        except Exception:  # noqa: BLE001
            pass
    text = f"{stdout}\n{stderr}"
    if is_sign_in_refusal(text):
        return False, "not signed in"
    return None, "status unknown"


def parse_codex_status(code: int, stdout: str, stderr: str) -> tuple[bool | None, str]:
    """``codex login status`` prints ``Logged in using ChatGPT`` (exit 0) or
    ``Not logged in`` (non-zero)."""
    text = f"{stdout}\n{stderr}".strip()
    if re.search(r"\blogged in\b", text, re.IGNORECASE) and not is_sign_in_refusal(text):
        return True, text.splitlines()[0][:80] if text else "signed in"
    if is_sign_in_refusal(text):
        return False, "not signed in"
    return None, "status unknown"


#: binary -> (argv after the exe, parser)
_PROBES: dict[str, tuple[list[str], Callable[[int, str, str], tuple[bool | None, str]]]] = {
    "claude": (["auth", "status"], parse_claude_status),
    "codex": (["login", "status"], parse_codex_status),
}


class CliAuthProbe:
    """Cached, off-loop sign-in verdicts for the subscription CLIs.

    ``verdict(binary)`` NEVER blocks: it returns the cached answer (``None``
    before the first probe lands) and, when the cache is stale, starts ONE
    background refresh. ``refresh(binary)`` blocks — for the rescan route
    (threadpool) and the boot warm-up thread. ``mark_signed_out`` is the
    adapter's feedback: a sign-in refusal on a real call is better evidence
    than any cache, so it flips the verdict immediately.
    """

    def __init__(
        self,
        *,
        runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
        which: Callable[[str], str | None] | None = None,
        ttl_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner = runner or _run_probe
        self._which = which or _which
        self.ttl_s = ttl_s
        self._clock = clock
        self._cache: dict[str, CliAuthStatus] = {}
        self._lock = threading.Lock()
        self._inflight: set[str] = set()

    # --- blocking ---------------------------------------------------------
    def refresh(self, binary: str) -> CliAuthStatus:
        exe = self._which(binary)
        now = self._clock()
        if not exe:
            st = CliAuthStatus(binary, False, None, "not installed", now)
        else:
            probe = _PROBES.get(binary)
            if probe is None:
                st = CliAuthStatus(binary, True, None, "no status probe for this CLI", now)
            else:
                args, parse = probe
                try:
                    code, out, err = self._runner([exe] + args)
                    signed, detail = parse(code, out, err)
                except subprocess.TimeoutExpired:
                    signed, detail = None, "status probe timed out"
                except Exception as exc:  # noqa: BLE001 — a probe never raises into a caller
                    signed, detail = None, f"status probe failed: {type(exc).__name__}"
                st = CliAuthStatus(binary, True, signed, detail, self._clock())
        with self._lock:
            self._cache[binary] = st
            self._inflight.discard(binary)
        return st

    def refresh_all(self) -> dict[str, CliAuthStatus]:
        return {b: self.refresh(b) for b in _PROBES}

    # --- non-blocking -----------------------------------------------------
    def status(self, binary: str) -> CliAuthStatus | None:
        """The cached verdict (maybe stale), never a probe."""
        with self._lock:
            return self._cache.get(binary)

    def verdict(self, binary: str) -> bool | None:
        """``True``/``False``/``None`` (unknown), and a background refresh
        when nothing is cached or the cache is older than ``ttl_s``."""
        with self._lock:
            st = self._cache.get(binary)
            stale = st is None or (self._clock() - st.checked_at) > self.ttl_s
            should_start = stale and binary not in self._inflight
            if should_start:
                self._inflight.add(binary)
        if should_start:
            self._start(binary)
        return None if st is None else st.signed_in

    def warm(self, binaries: tuple[str, ...] = tuple(_PROBES)) -> None:
        """Kick off one refresh per CLI on a thread; returns at once."""
        for b in binaries:
            with self._lock:
                if b in self._inflight:
                    continue
                self._inflight.add(b)
            self._start(b)

    def mark_signed_out(self, binary: str, detail: str = "not signed in") -> None:
        with self._lock:
            self._cache[binary] = CliAuthStatus(binary, True, False, detail[:120], self._clock())

    def _start(self, binary: str) -> None:
        t = threading.Thread(target=self.refresh, args=(binary,), name=f"cli-auth-{binary}", daemon=True)
        try:
            t.start()
        except RuntimeError:
            with self._lock:
                self._inflight.discard(binary)


#: The process-wide probe. The manager and the adapters share it so a
#: refusal seen by an adapter is the verdict the manager reports next.
DEFAULT_PROBE = CliAuthProbe()


def note_cli_failure(binary: str, message: str) -> None:
    """Adapter feedback: a sign-in refusal on a real call marks the CLI
    signed out on the shared probe. Never raises."""
    try:
        if is_sign_in_refusal(message):
            DEFAULT_PROBE.mark_signed_out(binary, "not signed in (a request was refused)")
    except Exception:  # noqa: BLE001
        pass
