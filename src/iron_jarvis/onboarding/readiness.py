"""First-run detection + the combined readiness report.

``is_first_run`` answers "is this a brand-new install?" so the dashboard can show
a welcome overlay. ``readiness`` bundles the machine diagnostic (:func:`doctor`)
and the getting-started checklist into one payload the daemon/CLI can render.
"""

from __future__ import annotations

from .checklist import (
    _has_any,
    _provider_connected,
    getting_started,
    voice_backend_present,
)
from .doctor import doctor


def is_first_run(platform) -> bool:
    """True for a brand-new install: zero sessions AND no real provider connected.

    A fresh checkout with only the offline mock model and no history is a first
    run; running any session or wiring a real model flips it to False.
    """
    from ..core.models import Session

    has_sessions = _has_any(platform.engine, Session)
    return not has_sessions and not _provider_connected(platform)


def readiness(platform) -> dict:
    """One payload combining diagnostics, the checklist, version, and first-run.

    Shape::

        {
          "version": str,
          "first_run": bool,
          "doctor": {ok, checks},
          "checklist": [ {key, title, detail, done, action, optional}, ... ],
          "next_step": {step dict} | None,   # first incomplete REQUIRED step
          "voice": {"available": bool, "backend": str | None},
        }

    ``next_step`` skips OPTIONAL steps (e.g. ``set_up_voice``) so voice never
    becomes the nudged "do this next" — it stays a pure opt-in.
    """
    from .. import __version__

    diagnostic = doctor()
    steps = getting_started(platform)
    # Only REQUIRED (non-optional) incomplete steps can be the next step: the
    # optional voice item must never be advertised as "do this next".
    next_step = next(
        (s for s in steps if not s["done"] and not s.get("optional")), None
    )
    voice_available, voice_backend = voice_backend_present(platform)
    return {
        "version": __version__,
        "first_run": is_first_run(platform),
        "doctor": diagnostic,
        "checklist": steps,
        "next_step": next_step,
        "voice": {"available": voice_available, "backend": voice_backend},
    }
