"""ONE account for a whole Build turn (v1.368.0, IJ harness Phase 5).

Chat keeps the v1.301.0 behaviour: every call leases the FIRST FREE account
and rotates to the next one when Iron-Proxy parks it. A Build (IJ harness)
session runs on the account the user picked — shown in its header — and must
never land on another one: no rotation, no other provider, no other account.

The build route arms an :class:`AccountPin` for the turn it starts (a
:class:`~contextvars.ContextVar`, the ``providers/budget.py`` pattern: the
adapters are SHARED across every concurrent chat turn and run, so a value set
on one would leak into the next caller). ``asyncio.create_task`` and
``asyncio.to_thread`` copy the context, so the pin reaches the turn's driver
task, its stream pump and the worker thread that leases. Readers:

* ``iron_proxy.accounts.lease`` — a PINNED pick of that account
  (``/iron/pick?profileId=&pinned=1``): that account or a plain refusal, never
  the next one; a lease for any OTHER subscription provider is refused.
* ``iron_proxy.accounts.retry_lease`` — reports a limit, never leases the next
  account; when Iron-Proxy parked the account the facts land on the pin.
* the router — a turn under a pin is held like an explicit strict pick: no
  default fallback, no sideways failover, no capability reroute.

The pin also CARRIES FACTS BACK (the value is one shared mutable object):
``limit`` (the account's park, for the ``account_limit`` frame) and
``thinking_dropped`` (a second Claude account refused the first one's
thinking, and the call was sent again without it). Nothing here blocks.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AccountPin:
    """The account one Build turn runs on, and what happened to it."""

    #: The Iron-Proxy account id (never shown to the user).
    profile_id: str
    #: The Iron Jarvis provider it serves (``claude-cli`` / ``codex-cli``).
    ij_provider: str
    #: The account's name (its Iron-Proxy title) — never an e-mail.
    title: str = ""
    #: Filled when the pinned account cannot serve: ``{account, account_name,
    #: provider, vendor, kind, resets_at, resumable}`` (``kind`` = ``usage`` |
    #: ``rate`` | ``busy`` | ``other`` (parked) | ``signin`` | ``missing`` |
    #: ``disabled``; ``resets_at`` ISO-8601 UTC or None).
    limit: dict[str, Any] | None = None
    #: True once a call was sent again without the earlier replies' thinking
    #: (the account refused another account's signed thinking blocks).
    thinking_dropped: bool = False
    #: Leases this pin answered (account ids) — the tests' and the receipt's
    #: proof that nothing else was used.
    leased: list[str] = field(default_factory=list)


ACCOUNT_PIN: ContextVar["AccountPin | None"] = ContextVar("iron_jarvis_account_pin", default=None)


def set_pin(pin: "AccountPin | None") -> Token:
    """Arm ``pin`` for the current context; returns the token for
    :func:`reset_pin`."""
    return ACCOUNT_PIN.set(pin)


def reset_pin(token: Token) -> None:
    """Restore the previous value. A token from another context (a generator
    closed elsewhere) leaves the pin cleared instead of raising."""
    try:
        ACCOUNT_PIN.reset(token)
    except (ValueError, LookupError):
        ACCOUNT_PIN.set(None)


def current() -> "AccountPin | None":
    """The pin armed for this context, or None. Never raises."""
    try:
        return ACCOUNT_PIN.get()
    except Exception:  # noqa: BLE001 — a reader never breaks a call
        return None


__all__ = ["ACCOUNT_PIN", "AccountPin", "current", "reset_pin", "set_pin"]
