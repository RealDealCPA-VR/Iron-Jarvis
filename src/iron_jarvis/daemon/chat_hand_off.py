"""A chat reply that hands off to the full agent with no window open
(fourth plan G28, DT1; owner decisions D6 and D13).

BEFORE: only the Chat page performed a hand-off (``sendAgent``). A reply that
decided it needed the full agent while no window followed it was written into
its chat by the daemon as its plain text, usually "(no response)", the tray
said the answer was finished, and the work the user asked for never started.

NOW the daemon performs it, at the one place a reply nobody followed goes
home (``durable_turns.TurnLogs._home_now``), through the SAME door code as
``POST /sessions`` and ``POST /agents/{name}/spawn``
(``routes.sessions.open_session_door``, ``routes.agents.spawn_door``), guard
included: a page that also hands the turn off gets the same run back.

* What rides is what the page's ``sendAgent`` sends: the request with a recap
  of the chat (``comm.inbound.InboundPoller.recap_task``, the ONE recap
  helper), the attachments, the skill, the tools and grants, the approval
  level, the folder, the project, the provider and model, Local only and the
  account. Each consent-carrying field is the STRICTER of the turn's body and
  the chat's setup as stored NOW: the user may have switched Local only on,
  tightened the level or removed a tool while the reply ran, and ``sendAgent``
  would have read the newer setup. Local only on in either wins; the tighter
  level wins; a tool the stored setup no longer holds stays out.
* The run is a chat-started run (``origin: "chat"``, D13): a question it asks
  waits for the user with no time limit, and the bell lists it.
* The hand-off line is written BEFORE the run starts (``before_start``), so a
  run that ends at once cannot land its reply above it.
* A door's refusal is a sentence in the chat, never an error: "This needs an
  agent run, and it could not start: <the door's own sentence>."
* The daemon always opens a NEW run; continuing an earlier run stays a press
  in an open window.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

from ..core.logging import get_logger

log = get_logger(__name__)

#: The longest the end of a reply waits for its hand-off (the run made and
#: its line written); the work goes on shielded after it.
HAND_OFF_TIMEOUT_S = 30.0
#: The page's ``MAX_TOOLS``: the armed tools that ride, at most.
MAX_TOOLS = 6
#: The page's reason when the turn gave none (``completeChat``).
DEFAULT_REASON = "this one needs the full agent"
#: ``handOff`` on the line of a hand-off that could not start (the page
#: offers to start it from there).
NOT_STARTED = "not started"
#: The line of a hand-off a door refused (the door's own sentence follows).
REFUSED_LEAD = "This needs an agent run, and it could not start: "
#: The line of a hand-off Iron Jarvis closed before it could start (the page
#: draws a "Start it now" press under a not-started line).
CLOSED_LINE = (
    "This needs an agent run, and Iron Jarvis closed before it could start one."
    " Press Start it now to start it."
)
#: Said after a refusal that trying again can change (a problem starting it,
#: not a door's own refusal: those say what to change).
RETRY_WORDS = "Press Start it now to try again."
#: The sentence when the door gave none of its own.
STARTING_FAILED = "Iron Jarvis hit a problem starting it."
#: The folder door's sentence, in plain words (its own names ``workspace_root``).
FOLDER_REFUSED = (
    "the chat's folder is missing, protected, or not one Iron Jarvis may save"
    " files in. Pick a folder you can save files in"
)
#: The spawn door's 404, in plain words.
AGENT_GONE = "the agent it was meant for is not on this PC any more"
#: Approval answers that mean "for this conversation" (or wider).
_GRANTING = frozenset({"conversation", "always"})
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


# --------------------------------------------------------------------------- #
# pure: what the turn asked for, and the lines
# --------------------------------------------------------------------------- #
def _data(raw: Any) -> dict[str, Any]:
    try:
        got = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def escalation_of(frames: Any) -> "dict[str, Any] | None":
    """The hand-off a turn's stored frames ask for: its ``done`` frame's
    ``escalate`` with the reason, the agent named, what the turn already ran
    and the approvals given; None for a turn that did not hand off. Pure;
    never raises."""
    try:
        done: "dict[str, Any] | None" = None
        for frame in frames if isinstance(frames, list) else []:
            if isinstance(frame, (list, tuple)) and len(frame) == 2 and frame[0] == "done":
                done = _data(frame[1])
        if not done or not done.get("escalate"):
            return None
        prior = done.get("prior_tool_results")
        approved = done.get("approved")
        return {
            "reason": " ".join(str(done.get("escalate_reason") or "").split()) or DEFAULT_REASON,
            "agent": str(done.get("escalate_agent") or "").strip(),
            "prior_tool_results": prior if isinstance(prior, list) else [],
            "approved": approved if isinstance(approved, list) else [],
        }
    except Exception:  # noqa: BLE001
        return None


def _agent_phrase(name: str) -> str:
    """How a roster name reads in a sentence (the page's ``agentPhrase``)."""
    custom = name.startswith("custom:")
    bare = (name[len("custom:"):] if custom else name).strip()
    if not bare:
        return "a specialist agent"
    return f"your {bare} agent" if custom else f"the {bare}"


def target_of(agent: str) -> dict[str, str]:
    """Which door runs the hand-off and what the line names (the page's
    ``sendAgent`` rule): ``custom:<slug>`` -> the spawn door with that slug;
    a builtin name other than the builder -> ``agent_type``; a remote pick ->
    the builder here, said on the line (``droppedRemote``); else the
    builder. Pure."""
    wanted = str(agent or "").strip()
    slug = wanted[len("custom:"):] if wanted.startswith("custom:") else ""
    builtin = wanted if wanted and ":" not in wanted and wanted != "builder" else ""
    target = wanted if slug else builtin
    remote = ""
    if wanted.lower().startswith("remote:"):
        name = wanted[len("remote:"):].strip()
        remote = name if _REMOTE_NAME.match(name) else ""
    return {
        "slug": slug,
        "agent_type": builtin or "builder",
        "escalated_to": _agent_phrase(target) if target else "",
        "dropped_remote": remote,
    }


def _base(turn_id: str) -> dict[str, Any]:
    return {"role": "assistant", "turnId": turn_id, "savedBy": "daemon"}


def hand_off_line(turn_id: str, esc: dict[str, Any], session_id: str) -> dict[str, Any]:
    """The line ``sendAgent`` saves for a hand-off that started: no words of
    its own (the page reads "Taking this on properly: <reason>." from
    ``escalated``), the run it waits on in ``awaitingSession``. Pure."""
    tgt = target_of(str(esc.get("agent") or ""))
    line = {**_base(turn_id), "content": "", "escalated": str(esc.get("reason") or DEFAULT_REASON)}
    if tgt["escalated_to"]:
        line["escalatedTo"] = tgt["escalated_to"]
    if tgt["dropped_remote"]:
        line["droppedRemote"] = tgt["dropped_remote"]
    if session_id:
        line["awaitingSession"] = str(session_id)
    return line


def _one_sentence(text: str) -> str:
    said = " ".join(str(text or "").split()).strip()
    if not said:
        return STARTING_FAILED
    return said if said[-1] in ".!?" else said + "."


def _not_started(turn_id: str, esc: "dict[str, Any] | None", content: str) -> dict[str, Any]:
    """A not-started line: its sentence, ``handOff: "not started"`` and the
    reason in ``escalated`` (the page's "Start it now" press hands off with
    it). Pure."""
    reason = str((esc or {}).get("reason") or "") or DEFAULT_REASON
    return {**_base(turn_id), "content": content, "escalated": reason, "handOff": NOT_STARTED}


def refused_line(turn_id: str, sentence: str, esc: "dict[str, Any] | None" = None, *,
                 retry: bool = False) -> dict[str, Any]:
    """The line of a hand-off a door refused, in the door's own words; with
    *retry* (a problem starting it, which trying again can change) it says
    so. Pure."""
    content = REFUSED_LEAD + _one_sentence(sentence)
    if retry:
        content = f"{content} {RETRY_WORDS}"
    return _not_started(turn_id, esc, content)


def closed_line(turn_id: str, esc: "dict[str, Any] | None" = None) -> dict[str, Any]:
    """The line of a hand-off Iron Jarvis closed before it could start (the
    start-up pass found no run). Pure."""
    return _not_started(turn_id, esc, CLOSED_LINE)


# --------------------------------------------------------------------------- #
# the body, and the stricter of it and the stored setup
# --------------------------------------------------------------------------- #
def _names(raw: Any) -> list[str]:
    out: list[str] = []
    for v in raw if isinstance(raw, (list, tuple)) else []:
        name = str(v or "").strip() if isinstance(v, str) else ""
        if name and name not in out:
            out.append(name)
    return out


def plan_from_body(body: Any) -> dict[str, Any]:
    """The fields of a chat turn's body a hand-off carries (what ``sendAgent``
    sends), as plain data kept with the turn. Pure; never raises."""
    try:
        msgs = []
        for m in list(getattr(body, "messages", None) or []):
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            msgs.append({"role": str(role or ""), "content": str(content or "")})
        return {
            "messages": msgs,
            "attachments": _names(getattr(body, "attachments", None)),
            "skill": str(getattr(body, "skill", "") or "").strip(),
            "tools": _names(getattr(body, "tools", None)),
            "granted_tools": _names(getattr(body, "granted_tools", None)),
            "approval_mode": str(getattr(body, "approval_mode", "") or "").strip(),
            "workspace_dir": str(getattr(body, "workspace_dir", "") or "").strip(),
            "project_id": str(getattr(body, "project_id", "") or "").strip(),
            "provider": str(getattr(body, "provider", "") or "").strip(),
            "model": str(getattr(body, "model", "") or "").strip(),
            "local_only": bool(getattr(body, "local_only", False)),
            "account": str(getattr(body, "account", "") or "").strip(),
        }
    except Exception:  # noqa: BLE001 — a plan that cannot be read is no plan
        log.warning("could not keep a chat turn's hand-off fields", exc_info=True)
        return {}


def stored_setup(engine: Any, thread_id: str) -> "tuple[dict[str, Any] | None, str | None, bool]":
    """The chat's setup as stored now (None when the chat has none saved),
    its project, and whether it is Local only (``egress.thread_local_only``:
    the switch or a hold). BLOCKING (one row read); never raises."""
    tid = str(thread_id or "").strip()[:80]
    if not tid or engine is None:
        return None, None, False
    try:
        from ..core.db import session_scope
        from ..core.models import ChatThreadRecord
        from ..providers.egress import setup_says_local_only

        with session_scope(engine) as db:
            row = db.get(ChatThreadRecord, tid)
            if row is None:
                return None, None, False
            raw, project = row.setup_json, row.project_id
        local = setup_says_local_only(raw)
        try:
            setup = json.loads(raw or "")
        except ValueError:
            setup = None
        return (setup if isinstance(setup, dict) and setup else None), project, local
    except Exception:  # noqa: BLE001 — unread: the body decides
        log.warning("could not read chat %s's setup for its hand-off", tid, exc_info=True)
        return None, None, False


def _stricter_level(a: str, b: str) -> str:
    """The tighter of two approval levels (the one order, ``routes.build.
    level_rank``); "" when both are the default."""
    from .chat_turn import normalize_approval_mode
    from .routes.build import level_rank

    pick = a if level_rank(a) >= level_rank(b) else b
    mode = normalize_approval_mode(pick)
    return "" if mode == "approve_for_me" else mode


def effective(plan: dict[str, Any], esc: dict[str, Any], setup: "dict[str, Any] | None",
              project: "str | None", stored_local: bool) -> dict[str, Any]:
    """Each field the run gets: the STRICTER of the turn's body (*plan*) and
    the chat's stored setup (None: no setup saved, the body decides). Pure."""
    has = setup is not None
    s = setup or {}
    tools = list(plan.get("tools") or [])[:MAX_TOOLS]
    grants = list(plan.get("granted_tools") or [])
    for a in esc.get("approved") or []:
        if isinstance(a, dict) and str(a.get("decision") or "") in _GRANTING:
            name = str(a.get("tool") or "").strip()
            if name and name not in grants:
                grants.append(name)
    allow = tools + [g for g in grants if g not in tools]
    if has:
        still = set(_names(s.get("tools"))) | set(_names(s.get("granted_tools")))
        allow = [t for t in allow if t in still]  # a removed tool stays removed
    level = str(plan.get("approval_mode") or "")
    if has:
        # The stored setup keeps no level for the default: absent IS it.
        level = _stricter_level(level, str(s.get("approval_mode") or ""))
    folder = str(s.get("workspace_dir") or "").strip() if has else str(plan.get("workspace_dir") or "")
    provider = str(s.get("provider") or "").strip() if has and s.get("provider") else str(plan.get("provider") or "")
    model = str(s.get("model") or "").strip() if has and s.get("provider") else str(plan.get("model") or "")
    account = str(s.get("account") or "").strip() if has else str(plan.get("account") or "")
    return {
        "allow_tools": allow,
        "approval_mode": level,
        "workspace_root": folder,
        "provider": provider,
        "model": model,
        "account": account,
        "project_id": (str(project).strip() if project else "") or str(plan.get("project_id") or ""),
        "local_only": bool(plan.get("local_only")) or bool(stored_local),
    }


def task_of(plan: dict[str, Any]) -> str:
    """The run's task: the request (with the skill named and the files
    attached, as ``sendAgent`` words them) under a recap of the chat — the
    ONE recap helper the phone's hand-off uses. Pure."""
    from ..comm.inbound import InboundPoller

    msgs = list(plan.get("messages") or [])
    request = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "user"), "")
    skill = str(plan.get("skill") or "")
    skill_line = (
        f'Use the "{skill}" skill for this — load it with skill_load first.\n\n' if skill else ""
    )
    attach = "".join(f"\n\nAttached file: {p}" for p in plan.get("attachments") or [])
    return InboundPoller.recap_task(msgs, skill_line + str(request) + attach)


# --------------------------------------------------------------------------- #
# the hand-off
# --------------------------------------------------------------------------- #
def _refusal_sentence(exc: BaseException) -> "tuple[str, bool]":
    """A door's refusal as the sentence the chat keeps (plain words), and
    whether trying again can change it (only a problem starting it: a door's
    own refusal says what to change)."""
    from fastapi import HTTPException

    from ..core.failures import plain_failure
    from ..providers.egress import LocalOnlyRefused

    if isinstance(exc, LocalOnlyRefused):
        return str(exc), False
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else ""
        if exc.status_code == 400 and detail.startswith("workspace_root"):
            return FOLDER_REFUSED, False
        if exc.status_code == 404:
            return AGENT_GONE, False
        return (detail, False) if detail else (STARTING_FAILED, True)
    said = plain_failure(STARTING_FAILED, exc)
    return said, said == STARTING_FAILED


class HandOff:
    """A chat turn's hand-off, kept with the turn (``TurnLog.hand_off``) and
    performed by the daemon when the turn ends with nobody following it."""

    def __init__(self, d: Any, plan: dict[str, Any]) -> None:
        self.d = d
        self.plan = plan

    async def __call__(
        self,
        turn_id: str,
        thread_id: str,
        esc: dict[str, Any],
        write: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> str:
        """Open the run through the door and write the chat's line with
        *write* (before the run starts). Returns the run's id, "" when it
        could not start (the refusal is written). Never raises but a
        cancellation."""
        d = self.d
        written = {"line": False}

        async def before_start(session_id: str) -> None:
            written["line"] = True
            await write(hand_off_line(turn_id, esc, session_id))

        try:
            setup, project, stored_local = await asyncio.to_thread(
                stored_setup, d.platform.engine, thread_id,
            )
            eff = effective(self.plan, esc, setup, project, stored_local)
            tgt = target_of(str(esc.get("agent") or ""))
            common: dict[str, Any] = {
                "task": task_of(self.plan),
                "wait": False,
                "origin": "chat",
                "allow_tools": eff["allow_tools"],
                "approval_mode": eff["approval_mode"],
                "workspace_root": eff["workspace_root"],
                "local_only": eff["local_only"],
                "prior_tool_results": esc.get("prior_tool_results") or [],
                "chat_thread_id": str(thread_id or ""),
                "handed_from_turn": turn_id,
            }
            if tgt["slug"]:
                from .routes.agents import spawn_door
                from .schemas import SpawnBody

                out = await spawn_door(d, tgt["slug"], SpawnBody(**common), before_start=before_start)
            else:
                from .routes.sessions import open_session_door
                from .schemas import SessionCreate

                body = SessionCreate(
                    **common,
                    agent_type=tgt["agent_type"],
                    provider=eff["provider"] or None,
                    model=eff["model"] or None,
                    project_id=eff["project_id"],
                    account=eff["account"],
                )
                out = await open_session_door(d, body, before_start=before_start)
            run = str((out or {}).get("id") or "")
            if not written["line"]:
                # The turn had already handed off (another window): name it.
                await write(hand_off_line(turn_id, esc, run))
            return run
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a refusal is a sentence
            if written["line"]:
                # The run was made and its line written; only its start failed.
                log.warning("chat turn %s: its agent run did not start", turn_id, exc_info=True)
                return ""
            sentence, retry = _refusal_sentence(exc)
            log.info("chat turn %s: its hand-off was refused: %s", turn_id, sentence)
            await write(refused_line(turn_id, sentence, esc, retry=retry))
            return ""


def plan(d: Any, body: Any) -> "HandOff | None":
    """The hand-off a chat page's streamed turn keeps (``routes.chat``): None
    for a Build pane's turn (``pane_id``: its own surface hands nothing off)."""
    if str(getattr(body, "pane_id", "") or "").strip():
        return None
    fields = plan_from_body(body)
    return HandOff(d, fields) if fields else None


__all__ = [
    "AGENT_GONE", "CLOSED_LINE", "DEFAULT_REASON", "FOLDER_REFUSED", "HAND_OFF_TIMEOUT_S",
    "HandOff", "MAX_TOOLS", "NOT_STARTED", "REFUSED_LEAD", "RETRY_WORDS", "STARTING_FAILED",
    "closed_line", "effective",
    "escalation_of", "hand_off_line", "plan", "plan_from_body", "refused_line",
    "stored_setup", "target_of", "task_of",
]
