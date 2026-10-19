"""An agent session's reply comes home to the chat it was asked in
(v1.345.0, lane T — "an @ reply comes home").

THE BUG this exists for (the user's install, 2026-10-16): a hand-off the chat
page opened (``POST /sessions`` / ``/agents/{slug}/spawn`` /
``/sessions/{id}/continue``) was finished by the PAGE: ``finalize()`` read
the session, composed the assistant line and ``PUT`` the whole array over
whatever chat was open. Leave for another chat while the run worked and the
reply either landed in the wrong conversation or never appeared at all.

Now every door the page opens from chat carries ``chat_thread_id`` (stored on
the row as ``options["chat_thread_id"]``), and EVERY terminal path of such a
session — ``run_session``'s success, ``_finalize_failed``,
``_finalize_cancelled`` and the boot reconcile's interrupted outcome —
appends ONE line to that chat through the daemon's one writer
(``daemon.chat_append.append_to_chat_thread``), idempotent by ``sessionId``.
The page then RE-READS the thread and renders what the daemon wrote (its
composing code stays as the fallback for an older daemon).

This module is the PURE half: it says which chat a session belongs to, who
the line is attributed to, and what the line looks like — MIRRORING the
page's own ``finalize()`` (dashboard/app/chat/page.tsx) exactly, so the
integrator can pin that a daemon-appended line and a page-composed line are
equal for the same session:

* ``content`` — the page's words: on ``completed`` the summary (trimmed) or
  ``"(no response)"``; otherwise the summary or (v1.352.0, G16) the plain
  sentence for HOW it ended — :data:`STOPPED_SENTENCE` for a run the user
  stopped (``cancelled``), :data:`FAILED_SENTENCE` for any other end;
* ``sessionId`` — the page's ``fromSession`` under the daemon's key (the
  page's ``lib/chatReread.adoptDaemonLines`` stands one in for the other);
* ``receipt`` — the page's ``runResult``: the ``GET /sessions/{id}/result``
  payload (``agents/outcome.session_result``), same adoption rule. Since
  v1.352.0 (G19) it is that route's RECEIPT read (``receipt=True``: the
  route note and the plain action lines ride along), so the card and the
  settled helper-agent row say the same whichever of the two wrote the
  line; ``on_this_network`` is kept only when the caller passed the
  provider manager it needs (judged without the endpoint settings a local
  model would read as cloud — absent beats wrong);
* ``panelWho`` — the participant key the page stores on a hand-off reply
  (``awaitingWhoRef``): ``builtin:<agent type>`` for a builtin,
  ``dynamic:<name>`` for a custom agent (the page's ``sessionTargetOf`` maps
  ``dynamic:`` back to ``custom:``), ``remote:<name>`` for a remote;
* ``agentType`` and ``documents`` (the result's absolute paths, only when
  there are any) — additive, read by nothing the page composes but kept so
  the line says where the files are without a second read.

The blocking half (``deliver_session_line``) reads the ledger and appends;
callers hop it off the loop (``asyncio.to_thread``) — the finalizers already
run their DB work there. It never raises: a delivery miss is logged, the run
is the point.
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)

#: The run option the chat thread id rides under (``Session.options_json``).
CHAT_THREAD_OPTION = "chat_thread_id"

#: The page's fallback words when a run the user STOPPED (``cancelled``)
#: left no summary (v1.352.0, G16: a stop is not a failure, so it no longer
#: reads like one — the page's ``finalize()`` says the same, pinned).
STOPPED_SENTENCE = "The agent was stopped before it finished."

#: The page's fallback words when a run that FAILED (or ended any other way
#: short of ``completed``) left no summary (v1.352.0, G16).
FAILED_SENTENCE = "The agent could not finish this. Try again or open the run."

#: The page's fallback words for a completed run with no summary.
NO_RESPONSE = "(no response)"


def clean_chat_thread_id(raw: object) -> str:
    """One trimmed id or "" — never a non-string, never whitespace."""
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def chat_thread_of(session: Any) -> str:
    """The chat thread a session was asked in (``options.chat_thread_id``),
    or "" for a run no chat is waiting on. Reads the row's options through
    the ONE reader (``scheduling.knobs.session_options``); junk reads "".
    """
    try:
        from ..scheduling.knobs import session_options

        options = session_options(session)
    except Exception:  # noqa: BLE001 — a junk column is "no chat", never a crash
        return ""
    if not isinstance(options, dict):
        return ""
    return clean_chat_thread_id(options.get(CHAT_THREAD_OPTION))


def participant_key_for(session: Any) -> str:
    """The participant key the chat page attributes a hand-off reply to —
    the same ``<source>:<name>`` ``POST /chat/panel`` answers as ``who`` for
    ``mode: "session"`` (``components/agents/identity.participantKey``).

    A session credited to a custom agent (``agent_name`` ``custom:<slug>``)
    is ``dynamic:<slug>``; a remote stays ``remote:<name>``; everything else
    is the builtin ``builtin:<agent type>``.
    """
    name = str(getattr(session, "agent_name", "") or "").strip()
    low = name.lower()
    if low.startswith("custom:"):
        slug = name[len("custom:"):].strip()
        if slug:
            return f"dynamic:{slug}"
    if low.startswith("remote:"):
        remote = name[len("remote:"):].strip()
        if remote:
            return f"remote:{remote}"
    agent_type = getattr(session, "agent_type", None)
    type_value = str(getattr(agent_type, "value", agent_type) or "").strip()
    return f"builtin:{type_value or 'builder'}"


def _status_value(session: Any) -> str:
    status = getattr(session, "status", None)
    return str(getattr(status, "value", status) or "").strip().lower()


def content_for_session(session: Any) -> str:
    """The reply text the page's ``finalize()`` shows for this session."""
    status = _status_value(session)
    summary = str(getattr(session, "summary", "") or "").strip()
    if status == "completed":
        return summary or NO_RESPONSE
    if status == "cancelled":
        return summary or STOPPED_SENTENCE
    return summary or FAILED_SENTENCE


def line_for_session(session: Any, result: Any, who: str) -> dict[str, Any]:
    """The ONE chat line a finished session leaves in the chat it was asked
    in — the page's ``finalize()`` message shape under the daemon's keys
    (see the module doc). ``result`` is ``session_result``'s dict (or None /
    a non-dict, when the ledger could not be read — then no receipt and no
    documents, exactly as the page composes when ``/result`` fails).
    """
    line: dict[str, Any] = {
        "role": "assistant",
        "content": content_for_session(session),
        "sessionId": str(getattr(session, "id", "") or ""),
    }
    agent_type = getattr(session, "agent_type", None)
    type_value = str(getattr(agent_type, "value", agent_type) or "").strip()
    if type_value:
        line["agentType"] = type_value
    who = str(who or "").strip()
    if who:
        line["panelWho"] = who
    if isinstance(result, dict) and result.get("found"):
        line["receipt"] = result
        docs = [str(p) for p in (result.get("documents") or []) if p]
        if docs:
            line["documents"] = docs
    return line


def delivery_result(
    engine: Any, session_id: str, *, manager: Any = None, config: Any = None
) -> dict[str, Any]:
    """The ledger card a delivered line carries (v1.352.0, G19): the RECEIPT
    read ``GET /sessions/{id}/result`` answers (``receipt=True``), so the
    line's card names the route note and the plain action lines the page's
    own read would have. ``on_this_network`` needs the provider ``manager``
    to read a local endpoint's address; without one it is DROPPED (a local
    model judged with no settings reads as cloud — absent beats wrong).
    BLOCKING. Raises what ``session_result`` raises (the caller catches)."""
    from .outcome import session_result

    result = session_result(engine, session_id, receipt=True, manager=manager, config=config)
    if manager is None and isinstance(result, dict):
        result.pop("on_this_network", None)
    return result


def deliver_session_line(
    engine: Any,
    session: Any,
    *,
    event_bus: Any = None,
    manager: Any = None,
    config: Any = None,
) -> dict[str, Any]:
    """Append the session's line to the chat it was asked in. BLOCKING
    (SQLite reads + the append) — call it off the loop. Returns
    ``append_to_chat_thread``'s answer, or ``{"ok": False, "why": ...}``
    when the session carries no chat or the delivery faulted. Never raises.
    ``manager`` / ``config`` (optional) let the receipt judge whether the
    run's model stayed on this network (:func:`delivery_result`).
    """
    thread_id = chat_thread_of(session)
    if not thread_id:
        return {"ok": False, "updated_at": None, "why": "no chat", "appended": 0, "skipped": 0}
    try:
        # Lazy, by import DIRECTION: ``daemon/`` imports the agent layer, so
        # a module-level import here would be the platform layer importing
        # the daemon (the inversion v1.185.0 removed) — the same reason
        # ``run_session`` imports ``_claimed_write_note`` inside the function.
        from ..daemon import chat_append as _chat_append

        try:
            result = delivery_result(
                engine, str(getattr(session, "id", "") or ""), manager=manager, config=config
            )
        except Exception:  # noqa: BLE001 — the line still lands without its card
            log.exception("ledger read failed before chat delivery for %s", getattr(session, "id", ""))
            result = None
        who = participant_key_for(session)
        line = line_for_session(session, result, who)
        return _chat_append.append_to_chat_thread(
            engine, thread_id, [line], event_bus=event_bus, who=who
        )
    except Exception as exc:  # noqa: BLE001 — a delivery miss must never fail a finalize
        log.exception("chat delivery failed for session %s", getattr(session, "id", ""))
        return {
            "ok": False,
            "updated_at": None,
            "why": f"could not deliver: {type(exc).__name__}",
            "appended": 0,
            "skipped": 0,
        }
