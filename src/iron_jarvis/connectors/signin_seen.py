"""Has the user finished an app's OWN sign-in on this PC? (v1.358.0, lane 8D)

Microsoft 365 is an app whose program loads all of its tools whether or not
anyone has signed in to Microsoft: the tools are listed, and every call then
fails ("No valid token found"). The Directory read that as a green
"Connected" with 24 tools before any sign-in, so a user who skipped Sign in
saw a working-looking app whose every call failed. This module is the ONE
cached answer to "did the sign-in land", read by the card
(``connectors.service``):

* the app's OWN check first — its ``verify-login`` tool. Softeria's server
  (``@softeria/ms-365-mcp-server`` 0.170.2, ``dist/auth-tools.js`` +
  ``dist/auth.js`` ``testLogin``, read on this PC) answers it with a SILENT
  token read and one Graph ``/me``: it never starts a sign-in and changes
  nothing, and its text is ``{"success": true|false, "message": …,
  "userData": {"displayName", "userPrincipalName"}}``;
* when that check cannot answer (the tool is not loaded, its answer is not
  that shape, it errors or runs past :data:`VERIFY_TIMEOUT_S`), a SUCCESSFUL
  call of one of the app's other tools since it loaded proves the sign-in
  instead (the tool ledger) — "the first successful call".

Until one of those says signed in, the answer is "not signed in" — the card
reads "Sign in to Microsoft to finish", never green on a guess.

:func:`seen` NEVER blocks (the ``providers/ollama_seen`` shape): it returns
the last verdict (``None`` before the first lands) and, when that is older
than its TTL, starts ONE background check for that app. The check runs on its
own thread with its own event loop and calls the app's client directly — not
through ``registry.invoke`` (nothing is written to the ledger) and not through
the tool's ``execute`` (a status read never marks the app "failing" or rings
the bell). :func:`check_now` blocks (≤ :data:`VERIFY_TIMEOUT_S`): it is for
the card's Test press, which runs on the threadpool, never on the loop.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

#: How long a "signed in" answer is served before it is checked again.
TTL_SIGNED_IN_S = 300.0
#: How long a "not signed in" answer is served — short, so a sign-in the user
#: just finished in a Build terminal turns the card within one or two polls.
TTL_NOT_SIGNED_IN_S = 15.0
#: The longest one check waits for the app's answer.
VERIFY_TIMEOUT_S = 20.0
#: The app's own sign-in check (Softeria's server).
VERIFY_TOOL = "verify-login"
#: The app's sign-in tools: a successful call of one of these proves nothing
#: about being signed in (``login`` answers with a code to type).
SIGNIN_TOOLS = frozenset(
    {"login", "logout", "verify-login", "list-accounts", "select-account", "remove-account"}
)


@dataclass(frozen=True)
class Verdict:
    """One answer: ``signed_in``, the account it names (``""`` when not
    said), and ``how`` it was learned: ``"check"`` (the app's own check),
    ``"call"`` (a successful call) or ``"none"`` (nothing could say — read
    as not signed in)."""

    signed_in: bool
    account: str = ""
    how: str = "none"


_lock = threading.Lock()
#: server -> (monotonic time, verdict, identity of the saved app it is about)
_answers: dict[str, tuple[float, Verdict, str]] = {}
#: servers with a check in flight -> when it started (monotonic)
_inflight: dict[str, float] = {}
#: servers whose own check call has not come back yet (it may outlive the
#: wait): never a second call into the same app while one is still out.
_calls_out: set[str] = set()


def _ttl(v: Verdict) -> float:
    return TTL_SIGNED_IN_S if v.signed_in else TTL_NOT_SIGNED_IN_S


def parse_check(result: Any) -> Verdict | None:
    """The app's ``verify-login`` answer (a raw MCP result) as a verdict, or
    ``None`` when it does not answer in the shape its server writes (then the
    ledger decides). Never raises; the account is the principal name, else the
    display name, never anything longer than an address."""
    try:
        if not isinstance(result, dict) or result.get("isError"):
            return None
        text = ""
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text += str(block.get("text") or "")
        body = json.loads(text) if text.strip() else None
        if not isinstance(body, dict) or not isinstance(body.get("success"), bool):
            return None
        if not body["success"]:
            return Verdict(False, "", "check")
        user = body.get("userData") if isinstance(body.get("userData"), dict) else {}
        account = str(user.get("userPrincipalName") or user.get("displayName") or "").strip()
        return Verdict(True, account[:120], "check")
    except Exception:  # noqa: BLE001 — an unreadable answer is "no answer"
        return None


def _call_check(platform, server: str, timeout_s: float) -> Verdict | None:
    """Ask the LIVE app's own check, bounded. ``None`` when it cannot answer."""
    try:
        tool = platform.registry.get(f"mcp__{server}__{VERIFY_TOOL}")
    except Exception:  # noqa: BLE001
        tool = None
    client = getattr(tool, "client", None)
    remote = getattr(tool, "remote_name", "") or VERIFY_TOOL
    if client is None or not hasattr(client, "call_tool"):
        return None
    with _lock:
        if server in _calls_out:
            return None  # the last call is still out: no second one behind it
        _calls_out.add(server)
    box: dict[str, Any] = {}
    done = threading.Event()

    def _run() -> None:
        try:
            box["result"] = asyncio.run(client.call_tool(remote, {}))
        except BaseException as exc:  # noqa: BLE001 — recorded, never raised
            box["error"] = exc
        finally:
            with _lock:
                _calls_out.discard(server)
            done.set()

    try:
        threading.Thread(target=_run, name=f"signin-check-{server}", daemon=True).start()
    except Exception:  # noqa: BLE001 — no thread: nothing is out
        with _lock:
            _calls_out.discard(server)
        return None
    if not done.wait(timeout_s):
        return None  # still running: the next check (after this one ends) asks again
    if "result" not in box:
        return None
    return parse_check(box["result"])


def _loaded_since(server: str) -> datetime | None:
    """When the app's tools were loaded (its load record), as a naive UTC
    time the ledger's ``created_at`` compares with; ``None`` when unknown."""
    try:
        from ..mcp.tools import load_status

        at = str((load_status(server) or {}).get("at") or "")
        if not at:
            return None
        when = datetime.fromisoformat(at)
        if when.tzinfo is not None:
            when = when.astimezone(timezone.utc).replace(tzinfo=None)
        return when
    except Exception:  # noqa: BLE001
        return None


def _call_succeeded(platform, server: str) -> bool:
    """Did one of the app's own tools (not a sign-in tool) answer a call since
    it loaded? A bounded read: one indexed-by-nothing ``LIMIT 1`` over the
    names of THIS app's loaded tools. Never raises."""
    try:
        names = [
            n
            for n in platform.registry.mcp_names(server)
            if n.split("__", 2)[-1] not in SIGNIN_TOOLS
        ]
        if not names:
            return False
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import ToolInvocation

        stmt = select(ToolInvocation.id).where(
            ToolInvocation.tool.in_(names),  # type: ignore[attr-defined]
            ToolInvocation.ok == True,  # noqa: E712 — SQL, not Python truth
        )
        since = _loaded_since(server)
        if since is not None:
            stmt = stmt.where(ToolInvocation.created_at >= since)
        with session_scope(platform.engine) as db:
            return db.exec(stmt.limit(1)).first() is not None
    except Exception:  # noqa: BLE001 — unsure is "not proven"
        return False


def check_now(
    platform, server: str, identity: str = "", *, timeout_s: float | None = None
) -> Verdict:
    """Ask now (blocking, ≤ ``timeout_s``), remember and return the verdict."""
    verdict = _call_check(platform, server, VERIFY_TIMEOUT_S if timeout_s is None else timeout_s)
    if verdict is None:
        verdict = Verdict(True, "", "call") if _call_succeeded(platform, server) else Verdict(False)
    record(server, verdict, identity)
    return verdict


def record(server: str, verdict: Verdict, identity: str = "") -> None:
    """Remember a verdict someone just learned."""
    with _lock:
        _answers[server] = (time.monotonic(), verdict, identity)


def seen(
    platform,
    server: str,
    identity: str = "",
    *,
    spawn: Callable[[Callable[[], None]], None] | None = None,
) -> Verdict | None:
    """The last verdict for this saved app (``None`` before the first lands,
    or when the app was saved differently since), without blocking.

    A stale or missing verdict starts ONE background check for this app;
    ``spawn`` lets a test run it inline."""
    now = time.monotonic()
    with _lock:
        entry = _answers.get(server)
        current = entry if entry is not None and entry[2] == identity else None
        stale = current is None or now - current[0] >= _ttl(current[1])
        running = server in _inflight
        start = stale and not running
        if start:
            _inflight[server] = now
    if start:

        def _run() -> None:
            try:
                check_now(platform, server, identity)
            except Exception:  # noqa: BLE001 — a check never breaks the gallery
                pass
            finally:
                with _lock:
                    _inflight.pop(server, None)

        try:
            (spawn or _thread)(_run)
        except Exception:  # noqa: BLE001 — no thread: the next read tries again
            with _lock:
                _inflight.pop(server, None)
    return current[1] if current is not None else None


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="signin-seen", daemon=True).start()


def forget(server: str | None = None) -> None:
    """Drop the verdict for one app (or all), so the next read asks again — a
    removed app must not keep its old answer for a new connect. A check in
    flight may land after this; it only records what it saw."""
    with _lock:
        if server is None:
            _answers.clear()
            _inflight.clear()
            _calls_out.clear()
        else:
            _answers.pop(server, None)
