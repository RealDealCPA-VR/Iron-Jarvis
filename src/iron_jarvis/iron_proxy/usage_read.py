"""Read ONE Iron-Proxy account's usage left NOW (v1.439.0).

Before this, nothing in the app ever asked a vendor how much of an account's
plan was used: the windows Iron-Proxy holds arrive only with FINISHED calls
(Claude's ``anthropic-ratelimit-unified-*`` headers through the relay, Codex's
rate-limit snapshot), so an account nobody used for a day showed a day-old
number — or none. Two on-demand sources were MEASURED (2026-11-06, scratch
homes, no model call):

* Claude Code 2.1.316: ``claude -p --input-format stream-json --output-format
  stream-json --verbose``, an ``initialize`` control request, then
  ``{"subtype": "get_usage", "skip_behaviors": true}`` — answered in 0.15 s
  with ``rate_limits {five_hour, seven_day {utilization 0..100, resets_at
  ISO}}``. Claude Code's own schema marks it EXPERIMENTAL ("the response shape
  may change"), so the answer is read DEFENSIVELY: an unknown shape reads as
  "not known", never as a number.
* Codex 0.157.0: ``codex app-server``, ``initialize`` + ``initialized``, then
  ``account/rateLimits/read`` (params null) — ``{rateLimits {primary,
  secondary {usedPercent, windowDurationMins, resetsAt unix}, planType}}``,
  exactly Iron-Proxy's ``rateLimits`` input.

What it read is TOLD to Iron-Proxy through ``/iron/profiles/{id}/signal`` with
``status: 200`` — Iron-Proxy's ``detectFromHttp`` finds no failure in a 200,
so nothing parks and no request is counted, and ``recordLimits`` stores the
windows (the vendored bundle's ``reportSignal``). Only when Iron-Proxy
advertises ``plan-usage`` (an older one ignores the fields). Then the account
snapshot is refreshed, so every reader — the menus, the router's floors, the
Usage tab, the Connections card — sees the new number.

THE RULES:

* BLOCKING: every caller is off the event loop (the route is a sync ``def``,
  the in-use loop hops through ``asyncio.to_thread``).
* The account's provider and CLI home come from the CACHED snapshot, applied
  with ``accounts.make_lease`` — the ONE place a lease's environment is
  decided (an adopted default home sets NO home variable). Claude's child gets
  ``claude_models.child_env`` first (no API key, no proxy pins, no parent
  session markers), plus ``--setting-sources ""`` and ``--strict-mcp-config``
  so the user's hooks and MCP servers do not start for a usage question.
* A private EMPTY working folder; the child through ``core.procs.spawn`` (its
  own kill-on-close Job), bounded by :data:`READ_TIMEOUT_S`, the TREE killed
  at the end whatever happened.
* Never for an account that is turned off, needs to sign in, or is not a
  Claude Code / Codex account (Grok and the rest: not supported, said in
  words).
* At most one read per account per :data:`THROTTLE_S`: a second press inside
  it returns the last answer (``throttled: true``); two presses at once share
  ONE read (a lock per account).
* The last read per account is kept IN MEMORY only: ``{at, ok, words}``.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

#: One read's whole bound (spawn, handshake, answer).
READ_TIMEOUT_S = 15.0
#: After its answer, the CLI gets this long to exit BY ITSELF once its stdin
#: closes (review: Claude Code killed while it saves ``.claude.json`` can cut
#: the user's own file short — an adopted account's home IS ``~/.claude``).
#: Only one that lingers past it is killed.
EXIT_GRACE_S = 3.0
#: At most one read per account this often; a second press returns the last.
THROTTLE_S = 20.0
#: The in-use loop: one pass this often ...
POLL_S = 60.0
#: ... re-reading an account that is IN USE when its last read is this old.
IN_USE_EVERY_S = 180.0

#: Vendors whose CLI answers a usage question without a model call.
SUPPORTED = ("claude", "codex")

NOT_SUPPORTED = "Checking usage now works for Claude Code and Codex accounts only."
OFF_WORDS = "This account is turned off; turn it on first."
SIGN_IN_WORDS = "This account needs to sign in again before its usage can be read."
PROXY_OFF = "Iron-Proxy is off; turn it on in Connections first."
PROXY_UPDATE = "Iron-Proxy needs an update to keep usage numbers."
PROXY_NOT_ANSWERING = "Iron-Proxy is not answering, so the usage could not be kept."
UNKNOWN_ACCOUNT = "Iron-Proxy does not list that account."
NO_HOME_WORDS = "Iron-Proxy does not say where this account's sign-in is kept, so its usage cannot be read."

_LABEL = {"claude": "Claude Code", "codex": "Codex"}
_BINARY = {"claude": "claude", "codex": "codex"}


class Refused(Exception):
    """A read that is not made at all. ``code``: ``unknown`` (404) |
    ``not_supported`` | ``off`` | ``sign_in`` | ``proxy_off`` | ``no_home``
    (409)."""

    def __init__(self, code: str, words: str) -> None:
        super().__init__(words)
        self.code = code
        self.words = words


# --------------------------------------------------------------------------- #
# the memory of reads
# --------------------------------------------------------------------------- #
_READS: dict[str, dict[str, Any]] = {}
_READ_MONO: dict[str, float] = {}
#: Review (v1.439.0): when each account's usage was last read SUCCESSFULLY.
#: "as of" speaks for the number shown, and a failed read read no number.
_OK_AT: dict[str, str] = {}
_LOCKS: dict[str, threading.Lock] = {}
_STATE_LOCK = threading.Lock()


def reset() -> None:
    """TESTS: forget every read."""
    with _STATE_LOCK:
        _READS.clear()
        _READ_MONO.clear()
        _OK_AT.clear()
        _LOCKS.clear()


def _lock_for(account_id: str) -> threading.Lock:
    with _STATE_LOCK:
        lock = _LOCKS.get(account_id)
        if lock is None:
            lock = _LOCKS[account_id] = threading.Lock()
        return lock


def last_read(account_id: str) -> dict[str, Any] | None:
    """The last read of ``account_id`` (``{at, ok, words}``), or None."""
    with _STATE_LOCK:
        got = _READS.get(str(account_id or ""))
        return dict(got) if got else None


def last_ok_at(account_id: str) -> str | None:
    """When ``account_id``'s usage was last read SUCCESSFULLY (ISO), or None
    — the time the "as of" words speak for (a failed read never moves it)."""
    with _STATE_LOCK:
        return _OK_AT.get(str(account_id or ""))


def _age_s(account_id: str) -> float | None:
    with _STATE_LOCK:
        at = _READ_MONO.get(account_id)
    return None if at is None else time.monotonic() - at


def _remember(account_id: str, ok: bool, words: str) -> dict[str, Any]:
    read = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ok": bool(ok),
        "words": str(words or ""),
    }
    with _STATE_LOCK:
        _READS[account_id] = read
        _READ_MONO[account_id] = time.monotonic()
        if read["ok"]:
            _OK_AT[account_id] = read["at"]
    return dict(read)


# --------------------------------------------------------------------------- #
# which accounts can be read
# --------------------------------------------------------------------------- #
def can_refresh(row: Mapping[str, Any] | None) -> bool:
    """Can ``row`` (a ``build_accounts`` row) have its usage read now? A
    Claude Code or Codex account that is on and signed in (a limit is fine:
    that is when a fresh number matters most)."""
    from ..harness import build_accounts as BA

    if not isinstance(row, Mapping) or row.get("vendor") not in SUPPORTED:
        return False
    return row.get("status") in (BA.READY, BA.USAGE, BA.LIMIT)


def decorate(row: dict[str, Any]) -> dict[str, Any]:
    """``row`` with ``usage_read_at`` (the last SUCCESSFUL read's time, or
    None) and ``can_refresh`` added. Mutates and returns it; never raises."""
    try:
        row["usage_read_at"] = last_ok_at(str(row.get("id") or ""))
        row["can_refresh"] = can_refresh(row)
    except Exception:  # noqa: BLE001 — a list never breaks over a decoration
        row.setdefault("usage_read_at", None)
        row.setdefault("can_refresh", False)
    return row


# --------------------------------------------------------------------------- #
# the child (seams for tests)
# --------------------------------------------------------------------------- #
def _cli_argv(vendor: str) -> list[str] | None:
    """The CLI to start (``[path]``), resolved like the adapters resolve it
    (PATH + the per-user bin folders a GUI-started app misses). TESTS replace
    this with ``[python, fake.py]``."""
    from ..providers.adapters.subprocess_cli import _which_cli

    exe = _which_cli(_BINARY[vendor])
    return [exe] if exe else None


def _argv(vendor: str, base: list[str]) -> list[str]:
    if vendor == "claude":
        return [
            *base, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            # The user's hooks and MCP servers do not start for a usage question.
            "--setting-sources", "",
            "--strict-mcp-config",
        ]
    return [*base, "app-server", "--listen", "stdio://"]


def _env(vendor: str, lease: Any) -> dict[str, str]:
    if vendor == "claude":
        from ..providers.claude_models import child_env

        env = child_env()
    else:
        env = dict(os.environ)
        from ..core.claude_markers import drop_claude_session_markers

        drop_claude_session_markers(env)
    return lease.apply(env)


def _exchange(
    argv: list[str],
    env: dict[str, str],
    frames: list[dict[str, Any]],
    is_answer: Any,
    timeout: float,
) -> dict[str, Any]:
    """Start ``argv`` in a private empty folder, write ``frames`` (one JSON
    line each), and return the first stdout object ``is_answer`` accepts.
    After an answer, stdin closes and the CLI gets :data:`EXIT_GRACE_S` to
    exit by itself; the tree is killed at the end, whatever happened. Raises
    ``TimeoutError`` (no answer in time), ``EOFError`` (it stopped first) or
    ``OSError`` (it could not start)."""
    from ..core import procs

    deadline = time.monotonic() + timeout
    lines: queue.Queue = queue.Queue()
    answered = False
    with tempfile.TemporaryDirectory(prefix="ij-usage-read-", ignore_cleanup_errors=True) as cwd:
        proc = procs.spawn(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            env=env,
        )
        try:
            def read() -> None:
                try:
                    for raw in proc.stdout:  # type: ignore[union-attr]
                        lines.put(raw)
                except Exception:  # noqa: BLE001 — a dead pipe ends the stream
                    pass
                finally:
                    lines.put(None)

            threading.Thread(target=read, daemon=True, name="ij-usage-read").start()
            try:
                for frame in frames:
                    proc.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
            except OSError:
                pass  # it stopped already: the reader says so
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError
                try:
                    raw = lines.get(timeout=left)
                except queue.Empty:
                    raise TimeoutError from None
                if raw is None:
                    raise EOFError
                text = raw.decode("utf-8", errors="replace").strip()
                if not text.startswith("{"):
                    continue
                try:
                    obj = json.loads(text)
                except ValueError:
                    continue
                if isinstance(obj, dict) and is_answer(obj):
                    answered = True
                    return obj
        finally:
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            if answered:
                try:
                    proc.wait(timeout=EXIT_GRACE_S)
                except Exception:  # noqa: BLE001 — it lingers: the kill below ends it
                    pass
            procs.kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 — the tree is killed; nothing to wait for
                pass
            procs.release(proc)


# --------------------------------------------------------------------------- #
# reading each vendor's answer (defensive: an unknown shape is "not known")
# --------------------------------------------------------------------------- #
_CLAUDE_WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))


def _unix_seconds(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if n > 1e12:  # milliseconds
            n /= 1000.0
        return int(n) if 1e9 <= n < 1e11 else None
    if isinstance(value, str) and value.strip():
        try:
            when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return int(when.timestamp())
    return None


def claude_headers(answer: Any) -> dict[str, str] | None:
    """Claude Code's ``get_usage`` answer as the ``anthropic-ratelimit-
    unified-<5h|7d>-utilization`` (0..1) / ``-reset`` (unix seconds) headers
    Iron-Proxy already reads from a finished call; None when the answer holds
    no window it can read."""
    if not isinstance(answer, Mapping):
        return None
    limits = answer.get("rate_limits")
    if not isinstance(limits, Mapping):
        return None
    out: dict[str, str] = {}
    for key, kind in _CLAUDE_WINDOWS:
        win = limits.get(key)
        if not isinstance(win, Mapping):
            continue
        used = win.get("utilization")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            continue
        if not 0 <= float(used) <= 150:
            continue
        prefix = f"anthropic-ratelimit-unified-{kind}"
        out[f"{prefix}-utilization"] = f"{float(used) / 100.0:.4f}"
        reset = _unix_seconds(win.get("resets_at"))
        if reset is not None:
            out[f"{prefix}-reset"] = str(reset)
    return out or None


def codex_rate_limits(result: Any) -> dict[str, Any] | None:
    """Codex's ``account/rateLimits/read`` result as Iron-Proxy's
    ``rateLimits`` input (``accounts.rate_limits_body``); None when it holds
    no window."""
    from .accounts import rate_limits_body

    if not isinstance(result, Mapping):
        return None
    snap = result.get("rateLimits")
    if not isinstance(snap, Mapping):
        return None
    snap = dict(snap)
    if "planType" not in snap and isinstance(result.get("planType"), str):
        snap["planType"] = result["planType"]
    snap.setdefault("observedAt", int(time.time()))
    body = rate_limits_body(snap)
    if not body or not any(k in body for k in ("primary", "secondary")):
        return None
    return body


def _mask(text: Any) -> str:
    try:
        from ..detections.redact import mask

        said = str(mask(str(text or "")))
    except Exception:  # noqa: BLE001
        said = ""
    return " ".join(said.split())[:200]


def _ask_claude(argv: list[str], env: dict[str, str]) -> dict[str, str]:
    frames = [
        {"type": "control_request", "request_id": "ij-usage-init", "request": {"subtype": "initialize"}},
        {"type": "control_request", "request_id": "ij-usage",
         "request": {"subtype": "get_usage", "skip_behaviors": True}},
    ]

    def answer(obj: dict[str, Any]) -> bool:
        resp = obj.get("response")
        return obj.get("type") == "control_response" and isinstance(resp, Mapping) \
            and resp.get("request_id") == "ij-usage"

    obj = _exchange(argv, env, frames, answer, READ_TIMEOUT_S)
    resp = obj.get("response") or {}
    if resp.get("subtype") == "error":
        said = _mask(resp.get("error"))
        raise _Said(f"Claude Code could not say{': ' + said if said else ''}.")
    headers = claude_headers(resp.get("response"))
    if headers is None:
        raise _Said("Claude Code's answer had no usage numbers.")
    return headers


def _ask_codex(argv: list[str], env: dict[str, str]) -> dict[str, Any]:
    from .. import __version__

    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "iron-jarvis", "title": "Iron Jarvis", "version": __version__},
                    "capabilities": {"experimentalApi": True}}},
        {"jsonrpc": "2.0", "method": "initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": None},
    ]

    def answer(obj: dict[str, Any]) -> bool:
        return obj.get("id") == 2 and ("result" in obj or "error" in obj)

    obj = _exchange(argv, env, frames, answer, READ_TIMEOUT_S)
    if "error" in obj:
        err = obj.get("error")
        said = _mask(err.get("message") if isinstance(err, Mapping) else err)
        raise _Said(f"Codex could not say{': ' + said if said else ''}.")
    body = codex_rate_limits(obj.get("result"))
    if body is None:
        raise _Said("Codex's answer had no usage numbers.")
    return body


class _Said(Exception):
    """A read that ran and failed, in plain words."""


# --------------------------------------------------------------------------- #
# one read
# --------------------------------------------------------------------------- #
def _row_and_lease(svc: Any, account_id: str) -> tuple[dict[str, Any], Any]:
    from ..harness import build_accounts as BA
    from . import accounts as A

    if svc is None or not getattr(svc, "enabled", False):
        raise Refused("proxy_off", PROXY_OFF)
    snap = BA.snapshot(svc)
    if snap is None:
        raise Refused("proxy_off", BA.NOT_READ_NOTE)
    row = BA.find(svc, account_id)
    if row is None:
        # Grok and other Iron-Proxy accounts are listed, but not readable.
        for p in snap[0]:
            if str(p.get("id") or "") == account_id:
                raise Refused("not_supported", NOT_SUPPORTED)
        raise Refused("unknown", UNKNOWN_ACCOUNT)
    vendor = row.get("vendor")
    if vendor not in SUPPORTED:
        raise Refused("not_supported", NOT_SUPPORTED)
    if row.get("status") == BA.OFF:
        raise Refused("off", OFF_WORDS)
    if row.get("status") == BA.NEEDS_SIGN_IN:
        raise Refused("sign_in", SIGN_IN_WORDS)
    home = BA.home_of(snap, account_id)
    if not home:
        # Review (v1.439.0): every other lease caller refuses an account with
        # no home ("an answer with no account home"). Its home variable set
        # to "" would run the CLI on whatever login this PC has and store
        # THAT usage under this account.
        raise Refused("no_home", NO_HOME_WORDS)
    spec = A.PROVIDERS[BA.VENDORS[vendor]["provider"]]
    lease = A.make_lease(spec, account_id, str(row.get("name") or account_id), home, [])
    return row, lease


def _tell(svc: Any, account_id: str, vendor: str, got: Any) -> None:
    from .client import IronProxyError
    from .service import PLAN_USAGE_FEATURE

    if not svc.has_feature(PLAN_USAGE_FEATURE):
        raise _Said(PROXY_UPDATE)
    client = svc.client()
    if client is None:
        raise _Said(PROXY_NOT_ANSWERING)
    try:
        if vendor == "claude":
            client.signal(account_id, status=200, headers=got)
        else:
            client.signal(account_id, status=200, rate_limits=got)
    except IronProxyError as exc:
        raise _Said(f"Iron-Proxy did not keep it: {_mask(exc.sentence)}") from None


def read_usage(svc: Any, account_id: str, *, force: bool = False) -> dict[str, Any]:
    """Read ``account_id``'s usage now, tell Iron-Proxy, refresh the snapshot.
    BLOCKING. Answers ``{at, ok, words, throttled}``; raises :class:`Refused`
    when no read is made at all. ``force`` skips the throttle (never used by
    a press or the loop; tests)."""
    account_id = str(account_id or "")
    row, lease = _row_and_lease(svc, account_id)
    vendor = str(row["vendor"])
    with _lock_for(account_id):
        age = _age_s(account_id)
        if not force and age is not None and age < THROTTLE_S:
            got = last_read(account_id) or {}
            return {**got, "throttled": True}
        label = _LABEL[vendor]
        try:
            base = _cli_argv(vendor)
            if not base:
                raise _Said(f"{label} is not installed on this PC.")
            env = _env(vendor, lease)
            got = (_ask_claude if vendor == "claude" else _ask_codex)(_argv(vendor, base), env)
            _tell(svc, account_id, vendor, got)
        except _Said as exc:
            return {**_remember(account_id, False, str(exc)), "throttled": False}
        except TimeoutError:
            words = f"{label} did not answer in {int(READ_TIMEOUT_S)} seconds."
            return {**_remember(account_id, False, words), "throttled": False}
        except EOFError:
            return {**_remember(account_id, False, f"{label} stopped before it answered."), "throttled": False}
        except OSError:
            return {**_remember(account_id, False, f"{label} could not be started."), "throttled": False}
        except Exception:  # noqa: BLE001 — a read never breaks its caller
            log.warning("reading an account's usage failed", exc_info=True)
            return {**_remember(account_id, False, "The usage could not be read."), "throttled": False}
        try:
            svc.refresh_accounts()
        except Exception:  # noqa: BLE001 — the number is kept; the next read shows it
            log.debug("refreshing the account snapshot after a usage read failed", exc_info=True)
        return {**_remember(account_id, True, "Usage checked."), "throttled": False}


# --------------------------------------------------------------------------- #
# accounts IN USE, re-read while they work
# --------------------------------------------------------------------------- #
def in_use_accounts(platform: Any) -> list[str]:
    """Accounts a reply is RUNNING on right now: Build › Code live sessions
    whose turn runs, and Chat › Code turns (the durable turn registry's
    ``build`` surface) joined to their session's ``setup.account``. BLOCKING
    (one indexed read per running Chat › Code turn); never raises."""
    found: list[str] = []

    def add(account: Any) -> None:
        a = str(account or "").strip()
        if a and a not in found:
            found.append(a)

    try:
        hosted = getattr(platform, "hosted", None)
        for live in list(getattr(hosted, "live", {}).values()) if hosted is not None else []:
            turn = getattr(live, "turn", None)
            if turn is not None and getattr(turn, "running", False):
                add(getattr(live, "account", ""))
    except Exception:  # noqa: BLE001
        log.debug("could not list Build › Code accounts in use", exc_info=True)
    try:
        from ..daemon.durable_turns import running_replies
        from ..harness import sessions as S

        engine = getattr(platform, "engine", None)
        for row in running_replies(getattr(platform, "turn_logs", None)):
            if row.get("surface") != "build" or engine is None:
                continue
            tid = str(row.get("thread_id") or "")
            if tid.startswith("build:"):
                tid = tid[len("build:"):]
            rec = S.get_row(engine, tid) if tid else None
            if rec is not None:
                add(S._setup(rec).get("account"))
    except Exception:  # noqa: BLE001
        log.debug("could not list Chat › Code accounts in use", exc_info=True)
    return found


def poll_in_use(platform: Any) -> list[str]:
    """One pass of the in-use loop: re-read each account IN USE whose last
    read is older than :data:`IN_USE_EVERY_S`. Nothing in use = no read at
    all. BLOCKING; never raises. Returns the ids read."""
    svc = getattr(platform, "iron_proxy", None)
    try:
        if svc is None or not svc.enabled or not svc.running():
            return []
    except Exception:  # noqa: BLE001
        return []
    read: list[str] = []
    for account_id in in_use_accounts(platform):
        age = _age_s(account_id)
        if age is not None and age < IN_USE_EVERY_S:
            continue
        try:
            read_usage(svc, account_id)
            read.append(account_id)
        except Refused:
            continue
        except Exception:  # noqa: BLE001 — one account never stops the pass
            log.debug("an in-use usage read failed", exc_info=True)
    return read


async def usage_loop(platform: Any, on_tick: Any, *, interval: float | None = None) -> None:
    """The lifespan loop: every :data:`POLL_S`, :func:`poll_in_use` off the
    event loop; each pass reports ITS OWN outcome (``loop_health``)."""
    import asyncio

    while True:
        try:
            await asyncio.to_thread(poll_in_use, platform)
            on_tick(True, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed pass is a row, not a crash
            on_tick(False, exc)
        await asyncio.sleep(POLL_S if interval is None else interval)


__all__ = [
    "EXIT_GRACE_S",
    "IN_USE_EVERY_S",
    "POLL_S",
    "READ_TIMEOUT_S",
    "Refused",
    "SUPPORTED",
    "THROTTLE_S",
    "can_refresh",
    "claude_headers",
    "codex_rate_limits",
    "decorate",
    "in_use_accounts",
    "last_ok_at",
    "last_read",
    "poll_in_use",
    "read_usage",
    "reset",
    "usage_loop",
]
