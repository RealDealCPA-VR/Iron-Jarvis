"""A Build session's ACCOUNT (v1.368.0, IJ harness Phase 5).

Each IJ harness session runs on ONE subscription account, shown in its header:
a Claude Code account (served by ``claude-cli``) or a Codex account (served by
``codex-cli``). Iron-Proxy is the account manager (CLAUDE.md v1.301.0); this
module only READS its cached snapshot (``IronProxyService.cached_accounts`` —
never a network call, the v1.302.0 rule for a list) and words it:

* :func:`accounts_view` — ``GET /build/accounts``: the two groups, each
  account with its name, the **Default** badge (first in Iron-Proxy's order),
  vendor + plan (Iron-Proxy's ``plan``, never guessed) and ONE status:
  ``ready`` | ``limit`` (+ ``resets_at``) | ``needs_sign_in`` | ``usage``
  (+ ``usage: {percent, window, window_mins, resets_at}`` — the window nearest
  its limit, Iron-Proxy's windows only; no percent when the vendor sent none);
* :func:`find` / :func:`default_for` — one account, or the vendor's Default
  for a new session: the account the list badges **Default** (the first one
  turned on), even when it is at its limit — the header shows that, and the
  first turn shows the limit card with Choose account. Starting a new session
  on a different account than the badge says would be the app choosing an
  account the user did not (v1.368.0 ship decision; the plan's wording);
* :func:`vendor_of` — which vendor a session's model belongs to ("" for a
  local model or an API key: such a session has no account, its header names
  the place in words);
* :func:`session_account` — the session's account with its live status.

Nothing here blocks except :func:`kick_plan_reads`, which only SCHEDULES a
background re-check (never waits). Iron-Proxy off, or no account for the
vendor, means no account — exactly Phase 4.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ..iron_proxy import accounts as _acc

log = logging.getLogger(__name__)

#: vendor -> (Iron Jarvis provider, Iron-Proxy provider, the group's name).
VENDORS: dict[str, dict[str, str]] = {
    "claude": {"provider": "claude-cli", "proxy": "anthropic", "label": "Claude Code"},
    "codex": {"provider": "codex-cli", "proxy": "openai", "label": "Codex"},
}
#: Iron Jarvis provider -> vendor.
VENDOR_OF_PROVIDER = {v["provider"]: k for k, v in VENDORS.items()}
#: Iron-Proxy provider -> vendor.
VENDOR_OF_PROXY = {v["proxy"]: k for k, v in VENDORS.items()}
#: A keyless API provider SERVED by the vendor's CLI (``manager.inherited_from``).
_API_OF = {"anthropic": "claude-cli", "openai": "codex-cli"}

#: The model a session gets when its account moves it to ANOTHER vendor and
#: the user named none: "" = the vendor CLI's own default (the picker's
#: "subscription" row).
DEFAULT_MODEL = ""

#: Status words (the API's own vocabulary; Phase 6 draws the card from them).
READY, LIMIT, NEEDS_SIGN_IN, USAGE = "ready", "limit", "needs_sign_in", "usage"
#: A session's account that the list leaves out (turned off on the card) or
#: Iron-Proxy no longer has.
OFF, MISSING = "off", "missing"

#: Plan words as a card shows them; anything else is title-cased as given.
_PLAN_WORDS = {"max": "Max", "pro": "Pro", "plus": "Plus", "team": "Team",
               "enterprise": "Enterprise", "free": "Free", "business": "Business",
               "edu": "Edu", "go": "Go"}

#: A Claude account whose plan Iron-Proxy does not know yet is re-read (its
#: status command reads only the plan for a serving or parked account) at
#: most this often, in the background.
PLAN_READ_EVERY_S = 600.0
_PLAN_ASKED: dict[str, float] = {}
_PLAN_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# small words
# --------------------------------------------------------------------------- #


def plan_label(plan: Any) -> str | None:
    text = str(plan or "").strip().lower()
    if not text:
        return None
    return _PLAN_WORDS.get(text) or text.replace("_", " ").title()


def window_words(mins: Any, kind: Any = "") -> str:
    """``10080`` -> "7 days", ``300`` -> "5 hours"; the kind when no length
    ("5h" -> "5 hours", "7d" -> "7 days"); "" when neither says."""
    try:
        m = int(mins) if mins is not None and not isinstance(mins, bool) else None
    except (TypeError, ValueError):
        m = None
    if m is None:
        m = {"5h": 300, "7d": 10080, "1d": 1440, "24h": 1440}.get(str(kind or "").lower())
    if not m or m <= 0:
        return ""
    if m % 1440 == 0:
        days = m // 1440
        return f"{days} day" + ("" if days == 1 else "s")
    if m % 60 == 0:
        hours = m // 60
        return f"{hours} hour" + ("" if hours == 1 else "s")
    return f"{m} minute" + ("" if m == 1 else "s")


def _parse(iso: Any) -> datetime | None:
    if iso is None or iso == "":
        return None
    try:
        if isinstance(iso, (int, float)) and not isinstance(iso, bool):
            return datetime.fromtimestamp(float(iso), tz=timezone.utc)
        when = datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _iso(when: datetime | None) -> str | None:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if when else None


# --------------------------------------------------------------------------- #
# the snapshot
# --------------------------------------------------------------------------- #


def snapshot(svc: Any) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """The service's cached ``(profiles, states)``, or None (off, not running,
    not read yet). Never a network call; never raises."""
    try:
        if svc is None or not getattr(svc, "enabled", False):
            return None
        cache = svc.cached_accounts() if hasattr(svc, "cached_accounts") else None
    except Exception:  # noqa: BLE001 — a list never breaks on the snapshot
        return None
    if not cache:
        return None
    profiles, states = cache
    return (
        [p for p in profiles if isinstance(p, Mapping)] if isinstance(profiles, list) else [],
        dict(states) if isinstance(states, Mapping) else {},
    )


def _vendor_profiles(profiles: list[dict[str, Any]], vendor: str) -> list[dict[str, Any]]:
    proxy = VENDORS[vendor]["proxy"]
    mine = [
        p for p in profiles
        if p.get("provider") == proxy and (p.get("lane") or "cli") == "cli"
    ]
    return sorted(mine, key=lambda p: (p.get("order") or 0))


def _is_on(profile: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    return profile.get("enabled") is not False and state.get("status") != "disabled"


def _parked_until(state: Mapping[str, Any], now: datetime) -> tuple[bool, datetime | None]:
    """``(parked now, until)``. A park whose reset has passed is not a park
    (Iron-Proxy clears it on the next pick); a park with no reset is one."""
    if state.get("status") != "parked":
        return False, None
    until = _parse(state.get("parkedUntil"))
    if until is None:
        return True, None
    return (until > now), until


def _reason_kind(state: Mapping[str, Any]) -> str:
    reason = state.get("parkedReason")
    return str(reason.get("kind") or "") if isinstance(reason, Mapping) else ""


def _usage_of(state: Mapping[str, Any], now: datetime) -> dict[str, Any] | None:
    """The window NEAREST its limit (the highest percent), from Iron-Proxy's
    ``windows`` only — none when the vendor sent none. A window whose reset
    has passed is over and left out."""
    best: dict[str, Any] | None = None
    for w in state.get("windows") or []:
        if not isinstance(w, Mapping):
            continue
        used = w.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            continue
        resets = _parse(w.get("resetsAt"))
        if resets is not None and resets <= now:
            continue
        if best is None or float(used) > best["_used"]:
            best = {
                "_used": float(used),
                "percent": int(round(float(used))),
                "window": window_words(w.get("windowMins"), w.get("kind")),
                "window_mins": w.get("windowMins") if isinstance(w.get("windowMins"), int) else None,
                "resets_at": _iso(resets),
            }
    if best is None:
        return None
    best.pop("_used", None)
    return best


def row_of(profile: Mapping[str, Any], state: Mapping[str, Any] | None, *,
           vendor: str, default_id: str = "", now: datetime | None = None) -> dict[str, Any]:
    """One account as a card shows it. No home, no e-mail, no token."""
    st = state if isinstance(state, Mapping) else {}
    now = now or datetime.now(timezone.utc)
    pid = str(profile.get("id") or "")
    row: dict[str, Any] = {
        "id": pid,
        "name": str(profile.get("title") or pid),
        "vendor": vendor,
        "vendor_label": VENDORS[vendor]["label"],
        "provider": VENDORS[vendor]["provider"],
        "default": bool(default_id) and pid == default_id,
        "plan": (str(st.get("plan")).strip().lower() or None) if st.get("plan") else None,
    }
    row["plan_label"] = plan_label(row["plan"])
    if not _is_on(profile, st):
        row["status"] = OFF
        return row
    if _acc.needs_sign_in(st):
        row["status"] = NEEDS_SIGN_IN
        return row
    parked, until = _parked_until(st, now)
    if parked:
        row["status"] = LIMIT
        row["resets_at"] = _iso(until)
        kind = _reason_kind(st)
        row["limit_kind"] = (
            "usage" if kind in ("quota-exhausted", "billing")
            else "rate" if kind == "rate-limit" else "busy" if kind == "overloaded" else "other"
        )
        return row
    usage = _usage_of(st, now)
    if usage is not None:
        row["status"] = USAGE
        row["usage"] = usage
    else:
        row["status"] = READY
    return row


def vendor_rows(snap: tuple[list[dict[str, Any]], dict[str, Any]], vendor: str, *,
                include_off: bool = False) -> list[dict[str, Any]]:
    """The vendor's CLI accounts in Iron-Proxy's order; the Default is the
    first one that is turned on."""
    profiles, states = snap
    mine = _vendor_profiles(profiles, vendor)
    now = datetime.now(timezone.utc)
    on = [p for p in mine if _is_on(p, states.get(p.get("id")) or {})]
    default_id = str(on[0].get("id") or "") if on else ""
    rows = [row_of(p, states.get(p.get("id")), vendor=vendor, default_id=default_id, now=now)
            for p in mine]
    return rows if include_off else [r for r in rows if r["status"] != OFF]


def find(svc: Any, account_id: str) -> dict[str, Any] | None:
    """The account ``account_id`` as a row (its vendor's group, status), from
    the cached snapshot — None when Iron-Proxy does not list it as a Claude
    Code / Codex subscription account."""
    snap = snapshot(svc)
    if snap is None or not account_id:
        return None
    for vendor in VENDORS:
        for row in vendor_rows(snap, vendor, include_off=True):
            if row["id"] == account_id:
                return row
    return None


def default_for(svc: Any, vendor: str) -> dict[str, Any] | None:
    """A new session's account: the vendor's **Default** (the row the list
    badges — the first account turned on in Iron-Proxy's order), whatever its
    status; None when Iron-Proxy has none (or is off)."""
    snap = snapshot(svc)
    if snap is None or vendor not in VENDORS:
        return None
    rows = vendor_rows(snap, vendor)
    return next((r for r in rows if r.get("default")), rows[0] if rows else None)


# --------------------------------------------------------------------------- #
# which vendor a session's model belongs to
# --------------------------------------------------------------------------- #


def vendor_of(platform: Any, provider: str) -> str:
    """``claude`` / ``codex`` when ``provider`` (or, empty, the default
    provider) runs on a subscription CLI — ``claude-cli`` / ``codex-cli``, or a
    keyless ``anthropic`` / ``openai`` the CLI serves — else "" (a local model
    or an API key: no account). Never raises."""
    try:
        name = str(provider or "").strip()
        if not name:
            name = str(getattr(getattr(platform, "config", None), "default_provider", "") or "")
        if name in VENDOR_OF_PROVIDER:
            return VENDOR_OF_PROVIDER[name]
        if name in _API_OF:
            manager = getattr(platform, "providers", None)
            served = manager.inherited_from(name) if manager is not None else None
            return VENDOR_OF_PROVIDER.get(str(served or ""), "")
    except Exception:  # noqa: BLE001 — no vendor is the safe answer (Phase 4)
        log.debug("build session vendor lookup failed", exc_info=True)
    return ""


# --------------------------------------------------------------------------- #
# the views
# --------------------------------------------------------------------------- #

OFF_NOTE = "Iron-Proxy is off; turn it on in Connections to run Build sessions on your accounts."
NOT_READ_NOTE = "Iron-Proxy has not listed its accounts yet; try again in a moment."
UPDATE_NOTE = "Iron-Proxy needs an update to keep a Build session on one account."


def _proxy_state(svc: Any) -> dict[str, Any]:
    """``{enabled, running, needs_update, note}`` — cached facts only."""
    from ..iron_proxy.service import PINNED_PICK_FEATURE

    enabled = bool(svc is not None and getattr(svc, "enabled", False))
    out: dict[str, Any] = {"enabled": enabled, "running": False, "needs_update": False, "note": ""}
    if not enabled:
        out["note"] = OFF_NOTE
        return out
    try:
        out["running"] = bool(svc.running())
        if out["running"] and not svc.has_feature(PINNED_PICK_FEATURE):
            out["needs_update"] = True
            out["note"] = UPDATE_NOTE
    except Exception:  # noqa: BLE001 — a fact we cannot read is not a guess
        out["running"] = False
    return out


def accounts_view(svc: Any) -> dict[str, Any]:
    """``GET /build/accounts``: ``{claude: [...], codex: [...], iron_proxy:
    {enabled, running, needs_update, note}}`` from the CACHED snapshot."""
    state = _proxy_state(svc)
    out: dict[str, Any] = {"claude": [], "codex": [], "iron_proxy": state}
    if not state["enabled"]:
        return out
    snap = snapshot(svc)
    if snap is None:
        if not state["note"]:
            state["note"] = NOT_READ_NOTE
        return out
    for vendor in VENDORS:
        out[vendor] = vendor_rows(snap, vendor)
    return out


def session_account(svc: Any, account_id: str, account_name: str = "") -> dict[str, Any] | None:
    """A session's account with its live status from the snapshot (``missing``
    when Iron-Proxy no longer lists it; ``status: None`` when Iron-Proxy is
    off or not read yet — the header still names it)."""
    if not account_id:
        return None
    row = find(svc, account_id)
    if row is not None:
        return row
    out = {"id": account_id, "name": account_name or "", "status": None}
    if snapshot(svc) is not None:
        out["status"] = MISSING
    return out


# --------------------------------------------------------------------------- #
# account GROUPS (v1.393.0, the long-jobs plan's Phase 4, decision 4)
# --------------------------------------------------------------------------- #

#: An account at or above this percent of ANY of its usage windows is not
#: usable for a takeover (decision 8: no new work starts on an account at
#: 95%). Read from the cached snapshot's windows — never a new vendor call.
TAKEOVER_MAX_PERCENT = 95.0

#: The limit kinds a group takeover answers: an account that REACHED A LIMIT
#: (its usage, its request rate, or a park Iron-Proxy did not explain). An
#: account that must sign in again, is turned off, is removed or is merely
#: busy is not "at its limit": its card asks the user (or waits), no move.
MOVE_KINDS = frozenset({"usage", "rate", "other", "unknown"})

GROUP_NOT_READ = "Iron-Proxy has not listed its accounts yet; try again in a moment"
GROUP_OFF = "Iron-Proxy is off; turn it on in Connections first"


def group_of(groups: Any, account_id: str) -> tuple[str, list[str]] | None:
    """``(name, ids)`` of the group holding ``account_id`` in the user's
    ``Config.account_groups``, or None (no group — such an account never
    moves and never receives a move). Never raises."""
    if not account_id or not isinstance(groups, Mapping):
        return None
    for name, ids in groups.items():
        if isinstance(ids, (list, tuple)) and account_id in [str(i) for i in ids]:
            return str(name), [str(i) for i in ids]
    return None


def peak_percent(state: Mapping[str, Any] | None, now: datetime | None = None) -> float | None:
    """The highest percent used across the account's usage windows that are
    still running (a window whose reset passed is over), or None when the
    vendor sent no number (never read as 0% — "usage not known yet")."""
    if not isinstance(state, Mapping):
        return None
    now = now or datetime.now(timezone.utc)
    best: float | None = None
    for w in state.get("windows") or []:
        if not isinstance(w, Mapping):
            continue
        used = w.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            continue
        resets = _parse(w.get("resetsAt"))
        if resets is not None and resets <= now:
            continue
        best = float(used) if best is None else max(best, float(used))
    return best


def _state_of(snap: tuple[list[dict[str, Any]], dict[str, Any]], pid: str) -> Mapping[str, Any]:
    st = snap[1].get(pid)
    return st if isinstance(st, Mapping) else {}


def usable_now(row: Mapping[str, Any] | None, state: Mapping[str, Any] | None,
               now: datetime | None = None) -> bool:
    """THE one rule for "this account can take over now", read from the
    cached snapshot: turned on, signed in (``accounts.needs_sign_in`` — the
    v1.382.0 rule, inside :func:`row_of`), not parked, and below
    :data:`TAKEOVER_MAX_PERCENT` of every usage window."""
    if not row or row.get("status") not in (READY, USAGE):
        return False
    peak = peak_percent(state, now)
    return peak is None or peak < TAKEOVER_MAX_PERCENT


def takeover(svc: Any, groups: Any, account_id: str) -> dict[str, Any] | None:
    """The account that takes over from ``account_id`` at its limit: another
    account of the SAME group and the SAME vendor that is usable now
    (:func:`usable_now`), the lowest usage first (an account with no window in
    progress counts as none used), Iron-Proxy's order breaking a tie. Its row
    with ``group`` (the group's name) and ``percent`` (its peak, or None).
    None when the account is in no group, or nothing in its group can serve —
    then the reply waits for the reset instead. Cached reads only; never a
    call; never raises."""
    try:
        snap = snapshot(svc)
        found = group_of(groups, account_id)
        if snap is None or found is None:
            return None
        name, ids = found
        own = find(svc, account_id)
        if own is None:
            return None
        vendor = own["vendor"]
        now = datetime.now(timezone.utc)
        order = {r["id"]: i for i, r in enumerate(vendor_rows(snap, vendor, include_off=True))}
        best: tuple[tuple[float, int], dict[str, Any]] | None = None
        for pid in ids:
            if pid == account_id or pid not in order:
                continue
            row = find(svc, pid)
            if row is None or row.get("vendor") != vendor:
                continue  # a Codex account never takes over a Claude session
            state = _state_of(snap, pid)
            if not usable_now(row, state, now):
                continue
            peak = peak_percent(state, now)
            key = (peak if peak is not None else 0.0, order[pid])
            if best is None or key < best[0]:
                best = (key, {**row, "group": name, "percent": peak})
        return best[1] if best is not None else None
    except Exception:  # noqa: BLE001 — no takeover is the safe answer (the wait)
        log.debug("account group takeover lookup failed", exc_info=True)
        return None


def group_partners(svc: Any, groups: Any, account_id: str) -> str:
    """The group's name when ``account_id`` shares it with at least one other
    account of its own vendor (whatever their state now), else "". A
    session on such an account may move at its limit, now or later."""
    try:
        found = group_of(groups, account_id)
        own = find(svc, account_id) if found is not None else None
        if found is None or own is None:
            return ""
        for pid in found[1]:
            if pid == account_id:
                continue
            row = find(svc, pid)
            if row is not None and row.get("vendor") == own["vendor"]:
                return found[0]
    except Exception:  # noqa: BLE001 — no partner is the safe answer
        log.debug("account group partner lookup failed", exc_info=True)
    return ""


def group_problem(svc: Any, groups: Any, kept: Any = None) -> str | None:
    """Why ``groups`` cannot be saved, in words, or None: every account id it
    ADDS must be a Claude Code or Codex account Iron-Proxy lists now (the
    cached snapshot). An id already saved (``kept``) may stay even after its
    account was removed — a stale id never blocks a later save, and it can
    never take over (it is in no snapshot). BLOCKING-safe (cached reads)."""
    old: set[str] = set()
    if isinstance(kept, Mapping):
        for ids in kept.values():
            if isinstance(ids, (list, tuple)):
                old.update(str(i) for i in ids)
    new = [
        str(i) for ids in (groups.values() if isinstance(groups, Mapping) else [])
        if isinstance(ids, (list, tuple)) for i in ids if str(i) not in old
    ]
    if not new:
        return None
    if svc is None or not getattr(svc, "enabled", False):
        return GROUP_OFF
    if snapshot(svc) is None:
        return GROUP_NOT_READ
    for pid in new:
        if find(svc, pid) is None:
            return f"there is no Claude Code or Codex account “{pid}” in Iron-Proxy"
    return None


# --------------------------------------------------------------------------- #
# plans Iron-Proxy does not know yet
# --------------------------------------------------------------------------- #


def kick_plan_reads(svc: Any, rows: list[dict[str, Any]]) -> list[str]:
    """For each Claude account in ``rows`` whose plan Iron-Proxy does not know
    and that is serving, parked or ready (NEVER one needing sign-in: a
    re-check reads the local credentials and could flip it back — v1.303.3),
    SCHEDULE a background re-check (``request_refresh``) at most once per
    :data:`PLAN_READ_EVERY_S` per account. Iron-Proxy's re-check of a serving
    or parked account reads only its plan (``claude auth status``). Never
    waits, never raises. Returns the ids asked."""
    asked: list[str] = []
    if svc is None or not hasattr(svc, "request_refresh"):
        return asked
    now = time.monotonic()
    for row in rows:
        if row.get("vendor") != "claude" or row.get("plan"):
            continue
        if row.get("status") not in (READY, USAGE, LIMIT):
            continue
        pid = str(row.get("id") or "")
        with _PLAN_LOCK:
            if not pid or now - _PLAN_ASKED.get(pid, float("-inf")) < PLAN_READ_EVERY_S:
                continue
            _PLAN_ASKED[pid] = now
        try:
            if svc.request_refresh(pid) is not None:
                asked.append(pid)
        except Exception:  # noqa: BLE001 — a plan read never breaks a list
            log.debug("plan read for an account failed to start", exc_info=True)
    return asked


__all__ = [
    "DEFAULT_MODEL",
    "LIMIT",
    "MOVE_KINDS",
    "NEEDS_SIGN_IN",
    "READY",
    "TAKEOVER_MAX_PERCENT",
    "USAGE",
    "VENDORS",
    "accounts_view",
    "default_for",
    "find",
    "group_of",
    "group_partners",
    "group_problem",
    "kick_plan_reads",
    "peak_percent",
    "plan_label",
    "row_of",
    "session_account",
    "snapshot",
    "takeover",
    "usable_now",
    "vendor_of",
    "vendor_rows",
    "window_words",
]
