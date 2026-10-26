"""YOUR MODEL TEAM (v1.394.0, the long-jobs plan's Phase 5, decisions 5 and 6).

A TEAM MEMBER is a custom agent (``DynamicAgentRecord``) with one or more
ROLES. Nothing else about it is new: it is pinned to a model like any custom
agent (``provider`` / ``model``), its monthly allowance, its notes, its pause
and its Agents-page card all apply. What being a member adds:

* the planner's list of who can take the work (``roster.roster_block``) gives
  a member's line its MODEL, WHERE it runs (this PC / your network / cloud),
  its COST TYPE (free / your plan / pay per use), its roles and its usage
  (from the CACHED Iron-Proxy snapshot — never a vendor call);
* under Local only a member whose model leaves the network is NOT listed, and
  a request for one is refused in words (:func:`local_only_refusal`);
* in a coding session a member may be a helper on ITS OWN model
  (``harness/helpers.for_member``) — held to it (``providers.hold``), on its
  own account when it names one (:func:`member_pin`), else its provider's
  normal pick. Every other custom agent pinned elsewhere is still refused
  there (the v1.372.0 rule is unchanged for non-members).

The ROLE vocabulary is small and lives HERE (:data:`ROLES`); the dashboard's
twin is ``dashboard/lib/teamRoles.ts``, pinned equal by
``tests/test_model_team_v1394.py``.

**Make a starter team** (:func:`starter_preview` / :func:`create_starter`)
builds the preview from the model catalog the pickers read
(``routes/connections.selectable_models``): every CONNECTED local model as a
ticked row, Claude Code and Codex as UNTICKED rows ("add to the team"), and
pay-per-use APIs only when the user turns that switch on. Nothing is created
before the Create press, and nothing at all when no model is connected.

Every reader here is cheap and never raises (a fact it cannot read is absent,
never a guess): the roster calls it ON the loop inside prompt composition.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# the role vocabulary (ONE place; dashboard twin: lib/teamRoles.ts)
# --------------------------------------------------------------------------- #

#: ``(id, label)`` in the order a card lists them. Plain words; the id is what
#: is stored (``DynamicAgentRecord.roles_json``) and what the planner reads.
ROLES: tuple[tuple[str, str], ...] = (
    ("read", "Reading and summarizing"),
    ("code", "Writing code"),
    ("review", "Reviewing a change"),
    ("test", "Running and fixing tests"),
)
ROLE_IDS: tuple[str, ...] = tuple(r[0] for r in ROLES)
ROLE_LABELS: dict[str, str] = dict(ROLES)


def _role_list(raw: Any) -> list[str]:
    """Every entry of a list-ish value, lower-cased and trimmed. Never raises."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    try:
        for item in list(raw):
            text = " ".join(str(item or "").split()).lower()
            if text:
                out.append(text)
    except Exception:  # noqa: BLE001 — a bad list is an empty list
        return []
    return out


def clean_roles(raw: Any) -> list[str]:
    """The known roles in ``raw``, once each, in :data:`ROLES` order."""
    have = set(_role_list(raw))
    return [rid for rid in ROLE_IDS if rid in have]


def unknown_roles(raw: Any) -> list[str]:
    """Entries of ``raw`` that are not roles (for a route's refusal)."""
    out: list[str] = []
    for text in _role_list(raw):
        if text not in ROLE_IDS and text not in out:
            out.append(text)
    return out


def roles_of(record: Any) -> list[str]:
    """A record's stored roles (``roles_json``); garbage reads as none."""
    try:
        data = json.loads(getattr(record, "roles_json", None) or "[]")
    except (TypeError, ValueError):
        return []
    return clean_roles(data) if isinstance(data, list) else []


def is_member(record: Any) -> bool:
    """Is this custom agent on the model team (one or more roles)?"""
    return record is not None and bool(roles_of(record))


def role_words(roles: Iterable[str]) -> str:
    """``read, code`` — the ids, for the planner's one line."""
    return ", ".join(r for r in roles if r in ROLE_IDS)


# --------------------------------------------------------------------------- #
# the facts: place, cost type, usage
# --------------------------------------------------------------------------- #

#: egress.model_place's answers, in the words every surface uses (dashboard
#: twin: ``lib/modelTeam.ts`` PLACE_WORDS).
PLACE_WORDS: dict[str, str] = {"this_pc": "this PC", "network": "your network", "cloud": "cloud"}
#: The three cost types (dashboard twin: ``lib/modelTeam.ts`` COST_WORDS).
COST_FREE, COST_PLAN, COST_PAY = "free", "plan", "pay"
COST_WORDS: dict[str, str] = {COST_FREE: "free", COST_PLAN: "your plan", COST_PAY: "pay per use"}

#: Subscription CLIs: work on them is covered by the user's plan.
_PLAN_CLIS = frozenset({"claude-cli", "codex-cli", "grok-cli"})
#: A model id that means "the provider's own default".
_PLACEHOLDER_MODELS = frozenset({"", "subscription", "default", "auto"})
#: The vendor CLI each Iron-Proxy-backed provider is (for its account).
_CLI_LABELS = {"claude-cli": "Claude Code", "codex-cli": "Codex", "grok-cli": "Grok"}


def _manager(platform: Any) -> Any:
    return getattr(platform, "providers", None)


def _config(platform: Any) -> Any:
    return getattr(platform, "config", None)


def _served_cli(provider: str, manager: Any) -> str:
    """The CLI a provider is served by: itself, or (a keyless ``anthropic`` /
    ``openai``) the CLI the manager says serves it; "" otherwise."""
    p = (provider or "").strip().lower()
    if p in _PLAN_CLIS:
        return p
    if manager is None:
        return ""
    try:
        served = str(manager.inherited_from(p) or "")
    except Exception:  # noqa: BLE001
        return ""
    return served if served in _PLAN_CLIS else ""


def place_of(platform: Any, provider: str, model: str = "") -> str:
    """``this_pc`` / ``network`` / ``cloud`` — :func:`egress.model_place`, the
    local-only lock's own predicate. Never raises (cloud when unsure)."""
    from ..providers.egress import PLACE_CLOUD, model_place

    try:
        return model_place(provider, _manager(platform), _config(platform), model)
    except Exception:  # noqa: BLE001
        return PLACE_CLOUD


def cost_type(platform: Any, provider: str, model: str = "") -> str:
    """``free`` (it stays on this network), ``plan`` (a subscription CLI —
    covered by the user's plan) or ``pay`` (billed per use: an API key, a
    proxy name that goes to a cloud service, a Claude model that draws
    pay-as-you-go usage credits). Never raises (``pay`` when unsure: the
    honest side for money)."""
    from ..providers.egress import stays_on_this_network

    try:
        manager = _manager(platform)
        if stays_on_this_network(provider, manager, _config(platform), model):
            return COST_FREE
        cli = _served_cli(provider, manager)
        if cli:
            if cli == "claude-cli":
                from ..eval.pricing import draws_usage_credits

                if draws_usage_credits(model):
                    return COST_PAY
            return COST_PLAN
    except Exception:  # noqa: BLE001
        return COST_PAY
    return COST_PAY


def _svc(platform: Any) -> Any:
    return getattr(platform, "iron_proxy", None)


def _window_adjective(mins: Any, kind: Any = "") -> str:
    """``300`` -> "5-hour", ``10080`` -> "7-day" ("" when unknown)."""
    try:
        m = int(mins) if mins is not None and not isinstance(mins, bool) else None
    except (TypeError, ValueError):
        m = None
    if m is None:
        m = {"5h": 300, "7d": 10080, "1d": 1440, "24h": 1440}.get(str(kind or "").lower())
    if not m or m <= 0:
        return ""
    if m % 1440 == 0:
        return f"{m // 1440}-day"
    if m % 60 == 0:
        return f"{m // 60}-hour"
    return f"{m}-minute"


def usage_for(platform: Any, provider: str, account: str = "") -> "dict[str, Any] | None":
    """A plan member's usage, from Iron-Proxy's CACHED snapshot only:
    ``{account, account_name, status, percent?, window?, window_mins?,
    resets_at?}`` — the named ``account`` when one is set, else the account
    Iron-Proxy's normal pick would take now (the first one in its order that
    can serve). None when it is not known (Iron-Proxy off, not read yet, no
    account for the vendor, not a subscription provider). Never a vendor
    call; never raises."""
    try:
        from ..harness import build_accounts as BA

        svc = _svc(platform)
        vendor = BA.vendor_of(platform, provider)
        if not vendor:
            return None
        snap = BA.snapshot(svc)
        if snap is None:
            return None
        rows = BA.vendor_rows(snap, vendor)
        row = None
        if account:
            row = next((r for r in rows if r.get("id") == account), None)
            if row is None:
                return None
        else:
            row = next((r for r in rows if r.get("status") in (BA.READY, BA.USAGE)), None)
            if row is None:
                return None
        out: dict[str, Any] = {
            "account": str(row.get("id") or ""),
            "account_name": str(row.get("name") or ""),
            "status": str(row.get("status") or ""),
        }
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else None
        if usage and isinstance(usage.get("percent"), int):
            out["percent"] = usage["percent"]
            out["window"] = str(usage.get("window") or "")
            out["window_mins"] = usage.get("window_mins")
            out["resets_at"] = usage.get("resets_at")
        elif row.get("status") == BA.LIMIT:
            out["resets_at"] = row.get("resets_at")
        return out
    except Exception:  # noqa: BLE001 — usage is a bonus, never a liability
        log.debug("model team usage read failed", exc_info=True)
        return None


def usage_words(cost: str, usage: "dict[str, Any] | None") -> str:
    """The planner's (and the API's) one phrase for a member's usage: only a
    plan member has a window; never "0%" for an unknown."""
    if cost != COST_PLAN:
        return ""
    if not usage:
        return "usage not known yet"
    status = usage.get("status")
    if status == "limit":
        return "at its usage limit"
    if status == "needs_sign_in":
        return "needs sign-in"
    pct = usage.get("percent")
    if isinstance(pct, int):
        adj = _window_adjective(usage.get("window_mins"))
        return f"{pct}% of its {adj} window used" if adj else f"{pct}% used"
    return "usage not known yet"


def model_words(platform: Any, provider: str, model: str) -> str:
    """The model's name for the planner: its id, or — for a provider's own
    default — the provider's name ("Claude Code", "Codex", a node's label)."""
    from ..providers.egress import friendly_name

    p = (provider or "").strip()
    m = (model or "").strip()
    if m and m.lower() not in _PLACEHOLDER_MODELS:
        return m[:48]
    if p in _CLI_LABELS:
        return _CLI_LABELS[p]
    try:
        return friendly_name(p, _manager(platform)) or p
    except Exception:  # noqa: BLE001
        return p


def member_facts(platform: Any, record: Any) -> "dict[str, Any] | None":
    """What a member IS, for the planner and the Settings list: ``{roles,
    provider, model, place, cost, account, usage}`` (``provider`` "" = it runs
    on whoever hands it work's model). None for a non-member. Never raises."""
    roles = roles_of(record)
    if not roles:
        return None
    provider = str(getattr(record, "provider", "") or "").strip()
    model = str(getattr(record, "model", "") or "").strip()
    account = str(getattr(record, "account", "") or "").strip()
    facts: dict[str, Any] = {
        "roles": roles,
        "provider": provider,
        "model": model,
        "account": account,
        "place": "",
        "cost": "",
        "usage": None,
    }
    if not provider:
        return facts
    facts["place"] = place_of(platform, provider, model)
    facts["cost"] = cost_type(platform, provider, model)
    if facts["cost"] == COST_PLAN:
        # v1.394.0 review: the usage of the account it WOULD run on from here
        # (``member_account`` with this context's pin: in a coding session the
        # session's own account for the same vendor, or one of its group) —
        # not the first free account's, which it may never touch.
        on = account
        if not on:
            try:
                from ..iron_proxy.pin import current

                pin, _why = member_account(platform, record, current(), coding=False)
                on = str(getattr(pin, "profile_id", "") or "") if pin is not None else ""
            except Exception:  # noqa: BLE001
                on = ""
        facts["usage"] = usage_for(platform, provider, on)
    return facts


def facts_words(platform: Any, facts: dict[str, Any]) -> str:
    """``glm-5.3-flash, this PC, free; roles: read, code`` — the planner's
    bracket for one member. Never clamped by the roster (these are what the
    planner chooses by)."""
    parts: list[str] = []
    provider = str(facts.get("provider") or "")
    if provider:
        parts.append(model_words(platform, provider, str(facts.get("model") or "")))
        place = PLACE_WORDS.get(str(facts.get("place") or ""), "")
        if place:
            parts.append(place)
        cost = str(facts.get("cost") or "")
        if cost in COST_WORDS:
            parts.append(COST_WORDS[cost])
        used = usage_words(cost, facts.get("usage"))
        if used:
            parts.append(used)
    else:
        parts.append("the model of whoever hands it work")
    roles = role_words(facts.get("roles") or [])
    return ", ".join(parts) + (f"; roles: {roles}" if roles else "")


def leaves_network(platform: Any, record: Any) -> bool:
    """Does this custom agent's OWN model leave the user's network? False for
    an agent with no pinned provider (it runs on its caller's model, which a
    Local only turn already keeps local). Never raises (True when unsure)."""
    provider = str(getattr(record, "provider", "") or "").strip()
    if not provider:
        return False
    from ..providers.egress import stays_on_this_network

    try:
        return not stays_on_this_network(
            provider, _manager(platform), _config(platform),
            str(getattr(record, "model", "") or ""),
        )
    except Exception:  # noqa: BLE001 — fail closed
        return True


def local_only_refusal(platform: Any, record: Any, name: str) -> str:
    """The sentence a Local only turn or run refuses a helper with when its
    own model leaves the network; "" when it does not. Before any session
    exists — the router would refuse its first call anyway, but a run that
    starts and dies is worse than one plain sentence."""
    if record is None or not leaves_network(platform, record):
        return ""
    who = str(name or getattr(record, "name", "") or "that agent")
    provider = str(getattr(record, "provider", "") or "")
    model = str(getattr(record, "model", "") or "")
    return (
        f"'{who}' runs on {model_words(platform, provider, model)}, which is not on this PC "
        "or your network, and this work is Local only — nothing leaves your network. "
        "Hand it to a team member that runs locally, or do it yourself."
    )


def member_pin(platform: Any, record: Any) -> Any:
    """The Iron-Proxy :class:`~iron_jarvis.iron_proxy.pin.AccountPin` of a
    member's OWN ``account`` when it names one (that account or a plain
    refusal at lease — never another), else None. Never raises. Which account
    a member with none runs on is :func:`member_account`'s rule."""
    account = str(getattr(record, "account", "") or "").strip()
    if not account:
        return None
    try:
        from ..harness import build_accounts as BA
        from ..iron_proxy.pin import AccountPin

        row = BA.find(_svc(platform), account)
        if row is not None:
            return AccountPin(profile_id=account, ij_provider=row["provider"], title=row["name"])
        vendor = BA.vendor_of(platform, str(getattr(record, "provider", "") or ""))
        if vendor:
            return AccountPin(profile_id=account, ij_provider=BA.VENDORS[vendor]["provider"])
    except Exception:  # noqa: BLE001 — no pin is the caller's to refuse at lease
        log.debug("model team member pin failed", exc_info=True)
    return None


def _group_words(name: str) -> str:
    return f"'{name}'"


def _other_group(platform: Any, record: Any, own: Any, caller: Any) -> str:
    """v1.394.0 review (decision 4: work and personal accounts never mix):
    the sentence when a member's OWN account sits in ANOTHER account group
    than the account the calling work runs on; "" otherwise — including when
    either account is in no group (the user made no statement about it).
    Never raises."""
    if caller is None or own is None:
        return ""
    try:
        from ..harness import build_accounts as BA

        groups = getattr(_config(platform), "account_groups", None) or {}
        mine = BA.group_of(groups, str(getattr(own, "profile_id", "") or ""))
        theirs = BA.group_of(groups, str(getattr(caller, "profile_id", "") or ""))
        if mine is None or theirs is None or mine[0] == theirs[0]:
            return ""
        who = str(getattr(record, "name", "") or "This member")
        title = str(getattr(own, "title", "") or "")
        acct = f"“{title}”" if title else "an account of its own"
        return (
            f"'{who}' runs on {acct}, which is in your {_group_words(mine[0])} account group, "
            f"and this work runs on an account of your {_group_words(theirs[0])} group — work never "
            "moves between groups. Set its account to “No account of its own” in Settings › "
            f"Model team (it then takes an account of the {_group_words(theirs[0])} group), or "
            "change your groups in Settings › Connections."
        )
    except Exception:  # noqa: BLE001 — unsure: no refusal (its own account, as set)
        return ""


def member_account(platform: Any, record: Any, caller: Any = None, *,
                   coding: bool = False) -> "tuple[Any, str]":
    """``(AccountPin | None, refusal)`` — the account a member's run leases
    (v1.394.0 review; decision 4 + decision 5 + v1.393.0's groups):

    1. its OWN ``account`` when it names one (:func:`member_pin`) — refused
       in words when that account sits in ANOTHER account group than
       ``caller``'s (both grouped: work and personal never mix);
    2. not a subscription model (a local model, an API key): no account;
    3. ``caller`` (the session's / the calling run's pin) is of the SAME
       vendor: THAT pin object — the session's own account (a helper's limit
       is the session's limit; v1.372.0's rule for every helper);
    4. ``caller`` sits in one of the user's ACCOUNT GROUPS: an account of the
       member's vendor IN THAT GROUP (usable now first, else the first one on,
       Iron-Proxy's order) — none in the group = ``refusal`` in words: work
       never moves to an account outside a group the user made;
    5. a coding session (``coding``) or any pinned caller: the vendor's first
       usable account, else its Default — PINNED (a coding session never runs
       "first free, rotating": ``routes/build.turn_account``);
    6. otherwise (an objective, an agent run, nothing pinned): None — its
       provider's normal pick, as every unpinned agent run.

    Cached reads only; never a call; never raises (no pin on an error)."""
    own = member_pin(platform, record)
    if own is not None:
        why = _other_group(platform, record, own, caller)
        return (None, why) if why else (own, "")
    try:
        from ..harness import build_accounts as BA
        from ..iron_proxy.pin import AccountPin

        vendor = BA.vendor_of(platform, str(getattr(record, "provider", "") or ""))
        if not vendor:
            return None, ""
        provider = BA.VENDORS[vendor]["provider"]
        if caller is not None and str(getattr(caller, "ij_provider", "") or "") == provider:
            return caller, ""
        if caller is None and not coding:
            return None, ""
        svc = _svc(platform)
        snap = BA.snapshot(svc)
        if snap is None:
            return None, ""
        every = BA.vendor_rows(snap, vendor, include_off=True)
        states = snap[1]
        groups = getattr(_config(platform), "account_groups", None) or {}
        found = BA.group_of(groups, str(getattr(caller, "profile_id", "") or "")) if caller else None
        if found is not None:
            gname, ids = found
            every = [r for r in every if r["id"] in ids]
            if not every:
                who = str(getattr(record, "name", "") or "This member")
                label = BA.VENDORS[vendor]["label"]
                return None, (
                    f"'{who}' runs on {label}, and the account this work runs on is in your "
                    f"{_group_words(gname)} account group, which has no {label} account — add one "
                    "to the group in Settings › Connections, or set the member's own account in "
                    "Settings › Model team."
                )
        if not every:
            return None, ""
        on = [r for r in every if r.get("status") != BA.OFF]
        row = next((r for r in on if BA.usable_now(r, states.get(r["id"]))), None)
        if row is None:
            # Nothing usable now: the Default (or the first one on) — its
            # lease then says why in words (a limit, a sign-in), never moves.
            row = next((r for r in on if r.get("default")), on[0] if on else every[0])
        return AccountPin(profile_id=row["id"], ij_provider=row["provider"], title=row["name"]), ""
    except Exception:  # noqa: BLE001 — no pin (a lease refuses honestly, never moves)
        log.debug("model team member account failed", exc_info=True)
        return None, ""


def account_refusal(platform: Any, record: Any) -> str:
    """The sentence a door refuses a member with, BEFORE any session exists,
    when the account it would run on (:func:`member_account`, from the pin of
    the context it is called in) cannot be one the user's groups allow; ""
    otherwise. Never raises."""
    if not runs_elsewhere(record):
        return ""
    try:
        from ..iron_proxy.pin import current

        return member_account(platform, record, current(), coding=False)[1]
    except Exception:  # noqa: BLE001
        return ""


def runs_elsewhere(record: Any) -> bool:
    """A member with a model of its own (a pinned provider) — the members a
    coding session may run on THEIR model instead of the session's."""
    return is_member(record) and bool(str(getattr(record, "provider", "") or "").strip())


def team_members(platform: Any) -> list[Any]:
    """Every custom agent with one or more roles, by name. Never raises."""
    registry = getattr(platform, "agents_registry", None)
    if registry is None:
        return []
    try:
        return [r for r in registry.list() if is_member(r)]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# the hold a member's run is created under (both doors, both contexts)
# --------------------------------------------------------------------------- #


#: A keyword default that is not None (None is a real pin: "no account").
_UNSET: Any = object()

#: v1.394.0 review: True inside a MEMBER's run that :class:`member_scope`
#: held (outside a coding session). That hold is the member's OWN — a teammate
#: the member consults or hands work to steps out of it (it runs as it would
#: for any caller); a coding session's hold is never lifted.
MEMBER_RUN: ContextVar[bool] = ContextVar("ij_member_run", default=False)


class member_scope:  # noqa: N801 — a context manager read like a function
    """The context a run TASK is created in (or a consult is asked in),
    outside a coding session (a mission, a plain agent run):

    * a MEMBER with a model of its own: its model HELD (``providers.hold`` —
      no fallback, no failover, no capability reroute) and the account
      :func:`member_account` names (its own; else the caller's when of its
      vendor; else none — its provider's normal pick);
    * v1.394.0 review: anyone else, INSIDE a member's run (:data:`MEMBER_RUN`):
      out of that member's hold, and off its account pin unless the pin is of
      the target's vendor (``provider``: the target's own, or the provider it
      inherits) — before, a member's teammate on another model was refused
      by the member's hold, in a coding session's words;
    * otherwise nothing. Inside a coding session ``harness.helpers.pinned``
      does this with the session's other rules."""

    def __init__(self, platform: Any, record: Any, provider: str = "", *, pin: Any = _UNSET) -> None:
        self.on = runs_elsewhere(record)
        self.provider = str(getattr(record, "provider", "") or "").strip()
        self.model = pinned_model(platform, record) if self.on else ""
        #: v1.395.0: the hold's refusal names the MEMBER, not a Build session.
        self.who = f"member:{getattr(record, 'name', '')}" if self.on else ""
        from ..iron_proxy.pin import current

        self.pin = None
        self.lift = False
        self.drop_pin = False
        if self.on:
            # v1.395.0: the account the TEAM ROUTER chose for a role's piece
            # (``RolePick.pin``) when it chose one; else ``member_account``'s.
            self.pin = pin if pin is not _UNSET else member_account(platform, record, current(), coding=False)[0]
        elif MEMBER_RUN.get():
            self.lift = True
            pin = current()
            if pin is not None:
                try:
                    from ..harness import build_accounts as BA

                    vendor = BA.vendor_of(platform, str(provider or "").strip())
                    keep = bool(vendor) and BA.VENDORS[vendor]["provider"] == getattr(pin, "ij_provider", "")
                except Exception:  # noqa: BLE001 — unsure: no pin (a lease never moves)
                    keep = False
                self.drop_pin = not keep
        self._hold = None
        self._pin = None
        self._flag = None

    def __enter__(self) -> "member_scope":
        if not (self.on or self.lift):
            return self
        from ..iron_proxy.pin import set_pin
        from ..providers.hold import set_hold

        if self.on:
            self._pin = set_pin(self.pin)
            self._hold = set_hold(self.provider, self.model, self.who)
            self._flag = MEMBER_RUN.set(True)
        else:
            if self.drop_pin:
                self._pin = set_pin(None)
            from ..providers.hold import PROVIDER_HOLD

            self._hold = PROVIDER_HOLD.set(None)
            self._flag = MEMBER_RUN.set(False)
        return self

    def __exit__(self, *exc: Any) -> None:
        if not (self.on or self.lift):
            return
        from ..iron_proxy.pin import reset_pin
        from ..providers.hold import reset_hold

        if self._flag is not None:
            try:
                MEMBER_RUN.reset(self._flag)
            except (ValueError, LookupError):
                MEMBER_RUN.set(False)
        if self._hold is not None:
            reset_hold(self._hold)
        if self._pin is not None:
            reset_pin(self._pin)


async def run_as_member(platform: Any, record: Any, make: Any) -> Any:
    """Await a member's run INSIDE :class:`member_scope`, entered in the
    run's own task (v1.395.0, the Phase 5 review's open item): an ASSIGNMENT
    and a SCHEDULE FIRE start a run that may be parked by the session
    governor and promoted later in a fresh context — a scope entered around
    the task's creation would be gone by then. Entered here, the member's
    model is held (no fallback, no failover) and its account pinned for the
    whole run, whenever it starts. A non-member's run is awaited as it is.

    ``make`` builds the run's coroutine INSIDE the scope (a zero-argument
    callable), so a run the governor refuses before it starts leaves no
    never-awaited coroutine behind."""
    with member_scope(platform, record):
        return await make()


def queued_refusal(platform: Any, record: Any, *, provider: str = "", model: str = "",
                   local_only: bool = False) -> str:
    """The sentence a QUEUED or SCHEDULED piece of work for a member is held
    or refused with BEFORE any session row exists (v1.395.0 — the doors'
    rules, at the doors that start work by themselves): its account must be
    one the user's groups allow (:func:`account_refusal`); Local only work
    never starts a member whose model leaves the network; and a task that
    names ANOTHER provider or model than the member runs on is said, never
    silently swapped (a member runs on its own model). "" when it may run.
    Never raises."""
    if not runs_elsewhere(record):
        return ""
    name = str(getattr(record, "name", "") or "")
    if local_only:
        lo = local_only_refusal(platform, record, name)
        if lo:
            return lo
    own_provider = str(getattr(record, "provider", "") or "").strip()
    asked_provider = str(provider or "").strip()
    asked_model = str(model or "").strip()
    own_model = pinned_model(platform, record)
    if (asked_provider and asked_provider != own_provider) or (
            asked_model and own_model and asked_model != own_model):
        asked = "/".join(p for p in (asked_provider, asked_model) if p)
        return (
            f"'{name}' is on your model team and runs on its own model "
            f"({model_words(platform, own_provider, own_model)}), but this task names {asked}. "
            "Remove the task's model, or change the member's model in Settings › Model team."
        )
    return account_refusal(platform, record)


# --------------------------------------------------------------------------- #
# the planner's team list in a coding session
# --------------------------------------------------------------------------- #

TEAM_HEADER = "# Your model team"
TEAM_LEAD = (
    "Helpers you can start by name. Each one works on ITS OWN model, named here — "
    "not this session's — so hand a piece to the member whose roles fit it "
    "(read = reading and summarizing, code = writing code, review = reviewing a change, "
    "test = running and fixing tests). Free and local members cost nothing; "
    "a member near its usage limit should get less. Or ask for a ROLE instead of a "
    "name — role:read, role:code, role:review or role:test as the helper's type — and "
    "Iron Jarvis picks the member: a local one first when its record for that role is "
    "good, and no cloud account near its usage limit gets new work."
)


def team_block(platform: Any, *, limit: int = 10) -> str:
    """The team section of a coding session's prompt: the roster's member
    lines only ("" when there are none, so every other turn's prompt is
    byte-identical). Under Local only a member whose model leaves the
    network is not listed (``roster_block`` does the filtering)."""
    names = [f"custom:{r.name}" for r in team_members(platform)]
    if not names:
        return ""
    from .roster import roster_block

    block = roster_block(platform, limit=limit, only=names)
    if not block:
        return ""
    lines = block.split("\n")
    # The roster's own header is replaced by the team's (same lines below).
    body = [ln for ln in lines[1:] if ln.startswith("- ")]
    if not body:
        return ""
    return "\n".join([TEAM_HEADER, TEAM_LEAD, *body])


# --------------------------------------------------------------------------- #
# Make a starter team
# --------------------------------------------------------------------------- #

GROUP_LOCAL, GROUP_PLAN, GROUP_PAY = "local", "subscription", "paid"

#: The roles a starter row is offered with (decision 6: local models for
#: reading, summarizing and simple coding).
STARTER_ROLES: dict[str, list[str]] = {
    GROUP_LOCAL: ["read", "code"],
    "claude-cli": ["code", "review"],
    "codex-cli": ["code", "test"],
    GROUP_PAY: ["code", "review"],
}

#: v1.394.0 review: how many LOCAL rows the preview ticks by default (the
#: first ones in the catalog's order). A fleet can list dozens of local
#: models; every one is still OFFERED, but a press of Create must not make a
#: team of thirty — the planner's team list shows ten.
STARTER_TICKED_LOCAL = 3

NOTHING_CONNECTED = (
    "No model is connected yet, so there is no one to put on the team. Connect a "
    "local model, Claude Code or Codex in Settings › Connections first."
)
STARTER_PROMPT = (
    "You are a member of the user's model team. You are handed one piece of a larger "
    "piece of work: do exactly that piece, in the folder you are given, and report back "
    "plainly what you did, what you found and anything you could not finish."
)

_NAME_RX = re.compile(r"[^a-z0-9._-]+")


def _slug(text: str) -> str:
    s = _NAME_RX.sub("-", str(text or "").strip().lower()).strip("-._")
    s = re.sub(r"-{2,}", "-", s)
    return s[:40].strip("-._")


def _taken_names(platform: Any) -> set[str]:
    from .types import _DEFINITIONS

    out = {t.value.casefold() for t in _DEFINITIONS}
    registry = getattr(platform, "agents_registry", None)
    try:
        for r in registry.list() if registry is not None else []:
            out.add(str(r.name).casefold())
    except Exception:  # noqa: BLE001
        pass
    return out


def _unique(base: str, taken: set[str]) -> str:
    name = base or "member"
    n = 2
    while name.casefold() in taken:
        name = f"{base}-{n}"
        n += 1
    taken.add(name.casefold())
    return name


def _row_key(provider: str, model: str) -> str:
    return f"{provider}|{model}"


def starter_preview(platform: Any, catalog: list[dict[str, Any]], *, paid: bool = False) -> dict[str, Any]:
    """The preview **Make a starter team** shows — NOTHING is created here.

    ``catalog`` is the pickers' model catalog (``selectable_models``). Rows:

    * every AVAILABLE model that stays on this network (``on_this_network``)
      — ticked, roles read + code, free;
    * Claude Code and Codex (one row each, their own default model) when
      connected — UNTICKED ("add to the team"), your plan;
    * pay-per-use models — only with ``paid``, one row per provider (its
      first model in the catalog), unticked.

    A row whose model a member already runs on says so (``exists``) and is
    unticked. ``note`` says why the list is empty."""
    manager = _manager(platform)
    existing: dict[str, str] = {}
    for r in team_members(platform):
        key = _row_key(str(r.provider or ""), str(r.model or ""))
        existing.setdefault(key, r.name)
    taken = _taken_names(platform)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    plan_done: set[str] = set()
    pay_done: set[str] = set()
    paid_available = 0
    ticked_local = 0
    try:
        from ..fleet.picker import looks_like_embedding
    except Exception:  # noqa: BLE001
        looks_like_embedding = lambda *_a: False  # noqa: E731
    for m in catalog or []:
        try:
            if not m.get("available"):
                continue
            provider = str(m.get("provider") or "").strip()
            model = str(m.get("model") or "").strip()
            if not provider or provider == "mock":
                continue
            key = _row_key(provider, model)
            if key in seen:
                continue
            # v1.394.0 review: an EMBEDDING model cannot chat (Ollama lists
            # every model it holds, this app's own embedder included).
            if looks_like_embedding(model):
                continue
            local = bool(m.get("on_this_network"))
            cli = _served_cli(provider, manager)
            if local:
                group = GROUP_LOCAL
                roles = list(STARTER_ROLES[GROUP_LOCAL])
                ticked = ticked_local < STARTER_TICKED_LOCAL and key not in existing
                if ticked:
                    ticked_local += 1
                base = _slug(model) or _slug(provider)
                description = "Reads, summarizes and writes simple code on a model of your own"
            elif cli in ("claude-cli", "codex-cli") and provider == cli:
                if cli in plan_done:
                    continue
                plan_done.add(cli)
                group = GROUP_PLAN
                roles = list(STARTER_ROLES[cli])
                ticked = False
                model = "subscription"
                key = _row_key(provider, model)
                base = "claude-code" if cli == "claude-cli" else "codex"
                description = (
                    "Writes and reviews code on your Claude Code plan"
                    if cli == "claude-cli"
                    else "Writes code and runs tests on your Codex plan"
                )
            elif cli:
                # A keyless API name served by a CLI (or Grok's CLI): the
                # CLI's own row stands for it.
                continue
            else:
                if provider in pay_done:
                    continue
                pay_done.add(provider)
                paid_available += 1
                if not paid:
                    continue
                group = GROUP_PAY
                roles = list(STARTER_ROLES[GROUP_PAY])
                ticked = False
                base = _slug(f"{provider}-{model}") or _slug(provider)
                description = "Writes and reviews code, billed per use"
            seen.add(key)
            exists = existing.get(key)
            row = {
                "key": key,
                "provider": provider,
                "model": model,
                "group": group,
                "roles": roles,
                "place": place_of(platform, provider, model),
                "cost": cost_type(platform, provider, model),
                "ticked": bool(ticked and not exists),
                "exists": exists or None,
                "description": description,
            }
            if m.get("name"):
                row["label"] = str(m.get("name"))
            row["name"] = _unique(base, taken) if not exists else exists
            rows.append(row)
        except Exception:  # noqa: BLE001 — one odd row, keep the rest
            continue
    order = {GROUP_LOCAL: 0, GROUP_PLAN: 1, GROUP_PAY: 2}
    rows.sort(key=lambda r: order.get(r["group"], 3))
    note = ""
    if not rows:
        note = NOTHING_CONNECTED if not paid_available else (
            "Only pay-per-use models are connected. Turn on 'Include pay-per-use models' "
            "to add one; each use is billed."
        )
    return {"rows": rows, "paid": bool(paid), "paid_available": paid_available, "note": note}


class StarterRefused(ValueError):
    """A starter-team create the route answers with a plain sentence."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def create_starter(platform: Any, preview: dict[str, Any], picks: list[dict[str, Any]]) -> dict[str, Any]:
    """Create the members the user ticked, from THIS preview (rebuilt by the
    caller at press time — a row the preview no longer offers, such as a model
    that went away or a pay-per-use one with the switch off, is skipped with
    its reason). Returns ``{created: [names], skipped: [{key, reason}]}``.
    Raises :class:`StarterRefused` when nothing could be created at all."""
    offered = {r["key"]: r for r in preview.get("rows") or []}
    if not offered:
        raise StarterRefused(preview.get("note") or NOTHING_CONNECTED, status=409)
    if not picks:
        raise StarterRefused("Tick at least one model to put on the team.")
    registry = getattr(platform, "agents_registry", None)
    if registry is None:
        raise StarterRefused("Custom agents are not available right now.", status=503)
    taken = _taken_names(platform)
    created: list[str] = []
    skipped: list[dict[str, str]] = []
    for pick in picks:
        key = str((pick or {}).get("key") or "")
        row = offered.get(key)
        if row is None:
            skipped.append({"key": key, "reason": "That model is not offered for the team right now."})
            continue
        if row.get("exists"):
            skipped.append({"key": key, "reason": f"{row['exists']} already runs on that model."})
            continue
        name = " ".join(str(pick.get("name") or row.get("name") or "").split())[:60]
        if not name:
            skipped.append({"key": key, "reason": "Give the member a name."})
            continue
        if name.casefold() in taken and name != row.get("name"):
            skipped.append({"key": key, "reason": f"{name} is already taken."})
            continue
        if registry.get(name) is not None:
            skipped.append({"key": key, "reason": f"{name} is already taken."})
            continue
        roles = clean_roles(pick.get("roles")) if pick.get("roles") is not None else list(row["roles"])
        if not roles:
            skipped.append({"key": key, "reason": "Pick at least one role for it."})
            continue
        registry.register(
            name,
            STARTER_PROMPT,
            [],
            base_type="builder",
            description=str(row.get("description") or ""),
            provider=row["provider"],
            model=row["model"],
            roles=roles,
        )
        taken.add(name.casefold())
        created.append(name)
    if not created:
        raise StarterRefused(
            "Nothing was added: " + " ".join(s["reason"] for s in skipped[:3]), status=409
        )
    return {"created": created, "skipped": skipped}


__all__ = [
    "COST_FREE", "COST_PAY", "COST_PLAN", "COST_WORDS", "MEMBER_RUN", "NOTHING_CONNECTED", "PLACE_WORDS",
    "ROLES", "ROLE_IDS", "ROLE_LABELS", "STARTER_PROMPT", "STARTER_TICKED_LOCAL", "StarterRefused",
    "TEAM_HEADER",
    "clean_roles", "cost_type", "create_starter", "facts_words", "is_member",
    "account_refusal", "leaves_network", "local_only_refusal", "member_account", "member_facts",
    "member_pin", "member_scope",
    "model_words", "place_of", "roles_of", "runs_elsewhere", "starter_preview", "team_block",
    "team_members", "unknown_roles", "usage_for", "usage_words",
]


def inherits_tools(record: Any) -> bool:
    """Does this custom agent store NO tool list (it inherits its base
    type's)? A team member that does is given, in a coding session, the tools
    its base type would get there (``harness/helpers.helper_definition``)."""
    try:
        data = json.loads(getattr(record, "tools_json", None) or "[]")
    except (TypeError, ValueError):
        return False
    return isinstance(data, list) and not data


def helper_is_custom(record: Any) -> bool:
    """The ``custom`` flag a door hands ``helpers.helper_definition``: True for
    a custom agent, except a team member that inherits its base type's tools
    (it works like that built-in in a coding session)."""
    if record is None:
        return False
    return not (is_member(record) and inherits_tools(record))


def pinned_model(platform: Any, record: Any) -> str:
    """The model a custom agent PINNED TO A PROVIDER runs: its own, else that
    provider's own default (``egress.default_model_of`` — the adapter's) —
    never the caller's model id, which belongs to another provider (and
    never ``create_session``'s configured default model, which belongs to the
    configured default provider only). "" when it pins no provider or the
    default cannot be read. Never raises."""
    provider = str(getattr(record, "provider", "") or "").strip()
    model = str(getattr(record, "model", "") or "").strip()
    if not provider or model:
        return model
    try:
        from ..providers.egress import default_model_of

        return default_model_of(provider, _manager(platform)) or ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# v1.395.0 (the long-jobs plan's Phase 6, decisions 7 and 8): THE TEAM ROUTER
# --------------------------------------------------------------------------- #
#
# The planner may name a ROLE instead of a member ("read these files", "write
# this module", "review this change") and Iron Jarvis picks the member —
# :func:`route_role`, the ONE function that decides (the doors only call it).
#
# THE FILTERS, in order: Local only (a member whose model leaves the network
# is not even considered); a paused or allowance-exhausted member is out; a
# coordinator (it hands work out itself) is out; the member's provider must be
# THERE (cached answers only — ``cli_auth.never_probe``: routing never starts
# a ``claude auth status`` or any probe); the usage thresholds from the CACHED
# Iron-Proxy snapshot (no NEW work on an account at
# ``build_accounts.NEW_WORK_MAX_PERCENT`` 80% of any window, which also keeps
# everything under the ``TAKEOVER_MAX_PERCENT`` 95% floor; an account with no
# numbers is "not known" and tried after the known-low ones — the v1.393.0
# rule; work already running is never stopped); a pay-per-use member only
# under the monthly cap (``Config.team_paid_cap_usd``, default 0 = never).
#
# THE ORDER: LOCAL members whose record for the role is good or not yet known
# (and whose box is not busy) first — good before unknown; then subscription
# members, lowest current usage first, unknown after; then pay-per-use; then,
# as a last resort, a local member that is busy or whose record is not good
# (the work goes local before it waits). The record is ``agents/team_record``
# (the finish check and the reviewer, not just "it finished"); the
# completion-only local oracle (``platform._local_oracle``) is NOT read here,
# and chat's Auto is untouched.
#
# A PIECE that failed twice on one member goes to the next member up — at most
# one step per piece (only the FIRST member it failed twice on is passed over),
# and never across Local only (the order holds local members only there).
#
# Every pick carries ONE plain sentence of why ("local: good record for
# reading", "Codex: the local box is busy", "skipped Claude Code (TSI): 83% of
# its 5-hour window"); a pick that finds no one answers in plain words naming
# what was skipped and the earliest reset — nothing starts on an account past
# the threshold, and the helper's row says it waited.

#: ``role:<id>`` in an agent name asks for a role (the dialects' own helper
#: tools carry no ``role`` argument — Claude Code's ``Agent`` and Codex's
#: ``spawn_agent`` take ``subagent_type: "role:code"``).
ROLE_PREFIX = "role:"

#: The role as a noun, in the reasons ("good record for reading").
ROLE_NOUNS: dict[str, str] = {"read": "reading", "code": "coding", "review": "reviewing", "test": "testing"}

_ROLE_WORDS: dict[str, str] = {
    **{rid: rid for rid in ROLE_IDS},
    **{label.lower(): rid for rid, label in ROLES},
    **{noun: rid for rid, noun in ROLE_NOUNS.items()},
    "reader": "read", "summarize": "read", "summarizing": "read", "summary": "read",
    "coder": "code", "write": "code", "write code": "code", "writing code": "code",
    "reviewer": "review", "tests": "test", "tester": "test", "run tests": "test",
}


def role_id(raw: Any) -> str:
    """The role id ``raw`` asks for ("read", "reading", "role:read",
    "Reading and summarizing" → ``read``), or "" when it names no role."""
    text = " ".join(str(raw or "").split()).lower()
    if text.startswith(ROLE_PREFIX):
        text = text[len(ROLE_PREFIX):].strip()
    return _ROLE_WORDS.get(text, "")


def role_named(name: Any) -> "str | None":
    """The role text after ``role:`` in an agent name, else None (a member or
    a helper type is named)."""
    text = " ".join(str(name or "").split())
    if text.lower().startswith(ROLE_PREFIX):
        return text[len(ROLE_PREFIX):].strip()
    return None


def unknown_role_refusal(raw: Any) -> str:
    """ONE sentence for a role the team does not have."""
    shown = " ".join(str(raw or "").split())[:60] or "that"
    return (
        f"'{shown}' is not a team role — ask for read, code, review or test, or name the "
        "member you want."
    )


def piece_key(piece: Any, task: Any) -> str:
    """The key a PIECE of work is known by across its tries: the planner's
    ``piece`` name when it gave one, else its task — lower-cased, spaces
    folded, at most 200 characters."""
    text = str(piece or "").strip() or str(task or "")
    return " ".join(text.lower().split())[:200]


@dataclass
class RolePick:
    """What :func:`route_role` decided for one piece."""

    role: str
    #: The member (a ``DynamicAgentRecord``), or None — then ``refusal``.
    record: Any = None
    #: ONE plain sentence of why (for the helper row and its Timeline row).
    reason: str = ""
    #: When no member can take it: the plain words the planner reads.
    refusal: str = ""
    #: The account the router chose for a subscription member (an
    #: ``AccountPin``) — used instead of ``member_account``'s when ``pin_set``.
    pin: Any = None
    pin_set: bool = False
    #: True when nothing started because every cloud account in reach is
    #: near its limit (the row says it WAITED).
    waited: bool = False


@dataclass
class _Acct:
    """One subscription member's account, as the thresholds read it."""

    usable: bool = True
    peak: "float | None" = None
    pin: Any = None
    pin_set: bool = False
    name: str = ""
    #: What was skipped, in words — ``(words, at a usage limit)`` — and when
    #: those accounts reset (ISO instants).
    skips: list = field(default_factory=list)
    resets: list = field(default_factory=list)
    limited: bool = False


@dataclass
class _Cand:
    record: Any
    tier: int
    key: tuple
    label: str
    local: bool
    reason: str = ""
    #: A local last resort: why it is not first; a pay member: its spend.
    note: str = ""
    acct: "_Acct | None" = None


def _reset_words(iso: Any) -> str:
    """"3:40 PM" (another day: "Tue 9:00 AM") for an ISO instant, else ""."""
    if not iso:
        return ""
    try:
        from ..iron_proxy.accounts import _local_time

        return _local_time(str(iso))[0]
    except Exception:  # noqa: BLE001
        return ""


def provider_ready(platform: Any, provider: str) -> "bool | None":
    """Is ``provider`` there right now — from CACHED answers only (a CLI's
    sign-in verdict is read, never refreshed: ``cli_auth.never_probe``)?
    None when it cannot be told. Never raises."""
    manager = _manager(platform)
    if manager is None or not provider:
        return None
    try:
        from ..providers.cli_auth import cached_verdicts, never_probe

        with cached_verdicts(), never_probe():
            return bool(manager.available(provider))
    except Exception:  # noqa: BLE001
        return None


def local_box(platform: Any, provider: str) -> str:
    """``busy`` when the fleet's latest snapshot of the box this LOCAL
    provider runs on shows requests WAITING, else ``ok`` (an unreachable box
    is told by :func:`provider_ready`, which reads the fleet's reachability
    for a fleet node). A cached read of the sampler's last pass; never a
    request, never raises."""
    try:
        from ..fleet.registry import FleetRegistry

        node = FleetRegistry._node_id_for(provider)
        sampler = getattr(platform, "fleet_sampler", None)
        if not node or sampler is None:
            return "ok"
        snap = sampler.latest(node)
        metrics = getattr(snap, "metrics", None) if snap is not None else None
        waiting = getattr(metrics, "requests_waiting", None) if metrics is not None else None
        if isinstance(waiting, (int, float)) and not isinstance(waiting, bool) and waiting > 0:
            return "busy"
    except Exception:  # noqa: BLE001
        log.debug("model team local box read failed", exc_info=True)
    return "ok"


def paid_spend_this_month(platform: Any, now: Any = None) -> float:
    """What the PAY-PER-USE members of the team spent this calendar month, in
    METERED dollars only (the allowance's reading, ``agents/allowance``: each
    run's own recorded cost — a subscription's list-price equivalent never
    counts). BLOCKING; 0.0 when it cannot be read."""
    engine = getattr(platform, "engine", None)
    names = []
    for r in team_members(platform):
        provider = str(getattr(r, "provider", "") or "").strip()
        if provider and cost_type(platform, provider, pinned_model(platform, r)) == COST_PAY:
            names.append(f"custom:{r.name}")
    if engine is None or not names:
        return 0.0
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import Session
        from ..eval.pricing import is_list_price_equivalent, recorded_cost
        from .allowance import _month_bounds, _naive

        start, end = _month_bounds(now)
        total = 0.0
        with session_scope(engine) as db:
            for row in db.exec(select(Session).where(Session.agent_name.in_(names))):  # type: ignore[attr-defined]
                created = _naive(getattr(row, "created_at", None))
                if created is None or not (start <= created < end):
                    continue
                provider = str(getattr(row, "provider", "") or "")
                model = str(getattr(row, "model", "") or "")
                if is_list_price_equivalent(provider, model):
                    continue
                total += float(recorded_cost(
                    provider, model, int(row.input_tokens or 0), int(row.output_tokens or 0),
                    getattr(row, "cost_usd", 0.0),
                ) or 0.0)
        return total
    except Exception:  # noqa: BLE001
        log.debug("model team paid spend read failed", exc_info=True)
        return 0.0


def _coordinator(platform: Any, record: Any) -> bool:
    """Does this member hand work out itself (it would be refused as a
    target — the anti-fork-bomb rule)? Never raises (False when unsure)."""
    if str(getattr(record, "base_type", "") or "").strip().casefold() == "supervisor":
        return True
    registry = getattr(platform, "agents_registry", None)
    try:
        definition = registry.definition(record.name) if registry is not None else None
    except Exception:  # noqa: BLE001
        definition = None
    tools = list(getattr(definition, "tools", None) or []) if definition is not None else []
    return "delegate" in tools or "spawn_agent" in tools


def _account_check(label: str, row: dict, state: Any, now: Any) -> "tuple[bool, float | None, str, str | None, bool]":
    """``(usable for new work, peak, why-words, resets_at, at a limit)`` of
    one account, from the cached snapshot."""
    from ..harness import build_accounts as BA

    name = f"{label} ({row.get('name')})" if row.get("name") else label
    status = row.get("status")
    if status == BA.OFF:
        return False, None, f"skipped {name}: it is turned off", None, False
    if status == BA.NEEDS_SIGN_IN:
        return False, None, f"skipped {name}: it needs to sign in again", None, False
    if status == BA.LIMIT:
        when = _reset_words(row.get("resets_at"))
        return (False, None, f"skipped {name}: at its limit" + (f" until {when}" if when else ""),
                row.get("resets_at"), True)
    peak = BA.peak_percent(state, now)
    if peak is not None and peak >= min(BA.NEW_WORK_MAX_PERCENT, BA.TAKEOVER_MAX_PERCENT):
        usage = BA.peak_usage(state, now) or {}
        adj = _window_adjective(usage.get("window_mins"))
        where = f"of its {adj} window" if adj else "of a usage window"
        return False, peak, f"skipped {name}: {int(round(peak))}% {where}", usage.get("resets_at"), True
    return True, peak, "", None, False


def _plan_account(platform: Any, record: Any, provider: str, caller: Any, now: Any) -> _Acct:
    """The account a SUBSCRIPTION member's piece would run on, by
    :func:`member_account`'s rules (its own account; the caller's when of its
    vendor; the caller's account GROUP; else the vendor's accounts) — and,
    where there is a choice, the one with the LOWEST known usage below
    ``NEW_WORK_MAX_PERCENT`` (an account with no numbers after the known-low
    ones, Iron-Proxy's order breaking a tie), PINNED. Cached reads only;
    never raises ("not known" when unsure — the lease then decides)."""
    from ..harness import build_accounts as BA
    from ..iron_proxy.pin import AccountPin

    try:
        svc = _svc(platform)
        vendor = BA.vendor_of(platform, provider)
        if not vendor:
            return _Acct()
        label = BA.VENDORS[vendor]["label"]
        snap = BA.snapshot(svc)
        own = member_pin(platform, record)
        rows: list[tuple[dict, Any]] = []
        if own is not None:
            if _other_group(platform, record, own, caller):
                return _Acct(usable=False, skips=[
                    (f"skipped {record.name}: its own account is in another account group than this work's",
                     False)])
            row = BA.find(svc, own.profile_id) if snap is not None else None
            if row is None:
                return _Acct(pin=own, pin_set=True, name=str(getattr(own, "title", "") or ""))
            rows = [(row, own)]
        elif caller is not None and str(getattr(caller, "ij_provider", "") or "") == BA.VENDORS[vendor]["provider"]:
            row = BA.find(svc, str(getattr(caller, "profile_id", "") or "")) if snap is not None else None
            if row is None:
                return _Acct(pin=caller, pin_set=True, name=str(getattr(caller, "title", "") or ""))
            rows = [(row, caller)]
        else:
            if snap is None:
                return _Acct()  # no numbers at all: "not known" — member_account decides
            every = BA.vendor_rows(snap, vendor)
            groups = getattr(_config(platform), "account_groups", None) or {}
            found = BA.group_of(groups, str(getattr(caller, "profile_id", "") or "")) if caller else None
            if found is not None:
                gname, ids = found
                every = [r for r in every if r["id"] in ids]
                if not every:
                    return _Acct(usable=False, skips=[
                        (f"skipped {record.name}: your '{gname}' account group has no {label} account", False)])
            rows = [(r, AccountPin(profile_id=r["id"], ij_provider=r["provider"], title=r["name"]))
                    for r in every]
            if not rows:
                return _Acct(usable=False, skips=[(f"skipped {record.name}: Iron-Proxy lists no {label} account",
                                                   False)])
        out = _Acct(usable=False)
        best: "tuple[tuple, dict, Any, float | None] | None" = None
        for order, (row, pin) in enumerate(rows):
            state = snap[1].get(row["id"]) if snap is not None else None
            usable, peak, why, resets, limited = _account_check(label, row, state, now)
            if not usable:
                if why and (why, limited) not in out.skips:
                    out.skips.append((why, limited))
                if resets:
                    out.resets.append(resets)
                out.limited = out.limited or limited
                continue
            key = (peak is None, peak if peak is not None else 0.0, order)
            if best is None or key < best[0]:
                best = (key, row, pin, peak)
        if best is None:
            return out
        _key, row, pin, peak = best
        # The accounts passed over stay on the answer: the reason names them
        # ("skipped Claude Code (TSI): 83% of its 5-hour window").
        return _Acct(usable=True, peak=peak, pin=pin, pin_set=True, name=str(row.get("name") or ""),
                     skips=out.skips)
    except Exception:  # noqa: BLE001 — unsure: "not known" (the lease decides, never moves)
        log.debug("model team account read failed", exc_info=True)
        return _Acct()


def _piece_passed_over(engine: Any, scope: str, role: str, piece: str) -> str:
    """The member a piece is passed over on: the FIRST (in the order its
    members took it) that it failed ``team_record.PIECE_FAILS`` times on —
    at most one per piece, so it moves up one step and no further. ""
    otherwise."""
    from . import team_record as TR

    for member, fails in TR.piece_failures(engine, scope, role, piece):
        if fails >= TR.PIECE_FAILS:
            return member
    return ""


def route_role(platform: Any, role: str, *, local_only: bool = False, caller_pin: Any = None,
               caller_provider: str = "", caller_model: str = "", scope: str = "",
               piece: str = "", now: Any = None, team: "list[str] | None" = None) -> RolePick:
    """THE TEAM ROUTER: the member that takes a piece of ``role`` now, and
    why (see the section comment above). ``team`` — a PROJECT mission's team
    (roster names, ``agents/team.mission_team``): only its members are asked
    (the doors refuse anyone else). BLOCKING (SQLite reads: the record, the
    allowance, the month's spend) — the doors call it off the loop. Never
    raises (an internal fault is a refusal in words)."""
    noun = ROLE_NOUNS.get(role, role)
    try:
        return _route(platform, role, noun, local_only=local_only, caller_pin=caller_pin,
                      caller_provider=caller_provider, caller_model=caller_model, scope=scope,
                      piece=piece, now=now, team=list(team or []))
    except Exception:  # noqa: BLE001
        log.warning("model team routing failed", exc_info=True)
        return RolePick(role, refusal=(
            f"Iron Jarvis could not pick a team member for {noun} just now — name the member "
            "you want, or do this piece yourself."), reason=f"not started: no member picked for {noun}")


def _route(platform: Any, role: str, noun: str, *, local_only: bool, caller_pin: Any,
           caller_provider: str, caller_model: str, scope: str, piece: str, now: Any,
           team: "list[str] | None" = None) -> RolePick:
    from datetime import datetime, timezone

    from . import allowance
    from . import team_record as TR

    now = now or datetime.now(timezone.utc)
    engine = getattr(platform, "engine", None)
    config = _config(platform)
    default_provider = str(getattr(config, "default_provider", "") or "")
    members = [r for r in team_members(platform) if role in roles_of(r)]
    if team:
        # A PROJECT mission: only the project's team (the user picked it).
        from . import team as _team

        members = [r for r in members if _team.on_team(team, f"custom:{r.name}")]
        if not members:
            return RolePick(role, reason=f"not started: no one on this project's team holds {noun}",
                            refusal=(f"No one on this project's team holds {noun} — hand this piece to a "
                                     f"team member by name ({', '.join(team)}), or give a member of the "
                                     "team that role in Settings › Model team."))
    hidden = 0
    if local_only:
        kept = [r for r in members if not leaves_network(platform, r)]
        hidden = len(members) - len(kept)
        members = kept
    if not members:
        if hidden:
            return RolePick(role, reason=f"not started: no member on this network holds {noun}", refusal=(
                f"No team member on this PC or your network holds {noun}, and this work is Local only "
                "— nothing leaves your network. Do this piece yourself, or give a local member that "
                "role in Settings › Model team."))
        return RolePick(role, reason=f"not started: no team member holds {noun}", refusal=(
            f"No one on your model team holds {noun} yet. Give a member that role in Settings › "
            "Model team, or hand the piece to a helper by name."))
    records = TR.records_for(engine, [r.name for r in members], role)
    passed_over = _piece_passed_over(engine, scope, role, piece) if piece else ""
    cands: list[_Cand] = []
    skipped: list[str] = []
    #: the accounts passed over for their USAGE (at or over the threshold, or
    #: at their limit) — named in a cloud pick's reason
    limit_skips: list[str] = []
    local_notes: list[str] = []
    resets: list[str] = []
    limited = False
    spend: "float | None" = None
    try:
        cap = max(0.0, float(getattr(config, "team_paid_cap_usd", 0.0) or 0.0))
    except (TypeError, ValueError):
        cap = 0.0
    for idx, r in enumerate(members):
        own_provider = str(getattr(r, "provider", "") or "").strip()
        provider = own_provider or caller_provider or default_provider
        model = pinned_model(platform, r) if own_provider else str(caller_model or "")
        cost = cost_type(platform, provider, model)
        local = cost == COST_FREE
        label = model_words(platform, provider, model)
        if _coordinator(platform, r):
            skipped.append(f"skipped {r.name} (it hands work out itself)")
            continue
        if allowance.refusal_for(r, engine):
            paused = bool(allowance.paused_sentence(r))
            skipped.append(f"skipped {r.name} ({'it is paused' if paused else 'it used its monthly allowance'})")
            if local:
                local_notes.append("the local member is paused" if paused
                                   else "the local member used its monthly allowance")
            continue
        if provider_ready(platform, provider) is False:
            if local:
                skipped.append(f"skipped {r.name} (its box is not answering)")
                local_notes.append("the local box is not answering")
            else:
                skipped.append(f"skipped {r.name} ({label} is not connected)")
            continue
        rr = records.get(r.name, {}).get(role) or TR.RoleRecord()
        if local:
            box = local_box(platform, provider)
            ok_record = rr.status in (TR.GOOD, TR.UNKNOWN)
            if box == "ok" and ok_record:
                good = rr.status == TR.GOOD
                if good:
                    reason = f"local: good record for {noun}"
                elif rr.runs == 0:
                    reason = f"local: no record yet for {noun}"
                else:
                    reason = f"local: only {rr.runs} {'piece' if rr.runs == 1 else 'pieces'} of {noun} so far"
                cands.append(_Cand(r, 0, (0, 0 if good else 1, -rr.rate, idx), label, True, reason))
                continue
            if box != "ok":
                note = "the local box is busy"
            elif rr.streak >= TR.FAIL_STREAK:
                note = f"the local member failed {noun} {rr.streak} times in a row"
            else:
                note = f"the local member's record for {noun} is not good ({rr.passed} of {rr.runs} passed)"
            local_notes.append(note)
            fall = "its box is busy" if box != "ok" else f"its record is {rr.words()}"
            cands.append(_Cand(r, 3, (3, 0 if ok_record else 1, 1 if box != "ok" else 0, idx), label,
                               True, note=fall))
        elif cost == COST_PLAN:
            acct = _plan_account(platform, r, provider, caller_pin, now)
            for s, at_limit in acct.skips:
                if s not in skipped:
                    skipped.append(s)
                if at_limit and s not in limit_skips:
                    limit_skips.append(s)
            if not acct.usable:
                resets.extend(acct.resets)
                limited = limited or acct.limited
                continue
            shown = f"{label} ({acct.name})" if acct.name else label
            cands.append(_Cand(r, 1, (1, acct.peak is None, acct.peak or 0.0, idx), shown, False, acct=acct))
        else:
            if cap <= 0:
                skipped.append(f"skipped {r.name} (pay per use is off: your monthly cap is $0)")
                continue
            if spend is None:
                spend = paid_spend_this_month(platform, now)
            if spend >= cap:
                skipped.append(f"skipped {r.name} (this month's pay-per-use spend, ${spend:.2f}, "
                               f"reached your ${cap:.2f} cap)")
                continue
            cands.append(_Cand(r, 2, (2, 0, 0.0, idx), label, False,
                               note=f"pay per use: ${spend:.2f} of your ${cap:.2f} monthly cap used"))
    cands.sort(key=lambda c: c.key)
    moved_from = ""
    if passed_over and any(c.record.name == passed_over for c in cands):
        rest = [c for c in cands if c.record.name != passed_over]
        if rest:
            moved_from = passed_over
            cands = rest
        else:
            only = cands[0]
            stay = (f"this work is Local only and no other member on this PC or your network holds {noun}"
                    if local_only else f"nothing else can take {noun} now")
            head = "local" if only.local else only.label
            return _picked(role, only, f"{head}: this piece failed its check twice here, but {stay}")
    if not cands:
        when = _reset_words(_earliest(resets))
        listed = "; ".join(skipped[:6]) or "nothing on the team can take it"
        lo = " (this work is Local only, so only members on this PC or your network were asked)" if local_only else ""
        refusal = (f"No team member can take {noun} now{lo}: {listed}."
                   + (f" The earliest reset is {when}." if when else "")
                   + " Nothing was started on an account near its limit — do this piece yourself, "
                   "or hand it out again later.")
        if limited:
            reason = (f"waited: every cloud account that can take {noun} is near its limit"
                      + (f"; the earliest reset is {when}" if when else ""))
        else:
            reason = f"not started: no team member can take {noun} now"
        return RolePick(role, reason=reason, refusal=refusal, waited=limited)
    top = cands[0]
    if moved_from:
        why_not = f"this piece failed its check twice on {moved_from}, so it moved up one"
    elif local_notes:
        why_not = local_notes[0]
    else:
        why_not = f"no local member holds {noun}"
    if top.tier == 0:
        reason = f"local: {why_not}" if moved_from else top.reason
    elif top.tier in (1, 2):
        parts = [why_not]
        if top.tier == 2 and top.note:
            parts.append(top.note)
        parts.extend(limit_skips[:2])
        reason = f"{top.label}: " + "; ".join(parts)
    else:
        if moved_from:
            head = why_not
        elif limited:
            head = f"every cloud account that can take {noun} is near its limit"
        else:
            head = f"nothing else can take {noun} now"
        reason = f"local: {head}; {top.note}" if top.note else f"local: {head}"
    return _picked(role, top, reason)


def _earliest(resets: list) -> str:
    """The EARLIEST of some ISO instants (parsed — never compared as text),
    as it was given; "" when none parses."""
    from ..harness.build_accounts import _parse

    best: "tuple[Any, str] | None" = None
    for iso in resets:
        when = _parse(iso)
        if when is not None and (best is None or when < best[0]):
            best = (when, str(iso))
    return best[1] if best is not None else ""


def _picked(role: str, cand: _Cand, reason: str) -> RolePick:
    acct = cand.acct
    return RolePick(role, record=cand.record, reason=reason,
                    pin=acct.pin if acct is not None else None,
                    pin_set=bool(acct is not None and acct.pin_set))


def route_for_call(platform: Any, *, role: str, piece: str, session_id: str,
                   helper_parent: Any = None, team: "list[str] | None" = None) -> RolePick:
    """:func:`route_role` for one ``delegate`` / ``spawn_agent`` call: the
    caller's account pin, model and Local only read from where the call runs
    — a coding session's turn (``helper_parent``) or the calling run's own
    row. BLOCKING (the doors call it via ``asyncio.to_thread``, which carries
    the context: the pin and the Local only scope)."""
    from ..iron_proxy.pin import current

    caller_provider = caller_model = ""
    if helper_parent is not None:
        pin = getattr(helper_parent, "pin", None)
        caller_provider = str(getattr(helper_parent, "provider", "") or "")
        caller_model = str(getattr(helper_parent, "model", "") or "")
        local_only = bool(getattr(helper_parent, "local_only", False))
    else:
        pin = current()
        try:
            from ..providers.egress import current_scope

            local_only = bool(current_scope().on)
        except Exception:  # noqa: BLE001
            local_only = False
        try:
            from ..core.db import session_scope
            from ..core.models import Session

            with session_scope(platform.engine) as db:
                row = db.get(Session, session_id)
                if row is not None:
                    caller_provider = str(row.provider or "")
                    caller_model = str(row.model or "")
        except Exception:  # noqa: BLE001
            pass
    return route_role(platform, role, local_only=local_only, caller_pin=pin,
                      caller_provider=caller_provider, caller_model=caller_model,
                      scope=str(session_id or ""), piece=piece, team=team)


async def note_piece(platform: Any, *, record: Any, role: str, session_id: str, scope: str,
                     piece: str, finished: bool, report: Any = "") -> None:
    """The door's record of one settled piece (``agents/team_record``), off
    the loop: its row; and, for a ``review`` piece, the reviewer's verdict on
    the work it reviewed. Only for a member that HOLDS ``role``. Never
    raises."""
    import asyncio

    from . import team_record as TR

    if record is None or not role or role not in roles_of(record):
        return
    try:
        row = await asyncio.to_thread(
            TR.note_run, platform.engine, member=record.name, role=role, session_id=session_id,
            scope=scope, piece=piece, finished=finished,
        )
        if role == "review" and finished and row:
            verdict = TR.verdict_of(report)
            if verdict:
                await asyncio.to_thread(TR.credit_review, platform.engine, scope=scope,
                                        review_row=row, verdict=verdict)
    except Exception:  # noqa: BLE001 — a record never breaks a delegation
        log.debug("model team piece record failed", exc_info=True)


def review_task(role: str, task: str) -> str:
    """A ``review`` piece's task with the verdict line asked for
    (``team_record.VERDICT_ASK``); any other piece's unchanged."""
    if role != "review":
        return task
    from .team_record import VERDICT_ASK

    return f"{str(task or '').rstrip()}\n\n{VERDICT_ASK}"


__all__ += [
    "ROLE_NOUNS", "ROLE_PREFIX", "RolePick", "local_box", "note_piece", "paid_spend_this_month",
    "piece_key", "provider_ready", "queued_refusal", "review_task", "role_id", "role_named",
    "route_for_call", "route_role", "run_as_member", "unknown_role_refusal",
]
