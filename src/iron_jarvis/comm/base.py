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

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

#: ``(url, json_payload[, headers])`` -> response-ish. The third argument is
#: OPTIONAL — a channel that needs request headers (Slack's
#: ``Authorization: Bearer``) passes them, everything else calls the two-arg
#: form, so legacy two-arg transports keep working. Response may be an
#: ``httpx.Response``, a ``{"status_code": int, "text"?: str}`` dict, or a
#: ``{"ok": bool}`` dict.
HttpPost = Callable[..., Any]

#: (url, query_params) -> response-ish carrying JSON (for inbound long-poll).
#: Response may be an ``httpx.Response`` (``.json()``) or a plain ``dict``.
HttpGet = Callable[[str, dict[str, Any]], Any]

#: secret name -> secret value (or ``None`` when unknown / not configured).
SecretResolver = Callable[[str], "str | None"]


@dataclass
class InboundMessage:
    """One inbound message received on a channel (the receive leg).

    ``sender_id`` is the channel-native, allowlist-checkable identity (e.g. a
    Telegram user/chat id, as a string). ``reply_to`` is whatever the channel
    needs to address a reply back (Telegram: the chat id). ``update_id`` drives
    the durable polling offset. ``is_bot`` lets the poller ignore the bot's own
    / other bots' messages (loop protection).
    """

    sender_id: str
    text: str
    update_id: int | None = None
    reply_to: Any = None
    is_bot: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def split_message(text: str, limit: int) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` chars for a surface with
    a hard message-size cap (Telegram: 4096).

    Prefers clean boundaries in order: paragraph break (``\\n\\n``), newline,
    space — so a chunk never ends mid-word when any word boundary exists inside
    the window. A single unbroken run longer than ``limit`` is hard-cut (the
    only honest option left). The separator that was cut on is dropped (the
    chunk boundary replaces it); all other whitespace is preserved verbatim.
    Reply senders (the inbound poller + the desktop fan-out route) call this
    with the channel's :attr:`Channel.chunk_limit`.
    """
    text = str(text if text is not None else "")
    limit = max(1, int(limit))
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = -1
        sep_len = 0
        for sep in ("\n\n", "\n", " "):
            # The separator must END within the window (idx + len(sep) <=
            # limit + 1 via rfind's end bound) and leave a non-empty chunk.
            idx = rest.rfind(sep, 1, limit + 1)
            if idx > 0:
                cut, sep_len = idx, len(sep)
                break
        if cut <= 0:  # one unbroken run — hard cut, never drop characters
            cut, sep_len = limit, 0
        out.append(rest[:cut])
        rest = rest[cut + sep_len :]
    if rest:
        out.append(rest)
    return out


def _no_transport(url: str, payload: dict[str, Any]) -> Any:  # pragma: no cover
    raise RuntimeError("no http_post transport configured for this channel")


def _no_get(url: str, params: dict[str, Any]) -> Any:  # pragma: no cover
    raise RuntimeError("no http_get transport configured for this channel")


def httpx_post(
    url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> Any:
    """Default production transport — POST ``payload`` as JSON via httpx.

    ``headers`` is optional and carries per-request headers a channel needs on
    the wire (Slack's ``Authorization: Bearer <bot token>``; the Slack Web API
    REFUSES a token passed as a JSON body field and answers ``200`` with
    ``{"ok": false, "error": "not_authed"}``).

    Imported lazily so the comm package imports cleanly even where httpx is
    unavailable; tests never reach this path (they inject their own callable).
    """
    import httpx

    # Short connect timeout so an unreachable/offline destination fails fast
    # (~2s) instead of stalling its worker thread for the full window.
    return httpx.post(
        url,
        json=payload,
        headers=dict(headers) if headers else None,
        timeout=httpx.Timeout(15.0, connect=2.0),
    )


def httpx_get(url: str, params: dict[str, Any]) -> Any:
    """Default production transport for the inbound long-poll (GET + JSON).

    The connect timeout fails fast offline; the read timeout is generous so a
    Telegram ``getUpdates`` long-poll (``timeout`` seconds server-side) can park
    without tripping the client. Imported lazily; tests inject their own.
    """
    import httpx

    server_timeout = float(params.get("timeout", 0) or 0)
    return httpx.get(
        url,
        params=params,
        timeout=httpx.Timeout(server_timeout + 15.0, connect=2.0),
    )


def _accepts_headers(transport: Any) -> bool:
    """Whether ``transport`` can be called as ``(url, payload, headers)``.

    The ``http_post`` contract grew an optional third argument, and channels are
    handed transports built elsewhere (tests inject two-arg recorders). A
    signature that cannot be inspected at all (a C builtin) is given the benefit
    of the doubt — the production transport is :func:`httpx_post`, which takes
    them.
    """
    try:
        inspect.signature(transport).bind("", {}, {})
    except TypeError:  # inspectable, and three arguments don't fit
        return False
    except Exception:  # noqa: BLE001 — uninspectable (C builtin) => try it
        return True
    return True


def interpret_json(resp: Any) -> dict[str, Any] | None:
    """Normalise an ``http_get`` return value into a JSON dict (or ``None``).

    Supports httpx-style responses (``.json()``, only on a 2xx status) and a
    plain dict (returned as-is). Anything else / any failure yields ``None`` so
    a polling caller fails safe (no messages) rather than raising.
    """
    if resp is None:
        return None
    if isinstance(resp, dict):
        return resp
    status = getattr(resp, "status_code", None)
    if status is not None and not (200 <= int(status) < 300):
        return None
    getter = getattr(resp, "json", None)
    if callable(getter):
        try:
            data = getter()
        except Exception:
            return None
        return data if isinstance(data, dict) else None
    return None


def envelope_error(resp: Any) -> str | None:
    """The error inside a **2xx** body that says the call FAILED, or ``None``.

    Slack (and Telegram) answer HTTP 200 for application-level failures and put
    the verdict in the body: ``{"ok": false, "error": "not_authed"}``. Judging
    such a response by status code alone reports a dropped message as delivered.

    Deliberately conservative — only an EXPLICIT falsey ``ok`` key counts as a
    failure. A body that is absent, empty, non-JSON (a Slack incoming webhook
    replies with the literal text ``ok``), not a dict, or simply carries no
    ``ok`` key leaves a 2xx a success.
    """
    getter = getattr(resp, "json", None)
    if not callable(getter):
        return None
    try:
        data = getter()
    except Exception:  # noqa: BLE001 — a non-JSON 2xx body is not a failure
        return None
    if not isinstance(data, dict) or "ok" not in data or bool(data.get("ok")):
        return None
    err = data.get("error") or data.get("description") or data.get("detail")
    return str(err) if err else "ok=false"


def interpret_response(resp: Any) -> tuple[bool, str]:
    """Normalise a ``http_post`` return value into ``(ok, detail)``.

    Supports httpx-style responses (``.status_code`` / ``.text``, plus the
    :func:`envelope_error` check on a 2xx body) and the two plain-dict contracts
    above. Unknown shapes are treated as success.
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
            failed = envelope_error(resp)
            if failed is not None:
                return False, f"HTTP {status}: {failed}"
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

    #: whether this channel type implements a receive/poll leg (overridden by
    #: subclasses that do, e.g. Telegram). Outbound-only channels stay ``False``
    #: so they are never polled.
    supports_inbound: bool = False

    #: Hard per-message size cap for chunked replies (:func:`split_message`).
    #: Conservative generic default; channels with a known platform limit
    #: override it (Telegram: 4096).
    chunk_limit: int = 3500

    #: which Reflex ``source`` an inbound message on this channel fires (CX-05).
    #: The generic chat channels stay ``"comm"``; channels that map to a distinct
    #: trigger source override it (EmailChannel -> ``"email"``, SlackChannel ->
    #: ``"slack"``) so a rule can scope to "an email arrived" vs "any comm
    #: message". Falls back to ``"comm"`` for any channel that doesn't set it.
    reflex_source: str = "comm"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        http_post: HttpPost | None = None,
        http_get: HttpGet | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._http_post: HttpPost = http_post or _no_transport
        self._http_get: HttpGet = http_get or _no_get
        self._secret_resolver: SecretResolver = secret_resolver or (lambda _k: None)

    # -- helpers ---------------------------------------------------------
    def _resolve_secret(self, secret_name: str | None) -> str | None:
        if not secret_name:
            return None
        return self._secret_resolver(secret_name)

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """GET via the injected transport and normalise to a JSON dict (or None)."""
        try:
            resp = self._http_get(url, params)
        except Exception:  # a transport failure must never raise to the poller
            return None
        return interpret_json(resp)

    def _post(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST via the injected transport and normalise the result.

        ``headers`` (Slack's ``Authorization: Bearer``) are only passed on when
        the caller supplies them, so a two-arg transport keeps working for every
        other channel. A transport that CANNOT take them fails closed: sending
        an auth header the channel asked for is not optional, and posting the
        request without it would be a request we already know is unauthorised.
        """
        try:
            if headers:
                if not _accepts_headers(self._http_post):
                    return {
                        "ok": False,
                        "detail": (
                            "http transport does not accept headers; "
                            f"{self.name or 'this channel'} needs them to authenticate"
                        ),
                    }
                resp = self._http_post(url, payload, headers)
            else:
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

    # -- inbound (receive) leg ------------------------------------------
    def poll(
        self, offset: int = 0, *, timeout: int = 0
    ) -> tuple[list[InboundMessage], int]:
        """Fetch new inbound messages since ``offset``.

        Returns ``(messages, next_offset)``. The base implementation has no
        receive leg, so it returns ``([], offset)``; channels that support
        inbound (e.g. Telegram) override this. Never raises — a transport
        failure yields no messages and an unchanged offset.
        """
        return [], offset

    # -- inbound config + authorization (off by default, fail-closed) ---
    def inbound_enabled(self) -> bool:
        """True only when this channel TYPE supports inbound AND the user has
        explicitly opted in via ``inbound_enabled = true`` in its config."""
        return self.supports_inbound and bool(self.config.get("inbound_enabled", False))

    def chat_enabled(self) -> bool:
        """True only when this destination is a FULL CHAT surface.

        Chat IMPLIES inbound: the flag is meaningless without a receive leg, so
        this requires the type to support inbound AND the user's explicit
        ``chat_enabled = true`` AND :meth:`inbound_enabled` (which itself
        requires ``supports_inbound`` + the ``inbound_enabled`` opt-in) — a
        ``chat_enabled`` toggle on its own, with two-way off, changes NOTHING
        (fail-closed, like every other inbound gate)."""
        return (
            self.supports_inbound
            and bool(self.config.get("chat_enabled", False))
            and self.inbound_enabled()
        )

    def allowed_senders(self) -> set[str]:
        """The configured sender allowlist (ids as strings); empty by default."""
        return {str(s) for s in (self.config.get("allowed_senders") or [])}

    def is_authorized(self, sender_id: Any) -> bool:
        """FAIL-CLOSED allowlist check: an empty/missing allowlist authorizes
        NOBODY. Only an explicitly listed ``sender_id`` is accepted."""
        allow = self.allowed_senders()
        return bool(allow) and str(sender_id) in allow

    def has_credentials(self) -> bool:
        """Whether the secret(s) this channel needs to receive resolve. Used by
        the poller so a channel toggled on but missing its token is skipped."""
        secret_name = self.config.get("token_secret")
        if secret_name is None:
            # An inbound-capable channel cannot poll without a token, so it is NOT
            # credentialed-to-receive even though pushing out may need no secret.
            return not self.supports_inbound
        return bool(self._resolve_secret(secret_name))
