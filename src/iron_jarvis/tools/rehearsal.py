"""Leap 06-a: a REHEARSAL — a run that reads for real and changes nothing.

A rule or a schedule can run once as a rehearsal. Its run carries
``options["rehearsal_id"]``; the lane copies it onto every call's
``ToolContext.rehearsal_id`` (:func:`rehearsal_of`, never from a model
argument). In ``registry.invoke``, AFTER the roster, the permission decision,
the shape check and the task's write rules (so those are the real ones) and
BEFORE the read cache and the Undo capture, a call this module :func:`holds`
does not execute: no intent and no Undo row are written, one
``ToolInvocation`` with ``rehearsed=True`` and ``would`` (run / ask / refuse)
records what the real run would have done, and the model is told "Rehearsal:
not done. … Carry on as if it worked."

What is NOT held (it runs for real):

* a tool that declares itself READONLY (an app's tool only when the app marks
  it read only: ``mcp/tools`` turns ``readOnlyHint`` into READONLY — D-06.2);
* :data:`REHEARSAL_READS`: tools that only read although they do not declare
  it (the lookup's own list, ``capability_search.index.READS_ONLY``: web pages
  and searches, memory reads — D-06.2 says a rehearsal may read the web);
* :data:`REHEARSAL_OWN_STATE`: a run's own working list;
* :data:`REHEARSAL_HELPERS`: a helper agent's hand-off. The helper's run
  inherits the rehearsal (``Orchestrator.create_session`` reads
  :func:`current`), so ITS writes are held in turn.

Everything else is held, an undeclared tool included: the fail-safe
direction, and so is :data:`REHEARSAL_HOLDS` (READONLY by declaration, yet
acting on the user's screen). ``tests/test_leap06a_gate.py`` keeps the
reviewed list of every tool a rehearsal lets run, and
``tests/test_leap06a_side_doors.py`` the reviewed list of every direct send
and process start in the automation packages: a new one fails until it is
reviewed. :data:`SIDE_DOORS` lists the effects that do not pass
``registry.invoke`` (a schedule's pre-run script, its delivery line, the chat
delivery, the post-run lessons, a rule's or a helper's hand-off to a remote
agent, an outgoing webhook); each records one rehearsed row through
:func:`record_side_door` and does nothing. :func:`event_is_rehearsal` tells
a reader of the bus (an outgoing webhook) that an event is a rehearsal's.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from .base import Reversibility

#: The run option a rehearsal rides on (``Session.options_json``).
REHEARSAL_OPTION = "rehearsal_id"

#: A run's own working list: it changes nothing outside the run.
REHEARSAL_OWN_STATE = frozenset({
    "worklist_add", "worklist_done", "worklist_next", "worklist_status",
})

#: A helper agent's hand-off runs; the helper inherits the rehearsal.
REHEARSAL_HELPERS = frozenset({"delegate", "spawn_agent"})

#: Tools that declare themselves READONLY yet act where the user can see
#: it, so a rehearsal holds them all the same. ``browser_screenshot`` brings
#: a tab of the user's own browser to the front before it takes its picture.
#: ``tests/test_leap06a_gate.py`` keeps the reviewed list of every tool a
#: rehearsal lets run; a tool found to act goes HERE.
REHEARSAL_HOLDS = frozenset({"browser_screenshot"})


def _reads_only() -> frozenset[str]:
    try:
        from ..capability_search.index import READS_ONLY

        return frozenset(READS_ONLY)
    except Exception:  # noqa: BLE001 — cannot read the list: hold more, never less
        return frozenset()


#: Reads that do not declare themselves READONLY (one list with the lookup's).
REHEARSAL_READS = _reads_only()

#: The rehearsal the CURRENT call belongs to — set by ``registry.invoke``
#: around a call it runs inside a rehearsal, read by ``create_session`` so a
#: helper agent's run inherits it. "" outside a rehearsal.
_CURRENT: ContextVar[str] = ContextVar("iron_jarvis_rehearsal", default="")


def current() -> str:
    """The rehearsal the running call belongs to ("" for a real call)."""
    return _CURRENT.get()


@contextmanager
def scope(rehearsal_id: str) -> Iterator[None]:
    """Run the block as part of ``rehearsal_id`` (no-op for "")."""
    if not rehearsal_id:
        yield
        return
    token = _CURRENT.set(rehearsal_id)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def rehearsal_of(session: Any) -> str:
    """The rehearsal a session's run belongs to, from its OWN options ("" for
    a real run). Never raises."""
    try:
        from ..scheduling.knobs import session_options

        value = session_options(session).get(REHEARSAL_OPTION)
    except Exception:  # noqa: BLE001
        return ""
    return str(value).strip() if isinstance(value, str) else ""


def event_is_rehearsal(event: Any, engine: Any = None) -> bool:
    """Does ``event`` belong to a REHEARSAL's run? Its type is
    ``tool.rehearsed``; its payload says so (``rehearsal: True``, the outcome
    ``rehearsed``); or, with an ``engine``, the session it names carries a
    rehearsal in its OWN options (``agent.completed`` and ``tool.executed``
    carry no mark of their own). BLOCKING with an engine (one indexed read):
    a sync event handler runs off the loop. Never raises."""
    try:
        if isinstance(event, dict):
            etype, payload, sid = event.get("type"), event.get("payload"), event.get("session_id")
        else:
            etype = getattr(event, "type", "")
            payload = getattr(event, "payload", None)
            sid = getattr(event, "session_id", None)
        if str(getattr(etype, "value", etype) or "") == "tool.rehearsed":
            return True
        payload = payload if isinstance(payload, dict) else {}
        if payload.get("rehearsal") is True or payload.get("outcome") == "rehearsed":
            return True
        sid = str(sid or payload.get("session_id") or "").strip()
        if engine is None or not sid:
            return False
        return session_is_rehearsal(engine, sid)
    except Exception:  # noqa: BLE001
        return False


def session_is_rehearsal(engine: Any, session_id: str) -> bool:
    """Does the session ``session_id`` carry a rehearsal in its OWN options?
    BLOCKING (one indexed read): call it off the loop. Never raises."""
    try:
        sid = str(session_id or "").strip()
        if engine is None or not sid:
            return False
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import Session

        with session_scope(engine) as db:
            raw = db.exec(select(Session.options_json).where(Session.id == sid)).first()
        opts = json.loads(raw) if isinstance(raw, str) and raw else {}
        return isinstance(opts, dict) and bool(str(opts.get(REHEARSAL_OPTION) or "").strip())
    except Exception:  # noqa: BLE001
        return False


def holds(tool: Any) -> bool:
    """Is a call to ``tool`` HELD in a rehearsal? True unless it only reads
    (READONLY, or named in :data:`REHEARSAL_READS`), is the run's own list
    (:data:`REHEARSAL_OWN_STATE`) or a helper's hand-off
    (:data:`REHEARSAL_HELPERS`). An undeclared tool is held, and so is one
    that says it only reads yet acts on the user's screen
    (:data:`REHEARSAL_HOLDS`)."""
    name = str(getattr(tool, "name", "") or "")
    if name in REHEARSAL_HOLDS:
        return True
    if name in REHEARSAL_READS or name in REHEARSAL_OWN_STATE or name in REHEARSAL_HELPERS:
        return False
    rev = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
    value = rev.value if isinstance(rev, Reversibility) else str(rev or "")
    return value != Reversibility.READONLY.value


# --------------------------------------------------------------------------- #
# What a held call would have done, in words
# --------------------------------------------------------------------------- #

#: The kinds of effect a report counts.
KINDS = ("write", "change", "move", "remove", "send", "run", "app", "page")


@dataclass(frozen=True)
class Effect:
    """What one held call would have done. ``kind`` is one of :data:`KINDS`;
    ``target`` the file, page or app it touches ("" when none); ``words`` the
    present-tense phrase ("rename a.pdf to b.pdf"); ``verb`` / ``noun`` how
    the report's sentence counts it ("rename" 14 "file")."""

    kind: str
    target: str
    words: str
    verb: str
    noun: str


_WRITES = frozenset({
    "write_file", "write_document", "convert_document", "image_convert", "image_resize",
    "pdf_split", "pdf_arrange", "redact_pii", "pdf_form_fill",
})
_CHANGES = frozenset({
    "edit_file", "multi_edit", "apply_patch", "apply_changes", "notebook_edit",
    "excel_edit", "excel_live_edit", "excel_reconcile", "excel_apply_spec", "docx_edit",
})
_SENDS = frozenset({
    "notify", "message_agent", "rest_call", "blackboard_post",
})
_COMMANDS = frozenset({
    "shell", "process_start", "process_write", "run_code", "code_run", "repl", "pane_send",
})
_PAGES_PREFIX = ("browser_", "page_")
_PAGES = frozenset({"web_action"})
_TARGET_KEYS = ("path", "output", "output_path", "dest", "destination", "file", "filename", "target")
_QUOTE_MAX = 80


def _one_line(value: Any, limit: int = _QUOTE_MAX) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _target(args: dict[str, Any]) -> str:
    for key in _TARGET_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return _one_line(value, 160)
    return ""


def _ask_words(name: str) -> str:
    """What a call to ``name`` sets out to do, in the approval card's own
    words (``agents/mission.ask_words``: "make a schedule", "use Notes ›
    update page"). Never raises."""
    try:
        from ..agents.mission import ask_words

        return str(ask_words(name) or "")
    except Exception:  # noqa: BLE001
        return ""


def _app_of(name: str) -> str:
    """The app of an app tool ``mcp__<app>__<tool>``."""
    parts = name.split("__")
    return parts[1] if len(parts) > 2 else ""


def effect_of(name: str, args: Any, tool: Any = None) -> Effect:
    """What a held call to ``name`` with ``args`` would have done. A tool may
    say it itself (``describe_effect(args) -> Effect``). Never raises, never
    quotes the CONTENT of a write (only its target)."""
    a = args if isinstance(args, dict) else {}
    describe = getattr(tool, "describe_effect", None)
    if callable(describe):
        try:
            own = describe(a)
            if isinstance(own, Effect):
                return own
        except Exception:  # noqa: BLE001 — the table below still answers
            pass
    door = SIDE_DOORS.get(name)
    if door is not None:
        words = _one_line(a.get("words") or door.words, 200)
        return Effect(door.kind, _one_line(a.get("target") or "", 160), words, door.verb, door.noun)
    target = _target(a)
    if name == "rename_file":
        src = _one_line(a.get("path"), 120)
        dst = _one_line(a.get("new_path"), 120)
        return Effect("move", src, f"rename {src} to {dst}", "rename", "file")
    if name == "download_manage":
        action = str(a.get("action") or "").lower()
        if action in ("delete", "remove", "trash"):
            return Effect("remove", target, f"remove {target or 'a downloaded file'}", "remove", "file")
        return Effect("move", target, f"move {target or 'a downloaded file'}", "move", "file")
    if name in _WRITES:
        return Effect("write", target, f"write {target or 'a file'}", "write", "file")
    if name in _CHANGES:
        return Effect("change", target, f"change {target or 'a file'}", "change", "file")
    if name in _SENDS:
        quoted = _one_line(a.get("message") or a.get("text") or a.get("task") or "")
        where = _one_line(a.get("channel") or a.get("to") or a.get("agent") or "", 60)
        words = "send a message" + (f" to {where}" if where else "") + (f": “{quoted}”" if quoted else "")
        return Effect("send", where, words, "send", "message")
    if name in _COMMANDS:
        cmd = _one_line(a.get("command") or a.get("code") or a.get("input") or "")
        words = f"run the command `{cmd}`" if cmd and name in ("shell", "process_start", "process_write") else (
            _ask_words(name) or "run some code"
        )
        return Effect("run", cmd, words, "run", "command")
    if name.startswith("mcp__"):
        return Effect("app", _app_of(name), _ask_words(name) or "use an app", "use", "app action")
    if name == "browser_screenshot":
        return Effect(
            "page", "", "bring a tab of your browser to the front and take a picture of it",
            "act on", "web page",
        )
    if name in _PAGES or name.startswith(_PAGES_PREFIX):
        url = _one_line(a.get("url") or "", 120)
        verb = name.split("_", 1)[-1].replace("_", " ")
        return Effect("page", url, f"{verb} on a web page" + (f" ({url})" if url else ""), "act on", "web page")
    if name.endswith(("_delete", "_remove", "_disconnect")) or name in ("tool_delete", "trash"):
        words = _ask_words(name) or f"use {name.replace('_', ' ')}"
        return Effect("remove", target, words, "remove", "setting")
    if name.endswith(("_create", "_add", "_update", "_set", "_change", "_switch", "_toggle", "_connect")):
        words = _ask_words(name) or f"use {name.replace('_', ' ')}"
        return Effect("change", target, words, "change", "setting")
    if name == "notebook":
        return Effect("write", "", _ask_words(name) or "update its notebook", "write", "note")
    if name in ("delegate_remote", "assign_work"):
        return Effect("send", target, _ask_words(name) or "hand work on", "hand on", "task")
    words = _ask_words(name) or f"use {name.replace('_', ' ')}"
    return Effect("run", target, words, "run", "other step")


def held_output(effect: Effect, would: str) -> str:
    """What the model reads for a held call."""
    asks = " (it would ask you first)" if would == "ask" else ""
    return f"Rehearsal: not done. Would {effect.words}{asks}. Carry on as if it worked."


_PLURAL = {"app action": "app actions", "web page": "web pages", "other step": "other steps"}


def plural(noun: str, n: int) -> str:
    if n == 1:
        return noun
    return _PLURAL.get(noun, noun + "s")


def sentence(effects: "list[Effect]") -> str:
    """"This would rename 14 files, write 1 file and send 1 message. Nothing
    was changed." — counted from ``effects`` (the ledger's rows), in the
    order each first appears. Never raises."""
    order: list[tuple[str, str]] = []
    counts: dict[tuple[str, str], int] = {}
    for e in effects:
        if not e.noun:
            continue  # listed, never counted (a "may", e.g. a lesson)
        key = (e.verb, e.noun)
        if key not in counts:
            order.append(key)
            counts[key] = 0
        counts[key] += 1
    if not order:
        return "This would change nothing. Nothing was changed."
    parts = [f"{verb} {counts[(verb, noun)]} {plural(noun, counts[(verb, noun)])}" for verb, noun in order]
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"This would {listed}. Nothing was changed."


# --------------------------------------------------------------------------- #
# The side doors: effects that do not pass registry.invoke
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SideDoor:
    """One effect outside the tool gate. ``where`` names the code that holds
    it (``tests/test_leap06a_side_doors.py`` reads the file and drives it)."""

    kind: str
    words: str
    verb: str
    noun: str
    where: str


SIDE_DOORS: dict[str, SideDoor] = {
    "pre_run_script": SideDoor(
        "run", "run the schedule's pre-run script", "run", "command", "platform.py"
    ),
    "schedule_delivery": SideDoor(
        "send", "send the result where the schedule reports", "send", "message", "tools/rehearsal_runs.py"
    ),
    "chat_delivery": SideDoor(
        "send", "post the result in the chat that asked", "send", "message", "agents/orchestrator.py"
    ),
    # Not counted in the sentence (``noun`` ""): a run keeps a lesson only
    # when it has one, so the report lists it as a "may".
    "post_run_learning": SideDoor(
        "write", "keep a lesson from this run, if it learned one", "", "", "agents/orchestrator.py"
    ),
    "remote_agent": SideDoor(
        "send", "hand the task to a remote agent", "send", "message", "reflex/router.py"
    ),
    # A helper's hand-off to a REMOTE agent (``delegate`` to ``remote:<name>``)
    # and ``consult``'s question to one: no local run inherits the rehearsal,
    # so ``_delegate_remote`` holds it there and sends nothing.
    "remote_handoff": SideDoor(
        "send", "hand the task to a remote agent", "send", "message", "agents/delegate_tool.py"
    ),
    # An outgoing webhook ("let Iron Jarvis tell other apps when work
    # finishes"): listed when the rehearsal starts; the delivery itself skips
    # every event of a rehearsal's run (``webhooks/outbound.on_event``).
    "outbound_webhook": SideDoor(
        "send", "tell another app through a webhook that the task finished", "send", "message",
        "tools/rehearsal_runs.py",
    ),
}


def record_side_door(
    engine: Any,
    *,
    session_id: str,
    run_id: str = "",
    door: str,
    words: str = "",
    target: str = "",
) -> str:
    """Write ONE rehearsed row for a side door that did nothing. BLOCKING
    (one insert) — call it off the loop. Returns the row id ("" on failure;
    never raises)."""
    try:
        from ..core.db import row_values
        from ..core.ids import new_id
        from ..core.models import PermissionMode, ToolInvocation

        spec = SIDE_DOORS[door]
        said = _one_line(words or spec.words, 200)
        row = row_values(
            ToolInvocation,
            id=new_id("tool"),
            session_id=str(session_id or ""),
            agent_run_id=str(run_id or ""),
            tool=door,
            args_json=json.dumps({"words": said, "target": _one_line(target, 160)}),
            verdict=PermissionMode.ALLOW,
            ok=True,
            output=held_output(Effect(spec.kind, target, said, spec.verb, spec.noun), "run"),
            reversibility=Reversibility.IRREVERSIBLE.value,
            rehearsed=True,
            would="run",
        )
        with engine.begin() as conn:
            conn.execute(ToolInvocation.__table__.insert(), row)
        return str(row.get("id") or "")
    except Exception:  # noqa: BLE001 — a record never breaks the run
        return ""
