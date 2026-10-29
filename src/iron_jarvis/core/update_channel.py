"""Keep a version off the STABLE update channel, from the app (v1.405.0).

The Stable channel (``updates-stable``) is written only by the repository's
``.github/workflows/stable.yml``; holding a version back is that workflow's
``hold`` input. A PC on Fresh meets a release first, so the Updates page there
offers "Keep this version off Stable" — which this module turns into ONE
``gh workflow run stable.yml --repo <repo> -f hold=<X.Y.Z>``:

* ONLY when the GitHub CLI is installed and signed in (``harness.branches.
  gh_path`` / ``gh_state`` — the Build sessions' own reading, reused);
* through ``core.procs`` (``branches._gh``: bounded, tree-killed, no prompt),
  never on the event loop (the routes are sync ``def`` — FastAPI's thread pool);
* the version validated as X.Y.Z before anything runs;
* and only after a second press (``confirm``).

When ``gh`` is missing or signed out the page shows the exact command and the
workflow's Actions page instead (:func:`hold_view`). The wait itself is TIME:
nothing here can see whether another PC ran a version, and these words never
claim it did.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: The repository the desktop app updates from — pinned equal to
#: desktop/package.json's publish config and main.js's manifest URLs.
STABLE_REPO = "RealDealCPA-VR/Iron-Jarvis"
#: The workflow that writes updates-stable.
STABLE_WORKFLOW = "stable.yml"
ACTIONS_URL = f"https://github.com/{STABLE_REPO}/actions/workflows/{STABLE_WORKFLOW}"
#: GitHub's host for the sign-in check.
GH_HOST = "github.com"

_VERSION_RX = re.compile(r"^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})$")

BAD_VERSION = "A version looks like 1.404.0."
CONFIRM_HOLD = "Press Keep this version off Stable twice to confirm."
NO_GH = "The GitHub CLI (gh) is not installed on this PC, so the app cannot ask GitHub for you."
GH_SIGNED_OUT = "The GitHub CLI is not signed in, so the app cannot ask GitHub for you."
GH_UNSURE = "The GitHub CLI did not answer in time, so the app cannot ask GitHub for you."


class HoldRefused(Exception):
    def __init__(self, message: str, status: int = 409, details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details


def clean_version(text: Any) -> str:
    """``"v1.404.0"`` / ``" 1.404.0 "`` → ``"1.404.0"``; raises
    :class:`HoldRefused` (400) on anything else."""
    m = _VERSION_RX.match(str(text or "").strip())
    if not m:
        raise HoldRefused(BAD_VERSION, 400)
    return "%d.%d.%d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def hold_args(version: str) -> list[str]:
    """The ``gh`` arguments of the hold (after the ``gh`` itself)."""
    return ["workflow", "run", STABLE_WORKFLOW, "--repo", STABLE_REPO, "-f", f"hold={clean_version(version)}"]


def hold_command(version: str) -> str:
    """The same request as a line the user can type."""
    return "gh " + " ".join(hold_args(version))


def _gh_facts() -> dict[str, Any]:
    from ..harness import branches as B

    try:
        return B.gh_state(GH_HOST)
    except Exception:  # noqa: BLE001 — gh_state never raises; belt and braces
        return {"installed": bool(B.gh_path()), "signed_in": None}


def hold_view(version: str) -> dict[str, Any]:
    """What the Updates page shows for "Keep this version off Stable":
    whether the app can ask for the user (``can_run``), why not in words
    (``reason``), and the command + Actions page to do it by hand."""
    v = clean_version(version)
    gh = _gh_facts()
    installed = bool(gh.get("installed"))
    signed = gh.get("signed_in")
    reason = "" if installed and signed is True else (
        NO_GH if not installed else GH_SIGNED_OUT if signed is False else GH_UNSURE
    )
    return {
        "version": v,
        "can_run": not reason,
        "reason": reason,
        "command": hold_command(v),
        "actions_url": ACTIONS_URL,
        "gh": {"installed": installed, "signed_in": signed},
    }


def request_hold(version: str, *, confirm: bool) -> dict[str, Any]:
    """Ask GitHub to run the Stable workflow with ``hold=<version>``.
    Blocking (a ``gh`` child, bounded) — call it off the event loop."""
    from ..detections.redact import mask
    from ..harness import branches as B

    v = clean_version(version)
    if confirm is not True:
        raise HoldRefused(CONFIRM_HOLD, 400)
    view = hold_view(v)
    if not view["can_run"]:
        raise HoldRefused(f"{view['reason']} Run this instead: {view['command']}", 409)
    try:
        got = B._gh(hold_args(v), Path.home(), timeout=B.GH_S)
    except B.GitRefused as exc:
        raise HoldRefused(str(exc) or NO_GH, 409) from None
    if got.timed_out:
        raise HoldRefused(
            f"GitHub did not answer within {int(B.GH_S)} seconds, so the hold may not have been "
            f"asked for. Check the Stable channel's runs: {ACTIONS_URL}",
            504,
        )
    if not got.ok:
        detail = str(mask((got.err or got.out or "").strip()))[:2000]
        first = detail.splitlines()[0] if detail else "no reason given"
        raise HoldRefused(f"GitHub did not take the request: {first}", 502, detail)
    return {
        "ok": True,
        "version": v,
        "message": (
            f"Asked GitHub to keep v{v} off Stable. The Stable channel's workflow records it "
            "within a few minutes, and Stable skips that version until you let it through."
        ),
        "actions_url": ACTIONS_URL,
    }
