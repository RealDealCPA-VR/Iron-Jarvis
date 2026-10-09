"""Apps that talk back — the chat-turn side (v1.324.0, wave C; ideas from the
MCP spec 2025-06-18: elicitation, sampling, progress, resources — no code
copied).

A pack (an installed MCP server, an "app" in UI words) may, WHILE one of its
tools runs, ask the user a question (``elicitation/create``), ask a model a
question for its own use (``sampling/createMessage``) or report progress
(``notifications/progress``). The transport worker thread hands those to
``mcp.interact``, which finds the :class:`InteractionScope` the chat lane set
around ``registry.invoke`` and runs this module's coroutines ON THE TURN'S
LOOP. Everything the user must see goes onto the turn's SIDE QUEUE as an
``(event, data)`` pair; ``chat_turn._run_tool_round`` yields it while the tool
is still running and the stream lane writes it as an SSE frame.

RULES (binding, pinned in ``tests/test_mcp_chat_lane_v1324.py``):

* ONE :class:`TurnInteractions` per attended STREAM turn. POST /chat, agent
  runs, schedules and every other door build none, so a pack there is
  answered ``decline`` / -1 by ``mcp.interact`` at once.
* A pending ask WAITS — no clock — until the user answers it, the turn is
  stopped (``stop`` is the stream lane's own predicate: the named turn's Stop
  or a dropped connection), or the turn ends (:meth:`close`); each of those
  resolves it (``mcp_resolved``) and pops it from :data:`PENDING`.
* Elicitation: a request ``interact.elicitation_fields`` cannot show (url
  mode, nested objects, ...) is DECLINED at once, with no card.
* Sampling is answered ONLY by the provider/model the turn's last round
  routed to (``route()``), explicitly, through the platform router — never a
  failover the turn did not take, never another provider (v1.162.0) — and
  ONLY after the user pressed Allow on the card for THAT request. Low trust,
  the demo model, no text, a denial, a stop or a model error → the JSON-RPC
  error ``{"code": -1, "message": <one plain sentence>}``. Billed like any
  chat model call (``chat_turn._persist_chat_usage``).
* Resources the user attached (``ChatBody.resources``) are read by
  :func:`read_turn_resources` — the ONE helper BOTH lanes call at the
  attachments seam (lock-step).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

#: How often a parked ask checks Stop / a dropped connection (seconds). The
#: stream lane passes its own ``_ASK_POLL_S`` so the two waits agree.
ASK_POLL_S = 1.0
#: Card caps (the SSE contract).
MESSAGE_CAP = 2_000
SYSTEM_CAP = 2_000
SAMPLE_TEXT_CAP = 4_000
SAMPLE_MESSAGES_CAP = 20
#: A bound on the one model call a sampling request makes (the router's own
#: timeouts still apply). The WAIT for the user has no clock; this does.
SAMPLING_TIMEOUT_S = 120.0

#: The JSON-RPC code the spec gives "user rejected".
REJECTED = -1

LOW_TRUST_REASON = (
    "This chat turn is running at low trust, so apps may not ask its model questions."
)
MOCK_REASON = "The demo model is answering this chat, so it cannot answer an app."
NO_MODEL_REASON = "No model has answered in this chat turn yet, so there is none to ask."
NO_TEXT_REASON = "Only text questions can be sent to the model, and this request had no text."
DENIED_REASON = "The user did not allow this question to be sent to the model."
STOPPED_REASON = "The chat turn was stopped before the question was answered."
NO_ROUTER_REASON = "No model is connected, so the question could not be answered."
MODEL_FAILED_REASON = "The model did not answer the question."
MODEL_SLOW_REASON = "The model took too long to answer the question."
OTHER_MODEL_REASON = (
    "A different model than this chat's answered, so the answer was not used."
)


@contextmanager
def interaction_scope(scope: Any):
    """``mcp.interact.interaction_scope`` — imported at use, so this module
    (and the chat lane importing it) loads without the MCP side."""
    from ..mcp.interact import interaction_scope as _scope

    with _scope(scope):
        yield scope


def _rejected(message: str) -> dict[str, Any]:
    return {"error": {"code": REJECTED, "message": message}}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


@dataclass
class _Ask:
    """One pending question, by id, in :data:`PENDING`."""

    id: str
    kind: str                       # "elicitation" | "sampling"
    turn: "TurnInteractions"
    loop: asyncio.AbstractEventLoop
    fut: "asyncio.Future[tuple[str, Any]]"
    pack: str
    call_id: str
    fields: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    answered: bool = False

    def resolve(self, outcome: str, content: Any = None) -> bool:
        """Settle this ask from ANY thread (a route may run on another loop
        in a test client). False when it was already settled."""
        with self.lock:
            if self.answered:
                return False
            self.answered = True

        def _set() -> None:
            if not self.fut.done():
                self.fut.set_result((outcome, content))

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            _set()
        else:
            try:
                self.loop.call_soon_threadsafe(_set)
            except RuntimeError:  # the turn's loop is gone — nothing waits
                return False
        return True


#: Every pending ask of every live stream turn, by id — the routes resolve
#: through this. An entry leaves when its ask is settled (answered, stopped,
#: turn ended), so a finished id is honestly unknown (404).
PENDING: dict[str, _Ask] = {}
_PENDING_LOCK = threading.Lock()


def find(ask_id: str) -> _Ask | None:
    with _PENDING_LOCK:
        return PENDING.get(str(ask_id or ""))


def _drop(ask_id: str) -> None:
    with _PENDING_LOCK:
        PENDING.pop(ask_id, None)


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def _block_text(block: Any) -> str:
    """The text of one MCP content block ("" for a non-text block)."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict) and str(block.get("type", "text")) == "text":
        text = block.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _message_text(content: Any) -> str:
    """A sampling message's text: one block (the 2025-06-18 shape) or a list
    of blocks (tolerated); non-text blocks contribute nothing."""
    if isinstance(content, list):
        return "\n".join(t for t in (_block_text(b) for b in content) if t)
    return _block_text(content)


def sampling_request(params: Any) -> tuple[str, list[dict[str, str]], int]:
    """``(system, messages, more)`` — what the card shows AND exactly what the
    model is sent (the user approves what they see). Messages without text
    are dropped; at most :data:`SAMPLE_MESSAGES_CAP` of the NEWEST are kept,
    the rest counted in ``more``; each text ≤ :data:`SAMPLE_TEXT_CAP`."""
    params = params if isinstance(params, dict) else {}
    system = params.get("systemPrompt")
    system = system[:SYSTEM_CAP] if isinstance(system, str) else ""
    rows: list[dict[str, str]] = []
    for m in params.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user").strip().lower()
        role = role if role in ("user", "assistant") else "user"
        text = _message_text(m.get("content"))
        if not text.strip():
            continue
        rows.append({"role": role, "text": text[:SAMPLE_TEXT_CAP]})
    more = max(0, len(rows) - SAMPLE_MESSAGES_CAP)
    return system, rows[-SAMPLE_MESSAGES_CAP:] if more else rows, more


def _max_tokens(params: dict[str, Any]) -> int:
    try:
        return max(0, int(params.get("maxTokens") or 0))
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# the per-turn object
# --------------------------------------------------------------------------- #


class TurnInteractions:
    """The pack→user side channel of ONE attended stream turn.

    ``d`` — the lane's ``SimpleNamespace(platform=...)``.
    ``stop`` — the lane's stop predicate (sync or async), checked every
    ``poll_s`` while an ask is parked.
    ``route`` — returns ``(provider, model)`` the turn's last round routed to.
    ``low_trust`` — returns True when the turn is (now) low trust; read live,
    so a mid-turn taint refuses the next sampling request.
    """

    def __init__(
        self,
        d: Any,
        *,
        stop: Callable[[], Any] | None = None,
        route: Callable[[], tuple[str, str]] | None = None,
        low_trust: Callable[[], bool] | None = None,
        poll_s: float = ASK_POLL_S,
    ) -> None:
        self.d = d
        self.side: asyncio.Queue = asyncio.Queue()
        self._stop = stop
        self.route: Callable[[], tuple[str, str]] = route or (lambda: ("", ""))
        self.low_trust: Callable[[], bool] = low_trust or (lambda: False)
        self.poll_s = float(poll_s) if poll_s and poll_s > 0 else ASK_POLL_S
        self.closed = False
        self._mine: dict[str, _Ask] = {}

    # -- frames ------------------------------------------------------------ #

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        try:
            self.side.put_nowait((event, data))
        except Exception:  # noqa: BLE001 — a frame must never break a pack call
            log.debug("mcp side frame dropped", exc_info=True)

    def progress(self, frame: dict[str, Any]) -> None:
        """``scope.progress`` — called ON THE LOOP by ``mcp.interact``."""
        if self.closed or not isinstance(frame, dict):
            return
        total = frame.get("total")
        try:
            progress = float(frame.get("progress") or 0)
        except (TypeError, ValueError):
            return
        self._emit("mcp_progress", {
            "call_id": str(frame.get("call_id") or ""),
            "pack": str(frame.get("pack") or ""),
            "progress": progress,
            "total": float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None,
            "message": str(frame.get("message") or "")[:MESSAGE_CAP],
        })

    def scope_for(self, call_id: str):
        """The :class:`~iron_jarvis.mcp.interact.InteractionScope` the lane
        sets around ``registry.invoke`` of the model's call ``call_id``."""
        from ..mcp.interact import InteractionScope

        cid = str(call_id or "")

        async def _elicit(pack: str, params: dict) -> dict:
            return await self.elicit(cid, pack, params)

        async def _sample(pack: str, params: dict) -> dict:
            return await self.sample(cid, pack, params)

        return InteractionScope(
            loop=asyncio.get_running_loop(),
            call_id=cid,
            elicit=_elicit,
            sample=_sample,
            progress=self.progress,
        )

    # -- waiting ----------------------------------------------------------- #

    async def _should_stop(self) -> bool:
        if self.closed:
            return True
        if self._stop is None:
            return False
        try:
            res = self._stop()
            if inspect.isawaitable(res):
                res = await res
            return bool(res)
        except Exception:  # noqa: BLE001 — a broken predicate never strands an ask
            return False

    def _open(self, kind: str, pack: str, call_id: str, fields=None) -> _Ask:
        loop = asyncio.get_running_loop()
        ask = _Ask(
            id=_new_id("el" if kind == "elicitation" else "sa"),
            kind=kind, turn=self, loop=loop, fut=loop.create_future(),
            pack=str(pack or ""), call_id=call_id, fields=list(fields or []),
        )
        with _PENDING_LOCK:
            PENDING[ask.id] = ask
        self._mine[ask.id] = ask
        return ask

    async def _wait(self, ask: _Ask) -> tuple[str, Any]:
        """Park until the ask is settled. No clock: an answer, Stop, a
        dropped connection or the turn's end are the only ways out."""
        try:
            while True:
                try:
                    return await asyncio.wait_for(asyncio.shield(ask.fut), timeout=self.poll_s)
                except asyncio.TimeoutError:
                    if await self._should_stop():
                        ask.resolve("stopped")
                        # resolve() on this loop sets the result at once.
                        return await asyncio.shield(ask.fut)
        finally:
            _drop(ask.id)
            self._mine.pop(ask.id, None)
            if not ask.fut.done():
                # Cancelled (the pack's call gave up): the card goes away.
                ask.resolve("stopped")
                self._emit("mcp_resolved", {"id": ask.id, "kind": ask.kind, "outcome": "stopped"})

    def close(self) -> None:
        """The turn is over: every ask still parked is resolved ``stopped``
        (the pack gets cancel / -1) and nothing new may park."""
        self.closed = True
        for ask in list(self._mine.values()):
            ask.resolve("stopped")
        for ask_id in list(self._mine):
            _drop(ask_id)

    # -- elicitation ------------------------------------------------------- #

    async def elicit(self, call_id: str, pack: str, params: dict) -> dict:
        """``elicitation/create`` → the MCP result ``{action, content?}``."""
        from ..mcp.interact import elicitation_fields

        params = params if isinstance(params, dict) else {}
        try:
            fields, reason = elicitation_fields(params)
        except Exception:  # noqa: BLE001 — an unreadable request is declined
            fields, reason = [], "the request could not be read"
        if reason:
            log.info("pack %r asked a question that cannot be shown (%s); declined", pack, reason)
            return {"action": "decline"}
        if self.closed:
            return {"action": "cancel"}
        ask = self._open("elicitation", pack, call_id, fields)
        message = params.get("message")
        self._emit("mcp_elicitation", {
            "id": ask.id,
            "call_id": call_id,
            "pack": ask.pack,
            "message": (message if isinstance(message, str) else "")[:MESSAGE_CAP],
            "fields": fields,
        })
        outcome, content = await self._wait(ask)
        self._emit("mcp_resolved", {"id": ask.id, "kind": "elicitation", "outcome": outcome})
        if outcome == "accept":
            return {"action": "accept", "content": dict(content or {})}
        if outcome == "decline":
            return {"action": "decline"}
        return {"action": "cancel"}  # cancel / stopped

    # -- sampling ---------------------------------------------------------- #

    async def sample(self, call_id: str, pack: str, params: dict) -> dict:
        """``sampling/createMessage`` → the MCP result or the -1 error."""
        params = params if isinstance(params, dict) else {}
        if self.closed:
            return _rejected(STOPPED_REASON)
        try:
            low = bool(self.low_trust())
        except Exception:  # noqa: BLE001 — unknown trust is not full trust
            low = True
        if low:
            return _rejected(LOW_TRUST_REASON)
        provider, model = self.route()
        provider, model = str(provider or "").strip(), str(model or "").strip()
        if not provider:
            return _rejected(NO_MODEL_REASON)
        if provider.lower() == "mock":
            return _rejected(MOCK_REASON)
        system, messages, more = sampling_request(params)
        if not messages:
            return _rejected(NO_TEXT_REASON)
        ask = self._open("sampling", pack, call_id)
        frame: dict[str, Any] = {
            "id": ask.id,
            "call_id": call_id,
            "pack": ask.pack,
            "system": system,
            "messages": messages,
            "max_tokens": _max_tokens(params),
            "model": provider,
            "model_id": model,
        }
        if more:
            frame["more"] = more
        self._emit("mcp_sampling", frame)
        outcome, _ = await self._wait(ask)
        self._emit("mcp_resolved", {"id": ask.id, "kind": "sampling", "outcome": outcome})
        if outcome == "stopped":
            return _rejected(STOPPED_REASON)
        if outcome != "approved":
            return _rejected(DENIED_REASON)
        return await self._ask_model(provider, model, system, messages)

    async def _ask_model(
        self, provider: str, model: str, system: str, messages: list[dict[str, str]]
    ) -> dict:
        platform = getattr(self.d, "platform", None)
        router = getattr(platform, "router", None)
        if router is None:
            return _rejected(NO_ROUTER_REASON)
        from ..providers.adapters.base import LLMMessage

        bound = asyncio.timeout(SAMPLING_TIMEOUT_S)
        try:
            async with bound:
                route = await router.complete(
                    # The turn's OWN provider and model, explicitly — never
                    # the default route, never a provider the turn did not use.
                    provider=provider,
                    model=model or None,
                    system=system,
                    messages=[LLMMessage(role=m["role"], content=m["text"]) for m in messages],
                    tools=[],  # never None: adapters iterate it
                    task_class="chat",
                    # Answered by THIS model or refused — a failover would
                    # send the pack's text to a provider the user never
                    # chose (v1.162.0). The check below stays as a backstop.
                    pin=True,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a model failure is a -1, never a crash
            if isinstance(exc, TimeoutError) and bound.expired():
                return _rejected(MODEL_SLOW_REASON)
            log.info("pack sampling: the model call failed (%s)", type(exc).__name__)
            return _rejected(MODEL_FAILED_REASON)
        await asyncio.to_thread(_bill, self.d, route)
        served = str(getattr(route, "provider", "") or "").strip()
        if served.lower() == "mock" or str(getattr(route, "reason", "") or "").lower() == "mock":
            return _rejected(MOCK_REASON)
        if served != provider:
            return _rejected(OTHER_MODEL_REASON)
        response = getattr(route, "response", None)
        text = getattr(response, "text", "") or ""
        try:
            from .chat_turn import _truncated_by

            truncated = _truncated_by(response)
        except Exception:  # noqa: BLE001
            truncated = False
        return {
            "role": "assistant",
            "content": {"type": "text", "text": text},
            "model": str(getattr(route, "model", "") or model),
            "stopReason": "maxTokens" if truncated else "endTurn",
        }


def _bill(d: Any, route: Any) -> None:
    """One ``session_id="chat"`` run row, like a chat completion."""
    usage = getattr(getattr(route, "response", None), "usage", None) or {}
    try:
        from ..core.models import AgentState
        from ..eval.pricing import UsageTally
        from .chat_turn import _persist_chat_usage

        provider = str(getattr(route, "provider", "") or "")
        model = str(getattr(route, "model", "") or "")
        tally = UsageTally()
        tally.add(provider, model, usage)
        _persist_chat_usage(
            d, provider=provider, model=model, state=AgentState.COMPLETED,
            completions=1, usage_in=tally.input_tokens, usage_out=tally.output_tokens,
            cost_usd=tally.cost_usd,
        )
    except Exception:  # noqa: BLE001 — accounting never fails the answer
        log.debug("pack sampling usage not recorded", exc_info=True)


# --------------------------------------------------------------------------- #
# resources the user attached (BOTH lanes, lock-step)
# --------------------------------------------------------------------------- #

#: At most this many resources ride one message (``ChatBody.resources``).
MAX_RESOURCES = 8
#: Each resource's text is cut to this many characters (head kept).
RESOURCE_CHARS = 20_000
#: Each read is bounded (the transport call itself runs off the loop).
RESOURCE_READ_TIMEOUT_S = 15.0


def _live_client(pack: str):
    from ..mcp import tools as _mcp_tools

    getter = getattr(_mcp_tools, "live_client", None)
    return getter(pack) if callable(getter) else None


def _resource_text(result: Any) -> str:
    parts: list[str] = []
    contents = result.get("contents") if isinstance(result, dict) else None
    for item in contents or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif "blob" in item:
            mime = str(item.get("mimeType") or "application/octet-stream")
            parts.append(f"[binary {mime} omitted]")
    return "\n\n".join(parts)


async def _read_one(d: Any, pack: str, uri: str, name: str) -> tuple[str, dict[str, Any]]:
    label = name or uri
    receipt: dict[str, Any] = {"pack": pack, "uri": uri, "ok": False, "note": ""}
    client = _live_client(pack)
    if client is None or not callable(getattr(client, "read_resource", None)):
        receipt["note"] = f"The app {pack} is not running, so {label} was not read."
        return "", receipt
    try:
        result = await asyncio.wait_for(client.read_resource(uri), RESOURCE_READ_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        receipt["note"] = f"The app {pack} took too long to send {label}."
        return "", receipt
    except Exception as exc:  # noqa: BLE001 — one failed read never ends the turn
        log.info("resource %r from pack %r not read: %s", uri, pack, type(exc).__name__)
        receipt["note"] = f"The app {pack} could not send {label}."
        return "", receipt
    text = _resource_text(result)
    notes: list[str] = []
    if len(text) > RESOURCE_CHARS:
        notes.append(
            f"It was cut to the first {RESOURCE_CHARS:,} of {len(text):,} characters."
        )
        text = text[:RESOURCE_CHARS] + f"\n[... cut: the rest of {label} was not included]"
    from ..core.promptguard import publish_blocked, scan_context

    scan = await asyncio.to_thread(
        scan_context, text, source=f"{pack} resource {label}", cap=None
    )
    text = scan.text
    if scan.blocked:
        publish_blocked(getattr(getattr(d, "platform", None), "event_bus", None), "chat", scan)
        notes.append("Part of it was removed as a suspected prompt injection.")
    receipt["ok"] = True
    receipt["note"] = " ".join(notes)
    block = f"\n\n## Resource from {pack}: {label}\n{text}"
    return block, receipt


async def read_turn_resources(d: Any, body: Any) -> tuple[str, list[dict[str, Any]]]:
    """``(block, receipt)`` for the resources the user attached to THIS
    message. ``block`` joins the system prompt at the attachments seam;
    ``receipt`` is ``[{pack, uri, ok, note}]`` — the done frame / POST
    response carry it ALWAYS (``[]`` with none). A read that fails is
    ``ok: false`` with one plain sentence and the turn continues."""
    refs = list(getattr(body, "resources", None) or [])[:MAX_RESOURCES]
    if not refs:
        return "", []
    blocks: list[str] = []
    receipt: list[dict[str, Any]] = []
    for ref in refs:
        pack = str(getattr(ref, "pack", None) or (ref.get("pack") if isinstance(ref, dict) else "") or "").strip()
        uri = str(getattr(ref, "uri", None) or (ref.get("uri") if isinstance(ref, dict) else "") or "").strip()
        name = str(getattr(ref, "name", None) or (ref.get("name") if isinstance(ref, dict) else "") or "").strip()
        block, row = await _read_one(d, pack, uri, name)
        if block:
            blocks.append(block)
        receipt.append(row)
    return "".join(blocks), receipt


__all__ = [
    "PENDING",
    "TurnInteractions",
    "find",
    "read_turn_resources",
    "sampling_request",
]
