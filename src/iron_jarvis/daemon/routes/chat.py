"""Direct-chat routes: /chat, threads, personas.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re as _re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pathlib import Path
from sqlmodel import select
from typing import Any

from ..schemas import (
    ChatBody,
    ChatCompactBody,
    ChatCrystallizeBody,
    ChatRememberBody,
    ChatShareBody,
    PersonaCreateBody,
    PersonaSaveBody,
)
from ...core.db import CONVERSATION_WRITE_LOCK, session_scope
from ...core.models import AgentState, PermissionMode
from ...memory import commit as _commit
from ...core.approvals import APPROVAL_TIMEOUT_S, DECISIONS, ChatApprovals
from ..doors import collect_doors, door_for

# The chat TURN lives in daemon/chat_turn.py (v1.136.0 messaging surfaces):
# POST /chat is a thin wrapper over run_chat_turn so headless callers (the
# comm inbound poller) run the SAME engine. The helpers are imported BACK
# here because POST /chat/stream deliberately keeps its own inline copy of
# the loop (SSE stays out of this arc) and the thread routes share the caps.
from ..chat_turn import (
    _DOC_WRITING_TOOLS,
    _ESCALATE_SPEC,
    _ESCALATE_TOOL,
    _MAX_ARMED_TOOLS,
    _MAX_CONNECTORS,
    _MAX_TOOL_ROUNDS,
    _WORKFLOW_DRAFT_SPEC,
    _WORKFLOW_DRAFT_TOOL,
    _attachment_budgets,
    _prepare_attachments,
    _compose_recall_query,
    _connector_memory_block,
    _claimed_write_note,
    DRAFT_BLOCK,
    _creation_honesty_note,
    _enforce_language,
    _last_user_text,
    _persist_chat_usage,
    _apply_compaction,
    _plan_context,
    _profile_section,
    _resolve_armed_tools,
    _resolve_connectors,
    _resolve_persona,
    _resolve_tool_workspace,
    _draft_from_calls,
    _draft_from_text,
    _sanitize_draft,
    _workspace_grounding_block,
    _saved_workflows_block,
    _write_directive,
    STRICT_ASK_TOOLS,
    normalize_approval_mode,
    _validated_escalate_agent,
    run_chat_turn,
)

log = logging.getLogger(__name__)

#: Serializes the whole read-modify-write of a thread row in
#: ``PUT``/``DELETE /chat/threads/{id}``. Route handlers are re-entered per
#: request across the threadpool, so this has to be process-wide, not per-object.
#:
#: This route never had a lock — it rewrote the whole message array and, since
#: v1.142.0, ALSO rewrites that thread's search docs. Two autosaves for the same
#: thread landing together could interleave row write and index write and leave
#: the index describing a transcript the row no longer holds. Held around the
#: session, so the row and its docs are written under one owner.
#:
#: It is the SHARED conversation lock, not a private one: comm threads and round
#: tables write the same index, and three separate locks around three
#: transactions that all take ``SearchIndex``' internal lock deadlock against
#: SQLite's single writer. See ``core.db.CONVERSATION_WRITE_LOCK`` for the
#: measurement (66s and two lost writes) that collapsed them into one.
_THREAD_SAVE_LOCK = CONVERSATION_WRITE_LOCK


def _sse(event: str, data: dict[str, Any]) -> str:
    """Serialize one Server-Sent Event frame (FX-01 wire format)."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _router_frames(router, **kwargs):
    """Yield streaming frames for one completion (FX-01).

    Prefers the router's native token stream (``ModelRouter.stream`` — added by
    the coordinator, same kwargs as ``complete``: provider/model/system/messages/
    tools/task_class), and degrades HONESTLY to a single-chunk stream over
    ``complete()`` when a router without ``stream`` is wired. Either path yields
    ``{"type":"text","text":<delta>}`` deltas then exactly one
    ``{"type":"final","response":LLMResponse,"provider":..,"model":..}`` carrying
    the aggregate — never fabricated output (a real-provider failure raises here,
    exactly as ``complete`` does, and the caller turns it into an ``error`` frame).
    """
    stream = getattr(router, "stream", None)
    if stream is not None:
        async for frame in stream(**kwargs):
            yield frame
        return
    route = await router.complete(**kwargs)
    if route.response.text:
        yield {"type": "text", "text": route.response.text}
    yield {
        "type": "final",
        "response": route.response,
        "provider": route.provider,
        "model": route.model,
        # Route disclosure (v1.165.0) survives the degraded single-chunk
        # path too — same fields ModelRouter.stream's final frame carries.
        # getattr-guarded: a stream-less fake router may return bare results.
        "requested": getattr(route, "requested", ""),
        "reason": getattr(route, "reason", ""),
    }


def _share_transcript(title: str, persona: str, updated_at, msgs: list) -> str:
    """The VERBATIM thread as shareable markdown. Deterministic — no model in
    the loop — so what the user shares is exactly what was said. Message
    extras (attachments, tools used, interruption) ride along as footnotes;
    dropping them would misrepresent how a reply was produced."""
    meta = ["Shared from Iron Jarvis"]
    if persona:
        meta.append(f"persona: {persona}")
    if updated_at is not None:
        try:
            meta.append(updated_at.strftime("%Y-%m-%d %H:%M UTC"))
        except Exception:  # noqa: BLE001 — a str timestamp still shares fine
            meta.append(str(updated_at))
    lines = [f"# {title}", "", "_" + " · ".join(meta) + "_", "", "---"]
    for m in msgs:
        if not isinstance(m, dict):
            continue
        who = "You" if m.get("role") == "user" else "Iron Jarvis"
        lines += ["", f"### {who}", ""]
        lines.append(str(m.get("content") or "").strip() or "_(empty message)_")
        extras = []
        names = m.get("attachmentNames") or []
        if names:
            extras.append("Attached: " + ", ".join(str(n) for n in names))
        tools = m.get("toolsUsed") or []
        if tools:
            extras.append("Tools used: " + ", ".join(str(t) for t in tools))
        if m.get("interrupted"):
            extras.append("reply was interrupted mid-stream")
        if extras:
            lines += ["", "_" + " · ".join(extras) + "_"]
    return "\n".join(lines) + "\n"


#: Compact-mode input budget (chars). Beyond it the transcript is clipped
#: head+tail with an EXPLICIT omission marker — the model must never receive
#: a silently truncated conversation and present its digest as complete.
_SHARE_COMPACT_INPUT = 24_000

#: Distill-mode input budget (chars) for committing a thread to memory —
#: clipped head+tail with an EXPLICIT omission marker (same contract as share
#: compact: the model must never present a silent clip as the whole thread).
#: Both budgets now live in ``memory.commit`` with the ladder that spends them
#: (v1.185.0); these names are kept as ALIASES because ``crystallize`` below
#: reuses the input budget and because a re-declared copy is exactly how the
#: two remember surfaces drifted apart in the first place.
_REMEMBER_INPUT = _commit.REMEMBER_INPUT
#: Verbatim-excerpt budget when a thread is committed without a model.
_REMEMBER_VERBATIM = _commit.REMEMBER_VERBATIM

#: Distill instruction for a two-party CHAT. A parameter of the shared ladder
#: rather than a constant inside it, because the panel's prompt must attribute
#: every claim to the agent that made it and must never resolve a disagreement
#: the panel left open — instructions that are meaningless here, where there is
#: one user and one assistant and nobody to attribute anything to.
CHAT_DISTILL_SYSTEM = (
    "You distill chat conversations into durable memory notes."
    " Extract ONLY what is worth remembering long-term: decisions"
    " made, facts established, user preferences, project details,"
    " exact names/numbers/dates as written, and open action items"
    " — as compact markdown bullets under short headings. Skip"
    " pleasantries and transient back-and-forth. NEVER invent"
    " content that is not in the transcript; if the transcript"
    " notes an omitted middle, say the note covers the shared"
    " parts. No preamble, no sign-off."
)


#: Generated-document paths remembered per thread (the preview chips).
_MAX_THREAD_DOCS = 30

# -- derived documents (threads from BEFORE v1.91.0 recorded none) ----------- #
_DOC_SUFFIX = r"(?:docx|xlsx|xlsm|pptx|pdf|csv|md|html|txt)"
#: Absolute Windows/UNC paths ending in a document suffix (no spaces — the
#: wrapped patterns below catch spaced paths exactly as written).
_ABS_DOC_RX = _re.compile(
    rf"(?:[A-Za-z]:\\|\\\\)[^\s\"'`|<>*?]+?\.{_DOC_SUFFIX}\b", _re.IGNORECASE
)
#: Filenames as replies actually format them: `wrapped in backticks`,
#: **bolded**, or a bare token without spaces.
_TICK_DOC_RX = _re.compile(rf"`([^`\n]+?\.{_DOC_SUFFIX})`", _re.IGNORECASE)
_BOLD_DOC_RX = _re.compile(rf"\*\*([^*\n]+?\.{_DOC_SUFFIX})\*\*", _re.IGNORECASE)
_NAME_DOC_RX = _re.compile(rf"[\w][\w()\-.]{{0,80}}\.{_DOC_SUFFIX}\b", _re.IGNORECASE)
#: Folder mentions ("at `C:\Users\VR\`") a bare filename can be joined to.
_FOLDER_RX = _re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\s\"'`|<>*?]*[\\/]")


def _derive_thread_documents(msgs: list, setup: dict) -> list[str]:
    """Best-effort document recovery for threads saved BEFORE v1.91.0 (whose
    setup never recorded generated files): document-writing turns name their
    files in the reply, so mine the transcript for paths/filenames, join bare
    names to mentioned folders (+ the thread's workspace), and keep ONLY
    files that exist and pass fs policy — a derived chip is always real."""
    from ...core.fs_policy import fs_read_ok, is_protected_path

    out: list[str] = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        if not (_DOC_WRITING_TOOLS & set(m.get("toolsUsed") or [])):
            continue
        content = str(m.get("content") or "")
        folders = _FOLDER_RX.findall(content)
        ws = str(setup.get("workspace_dir") or "").strip()
        if ws:
            folders.append(ws if ws.endswith(("\\", "/")) else ws + "\\")
        candidates: list[str] = list(_ABS_DOC_RX.findall(content))
        for name in (
            _TICK_DOC_RX.findall(content)
            + _BOLD_DOC_RX.findall(content)
            + _NAME_DOC_RX.findall(content)
        ):
            try:
                if Path(name).is_absolute():
                    candidates.append(name)
                    continue
            except (OSError, ValueError):
                continue
            for folder in folders:
                candidates.append(folder + name)
        for cand in candidates:
            try:
                p = Path(cand)
                if not p.is_absolute() or not p.is_file():
                    continue
                ok, _reason = fs_read_ok(str(p))
                if not ok or is_protected_path(str(p)):
                    continue
                s = str(p)
                if s not in out:
                    out.append(s)
            except (OSError, ValueError):
                continue
    return out[-_MAX_THREAD_DOCS:]


def _clean_setup(raw: Any) -> str:
    """Validate + compact a thread ``setup`` payload into its stored JSON.

    Keeps ONLY the known keys ({tools, connectors, documents, skill,
    workspace_dir, provider, model}), correctly typed (the lists: strings,
    capped at their live-turn maxima; the rest: strings); unknown keys and
    mistyped values are dropped rather than erroring. Returns "" when nothing
    valid remains, so ``has_setup`` stays an honest flag. ``documents`` are
    the conversation's generated files — persisted so their previews survive
    leaving the page and daemon restarts until deliberately dismissed.
    """
    if not isinstance(raw, dict):
        return ""
    out: dict[str, Any] = {}
    tools = raw.get("tools")
    if isinstance(tools, list):
        names = [t.strip() for t in tools if isinstance(t, str) and t.strip()]
        if names:
            out["tools"] = names[:_MAX_ARMED_TOOLS]
    connectors = raw.get("connectors")
    if isinstance(connectors, list):
        ids = [c.strip() for c in connectors if isinstance(c, str) and c.strip()]
        if ids:
            out["connectors"] = ids[:_MAX_CONNECTORS]
    documents = raw.get("documents")
    if isinstance(documents, list):
        docs = [d.strip() for d in documents if isinstance(d, str) and d.strip()]
        if docs:
            out["documents"] = docs[-_MAX_THREAD_DOCS:]  # newest survive the cap
    for key in ("skill", "workspace_dir", "provider", "model"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    # The approval POSTURE persists with the thread (v1.188.0) — vocabulary-
    # checked, and the DEFAULT is stored as nothing at all: a stray string
    # here would otherwise reload as a posture the user never picked.
    mode = raw.get("approval_mode")
    if isinstance(mode, str):
        mode = normalize_approval_mode(mode)
        if mode != "approve_for_me":
            out["approval_mode"] = mode
    return json.dumps(out, separators=(",", ":")) if out else ""


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _approvals() -> ChatApprovals:
        """THE shared approval registry — the platform's (v1.189.0).

        Since sessions pause too, chat and the runtime must share one registry
        or a pause answered on the wrong copy waits forever; the platform owns
        it. The ``d`` fallback keeps test doubles (a bare SimpleNamespace
        platform) working — never two live copies in one real app, because
        build_platform always attaches one."""
        ap = getattr(d.platform, "approvals", None)
        if ap is not None:
            return ap
        ap = getattr(d, "chat_approvals", None)
        if ap is None:
            ap = ChatApprovals()
            d.chat_approvals = ap
        return ap

    @app.post("/chat/approvals/{approval_id}")
    async def resolve_chat_approval(approval_id: str, body: dict) -> dict[str, Any]:
        """Answer a mid-turn tool approval (v1.187.0).

        ``decision``: ``once`` runs this one call; ``conversation`` grants the
        tool for the rest of the turn (the client re-arms it for later turns);
        ``deny`` refuses — and the refusal reaches the ledger as the user's
        decision, because the waiting turn still calls ``invoke`` with
        ``deny_reason=``. 404 = unknown, expired, or already answered: a
        double-click races the turn's cleanup and the second click must read
        as "already answered", never as an error worth retrying.
        """
        decision = str((body or {}).get("decision") or "").strip().lower()
        if decision not in DECISIONS:
            raise HTTPException(
                status_code=400,
                detail=f"decision must be one of: {', '.join(DECISIONS)}",
            )
        if not _approvals().resolve(approval_id, decision):
            raise HTTPException(
                status_code=404, detail="no such pending approval"
            )
        return {"ok": True, "decision": decision}

    @app.get("/chat/approvals/pending")
    async def pending_chat_approvals() -> dict[str, Any]:
        """List pending mid-turn approvals so ANY surface can see the ask
        (v1.200.0).

        A job-origin agent run (the Agents page posts origin ``job:agents``)
        genuinely pauses on an ask-tier tool and publishes
        ``approval.requested`` — but only the chat stream renders an approval
        card, so the job-poster never saw the question and the pause timed out
        into a silent degrade. The NotificationBell polls this endpoint and
        renders each pending ask with Approve/Deny buttons that hit the
        existing ``POST /chat/approvals/{id}``.

        ONLY ANNOUNCED ASKS ARE LISTED: a registry id with NO matching
        ``approval.requested`` event row is a CHAT-lane mid-turn ask — the
        stream lane files into the same registry but deliberately announces
        via its SSE frame only ("the SSE frame carries them to the one client
        that can answer"), so its one answering surface is the chat card
        already in front of the user. A metadata-less "unknown" row here would
        mislabel the user's own question as an agent run and double-surface it
        on every page (reviewer-confirmed, v1.200.0). The runtime lane always
        publishes the event, so real agent asks are never dropped by this
        filter.

        NEVER ARGS: the registry deliberately stores only ``{id: future}`` —
        "a registry holding argument payloads would just be a second place
        secrets could linger" (core/approvals.py) — and this listing keeps the
        same posture. The ``approval.requested`` event payload carries
        (redacted) args for the one chat card watching that stream; this
        response fans out to EVERY dashboard page on a 15s poll, so metadata
        is copied key-by-key and args are dropped even when present.
        """
        ids = _approvals().pending_ids()
        if not ids:
            return {"approvals": []}

        def _recent_requests() -> list[tuple[str, str, str]]:
            # Bounded by construction: a pending approval is at most one
            # timeout window old (<=300s), so the newest 200 rows of an
            # indexed-type query are more than enough to cover every live id.
            from ...core.events import EventType
            from ...core.models import EventRecord

            with session_scope(d.platform.engine) as db:
                rows = list(
                    db.exec(
                        select(EventRecord)
                        .where(EventRecord.type == EventType.APPROVAL_REQUESTED)
                        .order_by(EventRecord.created_at.desc())  # type: ignore[arg-type]
                        .limit(200)
                    )
                )
            return [
                (r.payload_json or "{}", r.session_id or "",
                 r.created_at.isoformat())
                for r in rows
            ]

        # Query OFF the event loop — the daemon is ONE asyncio loop and a
        # sync DB read on it stalls every request (the v1.153.1 rule).
        rows = await asyncio.to_thread(_recent_requests)
        pending = set(ids)
        meta: dict[str, dict[str, Any]] = {}
        for payload_json, event_session, created_at in rows:
            try:
                payload = json.loads(payload_json)
            except Exception:  # noqa: BLE001 - a corrupt row is not this list's problem
                continue
            if not isinstance(payload, dict):
                continue
            aid = payload.get("approval_id")
            if not isinstance(aid, str) or aid not in pending or aid in meta:
                continue  # newest-first scan: the first hit per id wins
            # ONLY these keys, copied explicitly — never the payload itself,
            # so args (or anything else that rides the event) cannot leak.
            meta[aid] = {
                "id": aid,
                "tool": str(payload.get("tool") or "unknown"),
                "session_id": event_session,
                "requested_at": created_at,
            }
        # Ids with no event row are DROPPED, not padded with "unknown": the
        # chat stream lane files into this same registry but announces via
        # its SSE frame only — that ask's one answering surface is the chat
        # card already in front of the user, and listing it here would
        # mislabel the user's own question as an agent run and double-surface
        # it on every page. The runtime lane always publishes the event, so a
        # real agent ask can never be dropped by this filter.
        return {"approvals": [meta[a] for a in ids if a in meta]}

    @app.get("/chat/threads")
    def chat_threads(project_id: str = "") -> dict[str, Any]:
        """List saved threads (newest first). ``project_id`` (optional) scopes
        the list to ONE project's conversations — the in-project workspace fetches
        only its own threads; empty returns every thread (unchanged behavior)."""
        from ...core.models import ChatThreadRecord

        pid = (project_id or "").strip()
        with session_scope(d.platform.engine) as db:
            stmt = select(ChatThreadRecord)
            if pid:
                stmt = stmt.where(ChatThreadRecord.project_id == pid)
            rows = list(db.exec(stmt))
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        out = []
        for r in rows[:100]:
            try:
                count = len(json.loads(r.messages_json or "[]"))
            except Exception:  # noqa: BLE001
                count = 0
            out.append(
                {"id": r.id, "title": r.title or "(untitled)",
                 "persona": r.persona, "messages": count,
                 "project_id": r.project_id,
                 "has_setup": bool(r.setup_json),
                 # v1.136.0 additive comm-thread fields, getattr-read so this
                 # route works whether or not the columns have landed (and
                 # pre-existing NULL rows read as the "user" default).
                 "owner": getattr(r, "owner", "user") or "user",
                 "comm_channel": getattr(r, "comm_channel", "") or "",
                 "comm_display": getattr(r, "comm_display", "") or "",
                 "updated_at": r.updated_at.isoformat()}
            )
        return {"threads": out}

    @app.get("/chat/threads/{thread_id}")
    def chat_thread(thread_id: str) -> dict[str, Any]:
        from ...core.models import ChatThreadRecord

        with session_scope(d.platform.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
        if r is None:
            raise HTTPException(status_code=404, detail="no such thread")
        try:
            msgs = json.loads(r.messages_json or "[]")
        except Exception:  # noqa: BLE001
            msgs = []
        setup: dict[str, Any] = {}
        if r.setup_json:
            try:
                setup = json.loads(r.setup_json)
            except Exception:  # noqa: BLE001
                setup = {}
        # Threads from before v1.91.0 recorded no generated documents — derive
        # them from the transcript (existence-checked) so their preview chips
        # appear too. Recorded documents always win; derivation never blocks.
        derived: list[str] = []
        if not setup.get("documents"):
            try:
                derived = _derive_thread_documents(msgs, setup)
            except Exception:  # noqa: BLE001 — recovery must never break a thread
                derived = []
        return {
            "id": r.id, "title": r.title, "persona": r.persona,
            "project_id": r.project_id, "messages": msgs, "setup": setup,
            "derived_documents": derived,
            # v1.136.0 additive comm-thread fields (see the list route).
            "owner": getattr(r, "owner", "user") or "user",
            "comm_channel": getattr(r, "comm_channel", "") or "",
            "comm_display": getattr(r, "comm_display", "") or "",
        }

    @app.get("/chat/threads/{thread_id}/compaction")
    def chat_thread_compaction(thread_id: str) -> dict[str, Any]:
        """The compaction summary STANDING over a saved thread — readable again
        (v1.169.0, ADDITIVE).

        The model-written summary is injected into the system prompt of every
        later turn and read back as authoritative, yet until now it was
        readable exactly once — in the response of the ``POST /chat/compact``
        (or auto-compacting turn) that created it. This loads the thread's
        stored messages and asks the store which summary stands over them,
        under the SAME content addressing the live turn uses
        (``CompactionStore.standing``: ``prefix_key`` over role/content pairs,
        longest stored prefix wins — see its docstring for why an exact-key
        lookup would go stale one turn after every compaction).

        ``found: false`` when no summary stands. ``stripped_claims`` may be
        empty while ``stripped`` is positive: rows written before v1.169.0
        (and the agent auto-lane) persisted only the count — the client says
        "not recorded" there rather than pretending nothing was removed.
        """
        from ...core.models import ChatThreadRecord
        from ..chat_turn import _compaction_store

        with session_scope(d.platform.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
        if r is None:
            raise HTTPException(status_code=404, detail="no such thread")
        try:
            msgs = json.loads(r.messages_json or "[]")
        except Exception:  # noqa: BLE001 — a corrupt blob is "no summary", not a 500
            msgs = []
        rec = (
            _compaction_store(d.platform).standing(msgs)
            if isinstance(msgs, list)
            else None
        )
        if rec is None:
            return {"found": False}
        return {
            "found": True,
            "summary": rec.summary,
            "covers": rec.covers,
            "stripped": rec.stripped,
            "stripped_claims": rec.claims(),
            "trigger": rec.trigger,
            "provider": rec.provider,
            "model": rec.model,
            "created_at": rec.created_at.isoformat(),
        }

    @app.put("/chat/threads/{thread_id}")
    def save_chat_thread(thread_id: str, body: dict) -> dict[str, Any]:
        """Upsert a thread (the chat autosaves after every turn). Send
        {messages, title?, persona?, project_id?, setup?}; 'new' as the id
        creates a thread — stamped with the ACTIVE project (the context spine)
        unless the body names one explicitly. ``setup`` ({tools, skill,
        workspace_dir, provider, model}) persists the thread's working
        configuration so reopening it restores how the user works there.

        DAEMON-OWNED threads (v1.136.0 comm threads — ``owner == "daemon"``)
        are server-authoritative over ``messages_json``: the daemon appends
        inbound messages and its own replies atomically, so a client
        ``messages`` write here would clobber that copy → 409. Title /
        persona / project_id / setup edits stay allowed (send the body
        WITHOUT a ``messages`` key). Creation ('new') is unaffected — owner
        defaults to "user". DELETE stays allowed for any thread.

        v1.142.0 adds two server-side responsibilities, both invisible to the
        client (which round-trips unknown message fields verbatim, so no
        dashboard change was needed):

        * every message without an ``at`` is STAMPED with the save time — the
          long-standing shape carried no timestamp at all, which made
          "what did we say in March" unanswerable. Existing ``at`` values are
          never overwritten, so a client that starts sending real per-message
          times immediately wins;
        * the thread is re-indexed for history search inside THIS transaction
          (``db=``), so the transcript and its search docs commit or roll back
          together. The sync is delete-all-then-insert, which is what makes it
          safe for a route that rewrites the whole array on every autosave.
        """
        from ...core.db import search_index
        from ...core.ids import utcnow as _now
        from ...core.models import ChatThreadRecord

        msgs = body.get("messages")
        raw_setup = body.get("setup")
        if "setup" in body and raw_setup is not None and not isinstance(raw_setup, dict):
            raise HTTPException(status_code=400, detail="setup must be an object")
        with _THREAD_SAVE_LOCK, session_scope(d.platform.engine) as db:
            r = None if thread_id == "new" else db.get(ChatThreadRecord, thread_id)
            # getattr-read (defaults "user") so the guard works even before
            # the additive owner column lands — no import-order coupling.
            daemon_owned = r is not None and getattr(r, "owner", "user") == "daemon"
            if daemon_owned and "messages" in body:
                raise HTTPException(
                    status_code=409,
                    detail="This thread is managed by a messaging destination"
                    " — reply from the thread instead.",
                )
            # User-owned threads keep the long-standing contract: messages is
            # REQUIRED (the autosave always sends it). Daemon-owned metadata
            # edits are the one path allowed to omit it.
            if not daemon_owned and not isinstance(msgs, list):
                raise HTTPException(status_code=400, detail="messages list required")
            if r is None:
                r = ChatThreadRecord()
            # Auto-title from the first user message when none is set.
            title = (body.get("title") or r.title or "").strip()
            if not title:
                first = next(
                    (m.get("content", "") for m in (msgs or [])
                     if m.get("role") == "user"),
                    "",
                )
                title = (first[:48] + ("…" if len(first) > 48 else "")) or "New chat"
            r.title = title
            r.persona = str(body.get("persona") or r.persona or "")
            # A thread is tagged to a project ONLY when saved with an explicit
            # project_id (the in-project chat does this). Threads from the main
            # chat stay project-agnostic — no leaking the globally-active one.
            if "project_id" in body:  # explicit tag (or explicit null to clear)
                r.project_id = body.get("project_id") or None
            # Setup persists ONLY when the body carries the key — a plain
            # autosave (messages-only PUT) never clobbers a stored setup; an
            # explicit null clears it (same contract as project_id above).
            if "setup" in body:
                r.setup_json = _clean_setup(raw_setup)
            kept: list = []
            if not daemon_owned:
                kept = list(msgs[-200:])
                # Server-side `at`: stamp only what has none. The client passes
                # unknown fields straight back, so a stamp written here survives
                # every later autosave untouched — ONCE the client has seen it.
                # KNOWN LIMITATION (pinned by
                # tests/test_search_sync.py::test_a_live_thread_restamps_until_it_is_reopened):
                # a message typed in the CURRENT session lives in the dashboard's
                # in-memory array with no `at`, so each autosave re-stamps it.
                # The stored time therefore means "this thread's last activity"
                # until the thread is reopened, at which point it freezes.
                # Monotonic and bounded, and the fix is one line of dashboard —
                # stamp at compose time client-side; the guard below already
                # yields to any client-supplied value.
                stamp = _now().isoformat()
                for m in kept:
                    if isinstance(m, dict) and not m.get("at"):
                        m["at"] = stamp
                r.messages_json = json.dumps(kept)
            else:
                # Metadata-only edit on a daemon-owned thread (a rename, a
                # project (un)tag). The transcript is the comm store's, but the
                # docs carry the title + project_id, so re-index from the
                # STORED copy or the index would keep serving the old label /
                # leak into the wrong project filter.
                try:
                    stored = json.loads(r.messages_json or "[]")
                    kept = stored if isinstance(stored, list) else []
                except Exception:  # noqa: BLE001 — a corrupt blob just skips
                    kept = []
            r.updated_at = _now()
            db.add(r)
            # NOTE: deliberately NO ``db.flush()`` here. There is no foreign key
            # from a doc back to the thread (``r.id`` is generated in Python), so
            # the flush bought nothing — and it took SQLite's single writer
            # BEFORE the index lock, which is the wrong order: see
            # ``core.db.CONVERSATION_WRITE_LOCK``. The autoflush inside
            # ``sync_thread`` writes the row anyway, just on the safe side of the
            # lock.
            # HISTORY SEARCH: same transaction, same lock — never raises
            # (SearchIndex logs and returns 0), guarded anyway so an index
            # regression can never cost the user a saved conversation.
            try:
                index = search_index(d.platform.engine)
                if index is not None:
                    index.sync_thread(
                        r.id,
                        "comm" if daemon_owned else "chat",
                        r.title or "",
                        r.project_id or "",
                        kept,
                        db=db,
                    )
            except Exception:  # noqa: BLE001 — a save must never fail on search
                log.warning("history-search sync failed for thread %s", r.id,
                            exc_info=True)
            db.commit()
            db.refresh(r)
        return {"id": r.id, "title": r.title, "project_id": r.project_id}

    @app.delete("/chat/threads/{thread_id}")
    def delete_chat_thread(thread_id: str) -> dict[str, Any]:
        """Delete a thread AND its history-search docs, in one transaction.

        The index has no foreign key back to the thread row (deliberately — see
        ``search/index.py``), so a delete that skipped this would leave the
        transcript searchable forever: "delete" has to mean deleted."""
        from ...core.db import search_index
        from ...core.models import ChatThreadRecord

        with _THREAD_SAVE_LOCK, session_scope(d.platform.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
            if r is None:
                raise HTTPException(status_code=404, detail="no such thread")
            db.delete(r)
            try:
                index = search_index(d.platform.engine)
                if index is not None:
                    index.drop_thread(thread_id, db=db)
            except Exception:  # noqa: BLE001 — a delete must always complete
                log.warning("history-search drop failed for thread %s", thread_id,
                            exc_info=True)
            db.commit()
        return {"deleted": thread_id}

    @app.post("/chat/threads/{thread_id}/share")
    async def share_chat_thread(thread_id: str, body: ChatShareBody) -> dict[str, Any]:
        """Render a saved thread for sharing: ``mode`` full (verbatim
        transcript) or compact (a faithful digest via the one-shot LLM path),
        as markdown or a self-contained HTML page. Returns the text — the
        dashboard copies/downloads it; the daemon never publishes anything."""
        from ...core.models import ChatThreadRecord

        mode = (body.mode or "full").strip().lower()
        fmt = (body.format or "markdown").strip().lower()
        if mode not in ("full", "compact"):
            raise HTTPException(status_code=400, detail="mode must be 'full' or 'compact'")
        if fmt not in ("markdown", "html"):
            raise HTTPException(status_code=400, detail="format must be 'markdown' or 'html'")
        with session_scope(d.platform.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
        if r is None:
            raise HTTPException(status_code=404, detail="no such thread")
        try:
            msgs = json.loads(r.messages_json or "[]")
        except Exception:  # noqa: BLE001
            msgs = []
        if not msgs:
            raise HTTPException(status_code=400, detail="this thread has no messages to share")
        title = (r.title or "Chat").strip() or "Chat"
        transcript = _share_transcript(title, r.persona or "", r.updated_at, msgs)

        used_provider = None
        if mode == "compact":
            from ...providers.adapters.base import LLMMessage
            from ...providers.adapters.mock import MockLLMAdapter

            provider = body.provider or d.platform.config.default_provider
            model = body.model or d.platform.config.default_model
            try:
                adapter = d.platform.providers.get(provider, model)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
            # A mock adapter would FABRICATE a digest of a real conversation —
            # never acceptable here (unlike the workflow builder's offline
            # demo). Route to the strongest REAL provider instead; with none
            # connected, refuse honestly. Checking the resolved adapter — not
            # the provider name — keeps a working adapter injected by tests on
            # the normal path.
            if isinstance(adapter, MockLLMAdapter):
                adapter, provider = d._failover_adapter("mock")
                if adapter is None:
                    raise HTTPException(
                        status_code=400,
                        detail="connect a model on the Connections page to compact chats"
                        " — the full transcript works offline",
                    )
            clipped = transcript
            if len(clipped) > _SHARE_COMPACT_INPUT:
                head, tail = _SHARE_COMPACT_INPUT // 3, _SHARE_COMPACT_INPUT * 2 // 3
                clipped = (
                    clipped[:head]
                    + "\n\n[… middle of the conversation omitted for length —"
                    " say so in the digest …]\n\n"
                    + clipped[-tail:]
                )
            system = (
                "You compact chat transcripts for sharing. Produce a faithful,"
                " self-contained digest in markdown: one opening paragraph of what"
                " the conversation was about, a '## Key points' bullet list"
                " (decisions, answers, figures, links — keep exact numbers, names"
                " and code identifiers as written), and '## Where it landed' with"
                " the outcome / next steps. NEVER invent content that is not in"
                " the transcript; if the transcript notes an omitted middle, say"
                " the digest covers the shared parts. No preamble, no sign-off."
            )
            resp, used_provider, _m = await d._one_shot_complete(
                provider, adapter, system=system,
                messages=[LLMMessage(role="user", content=clipped)],
            )
            digest = (resp.text or "").strip()
            if not digest:
                raise HTTPException(
                    status_code=422, detail="the model returned an empty digest — try again"
                )
            content = (
                f"# {title} — compacted\n\n"
                f"_A digest of {len(msgs)} messages · shared from Iron Jarvis_\n\n"
                f"{digest}\n"
            )
        else:
            content = transcript

        if fmt == "html":
            from ...documents.writers import html_page

            content = html_page(content, title=title)
        out: dict[str, Any] = {
            "content": content, "mode": mode, "format": fmt,
            "title": title, "messages": len(msgs),
        }
        if used_provider:
            out["provider"] = used_provider
        return out

    @app.post("/chat/threads/{thread_id}/remember")
    async def remember_chat_thread(
        thread_id: str, body: ChatRememberBody
    ) -> dict[str, Any]:
        """Commit a saved thread to LONG-TERM MEMORY. ``mode`` distill = a
        faithful one-shot distillation of what is worth remembering; full =
        the verbatim transcript. With no real model connected, distill falls
        back to an honest verbatim excerpt — a mock must never fabricate a
        "memory" of a real conversation. ``source`` targets any registered
        LTM store (the default brain, an MCP-served brain, Notion, …)."""
        from ...core.models import ChatThreadRecord

        mode = (body.mode or "distill").strip().lower()
        if mode not in ("distill", "full"):
            raise HTTPException(status_code=400, detail="mode must be 'distill' or 'full'")
        with session_scope(d.platform.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
        if r is None:
            raise HTTPException(status_code=404, detail="no such thread")
        try:
            msgs = json.loads(r.messages_json or "[]")
        except Exception:  # noqa: BLE001
            msgs = []
        if not msgs:
            raise HTTPException(
                status_code=400, detail="this thread has no messages to remember"
            )
        ltm = d.platform.ltm
        src = (body.source or "").strip() or ltm.default_source()
        if not src or ltm.get(src) is None:
            raise HTTPException(status_code=400, detail=f"no such memory source: {src}")
        title = (r.title or "Chat").strip() or "Chat"
        transcript = _share_transcript(title, r.persona or "", r.updated_at, msgs)

        # THE LADDER IS SHARED (v1.185.0). It used to live right here, inline in
        # this closure, which is precisely why the round table could not call it
        # and grew a second copy instead. See ``memory/commit.py``.
        outcome = await _commit.distill_or_excerpt(
            d,
            transcript=transcript,
            mode=mode,
            system=CHAT_DISTILL_SYSTEM,
            subject="conversation",
            provider=body.provider or "",
            model=body.model or "",
        )
        content_body = outcome.body
        distilled = outcome.distilled
        used_provider = outcome.provider
        note = outcome.note

        stamp = ""
        try:
            stamp = r.updated_at.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            stamp = ""
        header = (
            f"_Committed from the chat “{title}”"
            f" ({len(msgs)} messages{', ' + stamp if stamp else ''})._\n\n"
        )
        content = header + content_body + f"\n\n---\nthread: {thread_id}"
        try:
            ref = ltm.append(f"Chat: {title}", content, source=src)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — surface an append failure honestly
            raise HTTPException(
                status_code=422, detail=f"could not write to '{src}': {exc}"
            )
        out: dict[str, Any] = {
            "ok": True, "ref": ref, "source": src, "distilled": distilled,
            "title": f"Chat: {title}", "messages": len(msgs),
        }
        if used_provider and distilled:
            out["provider"] = used_provider
        if note:
            out["note"] = note
        return out

    @app.post("/chat/threads/{thread_id}/crystallize")
    async def crystallize_chat_thread(
        thread_id: str, body: ChatCrystallizeBody
    ) -> dict[str, Any]:
        """Turn a saved thread into a reusable workflow DRAFT (v1.120.0).

        Reads the transcript (message text + per-message tool names — the
        maximum storage keeps) and asks a one-shot model to GENERALIZE what
        happened into 2-6 ordered steps: one-off specifics become parameters,
        so the workflow works next time too. Returns the draft; nothing is
        saved — the card's Save button decides (suggest-don't-act). Unlike
        /remember there is no verbatim fallback: a fabricated workflow from a
        mock model would be worse than none, so offline → an honest 400."""
        from ...core.models import ChatThreadRecord
        from ...providers.adapters.base import LLMMessage
        from ...providers.adapters.mock import MockLLMAdapter

        with session_scope(d.platform.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
        if r is None:
            raise HTTPException(status_code=404, detail="no such thread")
        try:
            msgs = json.loads(r.messages_json or "[]")
        except Exception:  # noqa: BLE001
            msgs = []
        if not msgs:
            raise HTTPException(
                status_code=400, detail="this thread has no messages to turn into a workflow"
            )

        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
        if isinstance(adapter, MockLLMAdapter):
            adapter, provider = d._failover_adapter("mock")
            if adapter is None:
                raise HTTPException(
                    status_code=400,
                    detail="connect a model on the Connections page to turn chats into workflows",
                )

        title = (r.title or "Chat").strip() or "Chat"
        transcript = _share_transcript(title, r.persona or "", r.updated_at, msgs)
        if len(transcript) > _REMEMBER_INPUT:
            head, tail = _REMEMBER_INPUT // 3, _REMEMBER_INPUT * 2 // 3
            transcript = (
                transcript[:head]
                + "\n\n[… middle of the conversation omitted for length …]\n\n"
                + transcript[-tail:]
            )
        system = (
            "You turn a finished chat conversation into a REUSABLE Iron Jarvis "
            "workflow: the repeatable process behind what actually happened. "
            "GENERALIZE — replace one-off specifics (a particular file name, "
            "date, client) with role words ('the provided file', 'this week') "
            "so the workflow works next time too. Base the steps ONLY on what "
            "the conversation actually did or clearly set out to do; never "
            "invent work that didn't happen. Respond with ONLY a JSON object "
            "(no prose, no code fence) of the exact shape: "
            '{"name": "kebab-case-name", "description": "one line", '
            '"steps": [{"name": "Step name", "agent": "builder", "task": '
            '"a clear instruction for this step", "tool": null}]}. '
            "agent MUST be one of: builder, planner, researcher, reviewer, "
            "supervisor. Prefer 2-6 steps."
        )
        try:
            resp, used_provider, _m = await d._one_shot_complete(
                provider, adapter, system=system,
                messages=[LLMMessage(role="user", content=transcript)],
            )
        except HTTPException:
            raise  # _one_shot_complete raises CLEAN typed errors (429 etc.)
        except Exception as exc:  # noqa: BLE001 — honest failure, no fabrication
            raise HTTPException(status_code=502, detail=f"the model failed: {exc}")
        from ..app import _extract_workflow_json

        try:
            wf = _extract_workflow_json(resp.text or "")
        except Exception:  # noqa: BLE001
            wf = {}
        draft = _sanitize_draft(wf if isinstance(wf, dict) else None)
        if draft is None:
            raise HTTPException(
                status_code=422,
                detail="the model did not return a usable workflow — try again",
            )
        # The thread's project rides along so a Save can pin the workflow.
        return {
            **draft,
            "project_id": r.project_id or None,
            "thread": thread_id,
            "provider": used_provider,
        }

    def _persona_store():
        from ...personas import PersonaStore

        return PersonaStore(d.platform.engine)

    @app.get("/chat/personas")
    def chat_personas() -> dict[str, Any]:
        """Every persona — built-ins (with any user override applied) + the user's
        own — each fully editable, carrying its title + prompt."""
        from ...personas import merged

        return {"personas": merged(_persona_store(), d._PERSONAS)}

    @app.put("/chat/personas/{name}")
    def save_persona(name: str, body: PersonaSaveBody) -> dict[str, Any]:
        """Create or update a persona under ``name`` (a built-in name → an
        override; a new name → a new persona). The saved version wins next time."""
        from ...personas import merged, slugify

        slug = slugify(name)
        if not body.prompt.strip():
            raise HTTPException(status_code=400, detail="a persona prompt is required")
        _persona_store().upsert(
            slug,
            title=(body.title.strip() or slug.capitalize()),
            description=body.description.strip(),
            prompt=body.prompt.strip(),
        )
        saved = next(
            (p for p in merged(_persona_store(), d._PERSONAS) if p["name"] == slug), None
        )
        return {"saved": slug, "persona": saved}

    @app.post("/chat/personas")
    def create_persona(body: PersonaCreateBody) -> dict[str, Any]:
        """Create a NEW persona; its id is slugified from ``name`` or ``title``."""
        from ...personas import merged, slugify

        if not body.prompt.strip():
            raise HTTPException(status_code=400, detail="a persona prompt is required")
        slug = slugify(body.name or body.title)
        _persona_store().upsert(
            slug,
            title=(body.title.strip() or slug.capitalize()),
            description=body.description.strip(),
            prompt=body.prompt.strip(),
        )
        saved = next(
            (p for p in merged(_persona_store(), d._PERSONAS) if p["name"] == slug), None
        )
        return {"created": slug, "persona": saved}

    @app.delete("/chat/personas/{name}")
    def delete_persona(name: str) -> dict[str, Any]:
        """Remove a saved persona — reverts a built-in to its default, or deletes
        a custom one. 404 only when the name is neither saved nor a built-in."""
        from ...personas import slugify

        slug = slugify(name)
        removed = _persona_store().delete(slug)
        if not removed and slug not in d._PERSONAS:
            raise HTTPException(status_code=404, detail="no such persona")
        return {"deleted": slug, "reverted_to_builtin": slug in d._PERSONAS}

    @app.post("/chat/compact")
    async def chat_compact(body: ChatCompactBody) -> dict[str, Any]:
        """Compact this conversation because the USER asked (the 70% offer).

        Returns the summary itself, not just a receipt: the user is agreeing to
        let a paraphrase stand in for their own words in every later turn, so
        they get to read what it says. ``stripped_claims`` is the honest half of
        that — the things the model wrote that the record would not corroborate
        and which were therefore removed.
        """
        from ...context import compaction as _C
        from ..chat_turn import _compaction_store, _context_window

        msgs = list(body.messages or [])
        if len(msgs) <= _C.KEEP_RECENT + _C.MIN_COVERED:
            raise HTTPException(
                status_code=400,
                detail="not enough conversation to compact yet",
            )

        provider = (body.provider or "").strip()
        model = (body.model or "").strip()
        covered = msgs[: len(msgs) - _C.KEEP_RECENT]
        pairs = [
            (getattr(m, "role", "user") or "user", getattr(m, "content", "") or "")
            for m in covered
        ]
        key = _C.prefix_key([f"{r}\x1e{t}" for r, t in pairs])
        store = _compaction_store(d.platform)

        cached = store.get(key)
        if cached is not None and cached.summary.strip():
            return {
                "ok": True,
                "cached": True,
                "covers": cached.covers,
                "stripped": cached.stripped,
                # ADDITIVE (v1.169.0): the fresh path always returned the
                # claims; the cached path silently dropped them, so clicking
                # Compact twice LOST the honest half of the receipt.
                "stripped_claims": cached.claims(),
                "summary": cached.summary,
                "provider": cached.provider,
                "model": cached.model,
                "trigger": cached.trigger,
            }

        complete = None
        try:
            complete = d._compaction_complete(provider, model)
        except Exception:  # noqa: BLE001 — classified as "no real model" below
            complete = None
        if complete is None:
            # The honest-mock rule: a fabricated summary would be read back as
            # an authoritative account of the conversation on every later turn.
            raise HTTPException(
                status_code=400,
                detail=(
                    "compaction needs a real model — connect a provider, or let "
                    "the deterministic recap keep handling overflow"
                ),
            )

        out = await _C.compact_messages(pairs, complete=complete, trigger="manual")
        if not out.ok:
            raise HTTPException(
                status_code=502,
                detail=(
                    "nothing in the summary could be corroborated against the "
                    "transcript"
                    if out.stripped
                    else "the model returned no summary"
                ),
            )
        store.put(
            key,
            summary=out.summary,
            covers=len(covered),
            stripped=out.stripped,
            # v1.169.0: persist the claims TEXT, not just the count, so the
            # inspect route (GET /chat/threads/{id}/compaction) can show what
            # was removed after the creating response is gone.
            stripped_claims=out.stripped_claims,
            trigger="manual",
            provider=out.provider,
            model=out.model,
        )
        window = _context_window(d, provider, model)
        return {
            "ok": True,
            "cached": False,
            "covers": len(covered),
            "stripped": out.stripped,
            "stripped_claims": out.stripped_claims,
            "summary": out.summary,
            "provider": out.provider,
            "model": out.model,
            "trigger": "manual",
            "window": window,
        }

    @app.post("/chat")
    async def chat_complete(body: ChatBody) -> dict[str, Any]:
        """One conversational turn: full history in -> one reply out.

        Thin wrapper over :func:`iron_jarvis.daemon.chat_turn.run_chat_turn`
        (the turn was extracted VERBATIM in v1.136.0 so headless callers --
        the comm inbound poller, the desktop reply fan-out -- run the SAME
        engine). Persona resolution stays PersonaStore(platform.engine)-backed
        inside the service; the builtin defaults ride in as ``d._PERSONAS``.
        The service raises HTTPException (404 unknown skill, 400 empty
        messages, 502 router/tool failure) and FastAPI surfaces it here
        unchanged -- exactly the responses this route always returned.
        """
        return await run_chat_turn(d.platform, d._PERSONAS, body)

    @app.post("/chat/stream")
    async def chat_stream(body: ChatBody, request: Request):
        """Streaming twin of :func:`chat_complete` (FX-01).

        IDENTICAL prep (persona/project/learning/memory fabric/attachments/skill/
        armed tools/overrides/routing choice), but the turn is emitted as Server-
        Sent Events — token deltas as they generate, live tool-call frames, then a
        terminal ``done``. PURELY ADDITIVE: POST /chat is unchanged, and this
        shares the same router + tool-loop semantics so a streamed turn is byte-
        compatible with the non-streaming one (same usage ledger, same reply).
        """
        from ...providers.adapters.base import LLMMessage

        if not body.messages:
            raise HTTPException(status_code=400, detail="messages is required")

        # ------------------------------------------------------------------ #
        # PREP — verbatim from chat_complete (kept in lock-step deliberately).
        # ------------------------------------------------------------------ #
        # ONE retrieval query for the whole turn (v1.141.0): project knowledge,
        # the memory fabric, and toggled connector memory all key off this —
        # composed so short follow-ups inherit the conversation's subject (rule
        # documented + pinned on _compose_recall_query). Attachment RAG keeps
        # the raw last user message for within-file relevance.
        # MIRROR NOTE (lock-step): same line in chat_turn.run_chat_turn —
        # edit both or neither.
        recall_query = _compose_recall_query(body.messages)

        # Persona: a user override/creation wins, then a built-in, then the value
        # is treated as free-text instructions (used verbatim). With NO explicit
        # persona the configured default applies (Pair Z's config.default_persona
        # — getattr because the field lands with Z; "" = the old behaviour).
        # MIRROR NOTE (lock-step): same call in chat_turn.run_chat_turn.
        want = (body.persona or "").strip()
        persona = _resolve_persona(
            _persona_store(), d._PERSONAS, want,
            getattr(d.platform.config, "default_persona", ""),
        )
        system = persona + (
            "\n\n# Environment\n"
            f"- You run locally on the user's machine; their home directory is {Path.home()}.\n"
            # THIS LINE caused the reported behaviour. It told the model that
            # a mode existed and that switching was the USER's job, so when a
            # request outgrew the turn it dutifully said "you need to be in
            # agent mode" — the app asking the user to do its routing.
            "- Answer directly. There are no modes for the user to pick: when "
            "a request needs sustained multi-step work you cannot finish here, "
            "call escalate_to_agent and it is taken over seamlessly.\n"
            "- When the user describes a repeatable multi-step process (\"every "
            "Friday…\", \"whenever a client sends…\"), call workflow_draft so "
            "they get a saveable workflow card instead of prose steps."
        )
        # USER PROFILE (v1.144.0) — the lock-step copy of chat_turn's injection.
        # MIRROR NOTE: edit both or neither.
        system += _profile_section(d.platform)
        # DRAFT FENCE (v1.161.0) — lock-step copy of chat_turn's injection. This
        # is the STREAMING lane, which is the one a user actually watches write
        # an email; if the instruction reached only the non-streaming lane the
        # feature would look broken exactly where it is most used.
        system += DRAFT_BLOCK
        pid = (body.project_id or "").strip() or None
        resolved_proj = None
        if pid:
            try:
                from ...core.models import Project

                with session_scope(d.platform.engine) as db:
                    resolved_proj = db.get(Project, pid)
            except Exception:  # noqa: BLE001 — never block a chat turn
                resolved_proj = None
        if resolved_proj is not None:
            block = f"\n\n# Project: {resolved_proj.name}"
            instructions = (resolved_proj.instructions or "").strip()
            if instructions:
                block += f"\n\nInstructions (follow these):\n{instructions[:2000]}"
            if resolved_proj.brief:
                block += f"\n\nAbout this project: {resolved_proj.brief[:1500]}"
            # PROJECT PARITY (v1.141.0): root line + recent-activity recap,
            # the agents/runtime.py _project_context formats. MIRROR NOTE
            # (lock-step): same block in chat_turn.run_chat_turn — edit both
            # or neither.
            if (resolved_proj.root or "").strip():
                block += f"\n\nProject folder: {resolved_proj.root.strip()}"
            # Knowledge keyed off the turn's composed recall query (X.3).
            try:
                from ...projects.knowledge import ground

                knowledge = ground(d.platform, pid, recall_query)
                if knowledge:
                    block += f"\n\nProject knowledge (reference):\n{knowledge}"
            except Exception:  # noqa: BLE001 — retrieval must never break a turn
                pass
            # Recent activity: the last 5 sessions in this project, in the
            # exact line format the agent runtime injects. Best-effort.
            try:
                from sqlmodel import select as _select

                from ...core.models import Session as _Session

                with session_scope(d.platform.engine) as db:
                    _siblings = list(
                        db.exec(
                            _select(_Session)
                            .where(_Session.project_id == pid)
                            .order_by(_Session.created_at.desc())  # type: ignore[attr-defined]
                            .limit(5)
                        )
                    )
                _recent = [
                    f"- [{s.status.value}] {s.task[:80]}: {(s.summary or '(no summary)')[:160]}"
                    for s in _siblings
                ]
                if _recent:
                    block += (
                        "\n\nRecent activity in this project (newest first):\n"
                        + "\n".join(_recent)
                    )
            except Exception:  # noqa: BLE001 — the recap must never break a turn
                pass
            system += block

        learning = getattr(d.platform, "learning", None)
        if learning is not None:
            try:
                system = learning.apply_to_prompt(system)
            except Exception:  # noqa: BLE001 — never block a chat turn
                pass

        # AWARENESS INDEX (v1.141.0): Pair Y's memory_index_block, injected
        # after lessons. Import-guarded + callable-checked for landing order.
        # MIRROR NOTE (lock-step): same block in chat_turn.run_chat_turn —
        # edit both or neither.
        try:
            from ...memory.index_block import memory_index_block as _memory_index_block
        except ImportError:  # Pair Y's module not landed yet
            _memory_index_block = None
        if callable(_memory_index_block):
            try:
                _idx = _memory_index_block(d.platform, project_id=pid)
                if _idx:
                    system += "\n\n" + _idx.strip("\n")
            except Exception:  # noqa: BLE001 — awareness must never break a turn
                pass

        # MEMORY FABRIC (mirrors chat_complete): keyed off the composed
        # recall query; grounding failures LOG (never silently pass, never
        # break the turn) — a bare ``pass`` here swallowed the day-one
        # ``sources=`` TypeError. MIRROR NOTE (lock-step): same block in
        # chat_turn.run_chat_turn — edit both or neither.
        fabric = getattr(d.platform, "fabric", None)
        if fabric is not None and recall_query.strip():
            try:
                # OFF THE EVENT LOOP (v1.173.0) — lock-step with chat_turn:
                # grounding hits the DB and remote bases, and can now fan out
                # into several passes.
                grounding = await asyncio.to_thread(
                    fabric.ground,
                    recall_query,
                    project_id=pid,
                    sources=["files", "notes", "memory", "lessons", "sessions", "chats"],
                )
                if grounding:
                    system += grounding
            except Exception:  # noqa: BLE001 — never break a turn, never silent
                log.exception(
                    "chat memory-fabric grounding failed (turn continues)"
                )

        # Connector toggles (mirrors chat_complete): memory hits injected
        # directly; MCP tool groups merge into the armed set below. Same
        # composed recall query as the fabric (X.3).
        conn_tools, conn_memory = _resolve_connectors(d, body)
        if conn_memory:
            cm_block = await asyncio.to_thread(
                _connector_memory_block, d, conn_memory, recall_query
            )
            if cm_block:
                system += cm_block

        # Routing choice (hoisted, mirrors chat_complete) — attachment budgets
        # scale to the model that will actually answer.
        provider_choice = (body.provider or "").strip() or (
            (resolved_proj.default_provider or "").strip() if resolved_proj else ""
        )
        model_choice = (body.model or "").strip() or (
            (resolved_proj.default_model or "").strip() if resolved_proj else ""
        )
        _inline_budget, _rag_budget, _rag_k = _attachment_budgets(
            d,
            provider_choice or d.platform.config.default_provider,
            model_choice or d.platform.config.default_model,
        )

        # Attachments: text formats extracted inline (scans via OCR), images to
        # VISION. SHARED IMPLEMENTATION (v1.174.0): this lane and
        # chat_turn.run_chat_turn both call `_prepare_attachments`. It was a
        # hand-copied block in each until a scanned PDF — no text layer, "0
        # indexed sections", half of a real tax folder — had to be fixed in
        # both; the copy in THIS lane is the one the dashboard runs, so a
        # single-lane fix is a fix the user never sees. Do not re-inline it.
        images, attach_block = await _prepare_attachments(
            d, body,
            inline_budget=_inline_budget, rag_budget=_rag_budget, rag_k=_rag_k,
            provider_choice=provider_choice, model_choice=model_choice,
            # MIRROR NOTE (lock-step): same argument in chat_turn.run_chat_turn.
            # The grounded project's folder, which the preparer needs to decide
            # whether an IN-PLACE edit of an attachment can actually reach it —
            # everything else about the live-file handoff lives INSIDE
            # `_prepare_attachments`, so this lane inherits it (v1.196.0).
            project_root=(
                (resolved_proj.root or "") if resolved_proj is not None else ""
            ),
        )
        if attach_block:
            system += "\n\n# Attachments (provided by the user this turn)" + attach_block

        if (body.skill or "").strip():
            sk = d.platform.skills.get(body.skill.strip())
            if sk is None:
                raise HTTPException(status_code=404, detail=f"no such skill: {body.skill}")
            system += (
                f"\n\n# Skill invoked by the user: {sk.name}\n"
                "FOLLOW this playbook for this request.\n" + sk.instructions[:8000]
            )

        # CAPABILITY ROSTER (v1.139.0): who could take escalated work — after
        # the skills section, before the tools block, so the model can NAME a
        # specialist in escalate_to_agent's optional ``agent`` arg. Skipped
        # cleanly when empty; a missing/broken roster module never breaks a
        # turn.
        # MIRROR NOTE (lock-step): this is an inline copy of the same block in
        # chat_turn.run_chat_turn. The stream prep started as a byte-identical
        # lift of the turn service; from v1.139.0 it is kept in lock-step BY
        # HAND — edit both sites or neither.
        try:
            from ...agents.roster import roster_block

            _roster = roster_block(d.platform)
            if _roster:
                system += "\n\n" + _roster
        except Exception:  # noqa: BLE001 — the roster must never break a turn
            pass

        # SAVED WORKFLOWS (v1.170.0) — the lock-step copy of chat_turn's
        # injection: the bounded one-line map of the user's stored workflows,
        # added BEFORE the budget planner runs so its cost is priced (the
        # repo rule). This is the STREAMING lane — the one the dashboard
        # uses — so skipping it here would make the model workflow-blind on
        # every real turn. MIRROR NOTE (lock-step): same line in
        # chat_turn.run_chat_turn — edit both or neither.
        system += _saved_workflows_block(d.platform)

        # WORKSPACE GROUNDING (v1.210.0) — the lock-step copy of chat_turn's
        # injection: a chat bound to a folder (the Build pane's per-pane chat
        # sends `workspace_dir` every turn) has that folder NAMED in the
        # prompt, regardless of whether any tools arm. THIS lane is the one
        # the Build pane actually POSTs to, so skipping it here would leave
        # the live bug in place exactly where it was reported. Resolved at
        # most ONCE per turn (the armed branch below reuses this tuple for
        # its ToolContext), off the event loop (v1.153.1), and BEFORE the
        # budget planner so its cost is priced (the repo rule).
        # MIRROR NOTE (lock-step): same block in chat_turn.run_chat_turn —
        # edit both or neither.
        _ws_resolved: "tuple[Path, bool] | None" = None
        if (getattr(body, "workspace_dir", "") or "").strip():
            try:
                _ws_resolved = await asyncio.to_thread(
                    _resolve_tool_workspace,
                    d.platform.config.home / "uploads",
                    body.workspace_dir or "",
                    (resolved_proj.root or "") if resolved_proj is not None else "",
                )
            except Exception:  # noqa: BLE001 — resolution MKDIRs a folder the
                # user picked; that can fail. None renders the honest "not
                # accessible" wording rather than a grounding claim tools
                # cannot back.
                _ws_resolved = None
            system += _workspace_grounding_block(body.workspace_dir, _ws_resolved)

        # CONTEXT PROTECTION (v1.146.0) + COMPACTION (v1.153.0) — the lock-step
        # copy of chat_turn's. MIRROR NOTE: edit both or neither.
        system, _ctx_messages, context_report = await _apply_compaction(
            d, body, system, provider_choice, model_choice
        )
        plan = _plan_context(
            d, body, system, provider_choice, model_choice, messages=_ctx_messages
        )
        if plan.recap:
            system += "\n\n" + plan.recap
        msgs: list[LLMMessage] = [
            LLMMessage(role=m["role"], content=m["content"]) for m in plan.messages
        ]
        if images and msgs:
            for m in reversed(msgs):
                if m.role == "user":
                    m.images = images
                    break

        # An EXPLICITLY picked text-only CLI (codex exec has no structured
        # tool-calling) used to be capability-REROUTED here — the user asked
        # for their Codex subscription and got a different provider every
        # time. Honest fix (v1.125.0): honor the pick and serve the turn
        # TEXT-ONLY — no armed tools, no exit tools — with a note when tools
        # were explicitly requested. Only for explicit picks; default/auto
        # routes keep full capability routing.
        text_only_pick = False
        if (body.provider or "").strip() not in ("", "auto"):
            try:
                _picked = d.platform.providers.get(
                    provider_choice, model_choice or None
                )
                from ...providers.router import _capabilities

                # The ROUTER's accessor (adapter.capabilities()) — the same
                # truth the capability reroute reads, so the two can never
                # disagree about what "text-only" means.
                text_only_pick = not bool(
                    _capabilities(_picked).get("tool_use", True)
                )
            except Exception:  # noqa: BLE001 — resolution failures rout normally
                text_only_pick = False
        # ENVELOPE ADAPTATION DISCLOSURE (v1.202.0): non-null exactly when the
        # capability envelope narrowed this turn's arming (the tool cap below)
        # — null on every trusted/unmeasured route, which is the common case.
        # The text-only branch never arms, so nothing there can bend.
        # MIRROR NOTE (lock-step): chat_turn.run_chat_turn carries the same
        # computation — edit both or neither.
        envelope_adapted: "dict[str, Any] | None" = None
        if text_only_pick:
            armed, auto_armed, ask_armed = [], [], []
            tool_specs = []
        else:
            # ENVELOPE TOOL CAP (v1.202.0) — the lock-step twin of the consult
            # in `chat_turn.run_chat_turn`; see the reasoning there. The cap is
            # about a weak model facing a wide menu; explicit user tool picks
            # are consent and the autoselect contract already protects them.
            # Resolved for the model that will ANSWER (explicit/project pin,
            # else the config default route); trusted (cloud/CLI/mock) and
            # unmeasured profiles answer None -> arming byte-identical.
            # MIRROR NOTE (lock-step): edit both or neither.
            _env_model = model_choice or d.platform.config.default_model
            _tool_cap: "int | None" = None
            try:
                _profiler = getattr(
                    d.platform.providers, "capability_profile", None
                )
                if _profiler is not None:
                    _tool_cap = _profiler(
                        provider_choice or d.platform.config.default_provider,
                        _env_model,
                    ).max_tools()
            except Exception:  # noqa: BLE001 — never break a turn
                _tool_cap = None
            # OFF THE EVENT LOOP (v1.196.0) — the lock-step twin of the hop in
            # `chat_turn.run_chat_turn`; see the reasoning there. This lane
            # matters more, not less: it is the one the user watches token by
            # token, so a parked loop here reads as the app having died.
            _selection = await asyncio.to_thread(
                _resolve_armed_tools, d, body, _tool_cap
            )
            armed, auto_armed = _selection
            # "adapted" MUST MEAN THE LOOP BENT, not that a budget existed —
            # the gate is the MEASURED drop signal, and the number printed is
            # the ceiling that actually bit (lock-step twin of chat_turn's
            # disclosure gate; the two reviewer repros — plain "hello" under a
            # cap, and 5 explicit picks under a cap of 3 — are pinned in
            # tests/test_chat_envelope_v1202.py for BOTH lanes).
            if _selection.dropped > 0:
                envelope_adapted = {
                    "model": _env_model,
                    "changes": [f"tool_cap:{_selection.ceiling}"],
                }
            armed += [t for t in conn_tools if t not in armed]
            # ASK-TIER ARMING (v1.187.0): show the model the host-reach verbs
            # this message signals a need for — VISIBLE, never GRANTED. They
            # join tool_specs so the model can call them, and deliberately
            # never join `armed`/`overrides`/`armed_grant`, so a call pauses
            # the turn for the user's approval (the mid-turn ask below). THIS
            # LANE ONLY: the non-stream lane serves headless callers (the comm
            # poller, the phone) where nobody is present to answer, and arming
            # a question no one can hear just manufactures denials.
            ask_armed = []
            if bool(getattr(body, "auto_tools", True)):
                from ...tools.autoselect import select_ask_tools

                ask_armed = [
                    t for t in select_ask_tools(_last_user_text(body.messages))
                    if t not in armed
                ]
                # WORKSPACE ASK (v1.210.0): a chat BOUND to a folder (the
                # Build pane) is a coding surface — `shell` joins the ask tier
                # so the model can propose a command without the user typing
                # "run" first. Same contract as every other ask_armed entry:
                # VISIBLE (tool_specs), never GRANTED — a call renders the
                # mid-turn ApprovalCard and waits for the human. STREAM LANE
                # ONLY, deliberately: the non-stream lane serves headless
                # callers where nobody is present to answer a card (the
                # documented asymmetry above).
                if (
                    (getattr(body, "workspace_dir", "") or "").strip()
                    and "shell" not in ask_armed
                    and "shell" not in armed
                    and d.platform.registry.get("shell") is not None
                ):
                    ask_armed.append("shell")
            tool_specs = (
                d.platform.registry.specs(armed + ask_armed)
                if (armed or ask_armed)
                else []
            ) + [
                _ESCALATE_SPEC,
                _WORKFLOW_DRAFT_SPEC,
            ]
        # THE POSTURE (v1.188.0): how the mid-turn ask behaves this turn.
        # Resolved ONCE, for BOTH branches above (a text-only pick still runs
        # the loop, and the loop reads it), so the prompt sentence below and
        # the card predicate can never read two different answers.
        approval_mode = normalize_approval_mode(
            getattr(body, "approval_mode", "")
        )
        # Card-grants made THIS conversation (an approval card's
        # "conversation" answer). Deliberately separate from `armed_grant`,
        # which starts as EVERY armed tool — strict mode must card a
        # write_document that auto-arming granted a moment ago, and a set
        # that begins full would make strict mode a no-op on exactly the
        # common case.
        card_grants: set[str] = set()
        ctx = None
        if armed or ask_armed:
            from ...tools.base import ToolContext

            # OFF THE EVENT LOOP (v1.195.0, finding 7) — MIRROR NOTE
            # (lock-step): same call in chat_turn.run_chat_turn, edit both or
            # neither. The resolution is stats + resolve()s + a mkdir against a
            # folder the USER picked (network share, unhydrated OneDrive), and
            # THIS is the lane the user is watching when the app goes "Daemon
            # offline". One hop for the whole block, not four.
            # v1.210.0: a BOUND workspace was already resolved by the
            # grounding block above — reuse that tuple (one resolution per
            # turn; the prompt block and this ToolContext must agree on the
            # folder). The hop runs only for the project-root / scratch path.
            if _ws_resolved is not None:
                tool_ws, in_project_folder = _ws_resolved
            else:
                tool_ws, in_project_folder = await asyncio.to_thread(
                    _resolve_tool_workspace,
                    d.platform.config.home / "uploads",
                    body.workspace_dir or "",
                    (resolved_proj.root or "") if resolved_proj is not None else "",
                )
            ctx = ToolContext(
                workspace=tool_ws, session_id="chat", agent_run_id="chat",
                config=d.platform.config, event_bus=d.platform.event_bus,
                engine=d.platform.engine,
                # v1.200.0: resolved-project tag for artifact sinks. MIRROR
                # NOTE (lock-step): non-stream copy in daemon/chat_turn.py.
                project_id=(pid if resolved_proj is not None else None),
            )
            explicit_armed = [
                t for t in armed if t not in auto_armed and t not in conn_tools
            ]
            system += (
                "\n\n# Tools\n"
                + (
                    "The user armed these tools for this chat: "
                    + ", ".join(explicit_armed)
                    + ". "
                    if explicit_armed
                    else ""
                )
                + (
                    "Auto-selected from this request: " + ", ".join(auto_armed) + ". "
                    if auto_armed
                    else ""
                )
                + (
                    "Connector tools the user toggled on: "
                    + ", ".join(conn_tools)
                    + ". "
                    if conn_tools
                    else ""
                )
                + "Use them when they help; answer directly when they don't."
                + (
                    "\nSPREADSHEET FIGURES: never compute numbers yourself —"
                    " call excel_query (profile the workbook first with"
                    " excel_profile) and report its computed results exactly."
                    if any(t.startswith("excel_") for t in armed)
                    else ""
                )
                + (
                    "\nREDACTION: scan first (redact_scan), present the"
                    " numbered findings, and get the user's confirmation of"
                    " exactly which to remove BEFORE calling redact_pii —"
                    " pass the confirmed values via terms."
                    if any(t.startswith("redact") for t in armed)
                    else ""
                )
                + (
                    # Lock-step with chat_turn.py's non-stream lane (v1.167.0):
                    # the dashboard STREAMS, so this — the lane users actually
                    # see — shipped without the PDF guidance for a full wave.
                    "\nPDF PAGES: for page-level PDF work (merge/split/rotate/"
                    "reorder) use pdf_arrange/pdf_split — they write NEW files"
                    " and never modify the original."
                    if any(t in ("pdf_arrange", "pdf_split") for t in armed)
                    else ""
                )
                + (
                    # Lock-step with chat_turn.py's non-stream lane (v1.170.0):
                    # the workflow tool sentences, each gated on ITS OWN
                    # arming. workflow_list is auto-safe and routinely arms
                    # ALONE while workflow_run is ask-gated and never
                    # auto-armed, so a combined any() gate had the prompt
                    # claim a runnable tool absent from tool_specs — a lie
                    # the model relays. The saved-workflows LIST rides the
                    # prompt above regardless.
                    "\nWORKFLOWS: workflow_list lists the user's saved workflows."
                    if "workflow_list" in armed
                    else ""
                )
                + (
                    "\nWORKFLOWS: workflow_run runs a saved workflow by name and"
                    " returns its run id — prefer running a saved workflow over"
                    " redoing its steps by hand."
                    if "workflow_run" in armed
                    else ""
                )
                + (
                    f"\nYour file tools operate INSIDE the folder {tool_ws}; "
                    "read, edit, and create files there directly, and use the absolute paths "
                    "that file_search returns."
                    if in_project_folder
                    else ""
                )
                + (
                    # ASK-TIER sentence (v1.187.0, THIS LANE ONLY — see the
                    # arming above): the model must know these tools pause for
                    # a human, or a denial reads to it as a broken tool and it
                    # retries the exact call the user just refused. The
                    # posture rewrites it (v1.188.0) because each mode makes a
                    # DIFFERENT promise and the prompt must not claim a pause
                    # that will not happen (yolo) or stay silent about ones
                    # that will (always_ask).
                    "\nAPPROVAL-GATED: "
                    + ", ".join(sorted(ask_armed))
                    + " are pre-approved for this conversation (the user"
                    " chose auto-approve) — they run without pausing. Still"
                    " prefer the specialized tools when one fits."
                    if ask_armed and approval_mode == "yolo"
                    else "\nAPPROVAL-GATED: "
                    + ", ".join(sorted(ask_armed))
                    + " will PAUSE this turn while the user is asked to"
                    " approve the call — the user sees the exact command/code"
                    " you pass. Use them when the task genuinely needs them;"
                    " prefer the specialized tools when one fits, and if the"
                    " user declines, do not retry the same call."
                    if ask_armed
                    else ""
                )
                + (
                    "\nSTRICT APPROVAL: the user chose to approve every file"
                    " edit and internet call — document/file-writing tools"
                    " and web_search/web_fetch also pause for approval."
                    " One approval per call unless the user grants the tool"
                    " for the conversation; if they decline, do not retry"
                    " the same call."
                    if approval_mode == "always_ask"
                    else ""
                )
                # Lock-step with chat_turn.py (v1.186.0). THIS is the lane the
                # dashboard uses, and the failure it was built from happened
                # here: a local model read nine documents and wrote nothing.
                # A directive that reached only the non-stream lane would have
                # fixed the report without fixing the user's experience of it.
                + _write_directive(body, armed)
            )
        overrides: dict[str, str] = {}
        for _name in armed:
            overrides[_name] = "allow"
            _tool = d.platform.registry.get(_name)
            if _tool is not None:
                overrides[_tool.perm_key()] = "allow"
        armed_grant = set(overrides.keys())
        # (provider_choice/model_choice were resolved above the attachments.)

        # ------------------------------------------------------------------ #
        # STREAM — the round + tool loop, emitting SSE frames as it goes.
        # ------------------------------------------------------------------ #
        async def gen():
            usage_in = usage_out = completions = 0
            tools_used: list[str] = []          # ONLY tools that actually executed
            denied_tools: list[str] = []        # armed tools refused this turn
            # DOORS (v1.199.0): links into the surface a SUCCESSFUL creating
            # tool just changed. Appended only inside the `if ran:` block —
            # the same gate as tools_used, so a failed/denied call can never
            # mint one. MIRROR NOTE (lock-step): chat_turn.py's tool loop
            # carries the same collection — edit both or neither.
            door_entries: list[dict[str, str] | None] = []
            last_tool_output = ""               # last SUCCESSFUL output (synthesis)
            stopped_note = ""                   # round budget cut off tool calls
            escalate = False        # the turn asked for the full agent
            escalate_reason = ""
            escalate_agent = None   # v1.139.0: validated roster target (None = default)
            workflow_draft = None           # proposed reusable workflow (v1.120.0)
            made_docs: list[str] = []           # documents created/edited (preview)
            workflow_run_info = None    # v1.170.0: workflow this turn STARTED (contract 2)
            reply_text = ""
            route_provider = provider_choice or ""
            route_model = model_choice or ""
            # ROUTE DISCLOSURE (v1.165.0): filled from the router's final
            # frame each round; `requested` seeds from the explicit pick ("" =
            # default route) so even an errored turn reports what was asked.
            route_requested = provider_choice or ""
            route_reason = ""
            # USAGE LEDGER, EXACTLY ONE TERMINAL ROW. Every terminal path below
            # goes through this helper, so the cancellation guards can run
            # unconditionally without ever writing a second row for the same
            # turn. It reads the live counters/route by closure, which is what
            # makes it correct from an exception handler.
            persisted = False

            def _persist_once(state: AgentState) -> None:
                nonlocal persisted
                if persisted or not completions:
                    return
                persisted = True
                _persist_chat_usage(
                    d, provider=route_provider, model=route_model,
                    state=state, completions=completions,
                    usage_in=usage_in, usage_out=usage_out,
                )

            try:
                for _round in range(_MAX_TOOL_ROUNDS):
                    if await request.is_disconnected():
                        # The completed rounds were billed even though the
                        # client walked away — keep the ledger honest.
                        _persist_once(AgentState.CANCELLED)
                        return
                    yield _sse("round", {"round": _round})
                    final_resp = None
                    async for frame in _router_frames(
                        d.platform.router,
                        provider=provider_choice or None,
                        model=model_choice or None,
                        system=system,
                        messages=msgs,
                        tools=tool_specs,
                        task_class="chat",
                    ):
                        ftype = frame.get("type")
                        if ftype == "text":
                            txt = frame.get("text") or ""
                            if txt:
                                yield _sse("token", {"text": txt})
                        elif ftype == "meta":
                            route_provider = frame.get("provider") or route_provider
                            route_model = frame.get("model") or route_model
                            yield _sse(
                                "meta",
                                {"provider": route_provider, "model": route_model},
                            )
                        elif ftype == "reset":
                            # A pre-first-token failover swapped providers — tell the
                            # client to discard any partial text streamed so far.
                            yield _sse("reset", {"reason": frame.get("reason", "")})
                        elif ftype == "final":
                            final_resp = frame.get("response")
                            route_provider = frame.get("provider") or route_provider
                            route_model = frame.get("model") or route_model
                            # Route disclosure off the final frame (v1.165.0).
                            # `requested` is legitimately "" on the default
                            # route, so test MEMBERSHIP, not truthiness — an
                            # `or` here would silently keep the seed value and
                            # mask a router that reports differently.
                            if "requested" in frame:
                                route_requested = str(frame.get("requested") or "")
                            route_reason = str(frame.get("reason") or route_reason)
                    if final_resp is None:
                        # The stream ended without an aggregate — honest error, not
                        # a fabricated reply. Completed rounds still get counted.
                        _persist_once(AgentState.FAILED)
                        yield _sse(
                            "error",
                            {"detail": "stream ended without a final response"},
                        )
                        return
                    reply_text = final_resp.text or ""
                    _u = final_resp.usage or {}
                    usage_in += int(_u.get("input_tokens", 0) or 0)
                    usage_out += int(_u.get("output_tokens", 0) or 0)
                    completions += 1
                    calls = final_resp.tool_calls or []
                    # THE DRAFT, THREE WAYS (v1.225.0) — lock-step copy of
                    # chat_turn: the exit tool, an unarmed workflow_create,
                    # or JSON written in a text-only reply.
                    workflow_draft = _draft_from_calls(calls, armed)
                    if workflow_draft is None and not calls:
                        workflow_draft = _draft_from_text(reply_text)
                    if workflow_draft is not None:
                        break
                    esc_call = next(
                        (c for c in calls if c.name == _ESCALATE_TOOL), None
                    )
                    if esc_call is not None:
                        escalate = True
                        _esc_args = esc_call.arguments or {}
                        escalate_reason = str(_esc_args.get("reason") or "").strip()
                        # v1.139.0 roster target — validated; None keeps every
                        # caller default. MIRROR NOTE (lock-step): same
                        # extraction as chat_turn.run_chat_turn's escalate
                        # branch — edit both or neither.
                        escalate_agent = _validated_escalate_agent(
                            d.platform, _esc_args.get("agent")
                        )
                        break
                    # ask_armed counts (v1.187.0): a turn whose ONLY armed
                    # verbs are approval-gated still has real calls to run.
                    if not calls or not (armed or ask_armed):
                        break
                    if _round == _MAX_TOOL_ROUNDS - 1:
                        # LAST allowed round (mirrors chat_complete): no round is
                        # left to show the model these results — skip, say so.
                        stopped_note = (
                            f"stopped after {_round} tool rounds; "
                            f"{len(calls)} tool call(s) not executed"
                        )
                        escalate = True
                        escalate_reason = escalate_reason or (
                            "this needs more steps than a quick answer allows"
                        )
                        break
                    msgs.append(LLMMessage(role="assistant",
                                           content=final_resp.text,
                                           tool_calls=calls))
                    for tc in calls:
                        ran = False
                        _t = d.platform.registry.get(tc.name)
                        # REDACT args before they cross the wire — a planted secret
                        # (secrets/computeruse tools redact) never streams to the
                        # browser; same guard the DB-persist path uses.
                        safe_args = (
                            _t.redact_args(tc.arguments) if _t is not None else tc.arguments
                        )
                        # MID-TURN APPROVAL (v1.187.0). The two halves of this
                        # mechanism predate it: `authorize` names the
                        # interactive session grant as the sanctioned lift for
                        # an ask-tier tool, and `invoke` has carried
                        # `deny_reason=` since v1.155.0 for "a caller that
                        # already asked a human and was refused". Nothing in
                        # chat ever ASKED — an ask-tier call was silently
                        # denied and the user learned from a footnote. Now the
                        # turn pauses, the card renders, and the decision is
                        # genuinely the user's — including the refusal, which
                        # is why the deny path still calls `invoke`: the
                        # refusal must reach the ledger as a decision a human
                        # made, not vanish as a call that never happened.
                        #
                        # BEFORE the "started" frame, so the tool card never
                        # spins while the app is waiting on a human.
                        _deny_reason = ""
                        _grant_extra: set[str] = set()
                        _perm_name = _t.perm_key() if _t is not None else tc.name
                        _mode = d.platform.permissions.mode_for(_perm_name, overrides)
                        # THE POSTURE DECIDES WHETHER A CARD RENDERS
                        # (v1.188.0). The engine's answer is unchanged in
                        # every mode — the posture only chooses when to put a
                        # human between an *askable* call and its execution:
                        #   approve_for_me  engine-ask only (v1.187.0);
                        #   always_ask      engine-ask PLUS file edits + web,
                        #                   unless a card already granted the
                        #                   tool this conversation;
                        #   yolo            never — an engine-ask is granted
                        #                   because the user pre-approved it
                        #                   from the dropdown. A base `deny`
                        #                   never reaches this branch (it is
                        #                   not ASK) and `invoke` refuses it
                        #                   in yolo exactly as everywhere.
                        _engine_asks = (
                            _mode is PermissionMode.ASK
                            and _perm_name not in armed_grant
                            and tc.name not in armed_grant
                        )
                        if approval_mode == "yolo":
                            if _engine_asks:
                                _grant_extra = {tc.name, _perm_name}
                            _needs_card = False
                        elif approval_mode == "always_ask":
                            _needs_card = _engine_asks or (
                                tc.name in STRICT_ASK_TOOLS
                                and tc.name not in card_grants
                                and _mode is not PermissionMode.DENY
                            )
                        else:
                            _needs_card = _engine_asks
                        if _needs_card:
                            _apr = _approvals()
                            _ap_id, _fut = _apr.request(tc.name, safe_args)
                            yield _sse("approval", {
                                "id": _ap_id, "call_id": tc.id,
                                "tool": tc.name, "args": safe_args,
                                "timeout_s": int(APPROVAL_TIMEOUT_S),
                            })
                            _decision = "timeout"
                            _aloop = asyncio.get_running_loop()
                            _deadline = _aloop.time() + APPROVAL_TIMEOUT_S
                            try:
                                while True:
                                    _left = _deadline - _aloop.time()
                                    if _left <= 0:
                                        break
                                    try:
                                        # shield: a keepalive slice expiring
                                        # must not CANCEL the future — the
                                        # user's click can land in the next
                                        # slice.
                                        _decision = await asyncio.wait_for(
                                            asyncio.shield(_fut),
                                            timeout=min(15.0, _left),
                                        )
                                        break
                                    except asyncio.TimeoutError:
                                        # SSE comment — keeps the connection
                                        # alive through a slow human decision
                                        # without inventing a frame type.
                                        yield ": keepalive\n\n"
                            finally:
                                _apr.pop(_ap_id)
                            yield _sse("approval_resolved", {
                                "id": _ap_id, "call_id": tc.id,
                                "tool": tc.name, "decision": _decision,
                            })
                            if _decision == "once":
                                _grant_extra = {tc.name, _perm_name}
                            elif _decision == "conversation":
                                # Rest of THIS turn's rounds; the client
                                # persists it for later turns by arming the
                                # tool (the existing "+"-menu machinery — not
                                # a second grant store). IN-PLACE update, not
                                # `|=`: an augmented assignment would bind
                                # `armed_grant` as a LOCAL of this generator
                                # and unbind every earlier read of the
                                # enclosing scope's set. `card_grants` is what
                                # stops strict mode re-carding this tool —
                                # armed_grant alone cannot say WHO granted.
                                armed_grant.update({tc.name, _perm_name})
                                card_grants.update({tc.name, _perm_name})
                            elif _decision == "deny":
                                _deny_reason = (
                                    "you declined this call when asked"
                                )
                            else:
                                _deny_reason = (
                                    "the approval request timed out with no"
                                    " answer"
                                )
                        yield _sse("tool_call", {
                            "id": tc.id, "name": tc.name,
                            "status": "started", "args": safe_args,
                        })
                        try:
                            # deny_reason rides ONLY when a human actually
                            # refused — the common path stays byte-identical
                            # with every existing caller (and every test
                            # double) of this five-argument invoke.
                            result = await d.platform.registry.invoke(
                                tc.name, tc.arguments, ctx, d.platform.permissions,
                                overrides,
                                session_allow=(armed_grant | _grant_extra),
                                **(
                                    {"deny_reason": _deny_reason}
                                    if _deny_reason
                                    else {}
                                ),
                            )
                            if result.ok:
                                content = result.output
                                ran = True
                                last_tool_output = str(result.output or "")
                            else:
                                content = result.error or "error"
                                if "permission denied" in (result.error or ""):
                                    denied_tools.append(tc.name)
                        except Exception as exc:  # noqa: BLE001
                            content = f"{type(exc).__name__}: {exc}"
                        if ran:
                            tools_used.append(tc.name)
                            # DOOR (v1.199.0): a successful creating tool
                            # opens a link into its surface. Same gate as
                            # tools_used — inside this `if ran:` — so honesty
                            # is enforced at the call site. MIRROR NOTE
                            # (lock-step): chat_turn.py's tool loop carries
                            # the same append — edit both or neither.
                            door_entries.append(door_for(tc.name, result))
                            # WORKFLOW RUN RECEIPT (v1.170.0, contract 2): a
                            # SUCCESSFUL workflow_run's {run_id, workflow}
                            # rides the done frame as `workflow_run` so the
                            # client renders the live run under this reply.
                            # Only a run the tool actually started counts —
                            # a failed/denied call leaves the key absent —
                            # and only with a real run id, because a chip
                            # pointing at no run would poll a 404 forever.
                            # The last successful call wins. MIRROR NOTE
                            # (lock-step): chat_turn.py's tool loop carries
                            # this same capture — edit both or neither.
                            if tc.name == "workflow_run":
                                _wr = getattr(result, "data", None) or {}
                                _wr_id = str(_wr.get("run_id") or "").strip()
                                if _wr_id:
                                    workflow_run_info = {
                                        "run_id": _wr_id,
                                        "name": str(
                                            _wr.get("workflow") or ""
                                        ).strip(),
                                    }
                            # Track created/edited documents for the preview
                            # (mirrors chat_complete).
                            if tc.name in _DOC_WRITING_TOOLS:
                                _rel = str(
                                    (getattr(result, "data", None) or {}).get("path")
                                    or ""
                                )
                                if _rel:
                                    try:
                                        _abs = str((tool_ws / _rel).resolve())
                                        if _abs not in made_docs:
                                            made_docs.append(_abs)
                                    except Exception:  # noqa: BLE001
                                        pass
                            # EVERY file a turn creates is disclosed, not just
                            # the document tools' (v1.165.0): merge the
                            # ABSOLUTE ToolResult.created_paths (repl's
                            # workspace diff, batch jobs) so a repl-written
                            # file reaches `documents` here too. Call order
                            # kept, deduped against the doc-tool entries.
                            # ABSOLUTE paths only — the contract says absolute
                            # (tools/base.py); a relative name from a lying
                            # tool is an unverifiable claim and resolving it
                            # against a guessed base could disclose the WRONG
                            # file. MIRROR NOTE (lock-step): chat_turn.py's
                            # tool loop carries this same merge — edit both
                            # or neither.
                            for _cp in getattr(result, "created_paths", None) or []:
                                _cp = str(_cp)
                                try:
                                    if not Path(_cp).is_absolute():
                                        continue
                                except (OSError, ValueError):
                                    continue
                                if _cp not in made_docs:
                                    made_docs.append(_cp)
                            # FENCE externally-sourced output before the model (and
                            # the client) sees it — the same guard chat_complete +
                            # the agent runtime apply to returns_untrusted_content.
                            if getattr(_t, "returns_untrusted_content", False):
                                from ...computeruse.safety import (
                                    detect_injection,
                                    wrap_untrusted,
                                )

                                _inj = detect_injection(str(content))
                                content = wrap_untrusted(
                                    f"[content withheld — suspected {_inj['category']}: "
                                    f"{_inj['reason']}]"
                                    if _inj["flagged"]
                                    else str(content)
                                )
                        yield _sse("tool_call", {
                            "id": tc.id, "name": tc.name, "status": "finished",
                            "ok": ran, "output": str(content)[:2000],
                        })
                        msgs.append(LLMMessage(role="tool", tool_call_id=tc.id,
                                               name=tc.name, content=str(content)[:12000]))
            except Exception as exc:  # noqa: BLE001 — honest error, never fabricate
                # Completed rounds were still billed — persist BEFORE the error
                # frame (mirrors chat_complete's failure path); the client sees
                # the same error either way.
                _persist_once(AgentState.FAILED)
                yield _sse("error", {"detail": str(exc)})
                return
            except BaseException:
                # STOP MID-GENERATION. When the client aborts DURING a round,
                # Starlette cancels this generator at its current await
                # (CancelledError) or, if it is parked at a yield, at
                # finalization (GeneratorExit). Both are BaseException-shaped:
                # they skip the round-TOP disconnect check above AND the
                # handler above, so every COMPLETED earlier round — already
                # counted at its `final` frame and already billed by the
                # provider — used to vanish from the ledger entirely. No frame
                # is emitted here (the connection is gone) and the exception is
                # re-raised unchanged, so cancellation still means cancellation.
                _persist_once(AgentState.CANCELLED)
                raise

            # LANGUAGE GUARD (v1.144.0) — the lock-step copy of chat_turn's, and
            # like it, run BEFORE the ledger so a rewrite is billed.
            #
            # STREAM-SPECIFIC NOTE: the leaked text has already been streamed to
            # the client token by token, so the correction lands in the `done`
            # frame instead — which useChatStream treats as AUTHORITATIVE
            # ("done.reply is authoritative; fall back to the accumulated text"),
            # so the finished bubble and the saved thread both carry the
            # corrected reply. The user may see the wrong-language text flicker
            # during generation; that is honest (it IS what the model produced)
            # and needs no client change. MIRROR NOTE (lock-step): chat_turn.
            try:
                reply_text, lang_note, _l_in, _l_out, _l_n = await _enforce_language(
                    d.platform,
                    text=reply_text or "",
                    user_text=_last_user_text(body.messages),
                    system=system,
                    messages=msgs,
                    provider=provider_choice,
                    model=model_choice,
                )
            except BaseException:
                # The one remaining await between the last billed round and the
                # COMPLETED row below (it calls a model when it rewrites): a
                # Stop delivered HERE drops exactly the same already-billed
                # tokens as one delivered inside the loop.
                _persist_once(AgentState.CANCELLED)
                raise
            usage_in += _l_in
            usage_out += _l_out
            completions += _l_n

            # USAGE LEDGER — persist the run row exactly as chat_complete does so a
            # streamed turn counts the same on the Usage page.
            _persist_once(AgentState.COMPLETED)

            # Reply honesty (mirrors chat_complete): synthesize from the last tool
            # output when the model returned no final text; note denied tools.
            # THE DRAFT CARRIES THE CHAT'S PROJECT (v1.225.0) — lock-step copy
            # of chat_turn's stamp; this is the lane the card is born in.
            if workflow_draft is not None and resolved_proj is not None and pid:
                workflow_draft["project_id"] = pid
            reply = reply_text or ""
            if workflow_draft is not None:
                # Mirrors chat_complete: a draft exit is a success — no
                # placeholder, no creation-honesty note.
                reply = reply.strip()
                if denied_tools:
                    names = ", ".join(dict.fromkeys(denied_tools))
                    reply += f"\n\n_Note: {names} could not run (permission denied)._"
            else:
                if not reply.strip() and last_tool_output:
                    snippet = last_tool_output.strip()[:600]
                    ran_names = ", ".join(dict.fromkeys(tools_used)) or "the armed tools"
                    reply = f"Ran {ran_names}. Result:\n{snippet}"
                elif not reply.strip():
                    reply = "(no reply)"
                if denied_tools:
                    names = ", ".join(dict.fromkeys(denied_tools))
                    reply += f"\n\n_Note: {names} could not run (permission denied)._"
                if stopped_note:
                    reply += f"\n\n_Note: {stopped_note}._"
                if lang_note:
                    reply += f"\n\n_Note: {lang_note}._"
                _ctx_note = plan.note()
                if _ctx_note:
                    reply += f"\n\n_Note: {_ctx_note}._"
                reply += _creation_honesty_note(body, armed, tools_used)
                # v1.153.2: and check the reply's own CLAIMS against the
                # ledger — the note above keys off the user's phrasing and
                # so missed a reply announcing a saved file after only a
                # scan had run. MIRROR NOTE (lock-step): both lanes.
                reply += _claimed_write_note(reply, tools_used)
            if text_only_pick and (body.tools or []):
                reply += (
                    f"\n\n_Note: {provider_choice} can't run tools — this "
                    f"turn was answered text-only._"
                )
            done_frame: dict[str, Any] = {
                "reply": reply,
                "provider": route_provider,
                "model": route_model,
                # ROUTE DISCLOSURE (v1.165.0) — the identical object POST
                # /chat returns: server-side truth of WHO answered and WHY,
                # because the client-side "answered by X" chip is silent on
                # the default route. Top-level provider/model stay untouched
                # for existing clients. MIRROR NOTE (lock-step): the
                # non-stream response dict in chat_turn.py carries the same
                # object — edit both or neither.
                "route": {
                    "requested": route_requested,
                    "provider": route_provider,
                    "model": route_model,
                    "reason": route_reason,
                },
                "tools_used": tools_used,
                # DOORS (v1.199.0): server-derived links into the surfaces
                # this turn's SUCCESSFUL creating tools changed — deduped by
                # href, capped at 4, ALWAYS present (possibly empty). Files
                # are deliberately not doors (the ArtifactsRail owns files).
                # MIRROR NOTE (lock-step): the non-stream response dict in
                # chat_turn.py carries the identical key — edit both or
                # neither.
                "doors": collect_doors(door_entries),
                # ENVELOPE ADAPTATION (v1.202.0): {"model", "changes":
                # ["tool_cap:<n>", ...]} when the capability envelope bent
                # this turn, else null — ALWAYS PRESENT (null, never absent),
                # like doors' [], pinning lane parity on absent-vs-null.
                # MIRROR NOTE (lock-step): the non-stream response dict in
                # chat_turn.py carries the identical key — edit both or
                # neither.
                "adapted": envelope_adapted,
                "denied_tools": denied_tools,
                "auto_armed": auto_armed,
                "documents": made_docs,
                "escalate": escalate,
                "escalate_reason": escalate_reason,
                # v1.139.0 pinned contract change: the validated roster target
                # (None = the caller's default builder), same as POST /chat.
                "escalate_agent": escalate_agent,
                "workflow_draft": workflow_draft,
                "usage": {"input_tokens": usage_in, "output_tokens": usage_out},
                # v1.146.0 + v1.153.0 — same shape POST /chat returns, so the
                # composer's headroom meter and the compaction offer behave
                # identically on both lanes.
                "context": {**plan.as_dict(), **context_report},
            }
            # CONTRACT 2 (v1.170.0): present ONLY when this turn's tool loop
            # actually started a workflow run — absent otherwise (including
            # failed calls), so clients key off the key itself, never a null.
            # MIRROR NOTE (lock-step): the non-stream response dict in
            # chat_turn.py carries the same conditional key — edit both or
            # neither.
            if workflow_run_info is not None:
                done_frame["workflow_run"] = workflow_run_info
            yield _sse("done", done_frame)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
