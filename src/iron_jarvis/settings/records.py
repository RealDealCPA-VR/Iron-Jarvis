"""Records changed from chat, each with a ledger row and an Undo (calm UI
redesign S5, AUDIT §6.3).

Settings are keys in the one schema; RECORDS are the user's own things —
schedules, saved workflows, notification channels, connected apps. Chat could
create a schedule or a workflow but not change or remove one, and it could not
touch a channel or an app at all. These tools close that gap the same way the
settings tools do (settings.tools):

* every tool is REVERSIBLE: ``capture_undo`` snapshots the record as it was
  into a ``record_restore`` journal row the registry writes for this very
  call, and ``POST /undo/{id}`` puts it back (``restore``) — a created record
  is removed, a changed one restored, a removed one recreated;
* the result carries a ``record_change`` the chat lanes turn into the reply's
  "Changed … [Undo]" card (``chat_turn.config_cards_from_result``), with a
  ``change_id`` minted at capture so the card can find its Undo
  (``GET /config/changes/{change_id}``);
* a credential is NEVER a tool argument: ``channel_connect`` and
  ``app_connect`` write the record with a reference to a vault name and hand
  back ``secret_requests`` the page renders as secure cards (the same door as
  ``config_secret``).

Every change that can widen what reaches the machine or start work unattended
is ASK tier (the ordinary approval card): updating or removing a schedule or a
workflow, scheduling a workflow, turning a channel's two-way listening on or
off, connecting a channel or an app. ``schedule_create`` and
``workflow_create`` keep their own (allow) tiers and only gain the Undo.

The rest of AUDIT §6.3, same shape: webhooks, sentinels, goals and reflex
rules (update / delete, ``reflex_create``) are ask tier; ``grant_revoke`` is
allow tier — taking back an "always allow" only narrows what runs unasked —
and its Undo puts the grant back. ``webhook_add`` / ``sentinel_add`` /
``goal_add`` keep their tiers and gain the Undo through
:func:`capture_create_undo`. v1.434.0 (chat-first plan, Wave 2 group E,
B1.3): asked for in chat ("make a schedule that runs every weekday at 8am",
"add a sentinel that watches irs.gov"), the four create tools are offered by
the STREAM lane and ASK FIRST there (:data:`ASK_FIRST_CREATE_TOOLS`, an
"ask" override), and "run the triage workflow now" offers ``workflow_run``.
A record whose id is minted BY the store
(a goal, a reflex rule) is captured "pending" and named after the write
(:func:`bind_created`) — the registry journals the descriptor only after a
successful execute, the same post-execute fill ``finalize_post_hash`` does.

``config_undo`` is "undo that" from chat: it reverses the newest change the
Settings ledger lists (or the one named) through THE code ``POST
/undo/{id}`` runs (``routes.undo.perform_undo``).

Snapshots and restores are BLOCKING (SQLite, config.toml, MCP spawns); the
tools run them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from ..core.ids import new_id
from ..tools.base import Reversibility, SpendCheck, Tool, ToolContext, ToolResult

log = logging.getLogger("ironjarvis.settings")

RECORD_RESTORE = "record_restore"

#: The record tools added here, with their default permission. Kept in one
#: table so the permission defaults, the low-trust deny set and the roster
#: pins can all read the same names.
RECORD_TOOL_TIERS: dict[str, str] = {
    "schedule_update": "ask",
    "schedule_delete": "ask",
    "workflow_update": "ask",
    "workflow_delete": "ask",
    "workflow_schedule": "ask",
    "channel_toggle": "ask",
    "channel_connect": "ask",
    "app_connect": "ask",
    # AUDIT §6.3, the rest: each starts or reshapes unattended work (a
    # trigger, a watcher, a goal the autonomy loop reads, a signal→action
    # rule) — ask on the card, with an Undo.
    "webhook_update": "ask",
    "webhook_delete": "ask",
    "sentinel_update": "ask",
    "sentinel_delete": "ask",
    "goal_update": "ask",
    "goal_delete": "ask",
    "reflex_create": "ask",
    "reflex_update": "ask",
    "reflex_delete": "ask",
    # Taking back an "always allow" only narrows what runs without a card.
    "grant_revoke": "allow",
    # v1.435.0 (chat-first plan Wave 3 group H, settings/records_apps): an
    # app's switch, disconnect and import; a destination's removal and
    # alerts. Each asks on the card, with an Undo.
    "app_switch": "ask",
    "app_disconnect": "ask",
    "app_import": "ask",
    "channel_remove": "ask",
    "channel_alerts": "ask",
}
#: The create tools that gained an Undo (their own tiers are unchanged).
RECORD_CREATE_TOOLS = frozenset({"schedule_create", "workflow_create", "webhook_add", "sentinel_add", "goal_add"})
#: Tools whose successful result carries a ``record_change`` (the record
#: tools plus the create tools that gained an Undo).
RECORD_CARD_TOOLS = frozenset({*RECORD_TOOL_TIERS, *RECORD_CREATE_TOOLS})

#: "undo that" from chat (:class:`ConfigUndoTool`): ask — an undo is a change.
#: Kept OUT of ``RECORD_TOOL_TIERS``: it carries no card and is not itself on
#: the configuration ledger.
CONFIG_UNDO_TIERS: dict[str, str] = {"config_undo": "ask"}

_CHANGE_ID: ContextVar[str] = ContextVar("ij_record_change_id", default="")
#: A create captured before its store minted the id (see :func:`bind_created`).
_PENDING: ContextVar[dict[str, Any] | None] = ContextVar("ij_record_pending", default=None)
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _.-]{0,79}$")
_CHANNEL_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")

#: v1.434.0 (chat-first plan, Wave 2 group E, B1.3): the RECURRING time
#: shapes people use for a schedule — "every weekday", "each morning",
#: "every 2 hours", "on Mondays" — beside the ones it always had.
_DAY_NAMES = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_RECURRING = (
    r"(?:every|each)\s+(?:other\s+)?(?:day|morning|afternoon|evening|night|week|weekday|weekend"
    r"|work\s*day|business\s+day|month|quarter|year|hour|minute|" + _DAY_NAMES + r")s?"
    r"|every\s+(?:\d+|two|three|four|five|six|ten|twelve|fifteen|thirty|few|couple\s+of)\s+"
    r"(?:minutes?|hours?|days?|weeks?|months?)"
    r"|on\s+(?:weekdays|weekends|" + r"|".join(f"{d}s" for d in _DAY_NAMES.split("|")) + r")"
    r"|nightly|weekly|daily"
)
#: "schedule a call with the client" is a meeting on a calendar, never one of
#: Iron Jarvis's schedules.
#: LINEAR on purpose (review): the determiner carries its OWN trailing space.
#: The first cut wrote ``\s+(?:a|…)?\s*``, two whitespace runs side by side,
#: and "schedule" followed by a pasted run of blank lines was QUADRATIC —
#: 5 s at 12,000 characters, on the event loop.
_NOT_OUR_SCHEDULE = (
    r"(?!\s+(?:(?:a|an|the|my|our|your|some)\s+)?(?:call|meeting|appointment|zoom|interview|visit"
    r"|demo|lunch|dinner|coffee|chat\s+with|session\s+with|review\s+with)\b)"
)
_SCHEDULE_NOUN = re.compile(
    r"\b(?:schedules?" + _NOT_OUR_SCHEDULE + r"|scheduled|cron)\b", re.IGNORECASE
)
_RECURRING_WORDS = re.compile(r"\b(?:" + _RECURRING + r")\b", re.IGNORECASE)
_WORKFLOW_WORDS = re.compile(r"\bworkflows?\b", re.IGNORECASE)
#: A notification channel — never the UPDATE channel ("switch updates to the
#: stable channel" armed the channel tools, v1.434.0 B1.1), a TV channel or
#: "cut me some slack".
_CHANNEL_WORDS = re.compile(
    r"\b(?:notification channels?|two.?way|telegram|discord"
    r"|(?:e-?mail|alert|notification|text|sms)\s+destinations?"
    r"|(?<!\bsome\s)slack(?!\s+(?:time|off|space|in\s+the\s+schedule)\b)"
    r"|(?<!\bupdate\s)(?<!\bupdates\s)(?<!\bstable\s)(?<!\bfresh\s)(?<!\brelease\s)(?<!\btv\s)"
    r"(?<!\bnews\s)(?<!\bsports\s)(?<!\bcable\s)(?<!\byoutube\s)channels?(?!\s*\d))\b",
    re.IGNORECASE,
)
# v1.353.0 integrate (G41): the three drives join the list, so "find my tax
# files in Google Drive" arms app_connect -- and a Drive that is not set up
# answers with the "Connect Google Drive in Settings" button (daemon/doors.
# failure_door_for), not silence. A question ("is my Google Drive
# connected?") still drops app_connect (is_question).
_APP_WORDS = re.compile(
    r"\b(?:connect (?:my|to|the)|notion|github|sentry|stripe|brave search|google maps"
    r"|google drive|onedrive|dropbox|connectors?)\b",
    re.IGNORECASE,
)
#: v1.434.0 review: the code a coding chat WRITES is never one of the app's
#: records — "create a webhook handler in my Express app", "set up a watcher
#: in my React code", "add a goal section to my business plan", "make a
#: goal-setting worksheet". Each record noun is read with these guards.
_CODE_THING = (
    r"(?!\s+(?:handler|route|listener|receiver|function|controller|class|test|signature|parser"
    r"|payload|middleware|callback)s?\b)"
)
_CODE_PLACE = (
    r"(?!\s+(?:in|for|on|inside|into)\s+(?:(?:my|the|this|our|a|an|your)\s+)?(?:[\w'.-]+\s+)?"
    r"(?:code|codebase|components?|repo|repository|scripts?|functions?|class(?:es)?|programs?"
    r"|modules?|frontend|backend|react|vue|angular|svelte|express|django|flask)\b)"
)
_NOT_A_GOAL_RECORD = (
    r"(?!-)(?!\s+(?:section|setting|sheet|worksheet|template|tracker|page|slide|list|column|row"
    r"|statement|chart|table|field|box|card|board|line|keeper|kick|post|area|difference)s?\b)"
)
_WEBHOOK_WORDS = re.compile(r"\bweb ?hooks?\b" + _CODE_THING + _CODE_PLACE, re.IGNORECASE)
_SENTINEL_WORDS = re.compile(
    r"\b(?:sentinels?|watchers?)\b" + _CODE_THING + _CODE_PLACE + r"|\bstop\s+watching\b",
    re.IGNORECASE,
)
_GOAL_WORDS = re.compile(r"\bgoals?\b" + _NOT_A_GOAL_RECORD, re.IGNORECASE)
#: v1.434.0 (B1.4): "whenever a new email comes in, start an intake session",
#: "every time a client email arrives, …" — a signal that ARRIVES is a reflex
#: rule's trigger. The thing that arrives must be something Iron Jarvis can
#: hear about (an email, a file, a webhook…), so "when the package arrives"
#: arms nothing.
_ARRIVING_THING = (
    r"(?:e-?mails?|mail|messages?|texts?|sms|files?|documents?|pdfs?|invoices?|receipts?"
    r"|uploads?|attachments?|webhooks?|payments?|orders?|tickets?|issues?|pull\s+requests?"
    r"|alerts?|forms?|submissions?|leads?|events?|requests?)"
)
_REFLEX_TRIGGER = (
    r"\b(?:when|whenever|every\s+time|each\s+time|any\s*time|as\s+soon\s+as)\b[^.?!\n]{0,60}?\b"
    + _ARRIVING_THING
    + r"\b[^.?!\n]{0,40}?\b(?:arrives?|arriving|comes?\s+in|gets?\s+(?:received|added|uploaded|sent)"
    r"|is\s+(?:received|added|uploaded|created|sent|saved|dropped)|lands?|shows?\s+up|appears?"
    r"|fires?|happens|is\s+posted|gets?\s+posted|hits?)\b"
)
_REFLEX_WORDS = re.compile(
    # v1.434.0 (B1.4): "rule" is the app's rule only when something is DONE
    # to it ("turn off the nightly rule", "make a rule:") — never "use the
    # Daylight Savings rules for the dates", "the rules of the game".
    r"\breflex(?:es)?\b"
    r"|\b(?:turn\s+(?:on|off)|switch\s+(?:on|off)|delete|remove|pause|resume|make|create|add|set\s*up"
    r"|disable|enable|edit|change|update|stop|rename|drop|cancel)\b[^.?!\n]{0,40}?\brules?\b(?!\s+of\b)"
    r"|\brules?\s*:"
    r"|\bwhen\b[^.?!\n]{1,80}\bhappens\b|" + _REFLEX_TRIGGER,
    re.IGNORECASE,
)
_GRANT_WORDS = re.compile(r"\b(?:grants?|always[ -]allow(?:s|ed|ing)?)\b", re.IGNORECASE)
#: "undo that" (the brief's must-work example) and its close variants.
_UNDO_WORDS = re.compile(
    r"\bundo (?:that|it|this|the last (?:change|setting)|my last change)\b", re.IGNORECASE
)

# ---------------------------------------------------- creating and running
#: v1.434.0 (chat-first plan, Wave 2 group E, B1.3): asking to CREATE one of
#: these offered only its edit and delete tools — creating needed a hand-off
#: to an agent, and a watcher could not be made at all. The create tool is
#: now offered too, in the STREAM lane only and ASKING FIRST: its own tier is
#: allow (an agent may make one unasked), so the lane writes an "ask"
#: override for it (``ASK_FIRST_CREATE_TOOLS``, the folder-rule mechanism)
#: and the user sees the ordinary card. The POST lane offers none of them.
ASK_FIRST_CREATE_TOOLS = frozenset({"schedule_create", "goal_add", "webhook_add", "sentinel_add"})
_CREATE_VERB = (
    r"\b(?:make|create|add|set\s*up|setup|start|build|put\s+(?:in|together)|register|set|give\s+me"
    r"|i\s+(?:want|need|would\s+like|'d\s+like))"
    r"\s+(?:me\s+|us\s+)?(?:a|an|new|another|one|one\s+more|some)\s+(?:new\s+)?"
    r"(?:[\w'-]+\s+){0,2}?"
)
_CREATE_SCHEDULE = re.compile(_CREATE_VERB + r"(?:schedules?|scheduled\s+(?:task|job|run)s?|cron(?:\s+job)?)\b", re.IGNORECASE)
_CREATE_WEBHOOK = re.compile(
    _CREATE_VERB + r"web\s?hooks?\b" + _CODE_THING + _CODE_PLACE, re.IGNORECASE
)
_CREATE_SENTINEL = re.compile(
    _CREATE_VERB + r"(?:sentinels?|watchers?|folder\s+watchers?)\b" + _CODE_THING + _CODE_PLACE
    + r"|\b(?:watch|keep\s+an\s+eye\s+on|monitor)\s+(?:my|the|this|that|for)\b[^.?!\n]{0,30}?"
    r"\b(?:folder|directory|drive|downloads|desktop|documents\s+folder|path)\b",
    re.IGNORECASE,
)
_CREATE_GOAL = re.compile(_CREATE_VERB + r"goals?\b" + _NOT_A_GOAL_RECORD, re.IGNORECASE)
#: "Schedule a weekly backup of my settings" — the verb, at the head of a
#: request.
_SCHEDULE_VERB = re.compile(
    r"(?:\A\s*|[.!?;:]\s*|\b(?:please|can\s+you|could\s+you|and|then|also)\s+)schedule\s+"
    r"(?:a|an|my|the|some|it|this|that|daily|weekly|nightly|monthly)\b" + _NOT_OUR_SCHEDULE,
    re.IGNORECASE,
)
#: A recurring TIME ("every weekday", "each morning at 7", "every 2 hours",
#: "on Mondays") — not the adjectives ("the nightly brief"), which name an
#: existing schedule as often as they ask for a new one.
_EVERY_WORDS = re.compile(
    r"\b(?:(?:every|each)\s+(?:other\s+)?(?:day|morning|afternoon|evening|night|week|weekday"
    r"|weekend|work\s*day|business\s+day|month|quarter|year|hour|minute|" + _DAY_NAMES + r")s?"
    r"|every\s+(?:\d+|two|three|four|five|six|ten|twelve|fifteen|thirty|few|couple\s+of)\s+"
    r"(?:minutes?|hours?|days?|weeks?|months?)"
    r"|on\s+(?:weekdays|weekends|" + r"|".join(f"{d}s" for d in _DAY_NAMES.split("|")) + r"))\b",
    re.IGNORECASE,
)
#: A request that CHANGES an existing schedule ("pause the nightly brief",
#: "move it to 7am") is never a new one.
_EDIT_LEAD = re.compile(
    r"\A\s*(?:please\s+)?(?:pause|stop|delete|remove|cancel|disable|turn\s+off|move|rename"
    r"|change|edit|update|resume|unpause|reschedule)\b",
    re.IGNORECASE,
)
#: A sentence about oneself ("I go to the gym every morning", "my standup is
#: every Monday") is not a request for a schedule, unless it says it wants
#: one.
_SELF_STATEMENT = re.compile(
    r"\A\s*(?:(?:ok(?:ay)?|so|but|hey|also|well)\b[,\s]+)?"
    r"(?:i|i'm|we|we're|my|our|the|it|it's|this|that|there|he|she|they|his|her|their)\b"
    # v1.434.0 review: "every morning I check the bank feeds" — the time
    # first, then the person.
    r"|\A\s*(?:" + _RECURRING + r")\b[^.?!\n]{0,24}?\b(?:i|we)\b",
    re.IGNORECASE,
)
_WANTS = re.compile(
    # Review: "I need TO pay rent every month" is the person's own chore —
    # "I need a digest every morning" / "I need you to…" still ask.
    r"\b(?:please|remind\s+me|can\s+you|could\s+you|would\s+you"
    r"|i\s+(?:want|need(?!\s+to\b)|would\s+like|'d\s+like)"
    r"|should|(?<!\bi\s)(?<!\bwe\s)needs?\s+to|make\s+sure|let'?s)\b",
    re.IGNORECASE,
)
#: v1.434.0 review: a calendar EVENT booked on a recurring time ("set up a
#: meeting every Tuesday", "book a dentist appointment every six months",
#: "set my alarm every weekday at 6") is the user's calendar, never one of
#: Iron Jarvis's schedules — unless they ask to be reminded.
_BOOKS_AN_EVENT = re.compile(
    r"\b(?:set\s+up|book|arrange|plan|have|hold|host|organi[sz]e|set|make|add|put|attend)\s+"
    r"(?:(?:a|an|the|my|our|your|some)\s+)?(?:[\w'-]+\s+)?"
    r"(?:meeting|appointment|call|zoom|interview|alarm|lunch|dinner|coffee|class|lesson|standup"
    r"|stand-up|haircut|check-?up|reservation)s?\b",
    re.IGNORECASE,
)
_REMIND = re.compile(r"\bremind(?:er)?s?\b", re.IGNORECASE)
#: B1.4: "run the triage workflow now" — a run verb on a workflow (or on a
#: SAVED workflow's own name, read by the stream lane off the loop).
_RUN_VERB = r"(?:run|start|kick\s+off|trigger|launch|execute|fire\s+off|re-?run)"
_RUN_WORKFLOW = re.compile(
    # Linear (review): the determiner takes its own space — see
    # _NOT_OUR_SCHEDULE.
    r"\b" + _RUN_VERB + r"\s+(?:(?:the|my|our|that|this)\s+)?(?:[\w'-]+\s+){0,3}?workflows?\b",
    re.IGNORECASE,
)
_RUN_VERB_RX = re.compile(r"\b" + _RUN_VERB + r"\b", re.IGNORECASE)


def wants_workflow_names(text: str) -> bool:
    """Is it worth reading the saved workflows' names for *text*? A run verb
    and no question: the stream lane then reads them OFF THE LOOP (one SQLite
    read) and hands them to :func:`record_tools_for`."""
    t = text or ""
    return bool(_RUN_VERB_RX.search(t)) and not is_question(t)


def saved_workflow_names(platform: Any) -> list[str]:
    """The saved workflows' names (BLOCKING — call through a thread). Never
    raises: an unreadable store names nothing."""
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..workflows.models import WorkflowRecord

        with session_scope(platform.engine) as db:
            return [str(n or "") for n in db.exec(select(WorkflowRecord.name).limit(500))]
    except Exception:  # noqa: BLE001
        return []


_RUN_AT = re.compile(r"\b" + _RUN_VERB + r"\s+", re.IGNORECASE)
_RUN_DET_AT = re.compile(r"(?:the|my|our)\s+", re.IGNORECASE)
_NAME_GOES_ON = re.compile(r"[\w-]")


@lru_cache(maxsize=8)
def _workflow_name_table(names: "tuple[str, ...]") -> "tuple[frozenset[str], tuple[tuple[int, frozenset[str]], ...]]":
    """The saved names (3+ characters), lowercased, grouped by length, and
    their first characters."""
    by_len: dict[int, set[str]] = {}
    for n in names:
        s = (n or "").strip().lower()
        if len(s) >= 3:
            by_len.setdefault(len(s), set()).add(s)
    firsts = frozenset(s[0] for group in by_len.values() for s in group)
    return firsts, tuple(sorted((k, frozenset(v)) for k, v in by_len.items()))


def _runs_a_named_workflow(t: str, names: "list[str] | tuple[str, ...]") -> bool:
    """A run verb, then (after "the" / "my" / "our") a saved workflow's
    whole name. v1.434.0 review: the first cut compiled and ran one regex PER
    NAME over the whole message — 1.9 s for 500 workflows and a 100 KB pasted
    text, and one alternation of every name still held the interpreter lock
    for a third of a second in ONE call. Now each run verb is found once and
    the words after it are LOOKED UP by length: the same answer as the
    per-name patterns (pinned), at a cost that grows with the run verbs."""
    firsts, table = _workflow_name_table(tuple(str(n or "") for n in names or ()))
    if not table:
        return False
    low = (t or "").lower()
    for m in _RUN_AT.finditer(low):
        starts = [m.end()]
        det = _RUN_DET_AT.match(low, m.end())
        if det is not None:
            starts.append(det.end())
        for s in starts:
            if low[s:s + 1] not in firsts:
                continue
            for length, group in table:
                end = s + length
                if low[s:end] in group and _NAME_GOES_ON.match(low, end) is None:
                    return True
    return False


#: A QUESTION IS NOT CONSENT TO A WRITER (v1.344.0, "Jarvis looks after
#: itself"). Every rule below keys on a NOUN, so "what went wrong with my
#: schedule?" and "why did the nightly run fail" armed ``schedule_update`` /
#: ``schedule_delete`` / ``workflow_schedule`` — three writers shown to the
#: model off a sentence that asked for a reader (measured; the defect this
#: guard closes). The shape is read off the message the way
#: ``tools/autoselect._imperative`` reads it, inverted: a message whose HEAD
#: is a question word or an inverted auxiliary over a non-"you" subject ("is
#: my", "did the", "should I"), or whose last clause ENDS in "?" with no
#: polite request in it ("can you …?", "could you …?", "please …?"), is a
#: question. ``when`` is deliberately NOT a head word — "when a client email
#: happens, start an intake session" is the reflex-rule shape, an instruction
#: — and "can you" / "could you" / "would you" / "will you" are requests, not
#: questions, which is why the auxiliary branch requires a subject other than
#: ``you``. Discourse openers ("ok,", "so", "hey") are consumed first so a
#: real question behind one is still seen.
#:
#: Under a question only the READERS survive (:data:`_RECORD_READERS`);
#: ``grant_revoke`` (allow tier — it would RUN with no card) and
#: ``config_undo`` go with the writers. HONEST COST: "move my schedule to
#: 7am?" — an imperative typed with a question mark — arms nothing; the user
#: drops the mark or says "can you".
_QUESTION_HEAD = re.compile(
    r"\A\s*(?:(?:ok(?:ay)?|so|but|hey|hi|hello|alright|right|now|actually|well|also)"
    r"\b[,\s]+){0,2}"
    r"(?:(?:what|why|how|where|which|who|whose|whom|whether)\b"
    r"|(?:is|are|was|were|do|does|did|has|have|had|can|could|should|would|"
    r"will|might|may|shall)\s+"
    r"(?:i|we|it|this|that|these|those|there|my|our|the|a|an|any|anyone|"
    r"someone|something|he|she|they|everything|jarvis)\b)",
    re.IGNORECASE,
)
_POLITE_REQUEST = re.compile(
    r"\b(?:can|could|would|will)\s+you\b|\bplease\b|\bkindly\b|\bgo\s+ahead\b",
    re.IGNORECASE,
)
#: The record tools a question may still arm: they only READ.
_RECORD_READERS = frozenset({"goal_list"})


def is_question(text: str) -> bool:
    """Is this message asking rather than telling? See :data:`_QUESTION_HEAD`."""
    t = (text or "").strip()
    if not t:
        return False
    if _QUESTION_HEAD.search(t):
        return True
    if t.endswith("?"):
        last = re.split(r"[.!?\n]", t[:-1])[-1]
        return _POLITE_REQUEST.search(last) is None
    return False


def record_tools_for(text: str, *, workflow_names: "list[str] | tuple[str, ...]" = ()) -> list[str]:
    """The record tools a chat message plausibly asks for, by the record it
    names (chat-only arming, like ``settings.tools.wants_settings``). The
    stream lane arms them VISIBLE, never granted: an ask-tier one renders
    the card, ``grant_revoke`` (allow) and ``goal_list`` (read) run; a create
    tool (:data:`ASK_FIRST_CREATE_TOOLS`) gets an "ask" override from the
    lane, so it cards too.

    ``workflow_names`` (v1.434.0, B1.4) — the saved workflows' names, read by
    the stream lane only when :func:`wants_workflow_names` says a run verb is
    there: "run triage now" names no workflow word.

    A QUESTION-shaped message ("what went wrong with my schedule?") keeps only
    the readers — see :data:`_QUESTION_HEAD`."""
    t = text or ""
    out = _record_tools_named(t, workflow_names=workflow_names)
    # v1.435.0 (Wave 3 group H): an app's switch / disconnect / import, a
    # destination's removal / alerts (settings/records_apps).
    from .records_apps import apps_tools_for

    out += [n for n in apps_tools_for(t) if n not in out]
    if is_question(t):
        return [n for n in out if n in _RECORD_READERS]
    return out


def _schedule_named(t: str) -> bool:
    """A schedule by its noun, or a RECURRING time in a request ("every
    weekday at 8am send me a digest") — never a sentence about oneself ("I
    go to the gym every morning")."""
    if _SCHEDULE_NOUN.search(t):
        return True
    if not _RECURRING_WORDS.search(t):
        return False
    if _BOOKS_AN_EVENT.search(t) and not _REMIND.search(t):
        return False
    return not _SELF_STATEMENT.search(t) or bool(_WANTS.search(t))


def _record_tools_named(t: str, *, workflow_names: "list[str] | tuple[str, ...]" = ()) -> list[str]:
    out: list[str] = []
    schedule = _schedule_named(t)
    if schedule:
        out += ["schedule_update", "schedule_delete", "workflow_schedule"]
        # B1.3: a NEW schedule — "make a schedule that…", or a recurring time
        # in a request ("remind me every morning at 7…", "every weekday at
        # 8am send me a digest").
        if (
            _CREATE_SCHEDULE.search(t)
            or _SCHEDULE_VERB.search(t)
            or (_EVERY_WORDS.search(t) and not _SCHEDULE_NOUN.search(t) and not _EDIT_LEAD.search(t))
        ):
            out.append("schedule_create")
    if _WORKFLOW_WORDS.search(t):
        out += ["workflow_update", "workflow_delete", "workflow_schedule"]
    # B1.4: running a saved workflow — never inside a rule ("when a webhook
    # fires, run the triage workflow" is the rule's action).
    if (_RUN_WORKFLOW.search(t) or _runs_a_named_workflow(t, workflow_names)) and not re.search(
        _REFLEX_TRIGGER + r"|\b(?:when|whenever|every\s+time|each\s+time)\b[^.?!\n]{1,80}\b(?:fires?|happens)\b",
        t,
        re.IGNORECASE,
    ):
        out.append("workflow_run")
    if _CHANNEL_WORDS.search(t):
        out += ["channel_toggle", "channel_connect"]
    if _APP_WORDS.search(t) or _names_an_app(t):
        out += ["app_connect"]
    if _WEBHOOK_WORDS.search(t):
        out += ["webhook_update", "webhook_delete"]
        if _CREATE_WEBHOOK.search(t):
            out.append("webhook_add")
    sentinel_new = bool(_CREATE_SENTINEL.search(t))
    if _SENTINEL_WORDS.search(t) or sentinel_new:
        out += ["sentinel_update", "sentinel_delete"]
        if sentinel_new:
            out.append("sentinel_add")
    if _GOAL_WORDS.search(t):
        out += ["goal_list", "goal_update", "goal_delete"]
        if _CREATE_GOAL.search(t):
            out.append("goal_add")
    if _REFLEX_WORDS.search(t):
        out += ["reflex_create", "reflex_update", "reflex_delete"]
    if _GRANT_WORDS.search(t):
        out += ["grant_revoke"]
    if _UNDO_WORDS.search(t):
        out += ["config_undo"]
    return list(dict.fromkeys(out))


def _names_an_app(t: str) -> bool:
    """v1.354.0 (G39): does the message name a catalog app by the words a
    person uses for it ("check my Gmail", "open my inbox", "my Dropbox")?
    The ONE matcher (``connectors.app_names``, built from the catalog's own
    names), not a second word list: ``_APP_WORDS`` keeps the connect verbs
    and the names it always had. Never raises."""
    try:
        from ..connectors.app_names import apps_named

        return bool(apps_named(t))
    except Exception:  # noqa: BLE001 - a matcher fault arms nothing new
        return False


class RecordError(ValueError):
    pass


# ------------------------------------------------------------- schedules


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def snap_schedule(platform: Any, name: str) -> dict[str, Any] | None:
    rec = platform.scheduler.get(name)
    if rec is None:
        return None
    return {
        "name": rec.name,
        "trigger_type": rec.trigger_type,
        "cron": rec.cron or "",
        "run_at": _iso(rec.run_at),
        "interval_seconds": rec.interval_seconds,
        "kind": rec.kind,
        "payload": rec.decoded_payload(),
        "enabled": bool(rec.enabled),
    }


def _trigger_kwargs(prior: dict[str, Any]) -> dict[str, Any]:
    t = prior.get("trigger_type") or "cron"
    if t == "date":
        return {"run_at": prior.get("run_at")}
    if t == "interval":
        return {"interval_seconds": int(prior.get("interval_seconds") or 0)}
    return {"cron": prior.get("cron") or ""}


def restore_schedule(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    sch = platform.scheduler
    if prior is None:
        sch.remove(name)
        return f"undo: removed the schedule '{name}'"
    trig = _trigger_kwargs(prior)
    if sch.get(name) is None:
        sch.add_task(
            name,
            trig.get("cron"),
            run_at=trig.get("run_at"),
            interval_seconds=trig.get("interval_seconds"),
            kind=prior.get("kind") or "workflow",
            payload=prior.get("payload") or {},
            enabled=bool(prior.get("enabled", True)),
        )
        return f"undo: recreated the schedule '{name}'"
    sch.update_task(
        name,
        **trig,
        kind=prior.get("kind") or "workflow",
        payload=prior.get("payload") or {},
        enabled=bool(prior.get("enabled", True)),
    )
    return f"undo: restored the schedule '{name}'"


def _describe_schedule(s: dict[str, Any] | None) -> str | None:
    if s is None:
        return None
    t = s.get("trigger_type")
    when = (
        f"once at {s.get('run_at')}"
        if t == "date"
        else f"every {s.get('interval_seconds')} s"
        if t == "interval"
        else f"cron {s.get('cron')}"
    )
    return f"{when}{'' if s.get('enabled', True) else ' (paused)'}"


# ------------------------------------------------------------- workflows


def _wf_store(platform: Any) -> Any:
    from ..workflows.store import WorkflowStore

    return WorkflowStore(platform.engine)


def snap_workflow(platform: Any, name: str) -> dict[str, Any] | None:
    store = _wf_store(platform)
    rec = store.get(name)
    if rec is None:
        return None
    try:
        steps = json.loads(rec.steps_json or "[]")
    except ValueError:
        steps = []
    return {
        "name": rec.name,
        "description": rec.description or "",
        "steps": steps,
        "project_id": store.get_project_id(rec.name) or "",
    }


def restore_workflow(platform: Any, name: str, prior: dict[str, Any] | None, now: str = "") -> str:
    store = _wf_store(platform)
    now = now or name
    if prior is None:
        store.remove(now)
        return f"undo: removed the workflow '{now}'"
    if now != name and store.get(now) is not None and store.get(name) is None:
        store.patch(now, new_name=name)
    store.save(name, prior.get("steps") or [], prior.get("description") or "", project_id=prior.get("project_id") or "")
    return f"undo: restored the workflow '{name}'"


def _describe_workflow(w: dict[str, Any] | None) -> str | None:
    if w is None:
        return None
    n = len(w.get("steps") or [])
    return f"{n} step{'s' if n != 1 else ''}" + (f" — {w['description']}" if w.get("description") else "")


# -------------------------------------------------------------- channels


def _d(platform: Any) -> Any:
    w = getattr(platform, "config_writer", None)
    return getattr(w, "d", None)


def snap_channel(platform: Any, name: str) -> dict[str, Any] | None:
    cfg = ((platform.config.comm or {}).get("channels") or {}).get(name)
    return copy.deepcopy(cfg) if isinstance(cfg, dict) else None


def apply_channel(platform: Any, name: str, cfg: dict[str, Any] | None) -> None:
    """Write one channel's stored config (``None`` removes it) to config.toml
    and to the live notifier. Vault secrets are NOT touched: an undo that
    brings a channel back finds its token where it left it."""
    from ..core.config import persist_config_values

    comm = dict(platform.config.comm or {})
    channels = dict(comm.get("channels") or {})
    old = channels.get(name)
    if cfg is None:
        channels.pop(name, None)
    else:
        channels[name] = cfg
    comm["channels"] = channels
    platform.config.comm = comm
    persist_config_values(platform.config.home, {"comm": comm})
    notifier = platform.notifier
    notifier.remove_channel(name)
    if cfg is not None:
        from ..comm import CHANNEL_TYPES, httpx_get, httpx_post

        ctype = cfg.get("type")
        if ctype in CHANNEL_TYPES:
            notifier.add_channel(
                name,
                CHANNEL_TYPES[ctype](cfg, http_post=httpx_post, http_get=httpx_get, secret_resolver=platform.secrets.get),
            )
    if "slack" in ((old or {}).get("type"), (cfg or {}).get("type")):
        d = _d(platform)
        rearm = getattr(d, "_live_rearm", None) or {}
        loop, fn = rearm.get("loop"), rearm.get("slack")
        if loop is not None and fn is not None:
            try:
                loop.call_soon_threadsafe(fn)
            except Exception:  # noqa: BLE001 — a re-arm hiccup never fails the write
                pass


def restore_channel(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    apply_channel(platform, name, copy.deepcopy(prior) if prior is not None else None)
    return f"undo: {'removed' if prior is None else 'restored'} the channel '{name}'"


#: v1.435.0 review (Wave 3 group H, item 7): ``channel_toggle`` snapshotted
#: the WHOLE destination (``snap_channel``) — a Discord or Slack destination's
#: webhook address, its credential, landed in the undo journal in plain text —
#: and its Undo put the whole destination back, re-adding one the user had
#: removed since. The switch changes two flags, so its record keeps only
#: those (no credential, nothing to encrypt) and its Undo puts only those
#: back, on the destination as it is then. Older journal rows keep the
#: ``channel`` kind and restore as before.
CHANNEL_MODE_KEYS = ("inbound_enabled", "chat_enabled")


def _mode_flags(mode: str) -> dict[str, bool]:
    return {"inbound_enabled": mode in ("two_way", "chat"), "chat_enabled": mode == "chat"}


def snap_channel_mode(platform: Any, name: str) -> dict[str, Any] | None:
    cfg = snap_channel(platform, name)
    if cfg is None:
        return None
    return {k: cfg[k] for k in CHANNEL_MODE_KEYS if k in cfg}


def restore_channel_mode(platform: Any, name: str, prior: dict[str, Any] | None, now: str = "") -> str:
    if not isinstance(prior, dict):
        raise RecordError("this change kept nothing to bring back")
    cfg = snap_channel(platform, name)
    if cfg is None:
        raise RecordError(f"The channel {name!r} was removed since, so its two-way setting can't be put back.")
    if now in ("two_way", "chat", "off") and any(
        bool(cfg.get(k)) != v for k, v in _mode_flags(now).items()
    ):
        raise RecordError(
            f"The channel {name!r}'s two-way setting was changed since, so this Undo would undo that too: "
            "change it on the Notifications page."
        )
    for k in CHANNEL_MODE_KEYS:
        if k in prior:
            cfg[k] = prior[k]
        else:
            cfg.pop(k, None)
    apply_channel(platform, name, cfg)
    return f"undo: restored the channel '{name}'"


def _describe_channel(c: dict[str, Any] | None) -> str | None:
    if c is None:
        return None
    bits = [str(c.get("type") or "channel")]
    if c.get("chat_enabled"):
        bits.append("two-way chat on")
    elif c.get("inbound_enabled"):
        bits.append("two-way on")
    else:
        bits.append("send only")
    return ", ".join(bits)


# ------------------------------------------------------------------ apps


def snap_app(platform: Any, connector_id: str) -> dict[str, Any] | None:
    for s in list(getattr(platform.config, "mcp_servers", None) or []):
        if isinstance(s, dict) and s.get("name") == connector_id:
            return copy.deepcopy(s)
    return None


def load_app(platform: Any, cfg: dict[str, Any]) -> int:
    """(Re)load one MCP pack's tools live. Returns how many registered.

    v1.355.0 (G44): through the one door's loader (mcp/pack_door.load_config):
    unregister AND close the old client (v1.342.0, lane D — a bare unregister
    left the pack's stdio child running until restart), then the loader boot
    uses, its failure kept on the load record."""
    from ..mcp.pack_door import load_config

    return load_config(platform, cfg)


def apply_app(platform: Any, connector_id: str, cfg: dict[str, Any] | None, *, load: bool = True) -> int:
    """Put an app's pack row in place (``cfg``) or take it away (``None``) —
    ``app_connect`` and its Undo. v1.355.0 (G44): through THE ONE DOOR
    (mcp/pack_door): a removal is ``remove_pack`` — the row, every vault row,
    the manifest (a later connect is a first load), the live tools AND their
    client, the live client and the load record — exactly what the Tools
    page's Delete and the Directory's Disconnect do. A row is put in place
    with ``install_config`` (it already names its vault rows: the secure card
    stores the token after this, and an Undo restores a prior row whose rows
    must stay), then loaded by :func:`load_app` unless ``load`` is False
    ("save only": the live tools are left as they are)."""
    from ..mcp.pack_door import PackDoorError, forget_legacy_refs, install_config, remove_pack

    if cfg is None:
        removed = snap_app(platform, connector_id)
        try:
            remove_pack(platform, connector_id)
        except PackDoorError:
            from ..mcp.tools import unload_pack_tools

            # Nothing saved under that name: still close anything live (v1.342.0).
            unload_pack_tools(platform.registry, connector_id)
        forget_legacy_refs(platform, connector_id, removed)
        return 0
    install_config(platform, cfg, load=False)
    return load_app(platform, cfg) if load else 0


#: v1.358.0: the card's state for an app whose pack loaded no tools — it did
#: not start, and the next start will not change that by itself.
NOT_STARTED_STATE = "saved, did not start"
#: How much of a load failure's own words reach the model.
_WHY_MAX = 240


def _why_not_started(connector_id: str, *, has_needs: bool = False) -> str:
    """": <the pack's own reason>." for an app whose pack loaded no tools —
    the load record's plain words (``mcp.tools.load_status``, masked when
    recorded), with where to see it. Never a secret, an env var or a vault
    name. "." plus where to look when nothing was recorded. Never raises."""
    from ..connectors.app_names import APPS_PLACE

    where = f" Its card in {APPS_PLACE} shows why."
    try:
        from ..mcp.tools import STATE_OK, load_status

        rec = dict(load_status(connector_id) or {})
    except Exception:  # noqa: BLE001
        rec = {}
    if rec.get("state") == STATE_OK:
        return ": it started but offered no tools." + where
    reason = " ".join(str(rec.get("reason") or "").split()).rstrip(". ")
    if not reason:
        # A setup the app needs is said right after (`_needs`); else where.
        return "." if has_needs else "." + where
    if len(reason) > _WHY_MAX:
        reason = reason[: _WHY_MAX - 1].rstrip() + "…"
    fix = " ".join(str(rec.get("fix") or "").split()).rstrip(". ")
    # A fix that points at the Tools page's own row ("its last output below")
    # means nothing in chat; the card says where instead.
    tail = f" To fix it: {fix}." if fix and "below" not in fix.lower() else where
    return f": {reason}." + tail


def restore_app(platform: Any, connector_id: str, prior: dict[str, Any] | None) -> str:
    apply_app(platform, connector_id, copy.deepcopy(prior) if prior is not None else None)
    return f"undo: {'disconnected' if prior is None else 'restored'} the app '{connector_id}'"


def app_waiting_for(platform: Any, connector_id: str) -> list[str]:
    """The LABELS of the app's required secrets the vault still has no value
    for (v1.351.0, G15) — Box takes two; the first card's save must not try
    the pack (it cannot start yet) nor call it broken. ``[]`` when nothing is
    missing, the app is unknown, or the vault cannot be read (the load is
    tried, as before). Never raises."""
    try:
        from ..connectors.catalog import get_connector
        from ..connectors.service import _secret_name

        conn = get_connector(connector_id)
        if conn is None:
            return []
        saved = snap_app(platform, connector_id) or {}
        missing: list[str] = []
        for f in conn.fields:
            if f.kind != "secret" or f.optional:
                continue
            # v1.355.0 (G44): the row the card writes (`_secret_name`) OR the
            # row a pack saved before v1.355.0 still names (`conn_<id>_<field>`).
            names = [_secret_name(conn.id, f.name), _field_ref(saved, conn, f.name)]
            if not any(n and platform.secrets.get(n) for n in names):
                missing.append(f.label)
        # v1.433.0 (Wave 1, A4): an argument the card collects is missing
        # while the saved row names its vault row and the vault holds nothing.
        arg_refs = dict(saved.get("arg_secrets") or {})
        for f in card_arg_fields(conn):
            if f.optional:
                continue
            ref = str(arg_refs.get(str(card_arg_index(conn, f))) or "")
            if ref and not platform.secrets.get(ref):
                missing.append(f.label)
        return missing
    except Exception:  # noqa: BLE001 — unknown = try the load
        return []


def _field_ref(cfg: dict[str, Any], conn: Any, field_name: str) -> str:
    """The vault NAME a saved app row reads ``field_name`` from: its
    ``env_secrets`` entry, or — for a remote card's token — ``auth.secret``.
    ``""`` when the row does not name one."""
    auth = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else None
    if auth is not None and getattr(conn, "auth_field", "") == field_name and auth.get("secret"):
        return str(auth["secret"])
    return str(dict(cfg.get("env_secrets") or {}).get(field_name) or "")


def card_arg_fields(conn: Any) -> list[Any]:
    """The ARGUMENT fields the secure card collects instead of chat (v1.433.0,
    Wave 1 A4): an ``arg`` field whose name says it carries a credential
    (``mcp.secrets.is_secret_key`` — Postgres's ``connection``) and whose
    template argument is exactly its placeholder, so the vault can hold the
    whole argument (``arg_secrets``, put back at launch by
    ``mcp.secrets.resolve_args``). Never raises."""
    try:
        from ..mcp.secrets import is_secret_key

        args = list(getattr(conn, "args", None) or [])
        return [
            f for f in list(getattr(conn, "fields", None) or [])
            if f.kind == "arg" and is_secret_key(f.name) and f"<{f.name}>" in args
        ]
    except Exception:  # noqa: BLE001
        return []


def card_arg_index(conn: Any, f: Any) -> int:
    """The index of ``f``'s placeholder in ``conn``'s argument template."""
    return list(conn.args).index(f"<{f.name}>")


def card_fields(conn: Any) -> list[Any]:
    """Every field a secure card collects for ``conn``: its secret fields,
    then its credential-carrying argument fields, in catalog order — the one
    list ``app_connect`` names its cards from and ``credentials._resolve_app``
    resolves ``app.<id>[__FIELD]`` against."""
    cards = {id(f) for f in card_arg_fields(conn)}
    return [f for f in list(getattr(conn, "fields", None) or []) if f.kind == "secret" or id(f) in cards]


#: v1.433.0 (Wave 1, A4): a credential-carrying argument (a Postgres
#: connection string WITH its password) handed to app_connect.
CONNECTION_NOT_IN_CHAT = (
    "{label} for {name} holds a password, so it never goes through chat. Call app_connect for "
    "{id} again without `{field}` in values: a secure card asks the user for it."
)


def _card_arg_with_password(conn: Any, values: Any) -> Any:
    """The first card argument field whose value in ``values`` carries a
    credential (``mcp.secrets.split_args`` would hide it), or None."""
    if not isinstance(values, dict):
        return None
    from ..mcp.secrets import split_args

    for f in card_arg_fields(conn):
        val = str(values.get(f.name, "") or "").strip()
        if val and split_args([val])[1]:
            return f
    return None


def adopt_card_secrets(platform: Any, connector_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """A pack saved before v1.355.0 names its token ``conn_<id>_<field>``; the
    secure card (``app.<id>``) now writes the row the one door uses
    (``connectors.service._secret_name``). When the card's row HAS a value and
    the saved row still points elsewhere, point the row at the card's — so the
    token the user just pasted is the token the pack launches with (it was a
    silent no-op otherwise). Persisted, not loaded. Returns the row in force;
    never raises."""
    try:
        from ..connectors.catalog import get_connector
        from ..connectors.service import _secret_name

        conn = get_connector(connector_id)
        if conn is None:
            return cfg
        new = copy.deepcopy(cfg)
        changed = False
        for f in conn.fields:
            if f.kind != "secret":
                continue
            canonical = _secret_name(conn.id, f.name)
            current = _field_ref(new, conn, f.name)
            if not current or current == canonical or not platform.secrets.get(canonical):
                continue
            auth = new.get("auth") if isinstance(new.get("auth"), dict) else None
            if auth is not None and auth.get("secret") == current:
                auth["secret"] = canonical
            else:
                env_secrets = dict(new.get("env_secrets") or {})
                env_secrets[f.name] = canonical
                new["env_secrets"] = env_secrets
            changed = True
        # v1.433.0 (Wave 1, A4): an argument the card just stored (a Postgres
        # connection string) — a row connected from the Directory names its
        # own vault row (``mcp.<id>.arg.<i>``) or holds a plain argument; point
        # it at the card's row so the pack launches with what was pasted.
        from ..detections.redact import MASK

        for f in card_arg_fields(conn):
            canonical = _secret_name(conn.id, f.name)
            if not platform.secrets.get(canonical):
                continue
            idx = card_arg_index(conn, f)
            arg_refs = dict(new.get("arg_secrets") or {})
            args = list(new.get("args") or [])
            if arg_refs.get(str(idx)) == canonical or idx >= len(args):
                continue
            args[idx] = MASK
            arg_refs[str(idx)] = canonical
            new["args"] = args
            new["arg_secrets"] = arg_refs
            changed = True
        if not changed:
            return cfg
        from ..mcp.pack_door import install_config

        install_config(platform, new, load=False)
        return new
    except Exception:  # noqa: BLE001 — the row as it was still loads
        log.warning("app %s: could not adopt the card's token row", connector_id, exc_info=True)
        return cfg


def reload_app_after_secret(platform: Any, connector_id: str) -> dict[str, Any] | None:
    """A credential card saved an app's token: load the pack now if its config
    is waiting for it (best-effort; never raises).

    v1.351.0 (G15): answers what happened, so the card can say it —
    ``{"tools_loaded": N}`` and, when N is 0, ``"reason"`` (the load record's
    plain words, else what is known). ``None`` when the app has no saved pack
    to load (a key saved before ``app_connect`` ran)."""
    try:
        cfg = snap_app(platform, connector_id)
        if cfg is None:
            return None
        # v1.355.0 (G44): an older row is pointed at the row the card wrote.
        cfg = adopt_card_secrets(platform, connector_id, cfg)
        loaded = int(load_app(platform, cfg) or 0)
    except Exception:  # noqa: BLE001
        log.warning("app %s: reload after its credential failed", connector_id, exc_info=True)
        return {"tools_loaded": 0, "reason": "its tools could not be loaded"}
    out: dict[str, Any] = {"tools_loaded": loaded}
    if not loaded:
        reason = ""
        try:
            from ..detections.redact import mask
            from ..mcp.tools import load_status

            status = load_status(connector_id) or {}
            reason = mask(str(status.get("reason") or status.get("last_error") or ""))[:300]
        except Exception:  # noqa: BLE001
            reason = ""
        out["reason"] = reason or "it started but listed no tools"
    return out


def _dt(v: Any) -> Any:
    """An ISO string from a snapshot back to a datetime (None stays None)."""
    from datetime import datetime

    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


# -------------------------------------------------------------- webhooks


def _wh_row(db: Any, slug: str) -> Any:
    from sqlmodel import select

    from ..webhooks.models import WebhookRecord

    return db.exec(select(WebhookRecord).where(WebhookRecord.slug == slug)).first()


def snap_webhook(platform: Any, slug: str) -> dict[str, Any] | None:
    from ..core.db import session_scope

    with session_scope(platform.engine) as db:
        rec = _wh_row(db, slug)
        if rec is None:
            return None
        try:
            types = json.loads(rec.event_types_json or "[]")
        except ValueError:
            types = []
        return {
            "id": rec.id,
            "slug": rec.slug,
            "direction": rec.direction or "inbound",
            "target_url": rec.target_url or "",
            "secret_name": rec.secret_name or "",
            "event_types": types if isinstance(types, list) else [],
            "enabled": bool(rec.enabled),
            "created_at": _iso(rec.created_at),
        }


def _inbound_handler(platform: Any, slug: str) -> Any:
    """The live handler an inbound webhook answers with — the same body the
    ``webhook_add`` tool and ``POST /webhooks`` install: publish
    ``webhook.received`` and fire any bound reflex rule."""

    async def handler(body: Any, _slug: str = slug) -> dict[str, Any]:
        await platform.event_bus.publish("webhook.received", {"slug": _slug, "body": body})
        fired: list[Any] = []
        router = getattr(platform, "reflex_router", None)
        if router is not None:
            try:
                fired = await router.on_webhook(_slug, body)
            except Exception:  # noqa: BLE001 — never break the ack
                pass
        from ..reflex.router import summarize_fires

        return {"ok": True, **summarize_fires(fired)}

    return handler


def _sync_inbound(platform: Any, slug: str, enabled: bool, secret_name: str) -> None:
    """Make the live inbound handler match the row. The store has no "pause"
    verb (``unregister`` deletes the row), and dispatch answers from the live
    handler — so a paused inbound webhook is a row with ``enabled=False`` and
    no handler, exactly what a restart's ``rehydrate`` leaves for it."""
    inb = platform.inbound_webhooks
    if enabled:
        secret = platform.secrets.get(secret_name) if secret_name else None
        inb.register(slug, _inbound_handler(platform, slug), secret=secret, secret_name=secret_name or None)
    else:
        getattr(inb, "_handlers", {}).pop(slug, None)


def _write_webhook(platform: Any, s: dict[str, Any]) -> None:
    """Write one webhook row exactly as ``s`` describes it (id and creation
    time kept when the row is recreated)."""
    from ..core.db import session_scope
    from ..webhooks.models import WebhookRecord

    with session_scope(platform.engine) as db:
        row = _wh_row(db, s["slug"])
        if row is None:
            row = WebhookRecord(id=s.get("id") or new_id("whk"), slug=s["slug"])
            created = _dt(s.get("created_at"))
            if created is not None:
                row.created_at = created
        row.direction = s.get("direction") or "inbound"
        row.target_url = s.get("target_url") or ""
        row.secret_name = s.get("secret_name") or ""
        row.event_types_json = json.dumps(list(s.get("event_types") or []))
        row.enabled = bool(s.get("enabled", True))
        db.add(row)
        db.commit()


def remove_webhook(platform: Any, slug: str, direction: str) -> None:
    """``DELETE /webhooks/{slug}``'s removal: the store of its direction drops
    the row and the live handler / secret cache; the vault is left alone."""
    if direction == "outbound":
        platform.outbound_webhooks.unregister(slug)
    else:
        platform.inbound_webhooks.unregister(slug)


def apply_webhook_update(
    platform: Any, before: dict[str, Any], *, url: str | None, types: list[str] | None, enabled: bool | None
) -> None:
    """Change an outbound target / event types through the store's own
    ``register`` (the SSRF check and the empty-types refusal ``POST
    /webhooks`` runs), then the enabled flag on the row + the live handler."""
    slug = before["slug"]
    if url is not None or types is not None:
        sn = before.get("secret_name") or ""
        secret = platform.secrets.get(sn) if sn else None
        platform.outbound_webhooks.register(
            slug,
            url if url is not None else before.get("target_url") or "",
            list(types) if types is not None else list(before.get("event_types") or []),
            secret=secret,
            secret_name=sn or None,
        )
    if enabled is not None:
        now = snap_webhook(platform, slug) or before
        _write_webhook(platform, {**now, "enabled": enabled})
        if now.get("direction") != "outbound":
            _sync_inbound(platform, slug, enabled, now.get("secret_name") or "")


def restore_webhook(platform: Any, slug: str, prior: dict[str, Any] | None) -> str:
    cur = snap_webhook(platform, slug)
    if prior is None:
        if cur is not None:
            remove_webhook(platform, slug, cur["direction"])
        else:
            getattr(platform.inbound_webhooks, "_handlers", {}).pop(slug, None)
        return f"undo: removed the webhook '{slug}'"
    _write_webhook(platform, prior)
    if prior.get("direction") == "outbound":
        # A direction flipped by a re-register leaves no inbound handler behind.
        getattr(platform.inbound_webhooks, "_handlers", {}).pop(slug, None)
    else:
        _sync_inbound(platform, slug, bool(prior.get("enabled", True)), prior.get("secret_name") or "")
    return f"undo: {'recreated' if cur is None else 'restored'} the webhook '{slug}'"


def _describe_webhook(w: dict[str, Any] | None) -> str | None:
    if w is None:
        return None
    if w.get("direction") == "outbound":
        types = ", ".join(str(t) for t in w.get("event_types") or []) or "no events"
        text = f"sends {types} to {w.get('target_url')}"
    else:
        text = "receives calls"
    return text + ("" if w.get("enabled", True) else " (paused)")


# ------------------------------------------------------------- sentinels


def snap_sentinel(platform: Any, name: str) -> dict[str, Any] | None:
    rec = platform.sentinels.get(name)
    if rec is None:
        return None
    return {
        "id": rec.id,
        "name": rec.name,
        "kind": rec.kind,
        "config": rec.decoded_config(),
        "task": rec.task or "",
        "agent_type": rec.agent_type or "builder",
        "risk": rec.risk or "low",
        "enabled": bool(rec.enabled),
        "created_at": _iso(rec.created_at),
        "last_checked_at": _iso(rec.last_checked_at),
        "last_state": rec.decoded_state(),
        "last_error": rec.last_error,
    }


def restore_sentinel(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    """A created sentinel is removed; a changed one gets its settings back
    (its watch memory is left as it is — what it has seen since is real); a
    removed one is recreated WITH its memory, so it does not re-fire for
    changes it had already reported."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..sentinels.models import SentinelRecord

    svc = platform.sentinels
    if prior is None:
        svc.remove(name)
        return f"undo: removed the sentinel '{name}'"
    with session_scope(svc.engine) as db:
        row = db.exec(select(SentinelRecord).where(SentinelRecord.name == name)).first()
        recreated = row is None
        if row is None:
            row = SentinelRecord(id=prior.get("id") or new_id("sentinel"), name=name)
            created = _dt(prior.get("created_at"))
            if created is not None:
                row.created_at = created
            row.last_checked_at = _dt(prior.get("last_checked_at"))
            row.last_state_json = json.dumps(prior.get("last_state") or {}, default=str)
            row.last_error = prior.get("last_error")
        row.kind = prior.get("kind") or "file"
        row.config_json = json.dumps(prior.get("config") or {}, default=str)
        row.task = prior.get("task") or ""
        row.agent_type = prior.get("agent_type") or "builder"
        row.risk = prior.get("risk") or "low"
        row.enabled = bool(prior.get("enabled", True))
        db.add(row)
        db.commit()
    return f"undo: {'recreated' if recreated else 'restored'} the sentinel '{name}'"


def _describe_sentinel(s: dict[str, Any] | None) -> str | None:
    if s is None:
        return None
    where = (s.get("config") or {}).get("path") or s.get("kind")
    return f"watches {where}" + ("" if s.get("enabled", True) else " (paused)")


# ----------------------------------------------------------------- goals

#: The goal fields a change (and its Undo) touches; the budget COUNTERS are
#: the autonomy loop's running totals and are only put back on a recreate.
_GOAL_FIELDS = ("text", "source", "category", "priority", "autonomy_level", "status", "action_budget", "spend_budget")
GOAL_STATUSES = ("active", "paused", "done", "abandoned")


def snap_goal(platform: Any, goal_id: str) -> dict[str, Any] | None:
    rec = platform.intent.get_goal(goal_id)
    if rec is None:
        return None
    out = {f: getattr(rec, f) for f in _GOAL_FIELDS}
    out.update(
        id=rec.id,
        actions_taken=int(rec.actions_taken or 0),
        tokens_spent=int(rec.tokens_spent or 0),
        last_acted_at=_iso(rec.last_acted_at),
        created_at=_iso(rec.created_at),
    )
    return out


def resolve_goal(platform: Any, ref: str) -> str:
    """A goal's id from its id or its exact text (case-insensitive, unique).
    Blocking. "" when nothing (or more than one goal) matches."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if platform.intent.get_goal(ref) is not None:
        return ref
    hits = [g.id for g in platform.intent.list_goals() if (g.text or "").strip().casefold() == ref.casefold()]
    return hits[0] if len(hits) == 1 else ""


def restore_goal(platform: Any, goal_id: str, prior: dict[str, Any] | None) -> str:
    """A created goal is removed — the goal engine has no delete, so the row
    goes directly; if the autonomy loop already proposed work for it, the
    goal is ABANDONED instead (its proposals keep their goal). A changed goal
    gets its fields back."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..motivation.models import GoalRecord, ProposalRecord

    with session_scope(platform.engine) as db:
        row = db.get(GoalRecord, goal_id)
        if prior is None:
            if row is None:
                return "undo: the goal was already gone"
            text = row.text
            if db.exec(select(ProposalRecord).where(ProposalRecord.goal_id == goal_id)).first() is not None:
                row.status = "abandoned"
                db.add(row)
                db.commit()
                return f"undo: abandoned the goal '{text}' (it already had suggestions)"
            db.delete(row)
            db.commit()
            return f"undo: removed the goal '{text}'"
        recreated = row is None
        if row is None:
            row = GoalRecord(id=goal_id)
            row.actions_taken = int(prior.get("actions_taken") or 0)
            row.tokens_spent = int(prior.get("tokens_spent") or 0)
            row.last_acted_at = _dt(prior.get("last_acted_at"))
            created = _dt(prior.get("created_at"))
            if created is not None:
                row.created_at = created
        for f in _GOAL_FIELDS:
            if f in prior:
                setattr(row, f, prior[f])
        db.add(row)
        db.commit()
        return f"undo: {'recreated' if recreated else 'restored'} the goal '{prior.get('text')}'"


def _describe_goal(g: dict[str, Any] | None) -> str | None:
    if g is None:
        return None
    return f"{g.get('status')}, {g.get('autonomy_level')}, priority {g.get('priority')}"


# ----------------------------------------------------------- reflex rules

#: The fields a rule change (and its Undo) touches; the fire bookkeeping is
#: only put back on a recreate.
_REFLEX_FIELDS = ("name", "source", "match", "action", "target", "task_template", "project_id", "enabled")


def validate_reflex(source: str, match: str, action: str, target: str) -> str:
    """``POST /reflex/rules``'s checks, word for word (routes/reflex.py
    ``add_reflex_rule``) — "" when the rule is acceptable."""
    from ..reflex.models import REFLEX_ACTIONS, REFLEX_SOURCES

    if source not in REFLEX_SOURCES:
        return f"source must be one of {REFLEX_SOURCES}"
    if action not in REFLEX_ACTIONS:
        return f"action must be one of {REFLEX_ACTIONS}"
    if source == "webhook" and not match.strip():
        return "a webhook rule needs a webhook slug in `match`"
    if action in ("workflow", "remote_agent") and not target.strip():
        return f"a '{action}' action needs a `target` (the workflow/agent name)"
    return ""


def snap_reflex(platform: Any, rule_id: str) -> dict[str, Any] | None:
    r = platform.reflex.get(rule_id)
    if r is None:
        return None
    out = {f: getattr(r, f) for f in _REFLEX_FIELDS}
    out.update(
        id=r.id,
        created_at=_iso(r.created_at),
        last_fired_at=_iso(r.last_fired_at),
        fire_count=int(r.fire_count or 0),
        last_error=r.last_error,
        last_result=r.last_result,
    )
    return out


def resolve_reflex(platform: Any, ref: str) -> str:
    """A rule's id from its id or its exact name (unique). Blocking."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if platform.reflex.get(ref) is not None:
        return ref
    hits = [r.id for r in platform.reflex.list() if (r.name or "").strip().casefold() == ref.casefold()]
    return hits[0] if len(hits) == 1 else ""


def restore_reflex(platform: Any, rule_id: str, prior: dict[str, Any] | None) -> str:
    from ..core.db import session_scope
    from ..reflex.models import ReflexRule

    store = platform.reflex
    if prior is None:
        store.remove(rule_id)
        return f"undo: removed the rule '{rule_id}'"
    with session_scope(store.engine) as db:
        row = db.get(ReflexRule, rule_id)
        recreated = row is None
        if row is None:
            row = ReflexRule(id=rule_id)
            created = _dt(prior.get("created_at"))
            if created is not None:
                row.created_at = created
            row.last_fired_at = _dt(prior.get("last_fired_at"))
            row.fire_count = int(prior.get("fire_count") or 0)
            row.last_error = prior.get("last_error")
            row.last_result = prior.get("last_result")
        for f in _REFLEX_FIELDS:
            if f in prior:
                setattr(row, f, prior[f])
        db.add(row)
        db.commit()
    return f"undo: {'recreated' if recreated else 'restored'} the rule '{prior.get('name') or rule_id}'"


def _describe_reflex(r: dict[str, Any] | None) -> str | None:
    if r is None:
        return None
    on = f"{r.get('source')} '{r.get('match')}'" if r.get("match") else f"any {r.get('source')} signal"
    act = f"{r.get('action')} {r.get('target')}".strip()
    return f"on {on} → {act}" + ("" if r.get("enabled", True) else " (off)")


# -------------------------------------------------------- standing grants


def snap_grant(platform: Any, grant_id: str) -> dict[str, Any] | None:
    rec = platform.grants.get(grant_id)
    if rec is None:
        return None
    return {
        "id": rec.id,
        "scope_kind": rec.scope_kind,
        "scope_id": rec.scope_id,
        "tool": rec.tool,
        "args_hash": rec.args_hash,
        "label": rec.label,
        "created_by": rec.created_by,
        "created_at": _iso(rec.created_at),
        "expires_at": _iso(rec.expires_at),
        "revoked_at": _iso(rec.revoked_at),
        "uses": int(rec.uses or 0),
        "last_used_at": _iso(rec.last_used_at),
    }


def resolve_grant(platform: Any, ref: str) -> tuple[str, str]:
    """``(grant_id, problem)`` from a grant id or a tool name. A tool with
    several live grants is a problem that lists them, so the next call can
    name one. Blocking."""
    ref = (ref or "").strip()
    if not ref:
        return "", "Say which always-allow to take back: its id or its tool."
    store = platform.grants
    if store.get(ref) is not None:
        return ref, ""
    live = [g for g in store.list() if g.tool == ref]
    if len(live) == 1:
        return live[0].id, ""
    if not live:
        return "", f"There is no always-allow for {ref!r}."
    rows = "; ".join(f"{g.id} — {g.label} ({g.scope_kind})" for g in live[:10])
    return "", f"{len(live)} always-allows match {ref!r}; name one by id: {rows}"


def restore_grant(platform: Any, grant_id: str, prior: dict[str, Any] | None) -> str:
    """Put a revoked grant back as it was (un-revoke): the row's
    ``revoked_at`` returns to its prior value and the store's in-memory live
    view is re-read, so the next identical call is covered again."""
    from ..core.db import session_scope
    from ..core.grants import StandingGrantRecord

    store = platform.grants
    if prior is None:
        store.revoke(grant_id)
        return f"undo: took back the always-allow '{grant_id}'"
    with session_scope(store.engine) as db:
        row = db.get(StandingGrantRecord, grant_id)
        if row is None:
            row = StandingGrantRecord(id=grant_id)
            for f in ("scope_kind", "scope_id", "tool", "args_hash", "label", "created_by"):
                setattr(row, f, prior.get(f) or "")
            row.created_at = _dt(prior.get("created_at")) or row.created_at
            row.expires_at = _dt(prior.get("expires_at"))
            row.uses = int(prior.get("uses") or 0)
            row.last_used_at = _dt(prior.get("last_used_at"))
        row.revoked_at = _dt(prior.get("revoked_at"))
        db.add(row)
        db.commit()
    store.refresh()
    return f"undo: restored the always-allow '{prior.get('label') or grant_id}'"


# ---------------------------------------------------------------- dispatch

_SNAP = {
    "schedule": snap_schedule,
    "workflow": snap_workflow,
    "channel": snap_channel,
    "channel_mode": snap_channel_mode,
    "app": snap_app,
    "webhook": snap_webhook,
    "sentinel": snap_sentinel,
    "goal": snap_goal,
    "reflex": snap_reflex,
    "grant": snap_grant,
}


def _apps_snap(kind: str) -> Any:
    def _snap(platform: Any, name: str) -> dict[str, Any] | None:
        from .records_apps import SNAP

        return SNAP[kind](platform, name)

    return _snap


#: v1.435.0 (Wave 3 group H): a removed app, a removed destination, a
#: destination's alerts (settings/records_apps).
_APPS_KINDS = ("app_removed", "destination_removed", "destination_alerts", "app_switched")
_SNAP.update({k: _apps_snap(k) for k in _APPS_KINDS})


def restore(platform: Any, desc: dict[str, Any]) -> str:
    """``POST /undo/{id}`` for a ``record_restore`` row. Blocking."""
    record = str(desc.get("record") or "")
    name = str(desc.get("name") or "")
    prior = desc.get("prior")
    if not name:
        raise RecordError("this change has no record name")
    if record == "schedule":
        return restore_schedule(platform, name, prior)
    if record == "workflow":
        return restore_workflow(platform, name, prior, str(desc.get("now") or ""))
    if record == "channel":
        return restore_channel(platform, name, prior)
    if record == "channel_mode":
        return restore_channel_mode(platform, name, prior, str(desc.get("now") or ""))
    if record == "app":
        return restore_app(platform, name, prior)
    if record == "webhook":
        return restore_webhook(platform, name, prior)
    if record == "sentinel":
        return restore_sentinel(platform, name, prior)
    if record == "goal":
        return restore_goal(platform, name, prior)
    if record == "reflex":
        return restore_reflex(platform, name, prior)
    if record == "grant":
        return restore_grant(platform, name, prior)
    if record in _APPS_KINDS:
        from .records_apps import restore_kind

        return restore_kind(platform, record, name, prior, str(desc.get("now") or ""))
    raise RecordError(f"unknown record kind {record!r}")


# ------------------------------------------------------------------- tools


class _RecordTool(Tool):
    """A REVERSIBLE tool over one record: snapshot before, restore on Undo."""

    RECORD = ""
    reversibility = Reversibility.REVERSIBLE

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("name") or "").strip()

    def _now(self, args: dict[str, Any]) -> str:
        return ""

    def _resolve(self, args: dict[str, Any]) -> str:
        """The record's KEY for the snapshot. BLOCKING — a tool whose
        argument may name the record by its words (a goal's text, a rule's
        name) reads the store here; run through ``asyncio.to_thread``."""
        return self._target(args)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _CHANGE_ID.set("")  # never a previous call's id on this card
        _PENDING.set(None)
        try:
            name = await asyncio.to_thread(self._resolve, args)
        except Exception:  # noqa: BLE001 — no key, no undo row
            return None
        if not name:
            return None
        try:
            prior = await asyncio.to_thread(_SNAP[self.RECORD], self.platform, name)
        except Exception:  # noqa: BLE001 — no snapshot, no undo row
            return None
        change_id = new_id("rec")
        _CHANGE_ID.set(change_id)
        desc = {"record": self.RECORD, "name": name, "prior": prior, "change_id": change_id}
        now = self._now(args)
        if now:
            desc["now"] = now
        return {"kind": RECORD_RESTORE, "pre_inline": json.dumps(desc, default=str)}

    def _card(self, title: str, label: str, old: Any, new: Any, key: str) -> dict[str, Any]:
        return {"title": title, "key": key, "label": label, "old": old, "new": new, "change_id": _CHANGE_ID.get()}


# --- schedules


class ScheduleUpdateTool(_RecordTool):
    name = "schedule_update"
    RECORD = "schedule"
    description = (
        "Change an existing scheduled task: its timing (exactly one of `cron`, `run_at`, "
        "`interval_seconds`), its `payload`, or pause/resume it with `enabled`. The user "
        "approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "cron": {"type": "string"},
            "run_at": {"type": "string"},
            "interval_seconds": {"type": "integer", "minimum": 1},
            "payload": {"type": "object"},
            "enabled": {"type": "boolean"},
        },
        "required": ["name"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..scheduling import knobs as _knobs

        name = self._target(args)
        before = await asyncio.to_thread(snap_schedule, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no schedule called {name!r}.")
        payload = args.get("payload")
        if isinstance(payload, dict) and payload.get("script") not in (None, {}):
            return ToolResult(ok=False, error=_knobs.SCRIPT_FROM_AGENT_REFUSAL)
        try:
            if isinstance(payload, dict):
                await asyncio.to_thread(
                    _knobs.validate_knobs,
                    payload,
                    name=name,
                    kind=before["kind"],
                    scheduler=self.platform.scheduler,
                    skills=getattr(self.platform, "skills", None),
                    from_user=False,
                )
            await asyncio.to_thread(
                self.platform.scheduler.update_task,
                name,
                cron=args.get("cron"),
                run_at=args.get("run_at"),
                interval_seconds=args.get("interval_seconds"),
                payload=payload if isinstance(payload, dict) else None,
                enabled=args.get("enabled") if isinstance(args.get("enabled"), bool) else None,
            )
        except _knobs.KnobError as exc:
            return ToolResult(ok=False, error=exc.detail)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        after = await asyncio.to_thread(snap_schedule, self.platform, name)
        old, new = _describe_schedule(before), _describe_schedule(after)
        return ToolResult(
            ok=True,
            output=f"Changed the schedule '{name}': {old} → {new}.",
            data={"record_change": self._card("Schedule changed", f"Schedule “{name}”", old, new, f"schedule.{name}")},
        )


class ScheduleDeleteTool(_RecordTool):
    name = "schedule_delete"
    RECORD = "schedule"
    description = "Remove a scheduled task. The user approves on a card; Undo brings it back as it was."
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        before = await asyncio.to_thread(snap_schedule, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no schedule called {name!r}.")
        await asyncio.to_thread(self.platform.scheduler.remove, name)
        return ToolResult(
            ok=True,
            output=f"Removed the schedule '{name}'.",
            data={"record_change": self._card("Schedule removed", f"Schedule “{name}”", _describe_schedule(before), None, f"schedule.{name}")},
        )


# --- workflows


class WorkflowUpdateTool(_RecordTool):
    name = "workflow_update"
    RECORD = "workflow"
    description = (
        "Change a saved workflow: rename it (`new_name`), re-describe it, or replace its "
        "`steps` ({name, agent, task} objects). The user approves on a card; the change has "
        "an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "new_name": {"type": "string"},
            "description": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["name"],
    }

    def _now(self, args: dict[str, Any]) -> str:
        return str(args.get("new_name") or "").strip()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        new_name = self._now(args)
        before = await asyncio.to_thread(snap_workflow, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no workflow called {name!r}.")
        if new_name and not _NAME.match(new_name):
            return ToolResult(ok=False, error="A workflow name starts with a letter and is at most 80 characters.")
        store = _wf_store(self.platform)
        try:
            if new_name and new_name != name:
                await asyncio.to_thread(store.patch, name, new_name=new_name)
            target = new_name or name
            if isinstance(args.get("steps"), list) or isinstance(args.get("description"), str):
                cur = await asyncio.to_thread(snap_workflow, self.platform, target)
                steps = args["steps"] if isinstance(args.get("steps"), list) else (cur or {}).get("steps") or []
                desc = args["description"] if isinstance(args.get("description"), str) else (cur or {}).get("description") or ""
                await asyncio.to_thread(store.save, target, steps, desc)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        after = await asyncio.to_thread(snap_workflow, self.platform, new_name or name)
        old, new = _describe_workflow(before), _describe_workflow(after)
        label = f"Workflow “{name}”" + (f" → “{new_name}”" if new_name and new_name != name else "")
        return ToolResult(
            ok=True,
            output=f"Changed the workflow '{name}'" + (f" (now '{new_name}')" if new_name and new_name != name else "") + ".",
            data={"record_change": self._card("Workflow changed", label, old, new, f"workflow.{new_name or name}")},
        )


class WorkflowDeleteTool(_RecordTool):
    name = "workflow_delete"
    RECORD = "workflow"
    description = "Remove a saved workflow. The user approves on a card; Undo brings it back with its steps."
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        before = await asyncio.to_thread(snap_workflow, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no workflow called {name!r}.")
        await asyncio.to_thread(_wf_store(self.platform).remove, name)
        return ToolResult(
            ok=True,
            output=f"Removed the workflow '{name}'.",
            data={"record_change": self._card("Workflow removed", f"Workflow “{name}”", _describe_workflow(before), None, f"workflow.{name}")},
        )


class WorkflowScheduleTool(_RecordTool):
    name = "workflow_schedule"
    RECORD = "schedule"
    description = (
        "Run a saved workflow on a schedule: give the workflow's name and exactly one of "
        "`cron` (5-field crontab), `run_at` (ISO date-time, once) or `interval_seconds`. "
        "The user approves on a card; Undo removes the schedule."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "workflow": {"type": "string"},
            "schedule_name": {"type": "string", "description": "Optional; defaults to the workflow's name."},
            "cron": {"type": "string"},
            "run_at": {"type": "string"},
            "interval_seconds": {"type": "integer", "minimum": 1},
        },
        "required": ["workflow"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("schedule_name") or args.get("workflow") or "").strip()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        wf = str(args.get("workflow") or "").strip()
        name = self._target(args)
        if await asyncio.to_thread(snap_workflow, self.platform, wf) is None:
            return ToolResult(ok=False, error=f"There is no workflow called {wf!r}.")
        try:
            await asyncio.to_thread(
                self.platform.scheduler.add_task,
                name,
                args.get("cron"),
                run_at=args.get("run_at"),
                interval_seconds=args.get("interval_seconds"),
                kind="workflow",
                payload={"workflow": wf},
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        after = await asyncio.to_thread(snap_schedule, self.platform, name)
        return ToolResult(
            ok=True,
            output=f"Scheduled the workflow '{wf}' ({_describe_schedule(after)}).",
            data={"record_change": self._card("Workflow scheduled", f"Schedule “{name}”", None, _describe_schedule(after), f"schedule.{name}")},
        )


# --- channels


class ChannelToggleTool(_RecordTool):
    name = "channel_toggle"
    RECORD = "channel_mode"
    description = (
        "Turn a notification channel's two-way listening on or off: `mode` 'two_way' (people "
        "on the allowlist can message Iron Jarvis), 'chat' (they can chat with it), or 'off' "
        "(send only). The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "mode": {"type": "string", "enum": ["two_way", "chat", "off"]}},
        "required": ["name", "mode"],
    }

    def _now(self, args: dict[str, Any]) -> str:
        """The mode this change sets, so its Undo can tell a change since."""
        return str(args.get("mode") or "")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        mode = str(args.get("mode") or "")
        before = await asyncio.to_thread(snap_channel, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no channel called {name!r}.")
        if mode not in ("two_way", "chat", "off"):
            return ToolResult(ok=False, error="mode must be two_way, chat or off")
        cfg = copy.deepcopy(before)
        cfg["inbound_enabled"] = mode in ("two_way", "chat")
        cfg["chat_enabled"] = mode == "chat"
        if cfg["inbound_enabled"] and not cfg.get("allowed_senders"):
            return ToolResult(
                ok=False,
                error=f"The channel {name!r} has no allowlist yet — add who may message it on the Notifications page first.",
            )
        await asyncio.to_thread(apply_channel, self.platform, name, cfg)
        return ToolResult(
            ok=True,
            output=f"Channel '{name}' is now {_describe_channel(cfg)}.",
            data={"record_change": self._card("Channel changed", f"Channel “{name}”", _describe_channel(before), _describe_channel(cfg), f"channel.{name}")},
        )


#: The non-secret fields each channel type takes from chat; the token comes
#: through the secure card (vault name ``channel_<name>_token``).
#: v1.433.0 (Wave 1, A4): Discord takes NOTHING from chat. Its webhook URL IS
#: its credential (anyone holding it can post as the destination; the
#: Notifications page never hands it back, routes/comm.HIDDEN_VALUES), and it
#: was a plain tool argument, so it reached the model and the tool log. It now
#: comes through the same secure card as a Telegram or Slack token; the card
#: writes it where the Notifications page keeps it (``webhook_url`` in the
#: destination's own settings — settings/credentials.CredentialStore), so the
#: destination works the same whichever door saved it.
_CHANNEL_FIELDS = {
    "telegram": ("chat_id",),
    "slack": ("channel",),
    "discord": (),
    # v1.435.0 (Wave 3 group H): email — the mail server for sending, From
    # and Send-to from chat; the password through the secure card. ``port``
    # (587) and ``username`` (the From address) may be left out.
    "email": ("host", "from_addr", "to_addr"),
}

#: v1.435.0: a password (or any secret-named value) handed to channel_connect.
PASSWORD_NOT_IN_CHAT = (
    "A password never goes through chat. Call channel_connect again without `{field}`: "
    "a secure card asks the user for it."
)
_EMAIL_ADDRESS = re.compile(r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63}){1,8}$")

#: v1.433.0 (Wave 1, A4): the refusal a call carrying a webhook URL gets —
#: before any card, and its log row keeps no URL (``redact_args``).
WEBHOOK_NOT_IN_CHAT = (
    "A webhook URL works like a password, so it never goes through chat. Call channel_connect "
    "again without `webhook_url`: a secure card asks the user for it."
)

#: A Discord or Slack incoming-webhook address anywhere in a value: masked
#: whole in a log row (the path IS the key). Discord's API takes an optional
#: version in the path (``/api/v10/webhooks/…``, the form its own docs show) —
#: matched too (v1.433.0 review).
_WEBHOOK_URL_RX = re.compile(
    r"(?i)https?://[^\s\"'<>]*(?:discord(?:app)?\.com/api(?:/v\d+)?/webhooks|hooks\.slack\.com)/[^\s\"'<>]*"
)


def masked_args(value: Any, *, full: Any = None) -> Any:
    """``value`` (a tool call's arguments) safe for the tool log, the approval
    card and the stream (v1.433.0, Wave 1 A4): every string through
    ``detections.redact.mask`` (``postgresql://me:***@host``,
    ``password=***``, ``Bearer ***``…) and any incoming-webhook address
    replaced whole; a dict key ``full(key)`` answers True for is replaced
    whole (a token field). Never raises; non-strings pass through."""
    from ..detections.redact import MASK, mask

    try:
        if isinstance(value, str):
            return _WEBHOOK_URL_RX.sub(MASK, mask(value))
        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for k, v in value.items():
                if full is not None and v not in (None, "") and full(str(k)):
                    out[k] = MASK
                else:
                    out[k] = masked_args(v, full=full)
            return out
        if isinstance(value, list):
            return [masked_args(v, full=full) for v in value]
    except Exception:  # noqa: BLE001 — a log row never carries what could not be read
        return MASK
    return value


class ChannelConnectTool(_RecordTool):
    name = "channel_connect"
    RECORD = "channel"
    description = (
        "Add a notification channel so Iron Jarvis can message the user: `type` telegram "
        "(needs `chat_id`), slack (needs `channel`, e.g. #general), discord (needs nothing "
        "else) or email (needs `host`, the mail server for sending such as smtp.gmail.com, "
        "`from_addr` and `to_addr`; `port` and `username` are optional). Never ask for a token, "
        "a password or a webhook URL in chat — the user pastes it into the secure card this "
        "shows. The user approves on a card; Undo removes the channel."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string", "enum": sorted(_CHANNEL_FIELDS)},
            "chat_id": {"type": "string"},
            "channel": {"type": "string"},
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "username": {"type": "string"},
            "from_addr": {"type": "string"},
            "to_addr": {"type": "string"},
        },
        "required": ["name", "type"],
    }

    @staticmethod
    def _secret_arg(args: dict[str, Any]) -> str:
        """v1.435.0: the first secret-named argument a call carries (an email
        ``password``), or "" — refused before any card, like a webhook URL."""
        from ..mcp.secrets import is_secret_key

        return next(
            (str(k) for k, v in dict(args or {}).items()
             if k != "name" and is_secret_key(str(k)) and str(v or "").strip()),
            "",
        )

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """v1.433.0 (Wave 1, A4): never a webhook URL or a token in the log,
        the approval card or the stream — even one a model passed anyway."""
        from ..mcp.secrets import is_secret_key

        return masked_args(dict(args or {}), full=is_secret_key)

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """A call carrying a webhook URL is refused by ``execute`` and changes
        nothing, so it is no approval card either (the v1.227.0 rule "an
        Allow that cannot run is a lie"). Everything else keeps the card."""
        if str((args or {}).get("webhook_url") or "").strip() or self._secret_arg(args):
            return SpendCheck(ask=False, cost_note="changes nothing by itself")
        return None

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if str(args.get("webhook_url") or "").strip():
            return ToolResult(ok=False, error=WEBHOOK_NOT_IN_CHAT)
        _secret = self._secret_arg(args)
        if _secret:
            return ToolResult(ok=False, error=PASSWORD_NOT_IN_CHAT.format(field=_secret))
        name = self._target(args)
        ctype = str(args.get("type") or "").strip().lower()
        if not _CHANNEL_NAME.match(name):
            return ToolResult(ok=False, error="A channel name starts with a letter: letters, digits, - and _ only.")
        if ctype not in _CHANNEL_FIELDS:
            return ToolResult(ok=False, error=f"type must be one of {', '.join(sorted(_CHANNEL_FIELDS))}")
        if await asyncio.to_thread(snap_channel, self.platform, name) is not None:
            return ToolResult(ok=False, error=f"A channel called {name!r} already exists.")
        cfg: dict[str, Any] = {"type": ctype}
        for f in _CHANNEL_FIELDS[ctype]:
            v = str(args.get(f) or "").strip()
            if not v:
                return ToolResult(ok=False, error=f"{ctype} needs `{f}`.")
            cfg[f] = v
        if ctype == "email":
            # v1.435.0 (Wave 3 group H): the page's own fields
            # (comm.channels.EmailChannel reads them), checked here.
            for f in ("from_addr", "to_addr"):
                if not _EMAIL_ADDRESS.match(cfg[f]):
                    return ToolResult(ok=False, error=f"`{f}` must be an email address.")
            if args.get("port") not in (None, ""):
                try:
                    port = int(args["port"])
                except (TypeError, ValueError):
                    port = 0
                if not 0 < port < 65536:
                    return ToolResult(ok=False, error="`port` must be a number such as 587.")
                cfg["port"] = port
            cfg["username"] = str(args.get("username") or "").strip() or cfg["from_addr"]
        requests: list[dict[str, Any]] = []
        if ctype in ("telegram", "slack"):
            cfg["token_secret"] = f"channel_{name}_token"
            requests.append(
                {
                    "name": f"channel.{name}",
                    "label": f"{ctype.title()} token for “{name}”",
                    "help": "Telegram: from @BotFather. Slack: the bot token (xoxb-…).",
                    "why": f"to finish connecting {ctype.title()}",
                    "request_id": new_id("seq"),
                }
            )
        elif ctype == "discord":
            # The card writes the URL into this destination's settings, where
            # the Notifications page keeps it (settings/credentials).
            requests.append(
                {
                    "name": f"channel.{name}",
                    "label": f"Discord webhook URL for “{name}”",
                    "help": "In Discord: the channel's settings, then Integrations, then Webhooks, then Copy Webhook URL.",
                    "why": "to finish connecting Discord",
                    "request_id": new_id("seq"),
                }
            )
        elif ctype == "email":
            # v1.435.0: the card writes the password to the vault row the
            # destination reads (`password_secret`; credentials.resolve_secret
            # answers `channel_<name>_token` for `channel.<name>`).
            cfg["password_secret"] = f"channel_{name}_token"
            requests.append(
                {
                    "name": f"channel.{name}",
                    "label": f"Email password for “{name}” ({cfg['username']})",
                    "help": "For Gmail or Outlook, use an app password from your account's security page, not your sign-in password.",
                    "why": "to finish connecting email",
                    "request_id": new_id("seq"),
                }
            )
        await asyncio.to_thread(apply_channel, self.platform, name, cfg)
        what = {"discord": "webhook URL", "email": "password"}.get(ctype, "token")
        waiting = f" — paste its {what} into the secure card to finish" if requests else ""
        return ToolResult(
            ok=True,
            output=f"Added the {ctype} channel '{name}'{waiting}. Never ask for the {what} in chat.",
            data={
                "record_change": self._card("Channel added", f"Channel “{name}”", None, _describe_channel(cfg), f"channel.{name}"),
                "secret_requests": requests,
            },
        )


# --- apps


def _oauth_client_ready(platform: Any, provider: str) -> bool:
    """Could a sign-in for this OAuth app START right now (an OAuth client is
    registered)? The connections registry's own row, the one the Directory
    card reads (v1.353.0, G45). BLOCKING: callers hop off the loop. Unsure is
    True, so nothing extra is said on a guess. Never raises (v1.354.0, G39)."""
    try:
        rows = platform.connections.status()
        for r in rows or []:
            if r.get("provider") == provider:
                return bool(r.get("oauth_client_configured"))
        return True
    except Exception:  # noqa: BLE001 - unsure = say nothing extra
        return True


def _oauth_signed_in(platform: Any, provider: str) -> bool:
    """Is this OAuth app's account signed in? The connections registry's own
    row (the Directory card reads the same ``status()``). BLOCKING (database
    reads): callers hop off the loop. Unsure (no registry, a fault) is False,
    so the call answers as before. Never raises (v1.353.0 re-review)."""
    try:
        rows = platform.connections.status()
        return any(
            r.get("provider") == provider and bool(r.get("connected")) for r in rows or []
        )
    except Exception:  # noqa: BLE001 — unsure = not signed in
        return False


def signin_hint(conn: Any) -> str:
    """The sentence an app that SIGNS IN on this PC (Microsoft 365) adds to
    a chat connect, or ``""`` for every other app (v1.356.0 re-review, G60).

    The Directory card says "It works once you sign in…" beside its press;
    the chat's door said only "Connected", so the model reported the app
    ready and the next calendar question failed on a pack nobody had signed
    in to. The words are the card's own press ("Sign in to Microsoft") and
    where it is — never a command line."""
    if not getattr(conn, "signin_args", None):
        return ""
    from ..connectors.app_names import APPS_PLACE

    who = getattr(conn, "signin_name", "") or getattr(conn, "name", "")
    return (
        f' It works once the user signs in: "Sign in to {who}" on its card in'
        f" {APPS_PLACE} opens a pane with a web address and a code; tell the"
        " user to press it."
    )


class AppConnectTool(_RecordTool):
    name = "app_connect"
    RECORD = "app"
    description = (
        "Connect an app from the Directory (\"connect my Notion\") so its tools are available: "
        "`app` is the connector id (notion, github, sentry, slack, stripe, brave_search, "
        "google_maps, postgres, filesystem, fetch, memory, …). Non-secret settings go in "
        "`values`; a token is pasted into the secure card this shows — never ask for it in "
        "chat. The user approves on a card; Undo disconnects it. Apps that sign in through "
        "the browser (google_drive, onedrive, dropbox) and the browser itself are set up on "
        "their own pages: the call says whether one is connected, and where to connect it "
        "when it is not."
    )
    input_schema = {
        "type": "object",
        "properties": {"app": {"type": "string"}, "values": {"type": "object"}},
        "required": ["app"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("app") or "").strip().lower()

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """v1.433.0 (Wave 1, A4): the log, the approval card and the stream
        never carry a credential a model put in ``values`` — a token field
        (the app's own secret fields, or an unknown key named like one) is
        replaced whole, and every other value goes through the mask
        (``postgresql://me:***@host/db`` keeps the host the user approves)."""
        from ..connectors.catalog import get_connector
        from ..mcp.secrets import is_secret_key

        try:
            conn = get_connector(self._target(args))
        except Exception:  # noqa: BLE001
            conn = None
        fields = {f.name: f.kind for f in (getattr(conn, "fields", None) or [])}

        def full(key: str) -> bool:
            kind = fields.get(key)
            return kind == "secret" if kind is not None else is_secret_key(key)

        from ..detections.redact import MASK

        out: dict[str, Any] = {}
        for k, v in dict(args or {}).items():
            if k == "values" and isinstance(v, dict):
                out[k] = masked_args(v, full=full)
            elif k != "app" and v not in (None, "") and is_secret_key(str(k)):
                out[k] = MASK
            else:
                out[k] = masked_args(v)
        return out

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """No "connect an app" approval card for a call that CHANGES NOTHING
        (v1.353.0 integrate; the v1.227.0 rule "an Allow that cannot run is a
        lie"). Two shapes:

        * an app that cannot be connected from chat -- one that signs in
          through the browser (Google Drive, OneDrive, Dropbox, Gmail), the
          browser itself, or no app at all: the call answers with where it IS
          connected and leaves the fix button (``doors.failure_door_for``);
        * an app whose one secret is an API key (Pixio): the call only shows
          the SECURE key card, which is its own consent -- nothing is saved
          until the user pastes a key there.

        Everything else (an MCP app that is connected the moment the call
        runs) keeps the card: ``None`` = the ordinary permission verdict. A
        DENY rule still refuses all of it (the lanes read ``ask`` only when
        the mode is not DENY)."""
        from ..connectors.catalog import get_connector

        conn = get_connector(self._target(args))
        if conn is None or conn.connect_via in ("oauth", "api_key") or getattr(conn, "locked", ""):
            # v1.428.0: a LOCKED app is set up only from its card — this call
            # answers where, and changes nothing.
            return SpendCheck(ask=False, cost_note="changes nothing by itself")
        if _card_arg_with_password(conn, (args or {}).get("values")) is not None:
            # v1.433.0 (Wave 1, A4): refused by ``execute`` (the secure card
            # collects a connection string with a password) — no card.
            return SpendCheck(ask=False, cost_note="changes nothing by itself")
        # v1.356.0 integrate (G78): a saved app the user switched off — the
        # call only says so (``execute``), so it is no card either.
        platform = getattr(self, "platform", None)
        if platform is not None:
            from ..connectors.service import pack_on

            try:
                saved = await asyncio.to_thread(snap_app, platform, conn.id)
            except Exception:  # noqa: BLE001 — a verdict read never breaks the turn
                saved = None
            if saved is not None and not pack_on(saved):
                return SpendCheck(ask=False, cost_note="changes nothing by itself")
        return None

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..connectors.catalog import CATALOG, get_connector
        from ..connectors.service import _secret_name

        cid = self._target(args)
        conn = get_connector(cid)
        if conn is None:
            from ..connectors.catalog import get_link_card

            # v1.353.0 integrate: the browser is set up on its own page (the
            # Directory's "Your browser" card points there) -- say where,
            # instead of "there is no app called 'puppeteer'". The failed
            # call leaves the "Pair your browser" button (doors.failure_door_for).
            if get_link_card(cid) is not None:
                # v1.353.0 re-review: worded so it is true whether or not a
                # browser is already paired (this call cannot tell).
                return ToolResult(
                    ok=False,
                    error=(
                        "The browser isn't connected from chat: it is set up in "
                        "Settings › Connections › Browser (pair the Iron Jarvis browser "
                        "add-on there, and turn on browser access)."
                    ),
                )
            known = ", ".join(c.id for c in CATALOG)
            return ToolResult(ok=False, error=f"There is no app called {cid!r}. Known: {known}.")
        if conn.connect_via == "oauth":
            # v1.353.0 re-review: an app the user ALREADY signed in to is not
            # sent to connect it again. "find my tax files in Google Drive"
            # arms this tool (the drive words, v1.353.0 integrate), and a
            # failed call leaves "Connect Google Drive in Settings"
            # (doors.failure_door_for) — on a signed-in Drive that button and
            # "connect it from the Connections page" were both untrue. The
            # Directory's own answer (connections.status, off the loop).
            # The account name stays out of the answer (it would reach the model).
            if await asyncio.to_thread(_oauth_signed_in, self.platform, conn.provider):
                return ToolResult(
                    ok=True,
                    output=(
                        f"{conn.name} is already connected. Its files reach chat through a "
                        "memory base: if none points at one of its folders yet, the user can add "
                        "one on the Memory page."
                    ),
                    data={"app": conn.id, "already_connected": True},
                )
            # v1.354.0 (G39, with G45's words): the one-time setup an app
            # needs OUTSIDE Iron Jarvis is said BEFORE the user goes to
            # connect it, while no OAuth client is registered.
            from ..connectors.app_names import _needs_words

            needs = ""
            if conn.needs and not await asyncio.to_thread(
                _oauth_client_ready, self.platform, conn.provider
            ):
                needs = _needs_words(conn.needs)
            return ToolResult(
                ok=False,
                error=(
                    f"{conn.name} signs in through the browser — connect it from the"
                    f" Connections page.{needs}"
                ),
            )
        if conn.connect_via == "api_key":
            req = {
                "name": f"connection.{conn.provider}",
                "label": f"{conn.name} API key",
                "help": "",
                "why": f"to connect {conn.name}",
                "request_id": new_id("seq"),
            }
            return ToolResult(
                ok=True,
                output=f"A secure card for the {conn.name} key is shown. Never ask for it in chat.",
                data={"secret_requests": [req]},
            )
        values = args.get("values") if isinstance(args.get("values"), dict) else {}
        # v1.433.0 (Wave 1, A4): a connection string WITH its password is
        # never taken from chat — the secure card collects it (below), as it
        # does a token. Refused before anything is saved; the log row keeps
        # only the masked text (``redact_args``).
        _pw = _card_arg_with_password(conn, values)
        if _pw is not None:
            return ToolResult(
                ok=False,
                error=CONNECTION_NOT_IN_CHAT.format(label=_pw.label, name=conn.name, id=conn.id, field=_pw.name),
            )
        # v1.356.0 integrate (G78): an app the user SWITCHED OFF is still
        # saved, with its key — "connect my Slack" must not walk into the
        # connect flow and ask for the token again on the secure card. Say it
        # is off and where it is turned back on (the switch keeps the key).
        _saved = await asyncio.to_thread(snap_app, self.platform, conn.id)
        if _saved is not None:
            from ..connectors.app_names import APPS_PLACE
            from ..connectors.service import pack_on

            if not pack_on(_saved):
                # Not ok: nothing was connected, so the page must not turn the
                # app on for this chat (lib/appConnect.appConnectIds reads ok).
                return ToolResult(
                    ok=False,
                    error=(
                        f"{conn.name} is already added but switched off by the user, so its "
                        "tools are not loaded. It keeps its key: the user turns it back on with "
                        f"its switch in Settings › Tools or on its card in {APPS_PLACE}. "
                        "Never ask for its token again."
                    ),
                    data={"app": conn.id, "switched_off": True},
                )
        # v1.351.0 (G15): an app that is ALREADY connected and working is not
        # connected again — no card asking for a token it has, no record
        # change, no "waiting for its token". The page turns the app on for
        # this chat when it sees this call succeed (chat/page.tsx,
        # lib/appConnect.appConnectIds), which is what the sentence promises.
        if not values:
            have = await asyncio.to_thread(snap_app, self.platform, conn.id)
            live = len(self.platform.registry.mcp_names(conn.id)) if have is not None else 0
            if have is not None and live:
                # v1.351.0 integrate: an app with a token says how to replace
                # an expired one (`app.<id>` resolves to its first token field,
                # credentials._resolve_app); an app with none says nothing.
                _renew = (
                    f" If its token stopped working, config_secret with name app.{conn.id} "
                    "shows a secure card to replace it."
                    if any(f.kind == "secret" for f in conn.fields)
                    # v1.433.0 (Wave 1, A4): a connection string comes the same way.
                    else f" If its {card_arg_fields(conn)[0].label.lower()} changed, config_secret with "
                    f"name app.{conn.id} shows a secure card to replace it."
                    if card_arg_fields(conn)
                    else ""
                )
                return ToolResult(
                    ok=True,
                    output=(
                        f"{conn.name} is already connected ({live} {'tool' if live == 1 else 'tools'}). "
                        "It is on for this chat now; its tools can be used from the next message."
                        + _renew
                        + signin_hint(conn)
                    ),
                    data={"app": conn.id, "already_connected": True, "tools_loaded": live},
                )
        if getattr(conn, "locked", ""):
            # v1.428.0: Microsoft 365 is LOCKED DOWN for client data (read and
            # draft only, one account, its own lock, the user's own models) —
            # set up from its card, where the person types the account and the
            # firm's app, never from a model's arguments.
            from ..connectors.app_names import APPS_PLACE
            from ..connectors.service import LOCKED_ELSEWHERE

            return ToolResult(
                ok=False,
                error=LOCKED_ELSEWHERE.format(name=conn.name).replace(
                    "Settings › Connections", APPS_PLACE
                ),
                data={"app": conn.id, "locked": True},
            )
        cfg_args = list(conn.args)
        # v1.356.0 integrate (G60, lane 6D's review): the settings the pack
        # ALWAYS starts with (Microsoft 365's tool filter) — the Directory's
        # Connect writes them (service.pack_body), so must this door, or the
        # pack loads every tool its server has and its card offers no Sign in.
        env: dict[str, str] = dict(getattr(conn, "env", None) or {})
        env_secrets: dict[str, str] = {}
        requests: list[dict[str, Any]] = []
        # v1.433.0 (Wave 1, A4): one list of what a secure card collects —
        # secret fields AND credential-carrying arguments — so the card's name
        # here (`app.<id>[__FIELD]`) is the one credentials._resolve_app reads.
        _cards = card_fields(conn)
        multi = len(_cards) > 1
        _card_args = {id(g) for g in card_arg_fields(conn)}
        arg_card: dict[str, str] = {}
        for f in conn.fields:
            if f.kind == "secret":
                env_secrets[f.name] = _secret_name(conn.id, f.name)
                if not f.optional:
                    requests.append(
                        {
                            "name": f"app.{conn.id}" + (f"__{f.name}" if multi else ""),
                            "label": f.label,
                            "help": getattr(f, "help", "") or "",
                            "why": f"to connect {conn.name}",
                            "request_id": new_id("seq"),
                        }
                    )
                continue
            val = str(values.get(f.name, "")).strip()
            if not val and id(f) in _card_args and not f.optional:
                # The secure card collects it: the vault holds the whole
                # argument (``arg_secrets``) and the row a placeholder.
                from ..detections.redact import MASK

                idx = card_arg_index(conn, f)
                cfg_args[idx] = MASK
                arg_card[str(idx)] = _secret_name(conn.id, f.name)
                requests.append(
                    {
                        "name": f"app.{conn.id}" + (f"__{f.name}" if multi else ""),
                        "label": f.label,
                        "help": getattr(f, "help", "") or "",
                        "why": f"to connect {conn.name}",
                        "request_id": new_id("seq"),
                    }
                )
                continue
            if not val:
                if not f.optional:
                    return ToolResult(ok=False, error=f"{conn.name} needs `{f.name}` ({f.label}) in values.")
                continue
            if getattr(f, "options", None):
                # v1.356.0 integrate (G60): a choice is answered from its list,
                # never typed free — the Directory's door refuses the same
                # (service.choice_refusal). "work" is not a Microsoft authority.
                from ..connectors.catalog import option_for

                if option_for(f, val) is None:
                    allowed = ", ".join(f"`{o.value}` ({o.label})" for o in f.options)
                    return ToolResult(
                        ok=False,
                        error=f"{conn.name} needs `{f.name}` to be one of {allowed}.",
                    )
            if f.kind == "arg":
                cfg_args = [a.replace(f"<{f.name}>", val) for a in cfg_args]
            else:
                env[f.name] = val
        # v1.351.0 (G12): a value typed into an ARGUMENT (Postgres's
        # `postgresql://me:password@host/db`) is kept in the vault like any
        # pack's argument secret: config.toml holds the masked text, the vault
        # the real one, and the pack launches with the real one. A reconnect
        # with a DIFFERENT value never overwrites the row the prior config
        # names (`keep_others`): Undo restores that config, and must launch
        # the value it shows (v1.351.0 review).
        from ..mcp.secrets import split_args, store_arg_secrets

        cfg_args, arg_values = split_args(cfg_args)
        arg_secrets: dict[str, str] = {}
        if arg_values:
            try:
                arg_secrets = await asyncio.to_thread(
                    store_arg_secrets, self.platform.secrets, conn.id, arg_values, keep_others=True
                )
            except Exception as exc:  # noqa: BLE001 — names only, never the value
                return ToolResult(
                    ok=False,
                    error=f"The vault could not keep {conn.name}'s private setting ({type(exc).__name__}); nothing was connected.",
                )
        if arg_card:
            arg_secrets = {**arg_secrets, **arg_card}
        from ..connectors.catalog import is_remote

        if is_remote(conn):
            # v1.355.0 (G44): a server reached over the network (GitHub's own)
            # — the row the one add door stores for it: the token the secure
            # card saves is sent as "Authorization: Bearer <token>", its vault
            # row named by `_secret_name` (`mcp.<id>.auth.value`).
            from ..connectors.service import REMOTE_AUTH_FORMAT, REMOTE_AUTH_HEADER

            cfg: dict[str, Any] = {"name": conn.id, "transport": conn.transport, "url": conn.url}
            if conn.auth_field:
                cfg["auth"] = {
                    "header": REMOTE_AUTH_HEADER,
                    "format": REMOTE_AUTH_FORMAT,
                    "secret": _secret_name(conn.id, conn.auth_field),
                }
            if env:
                cfg["headers"] = env
        else:
            cfg = {"name": conn.id, "command": conn.command, "args": cfg_args}
            if arg_secrets:
                cfg["arg_secrets"] = arg_secrets
            if env:
                cfg["env"] = env
            if env_secrets:
                cfg["env_secrets"] = env_secrets
        before = await asyncio.to_thread(snap_app, self.platform, conn.id)
        # A pack waiting for its token is saved now and loaded when the card
        # stores it (credentials.CredentialStore); one with no token loads now.
        loaded = await asyncio.to_thread(apply_app, self.platform, conn.id, cfg, load=not requests)
        # v1.358.0: a pack that loaded NO tools did not start — the next start
        # runs the same loader and will not make it start either. It is never
        # "saved — loads at the next start": say it did not start and why.
        not_started = not requests and not loaded
        state = (
            "waiting for its token" if requests
            else (f"{loaded} tools" if loaded else NOT_STARTED_STATE)
        )
        # v1.354.0 (G39, G45's words): an app that needs a one-time setup
        # outside Iron Jarvis (Gmail's Google OAuth client) says so with the
        # connect, so the model can tell the user why it may not start yet.
        _needs = ""
        if conn.needs and not loaded:
            from ..connectors.app_names import APPS_PLACE, _needs_words

            _needs = (
                _needs_words(conn.needs).rstrip(".")
                + f"; its card in {APPS_PLACE} shows how."
            )
        if not_started:
            output = (
                f"Saved {conn.name}, but it did not start, so its tools are not in this chat"
                f"{_why_not_started(conn.id, has_needs=bool(_needs))} Never ask for a token in"
                f" chat.{_needs}"
            )
        else:
            # v1.356.0 re-review (G60): an app that signs in on this PC says
            # what is still to do, and where (only when nothing is waiting for
            # a token — the secure card comes first then).
            output = (
                f"Connected {conn.name} ({state}). Never ask for a token in chat.{_needs}"
                + ("" if requests else signin_hint(conn))
            )
        return ToolResult(
            ok=True,
            output=output,
            data={
                "record_change": self._card(
                    "App saved" if not_started else "App connected",
                    conn.name, "connected" if before else None, state, f"app.{conn.id}",
                ),
                "secret_requests": requests,
            },
        )


# --- webhooks


class WebhookUpdateTool(_RecordTool):
    name = "webhook_update"
    RECORD = "webhook"
    description = (
        "Change a webhook (by its `slug`): pause or resume it with `enabled`; for an outbound "
        "webhook also where it sends (`target_url`) or which `event_types` it sends. The user "
        "approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "enabled": {"type": "boolean"},
            "target_url": {"type": "string"},
            "event_types": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slug"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("slug") or "").strip()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        slug = self._target(args)
        before = await asyncio.to_thread(snap_webhook, self.platform, slug)
        if before is None:
            return ToolResult(ok=False, error=f"There is no webhook called {slug!r}.")
        url = args.get("target_url")
        url = url.strip() if isinstance(url, str) and url.strip() else None
        types = args.get("event_types") if isinstance(args.get("event_types"), list) else None
        enabled = args.get("enabled") if isinstance(args.get("enabled"), bool) else None
        if url is None and types is None and enabled is None:
            return ToolResult(ok=False, error="Say what to change: enabled, target_url or event_types.")
        if before["direction"] != "outbound" and (url is not None or types is not None):
            return ToolResult(
                ok=False, error=f"{slug!r} receives calls; it has no target or event types — only `enabled` can change."
            )
        try:
            await asyncio.to_thread(
                apply_webhook_update, self.platform, before, url=url, types=types, enabled=enabled
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        after = await asyncio.to_thread(snap_webhook, self.platform, slug)
        old, new = _describe_webhook(before), _describe_webhook(after)
        return ToolResult(
            ok=True,
            output=f"Changed the webhook '{slug}': {old} → {new}.",
            data={"record_change": self._card("Webhook changed", f"Webhook “{slug}”", old, new, f"webhook.{slug}")},
        )


class WebhookDeleteTool(_RecordTool):
    name = "webhook_delete"
    RECORD = "webhook"
    description = "Remove a webhook (by its `slug`). The user approves on a card; Undo brings it back as it was."
    input_schema = {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("slug") or "").strip()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        slug = self._target(args)
        before = await asyncio.to_thread(snap_webhook, self.platform, slug)
        if before is None:
            return ToolResult(ok=False, error=f"There is no webhook called {slug!r}.")
        await asyncio.to_thread(remove_webhook, self.platform, slug, before["direction"])
        return ToolResult(
            ok=True,
            output=f"Removed the webhook '{slug}'.",
            data={"record_change": self._card("Webhook removed", f"Webhook “{slug}”", _describe_webhook(before), None, f"webhook.{slug}")},
        )


# --- sentinels


class SentinelUpdateTool(_RecordTool):
    name = "sentinel_update"
    RECORD = "sentinel"
    description = (
        "Pause or resume a sentinel (an always-on watcher, by its `name`) with `enabled`. "
        "The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "enabled": {"type": "boolean"}},
        "required": ["name", "enabled"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        before = await asyncio.to_thread(snap_sentinel, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no sentinel called {name!r}.")
        if not isinstance(args.get("enabled"), bool):
            return ToolResult(ok=False, error="Say `enabled`: true to resume it, false to pause it.")
        await asyncio.to_thread(self.platform.sentinels.set_enabled, name, bool(args["enabled"]))
        after = await asyncio.to_thread(snap_sentinel, self.platform, name)
        old, new = _describe_sentinel(before), _describe_sentinel(after)
        return ToolResult(
            ok=True,
            output=f"Sentinel '{name}': {old} → {new}.",
            data={"record_change": self._card("Sentinel changed", f"Sentinel “{name}”", old, new, f"sentinel.{name}")},
        )


class SentinelDeleteTool(_RecordTool):
    name = "sentinel_delete"
    RECORD = "sentinel"
    description = "Remove a sentinel (by its `name`). The user approves on a card; Undo brings it back with what it had already seen."
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        before = await asyncio.to_thread(snap_sentinel, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no sentinel called {name!r}.")
        await asyncio.to_thread(self.platform.sentinels.remove, name)
        return ToolResult(
            ok=True,
            output=f"Removed the sentinel '{name}'.",
            data={"record_change": self._card("Sentinel removed", f"Sentinel “{name}”", _describe_sentinel(before), None, f"sentinel.{name}")},
        )


# --- goals


class _GoalTool(_RecordTool):
    RECORD = "goal"

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("goal") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_goal(self.platform, self._target(args))

    def _missing(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=False, error=f"There is no goal {self._target(args)!r} — goal_list shows each goal's id."
        )


class GoalUpdateTool(_GoalTool):
    name = "goal_update"
    description = (
        "Change a standing goal (`goal` = its id or its exact words): `status` (active, paused, "
        "done, abandoned), `text`, `category`, `priority` 1-5, its autonomy dial "
        "`autonomy_level` (suggest, act_low, act_all) or its budgets (`action_budget`, "
        "`spend_budget` tokens). The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "status": {"type": "string", "enum": list(GOAL_STATUSES)},
            "text": {"type": "string"},
            "category": {"type": "string"},
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
            "autonomy_level": {"type": "string", "enum": ["suggest", "act_low", "act_all"]},
            "action_budget": {"type": "integer", "minimum": 0},
            "spend_budget": {"type": "integer", "minimum": 0},
        },
        "required": ["goal"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..motivation.models import AUTONOMY_LEVELS

        gid = await asyncio.to_thread(self._resolve, args)
        before = await asyncio.to_thread(snap_goal, self.platform, gid) if gid else None
        if before is None:
            return self._missing(args)
        keys = ("status", "text", "category", "priority", "autonomy_level", "action_budget", "spend_budget")
        fields = {k: args[k] for k in keys if args.get(k) is not None}
        if not fields:
            return ToolResult(ok=False, error=f"Say what to change: {', '.join(keys)}.")
        if "status" in fields and fields["status"] not in GOAL_STATUSES:
            return ToolResult(ok=False, error=f"status must be one of {', '.join(GOAL_STATUSES)}")
        if "autonomy_level" in fields and fields["autonomy_level"] not in AUTONOMY_LEVELS:
            return ToolResult(ok=False, error=f"autonomy_level must be one of {', '.join(AUTONOMY_LEVELS)}")
        if "text" in fields and not str(fields["text"]).strip():
            return ToolResult(ok=False, error="A goal needs its words.")
        try:
            for k in ("priority", "action_budget", "spend_budget"):
                if k in fields:
                    fields[k] = int(fields[k])
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="priority and the budgets are whole numbers.")
        await asyncio.to_thread(self.platform.intent.update_goal, gid, **fields)
        after = await asyncio.to_thread(snap_goal, self.platform, gid)
        if after and after["text"] != before["text"]:
            old, new = f"“{before['text']}”", f"“{after['text']}”"
        else:
            old, new = _describe_goal(before), _describe_goal(after)
        return ToolResult(
            ok=True,
            output=f"Changed the goal '{before['text']}': {old} → {new}.",
            data={"record_change": self._card("Goal changed", f"Goal “{before['text']}”", old, new, f"goal.{gid}")},
        )


class GoalDeleteTool(_GoalTool):
    name = "goal_delete"
    description = (
        "Drop a standing goal (`goal` = its id or its exact words). The goal store keeps no "
        "delete, so the goal is marked ABANDONED: it stays listed and the autonomy loop never "
        "works on it again. The user approves on a card; Undo gives it back its status."
    )
    input_schema = {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        gid = await asyncio.to_thread(self._resolve, args)
        before = await asyncio.to_thread(snap_goal, self.platform, gid) if gid else None
        if before is None:
            return self._missing(args)
        if before["status"] == "abandoned":
            return ToolResult(ok=False, error=f"The goal '{before['text']}' is already abandoned.")
        await asyncio.to_thread(self.platform.intent.update_goal, gid, status="abandoned")
        after = await asyncio.to_thread(snap_goal, self.platform, gid)
        return ToolResult(
            ok=True,
            output=f"Dropped the goal '{before['text']}' (marked abandoned).",
            data={
                "record_change": self._card(
                    "Goal dropped", f"Goal “{before['text']}”", _describe_goal(before), _describe_goal(after), f"goal.{gid}"
                )
            },
        )


# --- reflex rules


class _ReflexTool(_RecordTool):
    RECORD = "reflex"

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("rule") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_reflex(self.platform, self._target(args))

    def _missing(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=False, error=f"There is no rule {self._target(args)!r} (give its id or its exact name).")


class ReflexCreateTool(_ReflexTool):
    name = "reflex_create"
    description = (
        "Make a rule that starts work when something happens: a signal from `source` "
        "(webhook, comm, email, calendar, slack) whose `match` is the webhook's slug or a "
        "keyword in the message (empty = every message of that source) starts `action` "
        "(workflow, remote_agent or session) on `target` (the workflow or agent name), with an "
        "optional `task_template` ({body}, {text}, {slug} are filled in). The user approves on "
        "a card; Undo removes the rule."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "source": {"type": "string", "enum": ["webhook", "comm", "email", "calendar", "slack"]},
            "match": {"type": "string"},
            "action": {"type": "string", "enum": ["workflow", "remote_agent", "session"]},
            "target": {"type": "string"},
            "task_template": {"type": "string"},
            "enabled": {"type": "boolean"},
            "project_id": {"type": "string"},
        },
        "required": ["source", "action"],
    }

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        # The store mints the rule's id, so the capture is named after the write.
        return await capture_create_undo(self.platform, "reflex", "", pending=True)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # Defaults and checks are POST /reflex/rules' (ReflexRuleBody + add_reflex_rule).
        source = str(args.get("source") or "webhook")
        action = str(args.get("action") or "workflow")
        match = str(args.get("match") or "")
        target = str(args.get("target") or "")
        problem = validate_reflex(source, match, action, target)
        if problem:
            return ToolResult(ok=False, error=problem)
        enabled = args.get("enabled") if isinstance(args.get("enabled"), bool) else True
        rule = await asyncio.to_thread(
            lambda: self.platform.reflex.add(
                name=str(args.get("name") or ""),
                source=source,
                match=match,
                action=action,
                target=target,
                task_template=str(args.get("task_template") or ""),
                enabled=enabled,
                project_id=args.get("project_id") or None,
            )
        )
        bind_created(rule.id)
        after = await asyncio.to_thread(snap_reflex, self.platform, rule.id)
        data: dict[str, Any] = {"id": rule.id, "name": rule.name}
        card = create_card("reflex", rule.id, _describe_reflex(after), label=f"Rule “{rule.name}”")
        if card:
            data["record_change"] = card
        return ToolResult(ok=True, output=f"Made the rule '{rule.name}' ({_describe_reflex(after)}).", data=data)


class ReflexUpdateTool(_ReflexTool):
    name = "reflex_update"
    description = (
        "Turn a rule (`rule` = its id or exact name) on or off with `enabled`, or ground it in "
        "a project with `project_id` (\"\" clears it). The user approves on a card; the change "
        "has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"rule": {"type": "string"}, "enabled": {"type": "boolean"}, "project_id": {"type": "string"}},
        "required": ["rule"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rid = await asyncio.to_thread(self._resolve, args)
        before = await asyncio.to_thread(snap_reflex, self.platform, rid) if rid else None
        if before is None:
            return self._missing(args)
        enabled = args.get("enabled") if isinstance(args.get("enabled"), bool) else None
        project = args.get("project_id") if isinstance(args.get("project_id"), str) else None
        if enabled is None and project is None:
            return ToolResult(ok=False, error="Say what to change: enabled or project_id.")
        # PATCH /reflex/rules/{id}'s three intents: omitted = unchanged,
        # "" = clear the grounding, a value = re-ground.
        if enabled is not None:
            await asyncio.to_thread(self.platform.reflex.set_enabled, rid, enabled)
        if project is not None:
            await asyncio.to_thread(self.platform.reflex.set_project, rid, project)
        after = await asyncio.to_thread(snap_reflex, self.platform, rid)
        label = f"Rule “{before['name']}”"
        old, new = _describe_reflex(before), _describe_reflex(after)
        if old == new:
            old, new = before.get("project_id") or "no project", (after or {}).get("project_id") or "no project"
        return ToolResult(
            ok=True,
            output=f"Changed the rule '{before['name']}': {old} → {new}.",
            data={"record_change": self._card("Rule changed", label, old, new, f"reflex.{rid}")},
        )


class ReflexDeleteTool(_ReflexTool):
    name = "reflex_delete"
    description = "Remove a rule (`rule` = its id or exact name). The user approves on a card; Undo brings it back as it was."
    input_schema = {"type": "object", "properties": {"rule": {"type": "string"}}, "required": ["rule"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rid = await asyncio.to_thread(self._resolve, args)
        before = await asyncio.to_thread(snap_reflex, self.platform, rid) if rid else None
        if before is None:
            return self._missing(args)
        await asyncio.to_thread(self.platform.reflex.remove, rid)
        return ToolResult(
            ok=True,
            output=f"Removed the rule '{before['name']}'.",
            data={"record_change": self._card("Rule removed", f"Rule “{before['name']}”", _describe_reflex(before), None, f"reflex.{rid}")},
        )


# --- standing grants


class GrantRevokeTool(_RecordTool):
    name = "grant_revoke"
    RECORD = "grant"
    description = (
        "Take back an \"always allow\" (a standing grant): `grant` is its id, or the tool's "
        "name when only one always-allow covers that tool. From then on that call asks again. "
        "Undo puts the always-allow back."
    )
    input_schema = {"type": "object", "properties": {"grant": {"type": "string"}}, "required": ["grant"]}

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("grant") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_grant(self.platform, self._target(args))[0]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        gid, problem = await asyncio.to_thread(resolve_grant, self.platform, self._target(args))
        if problem:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_grant, self.platform, gid)
        if before is None:
            return ToolResult(ok=False, error=f"There is no always-allow {gid!r}.")
        if before.get("revoked_at"):
            return ToolResult(ok=False, error="That always-allow was already taken back.")
        await asyncio.to_thread(self.platform.grants.revoke, gid)
        label = before.get("label") or before.get("tool") or gid
        where = f"{before.get('scope_kind')} {before.get('scope_id')}".strip()
        return ToolResult(
            ok=True,
            output=f"Took back the always-allow: {label}. It asks again from now on.",
            data={
                "record_change": self._card(
                    "Always-allow taken back", label, f"runs without asking ({where})", "asks first", f"grant.{gid}"
                )
            },
        )


# --- "undo that"


def ledger_tools() -> frozenset[str]:
    """The tools the configuration ledger lists — the SAME set as ``GET
    /config/ledger`` (routes/settings.py ``config_ledger``)."""
    return frozenset(
        {"update_settings", "set_secret", "config_set", "config_change", "config_change_protected", *RECORD_CARD_TOOLS}
    )


def find_undo_target(engine: Any, action_id: str = "") -> dict[str, Any] | None:
    """The configuration change ``config_undo`` reverses. Blocking.

    With an id: that ledger row (whatever state it is in — ``perform_undo``
    gives the exact refusal for one already undone or without an inverse),
    or None when it is not a configuration change. Without: the NEWEST row
    the ledger would offer an Undo for — ok, not itself an undo, not undone,
    with a reversible inverse, from a tool that did not declare itself
    irreversible."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ToolInvocation, UndoJournal

    tools = ledger_tools()
    with session_scope(engine) as db:
        if action_id:
            inv = db.get(ToolInvocation, action_id)
            if inv is None or inv.tool not in tools or inv.undo_of is not None or not inv.ok:
                return None
            return {"action_id": inv.id, "tool": inv.tool, "summary": (inv.output or "")[:200]}
        rows = db.exec(
            select(ToolInvocation, UndoJournal)
            .where(UndoJournal.action_id == ToolInvocation.id)
            .where(ToolInvocation.tool.in_(tools))  # type: ignore[attr-defined]
            .where(ToolInvocation.ok == True)  # noqa: E712
            .where(ToolInvocation.undo_of == None)  # noqa: E711
            .where(ToolInvocation.undone_at == None)  # noqa: E711
            .where(UndoJournal.reversible == True)  # noqa: E712
            .order_by(ToolInvocation.created_at.desc())  # type: ignore[attr-defined]
            .limit(50)
        ).all()
        for inv, _j in rows:
            if (inv.reversibility or "").lower() == Reversibility.IRREVERSIBLE.value:
                continue
            return {"action_id": inv.id, "tool": inv.tool, "summary": (inv.output or "")[:200]}
    return None


class ConfigUndoTool(Tool):
    """"undo that" from chat: reverse a configuration change through THE code
    ``POST /undo/{id}`` runs (``routes.undo.perform_undo``), so chat and the
    Undo button cannot disagree about what an undo does. Not itself undoable
    (the undo row is IRREVERSIBLE, exactly as the route writes it), so it
    carries no card — the reply says what was undone."""

    name = "config_undo"
    description = (
        "Undo a configuration change — \"undo that\". With no `action_id` it undoes the "
        "newest change that can still be undone (a setting, a credential, a schedule, "
        "workflow, channel, app, webhook, sentinel, goal, rule or always-allow — changed in "
        "chat or on a page). The user approves on a card. Tell the user what was undone."
    )
    input_schema = {"type": "object", "properties": {"action_id": {"type": "string"}}}

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.undo import perform_undo

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error="Undo from chat needs the running app.")
        aid = str(args.get("action_id") or "").strip()
        target = await asyncio.to_thread(find_undo_target, self.platform.engine, aid)
        if target is None:
            return ToolResult(
                ok=False,
                error=(
                    f"There is no configuration change {aid!r} to undo."
                    if aid
                    else "There is no recent change that can still be undone."
                ),
            )
        try:
            out = await perform_undo(d, target["action_id"])
        except HTTPException as exc:
            return ToolResult(ok=False, error=str(exc.detail))
        summary = target["summary"] or target["tool"]
        return ToolResult(
            ok=True,
            output=f"Undone: {summary}",
            data={
                "undone": target["action_id"],
                "tool": target["tool"],
                "summary": summary,
                "undo_invocation_id": out.get("undo_invocation_id"),
            },
        )


def record_tools(platform: Any) -> list[Tool]:
    return [
        ScheduleUpdateTool(platform),
        ScheduleDeleteTool(platform),
        WorkflowUpdateTool(platform),
        WorkflowDeleteTool(platform),
        WorkflowScheduleTool(platform),
        ChannelToggleTool(platform),
        ChannelConnectTool(platform),
        AppConnectTool(platform),
        WebhookUpdateTool(platform),
        WebhookDeleteTool(platform),
        SentinelUpdateTool(platform),
        SentinelDeleteTool(platform),
        GoalUpdateTool(platform),
        GoalDeleteTool(platform),
        ReflexCreateTool(platform),
        ReflexUpdateTool(platform),
        ReflexDeleteTool(platform),
        GrantRevokeTool(platform),
        ConfigUndoTool(platform),
        *_apps_record_tools(platform),
    ]


def _apps_record_tools(platform: Any) -> list[Tool]:
    from .records_apps import apps_record_tools

    return apps_record_tools(platform)


# ----------------------------------------------- undo for the create tools


async def capture_create_undo(
    platform: Any, record: str, name: str, *, pending: bool = False
) -> dict[str, Any] | None:
    """A create tool gains an Undo: the same snapshot (None for a brand-new
    record, so Undo removes it; the prior version for one re-saved in place —
    a workflow saved again, a webhook slug registered again).

    ``pending=True`` is for a record whose id the STORE mints (a goal, a
    reflex rule): nothing exists to snapshot yet, so the descriptor is
    captured with no name and the tool names it after the write with
    :func:`bind_created`. The registry journals the descriptor only after a
    SUCCESSFUL execute (the same post-execute fill as ``finalize_post_hash``),
    so a failed create leaves no row."""
    _CHANGE_ID.set("")
    _PENDING.set(None)
    if pending:
        change_id = new_id("rec")
        _CHANGE_ID.set(change_id)
        desc = {
            "kind": RECORD_RESTORE,
            "pre_inline": json.dumps({"record": record, "name": "", "prior": None, "change_id": change_id}),
        }
        _PENDING.set(desc)
        return desc
    if not name:
        return None
    try:
        prior = await asyncio.to_thread(_SNAP[record], platform, name)
    except Exception:  # noqa: BLE001
        return None
    change_id = new_id("rec")
    _CHANGE_ID.set(change_id)
    return {
        "kind": RECORD_RESTORE,
        "pre_inline": json.dumps({"record": record, "name": name, "prior": prior, "change_id": change_id}, default=str),
    }


def bind_created(name: str) -> None:
    """Name the record a ``pending`` capture is about, once the store minted
    it. A no-op when this call captured nothing (a direct ``execute``)."""
    desc = _PENDING.get()
    if not desc or not name:
        return
    try:
        meta = json.loads(desc.get("pre_inline") or "{}")
    except (TypeError, ValueError):
        return
    meta["name"] = str(name)
    desc["pre_inline"] = json.dumps(meta, default=str)
    _PENDING.set(None)


_CREATE_WORDS = {
    "schedule": "Schedule",
    "workflow": "Workflow",
    "webhook": "Webhook",
    "sentinel": "Sentinel",
    "goal": "Goal",
    "reflex": "Rule",
}


def create_card(
    record: str, name: str, new: str | None, *, existed: bool = False, label: str = ""
) -> dict[str, Any] | None:
    """The reply's card for a create — only when this call captured an Undo
    (no ledger row, no card: a direct ``execute`` keeps its old result)."""
    if not _CHANGE_ID.get():
        return None
    word = _CREATE_WORDS.get(record, record.title())
    return {
        "title": f"{word} {'updated' if existed else 'created'}",
        "key": f"{record}.{name}",
        "label": label or f"{word} “{name}”",
        "old": None,
        "new": new,
        "change_id": _CHANGE_ID.get(),
    }
