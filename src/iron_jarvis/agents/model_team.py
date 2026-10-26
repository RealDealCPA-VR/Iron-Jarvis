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


def member_account(platform: Any, record: Any, caller: Any = None, *,
                   coding: bool = False) -> "tuple[Any, str]":
    """``(AccountPin | None, refusal)`` — the account a member's run leases
    (v1.394.0 review; decision 4 + decision 5 + v1.393.0's groups):

    1. its OWN ``account`` when it names one (:func:`member_pin`);
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
        return own, ""
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

    def __init__(self, platform: Any, record: Any, provider: str = "") -> None:
        self.on = runs_elsewhere(record)
        self.provider = str(getattr(record, "provider", "") or "").strip()
        self.model = pinned_model(platform, record) if self.on else ""
        from ..iron_proxy.pin import current

        self.pin = None
        self.lift = False
        self.drop_pin = False
        if self.on:
            self.pin = member_account(platform, record, current(), coding=False)[0]
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
            self._hold = set_hold(self.provider, self.model)
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


# --------------------------------------------------------------------------- #
# the planner's team list in a coding session
# --------------------------------------------------------------------------- #

TEAM_HEADER = "# Your model team"
TEAM_LEAD = (
    "Helpers you can start by name. Each one works on ITS OWN model, named here — "
    "not this session's — so hand a piece to the member whose roles fit it "
    "(read = reading and summarizing, code = writing code, review = reviewing a change, "
    "test = running and fixing tests). Free and local members cost nothing; "
    "a member near its usage limit should get less."
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
