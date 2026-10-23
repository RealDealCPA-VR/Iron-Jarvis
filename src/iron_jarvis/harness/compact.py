"""COMPACT for a Build session (v1.374.0, IJ harness part 2 Phase 3).

When a session's conversation fills most of what its model can read at once,
the user may press **Compact** (or type ``/compact``): ONE model call writes a
summary of the older turns, the record has to agree with it, and from then on
every later turn's model reads that summary IN PLACE OF those turns. The
transcript the user sees is never cut — compaction changes what the MODEL is
sent, never what is stored (the v1.153.0 rule, ``context/compaction``).

THE SAME VERIFICATION as chat and agent compaction
(``context.compaction.compact_messages`` / ``verify``): every line of the
model's draft that names a file must find it in the covered transcript or in
THIS SESSION's ledger (its ``ToolInvocation`` rows' arguments and the undo
journal's targets, ``build:<id>``), and every double-quoted span must appear
verbatim in the covered transcript; a line that fails is removed. A summary
that survives nothing is not used — the press says so and keeps nothing.

LIGHTER OR NOTHING: the summarizer is told how much it replaces and asked
for a third of it (:func:`length_line`), and a verified summary that is not
lighter than what it takes the place of in the next turn's prompt (the
standing summary plus the history lines it now covers, :func:`replaced`) is
not kept — a live run on a 3,000-token window wrote one as long as the three
turns it covered, which made the next prompt heavier, not lighter.

HONEST MOCK: no real model, no summary. The demo model's prose read back as
an authoritative account of the conversation on every later turn would be
worse than no summary at all; it is refused before any call (a session on
the demo model) and after one (an answer the demo served).

THE SESSION'S OWN MODEL, ACCOUNT AND LOCAL ONLY: the call is made on the pair
the session's turns really run on (``routes/build._turn_pair``) under the
v1.372.0 hold (``providers.hold`` — nothing moves it to another provider), on
the session's ONE Iron-Proxy account when it has one (``iron_proxy.pin``),
inside the session's Local only scope (a model off this network is refused
with the 409 local-only shape), and its usage is billed to the session
(``GET /chat/threads/{id}/usage``).

STORED ON THE SESSION (``setup.compaction``): the verified body, the turn the
coverage ends on (``through_turn``), how many lines it covers, what the check
removed, who wrote it and when. ``start_build_turn`` sends the lines AFTER
``through_turn`` as history and puts the summary in the harness block
(:func:`prompt_section`, armed like the picked skill and the Browser pane's
address) — before the planner, so its cost is priced. COVERAGE ENDS ON A TURN:
the summary never splits a question from its reply, and the newest
``compaction.KEEP_RECENT`` lines are never covered.

A RE-COMPACTION covers from the beginning again with the earlier summary fed
in as ``prior`` (the v1.153.0 rule: covering only the new lines would discard
what the first summary said); when the whole older part would not fit the
model's window, it covers the lines since the earlier summary, which the
model absorbs as ``prior``.

REWIND: a rewind that cuts away any line the summary covers drops the summary
(``harness/sessions.cut_transcript`` — it describes turns that are no longer
there); a rewind to a turn after its coverage keeps it.

THE INJECTED TEXT IS SCANNED (the v1.298.0 rule): the verified body goes
through ``core/promptguard`` before it is stored; a flagged piece is replaced
by its placeholder and the bell hears ``context.blocked`` once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextvars import ContextVar, Token
from types import SimpleNamespace
from typing import Any

log = logging.getLogger(__name__)

#: The rendered summary standing over this turn's older lines ("" = none) —
#: armed by ``start_build_turn`` while the lane is built and its driver task
#: created (the task copies the context, like ``picked_skill`` / ``preview``).
SUMMARY: ContextVar[str] = ContextVar("ij_build_compaction", default="")

#: The stored summary's body, at most (it rides every later turn's prompt).
MAX_SUMMARY_CHARS = 12_000
#: The claims the check removed that are kept for the card, at most.
MAX_CLAIMS = 20
#: A claim, at most (a path or a quote — never the model's whole line).
MAX_CLAIM_CHARS = 300
#: The compaction call is bounded (a CLI can take a while; the press waits).
COMPACT_TIMEOUT_S = 240.0
#: Per covered reply: how many of its steps the summarizer is shown, and how
#: much of each step's (already masked) input.
STEP_LINES = 8
STEP_INPUT_CHARS = 120
#: The covered transcript handed to the summarizer, at most, as a share of
#: what the model can read at once (characters ≈ window x 3.6).
PROMPT_SHARE = 0.6
#: Where the scan says a flagged piece came from.
SCAN_SOURCE = "the summary of earlier turns"

# The press's refusals, in plain words (the route answers them as they are).
NO_SESSION = "no such Build session"
ARCHIVED = "This session is archived. Bring it back before you compact it."
NOT_ENOUGH = (
    "There is not enough conversation to summarize yet: the newest turns are always kept as they are."
)
NOTHING_NEW = "Nothing new to summarize: every older turn is already in the summary."
NO_REAL_MODEL = (
    "Compact needs a real model: this session runs on the demo model, which would make a summary up. "
    "Choose a model for the session first."
)
TOO_LONG = (
    "The older part of this conversation is too long for this model to summarize in one go. "
    "Choose a model that can read more at once, or rewind to an earlier point."
)
NOTHING_KEPT = (
    "The summary could not be checked against this conversation (it named files or quoted words "
    "the conversation does not have), so none of it was kept."
)
NO_SUMMARY = "The model wrote no summary, so nothing changed. Try again in a moment."
TOO_SLOW = "The model took too long to write the summary, so nothing changed. Try again in a moment."
NOT_LIGHTER = (
    "The summary came out as long as the turns it would replace, so it was not kept and nothing changed. "
    "Try again after a few more turns."
)
CHANGED = "The conversation changed while it was being summarized, so nothing was kept. Try again."


class CompactRefused(Exception):
    """A press that summarized nothing, in words, with the route's status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# the summary a turn carries
# --------------------------------------------------------------------------- #


def set_summary(text: str) -> Token[str]:
    """Arm this turn's standing summary ("" = none)."""
    return SUMMARY.set(str(text or ""))


def reset_summary(token: Token[str]) -> None:
    SUMMARY.reset(token)


def render(body: str) -> str:
    """The summary as the model reads it: the compaction module's own header
    (a condensation of the conversation, never something someone said)."""
    from ..context.compaction import render as _render

    return _render(str(body or "")[:MAX_SUMMARY_CHARS])


def prompt_section() -> str:
    """The standing summary for the harness block ("" when none). Like the
    date and the picked skill, it changes per message and is NOT part of the
    harness fingerprint."""
    text = SUMMARY.get()
    return "\n\n" + text if text.strip() else ""


# --------------------------------------------------------------------------- #
# the record kept on the session
# --------------------------------------------------------------------------- #


def clean_record(raw: Any) -> "dict[str, Any] | None":
    """``setup.compaction`` by whitelist (``routes/chat._clean_setup`` calls
    it): a summary and the turn its coverage ends on, or None."""
    if not isinstance(raw, dict):
        return None
    body = raw.get("summary")
    through = raw.get("through_turn")
    if not isinstance(body, str) or not body.strip() or not isinstance(through, str) or not through.strip():
        return None
    out: dict[str, Any] = {"summary": body.strip()[:MAX_SUMMARY_CHARS], "through_turn": through.strip()[:80]}
    for key in ("covers", "stripped"):
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            out[key] = v
    claims = raw.get("stripped_claims")
    if isinstance(claims, list):
        kept = [str(c)[:MAX_CLAIM_CHARS] for c in claims if isinstance(c, str) and c.strip()]
        if kept:
            out["stripped_claims"] = kept[:MAX_CLAIMS]
    for key in ("provider", "model", "at"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()[:200]
    return out


def standing(setup: Any) -> "dict[str, Any] | None":
    """The summary standing on a session's setup, or None."""
    return clean_record(setup.get("compaction")) if isinstance(setup, dict) else None


def view(setup: Any) -> "dict[str, Any] | None":
    """What ``GET /build/sessions/{id}`` says about it (the card reads it)."""
    rec = standing(setup)
    if rec is None:
        return None
    return {
        "summary": rec["summary"],
        "through_turn": rec["through_turn"],
        "covers": rec.get("covers", 0),
        "stripped": rec.get("stripped", 0),
        "stripped_claims": list(rec.get("stripped_claims") or []),
        "provider": rec.get("provider", ""),
        "model": rec.get("model", ""),
        "at": rec.get("at", ""),
    }


# --------------------------------------------------------------------------- #
# coverage: where the summary ends
# --------------------------------------------------------------------------- #


def _is_question(m: dict[str, Any]) -> bool:
    """A line that STARTS a turn: the user's question (a steer note is not)."""
    return m.get("role") == "user" and not m.get("steer")


def boundary(messages: list[dict[str, Any]], record: "dict[str, Any] | None") -> int:
    """Index of the first line the summary does NOT cover: the line after the
    last line of turn ``through_turn``. 0 when there is no summary — or when
    that turn is no longer in the transcript (the newest-200-lines cap took
    it: every line still there came after it, and the summary is the only
    record of what was trimmed)."""
    if not record:
        return 0
    through = record.get("through_turn")
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("turnId") == through:
            return i + 1
    return 0


def coverage(messages: list[dict[str, Any]], record: "dict[str, Any] | None" = None) -> "tuple[int, str]":
    """``(end, through_turn)`` of a new summary: it covers ``messages[:end]``,
    ``end`` falls right before a question (a turn is never split) and leaves
    the newest ``KEEP_RECENT`` lines as they are. Raises :class:`CompactRefused`
    when there is too little to cover, or nothing beyond ``record``'s."""
    from ..context.compaction import KEEP_RECENT, MIN_COVERED

    limit = len(messages) - KEEP_RECENT
    end = 0
    for i in range(1, max(0, limit) + 1):
        if i < len(messages) and _is_question(messages[i]) and str(messages[i - 1].get("turnId") or ""):
            end = i
    worded = sum(1 for m in messages[:end] if _line_text(m).strip())
    if end <= 0 or worded < MIN_COVERED:
        raise CompactRefused(NOT_ENOUGH)
    if record is not None and end <= boundary(messages, record):
        raise CompactRefused(NOTHING_NEW)
    return end, str(messages[end - 1].get("turnId") or "")


def covered_key(lines: list[dict[str, Any]]) -> str:
    """A content address of the covered lines (role, turn, words) — checked
    again right before the summary is stored, so a conversation that moved
    while the model wrote is never summarized by a stale record."""
    h = hashlib.sha256()
    for m in lines:
        h.update(b"\x1f")
        h.update(json.dumps([m.get("role"), m.get("turnId"), m.get("content")], ensure_ascii=False).encode("utf-8", "replace"))
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# what the summarizer reads
# --------------------------------------------------------------------------- #


def _line_text(m: dict[str, Any]) -> str:
    """One transcript line as the summarizer reads it: the words (a message
    that carried only files names them, the history's own rule), and for a
    reply the first few of its steps — each step's name and the start of its
    MASKED input (``chat_turn.step_detail``), never its output."""
    from .sessions import sent_files_line

    role = m.get("role")
    text = str(m.get("content") or "")
    if role == "user":
        if not text.strip():
            paths = [*(m.get("attachmentPaths") or []), *(m.get("filePaths") or [])]
            text = sent_files_line([p for p in paths if isinstance(p, str)])
        if m.get("steer") and text.strip():
            text = "(a note while it worked) " + text
        return text
    steps = m.get("steps") if isinstance(m.get("steps"), list) else []
    shown: list[str] = []
    for st in steps[:STEP_LINES]:
        if not isinstance(st, dict) or not st.get("name"):
            continue
        detail = st.get("detail") if isinstance(st.get("detail"), dict) else {}
        head = " ".join(str(detail.get("input") or "").split())[:STEP_INPUT_CHARS]
        mark = "" if st.get("ok") is not False else " (failed)"
        shown.append(f"{st['name']}{(' ' + head) if head else ''}{mark}")
    if shown:
        text = (text + "\n" if text.strip() else "") + "[tools used: " + "; ".join(shown) + "]"
    return text


def pairs_of(lines: list[dict[str, Any]], *, with_steps: bool = True) -> list[tuple[str, str]]:
    """``[(role, text)]`` for :func:`compaction.compact_messages`."""
    out: list[tuple[str, str]] = []
    for m in lines:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _line_text(m) if with_steps or role == "user" else str(m.get("content") or "")
        if text.strip():
            out.append((str(role), text))
    return out


def _chars(pairs: list[tuple[str, str]], prior: str) -> int:
    return sum(len(t) + 16 for _, t in pairs) + len(prior)


def fit_pairs(messages: list[dict[str, Any]], end: int, record: "dict[str, Any] | None",
              window: int) -> list[tuple[str, str]]:
    """The covered lines as the summarizer reads them, within
    :data:`PROMPT_SHARE` of the model's window: from the beginning with the
    steps; else without the steps; else (a re-compaction) only the lines since
    the earlier summary, which the model absorbs as ``prior``. Raises
    :class:`CompactRefused` (:data:`TOO_LONG`) when even that does not fit."""
    from ..context.budget import CHARS_PER_TOKEN

    room = int(max(1, window) * CHARS_PER_TOKEN * PROMPT_SHARE)
    prior = (record or {}).get("summary") or ""
    starts = [0]
    since = boundary(messages, record)
    if since > 0:
        starts.append(since)
    for start in starts:
        for with_steps in (True, False):
            pairs = pairs_of(messages[start:end], with_steps=with_steps)
            if pairs and _chars(pairs, prior) <= room:
                return pairs
    raise CompactRefused(TOO_LONG)


def replaced(messages: list[dict[str, Any]], end: int, record: "dict[str, Any] | None") -> tuple[int, int]:
    """``(tokens, words)`` a new summary would take the place of in the next
    turn's prompt: the standing summary (when there is one) and the history
    lines between its coverage and ``end`` — measured the planner's way
    (``context.budget.estimate_tokens``, 4 per message). A summary is only
    worth keeping when it is LIGHTER than this."""
    from ..context.budget import estimate_tokens
    from . import sessions as S

    texts = [m["content"] for m in S.history_of(messages[boundary(messages, record):end])]
    prior = render(record["summary"]) if record else ""
    tokens = sum(estimate_tokens(t) + 4 for t in texts) + (estimate_tokens(prior) if prior else 0)
    words = sum(len(t.split()) for t in texts) + len(prior.split())
    return tokens, words


def length_line(words: int) -> str:
    """What the summarizer is told about length: a third of what it replaces
    (at least 40 words) — the point of the summary is a lighter prompt."""
    budget = max(40, words // 3)
    return (
        f"\n\nKeep the whole record under about {budget} words. It replaces about {words} words of "
        "conversation and must be much shorter than what it replaces."
    )


# --------------------------------------------------------------------------- #
# the session's ledger: what the summary may name
# --------------------------------------------------------------------------- #


def ledger_facts(engine: Any, thread_id: str) -> tuple[set[str], set[str]]:
    """``(paths, tool names)`` this SESSION's ledger names: every path in its
    tool calls' arguments (``build:<id>``), the undo journal's targets, and
    the tools it ran. BLOCKING; never raises (no ledger: the transcript alone
    corroborates, the chat's rule)."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ToolInvocation, UndoJournal
    from .library import _journal_targets, _norm_path, _path_tokens, _strings
    from .sessions import session_id_for

    paths: set[str] = set()
    tools: set[str] = set()
    try:
        with session_scope(engine) as db:
            rows = db.exec(
                select(ToolInvocation, UndoJournal)
                .join(UndoJournal, UndoJournal.action_id == ToolInvocation.id, isouter=True)  # type: ignore[arg-type]
                .where(ToolInvocation.session_id == session_id_for(thread_id))
                .limit(5000)
            ).all()
            facts = [(inv.tool, inv.args_json, None if j is None else (j.kind, j.pre_inline)) for inv, j in rows]
    except Exception:  # noqa: BLE001 — verification must never break the press
        log.warning("reading build session %s's ledger failed", thread_id, exc_info=True)
        return set(), set()
    for tool, args_json, journal in facts:
        if tool:
            tools.add(str(tool))
        try:
            args = json.loads(args_json or "{}")
        except (TypeError, ValueError):
            args = {}
        for v in _strings(args):
            paths |= _path_tokens(v)
        if isinstance(args, dict):
            for key in ("path", "file_path", "notebook_path", "cwd"):
                if isinstance(args.get(key), str) and args[key]:
                    paths.add(_norm_path(args[key]))
        if journal is not None:
            paths |= _journal_targets(journal[1], journal[0])
    return {p for p in paths if p}, tools


# --------------------------------------------------------------------------- #
# the press
# --------------------------------------------------------------------------- #


def _is_mock(provider: Any, reason: Any = "") -> bool:
    return str(provider or "").strip().lower() == "mock" or str(reason or "").strip().lower() == "mock"


async def compact(platform: Any, thread_id: str) -> dict[str, Any]:
    """Summarize the session's older turns (see the module docstring) and keep
    the summary on the session. Returns the stored view plus ``window`` (what
    the model can read at once). Raises :class:`CompactRefused` in words, or
    ``egress.LocalOnlyRefused`` (the app answers its 409 shape). The caller
    holds the session (no turn or rewind runs meanwhile)."""
    from ..context import compaction as _C
    from ..context.budget import DEFAULT_WINDOW
    from ..core.ids import utcnow
    from ..core.promptguard import publish_blocked, scan_context
    from ..daemon.chat_turn import _context_window
    from ..daemon.routes.build import _turn_pair, turn_account
    from ..iron_proxy.pin import reset_pin, set_pin
    from ..projects.locate import project_for_path
    from ..providers import egress as _egress
    from ..providers.adapters.base import LLMMessage
    from ..providers.hold import reset_hold, set_hold
    from . import sessions as S
    from .library import _bill

    engine = platform.engine
    row = await asyncio.to_thread(S.get_row, engine, thread_id)
    if row is None:
        raise CompactRefused(NO_SESSION, 404)
    if getattr(row, "archived_at", None) is not None:
        raise CompactRefused(ARCHIVED, 409)
    setup = S._setup(row)
    messages = S._messages(row)
    prior = standing(setup)
    end, through = coverage(messages, prior)

    # THE SESSION'S OWN MODEL, ON ITS OWN ACCOUNT: the pair its turns run on.
    try:
        pin, account_provider = await asyncio.to_thread(turn_account, platform, thread_id, setup)
    except Exception as exc:  # noqa: BLE001 — the account's own words (an HTTPException)
        raise CompactRefused(str(getattr(exc, "detail", "") or exc), 409) from None
    project = await asyncio.to_thread(project_for_path, engine, S.folder_of(row))
    asked = SimpleNamespace(
        provider=account_provider or str(setup.get("provider") or ""), model=str(setup.get("model") or ""),
    )
    provider, model = _turn_pair(platform, asked, project)
    if not provider or _is_mock(provider):
        raise CompactRefused(NO_REAL_MODEL)
    window = _context_window(SimpleNamespace(platform=platform), provider, model) or DEFAULT_WINDOW
    pairs = fit_pairs(messages, end, prior, window)
    paths, tools = await asyncio.to_thread(ledger_facts, engine, thread_id)
    # What the summary takes the place of in the next turn's prompt: it is
    # asked to be much shorter, and kept only when it is lighter.
    replaced_tokens, replaced_words = replaced(messages, end, prior)

    router = platform.router
    got: dict[str, Any] = {}

    async def _complete(system: str, user: str) -> tuple[str, str, str]:
        try:
            route = await router.complete(
                provider=provider, model=model or None, system=system + length_line(replaced_words),
                messages=[LLMMessage(role="user", content=user)], tools=[],
                session_id=None, pin=True,
            )
        except BaseException as exc:
            got["error"] = exc
            raise
        got["route"] = route
        if _is_mock(getattr(route, "provider", ""), getattr(route, "reason", "")):
            # HONEST MOCK: the demo's prose is never a summary.
            got["mock"] = True
            return "", "mock", ""
        resp = getattr(route, "response", None)
        return str(getattr(resp, "text", "") or ""), str(route.provider or ""), str(route.model or "")

    pin_token = set_pin(pin) if pin is not None else None
    hold_token = set_hold(provider, model)
    try:
        with _egress.local_only_scope(setup.get("local_only") is True):
            async with asyncio.timeout(COMPACT_TIMEOUT_S):
                out = await _C.compact_messages(
                    pairs, complete=_complete, ledger_paths=paths, ledger_tools=tools,
                    trigger="manual", prior=(prior or {}).get("summary", ""),
                )
    except TimeoutError:
        raise CompactRefused(TOO_SLOW, 504) from None
    finally:
        reset_hold(hold_token)
        if pin_token is not None:
            reset_pin(pin_token)

    err = got.get("error")
    if isinstance(err, _egress.LocalOnlyRefused):
        raise err
    if got.get("route") is not None and not got.get("mock"):
        # Billed to the session, whatever the summary's fate (the model ran).
        _bill(platform, got["route"], thread_id)
    if err is not None:
        from ..providers.failure_words import friendly_error

        words = friendly_error(err, provider=provider)
        said = " ".join(p for p in (words.get("title", ""), words.get("fix", "")) if p).strip()
        raise CompactRefused(said or NO_SUMMARY, 502)
    if got.get("mock"):
        raise CompactRefused(NO_REAL_MODEL)
    if not out.ok:
        raise CompactRefused(NOTHING_KEPT if out.stripped else NO_SUMMARY, 502)

    # The verified body (the header is added when a turn reads it), scanned
    # like every other text injected into a prompt.
    body = out.summary.split("\n", 1)[1].strip() if "\n" in out.summary else out.summary.strip()
    from ..context.budget import estimate_tokens

    if estimate_tokens(render(body)) >= replaced_tokens:
        # A summary as long as what it replaces makes nothing lighter: the
        # turns are better read as they were.
        raise CompactRefused(NOT_LIGHTER, 502)
    scanned = await asyncio.to_thread(scan_context, body, source=SCAN_SOURCE, cap=MAX_SUMMARY_CHARS)
    if scanned.blocked:
        publish_blocked(getattr(platform, "event_bus", None), S.session_id_for(thread_id), scanned)
    record = clean_record({
        "summary": scanned.text,
        "through_turn": through,
        "covers": end,
        "stripped": out.stripped,
        "stripped_claims": out.stripped_claims,
        "provider": out.provider or provider,
        "model": out.model or model,
        "at": utcnow().isoformat(),
    })
    if record is None:
        raise CompactRefused(NOTHING_KEPT, 502)
    stored = await asyncio.to_thread(
        S.set_compaction, engine, thread_id, record, covered_key(messages[:end]), end,
    )
    if not stored:
        raise CompactRefused(CHANGED, 409)
    return {**(view({"compaction": record}) or {}), "window": int(window)}


def history_for_turn(row: Any) -> tuple[list[dict[str, str]], str]:
    """``(history, rendered summary)`` the next turn's model is sent: the
    lines after the standing summary's coverage (all of them when there is
    none) by the history's own rule, and the summary for the harness block
    ("" when none). Pure apart from reading ``row``."""
    from . import sessions as S

    rec = standing(S._setup(row))
    if rec is None:
        return S.history(row), ""  # no summary: exactly the history a turn always read
    messages = S._messages(row)
    return S.history_of(messages[boundary(messages, rec):]), render(rec["summary"])


def survives_cut(record: "dict[str, Any] | None", messages: list[dict[str, Any]], keep: int) -> bool:
    """Does the summary still describe the conversation once it is cut back
    to ``messages[:keep]`` (a rewind)? Not when the cut takes ANY line it
    covers: it would describe turns that are no longer there. A summary whose
    turn was already trimmed away (the 200-line cap) covers only lines before
    every line still there, which a rewind never touches."""
    if not record:
        return True
    through = record.get("through_turn")
    last = max((i for i, m in enumerate(messages) if m.get("turnId") == through), default=-1)
    return last < keep
