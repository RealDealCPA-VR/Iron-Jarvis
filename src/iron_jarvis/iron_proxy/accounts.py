"""Run a subscription CLI AS the account Iron-Proxy picks (v1.301.0).

Iron-Proxy is the ACCOUNT MANAGER: which accounts exist, their order, who is
parked until when, sign-in and usage. Iron Jarvis keeps its OWN adapters
(claude-cli native stream-json, codex-cli, grok-cli) and only borrows an
account for one call: :func:`lease` asks Iron-Proxy which account to use and
returns the vendor-CLI home to run it from; the adapter reports the outcome
back — :func:`report_limit` (Iron-Proxy's own classifier decides whether that
account is parked) or :func:`report_success` (usage recorded).

THE RULES (the shared contract, section 2):

* SAME PROVIDER ONLY. A lease is always for the adapter's own provider; this
  module never names another provider and never falls back to one (Iron
  Jarvis rule: never auto-switch providers).
* OFF IS OFF, ON IS ON. Iron-Proxy disabled, or with no CLI account for that
  provider (``NO_PROFILE``) -> :func:`lease` returns None and the adapter
  runs EXACTLY as before (the default login). While Iron-Proxy is ON, any
  other outcome is a plain sentence (:class:`~iron_jarvis.providers.adapters.
  base.ProviderError`, not transient, ``no_failover`` — the router never moves
  it to another provider; ``retry_after`` = seconds to the first reset): all
  accounts parked (said BY KIND), an account needing sign-in, a refused
  control token, an Iron-Proxy too old for the executor API, one that is
  starting or down — never a silent run on the default login.
* OVERLOAD IS NOT AN ACCOUNT. A 5xx/529 (or CLI words that only say
  "overloaded") is the provider's, not one account's: it is never reported
  and never rotates — it is raised exactly as before (transient).
* REPORTING NEVER FAILS A TURN. Every Iron-Proxy error is swallowed and
  logged ONCE (per call site and error code), at INFO, with no token.
* RETRY ONLY BEFORE ANYTHING REACHED THE CALLER. :func:`retry_lease` reports a
  limit and, when Iron-Proxy parked the account and no text has streamed yet,
  leases the next account (each account at most once, hard cap
  :data:`MAX_ATTEMPTS`). After text streamed the limit is still reported, then
  the error is raised as before (no mid-answer account switch).
* BLOCKING HTTP STAYS OFF THE LOOP: :func:`lease`, :func:`report_limit` and
  :func:`report_success` are synchronous; adapters call them through
  ``asyncio.to_thread`` (:func:`retry_lease` / :func:`report_success_async`
  do the hop themselves).

The client comes from the daemon's Iron-Proxy service
(``service.current().client()``), imported lazily; :func:`set_client_source`
is the test seam.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger("ironjarvis.iron_proxy")

#: Hard cap on attempts (accounts tried) for ONE call.
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ProviderAccounts:
    """How an Iron Jarvis provider maps onto Iron-Proxy."""

    ij_provider: str
    proxy_provider: str
    home_var: str
    label: str


#: Iron Jarvis provider id -> Iron-Proxy provider + the vendor CLI's home var.
PROVIDERS: dict[str, ProviderAccounts] = {
    "claude-cli": ProviderAccounts("claude-cli", "anthropic", "CLAUDE_CONFIG_DIR", "Claude"),
    "codex-cli": ProviderAccounts("codex-cli", "openai", "CODEX_HOME", "ChatGPT (Codex)"),
    "grok-cli": ProviderAccounts("grok-cli", "xai", "GROK_HOME", "Grok"),
}

#: Variables an account's ``unset`` list may never remove (the child could not
#: even start, or would lose its user profile).
_PROTECTED = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
})


@dataclass(frozen=True)
class Lease:
    """One account, borrowed for one call."""

    profile_id: str
    title: str
    #: The Iron-Proxy provider id (``anthropic`` / ``openai`` / ``xai``).
    provider: str
    #: Variables to SET in the child env — only the provider's home var.
    env_set: dict[str, str] = field(default_factory=dict)
    #: Variables to REMOVE from the child env (Iron-Proxy's list, minus PATH & co).
    env_unset: tuple[str, ...] = ()
    #: The Iron Jarvis provider id this lease is for (``claude-cli`` ...).
    ij_provider: str = ""

    @property
    def home(self) -> str:
        spec = PROVIDERS.get(self.ij_provider)
        return self.env_set.get(spec.home_var, "") if spec else ""

    def apply(self, env: dict[str, str]) -> dict[str, str]:
        """``env`` with this account applied: unset first, then set. Mutates
        and returns ``env`` (callers pass their own copy)."""
        for key in self.env_unset:
            env.pop(key, None)
        env.update(self.env_set)
        return env


# --------------------------------------------------------------------------- #
# The client (lazy; a seam for tests)
# --------------------------------------------------------------------------- #
_client_source: Callable[[], Any] | None = None


def set_client_source(fn: Callable[[], Any] | None) -> Callable[[], Any] | None:
    """TESTS: make ``fn()`` the client source (``None`` restores the live
    service). Returns the previous source."""
    global _client_source
    previous, _client_source = _client_source, fn
    return previous


def _service_module() -> Any:
    from . import service  # lazy: the daemon's service owns the client

    return service


def _client(*, for_lease: bool = False) -> Any:
    """The live Iron-Proxy client, or None.

    Reporting (``for_lease=False``): None when disabled / not running; never
    raises. Leasing (``for_lease=True``): the service's ``lease_client`` —
    None ONLY when Iron-Proxy is disabled; an Iron-Proxy that is ON but not
    usable raises (``IronProxyUnavailable``) so the caller can say so instead
    of running on the default login. An older service without
    ``lease_client`` falls back to ``client()``."""
    if not for_lease:
        try:
            if _client_source is not None:
                return _client_source()
            svc = _service_module().current()
            return svc.client() if svc is not None else None
        except Exception as exc:  # noqa: BLE001 — Iron-Proxy must never break a call
            _note("client", exc)
            return None
    if _client_source is not None:
        return _client_source()
    svc = _service_module().current()
    if svc is None:
        return None
    lease_client = getattr(svc, "lease_client", None)
    if callable(lease_client):
        return lease_client(timeout_s=LEASE_CLIENT_TIMEOUT_S)
    return svc.client()


#: How long a lease waits for an Iron-Proxy that is still starting.
LEASE_CLIENT_TIMEOUT_S = 15.0


_NOTED: set[tuple[str, str]] = set()
_NOTED_LOCK = threading.Lock()


def _note(where: str, exc: BaseException) -> None:
    """Log an Iron-Proxy failure ONCE per (call site, error code): the code and
    type only — never a token, a body or a path."""
    code = str(getattr(exc, "code", "") or type(exc).__name__)
    with _NOTED_LOCK:
        if (where, code) in _NOTED:
            return
        _NOTED.add((where, code))
    log.info("iron-proxy: %s failed (%s) — carrying on without it", where, code)


# --------------------------------------------------------------------------- #
# Plain words
# --------------------------------------------------------------------------- #
_CONNECTIONS = "the Connections page (Iron-Proxy card)"

#: Iron-Proxy's own hints name its CLI and its tray switcher; inside Iron Jarvis
#: the place to act is the Connections page. Every rule rewrites a WHOLE
#: sentence (Iron-Proxy's hints are one imperative sentence each), so the result
#: always reads as a sentence. Iron-Proxy's DEFAULT_HINTS (packages/core/src/
#: errors.ts) and the per-error hints built there are pinned verbatim against
#: the vendored bundle in tests/test_iron_proxy_accounts_v1301.py.
_HINT_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    # AUTH_REQUIRED (default and per account), cli lane
    (re.compile(r"Log (?P<who>.+?) in again: `?iron-proxy login [^\s,`]+`?, or 'Log in' on it in the switcher\.?"),
     r"Sign \g<who> in again on " + _CONNECTIONS + "."),
    # AUTH_REQUIRED, api-key lane: keys are entered in Iron-Proxy's own app
    (re.compile(r"Enter the API key for (?P<who>.+?) again: 'Set API key' on it in the switcher\.?"),
     r"Enter the API key for \g<who> again in the Iron-Proxy app."),
    # NO_PROFILE (default and per provider)
    (re.compile(r"Add an account(?: for [\w-]+)?: `?iron-proxy profiles add [^,]*?"
                r"(?: \(then iron-proxy login [^\s)]+\))?, or 'Add account' in the switcher\.?"),
     "Add an account on " + _CONNECTIONS + ", then sign it in there."),
    # PROFILE_NOT_FOUND
    (re.compile(r"Run iron-proxy profiles list \(or open the switcher\) and use one of the ids shown there\.?"),
     "Check the account list on " + _CONNECTIONS + "."),
    # ALL_PROFILES_EXHAUSTED (default: "...of this provider: iron-proxy profiles add.";
    # per provider: "...another anthropic account: iron-proxy profiles add --provider ... --title \"...\".")
    (re.compile(r"add another (?P<what>(?:[\w-]+ account)|(?:account of this provider)): "
                r"`?iron-proxy profiles add(?: [^`]*?\"\.\.\.\")?`?\.?(?=\s|$)"),
     r"add another \g<what> on " + _CONNECTIONS + "."),
    # PROVIDER_ERROR
    (re.compile(r"check the provider's status page and iron-proxy status\.?"),
     "check the provider's status page and the Iron-Proxy card on the Connections page."),
    # CLI_NOT_FOUND (default and installHint for an unknown binary)
    (re.compile(r"Install (?P<what>.+?) and put it on PATH, then run iron-proxy doctor to confirm\.?"),
     r"Install \g<what> and put it on PATH, then check the Iron-Proxy card on the Connections page."),
    # CLI_FAILED
    (re.compile(r"Run iron-proxy doctor, then try the same step yourself with iron-proxy login [^\s]+ --terminal\.?"),
     "Sign the account in again on " + _CONNECTIONS + "."),
    # INVALID_REQUEST
    (re.compile(r"Check the command or request against iron-proxy --help and docs/ADOPTING\.md, then try again\.?"),
     "Update Iron-Proxy, or turn it off and on again on " + _CONNECTIONS + "."),
)

#: Any CLI command or switcher mention a rule above did not cover.
_LEFTOVER = re.compile(r"`?\biron-proxy [a-z-]|\bswitcher\b")
_GENERIC_HINT = "See the Iron-Proxy card on the Connections page."


def plain_hint(text: str) -> str:
    """Iron-Proxy's words with every CLI command / tray-switcher instruction
    turned into a whole sentence about the Connections page — what a user of
    Iron Jarvis can do. A sentence no rule covers is replaced whole by
    :data:`_GENERIC_HINT` (never a half-rewritten fragment)."""
    out = str(text or "").strip()
    for pattern, repl in _HINT_REWRITES:
        out = pattern.sub(repl, out)
    if _LEFTOVER.search(out):
        parts = re.split(r"(?<=[.!?])\s+", out)
        kept: list[str] = []
        for part in parts:
            piece = _GENERIC_HINT if _LEFTOVER.search(part) else part
            if not (kept and piece == _GENERIC_HINT and kept[-1] == _GENERIC_HINT):
                kept.append(piece)
        out = " ".join(kept)
    return out


def _refusal(spec: ProviderAccounts, words: str, retry_after: float | None = None) -> Exception:
    """A plain-words, non-transient refusal that must never move the call to
    another provider (``no_failover`` — the router honours it in both lanes)."""
    from ..providers.adapters.base import ProviderError

    err = ProviderError(f"{spec.ij_provider}: {words}", retry_after=retry_after, transient=False)
    err.no_failover = True  # type: ignore[attr-defined]
    return err


# --------------------------------------------------------------------------- #
# Lease
# --------------------------------------------------------------------------- #
def _details(exc: BaseException) -> dict[str, Any]:
    details = getattr(exc, "details", None)
    return details if isinstance(details, dict) else {}


def _local_time(iso: str) -> tuple[str, float | None]:
    """``("3:05 PM", seconds from now)`` for an ISO instant, ``("", None)``
    when it does not parse. Another day gets its weekday ("Tue 9:00 AM")."""
    try:
        when = datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "", None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    local = when.astimezone()
    words = local.strftime("%I:%M %p").lstrip("0")
    if local.date() != now.astimezone().date():
        words = local.strftime("%a ") + words
    return words, max(0.0, (when - now).total_seconds())


#: Iron-Proxy's park kinds -> how a parked account is described.
_USAGE_KINDS = ("quota-exhausted", "billing")


def _account_kind(state: dict[str, Any]) -> str:
    """``signin`` / ``usage`` / ``rate`` / ``busy`` / ``other`` for one state."""
    reason = state.get("parkedReason") if isinstance(state.get("parkedReason"), dict) else {}
    kind = str(reason.get("kind") or "")
    if state.get("status") == "unauthenticated" or kind == "auth-expired":
        return "signin"
    if kind in _USAGE_KINDS:
        return "usage"
    if kind == "rate-limit":
        return "rate"
    if kind == "overloaded":
        return "busy"
    return "other"


def _parked_accounts(client: Any, spec: ProviderAccounts, tried: list[str]) -> list[dict[str, Any]]:
    """``[{title, kind, until}]`` for this provider's CLI accounts, read from
    Iron-Proxy (best effort: ``[]`` when it cannot say). BLOCKING."""
    try:
        profiles = client.profiles()
        states = client.states()
    except Exception as exc:  # noqa: BLE001 — the generic sentence still works
        _note("states", exc)
        return []
    if not isinstance(profiles, list) or not isinstance(states, dict):
        return []
    out = []
    for p in profiles:
        if not isinstance(p, dict) or p.get("provider") != spec.proxy_provider:
            continue
        if (p.get("lane") or "cli") != "cli" or p.get("enabled") is False:
            continue
        if tried and p.get("id") not in tried:
            continue
        st = states.get(p.get("id")) if isinstance(states.get(p.get("id")), dict) else {}
        out.append({"title": str(p.get("title") or p.get("id")), "kind": _account_kind(st),
                    "until": str(st.get("parkedUntil") or "")})
    return out


#: Words that never read as "transient" to the router's phrase check (no
#: "rate limit", "overload", "unavailable right now"…).
_WHAT = {"usage": "at its usage limit", "rate": "at its request limit", "busy": "busy"}


def _exhausted_error(spec: ProviderAccounts, exc: BaseException, client: Any) -> Exception:
    """Every account of this provider is parked — said BY KIND: rate-limited,
    at the usage limit, or needing to sign in (a mix names each account)."""
    details = _details(exc)
    tried = [t for t in (details.get("tried") or []) if isinstance(t, str)]
    reset = details.get("resetAt") or details.get("earliestResetAt") or ""
    words, seconds = _local_time(reset) if reset else ("", None)
    accts = _parked_accounts(client, spec, tried)
    kinds = {a["kind"] for a in accts}
    n = len(accts)
    label = spec.label
    if accts and kinds == {"signin"}:
        if n == 1:
            return _refusal(spec, f'The {label} account "{accts[0]["title"]}" in Iron-Proxy needs to sign in '
                                  f"again — sign it in on {_CONNECTIONS}, then try again.")
        both = "Both" if n == 2 else f"All {n}"
        return _refusal(spec, f"{both} {label} accounts in Iron-Proxy need to sign in again — sign them "
                              f"in on {_CONNECTIONS}, then try again.")
    if accts and len(kinds) == 1 and kinds <= {"usage", "rate"}:
        what = _WHAT[next(iter(kinds))]
        if n == 1:
            tail = f"; it is free again at {words}." if words else "."
            return _refusal(spec, f"The only {label} account in Iron-Proxy is {what}{tail}", seconds)
        plural = "at their usage limit" if what == _WHAT["usage"] else "at their request limit"
        tail = f"; the first is free again at {words}." if words else "."
        return _refusal(spec, f"All {n} {label} accounts in Iron-Proxy are {plural}{tail}", seconds)
    if accts:
        parts = []
        for a in accts:
            until, _s = _local_time(a["until"]) if a["until"] else ("", None)
            if a["kind"] == "signin":
                parts.append(f'"{a["title"]}" needs to sign in again on {_CONNECTIONS}')
            elif a["kind"] in _WHAT:
                parts.append(f'"{a["title"]}" is {_WHAT[a["kind"]]}' + (f" until {until}" if until else ""))
            else:
                parts.append(f'"{a["title"]}" is parked' + (f" until {until}" if until else ""))
        limited = any(a["kind"] != "signin" for a in accts)
        return _refusal(spec, f"No {label} account in Iron-Proxy can take this right now: "
                              + "; ".join(parts) + ".", seconds if limited else None)
    # Iron-Proxy could not say which kind: one honest generic sentence.
    tail = f"; the first is free again at {words}." if words else "."
    return _refusal(spec, f"Every {label} account in Iron-Proxy is parked right now (a limit or a "
                          f"sign-in){tail}", seconds)


def _auth_required_error(spec: ProviderAccounts, exc: BaseException) -> Exception:
    details = _details(exc)
    title = str(details.get("title") or details.get("profileId") or "").strip()
    who = f'"{title}"' if title else "an account"
    return _refusal(spec, f"The {spec.label} account {who} in Iron-Proxy needs to sign in again "
                          f"— sign it in on {_CONNECTIONS}, then try again.")


def _refused_error(spec: ProviderAccounts) -> Exception:
    return _refusal(spec, f"Iron-Proxy refused Iron Jarvis's access, so it cannot say which "
                          f"{spec.label} account to use — turn Iron-Proxy off and on again on the "
                          "Connections page.")


def _too_old_error(spec: ProviderAccounts) -> Exception:
    return _refusal(spec, f"The Iron-Proxy running on this PC is too old to lend its {spec.label} "
                          "accounts to Iron Jarvis — update Iron-Proxy, or turn it off on the "
                          "Connections page.")


def _unanswered_error(spec: ProviderAccounts, exc: BaseException) -> Exception:
    said = plain_hint(str(getattr(exc, "sentence", "") or getattr(exc, "message", "") or "")).strip()
    return _refusal(spec, f"Iron-Proxy is on but could not say which {spec.label} account to use"
                          + (f" ({said.rstrip('.')})" if said else "")
                          + " — try again, or turn Iron-Proxy off on the Connections page.")


def _is_unavailable(exc: BaseException) -> bool:
    """The service's ``IronProxyUnavailable`` (by class when it exists, by
    name until it lands)."""
    try:
        cls = getattr(_service_module(), "IronProxyUnavailable", None)
    except Exception:  # noqa: BLE001
        cls = None
    if isinstance(cls, type) and isinstance(exc, cls):
        return True
    return type(exc).__name__ == "IronProxyUnavailable"


def _unavailable_error(spec: ProviderAccounts, exc: BaseException) -> Exception:
    said = plain_hint(str(getattr(exc, "sentence", "") or exc or "")).strip()
    if not said:
        said = "Iron-Proxy is on but not answering."
    return _refusal(spec, said if said.endswith((".", "!", "?")) else said + ".")


def lease(ij_provider: str) -> Lease | None:
    """The account to run ``ij_provider``'s CLI as right now, or None (run as
    today: the default login). BLOCKING — call through ``asyncio.to_thread``.

    None ONLY when Iron-Proxy is OFF (disabled / no service), when it has no
    account for this provider (``NO_PROFILE``), or when the picked account is
    not a CLI account. While Iron-Proxy is ON, every other outcome is a
    non-transient ``ProviderError`` in plain words that never fails over to
    another provider (``no_failover``) — all parked (said by kind), an account
    needing sign-in, a refused control token, an Iron-Proxy too old for the
    executor API, one that is starting/down (``IronProxyUnavailable``) — so a
    call never silently runs on the default login."""
    spec = PROVIDERS.get(ij_provider)
    if spec is None:
        return None
    try:
        client = _client(for_lease=True)
    except Exception as exc:  # noqa: BLE001 — sorted below
        if _is_unavailable(exc):
            raise _unavailable_error(spec, exc) from None
        _note("client", exc)
        raise _unanswered_error(spec, exc) from None
    if client is None:
        return None
    try:
        answer = client.pick(spec.proxy_provider, lane="cli")
    except Exception as exc:  # noqa: BLE001 — sorted by Iron-Proxy's code below
        code = getattr(exc, "code", "")
        if code == "NO_PROFILE":
            return None  # no account for this provider: exactly today's behaviour
        if code == "ALL_PROFILES_EXHAUSTED":
            raise _exhausted_error(spec, exc, client) from None
        if code == "AUTH_REQUIRED":
            if _details(exc).get("profileId"):
                raise _auth_required_error(spec, exc) from None
            # No account named: Iron-Proxy refused OUR token (not an account
            # problem). It is running and may hold accounts — never guess.
            _note("pick", exc)
            raise _refused_error(spec) from None
        _note("pick", exc)
        if code == "INVALID_REQUEST" or getattr(exc, "status", None) == 404:
            # An Iron-Proxy older than the external-executor API (no /iron/pick).
            raise _too_old_error(spec) from None
        if _is_unavailable(exc):
            raise _unavailable_error(spec, exc) from None
        raise _unanswered_error(spec, exc) from None
    return _lease_of(spec, answer)


def _lease_of(spec: ProviderAccounts, answer: Any) -> Lease | None:
    profile = answer.get("profile") if isinstance(answer, dict) else None
    env = answer.get("env") if isinstance(answer, dict) else None
    if env is None:
        return None  # not a CLI account (an API-key one): Iron Jarvis keeps its own route
    pid = str((profile or {}).get("id") or "").strip() if isinstance(profile, dict) else ""
    env_set = (env or {}).get("set") if isinstance(env, dict) else None
    home = env_set.get(spec.home_var) if isinstance(env_set, dict) else None
    if not pid or not isinstance(home, str) or not home.strip():
        _note("pick", ValueError("an answer with no account home"))
        return None
    ignored = sorted(k for k in env_set if k != spec.home_var)
    if ignored:
        # ONLY the home is applied: anything else (an API key, a base URL) would
        # move a subscription CLI off the account it is meant to run as.
        _note("pick", ValueError("ignored env " + ",".join(ignored)))
    unset_raw = (env or {}).get("unset") if isinstance(env, dict) else None
    unset = tuple(
        k for k in (unset_raw if isinstance(unset_raw, list) else [])
        if isinstance(k, str) and k and k.upper() not in _PROTECTED and k != spec.home_var
    )
    return Lease(
        profile_id=pid,
        title=str(profile.get("title") or pid),
        provider=spec.proxy_provider,
        env_set={spec.home_var: home},
        env_unset=unset,
        ij_provider=spec.ij_provider,
    )


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _report(
    lease: Lease | None,
    status: int | None = None,
    headers: dict[str, str] | None = None,
    text: str | None = None,
) -> bool | None:
    """True = Iron-Proxy PARKED the account, False = it was told and declined,
    None = it could not be told (off, unreachable, an error). Never raises.
    BLOCKING."""
    if lease is None:
        return None
    client = _client()
    if client is None:
        return None
    try:
        answer = client.signal(
            lease.profile_id,
            status=status,
            headers=dict(headers) if headers else None,
            text=(text or "")[:4000] or None,
        )
    except Exception as exc:  # noqa: BLE001 — reporting never fails a turn
        _note("signal", exc)
        return None
    return bool(isinstance(answer, dict) and answer.get("parked") is True)


def report_limit(
    lease: Lease | None,
    status: int | None = None,
    headers: dict[str, str] | None = None,
    text: str | None = None,
) -> bool:
    """Hand Iron-Proxy what a failed attempt saw; True when it PARKED the
    account (its classifier decided). Never raises. BLOCKING."""
    return _report(lease, status, headers, text) is True


def _usage_body(usage: dict[str, Any] | None) -> dict[str, int] | None:
    """Our usage (``input_tokens`` = the TOTAL prompt incl. cache) in Iron-Proxy's
    shape: ``inputTokens`` = the prompt NOT read from cache, ``cacheReadTokens``
    separately."""
    if not isinstance(usage, dict):
        return None

    def num(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    if "input_tokens" not in usage and "output_tokens" not in usage:
        return None
    read = num("cache_read_input_tokens")
    body = {"inputTokens": max(0, num("input_tokens") - read), "outputTokens": num("output_tokens")}
    if read:
        body["cacheReadTokens"] = read
    return body


def report_success(
    lease: Lease | None,
    usage: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    model: str | None = None,
) -> None:
    """Tell Iron-Proxy the account served a request. Never raises. BLOCKING."""
    if lease is None:
        return
    client = _client()
    if client is None:
        return
    try:
        client.finished(
            lease.profile_id,
            usage=_usage_body(usage),
            duration_ms=int(duration_ms) if duration_ms is not None else None,
            model=model or None,
        )
    except Exception as exc:  # noqa: BLE001 — reporting never fails a turn
        _note("finished", exc)


async def report_success_async(
    lease: Lease | None,
    usage: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    model: str | None = None,
) -> None:
    """:func:`report_success` off the loop."""
    if lease is None:
        return
    try:
        await asyncio.to_thread(report_success, lease, usage, duration_ms, model)
    except Exception as exc:  # noqa: BLE001
        _note("finished", exc)


#: Statuses that mean the PROVIDER is busy, not that one account is limited.
_OVERLOAD_STATUSES = frozenset({500, 502, 503, 504, 529})

# Iron-Proxy's ``detectFromCliOutput`` billing, sign-in and limit words (its
# own regexes): CLI text with none of them is not one account's problem — a
# dropped connection, or words that only say "overloaded" (the provider's).
_ACCOUNT_WORDS = re.compile(
    r"credit balance|insufficient[_ ]quota|billing|payment required|upgrade your plan"
    r"|not (?:logged in|authenticated)|please (?:log ?in|sign in|run .*login)|invalid (?:api key|token)"
    r"|token (?:has )?expired|authentication (?:failed|required)|unauthorized|re-?authenticat"
    r"|(?:usage|rate|weekly|daily|monthly|session|5-hour|five-hour) limit|limit (?:reached|exceeded|hit)"
    r"|you(?:'ve| have) hit your|out of (?:credits|quota)|quota (?:exceeded|exhausted)|too many requests"
    r"|resource_exhausted|429",
    re.IGNORECASE,
)
_SIGNIN_WORDS = re.compile(
    r"not (?:logged in|authenticated)|please (?:log ?in|sign in|run .*login)|invalid (?:api key|token)"
    r"|token (?:has )?expired|authentication (?:failed|required)|unauthorized|re-?authenticat",
    re.IGNORECASE,
)


#: The statuses that are ONE ACCOUNT's problem (a limit, a refused sign-in).
_ACCOUNT_STATUSES = frozenset({401, 403, 429})


def _not_an_account_signal(signal: dict[str, Any]) -> bool:
    """True for a report that must never reach Iron-Proxy — anything that is
    not ONE ACCOUNT's limit or sign-in: an overload status (5xx/529), any
    other status, CLI words with no limit/sign-in/billing word in them (a
    dropped connection, overload-only words) or no words at all."""
    status = signal.get("status")
    if status is not None:
        return status in _OVERLOAD_STATUSES or status not in _ACCOUNT_STATUSES
    text = str(signal.get("text") or "")
    if not text.strip():
        return True
    return not _ACCOUNT_WORDS.search(text)


def _marked(cause: BaseException) -> BaseException:
    """The attempt's own error, marked so the router never moves it to another
    provider (it is an ACCOUNT's limit, and a lease existed)."""
    try:
        cause.no_failover = True  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — an exception that refuses attributes
        pass
    return cause


def _untold_error(current: Lease, cause: BaseException, signal: dict[str, Any]) -> Exception:
    """The account hit a limit (or a sign-in refusal) and Iron-Proxy could not
    be told: one plain sentence, never another provider."""
    spec = PROVIDERS.get(current.ij_provider)
    label = spec.label if spec else current.ij_provider
    status = signal.get("status")
    signin = status in (401, 403) or bool(_SIGNIN_WORDS.search(str(signal.get("text") or "")))
    what = "was refused (it may need to sign in again)" if signin else "hit a limit"
    retry_after = getattr(cause, "retry_after", None)
    words = (f'The {label} account "{current.title}" in Iron-Proxy {what} and Iron-Proxy could '
             "not be told — try again shortly.")
    if spec is None:
        from ..providers.adapters.base import ProviderError

        err = ProviderError(f"{current.ij_provider}: {words}", retry_after=retry_after, transient=False)
        err.no_failover = True  # type: ignore[attr-defined]
        return err
    return _refusal(spec, words, retry_after if isinstance(retry_after, (int, float)) else None)


async def _final_error(current: Lease, cause: BaseException) -> BaseException:
    """No retry will happen for an account signal: ask Iron-Proxy once more so
    the user gets the BY-KIND sentence (every account parked / needs sign-in);
    when it still offers an account (it declined to park, the cap was hit, it
    picked the same one again), the attempt's own error, marked no_failover."""
    try:
        await asyncio.to_thread(lease, current.ij_provider)
    except Exception as exc:  # noqa: BLE001 — the plain-words sentence
        if getattr(exc, "no_failover", False):
            exc.__cause__ = cause
            return exc
    return _marked(cause)


async def retry_lease(
    current: Lease,
    cause: BaseException,
    signal: dict[str, Any] | None,
    tried: list[str],
    *,
    retry_ok: bool = True,
) -> Lease:
    """After a failed attempt as ``current``: report the limit, and return the
    NEXT account to retry with — or re-raise ``cause``. ``tried`` is the
    call's own list of attempted account ids (the caller starts it empty);
    it counts ATTEMPTS, so the cap holds even if Iron-Proxy misbehaves.

    * not ONE ACCOUNT's limit/sign-in (``signal`` None, an overload, a
      dropped connection, no limit words) -> ``cause`` exactly as before,
      nothing reported;
    * otherwise the error that leaves ALWAYS carries ``no_failover``:
      Iron-Proxy could not be told -> "…hit a limit and Iron-Proxy could not
      be told — try again shortly."; text already streamed -> ``cause``
      marked; Iron-Proxy declined to park, the cap was hit, or it offers an
      account already tried -> one more pick for the BY-KIND sentence, else
      ``cause`` marked; parked -> the next account (returned). Never another
      provider, never the default login."""
    if signal is None or _not_an_account_signal(signal):
        raise cause
    # From here the failure IS an account's limit/sign-in while a lease existed:
    # whatever leaves carries no_failover (a raw 429 would otherwise read
    # transient — retried on the same account, then failed over elsewhere).
    told = await asyncio.to_thread(_report, current, **signal)
    tried.append(current.profile_id)
    if told is None:
        raise _untold_error(current, cause, signal) from cause
    if not retry_ok:
        raise _marked(cause)  # text already streamed: its own words, no switch
    if not told or len(tried) >= MAX_ATTEMPTS:
        raise await _final_error(current, cause)
    try:
        nxt = await asyncio.to_thread(lease, current.ij_provider)
    except Exception as exc:  # the plain-words sentence, with the attempt behind it
        if getattr(exc, "no_failover", False):
            raise exc from cause
        raise _marked(cause) from exc
    if nxt is None or nxt.profile_id in tried:
        raise await _final_error(current, cause)
    log.info("iron-proxy: %s account parked; retrying with the next account", current.ij_provider)
    return nxt
