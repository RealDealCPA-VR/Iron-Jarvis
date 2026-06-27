"""Communication channel base (§ integrations / notifications).

A :class:`Channel` is a user-choosable destination for outbound messages
(Slack, Telegram, Discord, ...). Every channel is fully dependency-injected so
the platform stays testable **offline**:

* ``http_post`` — a ``Callable[[str, dict], Any]`` (url, json -> response-ish).
  Channels never import a network library directly; they build a target URL and
  payload and hand it to this callable. Tests inject a recorder; production
  injects :func:`httpx_post`.
* ``secret_resolver`` — a ``Callable[[str], str | None]`` used to look up tokens
  by name (wired to the secrets/keychain layer). Channels never embed secrets in
  config; they store a *secret name* and resolve it at send time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

#: (url, json_payload) -> response-ish. Response may be an ``httpx.Response``,
#: a ``{"status_code": int, "text"?: str}`` dict, or a ``{"ok": bool}`` dict.
HttpPost = Callable[[str, dict[str, Any]], Any]

#: secret name -> secret value (or ``None`` when unknown / not configured).
SecretResolver = Callable[[str], "str | None"]


def _no_transport(url: str, payload: dict[str, Any]) -> Any:  # pragma: no cover
    raise RuntimeError("no http_post transport configured for this channel")


def httpx_post(url: str, payload: dict[str, Any]) -> Any:
    """Default production transport — POST ``payload`` as JSON via httpx.

    Imported lazily so the comm package imports cleanly even where httpx is
    unavailable; tests never reach this path (they inject their own callable).
    """
    import httpx

    # Short connect timeout so an unreachable/offline destination fails fast
    # (~2s) instead of stalling its worker thread for the full window.
    return httpx.post(url, json=payload, timeout=httpx.Timeout(15.0, connect=2.0))


def interpret_response(resp: Any) -> tuple[bool, str]:
    """Normalise a ``http_post`` return value into ``(ok, detail)``.

    Supports httpx-style responses (``.status_code`` / ``.text``) and the two
    plain-dict contracts above. Unknown shapes are treated as success.
    """
    if resp is None:
        return True, "sent"
    if isinstance(resp, dict):
        if "ok" in resp:
            ok = bool(resp["ok"])
            return ok, str(resp.get("detail", resp.get("text", "ok" if ok else "failed")))
        status = resp.get("status_code", resp.get("status"))
        if status is not None:
            ok = 200 <= int(status) < 300
            return ok, f"HTTP {status}"
        return True, "sent"
    status = getattr(resp, "status_code", None)
    if status is not None:
        ok = 200 <= int(status) < 300
        if ok:
            return True, f"HTTP {status}"
        text = getattr(resp, "text", "") or ""
        return False, f"HTTP {status}: {text[:200]}".rstrip(": ")
    return True, "sent"


class Channel(ABC):
    """Abstract outbound message channel.

    Subclasses set :attr:`name` and implement :meth:`send`, building their own
    target URL + payload and delegating the actual POST to ``self._http_post``.
    """

    #: stable channel-type identity (e.g. ``"slack"``).
    name: str = ""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        http_post: HttpPost | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._http_post: HttpPost = http_post or _no_transport
        self._secret_resolver: SecretResolver = secret_resolver or (lambda _k: None)

    # -- helpers ---------------------------------------------------------
    def _resolve_secret(self, secret_name: str | None) -> str | None:
        if not secret_name:
            return None
        return self._secret_resolver(secret_name)

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST via the injected transport and normalise the result."""
        try:
            resp = self._http_post(url, payload)
        except Exception as exc:  # transport failure must not raise to caller
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        ok, detail = interpret_response(resp)
        return {"ok": ok, "detail": detail}

    @staticmethod
    def _fail(detail: str) -> dict[str, Any]:
        return {"ok": False, "detail": detail}

    # -- contract --------------------------------------------------------
    @abstractmethod
    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        """Send ``message``; return ``{"ok": bool, "detail": str}``."""
        ...
