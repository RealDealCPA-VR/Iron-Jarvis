"""Which subscription account a Build pane's CLIs run as (v1.302.0).

A vendor CLI's account is its HOME variable at process start
(``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` / ``GROK_HOME``). A running interactive
CLI cannot change account, so the account is chosen WHEN A PANE STARTS (its
shell's environment) and switching = a pane started on another account. Nothing
is ever typed into an existing shell to change it.

Iron-Proxy is the ACCOUNT MANAGER (CLAUDE.md v1.301.0): this module only asks it
WHICH account and applies the answer exactly like ``iron_proxy.accounts.Lease``
— ONLY the provider's home variable is set and Iron-Proxy's ``unset`` list
(minus PATH & co.) is removed (``accounts._lease_of`` builds the lease, so the
rules are not forked here). The terminals manager merges it into the shell's
environment through ``_with_pane_env`` (start from the base, then the account,
then the pane's identity).

A request maps an Iron-Proxy provider (``anthropic`` / ``openai`` / ``xai``) to
``"<profile id>"`` or ``"default"`` (this PC's own login):

* explicit id -> THAT account (``pick(provider, profile_id=id)``), or a
  :class:`PaneAccountRefused` with one plain sentence (parked / needs sign-in /
  missing / turned off / Iron-Proxy off, not answering or too old). Never
  silently a different account.
* ``"default"`` -> no home variable; an INHERITED one is removed, so the pane
  really is this PC's login.
* key absent -> Iron-Proxy ON: its first free CLI account for that provider,
  chosen from the CACHED account snapshot (order + state; the account's own
  home) with NO network call — a plain "+" pane never waits on Iron-Proxy —
  recorded as ``source: "iron-proxy"``; no account for that provider ->
  nothing recorded, nothing changed. Every account parked / signed out, or no
  snapshot yet (Iron-Proxy still starting or not answering) -> this PC's login,
  recorded as ``source: "default"`` with a ``note`` the pane chip shows.
  Iron-Proxy OFF -> nothing at all (byte-identical to v1.301.0).

A pane strips ONLY its account's own provider's API-key variables
(:data:`PANE_OWN_KEYS`) — never another provider's: a Build pane is the user's
own shell and keeps the user's own keys (CLAUDE.md v1.217.0). An account whose
home IS the CLI's own default home sets no home variable at all
(``accounts.is_default_home``).

Restoring a pane after a daemon restart NEVER WAITS ON IRON-PROXY (boot speed):
an account's home is a stable folder (Iron-Proxy's ``cli-homes/<provider>/<id>``
or an adopted login's own folder), so the snapshot records the home VALUE the
shell was started with (and the unset names) and the restore applies it again
directly (:func:`from_recorded`). The pane's chip then learns the account's
state from the cached snapshot once the post-boot check (or the watch loop) has
read Iron-Proxy: gone / turned off -> a note that the pane still runs on that
account's folder; parked -> the normal parked state. Only a snapshot written
before homes were recorded is re-resolved by id (:func:`resolve` with
``restoring=True``, bounded by the manager); that never raises, and an account
that cannot be used comes back on this PC's login with a ``note`` — never on
another account.

Everything here BLOCKS (loopback HTTP): callers are sync routes on the
threadpool, ``asyncio.to_thread``, or the boot rehydrate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from ..iron_proxy import accounts as _acc
from ..iron_proxy.service import NOT_ANSWERING, PICK_PROFILE_FEATURE

log = logging.getLogger(__name__)

#: The Iron-Proxy providers a Build pane can carry an account for, in the
#: order a pane records them.
PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "xai")

#: Iron-Proxy provider -> how it maps onto the vendor CLI (home var, label).
SPECS: dict[str, _acc.ProviderAccounts] = {
    spec.proxy_provider: spec for spec in _acc.PROVIDERS.values()
}

#: Launch-catalog CLI id -> the Iron-Proxy provider whose accounts it runs as.
CLI_PROVIDER: dict[str, str] = {"claude": "anthropic", "codex": "openai", "grok": "xai"}
#: ...and back: the CLI an account of that provider opens in Build.
PROVIDER_CLI: dict[str, str] = {p: c for c, p in CLI_PROVIDER.items()}

#: The value that means "this PC's own login" in a request.
DEFAULT = "default"
#: The title a pane on this PC's login shows ("Claude · this PC's login").
DEFAULT_TITLE = "this PC's login"

#: How long a pane waits for an Iron-Proxy that is busy starting.
LEASE_TIMEOUT_S = 10.0

#: The only variables a PANE on an account removes besides the home: that
#: provider's OWN API-key names (so the CLI uses the subscription). Iron-Proxy's
#: unset list strips every provider's keys — right for one CLI child of a chat
#: call, wrong for a shell the user also runs everything else in.
PANE_OWN_KEYS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}),
    "openai": frozenset({"OPENAI_API_KEY"}),
    "xai": frozenset({"XAI_API_KEY", "GROK_API_KEY"}),
}

_CONNECTIONS = _acc._CONNECTIONS
_OFF = "Iron-Proxy is off; turn it on in Connections first."
#: The note on a pane that asked for no account while Iron-Proxy was on and
#: not answering (the shared contract's words).
NOT_ANSWERING_NOTE = "Iron-Proxy was not answering — this pane uses this PC's login."
#: ...and while Iron-Proxy is on but its account snapshot is not read yet.
STARTING_NOTE = "Iron-Proxy is not answering yet (starting or stopped) — this pane uses this PC's login."
_TOO_OLD = (
    "The Iron-Proxy running on this PC is too old to open a pane on a chosen account"
)


class PaneAccountRefused(Exception):
    """A pane cannot start on the account it asked for. ``sentence`` is the one
    plain sentence for the user (the route's ``detail``); ``status`` the HTTP
    status (409 = the account cannot be used now, 400 = it never could)."""

    def __init__(self, sentence: str, status: int = 409, *, what: str = "") -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status
        #: The bare clause (no "what to do" tail) — a restore builds its own.
        self.what = what or sentence.rstrip(".")


@dataclass
class PaneAccounts:
    """The accounts one pane runs as: what is recorded on the pane and how its
    shell's environment changes."""

    #: provider -> ``{"id", "title", "source"[, "note"]}`` (``id`` None = this
    #: PC's own login).
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The account leases applied to the environment (``Lease.apply``).
    leases: list[_acc.Lease] = field(default_factory=list)
    #: Home variables removed so the pane really is this PC's login.
    remove: list[str] = field(default_factory=list)

    def use_default(self, provider: str, note: str | None = None) -> None:
        spec = SPECS[provider]
        rec: dict[str, Any] = {"id": None, "title": DEFAULT_TITLE, "source": "default"}
        if note:
            rec["note"] = note
        self.records[provider] = rec
        if spec.home_var not in self.remove:
            self.remove.append(spec.home_var)

    def use_lease(self, provider: str, lease: _acc.Lease) -> None:
        spec = SPECS[provider]
        own = PANE_OWN_KEYS.get(provider, frozenset()) | {spec.home_var}
        # A PANE keeps every other provider's keys (the user's own shell).
        lease = replace(
            lease, env_unset=tuple(k for k in lease.env_unset if k.upper() in own)
        )
        self.records[provider] = {
            "id": lease.profile_id,
            "title": lease.title,
            "source": "iron-proxy",
            # What the shell was STARTED with — recorded so a restore can apply
            # it again without asking Iron-Proxy (a folder path, not a secret).
            # Never part of a pane row (``ROW_KEYS``).
            "home": lease.home,
            "unset": [k for k in lease.env_unset if k.upper() != spec.home_var],
        }
        self.leases.append(lease)

    def merge(self, other: "PaneAccounts") -> None:
        self.records.update(other.records)
        self.leases.extend(other.leases)
        self.remove.extend(v for v in other.remove if v not in self.remove)

    def apply(self, env: dict[str, str]) -> dict[str, str]:
        """``env`` with these accounts applied. Mutates and returns ``env``.

        Home variables are matched case-insensitively (Windows keeps one
        variable per name whatever its case), then each lease applies exactly
        as ``Lease.apply`` does: its ``unset`` list first, then its home."""
        drop = {v.upper() for v in self.remove}
        drop |= {SPECS[lease.provider].home_var for lease in self.leases if lease.provider in SPECS}
        for key in [k for k in env if k.upper() in drop]:
            env.pop(key, None)
        for lease in self.leases:
            lease.apply(env)
        return env

    def __bool__(self) -> bool:
        return bool(self.records)


#: The keys of a recorded account a pane ROW carries (``home``/``unset`` stay
#: in the daemon and the snapshot).
ROW_KEYS = ("id", "title", "source", "note")


def from_recorded(
    recorded: Mapping[str, Any],
) -> tuple[PaneAccounts, dict[str, str], dict[str, str]]:
    """A restored pane's accounts from its SNAPSHOT alone — no Iron-Proxy call.

    Returns ``(accounts, legacy, titles)``: ``accounts`` = every recorded
    account whose home was recorded (applied exactly as at spawn: that home,
    the recorded unset names minus PATH & co.) and every "this PC's login";
    ``legacy`` = ``{provider: id}`` for accounts recorded before homes were
    (re-resolved by id, bounded, by the caller); ``titles`` for the notes."""
    out = PaneAccounts()
    legacy: dict[str, str] = {}
    titles: dict[str, str] = {}
    for provider, rec in (recorded or {}).items():
        spec = SPECS.get(provider)
        if spec is None or not isinstance(rec, Mapping):
            continue
        pid = str(rec.get("id") or "").strip()
        title = str(rec.get("title") or pid)
        if title:
            titles[provider] = title
        if not pid:
            out.use_default(provider)
            continue
        home = rec.get("home")
        if not isinstance(home, str) or not home.strip():
            legacy[provider] = pid
            continue
        raw_unset = rec.get("unset") if isinstance(rec.get("unset"), list) else []
        out.use_lease(provider, _acc.make_lease(spec, pid, title, home, raw_unset))
    return out, legacy, titles


def late_note(title: str) -> str:
    """A legacy restore that Iron-Proxy could not answer within the budget."""
    return (
        "Iron-Proxy did not answer in time when this pane came back, so it uses this "
        f'PC\'s login instead of "{title}" — open a new pane on that account from Launch.'
    )


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
def normalise_request(request: Mapping[str, Any] | None) -> dict[str, str]:
    """``{provider: id | "default"}`` with blank values dropped (= absent).
    Raises :class:`PaneAccountRefused` (400) for a provider Build has no
    account for."""
    out: dict[str, str] = {}
    for key, value in (request or {}).items():
        provider = str(key or "").strip().lower()
        if provider not in SPECS:
            raise PaneAccountRefused(
                f'A Build pane has no account for "{key}" — use anthropic, openai or xai.',
                400,
            )
        text = str(value or "").strip()
        if text:
            out[provider] = DEFAULT if text.lower() == DEFAULT else text
    return out


# --------------------------------------------------------------------------- #
# Words
# --------------------------------------------------------------------------- #
def _what_parked(kind: str) -> str:
    return _acc._WHAT.get(kind, "parked")


def _until_words(iso: str) -> str:
    words, _secs = _acc._local_time(iso) if iso else ("", None)
    return f" until {words}" if words else ""


def _refuse_signin(spec: _acc.ProviderAccounts, title: str) -> PaneAccountRefused:
    what = f'The {spec.label} account "{title}" needs to sign in again'
    return PaneAccountRefused(f"{what} — sign it in on {_CONNECTIONS}, then try again.", what=what)


def _refuse_parked(
    spec: _acc.ProviderAccounts, title: str, kind: str, until: str
) -> PaneAccountRefused:
    what = f'The {spec.label} account "{title}" is {_what_parked(kind)}{_until_words(until)}'
    return PaneAccountRefused(
        f"{what} — pick another account, or this PC's login, from Launch.", what=what
    )


def _refuse_missing(spec: _acc.ProviderAccounts, title: str) -> PaneAccountRefused:
    what = f'The {spec.label} account "{title}" no longer exists in Iron-Proxy'
    return PaneAccountRefused(f"{what} — check the account list on {_CONNECTIONS}.", what=what)


def _refuse_disabled(spec: _acc.ProviderAccounts, title: str) -> PaneAccountRefused:
    what = f'The {spec.label} account "{title}" is turned off on the Iron-Proxy card'
    return PaneAccountRefused(f"{what} — turn it on on the Connections page first.", what=what)


def _refuse_too_old() -> PaneAccountRefused:
    return PaneAccountRefused(
        f"{_TOO_OLD} — update Iron-Proxy (a pane that does not choose an account still "
        "gets its first free one).",
        what=_TOO_OLD,
    )


def _refuse_unlent(spec: _acc.ProviderAccounts, title: str, said: str) -> PaneAccountRefused:
    what = f'Iron-Proxy would not lend the {spec.label} account "{title}" right now'
    if said:
        what += f" ({said.rstrip('.')})"
    return PaneAccountRefused(
        f"{what} — try again, or pick another account from Launch.", what=what
    )


def _said(exc: BaseException) -> str:
    text = str(getattr(exc, "sentence", "") or getattr(exc, "message", "") or exc or "")
    try:
        return _acc.plain_hint(text).strip()
    except Exception:  # noqa: BLE001 — a rewrite never loses the sentence
        return text.strip()


def _unavailable_sentence(exc: BaseException) -> str:
    if str(getattr(exc, "code", "") or "") == "UNREACHABLE":
        # A dropped connection: the service's plain sentence, not a URL.
        return NOT_ANSWERING
    said = _said(exc) or "Iron-Proxy is on but not answering."
    return said if said.endswith((".", "!", "?")) else said + "."


# --------------------------------------------------------------------------- #
# One explicit account
# --------------------------------------------------------------------------- #
def _parked_now(state: Mapping[str, Any]) -> bool:
    if state.get("status") != "parked":
        return False
    until = str(state.get("parkedUntil") or "")
    if not until:
        return True
    try:
        when = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when > datetime.now(timezone.utc)


def _reason_kind(state: Mapping[str, Any]) -> str:
    reason = state.get("parkedReason")
    return str(reason.get("kind") or "") if isinstance(reason, Mapping) else ""


def _pick_explicit(
    client: Any,
    spec: _acc.ProviderAccounts,
    pid: str,
    profiles: list[dict[str, Any]],
    states: Mapping[str, Any],
    title_hint: str = "",
) -> _acc.Lease:
    """THAT account's lease, or :class:`PaneAccountRefused`. The fresh account
    read is checked FIRST, so the sentence is by kind whatever an Iron-Proxy's
    pick does with a pinned account (one lets a signed-out pinned account
    through); then Iron-Proxy's own answer is checked to be that account."""
    prof = next((p for p in profiles if p.get("id") == pid), None)
    if prof is None:
        raise _refuse_missing(spec, title_hint or pid)
    title = str(prof.get("title") or pid)
    if prof.get("provider") != spec.proxy_provider:
        other = SPECS.get(str(prof.get("provider")))
        kind = other.label if other else str(prof.get("provider"))
        raise PaneAccountRefused(f'"{title}" is a {kind} account, not a {spec.label} one.', 400)
    if (prof.get("lane") or "cli") != "cli":
        raise PaneAccountRefused(
            f'"{title}" is an API-key account; a Build pane runs a subscription (CLI) account.',
            400,
        )
    if prof.get("enabled") is False:
        raise _refuse_disabled(spec, title)
    st = states.get(pid) if isinstance(states.get(pid), Mapping) else {}
    if st.get("status") == "unauthenticated" or _reason_kind(st) == "auth-expired":
        raise _refuse_signin(spec, title)
    if st.get("status") == "disabled":
        raise _refuse_disabled(spec, title)
    if _parked_now(st):
        kind = _acc._account_kind(dict(st))
        raise _refuse_parked(spec, title, kind, str(st.get("parkedUntil") or ""))
    try:
        answer = client.pick(spec.proxy_provider, lane="cli", profile_id=pid)
    except Exception as exc:  # noqa: BLE001 — sorted by Iron-Proxy's code below
        raise _explicit_error(spec, pid, title, exc) from None
    lease = _acc._lease_of(spec, answer)
    if lease is None or lease.profile_id != pid:
        # An Iron-Proxy that ignored the id (or lent a non-CLI account): never
        # run the pane as an account the user did not choose.
        what = f'Iron-Proxy answered with a different {spec.label} account than "{title}"'
        raise PaneAccountRefused(f"{what} — update Iron-Proxy.", what=what)
    return lease


def _explicit_error(
    spec: _acc.ProviderAccounts, pid: str, title: str, exc: BaseException
) -> PaneAccountRefused:
    """ANY Iron-Proxy error for a chosen account, as the pane's sentence — by
    kind when its code/details say so. Iron-Proxy (2d4f645) answers a pinned
    pick with: 404 PROFILE_NOT_FOUND; 400 INVALID_REQUEST (wrong provider/lane,
    or "…is disabled."); 429 QUOTA_EXCEEDED ``{profileId, title, provider,
    resetAt?, kind?}`` for a parked one; 401 AUTH_REQUIRED ``{profileId,
    title}``; 400 UNSUPPORTED for a lane it cannot run."""
    code = str(getattr(exc, "code", "") or "")
    details = _acc._details(exc)
    title = str(details.get("title") or title)
    if code == "PROFILE_NOT_FOUND":
        return _refuse_missing(spec, title)
    if code == "AUTH_REQUIRED" and details.get("profileId"):
        return _refuse_signin(spec, title)
    if code == "AUTH_REQUIRED":
        return PaneAccountRefused(_bare(_acc._refused_error(spec)))
    reset = str(details.get("resetAt") or details.get("earliestResetAt") or "")
    if code in ("ALL_PROFILES_EXHAUSTED", "QUOTA_EXCEEDED") or reset:
        return _refuse_parked(spec, title, _park_kind(str(details.get("kind") or "")), reset)
    message = str(getattr(exc, "message", "") or exc or "")
    if code == "INVALID_REQUEST" and "disabled" in message.lower():
        return _refuse_disabled(spec, title)
    if code == "UNREACHABLE":
        return PaneAccountRefused(_unavailable_sentence(exc))
    return _refuse_unlent(spec, title, _said(exc))


def _park_kind(kind: str) -> str:
    """Iron-Proxy's park kind -> the wording key (``accounts._WHAT``)."""
    return _acc._account_kind({"parkedReason": {"kind": kind}}) if kind else "other"


# --------------------------------------------------------------------------- #
# The resolver
# --------------------------------------------------------------------------- #
def _bare(err: BaseException) -> str:
    """An ``accounts`` refusal's sentence without its ``<provider id>: `` prefix."""
    return str(err).split(": ", 1)[-1]


def _restore_note(what: str) -> str:
    return f"{what}, so this pane came back on this PC's login."


def _off_note(title: str) -> str:
    return f"Iron-Proxy is off, so this pane came back on this PC's login instead of \"{title}\"."


def _unanswered_note(title: str) -> str:
    return (
        f"Iron-Proxy was not answering, so this pane came back on this PC's login instead of "
        f'"{title}" — open a new pane on that account from Launch.'
    )


def _seen(svc: Any) -> set[str]:
    try:
        return set(svc.providers_seen()) if hasattr(svc, "providers_seen") else set()
    except Exception:  # noqa: BLE001
        return set()


def resolve(
    svc: Any,
    request: Mapping[str, Any] | None,
    *,
    restoring: bool = False,
    titles: Mapping[str, str] | None = None,
    lease_timeout_s: float = LEASE_TIMEOUT_S,
) -> PaneAccounts:
    """The accounts a pane starts on. BLOCKING (loopback HTTP).

    ``request``: ``{provider: id | "default"}``; a provider not named is
    ABSENT (first free account while Iron-Proxy is on). ``restoring``: re-open
    a pane after a restart — only the named providers, by id, and never a
    refusal (an account that cannot be used comes back as this PC's login with
    a ``note``). ``titles``: the recorded titles a restore names in its note.
    Raises :class:`PaneAccountRefused` (never while restoring)."""
    req = normalise_request(request)
    titles = dict(titles or {})
    out = PaneAccounts()
    for provider, value in req.items():
        if value == DEFAULT:
            out.use_default(provider)
    explicit = {p: v for p, v in req.items() if v != DEFAULT}
    absent = [] if restoring else [p for p in PROVIDERS if p not in req]
    if not explicit:
        # No live call at all: a plain "+" pane never waits on Iron-Proxy.
        if absent and svc is not None and getattr(svc, "enabled", False):
            _first_free_from_cache(svc, absent, out)
        return out

    def degrade(note_for: Callable[[str], str]) -> None:
        """Restoring: every explicit account comes back as this PC's login."""
        for p, pid in explicit.items():
            out.use_default(p, note=note_for(titles.get(p) or pid))

    enabled = bool(svc is not None and getattr(svc, "enabled", False))
    if not enabled:
        if explicit and not restoring:
            raise PaneAccountRefused(_OFF)
        degrade(_off_note)
        return out
    if not explicit and not absent:
        return out
    try:
        client = svc.lease_client(timeout_s=lease_timeout_s)
    except Exception as exc:  # noqa: BLE001 — IronProxyUnavailable and anything else
        if explicit and not restoring:
            raise PaneAccountRefused(_unavailable_sentence(exc)) from None
        degrade(_unanswered_note)  # restoring (no-key panes never get here)
        return out
    if client is None:  # turned off in between: as if off
        if explicit and not restoring:
            raise PaneAccountRefused(_OFF)
        degrade(_off_note)
        return out

    if explicit:
        try:
            if not svc.has_feature(PICK_PROFILE_FEATURE):
                raise _refuse_too_old()
            try:
                profiles, states = client.profiles(), client.states()
            except Exception as exc:  # noqa: BLE001
                raise PaneAccountRefused(_unavailable_sentence(exc)) from None
            if not isinstance(profiles, list) or not isinstance(states, dict):
                raise PaneAccountRefused("Iron-Proxy could not list its accounts.")
            try:
                svc.note_accounts(profiles, states)  # the chips read the same read
            except Exception:  # noqa: BLE001
                log.debug("iron-proxy snapshot refresh failed", exc_info=True)
            profiles = [p for p in profiles if isinstance(p, dict)]
        except PaneAccountRefused as refused:
            if not restoring:
                raise
            what = refused.what
            degrade(lambda t: _restore_note(f"{what} (for \"{t}\")"))
            explicit = {}
        for p, pid in explicit.items():
            try:
                out.use_lease(
                    p,
                    _pick_explicit(client, SPECS[p], pid, profiles, states, titles.get(p, "")),
                )
            except PaneAccountRefused as refused:
                if not restoring:
                    raise
                out.use_default(p, note=_restore_note(refused.what))
    # The explicit pick refreshed the snapshot just above: the other providers
    # come from it, with no further call.
    _first_free_from_cache(svc, absent, out)
    return out


class _CachedAccounts:
    """The snapshot dressed as the two client reads ``accounts``' by-kind
    wording uses (``profiles``/``states``) — so the note is worded exactly as
    a chat refusal is, with no network call."""

    def __init__(self, profiles: list[dict[str, Any]], states: Mapping[str, Any]) -> None:
        self._profiles, self._states = profiles, dict(states)

    def profiles(self) -> list[dict[str, Any]]:
        return self._profiles

    def states(self) -> dict[str, Any]:
        return self._states


class _Exhausted(Exception):
    def __init__(self, details: dict[str, Any]) -> None:
        super().__init__("all parked")
        self.details = details


def _first_free_from_cache(svc: Any, absent: list[str], out: PaneAccounts) -> None:
    """Absent keys while Iron-Proxy is on, from the CACHED snapshot only:
    each provider's first enabled CLI account in Iron-Proxy's order that is
    not signed out and not parked (an expired park counts as free), run from
    its own home. No account for a provider -> nothing. None usable -> this
    PC's login + the by-kind note. No snapshot yet -> this PC's login +
    :data:`STARTING_NOTE` (for the providers Iron-Proxy had accounts for at
    its last read; every provider when it never answered)."""
    if not absent:
        return
    try:
        cache = svc.cached_accounts() if hasattr(svc, "cached_accounts") else None
    except Exception:  # noqa: BLE001
        cache = None
    if not cache:
        seen = _seen(svc)
        for p in absent:
            if not seen or p in seen:
                out.use_default(p, note=STARTING_NOTE)
        return
    profiles, states = cache
    for p in absent:
        spec = SPECS[p]
        mine = sorted(
            (
                prof for prof in profiles
                if isinstance(prof, Mapping) and prof.get("provider") == p
                and (prof.get("lane") or "cli") == "cli" and prof.get("enabled") is not False
            ),
            key=lambda prof: prof.get("order") or 0,
        )
        if not mine:
            continue  # no account for this provider: exactly today's behaviour
        chosen = None
        for prof in mine:
            st = states.get(prof.get("id")) if isinstance(states.get(prof.get("id")), Mapping) else {}
            if st.get("status") in ("unauthenticated", "disabled") or _parked_now(st):
                continue
            cli = prof.get("cli") if isinstance(prof.get("cli"), Mapping) else {}
            home = str(cli.get("home") or "").strip()
            if home:
                chosen = (prof, home)
                break
        if chosen is not None:
            prof, home = chosen
            pid = str(prof.get("id"))
            out.use_lease(p, _acc.make_lease(
                spec, pid, str(prof.get("title") or pid), home, sorted(PANE_OWN_KEYS[p])
            ))
            continue
        until = sorted(
            str((states.get(prof.get("id")) or {}).get("parkedUntil") or "")
            for prof in mine
        )
        until = [u for u in until if u]
        exc = _Exhausted({"resetAt": until[0] if until else "",
                          "tried": [str(prof.get("id")) for prof in mine]})
        words = _bare(_acc._exhausted_error(spec, exc, _CachedAccounts(list(profiles), states)))
        out.use_default(p, note=f"{words.rstrip('.')} — this pane uses this PC's login.")


# --------------------------------------------------------------------------- #
# Live state for a pane row (CACHED — never a call)
# --------------------------------------------------------------------------- #
def with_live_state(svc: Any, records: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The pane's recorded accounts, each with its live ``state`` read from the
    service's CACHED snapshot (never a network call — a list route reads this):
    ``active`` / ``ready`` / ``parked`` (+ ``until``, ``reason``) /
    ``needs-sign-in`` / ``missing`` (+ ``reason: "disabled"`` for one turned
    off) / ``default`` (this PC's login) / ``unknown`` (Iron-Proxy is off or
    has not been read)."""
    cache = None
    enabled = False
    try:
        enabled = bool(getattr(svc, "enabled", False)) if svc is not None else False
        if svc is not None and hasattr(svc, "cached_accounts"):
            cache = svc.cached_accounts()
    except Exception:  # noqa: BLE001 — a chip never breaks a list
        cache = None
    out: dict[str, dict[str, Any]] = {}
    for provider, rec in (records or {}).items():
        if not isinstance(rec, Mapping):
            continue
        row = {k: rec.get(k) for k in ("id", "title", "source")}
        if rec.get("note"):
            row["note"] = rec["note"]
        row.update(_state_of(rec, cache))
        note = _still_runs_note(row, enabled)
        if note and not row.get("note"):
            row["note"] = note
        out[str(provider)] = row
    return out


def _still_runs_note(row: Mapping[str, Any], enabled: bool) -> str:
    """The pane's shell was STARTED on this account's folder and keeps it for
    its life — say so when Iron-Proxy no longer offers that account."""
    if row.get("source") != "iron-proxy":
        return ""
    title = str(row.get("title") or row.get("id") or "")
    folder = f"This pane still runs on \"{title}\"'s folder"
    if row.get("state") == "missing" and row.get("reason") == "disabled":
        return f"{folder}; it is turned off on the Iron-Proxy card."
    if row.get("state") == "missing":
        return f"{folder}; Iron-Proxy no longer lists it."
    if row.get("state") == "unknown" and not enabled:
        return f"Iron-Proxy is off; {folder[0].lower()}{folder[1:]}."
    return ""


def _state_of(rec: Mapping[str, Any], cache: Any) -> dict[str, Any]:
    pid = rec.get("id")
    if rec.get("source") == "default" or not pid:
        return {"state": "default"}
    if not cache:
        return {"state": "unknown"}
    profiles, states = cache
    prof = next((p for p in profiles if isinstance(p, Mapping) and p.get("id") == pid), None)
    if prof is None:
        return {"state": "missing"}
    st = states.get(pid) if isinstance(states.get(pid), Mapping) else {}
    if prof.get("enabled") is False or st.get("status") == "disabled":
        return {"state": "missing", "reason": "disabled"}
    if st.get("status") == "unauthenticated" or _reason_kind(st) == "auth-expired":
        return {"state": "needs-sign-in"}
    if _parked_now(st):
        out: dict[str, Any] = {"state": "parked"}
        if st.get("parkedUntil"):
            out["until"] = st["parkedUntil"]
        kind = _reason_kind(st)
        if kind:
            out["reason"] = kind
        return out
    if st.get("status") == "active":
        return {"state": "active"}
    return {"state": "ready"}

