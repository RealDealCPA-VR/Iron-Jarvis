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
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["login_succeeded", "watch_signin"]

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
            if not login_succeeded(session.output_tail(), getattr(session, "signin_provider", "")):
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
