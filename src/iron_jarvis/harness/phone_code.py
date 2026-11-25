"""START CODING WORK FROM YOUR PHONE (next leaps idea 12, phase 12-a; master
plan R2.5; decisions D-12.1 and D-12.2; the night shift plan's decision 5 of
2026-10-26).

"in the website folder, fix the broken contact form" (or ``/code website: fix
the broken contact form``) gets ONE card on the phone: what will happen, what
it uses ("Uses your Claude plan (42% of this 5-hour window used)"), and Start
or Not now. Start queues a night task that starts NOW (D-12.1: inside the
night shift's own limits — how many at once, one per folder, its time limit,
the pay-per-use limit, Emergency stop), on a new branch of its own, under the
night's rules (full trust inside its branch, everything else asks and is
declined after the wait); it is committed to that branch and never pushed or
merged (D-12.2). The phone hears "Started on branch …" when it starts and ONE
ending, read from the task's record, never the model's words.

This module is the ONE door both ways in — the ``/code`` command and the
phone chat's ``code_start`` tool (``settings/records_build.CodeStartTool``):

* :func:`find_folder` — only folders the user has USED in Iron Jarvis (coding
  sessions' folders and their display names, project roots, earlier night
  tasks), matched on the display name or the folder's own name, ignoring
  case. A full path is taken only when it is one of those folders. None: the
  words name the first eight; more than one: a numbered pick.
* :func:`propose` — every check the night task's add and start would run,
  BEFORE asking (a card is never offered for work that would then be
  refused); registers the ``code_start`` prompt on the ASKING sender's chat.
* :func:`start_from_card` — on Start: the same checks again, then ONE task
  (``night.add_task(start_now=True, asked_from="comm:<channel>",
  report_json=...)``) with exactly the words the card showed.
* :func:`ending_words` / :func:`started_words` — from the record;
  :func:`deliver` sends them to the chat in ``report_json`` (the bus handler
  ``daemon/app`` registers for ``build.phone_code``).

THE ASKING SENDER is set only by the phone door (``comm/inbound``), after its
allowlist, its proof and the private-chat guard (:func:`set_asking`): the
chat tool reads it, so a tool call from anywhere else (the PC's chat, an
agent run) asks nothing and starts nothing. The tool writes no task — only
the person's own Start does — which is why it is not on the low-trust list
while ``night_task_add`` stays there.

Everything that reads or writes the database is BLOCKING unless it is
``async``; nothing here raises into a phone message.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Prompt kinds and the card's two answers, in order (Reply 1 / Reply 2; the
#: buttons): the SAME values as ``comm/prompts``' (``CODE_START_KIND``,
#: ``CODE_FOLDER_KIND``, ``CODE_START_OPTIONS``; pinned equal by
#: ``tests/test_leap12a_code_start_card.py``). Not imported from there at
#: load: ``harness`` must not join ``comm``'s import group (docs/DEPENDENCIES.md).
CODE_START_KIND = "code_start"
CODE_FOLDER_KIND = "code_folder"
START_OPTIONS = ["Start", "Not now"]
START, NOT_NOW = START_OPTIONS

WHEN_NOW = "now"
WHEN_TONIGHT = "tonight"

#: ``NightTaskRecord.asked_from`` of a task started from the phone.
ASKED_FROM_PREFIX = "comm:"
#: How many of the user's folders a refusal names.
SHOWN_FOLDERS = 8
#: How long a card (or a folder pick) waits for its answer.
CARD_TTL_S = 3600.0
#: The longest request a phone may start (a night task's own limit is far
#: larger; a page of instructions belongs on the PC).
MAX_PHONE_WORDS = 4000

#: The bus event that carries a phone task's start and ending to the phone:
#: ``{task_id, step: "started" | "ended"}`` — never the words (the handler
#: reads them from the record).
from ..core.events import EventType as _EventType  # noqa: E402

EVENT = _EventType.BUILD_PHONE_CODE
STEP_STARTED = "started"
STEP_ENDED = "ended"

NO_WORDS = "Say what the coding work should do, for example: /code website: fix the broken contact form."
TOO_LONG = (
    f"That request is longer than the phone can start ({MAX_PHONE_WORDS:,} characters). "
    "Queue it on the PC in Coding sessions › Tonight instead."
)
NO_FOLDERS = (
    "I only start coding work in folders you have used in Iron Jarvis, and there are none yet. "
    "Open a folder in Build › Code on the PC first."
)
USAGE = (
    "Send /code <folder>: <what to do>, for example /code website: fix the broken contact form. "
    "Add tonight after the folder to queue it for tonight instead."
)
NOT_ON_PHONE = (
    "Coding work is started this way only from your phone. On this PC, open Build › Code, or queue it "
    "for tonight in Coding sessions › Tonight."
)
LOCAL_ONLY_WORDS = (
    "This chat is Local only, so coding work started from it runs only on a model on this PC or your own "
    "network, and {model} is not one. Pick a local default model in Settings, or ask from a chat that is "
    "not Local only."
)
NOTHING_STARTED = "→ OK, nothing was started."
CARD_GONE = "That question is no longer open, so nothing was started. Ask again to start it."


# --------------------------------------------------------------------------- #
# the asking sender (set only by the phone door)
# --------------------------------------------------------------------------- #


@dataclass
class Asking:
    """Who asked, as the phone door proved it: the destination's name, the
    sender, their phone chat on the PC, where a reply goes, whether that chat
    is Local only, and the prompt store. ``cards`` collects what the chat
    tool asked during the turn; the door sends them after the reply."""

    channel: str
    sender_id: str
    thread_id: str = ""
    reply_to: str = ""
    local_only: bool = False
    store: Any = None
    cards: list[dict[str, Any]] = field(default_factory=list)


_ASKING: "contextvars.ContextVar[Asking | None]" = contextvars.ContextVar("ij_phone_code_asking", default=None)


def asking() -> "Asking | None":
    return _ASKING.get()


def set_asking(who: "Asking | None") -> Any:
    return _ASKING.set(who)


def reset_asking(token: Any) -> None:
    try:
        _ASKING.reset(token)
    except Exception:  # noqa: BLE001 — another context's token
        _ASKING.set(None)


def door_open() -> bool:
    """Is this turn the phone's own chat turn (the phone surface AND a sender
    the phone door proved)? What arms ``code_start``. Never raises."""
    try:
        from ..providers.failure_words import SURFACE_PHONE, current_surface

        return current_surface() == SURFACE_PHONE and asking() is not None
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# the folders you have used
# --------------------------------------------------------------------------- #


def _base(path: str) -> str:
    text = str(path or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] or text


def where_words(path: str) -> str:
    """The last two parts of a folder ("code/website"): enough to tell two
    folders of one name apart, without the whole path."""
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p and not p.endswith(":")]
    return "/".join(parts[-2:]) if parts else str(path or "")


def used_folders(engine: Any) -> list[dict[str, str]]:
    """The folders the user has used in Iron Jarvis, newest use first:
    coding sessions' folders (the folder the user picked, never a branch's
    worktree), active projects' roots, earlier night tasks' folders. Each
    ``{path, name, key}`` with its ONE display name (``sessions.folder_names``).
    BLOCKING; never raises ([])."""
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import BUILD_SURFACE, ChatThreadRecord, Project
        from . import sessions as S
        from .branches import facts_of
        from .night import NightTaskRecord

        seen: dict[str, str] = {}

        def add(path: Any) -> None:
            text = str(path or "").strip()
            key = S.folder_key(text)
            if key and key not in seen:
                seen[key] = text

        T = ChatThreadRecord
        with session_scope(engine) as db:
            rows = db.exec(
                select(T.setup_json).where(T.surface == BUILD_SURFACE)
                .order_by(T.updated_at.desc())  # type: ignore[union-attr]
            ).all()
            for raw in rows:
                try:
                    setup = json.loads(raw) if raw else {}
                except (TypeError, ValueError):
                    continue
                if not isinstance(setup, dict):
                    continue
                facts = facts_of(setup) or {}
                add(facts.get("checkout") or setup.get("workspace_dir"))
            for folder in db.exec(
                select(NightTaskRecord.folder).order_by(NightTaskRecord.created_at.desc())  # type: ignore[union-attr]
            ).all():
                add(folder)
            for project in db.exec(select(Project)).all():
                if (getattr(project, "status", "active") or "active") != "archived":
                    add(getattr(project, "root", ""))
        names = S.folder_names(engine, list(seen.values()))
        return [{"path": path, "name": str((names.get(key) or {}).get("name") or _base(path)), "key": key}
                for key, path in seen.items()]
    except Exception:  # noqa: BLE001 — no folders known: nothing is started
        log.warning("the folders used in Iron Jarvis could not be read", exc_info=True)
        return []


_LEAD = re.compile(r"^(?:in\s+|on\s+|at\s+)?(?:the|my|our)\s+", re.IGNORECASE)
_TAIL = re.compile(r"\s+(?:folder|repo|repository|project|codebase|directory)$", re.IGNORECASE)


def clean_folder_name(raw: Any) -> str:
    """"the website folder" → "website"; quotes and a trailing dot off."""
    text = " ".join(str(raw or "").split()).strip().strip("\"'“”‘’`").rstrip(".").strip()
    text = _LEAD.sub("", text, count=1)
    text = _TAIL.sub("", text, count=1)
    return text.strip()


def _looks_like_path(text: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith(("/", "\\", "~"))


def find_folder(engine: Any, raw: Any) -> list[dict[str, str]]:
    """The used folders ``raw`` names: by display name or folder name,
    ignoring case; a full path only when it IS one of them. BLOCKING."""
    from . import sessions as S

    text = clean_folder_name(raw)
    if not text:
        return []
    folders = used_folders(engine)
    if _looks_like_path(text):
        if S._network_spelling(text):  # noqa: SLF001 — never resolved, never matched
            return []
        key = S.folder_key(text)
        return [f for f in folders if f["key"] == key]
    low = text.casefold()
    return [f for f in folders if f["name"].casefold() == low or _base(f["path"]).casefold() == low]


def none_words(engine: Any) -> str:
    """The refusal when no used folder matches: the first eight by name."""
    folders = used_folders(engine)
    if not folders:
        return NO_FOLDERS
    names: list[str] = []
    counts: dict[str, int] = {}
    for f in folders:
        counts[f["name"].casefold()] = counts.get(f["name"].casefold(), 0) + 1
    for f in folders[:SHOWN_FOLDERS]:
        names.append(f"{f['name']} ({where_words(f['path'])})" if counts[f["name"].casefold()] > 1 else f["name"])
    more = f", and {len(folders) - SHOWN_FOLDERS} more" if len(folders) > SHOWN_FOLDERS else ""
    return f"I only start coding work in folders you have used in Iron Jarvis. Yours: {', '.join(names)}{more}."


def usage_words(engine: Any) -> str:
    """``/code`` with nothing after it: how to use it, and the user's
    folders by name. BLOCKING."""
    folders = used_folders(engine)
    if not folders:
        return f"{USAGE} {NO_FOLDERS}"
    names = ", ".join(dict.fromkeys(f["name"] for f in folders[:SHOWN_FOLDERS]))
    return f"{USAGE} Folders you have used: {names}."


def pick_options(matches: list[dict[str, str]]) -> list[str]:
    return [f"{m['name']} ({where_words(m['path'])})" for m in matches]


def pick_words(matches: list[dict[str, str]]) -> str:
    listing = "\n".join(f"{i}. {o}" for i, o in enumerate(pick_options(matches), 1))
    return f"More than one folder is called {matches[0]['name']}. Which one?\n{listing}"


# --------------------------------------------------------------------------- #
# what it uses
# --------------------------------------------------------------------------- #

_VENDOR_WORDS = {"claude": "Claude", "codex": "Codex", "grok": "Grok"}


def uses_words(platform: Any, provider: str, model: str) -> str:
    """What the work will use, in one sentence for the card (the master
    plan's calm note: "Uses your Claude plan (42% of this window used)").
    From Iron-Proxy's CACHED usage only — never a vendor call. BLOCKING
    (this month's pay-per-use spend); never raises."""
    from ..agents import model_team as MT

    try:
        name = MT.model_words(platform, provider, model) if provider else "the default model"
        cost = MT.cost_type(platform, provider, model) if provider else ""
        if cost == MT.COST_PLAN:
            from . import build_accounts as BA

            vendor = BA.vendor_of(platform, provider)
            plan = f"your {_VENDOR_WORDS.get(vendor, vendor.title() or name)} plan"
            # 12-a review: the account the work REALLY runs on — the vendor's
            # Default, which a new session is pinned to whatever its status
            # (``routes/build._with_initial_account``) — never the first
            # account that could serve, whose numbers are not this work's.
            svc = MT._svc(platform)  # noqa: SLF001 — the same cached service usage_for reads
            own = BA.default_for(svc, vendor) if vendor in BA.VENDORS else None
            if own is not None and own.get("status") == BA.LIMIT:
                cfg = getattr(platform, "config", None)
                when = MT._reset_words(own.get("resets_at"))  # noqa: SLF001 — the planner's own clock words
                at_limit = f"at its usage limit {f'until {when}' if when else 'now'}"
                # 12-a review 3: "Carry on by itself after an account's limit"
                # OFF arms neither a wait nor a move within the group
                # (``routes/build.auto_wait``): the task ends at the limit.
                if not bool(getattr(cfg, "build_auto_resume", True)):
                    return (f"Uses {plan} ({at_limit}; it stops at the limit, because "
                            "\"Carry on by itself after an account's limit\" is off).")
                groups = getattr(cfg, "account_groups", None) or {}
                partner = (BA.takeover(svc, groups, str(own.get("id") or ""))
                           if own.get("limit_kind") in BA.MOVE_KINDS else None)
                if partner is not None:
                    return (f"Uses {plan} ({own.get('name') or 'its account'} is at its usage limit now; "
                            f"{partner.get('name') or 'another account'}, in the same group, takes over).")
                return f"Uses {plan} ({at_limit}; it waits for the reset)."
            if own is not None and own.get("status") == BA.NEEDS_SIGN_IN:
                return f"Uses {plan} (its account needs to sign in again)."
            usage = MT.usage_for(platform, provider, str(own.get("id") or "")) if own is not None else None
            if not usage:
                return f"Uses {plan} (usage not known yet)."
            pct = usage.get("percent")
            if isinstance(pct, int) and not isinstance(pct, bool):
                adj = MT._window_adjective(usage.get("window_mins"))  # noqa: SLF001 — the planner's own words
                return f"Uses {plan} ({pct}% of this {adj + ' ' if adj else ''}window used)."
            return f"Uses {plan} (usage not known yet)."
        if cost == MT.COST_FREE:
            place = MT.PLACE_WORDS.get(MT.place_of(platform, provider, model), "this PC")
            return f"Uses {name} on {place}, at no cost."
        if cost == MT.COST_PAY:
            from ..daemon.routes.night import pay_room

            room = pay_room(platform, provider, model)
            left = f" (${room:.2f} of this month's pay-per-use limit left)" if room is not None else ""
            return f"Uses {name}, billed per use{left}."
        return f"Uses {name}."
    except Exception:  # noqa: BLE001 — the card still says the model
        log.debug("the card's usage words failed", exc_info=True)
        return f"Uses {provider or 'the default model'}."


# --------------------------------------------------------------------------- #
# the checks, and the card
# --------------------------------------------------------------------------- #


@dataclass
class Card:
    """What :func:`propose` answers: ``ok`` with the card's words and its
    prompt, or a refusal in ``words``."""

    ok: bool
    words: str
    prompt_id: str = ""
    kind: str = CODE_START_KIND
    options: list[str] = field(default_factory=list)
    hint: str = ""


def answer_hint(kind: str, count: int = 2) -> str:
    if kind == CODE_FOLDER_KIND:
        return "Reply with its number."
    return "Reply 1 to start or 2 for not now."


def _pair(platform: Any) -> tuple[str, str]:
    from ..daemon.routes.night import _night_pair

    return _night_pair(platform, {})


def _emergency(platform: Any) -> bool:
    try:
        from ..agents.orchestrator import emergency_stop_on

        return bool(emergency_stop_on(getattr(platform, "config", None)))
    except Exception:  # noqa: BLE001 — unsure: refused (it would start unasked)
        return True


async def problem(platform: Any, folder: str, words: str, *, local_only: bool) -> str:
    """Every check the night task's add and start run, BEFORE anything is
    asked or kept: the words, Emergency stop, the folder (usable, in a
    repository that can have a branch), the pay-per-use limit for the model
    it runs on, and a Local only chat's model. "" when it may start."""
    from ..daemon.routes import night as NR
    from . import night as N

    text = str(words or "").strip()
    if not text:
        return NO_WORDS
    if len(text) > MAX_PHONE_WORDS:
        return TOO_LONG
    if _emergency(platform):
        from ..agents.orchestrator import EMERGENCY_STOP_REFUSAL

        return EMERGENCY_STOP_REFUSAL
    try:
        await NR._check_folder(folder)  # noqa: SLF001 — the night task's own check
    except N.NightRefused as exc:
        return str(exc)
    provider, model = _pair(platform)
    if local_only:
        # A Local only chat's work stays on this PC or the user's network:
        # said first, whatever the model would cost.
        from ..providers import egress

        try:
            stays = egress.stays_on_this_network(
                provider, getattr(platform, "providers", None), getattr(platform, "config", None), model)
        except Exception:  # noqa: BLE001 — unknown: it does not stay
            stays = False
        if not stays:
            return LOCAL_ONLY_WORDS.format(model=f"{provider} · {model}" if model else (provider or "the default model"))
    why = await asyncio.to_thread(NR.pay_problem, platform, provider, model)
    if why:
        return why
    return ""


def _wait_words(platform: Any, folder: str) -> str:
    """Why it would not start at once (one per folder, how many at once), or
    "". BLOCKING."""
    from . import night as N
    from . import sessions as S

    try:
        running = N.rows_in(platform.engine, N.RUNNING)
        key = S.folder_key(folder)
        if any(S.folder_key(t["folder"]) == key for t in running):
            return " It waits for the coding work already running in this folder to end."
        at_once = N.max_at_once(platform.config)
        if sum(N.task_weight(t, platform.config) for t in running) >= at_once:
            return f" It waits until one of the {at_once} running now ends."
    except Exception:  # noqa: BLE001 — words only
        pass
    return ""


def card_words(platform: Any, name: str, path: str, words: str, when: str) -> str:
    """The card (BLOCKING: the usage and the queue are read)."""
    from . import night as N

    minutes = N.task_minutes(platform.config)
    uses = uses_words(platform, *_pair(platform))
    where = f"{name} ({where_words(path)})"
    if when == WHEN_TONIGHT:
        start, end = N.window_of(platform.config)
        from .night_window import clock_words

        lead = f"Queue coding work in {where} for tonight (it starts between {clock_words(start)} and {clock_words(end)})?"
        wait = ""
    else:
        lead = f"Start coding work in {where} now?"
        wait = _wait_words(platform, path)
    quoted = " ".join(str(words or "").split())
    return (
        f"{lead} It works on a new branch of its own, edits and runs commands only there, may take up to "
        f"{minutes} minutes, and nothing is merged.{wait} {uses}\nWhat it will do: {quoted}"
    )


def question_words(name: str, when: str) -> str:
    """The short question a reminder repeats."""
    return (f"Queue coding work in {name} for tonight?" if when == WHEN_TONIGHT
            else f"Start coding work in {name} now?")


def _ref(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False)


def read_ref(prompt: Any) -> dict[str, Any]:
    try:
        raw = json.loads(str(getattr(prompt, "ref_id", "") or "{}"))
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


async def propose(platform: Any, who: Asking, *, folder: dict[str, str], words: str,
                  when: str = WHEN_NOW) -> Card:
    """The card for ONE used folder, or the refusal. Every check runs first
    (:func:`problem`); only then is the ``code_start`` prompt registered on
    the asking sender's chat (an older open card of theirs is closed: one
    card to answer)."""
    when = WHEN_TONIGHT if str(when or "").strip().lower() == WHEN_TONIGHT else WHEN_NOW
    text = " ".join(str(words or "").split())
    why = await problem(platform, folder["path"], text, local_only=who.local_only)
    if why:
        return Card(ok=False, words=why)
    if who.store is None:
        return Card(ok=False, words=NOT_ON_PHONE)
    words_out = await asyncio.to_thread(card_words, platform, folder["name"], folder["path"], text, when)
    await asyncio.to_thread(who.store.expire_kind, CODE_START_KIND, who.channel, who.sender_id)
    rec = await asyncio.to_thread(
        who.store.register, CODE_START_KIND,
        _ref(path=folder["path"], name=folder["name"], words=text, when=when),
        question_words(folder["name"], when), START_OPTIONS, who.channel, who.sender_id, who.thread_id,
    )
    if rec is None:
        return Card(ok=False, words="The question could not be kept, so nothing will start. Try again.")
    return Card(ok=True, words=words_out, prompt_id=rec.id, kind=CODE_START_KIND,
                options=list(START_OPTIONS), hint=answer_hint(CODE_START_KIND))


async def ask_which(platform: Any, who: Asking, matches: list[dict[str, str]], *, words: str,
                    when: str = WHEN_NOW) -> Card:
    """Two or more folders of one name: a numbered pick (``code_folder``)."""
    if who.store is None:
        return Card(ok=False, words=NOT_ON_PHONE)
    text = " ".join(str(words or "").split())
    if not text:
        return Card(ok=False, words=NO_WORDS)
    if len(text) > MAX_PHONE_WORDS:
        return Card(ok=False, words=TOO_LONG)
    options = pick_options(matches)
    await asyncio.to_thread(who.store.expire_kind, CODE_FOLDER_KIND, who.channel, who.sender_id)
    rec = await asyncio.to_thread(
        who.store.register, CODE_FOLDER_KIND,
        _ref(paths=[m["path"] for m in matches], words=text, when=when),
        f"Which {matches[0]['name']} folder?", options, who.channel, who.sender_id, who.thread_id,
    )
    if rec is None:
        return Card(ok=False, words="The question could not be kept. Try again.")
    return Card(ok=True, words=pick_words(matches), prompt_id=rec.id, kind=CODE_FOLDER_KIND,
                options=options, hint=answer_hint(CODE_FOLDER_KIND))


async def from_words(platform: Any, who: Asking, folder: Any, words: str, when: str = WHEN_NOW) -> Card:
    """The one entry both doors use: find the folder, then the card, a
    numbered pick, or the refusal naming the user's folders."""
    engine = platform.engine
    matches = await asyncio.to_thread(find_folder, engine, folder)
    if not matches:
        return Card(ok=False, words=await asyncio.to_thread(none_words, engine))
    if len(matches) > 1:
        return await ask_which(platform, who, matches, words=words, when=when)
    return await propose(platform, who, folder=matches[0], words=words, when=when)


async def after_pick(platform: Any, who: Asking, prompt: Any, answer: str) -> Card:
    """The answer to the folder pick: the card for the folder picked."""
    ref = read_ref(prompt)
    paths = [str(p) for p in (ref.get("paths") or []) if str(p or "").strip()]
    try:
        from ..comm.prompts import prompt_options

        idx = prompt_options(prompt).index(answer)
    except ValueError:
        return Card(ok=False, words="Reply with the number of the folder.")
    if not 0 <= idx < len(paths):
        return Card(ok=False, words="Reply with the number of the folder.")
    from . import sessions as S

    key = S.folder_key(paths[idx])
    folder = next((f for f in await asyncio.to_thread(used_folders, platform.engine) if f["key"] == key), None)
    if folder is None:
        return Card(ok=False, words=await asyncio.to_thread(none_words, platform.engine))
    return await propose(platform, who, folder=folder, words=str(ref.get("words") or ""),
                         when=str(ref.get("when") or WHEN_NOW))


# --------------------------------------------------------------------------- #
# Start
# --------------------------------------------------------------------------- #


async def start_from_card(platform: Any, who: Asking, prompt: Any) -> tuple[str, str]:
    """Start: the checks again (things change between the card and the
    press), then ONE task with exactly the card's words, started now (or
    queued for tonight), its ending reported to this chat. Returns ``(the
    reply, "started" | "queued" | "refused")``. Never raises."""
    from . import night as N

    try:
        ref = read_ref(prompt)
        path = str(ref.get("path") or "")
        name = str(ref.get("name") or "") or _base(path)
        words = str(ref.get("words") or "")
        when = WHEN_TONIGHT if ref.get("when") == WHEN_TONIGHT else WHEN_NOW
        if not path:
            return CARD_GONE, "refused"
        why = await problem(platform, path, words, local_only=who.local_only)
        if why:
            return f"Nothing was started: {why}", "refused"
        report = {"channel": who.channel, "sender_id": str(who.sender_id), "thread_id": who.thread_id,
                  "reply_to": str(who.reply_to or who.sender_id), "name": name}
        wait = "" if when == WHEN_TONIGHT else await asyncio.to_thread(_wait_words, platform, path)
        task = await asyncio.to_thread(
            N.add_task, platform.engine, folder=path, words=words,
            start_now=when == WHEN_NOW, asked_from=f"{ASKED_FROM_PREFIX}{who.channel}",
            report_json=json.dumps(report),
        )
        from ..daemon.routes.night import wake_night_loop

        wake_night_loop()
        if when == WHEN_TONIGHT:
            start, end = N.window_of(platform.config)
            from .night_window import clock_words

            return (f"→ Queued for tonight in {name}: it starts between {clock_words(start)} and "
                    f"{clock_words(end)}. I'll tell you how it ends."), "queued"
        if wait:
            return f"→ Queued in {name}.{wait} I'll tell you when it starts.", "queued"
        log.info("phone task %s queued to start now", task.get("id"))
        return f"→ Starting coding work in {name}.", "started"
    except N.NightRefused as exc:
        return f"Nothing was started: {exc}", "refused"
    except Exception:  # noqa: BLE001 — the phone hears it, the log keeps why
        log.warning("a phone task could not be started", exc_info=True)
        return "Nothing was started: Iron Jarvis could not keep the task. Try again.", "refused"


# --------------------------------------------------------------------------- #
# what the phone hears when it starts and ends (from the record)
# --------------------------------------------------------------------------- #


def from_phone(task: Any) -> bool:
    return str((task or {}).get("asked_from") or "").startswith(ASKED_FROM_PREFIX)


def report_of(row_or_task: Any) -> dict[str, str]:
    """``report_json`` as a dict ({} when none or unreadable). Pure."""
    raw = row_or_task.get("report_json") if isinstance(row_or_task, dict) else getattr(row_or_task, "report_json", "")
    try:
        data = json.loads(str(raw or "") or "{}")
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _name(task: dict[str, Any], report: dict[str, str]) -> str:
    return report.get("name") or _base(str(task.get("folder") or "")) or "your folder"


def _local_only(task: dict[str, Any]) -> bool:
    morning = task.get("morning") if isinstance(task.get("morning"), dict) else {}
    return morning.get("local_only") is True


def started_words(task: dict[str, Any], report: dict[str, str], config: Any = None) -> str:
    """"Started on branch ij/… in website." — from the record. A Local only
    task's branch (made from its words) is not named."""
    from . import night as N

    name = _name(task, report)
    minutes = N.task_minutes(config) if config is not None else 90
    branch = str(task.get("branch") or "")
    if branch and not _local_only(task):
        head = f"Started on branch {branch} in {name}."
    else:
        head = f"Started in {name}, on a branch of its own."
    return f"{head} It may take up to {minutes} minutes; I'll tell you how it ends. Nothing is merged."


_STATE_HEAD = {"done": "Done", "needs_you": "Needs you", "failed": "Failed", "stopped": "Stopped"}


def ending_words(task: dict[str, Any], report: dict[str, str]) -> str:
    """The ONE ending, read from the task's record and its morning facts
    (``night_morning.summary_row``, masked as the night summary masks what
    reaches a phone; a Local only task's words, branch and notes left out)
    — never the model's words. "Checks failed" when its tests failed,
    "Needs you" with the first reason when it needs the user. Pure."""
    from . import night_morning as M

    name = _name(task, report)
    row = M.shared_row(M.summary_row(task))
    state = str(row.get("state") or "")
    if row.get("local_only"):
        head = _STATE_HEAD.get(state, "Ended")
        return f"{head} in {name}: {M.LOCAL_ONLY_LINE}. Nothing is merged."
    bits: list[str] = []
    if row.get("main_changed"):
        bits.append("Your main folder changed while it ran")
    if state == "failed" and row.get("tests") == "failed":
        head = "Checks failed"
    elif state == "failed":
        head = "Failed"
        if row.get("failure"):
            bits.append(str(row["failure"]).rstrip("."))
    else:
        head = _STATE_HEAD.get(state, "Ended")
    if state in ("needs_you", "stopped") or (state == "failed" and row.get("tests") == "failed"):
        bits.extend(str(n).rstrip(".") for n in (row.get("needs") or [])[:1])
    files = int(row.get("files") or 0)
    if files:
        bits.append(f"{files} file{'s' if files != 1 else ''} changed")
    if row.get("tests") == "passed":
        bits.append("tests passed" + ("" if row.get("tests_trusted", True) else " (not trusted)"))
    elif row.get("tests") == "failed":
        bits.append("tests failed")
    elif state == "done":
        bits.append("no tests were checked")
    detail = "; ".join(b for b in bits if b)
    line = f"{head} in {name}" + (f": {detail}." if detail else ".")
    branch = str(row.get("branch") or "")
    if row.get("commit") and branch:
        line += f" Its work is committed on the branch {branch}; nothing is merged."
    elif branch and files:
        line += f" Its work is on the branch {branch}; nothing is merged."
    else:
        line += " Nothing is merged."
    return line


# --------------------------------------------------------------------------- #
# delivery (the bus handler)
# --------------------------------------------------------------------------- #


def publish(event_bus: Any, task_id: str, step: str) -> None:
    """Ring :data:`EVENT` for one task's start or end. Best effort, on the
    loop as a task or under ``asyncio.run`` off it. Never raises."""
    if event_bus is None or not task_id:
        return
    try:
        from ..agents.chat_delivery import _publish_event

        _publish_event(event_bus, EVENT, {"task_id": str(task_id), "step": step})
    except Exception:  # noqa: BLE001 — the record stands without the message
        log.warning("phone task %s: its %s message could not be rung", task_id, step, exc_info=True)


async def publish_now(event_bus: Any, task_id: str, step: str) -> None:
    """:func:`publish` from the loop, awaited (its handlers have run when it
    returns). Never raises."""
    if event_bus is None or not task_id:
        return
    try:
        await event_bus.publish(EVENT, {"task_id": str(task_id), "step": step})
    except Exception:  # noqa: BLE001 — the record stands without the message
        log.warning("phone task %s: its %s message could not be rung", task_id, step, exc_info=True)


def _stopped_by_you(task: dict[str, Any]) -> bool:
    from . import night as N

    return any(n.get("kind") == "stopped" and n.get("text") in (N.CANCELLED_RUNNING, N.CANCELLED_BEFORE)
               for n in task.get("notes") or [])


def _onto_phone_chat(thread_store: Any, ch: Any, channel: str, sender: str, asked_in: str, words: str) -> str:
    """The line onto the phone chat on the PC (:func:`deliver`): the chat that
    asked (``report_json``'s ``thread_id``) while it still exists; else, only
    while this destination's two-way CHAT is on, the sender's chat as the
    poller resolves it (made on first contact, as a message would make it). A
    command-only destination keeps no chat on the PC (the poller's
    ``_answer_reply`` appends only with chat on), so this makes none. Returns
    the chat's id, or "" when no chat heard it. Raises what the store raises."""
    if asked_in:
        try:
            thread_store.append(asked_in, "assistant", words)
            return asked_in
        except ValueError:  # that chat was deleted on the PC: as below
            pass
    try:
        chat_on = bool(ch.chat_enabled())
    except Exception:  # noqa: BLE001 — a config quirk keeps no chat
        chat_on = False
    if not chat_on:
        return ""
    tid = thread_store.resolve(channel, sender, "").id
    thread_store.append(tid, "assistant", words)
    return tid


def deliver(event: Any, *, notifier: Any, thread_store: Any, engine: Any, config: Any = None) -> dict[str, Any]:
    """The ``build.phone_code`` bus handler (``daemon/app`` registers it): the
    start or the ending of ONE phone task, read from its record, to the chat
    that asked — its destination still set up and two-way, the sender still
    allowed (fail-closed, as the poller) — and onto that phone chat on the PC
    (:func:`_onto_phone_chat`: never a new chat for a command-only
    destination). A task the user stopped themselves says nothing. BLOCKING
    (it sends); never raises."""
    try:
        etype = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
        if etype != EVENT:
            return {"ok": False, "why": "not mine"}
        payload = (event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)) or {}
        task_id = str(payload.get("task_id") or "")
        step = str(payload.get("step") or "")
        from . import night as N

        task = N.get_task(engine, task_id)
        if task is None or not from_phone(task):
            return {"ok": False, "why": "not a phone task"}
        report = _report_for(engine, task_id)
        channel = report.get("channel", "")
        sender = report.get("sender_id", "")
        if not channel or not sender:
            return {"ok": False, "why": "nowhere to report"}
        if step == STEP_STARTED:
            words = started_words(task, report, config)
        elif step == STEP_ENDED:
            if task.get("state") == N.STOPPED and _stopped_by_you(task):
                return {"ok": False, "why": "stopped by you"}
            words = ending_words(task, report)
        else:
            return {"ok": False, "why": "unknown step"}
        ch = notifier.get(channel) if notifier is not None else None
        if ch is None:
            return {"ok": False, "why": "no such destination"}
        try:
            if not (ch.has_credentials() and ch.inbound_enabled() and ch.is_authorized(sender)):
                return {"ok": False, "why": "not allowed"}
        except Exception:  # noqa: BLE001 — a config quirk sends nothing
            return {"ok": False, "why": "not allowed"}
        tid = ""
        if thread_store is not None:
            try:
                tid = _onto_phone_chat(thread_store, ch, channel, sender, report.get("thread_id", ""), words)
            except Exception:  # noqa: BLE001 — the phone still hears it
                log.warning("phone task %s: its line could not land on the phone chat", task_id, exc_info=True)
        from ..comm.prompts import _send_chunked_sync

        sent = _send_chunked_sync(ch, f"Iron Jarvis: {words}", chat_id=report.get("reply_to") or sender)
        return {"ok": bool(sent), "words": words, "thread_id": tid}
    except Exception:  # noqa: BLE001 — the event bus must survive any handler
        log.exception("a phone task's message failed")
        return {"ok": False, "why": "failed"}


def _report_for(engine: Any, task_id: str) -> dict[str, str]:
    from ..core.db import session_scope
    from .night import NightTaskRecord

    with session_scope(engine) as db:
        row = db.get(NightTaskRecord, str(task_id))
        return report_of(row) if row is not None else {}


# --------------------------------------------------------------------------- #
# /code
# --------------------------------------------------------------------------- #

#: "/code website: fix it", "/code C:\\work\\site: fix it", "/code website
#: tonight: fix it". The folder is everything up to the first colon that is
#: followed by a space (a drive's "C:\\" never is); linear.
_CODE = re.compile(r"^(?P<folder>[^\n]{1,400}?)\s*:\s+(?P<words>.+)$", re.DOTALL)


def parse_code(rest: str) -> "tuple[str, str, str] | None":
    """``(folder, words, when)`` from what follows ``/code``, or None."""
    m = _CODE.match(str(rest or "").strip())
    if m is None:
        return None
    folder = m.group("folder").strip()
    when = WHEN_NOW
    low = folder.lower()
    if low.endswith(" tonight"):
        folder, when = folder[: -len(" tonight")].strip(), WHEN_TONIGHT
    elif low.startswith("tonight "):
        folder, when = folder[len("tonight "):].strip(), WHEN_TONIGHT
    words = m.group("words").strip()
    if not folder or not words:
        return None
    return folder, words, when


__all__ = [
    "ASKED_FROM_PREFIX", "Asking", "CARD_TTL_S", "CODE_FOLDER_KIND", "CODE_START_KIND", "Card", "EVENT",
    "NOT_NOW", "START", "START_OPTIONS", "STEP_ENDED", "STEP_STARTED", "WHEN_NOW", "WHEN_TONIGHT",
    "after_pick", "ask_which", "asking", "card_words", "clean_folder_name", "deliver", "door_open",
    "ending_words", "find_folder", "from_phone", "from_words", "none_words", "parse_code", "problem",
    "propose", "publish", "report_of", "reset_asking", "set_asking", "start_from_card", "started_words",
    "used_folders", "uses_words", "where_words",
]
