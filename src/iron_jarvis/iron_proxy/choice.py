"""An ACCOUNT the user chose for one piece of work (v1.424.0, D6).

The audit (docs/HARNESS-BUILD-AUDIT-2026-11-01.md, gap D6): only a Chat ›
Code session could be put on one Iron-Proxy account (``routes/build``'s
:class:`~iron_jarvis.iron_proxy.pin.AccountPin`). Everywhere else — a plain
chat, a custom agent that is not a team member, a schedule, a night task —
every call leased the FIRST FREE account in Iron-Proxy's order. Now each of
those doors may name an account; this module is the ONE rule every door
checks that choice by (the model team's member rule, v1.394.0), and builds the
pin from:

* the account must be one Iron-Proxy LISTS (its CACHED snapshot — never a
  call) and turned on;
* it belongs to ONE vendor (Claude Code, Codex, Grok), and the work's model
  must run on that vendor's CLI (``claude-cli`` / ``codex-cli`` /
  ``grok-cli``, or a keyless ``anthropic`` / ``openai`` the CLI serves):
  another vendor's account is refused in words, never "fixed" by moving the
  model;
* Iron-Proxy off (or not read yet) with an account named: refused in words —
  nothing ever runs on another login than the one chosen.

No account named: ``(None, "")`` — the door behaves exactly as it did before
this module (Iron-Proxy's normal pick, or this PC's own login). Nothing here
blocks (cached reads only) and nothing raises.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: The longest account id accepted (Iron-Proxy's are short).
MAX_ID = 120

OFF_WORDS = (
    "Several accounts is off, so the chosen account cannot be used — turn it on in Settings › "
    "Connections, or choose No account."
)
NOT_READ_WORDS = "Your accounts are not listed yet — try again in a moment."
UNKNOWN_WORDS = (
    "That account is no longer in your accounts — choose another account, or No account."
)
TURNED_OFF_WORDS = (
    "The account “{name}” is turned off on the Several accounts card — turn it on in Settings › Connections, "
    "or choose another account."
)
NOT_A_PLAN_WORDS = (
    "“{name}” is a {vendor} account, and an account only applies to work on Claude Code, "
    "Codex or Grok — this {who} runs on {model}. Choose No account, or one of those models."
)
OTHER_VENDOR_WORDS = (
    "“{name}” is a {vendor} account, and this {who} runs on {model} — choose a {model_vendor} "
    "account, or No account."
)


def clean(account: Any) -> str:
    """An account id as sent: trimmed, one line, bounded ("" = none)."""
    text = " ".join(str(account or "").split())
    return text[:MAX_ID]


def _svc(platform: Any) -> Any:
    return getattr(platform, "iron_proxy", None)


def check(platform: Any, account: Any, provider: str, model: str = "", *,
          who: str = "chat") -> "tuple[dict[str, Any] | None, str]":
    """``(the account's row, "")`` when ``account`` may carry work that runs
    on ``provider`` (``""`` = the configured default provider), else
    ``(None, refusal in words)``; ``(None, "")`` when no account is named.
    ``who`` names the work in the sentence ("chat", "agent", "schedule",
    "night task"). Cached reads only; never raises (an unreadable fact is a
    refusal — nothing runs on an account that could not be checked)."""
    acct = clean(account)
    if not acct:
        return None, ""
    try:
        from ..harness import build_accounts as BA

        svc = _svc(platform)
        if svc is None or not getattr(svc, "enabled", False):
            return None, OFF_WORDS
        if BA.snapshot(svc) is None:
            return None, NOT_READ_WORDS
        row = BA.find(svc, acct)
        if row is None:
            return None, UNKNOWN_WORDS
        if row.get("status") == BA.OFF:
            return None, TURNED_OFF_WORDS.format(name=row["name"])
        name = str(provider or "").strip()
        if not name:
            name = str(getattr(getattr(platform, "config", None), "default_provider", "") or "")
        vendor = BA.vendor_of(platform, name)
        from ..agents.model_team import model_words

        words = model_words(platform, name, model)
        if not vendor:
            return None, NOT_A_PLAN_WORDS.format(
                name=row["name"], vendor=row["vendor_label"], who=who, model=words,
            )
        if vendor != row["vendor"]:
            return None, OTHER_VENDOR_WORDS.format(
                name=row["name"], vendor=row["vendor_label"], who=who, model=words,
                model_vendor=BA.VENDORS[vendor]["label"],
            )
        return row, ""
    except Exception:  # noqa: BLE001 — unsure: refused, never another login
        log.debug("account choice check failed", exc_info=True)
        return None, NOT_READ_WORDS


def pin_for(platform: Any, account: Any, provider: str, model: str = "", *,
            who: str = "chat") -> "tuple[Any, str]":
    """``(AccountPin | None, refusal)`` for the work :func:`check` admits:
    the pin every lease of that work honours (that account or a plain
    refusal — never the next one). Never raises."""
    row, refusal = check(platform, account, provider, model, who=who)
    if row is None:
        return None, refusal
    from .pin import AccountPin

    return AccountPin(profile_id=row["id"], ij_provider=row["provider"], title=row["name"]), ""


#: v1.424.0 (review): a piece of work names one account and the custom agent
#: that does it names ANOTHER of its own — the agent's own pin is armed inside
#: the work's (``model_team.member_scope``), so the work would run on the
#: agent's account, not the one chosen for it.
AGENT_OWN_WORDS = (
    "'{agent}' runs on its own account “{own}” — choose that account for this {who}, or No "
    "account (it then runs on its own)."
)


def agent_conflict(platform: Any, account: Any, record: Any, *, who: str = "schedule") -> str:
    """The sentence when ``account`` (the work's choice) is not the account
    the custom agent ``record`` names of its own; "" when there is no
    conflict (no account chosen, an agent with none, the same one). Cached
    reads only; never raises (v1.424.0 review)."""
    acct = clean(account)
    own = clean(getattr(record, "account", "") if record is not None else "")
    if not acct or not own or acct == own:
        return ""
    title = own
    try:
        from ..harness import build_accounts as BA

        row = BA.find(_svc(platform), own)
        if row is not None and row.get("name"):
            title = str(row["name"])
    except Exception:  # noqa: BLE001 — the id stands in for its name
        pass
    return AGENT_OWN_WORDS.format(agent=str(getattr(record, "name", "") or "This agent"),
                                  own=title, who=who)


def receipt(pin: Any) -> dict[str, Any]:
    """``route.account`` of work that ran on a chosen account: its id and
    NAME (Iron-Proxy's title — never an e-mail), as a Build turn's says it
    (``routes/build.account_receipt``)."""
    out: dict[str, Any] = {"id": str(getattr(pin, "profile_id", "") or ""),
                           "name": str(getattr(pin, "title", "") or "")}
    return out


__all__ = ["agent_conflict", "check", "clean", "pin_for", "receipt"]
