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
import re
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

#: 10-a: ``(url, headers | None, max_bytes)`` -> the body as bytes. Streams and
#: stops at ``max_bytes`` (raising :class:`FileTooLarge`), so a file from the
#: phone is never held past the cap. Tests inject a recorder; production
#: injects :func:`httpx_get_bytes`.
HttpGetBytes = Callable[[str, "dict[str, str] | None", int], bytes]

#: 10-b: ``(url, fields, file | None, headers | None)`` -> response-ish. A
#: multipart POST: ``fields`` are form fields, ``file`` is ``(field name,
#: file name, bytes, media type)`` or ``None`` (a plain form POST, which every
#: Slack Web API method takes). Tests inject a recorder; production injects
#: :func:`httpx_post_file`. Unset, an upload is refused in words.
HttpPostFile = Callable[
    [str, "dict[str, Any]", "tuple[str, str, bytes, str] | None", "dict[str, str] | None"], Any
]

#: 10-a: the kinds of file a two-way destination can bring.
FILE_KINDS = ("photo", "document", "voice", "audio", "video")


@dataclass
class Press:
    """A button pressed under one of Iron Jarvis's own messages (10-b).

    ``data`` is what the button carries (``ij:...``, ``comm/prompts``);
    ``ref`` the service's handle for the press itself (Telegram's callback
    query id, answered so the button stops spinning); ``chat`` and
    ``message`` say which message to change once it is answered (Telegram's
    chat id and message id, Slack's channel id and ``ts``); ``text`` is that
    message's own text (Slack's update needs it again)."""

    data: str
    ref: str = ""
    chat: Any = None
    message: str = ""
    text: str = ""


class FileNotTaken(Exception):
    """A file from the phone could not be taken (10-a). ``words`` is the
    reason in plain words, said to the sender after the file's name. It is
    written by Iron Jarvis, never copied from a transport error, so it can
    never carry a URL with a token in it (Telegram's file URL holds the bot
    token)."""

    def __init__(self, words: str) -> None:
        super().__init__(words)
        self.words = words


class FileTooLarge(FileNotTaken):
    """The file is larger than what is taken from the phone (10-a)."""


@dataclass
class InboundFile:
    """One file that came with an inbound message (10-a).

    ``kind`` is one of :data:`FILE_KINDS`; ``size`` is what the service said
    (``None`` when it did not say); ``ref`` is the channel's own handle for
    it (Telegram's ``file_id``, Slack's download address on its file host,
    an email part's number), never a URL with a secret in it. ``handle`` is
    the channel's private object (an email part, read only when the file is
    fetched); ``smaller`` lists ``(ref, size)`` of smaller copies of the same
    picture, largest first (Telegram sends a photo in several sizes)."""

    kind: str
    name: str
    mime: str = ""
    size: int | None = None
    ref: str = ""
    handle: Any = field(default=None, repr=False, compare=False)
    smaller: list[tuple[str, "int | None"]] = field(default_factory=list, repr=False)


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
    #: v1.444.0: the reply goes to a place OTHER people may read — the Slack
    #: destination's own channel (a thread under the user's message). Only the
    #: Slack Socket Mode pump sets it, and only after checking that the message
    #: came from an allowlisted person in the channel the destination posts to.
    #: The poller's private-chat guard lets such a message through; every other
    #: message keeps the old rule (reply_to must be the sender's own chat).
    own_channel: bool = False
    #: R0.3: how many files came with the message that Iron Jarvis cannot
    #: open (since 10-a: a Slack file with no download, such as a link to a
    #: Google Doc). The pipeline says so to an allowlisted sender in their own
    #: chat (``InboundPoller._take_files``); the words still run.
    files_not_read: int = 0
    #: 10-a: the files that came with the message, in order. Nothing is
    #: downloaded at poll time: ``InboundPoller._take_files`` fetches them
    #: (``Channel.fetch_file``) only after the allowlist, the sender's proof
    #: and the private-chat guard.
    files: list[InboundFile] = field(default_factory=list)
    #: 10-a: the words the sender wrote, when ``text`` carries more (an
    #: e-mail: its body, where ``text`` is the subject and then the body).
    #: ``None``: all of ``text`` is theirs. A voice memo sent as a file is
    #: heard when the message has no words of its own, and the subject of a
    #: shared recording is usually only its name ("New Recording 3"). An
    #: e-mail's is its body without what the mail app adds by itself
    #: (:func:`without_signoff`).
    own_words: str | None = None
    #: 10-b: a button press, not a message (``text`` is empty). Read only
    #: after the allowlist and the private-chat guard, like everything else:
    #: a stranger's press resolves nothing.
    press: Press | None = None


#: 10-a: the line a mail app puts under an e-mail by itself, in the app's own
#: words ("Sent from my iPhone", "Get Outlook for iOS"), compared in lower
#: case with spaces collapsed and a final full stop dropped. A small, fixed
#: list: a line the person typed (their name, a phone number) is never guessed
#: at, so it stays their words.
_SIGNOFFS = re.compile(
    r"sent from my (iphone|ipad|ipod( touch)?|apple watch|mac"
    r"|android( phone| device)?|(samsung )?galaxy( [a-z0-9+-]+){0,3}"
    r"|(google )?pixel( [a-z0-9+-]+){0,2}|blackberry( [a-z0-9+-]+){0,3}"
    r"|huawei( [a-z0-9+-]+){0,3}|mobile( device| phone)?|phone|smartphone|tablet)"
    r"|sent from (mail|outlook) for (windows( \d+)?|ios|android|mac)"
    r"|sent from outlook"
    r"|sent from yahoo mail (for|on) (iphone|ipad|android)"
    r"|sent from gmail mobile"
    r"|sent from proton ?mail( mobile| for (ios|android))?"
    r"|sent with proton ?mail secure email"
    r"|get outlook for (ios|android)"
    r"|von meinem (iphone|ipad) gesendet"
    r"|envoyé de mon (iphone|ipad)"
    r"|enviado desde mi (iphone|ipad)"
    r"|enviado do meu (iphone|ipad)"
    r"|inviato da (il mio )?(iphone|ipad)"
    r"|verstuurd vanaf mijn (iphone|ipad)"
)


def _is_signoff(line: str) -> bool:
    # Outlook's plain text carries its link: "Get Outlook for iOS<https://...>".
    s = re.sub(r"<https?://[^>\s]*>", "", str(line or ""))
    s = " ".join(s.split()).rstrip(".!").strip().lower()
    return bool(s) and _SIGNOFFS.fullmatch(s) is not None


#: 10-a: the English line a mail app puts above the earlier e-mail a reply
#: quotes ("On 22 Nov 2026, at 09:00, Iron Jarvis <...> wrote:"). Only ever
#: read directly above a quoted ``>`` block; another language's line is not
#: guessed at, so it stays the sender's words.
_ATTRIBUTION = re.compile(r"on\s.+\swrote:", re.IGNORECASE | re.DOTALL)


def without_signoff(text: str, *, quoted_reply: bool = False) -> str:
    """``text`` without what a mail app adds under it by itself (10-a): a
    signature under a ``-- `` line (``--`` once trailing spaces are trimmed),
    and a last line that is one of the known automatic sign-offs
    (:data:`_SIGNOFFS`). With ``quoted_reply`` (an e-mail's body) also the
    earlier e-mail a reply quotes at its end: the ``>`` lines and, directly
    above them, the English line that introduces them (:data:`_ATTRIBUTION`,
    once wrapped onto two lines). Everything the person typed stays, a line
    between two quotes too. It decides only whether a message carries words of
    its own; the words a turn gets are never changed by it."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, line in enumerate(lines):
        if line.rstrip() == "--":
            lines = lines[:i]
            break
    quote = False
    while lines:
        last = lines[-1].strip()
        if not last or _is_signoff(last):
            lines.pop()
        elif quoted_reply and last.startswith(">"):
            lines.pop()
            quote = True
        elif quote and _ATTRIBUTION.fullmatch(last):
            lines.pop()
            quote = False
        elif quote and len(lines) > 1 and _ATTRIBUTION.fullmatch(f"{lines[-2].strip()} {last}"):
            del lines[-2:]
            quote = False
        else:
            break
    return "\n".join(lines).strip()


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


def _no_get_bytes(url: str, headers: Any, max_bytes: int) -> bytes:
    raise FileNotTaken("this destination cannot download files")


def _mb(n: float) -> str:
    """``3 MB`` / ``0.5 MB``: a size in the words the phone hears."""
    mb = float(n) / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 1 else f"{max(mb, 0.1):.1f} MB"


def too_large_words(size: "int | None", max_bytes: int) -> str:
    """Why a file over the cap was not taken, in the phone's words."""
    if size:
        return f"it is {_mb(size)}, and the most taken from your phone is {_mb(max_bytes)}"
    return f"it is over the {_mb(max_bytes)} taken from your phone"


def httpx_get_bytes(url: str, headers: "dict[str, str] | None", max_bytes: int) -> bytes:
    """Default production transport for a file from the phone (10-a): GET
    ``url`` STREAMING, and stop the moment the body passes ``max_bytes``.

    Raises :class:`FileTooLarge` (the declared length or the bytes read
    passed the cap) or :class:`FileNotTaken` (a refusing status, a dropped
    connection). The words are Iron Jarvis's own: the URL may hold a token
    (Telegram's file address holds the bot token), so neither it nor the
    transport's own message is ever put in them, and ``from None`` keeps the
    original exception out of any traceback that is logged. BLOCKING: run it
    through ``asyncio.to_thread``. Imported lazily, like :func:`httpx_post`.
    """
    import httpx

    try:
        with httpx.stream(
            "GET",
            url,
            headers=dict(headers) if headers else None,
            timeout=httpx.Timeout(60.0, connect=5.0),
            follow_redirects=True,
        ) as resp:
            if resp.status_code >= 400:
                raise FileNotTaken(f"the service answered {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise FileTooLarge(too_large_words(int(declared), max_bytes))
            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise FileTooLarge(too_large_words(None, max_bytes))
            return bytes(buf)
    except FileNotTaken:
        raise
    except Exception as exc:  # noqa: BLE001 — never the message: it may name the URL
        raise FileNotTaken(f"the download failed ({type(exc).__name__})") from None


def _no_post_file(url: str, fields: Any, file: Any, headers: Any) -> Any:
    raise FileNotTaken("this destination cannot send files")


def httpx_post_file(
    url: str,
    fields: "dict[str, Any]",
    file: "tuple[str, str, bytes, str] | None",
    headers: "dict[str, str] | None" = None,
) -> Any:
    """Default production transport for a file sent to the phone (10-b): a
    multipart POST of ``fields`` and ``file`` (``(field, name, bytes,
    type)``), or a plain form POST when ``file`` is ``None``. BLOCKING: run it
    through ``asyncio.to_thread``. The URL may hold a token (Telegram's
    address holds the bot token): the caller rewords every failure, and this
    raises only the transport's own exception. Imported lazily."""
    import httpx

    data = {str(k): str(v) for k, v in (fields or {}).items() if v is not None}
    files = None
    if file is not None:
        field, name, blob, mime = file
        files = {str(field): (str(name), bytes(blob), str(mime or "application/octet-stream"))}
    return httpx.post(
        url,
        data=data,
        files=files,
        headers=dict(headers) if headers else None,
        timeout=httpx.Timeout(120.0, connect=5.0),
    )


def upload_failed_words(exc: BaseException) -> str:
    """Why an upload did not go, in Iron Jarvis's own words (10-b): never the
    transport's message, which may name an address that holds a token."""
    if isinstance(exc, FileNotTaken):
        return exc.words
    return f"the upload failed ({type(exc).__name__})"


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


class ChannelAuthError(Exception):
    """The service REFUSED the channel's credential (v1.231.0, audit AE8).

    A revoked/rotated Telegram bot token answers ``getUpdates`` with 401/403
    and an IMAP server refuses ``LOGIN``; both used to be flattened into
    ``([], offset)`` — indistinguishable from "no new messages" — so the
    inbound loop reported a healthy poll every 15 s forever while the phone
    was dead. A poll raises this instead; the poller records it per channel
    and the loop ticks ``ok=False`` with the words.
    """


def auth_refusal(resp: Any) -> str | None:
    """``"HTTP 401: Unauthorized"``-style detail when ``resp`` is a credential
    refusal (status 401/403 on an httpx-style response, or a Telegram body
    ``{"ok": false, "error_code": 401|403}``), else ``None``."""
    if resp is None:
        return None
    code: Any = None
    desc = ""
    if isinstance(resp, dict):
        if resp.get("ok") is False:
            code = resp.get("error_code", resp.get("status_code", resp.get("status")))
            desc = str(resp.get("description") or resp.get("error") or resp.get("detail") or "")
    else:
        code = getattr(resp, "status_code", None)
        if code is not None:
            getter = getattr(resp, "json", None)
            body: Any = None
            if callable(getter):
                try:
                    body = getter()
                except Exception:  # noqa: BLE001 — a non-JSON body still has a status
                    body = None
            if isinstance(body, dict):
                desc = str(body.get("description") or body.get("error") or "")
            if not desc:
                desc = str(getattr(resp, "text", "") or "")[:120]
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        return None
    if code not in (401, 403):
        return None
    return f"HTTP {code}: {desc or ('Forbidden' if code == 403 else 'Unauthorized')}"


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

    #: 10-b: whether this destination can carry a finished file back to the
    #: phone (:meth:`send_files`), and buttons under a question
    #: (``send(..., buttons=...)`` and :meth:`answer_press`).
    supports_files: bool = False
    supports_buttons: bool = False
    #: 10-b: how this destination is named in a sentence ("to Telegram").
    display_name: str = ""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        http_post: HttpPost | None = None,
        http_get: HttpGet | None = None,
        secret_resolver: SecretResolver | None = None,
        http_get_bytes: HttpGetBytes | None = None,
        http_post_file: HttpPostFile | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._http_post: HttpPost = http_post or _no_transport
        self._http_get: HttpGet = http_get or _no_get
        self._secret_resolver: SecretResolver = secret_resolver or (lambda _k: None)
        #: 10-a: the file download (streams, stops at the cap). Unset, a
        #: fetch is refused in words; nothing falls back to a network call.
        self._http_get_bytes: HttpGetBytes = http_get_bytes or _no_get_bytes
        #: 10-b: the upload (a multipart POST). Unset, a send is refused in
        #: words; nothing falls back to a network call.
        self._http_post_file: HttpPostFile = http_post_file or _no_post_file

    # -- helpers ---------------------------------------------------------
    def _resolve_secret(self, secret_name: str | None) -> str | None:
        if not secret_name:
            return None
        return self._secret_resolver(secret_name)

    def _get_raw(self, url: str, params: dict[str, Any]) -> Any:
        """GET via the injected transport; the RAW response (or ``None`` when
        the transport itself failed). A poll that must tell a refused
        credential from an empty batch reads the status off this — see
        :func:`auth_refusal` — before :func:`interpret_json` flattens a 401
        into ``None`` (v1.231.0, audit AE8)."""
        try:
            return self._http_get(url, params)
        except Exception:  # a transport failure must never raise to the poller
            return None

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """GET via the injected transport and normalise to a JSON dict (or None)."""
        return interpret_json(self._get_raw(url, params))

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

    def _get_bytes(self, url: str, headers: "dict[str, str] | None", max_bytes: int) -> bytes:
        """Download through the injected transport (10-a). Any failure is a
        :class:`FileNotTaken` in Iron Jarvis's own words, raised ``from None``:
        the URL may hold a token, so no transport message or traceback of
        one ever leaves here."""
        try:
            data = self._http_get_bytes(url, headers, int(max_bytes))
        except FileTooLarge as exc:
            raise FileTooLarge(exc.words) from None
        except FileNotTaken as exc:
            raise FileNotTaken(exc.words) from None
        except Exception as exc:  # noqa: BLE001 — never its message
            raise FileNotTaken(f"the download failed ({type(exc).__name__})") from None
        if not isinstance(data, (bytes, bytearray)):
            raise FileNotTaken("the download did not return a file")
        if len(data) > max_bytes:
            raise FileTooLarge(too_large_words(len(data), max_bytes))
        return bytes(data)

    # -- files from the phone (10-a) ------------------------------------
    def fetch_file(self, f: InboundFile, max_bytes: int) -> bytes:
        """The bytes of ``f``, at most ``max_bytes``. BLOCKING (a download):
        the pipeline calls it through ``asyncio.to_thread``, and only after
        the allowlist, the sender's proof and the private-chat guard. Raises
        :class:`FileNotTaken` with the reason in words; a destination with no
        receive leg for files refuses."""
        raise FileNotTaken("files can't be taken from this destination")

    # -- files back to the phone (10-b) ----------------------------------
    def _post_file(
        self, url: str, fields: dict[str, Any], file: "tuple[str, str, bytes, str] | None",
        headers: "dict[str, str] | None" = None,
    ) -> Any:
        """Upload through the injected transport (10-b). Any failure is a
        :class:`FileNotTaken` in Iron Jarvis's own words, raised ``from None``
        (the address may hold a token); returns the response-ish answer."""
        try:
            return self._http_post_file(url, fields, file, headers)
        except FileNotTaken as exc:
            raise FileNotTaken(exc.words) from None
        except Exception as exc:  # noqa: BLE001 — never its message
            raise FileNotTaken(upload_failed_words(exc)) from None

    def send_file(self, path: str, *, chat_id: Any = None, caption: str = "") -> dict[str, Any]:
        """Send the file at ``path`` to ``chat_id`` (10-b): ``{"ok", "detail"}``,
        the detail in words. BLOCKING (a read and an upload): the pipeline
        calls it through ``asyncio.to_thread``, and only for a file inside
        the chat's or the run's folders (``agents/reply_files``). A
        destination that cannot carry files refuses."""
        return self._fail("this destination can't take files")

    def send_files(
        self, paths: list[str], *, chat_id: Any = None, caption: str = ""
    ) -> list[dict[str, Any]]:
        """Send each of ``paths`` (10-b), one answer per path, in order. The
        caption rides the first. Email overrides this to send them in one
        message. BLOCKING."""
        out: list[dict[str, Any]] = []
        for i, p in enumerate(paths):
            try:
                out.append(self.send_file(p, chat_id=chat_id, caption=caption if i == 0 else ""))
            except Exception as exc:  # noqa: BLE001 — one file never sinks the rest
                out.append(self._fail(upload_failed_words(exc)))
        return out

    def answer_press(self, press: Press, label: str) -> bool:
        """Answer a button press (10-b): the buttons under the message become
        ``label`` ("Approved by you"). True when the service took it.
        BLOCKING. A destination with no buttons has nothing to answer."""
        return False

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
