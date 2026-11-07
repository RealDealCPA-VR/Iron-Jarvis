"""A sign-in pane notices its own login landing (v1.303.3).

``POST /iron-proxy/accounts/{id}/signin`` opens a Build pane that runs the
account's login command AS that account. The login lands in the account's own
home (``.credentials.json``), but Iron-Proxy is not told: its state was set
``unauthenticated`` when the account was created and nothing asked it to look
again, so the card said "Needs sign-in" forever (a LIVE report).

So the pane is marked (``signin_for`` = the profile id) and its activity step
watches the output for the vendor CLI's OWN success shape. On the FIRST
sighting in a pane, Iron-Proxy re-checks that account at once (``POST
/iron/refresh {id}``, off the loop, in the service's background pool); a later
re-check — when that one said "not yet" — waits for the normal 10 s throttle,
at most :data:`MAX_CHECKS` per pane. Once the re-checked state is ``ready`` (or
``active``) the pane row says ``signed_in: true`` and the account snapshot —
chips, provider availability — is refreshed. ``signed_in`` comes from
Iron-Proxy's answer, never from the words alone.

THE SUCCESS SHAPES, read from the claude 2.1.289 binary (``claude.exe``):

* ``claude auth login`` (what Iron-Proxy's login command runs) ends with
  ``process.stdout.write("Login successful.\\n")`` (two call sites, after
  ``tengu_oauth_success``) — a line that is exactly ``Login successful.``;
* the interactive ``/login`` screen renders ``Logged in as <email>`` above
  ``Login successful. Press Enter to continue…`` — the PAIR.

Never: a line Claude Code shows as a tool result (``⎿``), a prompt the user
typed (``>``/``❯``, also inside the input box ``│ >``), or the output of an
``echo`` the pane ran (an ``echo … Login successful`` command in the window
voids the sighting). "Authentication successful" is the browser page and MCP
server auth — never the account login. Only Anthropic accounts are watched
(the other CLIs' words were not measured); every account still has the card's
"Check again".

v1.443.0: THIS PC'S OWN LOGIN too (the connect door's and Settings' "Sign in
to Claude Code / Codex" press, ``POST /terminals/launch {account:
"default"}``). Before, the door said "not signed in" for up to two minutes
after the login landed (``cli_auth.UNSIGNED_RECHECK_S``) and offered Sign in
again. :func:`watch_default_login` watches such a pane on a small daemon
thread; when the CLI's own success shape is on screen it re-checks the shared
sign-in verdict (``cli_auth.DEFAULT_PROBE.refresh``), so /health — and the
door — turn green within seconds. Codex's shapes were read from the codex
0.157.x binary's own strings: the sign-in screen's ``✓ Signed in with your
ChatGPT account`` and ``codex login``'s ``Successfully logged in`` (never
``Successfully logged in to MCP server …`` nor the MCP flow's
``Successfully logged in.``). The probe runs ONLY on that positive evidence,
at most :data:`MAX_CHECKS` times a pane and :data:`RECHECK_GAP_S` apart.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

__all__ = [
    "default_login_step",
    "default_login_succeeded",
    "login_succeeded",
    "watch_default_login",
    "watch_signin",
]

#: The newest rendered lines searched for the success shape.
SCAN_LINES = 40
#: Re-checks one sign-in pane may ask for.
MAX_CHECKS = 6

#: Box sides a TUI frame may put around a line.
_FRAME = "│┃║"
_ECHO = re.compile(r"\becho\b.*login successful", re.I)

#: Re-checked states that mean the login took.
_SIGNED_IN = frozenset({"ready", "active"})


def _body(line: str) -> str:
    return line.strip().strip(_FRAME).strip()


def _claude_success(lines: list[str]) -> bool:
    if any(_ECHO.search(ln) for ln in lines):
        return False  # an echo in the window: the words are the shell's
    # A line's BODY (box sides and spaces stripped, nothing else) must BE the
    # shape: a ⎿ result row, a > / ❯ prompt or a "│ > …" input box keeps its
    # mark in the body and can never equal it.
    bodies = [_body(ln) for ln in lines]
    for i, body in enumerate(bodies):
        if body == "Login successful.":
            return True  # `claude auth login`
        if body.startswith("Login successful. Press Enter to continue"):
            before = [b for b in bodies[max(0, i - 3):i] if b]
            if any(b.startswith("Logged in as ") for b in before):
                return True  # the /login screen's pair
    return False


#: Per Iron-Proxy provider: does this window show its CLI's own success shape?
_SUCCESS = {"anthropic": _claude_success}


def login_succeeded(tail: str, provider: str) -> bool:
    """Does ``tail`` (ANSI-stripped) show ``provider``'s CLI saying the login
    succeeded, in its last :data:`SCAN_LINES` lines? Pure."""
    check = _SUCCESS.get(str(provider or ""))
    if check is None:
        return False
    lines = [ln.rstrip() for ln in str(tail or "").splitlines() if ln.strip()][-SCAN_LINES:]
    return check(lines)


def watch_signin(session: Any, svc: Any) -> None:
    """The activity step for a sign-in pane. BLOCKING only for the snapshot
    refresh after a confirmed login (the activity route runs on the
    threadpool); the re-check itself runs in the service's pool. Never raises.

    * the success shape is on screen and no re-check is pending → ask
      Iron-Proxy: FORCED on the pane's first sighting, then throttled, at
      most :data:`MAX_CHECKS` per pane;
    * that re-check finished ``ready``/``active`` → ``session.signed_in =
      True`` and ``svc.refresh_accounts()``."""
    try:
        pid = getattr(session, "signin_for", None)
        if not pid or getattr(session, "signed_in", False) or svc is None:
            return
        fut = getattr(session, "_signin_future", None)
        if fut is None:
            checks = int(getattr(session, "_signin_checks", 0) or 0)
            if checks >= MAX_CHECKS:
                return
            # v1.333.0: the tail is read again only when the pane PRINTED
            # since a read that found nothing — the activity poll asked
            # every 2.5 s and decoded and scanned the tail each time.
            seq = getattr(session, "output_seq", None)
            if seq is not None and seq == getattr(session, "_signin_scanned_seq", None):
                return
            if not login_succeeded(session.output_tail(), getattr(session, "signin_provider", "")):
                session._signin_scanned_seq = seq
                return
            fut = svc.request_refresh(pid, force=checks == 0)
            if fut is not None:
                session._signin_future = fut
                session._signin_checks = checks + 1
            return
        if not fut.done():
            return
        session._signin_future = None
        state = None if fut.exception() is not None else fut.result()
        if isinstance(state, dict) and state.get("status") in _SIGNED_IN:
            session.signed_in = True
            svc.refresh_accounts()
    except Exception:  # noqa: BLE001 — a watch never breaks the activity poll
        return


# --- v1.443.0: this PC's own login ---------------------------------------

#: How often the watch thread looks at a default-login sign-in pane.
WATCH_TICK_S = 2.0
#: How long it watches before giving up (the 2-minute re-check carries on).
WATCH_FOR_S = 900.0
#: The least time between two sign-in re-checks of one pane.
RECHECK_GAP_S = 10.0

_CODEX_ECHO = re.compile(r"\becho\b.*(successfully logged in|signed in with)", re.I)
_CODEX_SIGNED_IN = "Signed in with your ChatGPT account"


def _codex_success(lines: list[str]) -> bool:
    if any(_CODEX_ECHO.search(ln) for ln in lines):
        return False
    for ln in lines:
        body = _body(ln)
        if body == "Successfully logged in":
            return True  # `codex login`
        # The sign-in screen's tick line ("✓ Signed in with your ChatGPT
        # account"); a ⎿ / > / ❯ mark keeps the body from ever equalling it.
        if body.lstrip("✓✔ ").strip() == _CODEX_SIGNED_IN and body[:1] in "✓✔":
            return True
    return False


#: Per CLI binary (``cli_auth.CLI_BINARIES``): its own success shape.
_DEFAULT_SUCCESS = {"claude": _claude_success, "codex": _codex_success}


def default_login_succeeded(tail: str, binary: str) -> bool:
    """Does ``tail`` show ``binary`` (``claude`` / ``codex``) saying its login
    succeeded, in its last :data:`SCAN_LINES` lines? Pure."""
    check = _DEFAULT_SUCCESS.get(str(binary or ""))
    if check is None:
        return False
    lines = [ln.rstrip() for ln in str(tail or "").splitlines() if ln.strip()][-SCAN_LINES:]
    return check(lines)


def default_login_step(session: Any, binary: str, state: dict, *, probe: Any, now: float) -> bool:
    """One look at a default-login sign-in pane. Returns True when the watch
    is over (signed in, the pane gone, or the re-checks used up). BLOCKING
    only for a re-check (the CLI's own status command, bounded by the probe).
    Never raises."""
    try:
        st = probe.status(binary)
        if st is not None and st.signed_in is True:
            return True
        if not getattr(session, "alive", False):
            return True
        checks = int(state.get("checks", 0))
        if checks >= MAX_CHECKS:
            return True
        last = state.get("last_at")
        if last is not None and now - float(last) < RECHECK_GAP_S:
            return False
        seq = getattr(session, "output_seq", None)
        if seq is not None and seq == state.get("seq") and last is None:
            return False  # nothing new on screen since a look that found nothing
        if not default_login_succeeded(session.output_tail(), binary):
            state["seq"] = seq
            return False
        state["checks"] = checks + 1
        state["last_at"] = now
        fresh = probe.refresh(binary)
        return bool(getattr(fresh, "signed_in", None) is True)
    except Exception:  # noqa: BLE001 — a watch never breaks a pane
        return True


def watch_default_login(
    session: Any,
    binary: str,
    *,
    probe: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    start: bool = True,
) -> threading.Thread | None:
    """Watch a pane started to sign in to THIS PC's own ``claude``/``codex``
    (v1.443.0) on a daemon thread, for at most :data:`WATCH_FOR_S`. Returns
    the thread (not started when ``start`` is False — tests drive
    :func:`default_login_step`), or None for a CLI with no known shape.
    Never raises."""
    if binary not in _DEFAULT_SUCCESS:
        return None
    if probe is None:
        from ..providers.cli_auth import DEFAULT_PROBE

        probe = DEFAULT_PROBE
    state: dict = {}

    def _loop() -> None:
        until = clock() + WATCH_FOR_S
        while clock() < until:
            if default_login_step(session, binary, state, probe=probe, now=clock()):
                return
            sleep(WATCH_TICK_S)

    t = threading.Thread(target=_loop, name=f"signin-watch-{binary}", daemon=True)
    if start:
        try:
            t.start()
        except RuntimeError:
            return None
    return t
