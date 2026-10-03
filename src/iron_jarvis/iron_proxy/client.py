"""A small, synchronous client for Iron-Proxy's control API (``/iron/*``).

Iron-Proxy (RealDealCPA-VR/Iron-Proxy, MIT) is the ACCOUNT MANAGER: which
subscription accounts exist, their order, which are parked until when, sign-in
and usage. Iron Jarvis talks to it over loopback HTTP with the bearer token the
proxy wrote into ``<data_dir>/proxy.json``.

Every method is BLOCKING (httpx, sync). Callers on the daemon's event loop go
through ``asyncio.to_thread`` — the CLAUDE.md "nothing blocking on the event
loop" rule.

The token is a credential for every account the user signed in to. It lives on
this object only: it is never part of ``repr``, of an exception, of a log line,
or of anything a route returns.

Errors: any non-2xx answer or transport failure raises :class:`IronProxyError`.
Iron-Proxy writes the ``json`` dialect for ``/iron/*`` (see
``packages/proxy/src/server.ts`` ``errorBody``/``sendError``)::

    {"error": {"message": "...", "code": "NO_PROFILE"},
     "iron":  {"code": "NO_PROFILE", "retryable": false,
               "details": {...}, "hint": "..."}}

with the HTTP status from ``statusForCode`` (NO_PROFILE/PROFILE_NOT_FOUND 404,
AUTH_REQUIRED 401, ALL_PROFILES_EXHAUSTED/QUOTA_EXCEEDED 429 + ``retry-after``,
INVALID_REQUEST/UNSUPPORTED 400, else 502). A transport failure is code
``UNREACHABLE``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

#: The fields Iron Jarvis may change on an account. Iron-Proxy's PATCH also
#: takes cli/apiKey/order/defaultModel; those are not Iron Jarvis's to edit
#: (order goes through ``reorder``).
_PATCHABLE = ("enabled", "title")


class IronProxyError(Exception):
    """One failed Iron-Proxy call, in plain words.

    ``code`` is Iron-Proxy's error code (``NO_PROFILE``, ``ALL_PROFILES_EXHAUSTED``,
    ``AUTH_REQUIRED``, ``PROFILE_NOT_FOUND``, ``INVALID_REQUEST`` …) or
    ``UNREACHABLE`` for a transport failure; ``hint`` is Iron-Proxy's own
    what-to-do sentence when it gave one; ``status`` the HTTP status (``None``
    for a transport failure); ``details`` Iron-Proxy's details object (e.g.
    ``resetAt``/``earliestResetAt`` for exhausted, ``profileId``/``title`` for
    auth-required).
    """

    def __init__(
        self,
        code: str,
        message: str,
        hint: str | None = None,
        status: int | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.status = status
        self.details = dict(details or {})
        self.retryable = retryable

    def __repr__(self) -> str:  # never anything but what Iron-Proxy said
        return f"IronProxyError(code={self.code!r}, status={self.status!r}, message={self.message!r})"

    @property
    def sentence(self) -> str:
        """The one sentence to show a user: Iron-Proxy's hint when it gave one,
        else its message."""
        return (self.hint or self.message or self.code).strip()


def _error_from_response(resp: httpx.Response) -> IronProxyError:
    """Parse Iron-Proxy's json-dialect error body. A body that is not that shape
    (an HTML error page, a wrong server on the port) still yields a typed error
    with the status, never a raw dump of whatever came back."""
    status = resp.status_code
    try:
        doc = resp.json()
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        iron = doc.get("iron") if isinstance(doc.get("iron"), dict) else {}
        err = doc.get("error") if isinstance(doc.get("error"), dict) else {}
        code = str(iron.get("code") or err.get("code") or f"HTTP_{status}")
        message = str(err.get("message") or f"Iron-Proxy answered {status}.")
        hint = iron.get("hint")
        details = iron.get("details") if isinstance(iron.get("details"), dict) else {}
        return IronProxyError(
            code,
            message,
            hint=str(hint) if hint else None,
            status=status,
            details=details,
            retryable=bool(iron.get("retryable", False)),
        )
    return IronProxyError(f"HTTP_{status}", f"Iron-Proxy answered {status}.", status=status)


class IronProxyClient:
    """``IronProxyClient(url, token, timeout=5.0)`` — see the module docstring.

    ``on_unreachable`` (optional) is called once per transport failure, before
    the error is raised; the service uses it to drop its cached client so the
    next ``service.client()`` re-locates the proxy.

    ``refresh`` (optional) is called when Iron-Proxy refuses the TOKEN (a 401
    ``AUTH_REQUIRED`` with no ``details.profileId`` — Iron-Proxy answers a
    missing or wrong bearer token that way, and an account that needs to sign
    in WITH a profileId). It returns the ``(url, token)`` proxy.json names now,
    or ``None``; when that differs (the proxy restarted) the client rebinds and
    retries ONCE. Otherwise the call fails with "Iron-Proxy refused Iron
    Jarvis's access token." — never read as an account needing sign-in.
    """

    def __init__(
        self,
        url: str,
        token: str,
        timeout: float = 5.0,
        *,
        on_unreachable: Callable[[], None] | None = None,
        refresh: Callable[[], "tuple[str, str] | None"] | None = None,
        on_refused: Callable[[], None] | None = None,
    ) -> None:
        self.timeout = float(timeout)
        self._on_unreachable = on_unreachable
        self._refresh = refresh
        self._on_refused = on_refused
        #: Serialises a token re-read + rebind: two threads refused at once do
        #: ONE re-read, and the second retries on the first one's binding.
        self._bind_lock = threading.Lock()
        self._bind(url, token)

    def _bind(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self._http = httpx.Client(
            base_url=self.url,
            timeout=self.timeout,
            headers={"authorization": f"Bearer {token}"},
            # Loopback only: an HTTP(S)_PROXY in the user's environment must
            # never see the bearer token or the account list.
            trust_env=False,
        )

    def __repr__(self) -> str:
        return f"IronProxyClient(url={self.url!r})"

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001 — closing never raises
            pass

    # ------------------------------------------------------------ transport
    def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        _retried: bool = False,
    ) -> Any:
        http, sent_token = self._http, self._token
        try:
            resp = http.request(
                method,
                path,
                json=json,
                params={k: v for k, v in (params or {}).items() if v is not None} or None,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except httpx.HTTPError as exc:
            # The exception text names the URL at most; the token rides a
            # header and is never in it. Still: say it in our own words.
            if self._on_unreachable is not None:
                try:
                    self._on_unreachable()
                except Exception:  # noqa: BLE001 — a hook never masks the error
                    pass
            raise IronProxyError(
                "UNREACHABLE",
                f"Iron-Proxy did not answer at {self.url} ({type(exc).__name__}).",
            ) from None
        if resp.status_code >= 400:
            err = _error_from_response(resp)
            if (
                err.status == 401
                and err.code == "AUTH_REQUIRED"
                and not err.details.get("profileId")
            ):
                if not _retried and self._refresh is not None:
                    with self._bind_lock:
                        if self._token != sent_token:
                            rebound = True  # another thread already re-read it
                        else:
                            try:
                                fresh = self._refresh()
                            except Exception:  # noqa: BLE001 — a failed re-read is "no news"
                                fresh = None
                            rebound = bool(fresh) and (
                                fresh[0].rstrip("/"), fresh[1]  # type: ignore[index]
                            ) != (self.url, self._token)
                            if rebound:
                                # The old httpx client is dropped, not closed:
                                # another thread may still be mid-request on it.
                                self._bind(*fresh)  # type: ignore[misc]
                    if rebound:
                        return self._call(
                            method, path, json=json, params=params, timeout=timeout,
                            _retried=True,
                        )
                if self._on_refused is not None:
                    try:
                        self._on_refused()
                    except Exception:  # noqa: BLE001 — a hook never masks the error
                        pass
                raise IronProxyError(
                    "AUTH_REQUIRED",
                    "Iron-Proxy refused Iron Jarvis's access token.",
                    status=401,
                )
            raise err
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            raise IronProxyError(
                "BAD_RESPONSE",
                f"Iron-Proxy answered {resp.status_code} with something that is not JSON.",
                status=resp.status_code,
            ) from None

    @staticmethod
    def _id(profile_id: str) -> str:
        return quote(str(profile_id), safe="")

    # ------------------------------------------------------------- reads
    def health(self) -> dict[str, Any]:
        return self._call("GET", "/iron/health")

    def profiles(self) -> list[dict[str, Any]]:
        return self._call("GET", "/iron/profiles")

    def states(self) -> dict[str, dict[str, Any]]:
        return self._call("GET", "/iron/states")

    def discover(self, timeout: float | None = None) -> list[dict[str, Any]]:
        """Existing vendor-CLI logins on this PC. Iron-Proxy runs each CLI's own
        status command, so this can take seconds — pass a longer ``timeout``."""
        return self._call("GET", "/iron/discover", timeout=timeout)

    def usage(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        return self._call("GET", "/iron/usage", params={"profileId": profile_id})

    def login_command(self, profile_id: str) -> dict[str, Any]:
        return self._call("GET", f"/iron/profiles/{self._id(profile_id)}/login-command")

    # ------------------------------------------------------------ writes
    def create_profile(self, provider: str, title: str, lane: str = "cli") -> dict[str, Any]:
        return self._call(
            "POST", "/iron/profiles", json={"provider": provider, "title": title, "lane": lane}
        )

    def update_profile(self, profile_id: str, **patch: Any) -> dict[str, Any]:
        """PARTIAL: only the keys passed are sent (``enabled``/``title``)."""
        unknown = sorted(set(patch) - set(_PATCHABLE))
        if unknown:
            raise ValueError(f"update_profile takes only {_PATCHABLE}; got {unknown}")
        return self._call("PATCH", f"/iron/profiles/{self._id(profile_id)}", json=patch)

    def delete_profile(self, profile_id: str) -> Any:
        return self._call("DELETE", f"/iron/profiles/{self._id(profile_id)}")

    def reorder(self, provider: str, ids: list[str]) -> list[dict[str, Any]]:
        return self._call(
            "POST", "/iron/profiles/reorder", json={"provider": provider, "ids": list(ids)}
        )

    def unpark(self, profile_id: str) -> Any:
        return self._call("POST", f"/iron/profiles/{self._id(profile_id)}/unpark")

    def adopt(self, provider: str, home: str, title: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"provider": provider, "home": home}
        if title:
            body["title"] = title
        return self._call("POST", "/iron/adopt", json=body)

    def logout(self, profile_id: str) -> Any:
        return self._call("POST", f"/iron/profiles/{self._id(profile_id)}/logout")

    # ------------------------------------------- the external-executor API
    def pick(self, provider: str, lane: str = "cli") -> dict[str, Any]:
        """``{"profile": Profile, "env": {"set": {...}, "unset": [...]}}`` — the
        account a call should run as right now. Raises ``NO_PROFILE``,
        ``ALL_PROFILES_EXHAUSTED`` (``details.resetAt``) or ``AUTH_REQUIRED``
        (``details.profileId``/``title``)."""
        return self._call("GET", "/iron/pick", params={"provider": provider, "lane": lane})

    def signal(
        self,
        profile_id: str,
        status: int | None = None,
        headers: dict[str, str] | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if status is not None:
            body["status"] = int(status)
        if headers:
            body["headers"] = {str(k).lower(): str(v) for k, v in headers.items()}
        if text is not None:
            body["text"] = text
        return self._call("POST", f"/iron/profiles/{self._id(profile_id)}/signal", json=body)

    def finished(
        self,
        profile_id: str,
        usage: dict[str, int] | None = None,
        duration_ms: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if usage:
            body["usage"] = dict(usage)
        if duration_ms is not None:
            body["durationMs"] = int(duration_ms)
        if model:
            body["model"] = model
        return self._call("POST", f"/iron/profiles/{self._id(profile_id)}/finished", json=body)
