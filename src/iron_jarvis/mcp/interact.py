"""Two-way MCP: a pack that ASKS the user, ASKS a model, or REPORTS progress
(v1.324.0, wave C — "apps that talk back"; ideas from the MCP spec 2025-06-18,
no third-party code copied).

A pack (an MCP server the user installed) may, in the middle of a
``tools/call``, send the client its own requests:

* ``elicitation/create`` — "ask the user these questions" (form mode only);
* ``sampling/createMessage`` — "ask a model this for me";
* and the notification ``notifications/progress`` — "this far along".

Who answers is decided by an :class:`InteractionScope`, set by the chat lane
around ONE tool call (``with interaction_scope(ti.scope_for(tc.id)):``). The
transports run on a WORKER THREAD (``MCPClient._request`` offloads them with
``asyncio.to_thread``, which copies the calling context), so the scope set on
the loop is visible there through :data:`MCP_SCOPE`. With NO scope — every
surface but an attended stream turn — an elicitation is declined and a
sampling request refused with ``-1`` at once: nobody is there to answer.

Every function here can see bad input from a pack, and none of them may
raise into the transport: a reply that never goes back leaves the pack
waiting for it, which hangs the call until its deadline.
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import contextvars
import math
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Iterator
from urllib.parse import urlsplit

from ..core.logging import get_logger

log = get_logger("mcp")

#: JSON-RPC "method not found" — the answer to a server→client request this
#: client does not serve (v1.322.0).
METHOD_NOT_FOUND = -32601
#: The spec's "user rejected" code for a refused sampling request.
SAMPLING_REFUSED = -1

#: How long one wait on the loop lasts before the cancel token is checked.
WAIT_SLICE_S = 0.25

#: Progress frames delivered per call id per rolling second.
PROGRESS_PER_SECOND = 4

#: The most questions one elicitation may ask.
MAX_ELICIT_FIELDS = 20

#: The words a pack sees when no one can answer it.
NO_SCOPE_SAMPLING = (
    "Iron Jarvis lets an app ask a model only during a chat the user is watching."
)
GAVE_UP_SAMPLING = "The chat turn ended before this request was answered."
FAILED_SAMPLING = "Iron Jarvis could not answer this request."

_PROP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_FIELD_TYPES = ("string", "number", "integer", "boolean")
_FORMATS = ("email", "uri", "date", "date-time")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([Zz]|[+-]\d{2}:\d{2})?$"
)


# --------------------------------------------------------------------------- #
# The scope.
# --------------------------------------------------------------------------- #
@dataclass
class InteractionScope:
    """Who answers a pack during ONE tool call of an attended chat turn."""

    #: The chat turn's loop: every callable below runs ON it.
    loop: asyncio.AbstractEventLoop
    #: The model's tool-call id — also the call's progress token.
    call_id: str
    #: ``(pack, params) -> MCP result dict`` ({"action": ..., "content"?}).
    elicit: Callable[[str, dict], Awaitable[dict]]
    #: ``(pack, params) -> MCP result dict`` or ``{"error": {"code": -1, "message": str}}``.
    sample: Callable[[str, dict], Awaitable[dict]]
    #: Called ON THE LOOP with one ``mcp_progress`` frame dict.
    progress: Callable[[dict], None]


MCP_SCOPE: "contextvars.ContextVar[InteractionScope | None]" = contextvars.ContextVar(
    "iron_jarvis_mcp_scope", default=None
)


@contextmanager
def interaction_scope(scope: InteractionScope) -> Iterator[InteractionScope]:
    """Set :data:`MCP_SCOPE` for the body of the ``with`` (and reset it after)."""
    token = MCP_SCOPE.set(scope)
    try:
        yield scope
    finally:
        MCP_SCOPE.reset(token)


def current_scope() -> InteractionScope | None:
    """The scope of the call in flight in THIS context, or ``None``."""
    try:
        return MCP_SCOPE.get()
    except Exception:  # noqa: BLE001 — never raises
        return None


# --------------------------------------------------------------------------- #
# Replies.
# --------------------------------------------------------------------------- #
def _result(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def plain_reply(msg: dict[str, Any]) -> dict[str, Any]:
    """The v1.322.0 reply to a server→client request: ``ping`` → ``{}``,
    anything else -32601 — so a server is never left WAITING on an answer
    that will not come."""
    method = str(msg.get("method", ""))
    if method == "ping":
        return _result(msg.get("id"), {})
    log.info("mcp server sent a %r request this client does not serve; refused it", method)
    return _error(
        msg.get("id"),
        METHOD_NOT_FOUND,
        f"Method not found: {method} (this client does not serve it)",
    )


def _wait_on_loop(
    scope: InteractionScope,
    make: Callable[[], Awaitable[Any]],
    cancel: threading.Event | None,
) -> tuple[str, Any]:
    """Run ``make()`` on the scope's loop and wait for it from THIS thread.

    Returns ``("ok", value)``, ``("gave_up", None)`` (cancel set, loop gone,
    or the coroutine was cancelled) or ``("error", exc)``. Waits in
    :data:`WAIT_SLICE_S` slices so a Stop / deadline (``cancel``) is seen
    within one slice; on giving up the future is cancelled, which cancels the
    coroutine on the loop. Never raises.
    """
    loop = scope.loop
    try:
        if asyncio.get_running_loop() is loop:
            # Called ON the loop (an async transport): waiting here would
            # deadlock the very loop that must answer.
            return "gave_up", None
    except RuntimeError:
        pass  # the normal case: a worker thread with no loop of its own
    coro: Any = None
    try:
        coro = make()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 — a closed loop, a bad callable
        close = getattr(coro, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        return "error", exc
    while True:
        try:
            return "ok", fut.result(timeout=WAIT_SLICE_S)
        except concurrent.futures.TimeoutError:
            gone = loop.is_closed() or not loop.is_running()
            if gone or (cancel is not None and cancel.is_set()):
                fut.cancel()
                return "gave_up", None
        except concurrent.futures.CancelledError:
            return "gave_up", None
        except Exception as exc:  # noqa: BLE001 — the answerer raised
            return "error", exc


def _clean_elicit_result(value: Any) -> dict[str, Any]:
    """An answerer's result as the spec shape, or a decline."""
    if not isinstance(value, dict):
        return {"action": "decline"}
    action = value.get("action")
    if action == "accept":
        content = value.get("content")
        return {"action": "accept", "content": dict(content) if isinstance(content, dict) else {}}
    if action in ("decline", "cancel"):
        return {"action": action}
    return {"action": "decline"}


def _serve_elicitation(
    pack: str, rid: Any, params: dict[str, Any], cancel: threading.Event | None
) -> dict[str, Any]:
    scope = current_scope()
    if scope is None:
        return _result(rid, {"action": "decline"})
    _fields, reason = elicitation_fields(params)
    if reason is not None:
        # Url mode, a nested object, too many questions …: nothing the card
        # can show, so the pack hears "no" at once rather than wait.
        log.info("pack %r asked something Iron Jarvis cannot show (%s); declined", pack, reason)
        return _result(rid, {"action": "decline"})
    status, value = _wait_on_loop(scope, lambda: scope.elicit(pack, params), cancel)
    if status == "error":
        log.warning("pack %r: answering its question failed: %s", pack, value)
    if status != "ok":
        return _result(rid, {"action": "decline"})
    return _result(rid, _clean_elicit_result(value))


def _serve_sampling(
    pack: str, rid: Any, params: dict[str, Any], cancel: threading.Event | None
) -> dict[str, Any]:
    scope = current_scope()
    if scope is None:
        return _error(rid, SAMPLING_REFUSED, NO_SCOPE_SAMPLING)
    status, value = _wait_on_loop(scope, lambda: scope.sample(pack, params), cancel)
    if status == "gave_up":
        return _error(rid, SAMPLING_REFUSED, GAVE_UP_SAMPLING)
    if status == "error" or not isinstance(value, dict):
        if status == "error":
            log.warning("pack %r: answering its model request failed: %s", pack, value)
        return _error(rid, SAMPLING_REFUSED, FAILED_SAMPLING)
    err = value.get("error")
    if err is not None:
        code = err.get("code") if isinstance(err, dict) else None
        message = err.get("message") if isinstance(err, dict) else None
        return _error(
            rid,
            code if isinstance(code, int) and not isinstance(code, bool) else SAMPLING_REFUSED,
            message if isinstance(message, str) and message else FAILED_SAMPLING,
        )
    return _result(rid, value)


def serve_server_request(
    pack: str, msg: dict, cancel: threading.Event | None = None
) -> dict:
    """The full JSON-RPC reply envelope for one server→client request.

    Runs on the TRANSPORT WORKER THREAD. ``ping`` → ``{}``;
    ``elicitation/create`` and ``sampling/createMessage`` → through
    :func:`current_scope` on the scope's loop, waiting in
    :data:`WAIT_SLICE_S` slices and giving up (future cancelled; decline /
    ``-1``) when ``cancel`` is set; no scope → decline / ``-1`` at once;
    anything else → -32601 as in v1.322.0. Never raises.
    """
    try:
        if not isinstance(msg, dict):
            return _error(None, -32600, "Invalid request")
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "elicitation/create":
            return _serve_elicitation(str(pack), rid, params, cancel)
        if method == "sampling/createMessage":
            return _serve_sampling(str(pack), rid, params, cancel)
        return plain_reply(msg)
    except Exception as exc:  # noqa: BLE001 — a reply must ALWAYS go back
        log.warning("pack %r: could not answer its request: %s", pack, exc)
        rid = msg.get("id") if isinstance(msg, dict) else None
        return _error(rid, -32603, "Internal error")


# --------------------------------------------------------------------------- #
# Progress.
# --------------------------------------------------------------------------- #
#: Clock for the throttle (a test pins it).
_now: Callable[[], float] = time.monotonic
_progress_lock = threading.Lock()
_progress_sent: "dict[str, collections.deque[float]]" = {}
_PROGRESS_KEEP = 512


def _reset_progress_throttle() -> None:
    with _progress_lock:
        _progress_sent.clear()


def _progress_allowed(call_id: str) -> bool:
    now = _now()
    with _progress_lock:
        sent = _progress_sent.get(call_id)
        if sent is None:
            if len(_progress_sent) >= _PROGRESS_KEEP:
                # Forget the call that went quiet longest (bounded memory).
                oldest = min(_progress_sent, key=lambda k: _progress_sent[k][-1])
                _progress_sent.pop(oldest, None)
            sent = _progress_sent[call_id] = collections.deque(maxlen=PROGRESS_PER_SECOND)
        while sent and now - sent[0] >= 1.0:
            sent.popleft()
        if len(sent) >= PROGRESS_PER_SECOND:
            return False
        sent.append(now)
        return True


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def handle_notification(pack: str, msg: dict) -> None:
    """One server notification, on the worker thread. ``notifications/progress``
    for the scope's OWN token becomes an ``mcp_progress`` frame on the loop
    (at most :data:`PROGRESS_PER_SECOND` per call per second); everything else
    is ignored. Never raises."""
    try:
        if not isinstance(msg, dict) or msg.get("method") != "notifications/progress":
            return
        scope = current_scope()
        if scope is None:
            return
        params = msg.get("params")
        if not isinstance(params, dict):
            return
        token = params.get("progressToken")
        if isinstance(token, bool) or not isinstance(token, (str, int)):
            return
        if str(token) != str(scope.call_id):
            return
        progress = _number(params.get("progress"))
        if progress is None:
            return
        total = _number(params.get("total"))
        message = params.get("message")
        frame = {
            "call_id": scope.call_id,
            "pack": str(pack),
            "progress": progress,
            "total": total,
            "message": message[:500] if isinstance(message, str) else "",
        }
        if not _progress_allowed(str(scope.call_id)):
            return
        scope.loop.call_soon_threadsafe(scope.progress, frame)
    except Exception as exc:  # noqa: BLE001 — a closed loop, a bad scope
        log.debug("pack %r: progress notification dropped: %s", pack, exc)


# --------------------------------------------------------------------------- #
# Elicitation forms (PURE).
# --------------------------------------------------------------------------- #
def _type_ok(kind: str, value: Any) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if kind == "integer":
        return isinstance(value, int) or (
            isinstance(value, float) and math.isfinite(value) and value.is_integer()
        )
    if kind == "number":
        return _number(value) is not None
    return False


def _field(name: str, prop: Any, required: bool) -> tuple[dict[str, Any] | None, str | None]:
    if not _PROP_NAME_RE.match(name):
        return None, f"the question name {name[:40]!r} is not one Iron Jarvis accepts"
    if not isinstance(prop, dict):
        return None, f"the question {name!r} is not described"
    kind = prop.get("type")
    one_of = prop.get("oneOf")
    enum = prop.get("enum")
    enum_names = prop.get("enumNames", prop.get("enum_names"))
    if kind is None and enum is None and isinstance(one_of, list):
        kind = "string"
    if kind is None and isinstance(enum, list) and enum and all(isinstance(v, str) for v in enum):
        kind = "string"
    if kind == "object":
        return None, f"the question {name!r} has questions inside it"
    if kind == "array":
        return None, f"the question {name!r} asks for a list"
    if kind not in _FIELD_TYPES:
        return None, f"the question {name!r} has a type Iron Jarvis cannot show"
    title = prop.get("title")
    description = prop.get("description")
    field: dict[str, Any] = {
        "name": name,
        "type": kind,
        "title": title[:200] if isinstance(title, str) and title.strip() else name,
        "description": description[:500] if isinstance(description, str) else "",
        "required": required,
    }
    if enum is None and isinstance(one_of, list):
        # The titled-choice shape: oneOf [{const, title}].
        enum, enum_names = [], []
        for opt in one_of:
            if not isinstance(opt, dict) or "const" not in opt:
                return None, f"the choices for {name!r} are not readable"
            enum.append(opt["const"])
            t = opt.get("title")
            enum_names.append(t if isinstance(t, str) and t else str(opt["const"]))
    if enum is not None:
        if (
            not isinstance(enum, list)
            or not enum
            or len(enum) > 200
            or not all(_type_ok(kind, v) for v in enum)
            or kind == "boolean"
        ):
            return None, f"the choices for {name!r} are not readable"
        field["enum"] = list(enum)
        if isinstance(enum_names, list) and len(enum_names) == len(enum) and all(
            isinstance(n, str) for n in enum_names
        ):
            field["enum_names"] = [n[:200] for n in enum_names]
    if kind == "string":
        fmt = prop.get("format")
        if fmt in _FORMATS:
            field["format"] = fmt
        for src, dst in (("minLength", "min_length"), ("maxLength", "max_length")):
            v = prop.get(src)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                field[dst] = v
    if kind in ("number", "integer"):
        for key in ("minimum", "maximum"):
            v = _number(prop.get(key))
            if v is not None:
                field[key] = v
    if "default" in prop and _type_ok(kind, prop["default"]):
        field["default"] = prop["default"]
    return field, None


def elicitation_fields(params: dict) -> tuple[list[dict], str | None]:
    """Normalise an ``elicitation/create`` request's ``requestedSchema`` into
    the card's ``fields`` list — or ``([], reason)`` when it cannot be shown
    (url mode, nested objects, arrays other than an enum's choices, more than
    :data:`MAX_ELICIT_FIELDS` questions, a question name outside
    ``^[A-Za-z0-9_.-]{1,64}$``). PURE; never raises."""
    try:
        if not isinstance(params, dict):
            return [], "the request is not readable"
        mode = params.get("mode")
        if mode is not None and mode != "form":
            return [], "the app asked to open a web page, which Iron Jarvis does not do"
        schema = params.get("requestedSchema")
        if schema is None:
            return [], None  # a plain yes/no: only the message
        if not isinstance(schema, dict):
            return [], "the questions are not readable"
        if schema.get("type", "object") != "object":
            return [], "the questions are not readable"
        props = schema.get("properties", {})
        if props is None:
            props = {}
        if not isinstance(props, dict):
            return [], "the questions are not readable"
        if len(props) > MAX_ELICIT_FIELDS:
            return [], f"the app asked more than {MAX_ELICIT_FIELDS} questions at once"
        required_raw = schema.get("required") or []
        required = (
            {r for r in required_raw if isinstance(r, str)}
            if isinstance(required_raw, list)
            else set()
        )
        fields: list[dict] = []
        for name, prop in props.items():
            field, reason = _field(str(name), prop, str(name) in required)
            if reason is not None:
                return [], reason
            fields.append(field)  # type: ignore[arg-type]
        return fields, None
    except Exception:  # noqa: BLE001 — PURE and total
        return [], "the questions are not readable"


def _format_ok(fmt: str, value: str) -> bool:
    if fmt == "email":
        return bool(_EMAIL_RE.match(value))
    if fmt == "uri":
        parts = urlsplit(value)
        return bool(parts.scheme and _SCHEME_RE.match(parts.scheme) and (parts.netloc or parts.path))
    if fmt == "date":
        if not _DATE_RE.match(value):
            return False
        date.fromisoformat(value)
        return True
    if fmt == "date-time":
        if not _DATETIME_RE.match(value):
            return False
        datetime.fromisoformat(value.replace("z", "Z").replace("t", "T"))
        return True
    return True


_FORMAT_WORDS = {
    "email": "This must be an email address.",
    "uri": "This must be a web address (for example https://example.com).",
    "date": "This must be a date (YYYY-MM-DD).",
    "date-time": "This must be a date and time (YYYY-MM-DDTHH:MM).",
}
_TYPE_WORDS = {
    "string": "This must be text.",
    "number": "This must be a number.",
    "integer": "This must be a whole number.",
    "boolean": "This must be yes or no.",
}


def _fmt_num(v: Any) -> str:
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def _check_one(field: dict, value: Any) -> str | None:
    kind = field.get("type")
    if not _type_ok(str(kind), value):
        return _TYPE_WORDS.get(str(kind), "This answer is not the right kind.")
    enum = field.get("enum")
    if isinstance(enum, list) and value not in enum:
        return "Pick one of the offered choices."
    if kind in ("number", "integer"):
        lo, hi = _number(field.get("minimum")), _number(field.get("maximum"))
        if lo is not None and value < lo:
            return f"This must be at least {_fmt_num(lo)}."
        if hi is not None and value > hi:
            return f"This must be at most {_fmt_num(hi)}."
    if kind == "string":
        lo, hi = field.get("min_length"), field.get("max_length")
        if isinstance(lo, int) and not isinstance(lo, bool) and len(value) < lo:
            return f"This must be at least {lo} characters."
        if isinstance(hi, int) and not isinstance(hi, bool) and len(value) > hi:
            return f"This must be at most {hi} characters."
        fmt = field.get("format")
        if isinstance(fmt, str) and fmt in _FORMATS:
            try:
                ok = _format_ok(fmt, value)
            except Exception:  # noqa: BLE001 — fromisoformat said no
                ok = False
            if not ok:
                return _FORMAT_WORDS[fmt]
    return None


def check_elicitation_answer(fields: list[dict], content: dict) -> dict[str, str]:
    """``{field_name: one plain sentence}`` for every problem in an accepted
    answer: a missing required answer (absent, ``None`` or — for text — an
    empty string), the wrong type, a value outside the choices, a number out
    of ``minimum``/``maximum``, text out of ``min_length``/``max_length``, a
    bad email / uri / date / date-time, and any answer to a question that was
    not asked. ``{}`` = valid. Coerces nothing. PURE; never raises."""
    errors: dict[str, str] = {}
    answers = content if isinstance(content, dict) else {}
    known: set[str] = set()
    for field in fields if isinstance(fields, list) else []:
        try:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                continue
            name = field["name"]
            known.add(name)
            value = answers.get(name)
            missing = value is None or (field.get("type") == "string" and value == "")
            if missing:
                if field.get("required"):
                    errors[name] = "This answer is required."
                continue
            problem = _check_one(field, value)
            if problem:
                errors[name] = problem
        except Exception:  # noqa: BLE001 — PURE and total
            errors[str(field.get("name", "?")) if isinstance(field, dict) else "?"] = (
                "This answer could not be checked."
            )
    for key in answers:
        if str(key) not in known:
            errors[str(key)] = "This app did not ask for this."
    return errors


__all__ = [
    "InteractionScope",
    "MCP_SCOPE",
    "interaction_scope",
    "current_scope",
    "serve_server_request",
    "handle_notification",
    "elicitation_fields",
    "check_elicitation_answer",
    "plain_reply",
    "METHOD_NOT_FOUND",
    "SAMPLING_REFUSED",
    "WAIT_SLICE_S",
    "PROGRESS_PER_SECOND",
    "MAX_ELICIT_FIELDS",
]
