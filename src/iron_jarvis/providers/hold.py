"""A Build session's MODEL is held for the whole turn (v1.372.0).

The user's decision for IJ harness part 2 (2026-10-22, decision 4): a Build
session and its helper agents run "always on the session's own account and
model — never another provider". v1.368.0 held a turn on its provider only
when an Iron-Proxy account was pinned (``iron_proxy.pin``); a session on this
PC's own login, a local model or an API key had no pin, so a failed call could
still fall back to the default provider or fail over sideways, and an image
could be rerouted to a vision model the session never chose.

The build route arms a :class:`ProviderHold` for the turn it starts (a
``ContextVar``, the ``iron_proxy.pin`` pattern: ``asyncio.create_task`` and
``asyncio.to_thread`` copy it, so the driver task, its pump, the tool calls
and every helper the turn starts carry it). Readers:

* the router — a call under a hold is BOUND to it (``_bind_to_hold``: no
  provider named = the session's; another provider named = refused in a
  sentence), and a held call that is not an AUTO call gets no capability
  reroute, no default fallback and no sideways failover; the provider's own
  error is raised. The prompted-tools wrap stays: it keeps the request on the
  SAME model. A hold on Auto or on the default ("") binds nothing.
* ``view_image`` — asks the session's own model, never the vision role's.
* ``harness.helpers.pinned`` — arms it again explicitly around a helper's
  task, so a hop that dropped the context cannot free the helper.

Nothing here blocks; only the router raises :class:`HeldProviderRefused`.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from .adapters.base import ProviderError


@dataclass(frozen=True)
class ProviderHold:
    """The provider and model one Build turn runs on ("" = the default)."""

    provider: str = ""
    model: str = ""


PROVIDER_HOLD: ContextVar["ProviderHold | None"] = ContextVar(
    "iron_jarvis_provider_hold", default=None
)


class HeldProviderRefused(ProviderError):
    """A call inside a held Build turn named ANOTHER provider (v1.372.0
    review). Never failed over, never retried: the session's model is the
    only one its work may reach."""

    no_failover = True

    def __init__(self, held: str, asked: str) -> None:
        super().__init__(
            f"This Build session runs on {held}, so its work is never sent to "
            f"{asked}. Choose another model for the session if you want that.",
            transient=False,
        )
        self.held = held
        self.asked = asked


def set_hold(provider: str = "", model: str = "") -> Token:
    """Arm a hold for the current context; returns the token for
    :func:`reset_hold`."""
    return PROVIDER_HOLD.set(
        ProviderHold(str(provider or "").strip(), str(model or "").strip())
    )


def reset_hold(token: Token) -> None:
    """Restore the previous value. A token from another context leaves the
    hold cleared instead of raising."""
    try:
        PROVIDER_HOLD.reset(token)
    except (ValueError, LookupError):
        PROVIDER_HOLD.set(None)


def current_hold() -> "ProviderHold | None":
    """The hold armed for this context, or None. Never raises."""
    try:
        return PROVIDER_HOLD.get()
    except Exception:  # noqa: BLE001 — a reader never breaks routing
        return None


def held_model_cannot_see(manager: object) -> bool:
    """True when a Build turn's hold is armed and the HELD model reports that
    it cannot take pictures (v1.372.0 review). The router no longer moves a
    held picture to a vision model, so ``view_image`` and OCR ask here first
    and say so — a model that never saw the picture must not describe it.
    False when unknown (no hold, no manager, a hold on Auto or the default,
    a lookup that failed): the call itself then decides. Never raises."""
    hold = current_hold()
    if hold is None or not hold.provider or hold.provider == "auto" or manager is None:
        return False
    try:
        adapter = manager.get(hold.provider, hold.model or None)  # type: ignore[attr-defined]
        caps = adapter.capabilities() if callable(getattr(adapter, "capabilities", None)) else {}
        return not bool((caps or {}).get("vision", True))
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "PROVIDER_HOLD", "HeldProviderRefused", "ProviderHold", "current_hold",
    "held_model_cannot_see", "reset_hold", "set_hold",
]
