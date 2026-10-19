"""The MISSION view (v1.307.0): one objective, one team, read off the record.

The Agents page's mission layout shows the deliverable first and the
workforce underneath it: a coordinator session (the Team — a SUPERVISOR run)
and every teammate it handed work to. This module composes that view from
data the app already keeps — nothing here is new state:

* MEMBERS come from the delegation tree (``AgentRun.parent_id`` links, the
  same walk ``GET /sessions/{id}/team`` does, depth-capped at the delegation
  limit), plus remote teammates, which open no session and are known only
  from their ``delegation.*`` events.
* PROGRESS is honest or absent. ``done`` is 100%, a run that has not started
  is 0%, and a running member shows a percentage ONLY when there is a count
  behind it: its plan's completed steps (``plan.*`` events of the decomposed
  lane). Otherwise ``pct`` is ``None`` and the label says what IS known
  ("working · step 3"). A bar that moved because a clock ticked would be a
  claim the record cannot support — the one thing this app refuses.
* ACTIVITY is a human-readable log built from the ledger
  (``ToolInvocation`` — whose args are stored REDACTED) and the persisted
  events (delegations, plans, asks, completions), newest last. One renderer,
  server side, so the page never guesses what a tool name means.
* The DELIVERABLE is the coordinator's own result once it has one, plus the
  files the whole team created or changed (journaled mutations, never the
  prose — the exact ``outcome.session_result`` documents semantics, computed
  team-wide in one read since v1.309.0: see ``_team_documents``).

THE POLL IS CHEAP (v1.309.0, mission-poll-n-plus-one / -recomputes-whole-
ledger). The page asks for this view every 2 s while a mission runs. It used
to cost a ``db.get`` per member plus a full ``session_result`` per member —
each of which read every ``ToolInvocation`` row of that member WITH its
``output`` (up to 4,000 chars a row) — so a team of eight with a few hundred
tool calls was ~60 statements and megabytes of text per tick, for a view
that shows none of those outputs. Now the statement count does not grow with
the team (one batched member read, one team-wide journal join, explicit
columns everywhere, ``output`` never selected) and the route answers an ETag,
so an unchanged tick is a bodiless 304. Pinned by statement count, never by
a clock (``tests/test_wave1_mission_view_v1309.py``).

BLOCKING (SQLite reads): the route calls it from FastAPI's threadpool. Never
raises past a missing session (``None``); every sub-read degrades to less.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import literal_column
from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.models import (
    AgentRun,
    AgentState,
    EventRecord,
    Session,
    SessionStatus,
    ToolInvocation,
    UndoJournal,
)

#: Delegation depth cap (``delegate_tool._MAX_DELEGATION_DEPTH``) — the walk
#: stops there, which also makes a corrupt parent_id cycle harmless.
_MAX_DEPTH = 3
_MAX_MEMBERS = 24
#: Rows of activity the view carries (newest kept).
_ACTIVITY_MAX = 80
#: How many recent ledger/event rows are read to build it.
_SCAN_ROWS = 240
_TASK_CHARS = 140
_RESULT_CHARS = 600

COORDINATOR = "Jarvis"

#: The origin the mission door stamps on its coordinator. ONE definition:
#: ``agents/team.py`` owns it (and the teammates' ``job:mission-member``).
from .team import MISSION_ORIGIN  # noqa: E402

#: Route changes the view reads for its ``route_note`` (v1.309.0,
#: mission-no-route-receipt) — a separate, small read, so a busy mission's
#: 240-row event window can never push the one failover out of sight.
_ROUTE_EVENT_TYPES = (EventType.PROVIDER_FAILOVER, EventType.PROVIDER_DOWNGRADED)
_ROUTE_ROWS = 20
#: How many candidate rows the ``continued_as`` lookup verifies.
_CONTINUATION_ROWS = 10

#: WHO REFUSED A CALL (v1.309.0 review). A refused call is ledgered
#: ``verdict=ask, ok=False`` whoever refused it: ``registry.invoke`` builds
#: ``PermissionDecision(False, ASK, deny_reason)`` for ANY caller-supplied
#: refusal, so the runtime's repeated-failure breaker, the roster gate and
#: the low-trust refusal share the verdict with a real ask. Only the
#: ``tool.denied`` event's ``kind`` (the registry's ``deny_label``) says which
#: it was. These two are the approval pause: the user said no / nothing here
#: could ask ("permission denied" — an engine ASK refusal or the pause's
#: decline) and the pause timed out ("paused").
_ASK_DENIAL_KINDS = frozenset({"permission denied", "paused"})
#: The other refusals, in plain words — nobody was asked, so none of them may
#: read "needed your OK" (a breaker-refused ``read_file`` is an allow-tier
#: tool). ``{t}`` is the tool's own name (``tool_own_name``: spaces, an
#: app's tool as "<app>: <its tool in words>").
_REFUSAL_WORDS: dict[str, str] = {
    "refused": "was stopped from repeating a {t} call that kept failing",
    "not armed": "tried {t}, a tool it was not given — nothing ran",
    "low trust": "was kept from using {t} (low trust)",
}

_EVENT_TYPES = (
    EventType.DELEGATION_STARTED,
    EventType.DELEGATION_COMPLETED,
    EventType.PLAN_CREATED,
    EventType.PLAN_STEP_STARTED,
    EventType.PLAN_STEP_COMPLETED,
    EventType.APPROVAL_REQUESTED,
    EventType.APPROVAL_RESOLVED,
    EventType.SESSION_COMPLETED,
)


def display_name(roster_name: str) -> str:
    """``custom:tax-reader`` → ``tax-reader``; ``researcher`` → ``Researcher``;
    ``remote:hermes`` → ``hermes``. A user's own name is never re-cased."""
    text = " ".join(str(roster_name or "").split())
    if not text:
        return "Agent"
    head, sep, tail = text.partition(":")
    if sep and head.lower() in ("custom", "remote", "builtin", "dynamic"):
        return tail.strip() or text
    return text[:1].upper() + text[1:]


def member_kind(roster_name: str) -> str:
    low = str(roster_name or "").lower()
    if low.startswith("custom:"):
        return "custom"
    if low.startswith("remote:"):
        return "remote"
    return "builtin"


def _short(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _iso(moment: Any) -> str | None:
    try:
        return moment.isoformat() if moment is not None else None
    except Exception:  # noqa: BLE001
        return None


def _payload(row: EventRecord) -> dict[str, Any]:
    try:
        data = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _args(inv: ToolInvocation) -> dict[str, Any]:
    try:
        data = json.loads(inv.args_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _base(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        return Path(text).name or text
    except Exception:  # noqa: BLE001
        return text


#: tool name -> (verb when it worked, verb when it failed). ``{x}`` is filled
#: from the call's (redacted) arguments when one is available.
_TOOL_WORDS: dict[str, tuple[str, str]] = {
    "read_file": ("read {x}", "could not read {x}"),
    "read_document": ("read {x}", "could not read {x}"),
    "extract_pdf": ("read the PDF {x}", "could not read the PDF {x}"),
    "view_image": ("looked at {x}", "could not open the image {x}"),
    "list_files": ("looked through a folder", "could not list a folder"),
    "list_folder": ("looked through a folder", "could not list a folder"),
    "grep": ("searched the files for “{q}”", "searched the files and found nothing usable"),
    "file_search": ("searched your drives for “{q}”", "searched your drives without a result"),
    "write_file": ("wrote {x}", "could not write {x}"),
    "write_document": ("created {x}", "could not create {x}"),
    "edit_file": ("edited {x}", "could not edit {x}"),
    "rename_file": ("renamed {x}", "could not rename {x}"),
    "docx_edit": ("edited {x}", "could not edit {x}"),
    "excel_edit": ("updated the workbook {x}", "could not update the workbook {x}"),
    "excel_read": ("read the workbook {x}", "could not read the workbook {x}"),
    "web_search": ("searched the web for “{q}”", "searched the web without a result"),
    "browse": ("opened a web page", "could not open a web page"),
    "web_extract": ("read a web page", "could not read a web page"),
    "recall": ("checked memory for “{q}”", "checked memory without a result"),
    "ltm_search": ("searched the knowledge base for “{q}”", "searched the knowledge base without a result"),
    "memory_search": ("searched notes for “{q}”", "searched notes without a result"),
    "shell": ("ran a command", "ran a command that failed"),
    "consult": ("asked {a} for advice", "could not reach {a} for advice"),
    "blackboard_post": ("posted a note for the team", "could not post a note"),
    "message_agent": ("messaged a teammate", "could not message a teammate"),
    "worklist_add": ("listed the items to work through", "could not list the items"),
    "worklist_done": ("finished an item", "could not record an item"),
    "remember_preference": ("noted a preference", "could not note a preference"),
    "notebook": ("updated its notebook", "could not update its notebook"),
    # v1.358.0 (wave 8, lane 8C): the chat's page reader (the same words as
    # the work line's ``page`` kind, dashboard/lib/stepWords "Read a web
    # page"), and one call to a saved web service, named by the service
    # (the work line's "Called crm"; never its path, method, body or key).
    "web_fetch": ("read a web page", "could not read a web page"),
    "rest_call": ("called {s}", "could not call {s}"),
}

#: What an ASK wants to do, in the approval card's own words (v1.352.0, one
#: approval vocabulary): "needed your OK to save a file" beside a card that
#: says "Waiting for your OK to save a file.", never "…to use write file".
#: The Python twin of ``dashboard/lib/toolWords.ts`` for the built-ins an
#: agent run asks about most; every phrase is pinned EQUAL to that file
#: (``tests/test_receipt_seams_v13520.py``). Built-ins only, by exact name;
#: any other tool keeps "use <its name>" (never invented words).
ASK_WORDS: dict[str, str] = {
    "write_file": "save a file",
    "edit_file": "change a file",
    "rename_file": "rename or move a file",
    "shell": "run a command",
    "repl": "run some code",
    "run_code": "run some code",
    "web_fetch": "read a web page",
    "web_search": "search the web",
    # v1.357.0 (G84): a saved web service (integrations/tools.RestCallTool).
    "rest_call": "call one of your saved web services",
    # v1.358.0 (integrate, G41): the chat's refusal footnote read "Jarvis
    # couldn't use app connect" / "use generate media". The card's words
    # (lib/toolWords WORDS, pinned equal by test_receipt_seams_v13520).
    "app_connect": "connect an app",
    "write_document": "create a document",
    "excel_edit": "edit a workbook",
    "generate_media": "make an image, video or sound",
}


def ask_words(tool: str) -> str:
    """What an ask for ``tool`` wants to do ("save a file"), else "use <its
    name>". Pure."""
    name = str(tool or "").strip()
    return ASK_WORDS.get(name) or f"use {name.replace('_', ' ') or 'a tool'}"


#: Tools whose lines are bookkeeping, not work — kept out of the log.
_QUIET_TOOLS = frozenset(
    {"delegate", "spawn_agent", "worklist_next", "worklist_status", "blackboard_read",
     "recall_lessons", "skill_search", "skill_load", "tool_list", "list_agents"}
)


# --------------------------------------------------------------------------- #
# v1.358.0 (wave 8, lane 8C, G19): a tool _TOOL_WORDS does not word reads in
# the DASHBOARD's words, never its id. The run card's "What it did" fold and
# the mission log used to say "Builder used history search" and, for a
# connected app, "used mcp  gmail  search messages". These tables are the
# Python twins of dashboard/lib/runWords.ts (AGENT_TOOLS + KIND_NOUN),
# lib/workTarget.ts (KIND_OF), lib/stepWords.ts (STEP_WORDS[kind].count(1))
# and lib/toolWords.ts (appNameWords / appToolNameWords); ``fallback_line`` is
# ``toolStepLine`` there. ONE fixture holds both to the same words:
# dashboard/__tests__/fixtures/tool-lines-v13580.json (written and checked by
# tests/test_one_receipt_v13580.py, read back by
# dashboard/__tests__/one-receipt-v13580.test.tsx).
# --------------------------------------------------------------------------- #

#: lib/runWords.ts AGENT_TOOLS: (one call that worked, one call as a noun).
_RUN_TOOL_WORDS: dict[str, tuple[str, str]] = {
    "history_search": ("searched your chats", "chat search"),
    "memory_read": ("looked up a memory", "memory lookup"),
    "memory_search": ("searched its memory", "memory search"),
    "memory_write": ("saved to its memory", "memory save"),
    "memory_propose": ("suggested something to remember", "memory suggestion"),
    "recall": ("searched everything Jarvis knows", "recall"),
    "recall_lessons": ("checked its lessons", "lesson check"),
    "ltm_search": ("searched your knowledge base", "knowledge base search"),
    "ltm_append": ("added to your knowledge base", "knowledge base note"),
    "file_search": ("searched your drives", "drive search"),
    "browse": ("opened a web page", "web page visit"),
    "web_extract": ("read part of a web page", "web page read"),
    "excel_query": ("queried a workbook", "workbook query"),
    "compare_documents": ("compared documents", "document compare"),
    "consult": ("asked a teammate for advice", "request for advice"),
    "delegate": ("handed work to a teammate", "hand-off"),
    "spawn_agent": ("started a helper agent", "helper agent start"),
    "message_agent": ("messaged a teammate", "message to a teammate"),
    "blackboard_post": ("posted a note for the team", "team note"),
    "blackboard_read": ("read the team's notes", "team notes read"),
    "worklist_add": ("listed the items to work through", "worklist update"),
    "worklist_next": ("picked the next item to work on", "worklist pick"),
    "worklist_done": ("finished an item on its list", "worklist item"),
    "worklist_status": ("checked its list", "worklist check"),
    "skill_search": ("looked for a skill", "skill search"),
    "skill_load": ("loaded a skill", "skill load"),
    "remember_preference": ("noted a preference", "preference note"),
    "notebook": ("updated its notebook", "notebook update"),
    "guide_search": ("searched the Iron Jarvis guide", "guide search"),
    "guide_read": ("read the Iron Jarvis guide", "guide read"),
    "app_search": ("searched your Iron Jarvis setup", "setup search"),
    "app_status": ("checked your Iron Jarvis setup", "setup check"),
}

#: browser/tools.py's 14 built-in browser tools, by EXACT name (the
#: dashboard's lib/toolWords BROWSER_TOOLS) — never a ``browser_`` prefix.
_BROWSER_TOOLS = frozenset({
    "browser_get_status", "browser_list_tabs", "browser_get_active_tab",
    "browser_read_page", "browser_get_elements", "browser_screenshot",
    "browser_activate_tab", "browser_scroll", "browser_create_tab",
    "browser_close_tab", "browser_click", "browser_type", "browser_press_key",
    "browser_navigate",
})

#: lib/workTarget.ts KIND_OF: the built-ins the work line words by kind.
_STEP_KIND: dict[str, str] = {
    **dict.fromkeys(
        ("read_file", "read_document", "extract_pdf", "view_image", "excel_read",
         "excel_profile", "image_info", "pdf_form_fields"), "read"),
    **dict.fromkeys(
        ("write_file", "write_document", "convert_document", "image_convert",
         "image_resize", "pdf_split", "redact_pii"), "make"),
    **dict.fromkeys(
        ("edit_file", "docx_edit", "excel_edit", "excel_apply_spec", "rename_file",
         "pdf_arrange", "pdf_form_fill"), "change"),
    **dict.fromkeys(("list_files", "list_folder"), "folder"),
    "grep": "search",
    "web_search": "web",
    "web_fetch": "page",
    **dict.fromkeys(("shell", "run_code", "repl"), "command"),
    "rest_call": "service",
}

#: lib/stepWords.ts STEP_WORDS[kind].count(1) + lib/runWords.ts KIND_NOUN.
_KIND_WORDS: dict[str, tuple[str, str]] = {
    "read": ("read 1 file", "file read"),
    "make": ("made 1 file", "file save"),
    "change": ("changed 1 file", "file change"),
    "folder": ("looked in 1 folder", "folder listing"),
    "search": ("searched files", "file search"),
    "web": ("searched the web", "web search"),
    "page": ("read 1 web page", "web page read"),
    "command": ("ran 1 command", "command"),
    "browser": ("used your browser", "browser step"),
    "service": ("called a web service", "web service call"),
}

_APP_NAME_MAX = 40
_APP_TOOL_MAX = 60
_APP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _cut_words(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _app_tool_parts(tool: str) -> tuple[str, str] | None:
    """``mcp__<pack>__<tool>`` split at the FIRST ``__`` after the prefix
    (lib/toolWords appToolParts), else None."""
    if not tool.startswith("mcp__"):
        return None
    rest = tool[len("mcp__"):]
    cut = rest.find("__")
    if cut <= 0:
        return None
    app, name = rest[:cut], rest[cut + 2:]
    if not _APP_ID.match(app) or not name.strip():
        return None
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return None
    return app, name


def app_name_words(app: str) -> str:
    """lib/toolWords appNameWords: the pack's id with spaces, one short line."""
    words = " ".join(app.replace("_", " ").split())
    return _cut_words(words or app, _APP_NAME_MAX)


def app_tool_name_words(name: str) -> str:
    """lib/toolWords appToolNameWords: "search_messages" → "search messages",
    "listOpenIssues" → "list open issues", an acronym keeps its case."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    words = [w for w in re.split(r"[\s_-]+", spaced) if w]
    words = [w.lower() if re.fullmatch(r"[A-Z][a-z]+", w) else w for w in words]
    return _cut_words(" ".join(words) or name, _APP_TOOL_MAX)


def tool_own_name(tool: str) -> str:
    """lib/runWords toolOwnName: a tool said by its own name — a connected
    app's tool as "<app>: <its tool in words>", anything else with spaces."""
    tid = str(tool or "").strip()
    if not tid:
        return "a tool"
    parts = _app_tool_parts(tid)
    if parts:
        return f"{app_name_words(parts[0])}: {app_tool_name_words(parts[1])}"
    loose = re.match(r"^mcp__(.+?)__(.+)$", tid)
    if loose:
        server = " ".join(loose.group(1).replace("_", " ").split())
        rest = " ".join(loose.group(2).replace("_", " ").split())
        return f"{server}: {rest}" if server and rest else tid
    return " ".join(tid.replace("_", " ").split()) or tid


def _step_kind(tool: str) -> str | None:
    if tool in _STEP_KIND:
        return _STEP_KIND[tool]
    if tool in _BROWSER_TOOLS:
        return "browser"
    return None


def fallback_line(tool: str, ok: bool) -> str:
    """ONE call of a tool ``_TOOL_WORDS`` does not word, in the dashboard's
    words (lib/runWords ``toolStepLine``): "searched your chats", "read 1 web
    page", "used gmail: search messages", "used rename real file"; a failed
    one "tried a chat search and it failed" / "tried gmail: … and it
    failed". Pure."""
    tid = str(tool or "").strip()
    words = _RUN_TOOL_WORDS.get(tid)
    if words is None:
        kind = _step_kind(tid)
        words = _KIND_WORDS.get(kind) if kind else None
    if words is not None:
        if ok:
            return words[0]
        noun = words[1]
        article = "an" if noun[:1].lower() in "aeiou" else "a"
        return f"tried {article} {noun} and it failed"
    own = tool_own_name(tid)
    return f"used {own}" if ok else f"tried {own} and it failed"


def _service_name(raw: Any) -> str:
    """The saved service a ``rest_call`` named, by its name only (the tool
    takes "<name> (<id>)" too — lib/toolWords serviceNameWords)."""
    flat = " ".join(str(raw or "").split())
    named = re.match(r"^(.+?)\s*\([^()]*\)$", flat)
    return _short((named.group(1) if named else flat).strip(), 60)


def tool_line(tool: str, args: dict[str, Any], ok: bool) -> str:
    """One plain-words line for a tool call. Never quotes a whole argument
    blob — a basename, a short query, a service's name, or nothing.

    v1.358.0 (G19): a tool the table above does not word reads in the
    dashboard's own words (:func:`fallback_line`), never its id."""
    words = _TOOL_WORDS.get(tool)
    if words is None:
        return fallback_line(tool, ok)
    x = _base(args.get("path") or args.get("file") or args.get("source") or args.get("dst") or "")
    q = _short(args.get("query") or args.get("pattern") or args.get("q") or "", 60)
    a = _short(args.get("agent") or "a teammate", 40)
    sv = _service_name(args.get("service"))
    text = words[0] if ok else words[1]
    if "{x}" in text and not x:
        text = text.replace(" {x}", " a file").replace("{x}", "a file")
    if "{q}" in text and not q:
        text = text.replace(" for “{q}”", "")
    if "{s}" in text and not sv:
        text = text.replace("{s}", "a web service")
    return text.format(x=x, q=q, a=a, s=sv)


def session_options(session: Any) -> dict[str, Any]:
    """The decoded run options of a session row — ``{}`` for a legacy row or
    junk in the column. Never raises."""
    raw = getattr(session, "options_json", None)
    if not raw:
        return {}
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def display_objective(session: Any) -> str:
    """What the user ASKED for, for display (v1.309.0, contract 5/6).

    A continuation's ``Session.task`` is the model-facing recap ("<message>
    [Continuing an earlier session. Original task: …]"); shown as the
    mission's objective it filled the Recent list with recap-wrapped text and
    listed every follow-up as a new, oddly-worded mission. The continue door
    stores the user's own words in ``options["objective"]`` (Track A writes
    it in ``continue_session``; the route also stamps it), and that wins;
    every other mission's objective IS its task."""
    objective = session_options(session).get("objective")
    if isinstance(objective, str) and objective.strip():
        return objective.strip()
    return str(getattr(session, "task", "") or "")


def continued_as(db, session_id: str, since: Any = None) -> str | None:
    """The id of the NEWEST session continuing ``session_id``, or None
    (v1.309.0, interrupted-mission-no-continue). No column links a
    continuation to its parent, so the continue and retry-failed doors stamp
    ``options["continued_from"]`` on the new row (always overwritten — run
    options carry over through ``_stored_options``). The LIKE only narrows the
    candidates; each one is decoded and checked, so a formatting difference
    or an id that is a prefix of another can never produce a wrong link.

    BOUNDED (v1.309.0 review): the view runs this every 2 s, and a bare LIKE
    is an unindexed scan of the WHOLE session table per tick — a cost the
    statement-count pin cannot see. Two facts true of every continuation
    narrow it first: it is a mission (only a mission's continuation is
    stamped) and it was created no earlier than the mission it continues
    (``since`` = that row's ``created_at``)."""
    if not session_id:
        return None
    stmt = select(Session.id, Session.options_json).where(
        Session.origin == MISSION_ORIGIN,
        Session.options_json.like(f"%{session_id}%"),  # type: ignore[union-attr]
    )
    if since is not None:
        stmt = stmt.where(Session.created_at >= since)  # type: ignore[operator]
    rows = db.exec(
        stmt
        .order_by(
            Session.created_at.desc(),  # type: ignore[attr-defined]
            literal_column("session.rowid").desc(),
        )
        .limit(_CONTINUATION_ROWS)
    )
    for sid, raw in rows:
        try:
            opts = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(opts, dict) and opts.get("continued_from") == session_id and sid != session_id:
            return str(sid)
    return None


def _walk(db, root_id: str) -> tuple[list[Session], dict[str, list[AgentRun]]]:
    """``(member sessions in discovery order, runs by session id)`` — the
    delegation tree under ``root_id`` (root included in ``runs``).

    v1.309.0: the member rows of one depth are read in ONE statement (it was
    a ``db.get`` per member), so the walk costs a fixed number of statements
    per DEPTH — capped at ``_MAX_DEPTH`` — however large the team."""
    runs_by: dict[str, list[AgentRun]] = {}
    members: list[Session] = []
    seen = {root_id}
    frontier = list(db.exec(select(AgentRun).where(AgentRun.session_id == root_id)))
    runs_by[root_id] = list(frontier)
    for _depth in range(_MAX_DEPTH):
        parent_ids = [r.id for r in frontier]
        if not parent_ids:
            break
        linked = list(
            db.exec(
                select(AgentRun)
                .where(AgentRun.parent_id.in_(parent_ids))  # type: ignore[attr-defined, union-attr]
                .order_by(AgentRun.created_at)  # type: ignore[arg-type]
            )
        )
        candidates: list[str] = []
        for r in linked:
            sid = r.session_id
            if not sid or sid in seen:
                continue
            seen.add(sid)
            candidates.append(sid)
        rows: dict[str, Session] = {}
        if candidates:
            rows = {
                row.id: Session(**row.model_dump())
                for row in db.exec(
                    select(Session).where(Session.id.in_(candidates))  # type: ignore[attr-defined]
                )
            }
        next_ids: list[str] = []
        for sid in candidates:
            row = rows.get(sid)
            if row is None:
                continue
            members.append(row)
            next_ids.append(sid)
            if len(members) >= _MAX_MEMBERS:
                break
        frontier = []
        if next_ids:
            frontier = list(
                db.exec(
                    select(AgentRun).where(AgentRun.session_id.in_(next_ids))  # type: ignore[attr-defined]
                )
            )
            for r in frontier:
                runs_by.setdefault(r.session_id, []).append(r)
        if len(members) >= _MAX_MEMBERS:
            break
    return members, runs_by


def _status(session: Session, runs: list[AgentRun], waiting: bool) -> str:
    """``waiting_you`` | ``queued`` | ``working`` | ``done`` | ``failed`` |
    ``cancelled`` — the card's one word, from the row and its runs."""
    st = session.status
    if st is SessionStatus.COMPLETED:
        return "done"
    if st is SessionStatus.FAILED:
        return "failed"
    if st is SessionStatus.CANCELLED:
        return "cancelled"
    if waiting or any(r.state is AgentState.WAITING for r in runs):
        return "waiting_you"
    if st is SessionStatus.QUEUED or not runs:
        return "queued"
    return "working"


def _plan_counts(events: list[EventRecord], session_id: str) -> tuple[int, int, str]:
    """``(steps planned, steps finished, current step goal)`` from the
    decomposed lane's persisted ``plan.*`` events for one session."""
    total = finished = 0
    current = ""
    for ev in events:
        if ev.session_id != session_id:
            continue
        data = _payload(ev)
        if ev.type == EventType.PLAN_CREATED:
            steps = data.get("steps")
            total = len(steps) if isinstance(steps, list) else total
            finished, current = 0, ""
        elif ev.type == EventType.PLAN_STEP_STARTED:
            current = _short(data.get("goal") or "", 90)
        elif ev.type == EventType.PLAN_STEP_COMPLETED:
            finished += 1
    return total, min(finished, total) if total else finished, current


def progress_for(status: str, steps: int, plan: tuple[int, int, str]) -> dict[str, Any]:
    """The card's bar. ``pct`` is None unless something COUNTS it."""
    total, finished, current = plan
    if status == "done":
        return {"pct": 100, "label": "Done", "basis": "done"}
    if status == "queued":
        return {"pct": 0, "label": "Waiting to start", "basis": "queued"}
    if status in ("failed", "cancelled"):
        return {"pct": None, "label": "Failed" if status == "failed" else "Stopped", "basis": status}
    if total > 0:
        pct = int(finished * 100 / total)
        label = f"Step {min(finished + 1, total)} of {total}"
        if current:
            label += f": {current}"
        return {"pct": pct, "label": label, "basis": "plan"}
    if status == "waiting_you":
        return {"pct": None, "label": "Waiting for your OK", "basis": "waiting"}
    label = f"Working · step {steps}" if steps else "Working"
    return {"pct": None, "label": label, "basis": "steps"}


def _team_documents(db, sessions: dict[str, Session]) -> dict[str, list[str]]:
    """``{session id: documents}`` for the whole team in ONE statement
    (v1.309.0) — the exact ``outcome.session_result`` ``documents`` list for
    every session in ``sessions``, without reading a single tool output.

    ``session_result`` walks every invocation of a session and looks each one
    up in the undo journal; only the rows that HAVE a file journal can name a
    document, so this joins the two and selects just those rows' id, session,
    journal kind and envelope. Same rules, same order: undo rows skipped;
    ``file_delete`` = created, ``file_restore`` = changed, ``files_delete``
    expands its envelope into created; a path both created and changed is a
    creation; workspace-relative de-duplication, then resolved under the
    session's workspace (no workspace = no documents), capped at the same
    list cap. Ties on ``created_at`` break on rowid, which is the order
    ``session_result``'s single-session read returns them in.
    ``test_team_documents_match_the_ledger_after_the_rewrite`` is the golden
    pin that keeps this and ``session_result`` agreeing.
    """
    from .outcome import (
        _CHANGED_KIND,
        _CREATED_KIND,
        _CREATED_MANY_KIND,
        _LIST_CAP,
        _envelope_path,
        _envelope_paths,
        _rel,
    )

    out: dict[str, list[str]] = {sid: [] for sid in sessions}
    if not sessions:
        return out
    rows = db.exec(
        select(ToolInvocation.session_id, UndoJournal.kind, UndoJournal.pre_inline)
        .join(UndoJournal, UndoJournal.action_id == ToolInvocation.id)  # type: ignore[arg-type]
        .where(
            ToolInvocation.session_id.in_(list(sessions)),  # type: ignore[attr-defined]
            ToolInvocation.undo_of.is_(None),  # type: ignore[union-attr]
            UndoJournal.kind.in_((_CREATED_KIND, _CHANGED_KIND, _CREATED_MANY_KIND)),  # type: ignore[attr-defined]
        )
        .order_by(
            ToolInvocation.created_at,  # type: ignore[arg-type]
            literal_column("toolinvocation.rowid"),
        )
    )
    created: dict[str, list[str]] = {sid: [] for sid in sessions}
    changed: dict[str, list[str]] = {sid: [] for sid in sessions}
    for sid, kind, pre_inline in rows:
        workspace = (sessions[sid].workspace_path or "") if sid in sessions else ""
        journal = UndoJournal(action_id="", kind=kind or "", pre_inline=pre_inline)
        made, edited = created.setdefault(sid, []), changed.setdefault(sid, [])
        if kind == _CREATED_MANY_KIND:
            for p in _envelope_paths(journal):
                rp = _rel(p, workspace)
                if rp and rp not in made:
                    made.append(rp)
            continue
        path = _rel(_envelope_path(journal), workspace)
        if not path:
            continue
        if kind == _CREATED_KIND and path not in made:
            made.append(path)
        elif kind == _CHANGED_KIND and path not in edited:
            edited.append(path)
    for sid, row in sessions.items():
        workspace = row.workspace_path or ""
        if not workspace:
            continue
        made = created.get(sid, [])
        edited = [p for p in changed.get(sid, []) if p not in made]
        try:
            out[sid] = [str((Path(workspace) / p).resolve()) for p in (made + edited)[:_LIST_CAP]]
        except (OSError, ValueError):  # a path the OS refuses costs that card its files
            out[sid] = []
    return out


def _read_route_events(db, session_ids: list[str]) -> list[tuple[str, str | None, str]]:
    """The newest ``_ROUTE_ROWS`` failover/downgrade events of these sessions,
    as ``(type, session_id, payload_json)``, newest first — what
    :func:`_route_note` reads. One bounded statement."""
    return [
        (str(t), s, str(p or "{}"))
        for t, s, p in db.exec(
            select(EventRecord.type, EventRecord.session_id, EventRecord.payload_json)
            .where(
                EventRecord.session_id.in_(session_ids),  # type: ignore[attr-defined, union-attr]
                EventRecord.type.in_(_ROUTE_EVENT_TYPES),  # type: ignore[attr-defined]
            )
            .order_by(EventRecord.created_at.desc())  # type: ignore[attr-defined]
            .limit(_ROUTE_ROWS)
        )
    ]


def route_note_for(db, session_id: str, name: str) -> str:
    """:func:`_route_note` for ONE run (v1.351.0, G18): the receipt of a
    delegated or scheduled run says when its model failed over, in the same
    words the mission screen uses. ``""`` when nothing moved. BLOCKING."""
    names = {session_id: name or "The agent"}
    return _route_note(_read_route_events(db, [session_id]), names, scope="run")


def _route_note(
    route_events: list[tuple[str, str | None, str]],
    names: dict[str, str],
    scope: str = "mission",
) -> str:
    """ONE plain sentence when a failover or a downgrade touched this mission,
    else ``""`` (v1.309.0, mission-no-route-receipt). ``route_events`` are
    ``(type, session_id, payload_json)``, newest first. The newest change is
    named; more than one is counted. Provider names and the router's own
    DERIVED reason (``failure_reason``: "unreachable", "http 500", …) only —
    never an error blob. ``scope`` is the word the count sentence uses
    ("mission", or "run" for one session's receipt — v1.351.0)."""
    notes: list[str] = []
    for etype, sid, raw in route_events:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        who = names.get(sid or "", "A teammate")
        why = _short(data.get("reason") or "", 80)
        if etype == EventType.PROVIDER_FAILOVER:
            src = _short(data.get("from") or "its model", 40)
            dst = _short(data.get("to") or "another model", 40)
            text = f"{who}'s model {src} failed" + (f" ({why})" if why else "")
            notes.append(f"{text}; {dst} answered instead.")
        elif etype == EventType.PROVIDER_DOWNGRADED:
            used = str(data.get("used") or "")
            if used == "mock":
                # v1.329.0 (calm chat K4): two plain sentences, the words the
                # mission screen's own line uses. v1.357.0 (G76): the demo's
                # one name (dashboard lib/demoWords DEMO_RUNNING).
                notes.append(f"{who} ran on the demo model. No AI model ran.")
            else:
                # A refusal (``used: "none"``) carries ``label`` since
                # v1.329.0 (calm chat J1): the name the user gave a fleet
                # endpoint. It wins over ``requested`` (the raw
                # ``fleet-<id>``, kept for the logs); an older event without
                # it still names the requested provider.
                wanted = _short(
                    data.get("label") or data.get("requested") or "the chosen model", 40
                )
                notes.append(f"{who} could not use {wanted}" + (f" ({why})." if why else "."))
    if not notes:
        return ""
    note = notes[0]
    if len(notes) > 1:
        note = f"{note} There were {len(notes)} route changes in this {scope}."
    return " ".join(note.split())


def _read_tool_rows(
    db, session_ids: list[str], limit: int = _SCAN_ROWS
) -> tuple[list[Any], dict[str, str]]:
    """The newest ``limit`` tool calls of these sessions (oldest first, as
    explicit-column tuples) and WHO REFUSED each refused call
    (``invocation_id`` → the ``tool.denied`` event's ``kind``). Two bounded
    statements — lifted out of :func:`mission_view` (v1.351.0, G18) so one
    run's receipt reads its actions the way the mission screen does."""
    # EXPLICIT COLUMNS (v1.309.0): the log needs who/what/ok/when and the
    # redacted args — never ``output``, which is up to 4,000 chars a row
    # and was read 240 rows at a time, every 2 s, to be thrown away.
    invocations = list(
        db.exec(
            select(
                ToolInvocation.id,
                ToolInvocation.session_id,
                ToolInvocation.tool,
                ToolInvocation.args_json,
                ToolInvocation.ok,
                ToolInvocation.verdict,
                ToolInvocation.undo_of,
                ToolInvocation.created_at,
            )
            .where(ToolInvocation.session_id.in_(session_ids))  # type: ignore[attr-defined]
            .order_by(
                ToolInvocation.created_at.desc(),  # type: ignore[attr-defined]
                literal_column("toolinvocation.rowid").desc(),
            )
            .limit(limit)
        )
    )
    invocations.reverse()
    # WHO REFUSED each refused call (v1.309.0 review): the registry's
    # ``tool.denied`` event names it (``kind``) and points at its ledger
    # row (``invocation_id``). One bounded statement for the whole team,
    # explicit column — a refusal is never more frequent than the calls
    # it refuses, so the same window covers the invocations above.
    denied_kind: dict[str, str] = {}
    for raw in db.exec(
        select(EventRecord.payload_json)
        .where(
            EventRecord.session_id.in_(session_ids),  # type: ignore[attr-defined, union-attr]
            EventRecord.type == EventType.TOOL_DENIED,
        )
        .order_by(EventRecord.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ):
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("invocation_id"):
            denied_kind[str(data["invocation_id"])] = str(data.get("kind") or "")
    return invocations, denied_kind


def tool_activity(
    invocations: list[Any], denied_kind: dict[str, str], names: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The plain-words log lines for tool calls read by :func:`_read_tool_rows`
    — ``(lines oldest first, last line per session)``. Pure. Bookkeeping
    tools (``_QUIET_TOOLS``) and undo rows are skipped; a refused call is
    worded by WHO refused it (never "could not" for an ask the user did not
    answer). Each line is ``{at, who, session_id, text, tone}``."""
    last_line: dict[str, str] = {}
    lines: list[dict[str, Any]] = []
    for inv_id, inv_session, inv_tool, inv_args, inv_ok, inv_verdict, inv_undo, inv_at in invocations:
        tool = inv_tool or ""
        if inv_undo or tool in _QUIET_TOOLS:
            continue
        who = names.get(inv_session, "Agent")
        verdict = str(getattr(inv_verdict, "value", inv_verdict) or "").lower()
        kind = denied_kind.get(str(inv_id or "")) if verdict == "ask" and not inv_ok else None
        if kind in _ASK_DENIAL_KINDS:
            # A DENIED ASK IS NOT A FAILURE (v1.309.0, contract 5): the call
            # needed the user's OK and did not get it (nobody could ask, the
            # user said no, or the pause timed out). "Builder could not write
            # report.md" read as the agent failing at the work; it was
            # waiting on a person.
            text, tone = f"needed your OK to {ask_words(tool)}", "ask"
        elif kind is not None:
            # …and a refusal NOBODY was asked about is never worded as one
            # (v1.309.0 review, honest copy): the breaker, the roster gate
            # and low trust share the ``ask`` verdict but asked no one.
            words = _REFUSAL_WORDS.get(kind, "was not allowed to use {t}")
            # v1.358.0 (G19): a connected app's tool by its app and words.
            text, tone = words.format(t=tool_own_name(tool)), "warn"
        else:
            # No ``tool.denied`` event = not a refusal: an ``ask`` call the
            # user APPROVED whose tool then failed reads as the failure it is
            # (as does a refusal whose event was pruned — never "needed your
            # OK" on a guess).
            try:
                args = json.loads(inv_args or "{}")
            except (TypeError, ValueError):
                args = {}
            text = tool_line(tool, args if isinstance(args, dict) else {}, bool(inv_ok))
            tone = "ok" if inv_ok else "warn"
        last_line[inv_session] = text
        lines.append({
            "at": _iso(inv_at), "who": who, "session_id": inv_session,
            "text": f"{who} {text}", "tone": tone,
        })
    return lines, last_line


def activity_lines(
    db, session_ids: list[str], names: dict[str, str], newest: int | None = None
) -> list[dict[str, Any]]:
    """The plain-words action log of these sessions' tool calls (v1.351.0,
    G18): the mission screen's words for one run's receipt. Oldest first;
    ``newest`` keeps only that many of the most recent lines. BLOCKING."""
    invocations, denied_kind = _read_tool_rows(db, list(session_ids))
    lines, _last = tool_activity(invocations, denied_kind, names)
    if newest is not None:
        lines = lines[-newest:] if newest > 0 else []
    return lines


def _handoff_key(data: dict[str, Any]) -> str:
    """A per-handoff id when the delegation event carries one (Track A may add
    ``handoff_id``; a tool call id is the same idea), else ``""``."""
    for key in ("handoff_id", "call_id", "tool_call_id"):
        value = data.get(key)
        if value:
            return str(value)
    return ""


def mission_view(platform: Any, session_id: str) -> dict[str, Any] | None:
    """The whole mission under coordinator ``session_id``, or None when the
    session does not exist. BLOCKING."""
    engine = platform.engine
    approvals = getattr(platform, "approvals", None)

    def _waiting(sid: str) -> dict | None:
        pending = getattr(approvals, "pending_for", None)
        if pending is None:
            return None
        try:
            rows = list(pending(sid) or [])
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        first = rows[0]
        out = {"approval_id": first.get("approval_id") or first.get("id"), "tool": first.get("tool")}
        # v1.352.0 (G25): can the action be undone — the verdict every ask
        # card shows (``tools/reversible``), so the mission's card says it too.
        # Left out for a read-only tool / an unknown name. In-memory read.
        try:
            from ..tools.reversible import reversible_flag

            registry = getattr(platform, "registry", None)
            rev = reversible_flag(registry.get(str(first.get("tool") or ""))) if registry is not None else None
        except Exception:  # noqa: BLE001 — wording only
            rev = None
        if rev is not None:
            out["reversible"] = rev
        # v1.356.0 (G79): a priced ask's cost sentence — the words the
        # mission's own card shows before Allow (never the arguments).
        cost = first.get("cost_note")
        if isinstance(cost, str) and cost.strip():
            out["cost_note"] = cost
        return out

    with session_scope(engine) as db:
        root = db.get(Session, session_id)
        if root is None:
            return None
        root = Session(**root.model_dump())
        members, runs_by = _walk(db, session_id)
        team_ids = [session_id, *[m.id for m in members]]
        events = list(
            db.exec(
                select(EventRecord)
                .where(
                    EventRecord.session_id.in_(team_ids),  # type: ignore[attr-defined, union-attr]
                    EventRecord.type.in_(_EVENT_TYPES),  # type: ignore[attr-defined]
                )
                # Ties on created_at break on rowid (insertion order): a
                # remote's started + completed events land in one 15.6 ms
                # Windows tick, and read completion-first they made TWO
                # cards (v1.317.0; the v1.286.0 same-tick rule).
                .order_by(
                    EventRecord.created_at.desc(),  # type: ignore[attr-defined]
                    literal_column("eventrecord.rowid").desc(),
                )
                .limit(_SCAN_ROWS)
            )
        )
        events.reverse()
        # The team's tool calls + who refused each refused one, and the route
        # changes — the same bounded reads the receipt of one run uses
        # (v1.351.0: lifted into ``_read_tool_rows`` / ``_read_route_events``),
        # still inside this ONE transaction.
        invocations, denied_kind = _read_tool_rows(db, team_ids)
        route_events = _read_route_events(db, team_ids)
        try:
            team_docs = _team_documents(db, {root.id: root, **{m.id: m for m in members}})
        except Exception:  # noqa: BLE001 — files are a bonus on a card
            team_docs = {}
        try:
            next_id = continued_as(db, session_id, root.created_at)
        except Exception:  # noqa: BLE001 — a missing link is "not continued"
            next_id = None

    from .roster import session_roster_name

    names: dict[str, str] = {session_id: COORDINATOR}
    roster_names: dict[str, str] = {}
    for m in members:
        rn = session_roster_name(m) or "builder"
        roster_names[m.id] = rn
        names[m.id] = display_name(rn)

    # The task each member was HANDED (the delegation event), else its row.
    # REMOTE teammates open no session; they are known only from their
    # delegation events, and since v1.309.0 (remote-teammates-invisible) each
    # HANDOFF is its own card: two jobs given to one remote are two cards,
    # each with its own task and outcome. A per-handoff key is used when the
    # event carries one; otherwise a completion settles the OLDEST open
    # handoff to that agent (the events are read oldest first).
    handed: dict[str, str] = {}
    remote_members: list[dict[str, Any]] = []
    open_remote: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev.type not in (EventType.DELEGATION_STARTED, EventType.DELEGATION_COMPLETED):
            continue
        data = _payload(ev)
        child = str(data.get("child_session_id") or "")
        agent = str(data.get("agent") or "")
        if ev.type == EventType.DELEGATION_STARTED and child:
            handed.setdefault(child, str(data.get("task") or ""))
        if not (agent.lower().startswith("remote:") and not child):
            continue
        key = _handoff_key(data)
        queue = open_remote.setdefault(key or agent, [])
        if ev.type == EventType.DELEGATION_STARTED:
            slot = {
                "agent": agent, "task": str(data.get("task") or ""), "status": "working",
                "started_at": _iso(ev.created_at), "finished_at": None,
            }
            remote_members.append(slot)
            queue.append(slot)
            continue
        if queue:
            slot = queue.pop(0)
        else:  # a completion whose start fell out of the scan window
            slot = {"agent": agent, "task": "", "started_at": None}
            remote_members.append(slot)
        slot["status"] = "done" if data.get("ok") else "failed"
        slot["result"] = _short(data.get("result") or "", _RESULT_CHARS)
        slot["finished_at"] = _iso(ev.created_at)

    lines, last_line = tool_activity(invocations, denied_kind, names)
    for ev in events:
        data = _payload(ev)
        who = names.get(ev.session_id or "", "Agent")
        text = ""
        tone = "info"
        if ev.type == EventType.DELEGATION_STARTED:
            agent = display_name(str(data.get("agent") or ""))
            # "gave X a task: …" reads right whatever the task's first word is
            # ("asked X to Industry research…" did not).
            text = f"{who} gave {agent} a task: {_short(data.get('task') or 'help', 120)}"
        elif ev.type == EventType.DELEGATION_COMPLETED:
            agent = display_name(str(data.get("agent") or ""))
            if data.get("ok"):
                text, tone = f"{agent} finished and reported back to {who}", "ok"
            else:
                text, tone = f"{agent} could not finish: {_short(data.get('result') or 'no reason given', 140)}", "warn"
        elif ev.type == EventType.PLAN_CREATED:
            steps = data.get("steps")
            n = len(steps) if isinstance(steps, list) else 0
            text = f"{who} broke the work into {n} step{'s' if n != 1 else ''}"
        elif ev.type == EventType.PLAN_STEP_STARTED:
            text = f"{who} is on step {int(data.get('index') or 0) + 1}: {_short(data.get('goal') or '', 100)}"
        elif ev.type == EventType.APPROVAL_REQUESTED:
            tool = str(data.get("tool") or "a tool")
            from .runtime import PLAN_APPROVAL_TOOL  # lazy: the runtime is heavy

            if tool == PLAN_APPROVAL_TOOL:
                # v1.350.0: the plan approval is not a tool to "use".
                text, tone = f"{who} is waiting for you to approve the plan", "ask"
            else:
                text, tone = f"{who} is waiting for your OK to {ask_words(tool)}", "ask"
        elif ev.type == EventType.APPROVAL_RESOLVED:
            decision = str(data.get("decision") or "")
            if decision == "deny":
                text, tone = f"You declined {who}'s request", "warn"
            elif decision == "timeout":
                text, tone = f"{who}'s request went unanswered", "warn"
            else:
                text = f"You approved {who}'s request"
        elif ev.type == EventType.SESSION_COMPLETED and ev.session_id == session_id:
            status = str(data.get("status") or "")
            if status == "completed":
                text, tone = f"{COORDINATOR} finished the objective", "ok"
            elif status == "cancelled":
                text, tone = "The objective was stopped", "warn"
            else:
                text, tone = f"{COORDINATOR} could not finish the objective", "warn"
        if text:
            lines.append({
                "at": _iso(ev.created_at), "who": who, "session_id": ev.session_id,
                "text": text, "tone": tone,
            })
    lines.sort(key=lambda r: r.get("at") or "")
    lines = lines[-_ACTIVITY_MAX:]

    out_members: list[dict[str, Any]] = []
    documents: list[str] = []
    for m in members:
        runs = runs_by.get(m.id, [])
        waiting = _waiting(m.id)
        status = _status(m, runs, waiting is not None)
        steps = sum(int(r.steps or 0) for r in runs)
        plan = _plan_counts(events, m.id)
        files = list(team_docs.get(m.id) or [])
        for f in files:
            if f not in documents:
                documents.append(f)
        rn = roster_names.get(m.id, "builder")
        out_members.append({
            "session_id": m.id,
            "agent": rn,
            "name": names.get(m.id, "Agent"),
            "kind": member_kind(rn),
            "task": _short(handed.get(m.id) or m.task, _TASK_CHARS),
            "status": status,
            "progress": progress_for(status, steps, plan),
            "activity": last_line.get(m.id, ""),
            "waiting_on": waiting,
            "steps": steps,
            "result": _short(m.summary, _RESULT_CHARS) if status in ("done", "failed", "cancelled") else "",
            "files": files[:20],
            # WHICH MODEL DID THIS PART (v1.309.0, mission-no-model-disclosure):
            # the member's own row, as the coordinator's block already says.
            "provider": m.provider,
            "model": m.model,
            "started_at": _iso(m.created_at),
            "finished_at": _iso(m.finished_at),
        })
    for rec in remote_members:
        st = rec["status"]
        out_members.append({
            "session_id": None,
            "agent": rec["agent"],
            "name": display_name(rec["agent"]),
            "kind": "remote",
            "task": _short(rec.get("task") or "", _TASK_CHARS),
            "status": st,
            "progress": progress_for(st, 0, (0, 0, "")),
            "activity": "",
            "waiting_on": None,
            "steps": 0,
            "result": rec.get("result", ""),
            "files": [],
            # A remote teammate runs on ITS machine's model — this app cannot
            # name it, so it says nothing rather than guess.
            "provider": None,
            "model": None,
            "started_at": rec.get("started_at"),
            "finished_at": rec.get("finished_at"),
        })

    root_runs = runs_by.get(session_id, [])
    root_waiting = _waiting(session_id)
    root_status = _status(root, root_runs, root_waiting is not None)
    for f in team_docs.get(session_id) or []:
        if str(f) not in documents:
            documents.insert(0, str(f))

    worklist = None
    store = getattr(platform, "worklist", None)
    if store is not None:
        try:
            summary = store.summary(store.root_session_for(session_id))
            if int(summary.get("total") or 0) > 0:
                worklist = summary
        except Exception:  # noqa: BLE001
            worklist = None

    finished = sum(1 for m in out_members if m["status"] == "done")
    return {
        "found": True,
        "session": {
            "id": root.id,
            "task": root.task,
            # The user's own words (v1.309.0): a continuation's ``task`` is
            # the model-facing recap, which stays untouched above.
            "objective": display_objective(root),
            "status": root.status.value,
            "outcome": getattr(root, "outcome", None) or None,
            "agent_type": root.agent_type.value,
            "project_id": root.project_id,
            "provider": root.provider,
            "model": root.model,
            # A RESTART CUT IT OFF (v1.309.0): the boot reconcile's tag, so
            # the screen can offer Continue instead of a bare "failed".
            "interrupted": getattr(root, "interrupted_at", None) is not None,
            # …and once continued, where the work went on (the newest
            # continuation), so the old screen links forward, not back.
            "continued_as": next_id,
            "route_note": _route_note(route_events, names),
            "created_at": _iso(root.created_at),
            "finished_at": _iso(root.finished_at),
        },
        "coordinator": {
            "name": COORDINATOR,
            "status": root_status,
            "waiting_on": root_waiting,
            "steps": sum(int(r.steps or 0) for r in root_runs),
        },
        "members": out_members,
        "progress": {"done": finished, "total": len(out_members)},
        "activity": lines,
        "deliverable": {
            # The coordinator's own answer, once it HAS one. A running
            # session's summary is only the create-time folder note, if any.
            "text": (
                root.summary
                if root.status
                in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
                else ""
            ),
            "documents": documents[:30],
            "worklist": worklist,
        },
    }


def missions_waiting(platform: Any, db, root_ids: list[str]) -> set[str]:
    """The mission roots (of ``root_ids``) parked on an ask — their own, or
    any delegated member's (v1.309.0, mission-list-says-working-while-blocked,
    contract 6). The Recent list said "working" while the whole team sat on a
    question for the user.

    Bounded for a list: nothing at all is read when no ask is pending
    anywhere, and otherwise one statement per delegation DEPTH (``_MAX_DEPTH``)
    for every row together — never the full ``_walk`` per row. Asks are
    filed under the asking run's OWN session id (a member's under the
    child's), so each level's session ids are checked against the one
    in-memory approvals registry."""
    approvals = getattr(platform, "approvals", None)
    pending_for = getattr(approvals, "pending_for", None)
    if pending_for is None or not root_ids:
        return set()
    try:
        if int(approvals.pending_count()) == 0:
            return set()
    except Exception:  # noqa: BLE001 — no count = look anyway
        pass

    def _asks(sid: str) -> bool:
        try:
            return bool(pending_for(sid))
        except Exception:  # noqa: BLE001
            return False

    waiting = {rid for rid in root_ids if _asks(rid)}
    # session id -> its mission root, level by level down the run links.
    root_of: dict[str, str] = {rid: rid for rid in root_ids}
    frontier = list(
        db.exec(
            select(AgentRun.id, AgentRun.session_id).where(
                AgentRun.session_id.in_([r for r in root_ids if r not in waiting])  # type: ignore[attr-defined]
            )
        )
    )
    for _depth in range(_MAX_DEPTH):
        run_root = {run_id: root_of.get(sid, "") for run_id, sid in frontier}
        if not run_root:
            break
        children = list(
            db.exec(
                select(AgentRun.id, AgentRun.session_id, AgentRun.parent_id).where(
                    AgentRun.parent_id.in_(list(run_root))  # type: ignore[attr-defined, union-attr]
                )
            )
        )
        frontier = []
        for run_id, sid, parent in children:
            rid = run_root.get(parent or "", "")
            if not rid or rid in waiting or not sid:
                continue
            if sid not in root_of:  # a session with several runs is asked once
                root_of[sid] = rid
                if _asks(sid):
                    waiting.add(rid)
                    continue
            frontier.append((run_id, sid))
    return waiting
