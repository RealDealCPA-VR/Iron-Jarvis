"""A reply that CLAIMS a setting or a record changed is checked against what
this turn really changed (v1.436.0, chat-first plan Wave 5 group K).

THE GAP. Chat can now change every setting and most of the user's own
records (``settings/tools.py``, ``settings/records*.py``) — and a model can
also just SAY it did. "Done — read aloud is now on" after a refused
``config_set``, "I've paused the bookkeeper agent" after a declined card, or
the same words with no tool at all, read exactly like a real change. The
owner (a CPA) must be able to trust what chat says it did. The file twin of
this check is ``chat_turn._claimed_write_note`` (v1.153.2): the record
decides, never the words alone.

WHAT COUNTS AS A CLAIM (deliberately narrow; when unsure, no note):

* an assertive past-tense change by the ASSISTANT: "I've turned on read
  aloud", "I switched you to the Daylight theme", "I paused the bookkeeper
  agent", "Turned on Auto tools." (a bare past verb at a sentence start, or
  after "Done —"), "…and created a schedule";
* a result stated as NOW true or as having been done: "your default model is
  now Sonnet", "Local only is now on", "The schedule has been created";
* "Undone." / "I've undone that change";
* a reply saying THIS reply was kept Local only (``chat_local_only`` applies
  from the user's NEXT message, so this reply never is, unless it was
  already Local only when it started).

NEVER A CLAIM: a question; an offer or a future ("I can turn it on", "Shall
I…", "it will…", "once you approve…"); the user's own action ("you turned it
on"); quoted words, code, a block quote; how a setting works ("When read
aloud is on, …"); a sentence that points back to an EARLIER turn ("earlier",
"already", "yesterday", "as I said", "in my last reply"); and a claim whose
kind an EARLIER assistant message in this conversation already claimed (the
reply may be summarising it — a claim the earlier reply's own note marked as
not made does not count as that excuse).

THE RECORD (this turn only): ``tools_used`` (the calls that SUCCEEDED — a
refused, failed, declined or still-waiting call is not in it) and the
``config_cards`` the successful calls produced (a setting's key, a record's
key, an app waiting for its key). A claim is answered by any successful
change of a kind it may name (a union — the conservative side). Nothing is
judged after a tool that can change anything without naming it (a command,
code, a hand-off, a workflow run, a user-made tool), and record claims are
not judged after a file was changed (a coding turn's "I updated the
scheduler" is about code).

THE WORDS. One plain line, the setting named by its label in
``settings/schema.py`` (never a key): "Note: “Read replies aloud” was not
changed in this reply." Records by their plain noun: "No schedule was created
in this reply." MIRROR NOTE (lock-step): both chat lanes append
:func:`claimed_change_note` right after ``_claimed_write_note``.

LINEAR. Every pattern here runs on a BOUNDED sentence (at most
:data:`MAX_SENTENCE` characters, at most :data:`MAX_SENTENCES` of them, the
reply cut to :data:`MAX_SCAN`), each repeat is bounded and no two unbounded
runs sit side by side; the cleaning passes are bounded per match. Pinned in a
child process with a deadline (tests/test_change_claims_v14360.py). Pure: it
reads no file and no database, so it runs on the event loop like the file
twin. Never raises (a fault says nothing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

#: How much of a reply is read: its head and tail halves when it is longer.
MAX_SCAN = 24_000
#: One sentence (a longer run with no stop is read in pieces of this size).
MAX_SENTENCE = 400
MAX_SENTENCES = 400
#: Claims judged per reply, and sentences said in the note.
MAX_CLAIMS = 24
MAX_SAID = 4
#: Earlier assistant messages read for the summary excuse.
MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 8_000
#: Words the clause scanner reads after a verb (its object).
MAX_OBJECT_TOKENS = 14
MAX_SUBJECT_TOKENS = 10

#: Every note line ends a sentence with these words — the marker an earlier
#: message's note is recognised by (the summary excuse never counts a claim
#: its own reply's note said was not made).
IN_THIS_REPLY = "in this reply"

# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

#: A success of one of these may have changed ANYTHING without naming it
#: (a command, code, a hand-off, a workflow run, a program and what is typed
#: into it): no claim is judged after one (the file twin's rule).
_MAY_CHANGE_ANYTHING = frozenset(
    {
        "shell", "run_code", "code_run", "repl", "process_start", "process_write",
        "delegate", "delegate_remote", "spawn_agent", "workflow_run", "assign_work",
        "pane_send", "pane_spawn",
    }
)
#: The settings writers (their card names the key changed).
_SETTINGS_WRITERS = frozenset({"config_set", "config_change", "config_change_protected"})
_UNDO_TOOLS = frozenset({"config_undo", "change_undo"})

#: The successful tools that answer a claim about each kind of record.
_RECORD_TOOLS: dict[str, frozenset[str]] = {
    "schedule": frozenset({"schedule_create", "schedule_update", "schedule_delete", "workflow_schedule"}),
    "workflow": frozenset({"workflow_create", "workflow_update", "workflow_delete", "workflow_schedule"}),
    "destination": frozenset({"channel_connect", "channel_toggle", "channel_remove", "channel_alerts"}),
    "webhook": frozenset({"webhook_add", "webhook_update", "webhook_delete"}),
    "watcher": frozenset({"sentinel_add", "sentinel_update", "sentinel_delete"}),
    "goal": frozenset({"goal_add", "goal_update", "goal_delete"}),
    "rule": frozenset({"reflex_create", "reflex_update", "reflex_delete"}),
    "grant": frozenset({"grant_revoke"}),
    "project": frozenset({"project_create", "project_update", "project_archive", "project_focus"}),
    "agent": frozenset({"agent_create", "agent_update", "agent_pause", "agent_delete", "create_agent"}),
}
#: A record card's key starts with these (``records._card``).
_RECORD_CARD_PREFIX: dict[str, tuple[str, ...]] = {
    "schedule": ("schedule.",),
    "workflow": ("workflow.", "schedule."),
    "destination": ("channel.",),
    "webhook": ("webhook.",),
    "watcher": ("sentinel.",),
    "goal": ("goal.",),
    "rule": ("reflex.",),
    "grant": ("grant.",),
    "project": ("project.",),
    "agent": ("agent.",),
}
_APP_CONNECTORS = frozenset({"app_connect", "app_switch", "app_import", "channel_connect", "channel_toggle"})
_APP_REMOVERS = frozenset({"app_disconnect", "app_switch", "app_import", "channel_remove", "channel_toggle"})
_LOCAL_ONLY_TOOL = "chat_local_only"
_LOCAL_ONLY_NEW_KEY = "chat_new_local_only"
_LOCAL_ONLY_CARD_KEY = "chat.local_only"
#: ``records.AppConnectTool``'s state for an app still waiting for its token.
_WAITING_STATE = "waiting for its token"

# --------------------------------------------------------------------------
# words
# --------------------------------------------------------------------------

#: The plain nouns a record kind is called in the note.
_RECORD_NOUN = {
    "schedule": "schedule",
    "workflow": "saved workflow",
    "destination": "notification destination",
    "webhook": "webhook",
    "watcher": "watcher",
    "goal": "goal",
    "rule": "rule",
    "grant": "always-allow",
    "project": "project",
    "agent": "agent",
    "app": "app",
}

ON, OFF, CHANGE, CREATE, REMOVE, PAUSE, CONNECT, DISCONNECT, UNDO = (
    "on", "off", "change", "create", "remove", "pause", "connect", "disconnect", "undo",
)

#: One-word verbs (past tense / participle) and what they claim.
_VERBS: dict[str, str] = {
    "created": CREATE, "added": CREATE, "made": CREATE, "built": CREATE, "saved": CREATE,
    "scheduled": CREATE,
    "changed": CHANGE, "switched": CHANGE, "set": CHANGE, "updated": CHANGE, "edited": CHANGE,
    "modified": CHANGE, "adjusted": CHANGE, "raised": CHANGE, "lowered": CHANGE,
    "increased": CHANGE, "decreased": CHANGE, "reset": CHANGE, "moved": CHANGE, "put": CHANGE,
    "renamed": CHANGE, "configured": CHANGE, "restored": CHANGE,
    "enabled": ON, "activated": ON, "unpaused": ON,
    "disabled": OFF, "deactivated": OFF, "muted": OFF,
    "deleted": REMOVE, "removed": REMOVE, "archived": REMOVE, "cancelled": REMOVE,
    "canceled": REMOVE, "revoked": REMOVE,
    "paused": PAUSE, "resumed": PAUSE, "stopped": PAUSE, "restarted": PAUSE,
    "connected": CONNECT, "imported": CONNECT, "installed": CONNECT, "linked": CONNECT,
    "disconnected": DISCONNECT, "uninstalled": DISCONNECT, "unlinked": DISCONNECT,
    "undid": UNDO, "undone": UNDO, "reverted": UNDO,
}
#: A verb whose particle carries the claim ("turned on", "set up").
_PARTICLE_VERBS: dict[str, dict[str, str]] = {
    "turned": {"on": ON, "off": OFF},
    "switched": {"on": ON, "off": OFF},
    "set": {"up": CREATE},
    "rolled": {"back": UNDO},
    "hooked": {"up": CONNECT},
}
#: Past forms that are also a present (an instruction: "Set the theme in
#: Settings"): never a claim as the bare first word of a sentence.
_AMBIGUOUS = frozenset({"set", "put", "reset", "made"})

#: What each kind of claim may say (verbs of another family name something
#: else: "I added a Notion page" is work IN the app, not a connection).
_KIND_VERBS = {
    "setting": frozenset({ON, OFF, CHANGE, CREATE}),
    "record": frozenset({ON, OFF, CHANGE, CREATE, REMOVE, PAUSE}),
    "app": frozenset({ON, OFF, CONNECT, DISCONNECT, REMOVE}),
    "local_only": frozenset({ON, OFF, CHANGE}),
}

_BOUNDARY = frozenset(
    {",", ";", ":", "(", ")", "—", "and", "but", "so", "plus", "because", "which", "while",
     "although", "though", "whereas", "or", "then"}
)
#: Words between a subject and its verb that the clause scanner skips.
_ADVERBS = frozenset(
    {"just", "now", "also", "successfully", "finally", "quickly", "right", "have", "has",
     "gone", "went", "ahead", "here", "go", "all", "both"}
)
_SUBJECTS = frozenset({"i", "we", "i've", "we've"})
#: An interjection before a bare verb ("Done — turned on read aloud").
_INTERJECTIONS = frozenset(
    {"done", "ok", "okay", "sure", "alright", "all", "set", "great", "perfect", "yes", "got",
     "it", "there", "you", "go", "absolutely", "certainly", "of", "course", "good", "news",
     "and", "that's", "thats", "finished", "complete", "completed"}
)
#: A word before the verb that makes it an offer, a future, a denial, a
#: condition, an instruction or someone else's action.
_NOT_A_CLAIM_BEFORE = frozenset(
    {
        "not", "never", "no", "nothing", "can", "can't", "cannot", "could", "couldn't", "will",
        "won't", "would", "wouldn't", "should", "shouldn't", "shall", "may", "might", "must",
        "to", "i'll", "we'll", "i'd", "we'd", "you'll", "let", "let's", "lets", "want", "wanted",
        "wants", "try", "tried", "trying", "attempt", "attempted", "going", "gonna", "about",
        "plan", "planned", "planning", "need", "needs", "needed", "ready", "able", "unable",
        "didn't", "don't", "doesn't", "wasn't", "weren't", "haven't", "hasn't", "hadn't", "had",
        "isn't", "aren't", "if", "once", "when", "whenever", "unless", "after", "until", "how",
        "why", "what", "who", "whether", "please", "you", "you've", "you're", "your", "they",
        "he", "she", "it", "this", "that", "someone", "anyone", "nobody", "asked", "help",
        "helped", "hope", "think", "believe", "probably", "maybe", "perhaps", "almost", "nearly",
    }
)
#: A word anywhere in the sentence that points back to an earlier turn.
_EARLIER = frozenset(
    {"earlier", "previously", "yesterday", "ago", "already", "prior", "originally", "since",
     "formerly", "before", "beforehand", "last", "previous"}
)
#: Words after "is now" that DESCRIBE ("now available in Settings").
_DESCRIPTIVE = frozenset(
    {
        "available", "supported", "possible", "part", "located", "found", "shown", "listed",
        "visible", "called", "named", "known", "under", "inside", "accessible", "included",
        "optional", "required", "deprecated", "retired", "in", "at", "here", "there", "easier",
        "harder", "faster", "slower", "simpler", "better", "worse", "more", "less", "also",
        "only", "a", "an", "much", "very", "really", "quite", "smarter", "cheaper", "free",
        "different", "separate", "per", "your", "my", "where", "how", "what", "one", "two",
        "able", "ready", "waiting", "pending", "asking", "shown", "up", "to",
    }
)
_STATE_ON = frozenset({"on", "enabled", "active", "activated", "live", "running"})
_STATE_OFF = frozenset({"off", "disabled", "inactive", "deactivated", "muted"})
_STATE_PAUSE = frozenset({"paused", "stopped", "resumed"})
_STATE_REMOVE = frozenset({"deleted", "removed", "gone", "archived", "revoked", "cancelled", "canceled"})
_STATE_CREATE = frozenset({"created", "scheduled", "saved", "added"})

#: Words after a record noun that make it code or a document ("the
#: scheduler logic", "a workflow diagram", "the agent class").
_NOT_THE_RECORD_AFTER = frozenset(
    {
        "class", "classes", "function", "functions", "method", "methods", "module", "modules",
        "handler", "handlers", "component", "components", "code", "test", "tests", "file",
        "files", "folder", "folders", "logic", "config", "script", "scripts", "endpoint",
        "endpoints", "api", "route", "routes", "table", "tables", "column", "columns", "field",
        "fields", "query", "type", "types", "interface", "struct", "service", "object",
        "variable", "diagram", "chart", "doc", "docs", "document", "outline", "draft",
        "description", "template", "templates", "structure", "timeline", "plan", "budget",
        "page", "pages", "view", "loop", "runtime", "prompt", "framework", "library", "pattern",
        "engine", "parser", "schema", "directory", "scaffold", "repo", "repository", "skeleton",
        "layout", "root", "dependencies", "readme", "settings", "post", "line", "kick",
        "keeper", "of", "string", "id", "ids", "name", "names", "list", "status", "card",
        "cards", "section", "step", "steps", "spec", "example", "examples", "sample",
    }
)
#: Words before "agent" that name someone else's agent.
_NOT_OUR_AGENT_BEFORE = frozenset(
    {"travel", "estate", "insurance", "booking", "user", "support", "customer", "real",
     "sales", "secret", "free", "double", "enrollment", "transfer", "leasing", "ai", "an"}
)
#: The user's own content: a record noun "in your spreadsheet" is something
#: else ("a schedule in your spreadsheet", "the goal of this essay").
_CONTENT = frozenset(
    {
        "spreadsheet", "sheet", "sheets", "workbook", "document", "doc", "docs", "file", "files",
        "email", "emails", "calendar", "essay", "report", "presentation", "slide", "slides",
        "deck", "code", "script", "scripts", "repo", "repository", "page", "pages", "note",
        "notes", "list", "table", "plan", "draft", "letter", "memo", "invoice", "contract",
        "book", "books", "ledger", "quickbooks", "website", "site", "outline", "diagram",
        "chart", "codebase", "program", "function", "readme", "message", "post", "workflow's",
        "\x00file",
    }
)
_ELSEWHERE_PREPS = frozenset({"in", "of", "inside", "into", "from", "on", "within"})
_DETS = frozenset({"your", "the", "this", "that", "my", "a", "an", "our", "these", "those"})
#: A calendar item, never one of Iron Jarvis's schedules.
_NOT_OUR_SCHEDULE = frozenset(
    {"meeting", "meetings", "call", "calls", "appointment", "appointments", "zoom", "interview",
     "visit", "demo", "lunch", "dinner", "coffee", "event", "events", "session", "review",
     "reminder", "reminders"}
)
_RECURRING_HINTS = frozenset(
    {"every", "each", "daily", "weekly", "nightly", "monthly", "hourly", "weekday", "weekdays",
     "run", "runs", "running", "recurring", "cron", "morning", "evening"}
)
_NOTIFY_WORDS = frozenset(
    {"notification", "notifications", "alert", "alerts", "slack", "telegram", "discord", "phone",
     "message", "messages", "channel"}
)
_APP_NOUNS = frozenset({"app", "apps", "extension", "extensions", "connector", "connectors",
                        "integration", "integrations"})
#: Content IN an app ("I added a Notion page"): the claim is work in the app.
_IN_APP_CONTENT = frozenset(
    {"page", "pages", "database", "databases", "doc", "docs", "document", "documents", "note",
     "notes", "task", "tasks", "issue", "issues", "file", "files", "row", "rows", "entry",
     "entries", "comment", "comments", "message", "messages", "card", "cards", "event",
     "events", "repo", "repository", "branch", "item", "items", "folder", "folders", "pr",
     "invoice", "invoices", "customer", "customers", "draft", "drafts", "email", "emails",
     "workspace", "channel", "post", "posts", "record", "records", "link", "links"}
)
#: An undo claim about code or a file is the coding turn's, never a setting's.
_UNDO_CODE = frozenset(
    {"file", "files", "line", "lines", "function", "commit", "commits", "edit", "edits", "code",
     "patch", "diff", "branch", "test", "tests", "\x00file", "import", "imports", "migration"}
)

# --------------------------------------------------------------------------
# cleaning, sentences, tokens (all linear: every repeat is bounded)
# --------------------------------------------------------------------------

_INLINE_CODE = re.compile(r"`[^`\n]{0,300}`")
_STRAIGHT_QUOTE = re.compile(r'"[^"\n]{0,300}"')
_CURLY_QUOTE = re.compile(r"“[^”\n]{0,300}”")
_FILE_NAME = re.compile(r"\b[\w-]{1,80}\.[A-Za-z0-9]{1,6}\b")
_HYPHEN_IN_WORD = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")
_SPACED_DASH = re.compile(r"\s[-–—]{1,3}\s|[–—]")
_SENTENCE = re.compile(r"[^.!?\n]{1,400}[.!?]{0,8}")
_TOKEN = re.compile(r"[a-z0-9\x00]+(?:'[a-z]{1,3})?|[,;:()—]")


def _bounded(text: str) -> str:
    text = str(text or "")
    if len(text) > MAX_SCAN:
        half = MAX_SCAN // 2
        text = text[:half] + "\n" + text[-half:]
    return text


def _clean(text: str) -> str:
    """The reply as prose the claim scanner reads: no code (fenced or
    inline), no block quote, no quoted words, no note line of ours, file
    names as one placeholder word, dashes as one boundary."""
    lines: list[str] = []
    fenced = False
    for line in _bounded(text).replace("\r", "").split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or stripped.startswith(">") or stripped.startswith("_Note:"):
            continue
        lines.append(line)
    out = "\n".join(lines).replace("’", "'").replace("‘", "'")
    out = _INLINE_CODE.sub(" ", out)
    out = _STRAIGHT_QUOTE.sub(" ", out)
    out = _CURLY_QUOTE.sub(" ", out)
    out = _FILE_NAME.sub(" \x00file ", out)
    out = _HYPHEN_IN_WORD.sub(" ", out)
    return _SPACED_DASH.sub(" — ", out)


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for m in _SENTENCE.finditer(text):
        s = m.group(0).strip()
        if s:
            out.append(s)
            if len(out) >= MAX_SENTENCES:
                break
    return out


def _tokens(sentence: str) -> list[str]:
    return _TOKEN.findall(sentence.lower())


# --------------------------------------------------------------------------
# claims
# --------------------------------------------------------------------------


@dataclass
class Claim:
    """One thing a reply says it changed: the verb's family, the words the
    change was about, and what those words may name."""

    verb: str  # ON, OFF, CHANGE, …
    word: str  # the verb as written ("paused", "turned on")
    phrase: list[str] = field(default_factory=list)
    settings: list[str] = field(default_factory=list)  # schema keys, most likely first
    records: list[str] = field(default_factory=list)  # record kinds
    apps: list[str] = field(default_factory=list)  # catalog ids ("" = an app, unnamed)
    local_only: bool = False
    undo: bool = False

    def named(self) -> bool:
        return bool(self.settings or self.records or self.apps or self.local_only or self.undo)

    def candidates(self) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for k in self.settings:
            out |= {("setting", f) for f in _family(k)}
        out |= {("record", r) for r in self.records}
        out |= {("app", a) for a in self.apps}
        if self.local_only:
            out.add(("local_only", ""))
        if self.undo:
            out.add(("undo", ""))
        return out


def _earlier(toks: list[str]) -> bool:
    """The sentence points back to an earlier turn: "earlier", "already",
    "as I said", "in my last reply"…"""
    if _EARLIER.intersection(toks):
        return True
    for i in range(len(toks) - 2):
        if toks[i] == "as" and toks[i + 1] in ("i", "we") and toks[i + 2] in (
            "said", "mentioned", "noted", "explained", "told", "did"
        ):
            return True
        if toks[i] == "other" and toks[i + 1] == "day":
            return True
    return False


def _negated(toks: Iterable[str]) -> bool:
    for t in toks:
        if t in ("not", "never", "no", "nothing") or t.endswith("n't") or t == "cannot":
            return True
    return False


def _clause_start(toks: list[str], i: int) -> int:
    j = i - 1
    while j >= 0 and toks[j] not in _BOUNDARY:
        j -= 1
    return j + 1


def _clause_end(toks: list[str], i: int) -> int:
    j = i
    stop = min(len(toks), i + MAX_OBJECT_TOKENS)
    while j < stop and toks[j] not in _BOUNDARY:
        j += 1
    return j


def _assistant_subject(toks: list[str], start: int, verb_at: int, verb: str) -> bool:
    """Is the assistant the one claiming the change — "I've …", a bare past
    verb at a sentence start or after "Done —", or a verb joined to an
    earlier "I …" in the same sentence?"""
    pre = toks[start:verb_at]
    if any(t in _NOT_A_CLAIM_BEFORE for t in pre):
        return False
    core = [t for t in pre if t not in _ADVERBS]
    if core and (len(core) != 1 or core[0] not in _SUBJECTS):
        return False
    if core:
        return True
    # A bare verb: at the sentence start (never one that is also an
    # instruction), after an interjection ("Done —"), or joined to an
    # earlier "I …" ("I turned on X and switched Y").
    if start == 0:
        return verb not in _AMBIGUOUS
    before = [t for t in toks[:start] if t not in _BOUNDARY]
    if before and all(t in _INTERJECTIONS for t in before):
        return True
    if toks[start - 1] in ("and", ",", "plus", "then", "—", ";", ":"):
        head = toks[: start - 1]
        return any(t in _SUBJECTS for t in head) and "you" not in head
    return False


def _verb_at(toks: list[str], i: int) -> "tuple[str, str, int] | None":
    """``(family, word as written, index after the verb and its particle)``
    for a claim verb at *i*, else None. "turned read aloud on" reads its
    particle up to three words later."""
    t = toks[i]
    parts = _PARTICLE_VERBS.get(t)
    if parts:
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if nxt in parts:
            return parts[nxt], f"{t} {nxt}", i + 2
        for j in range(i + 2, min(len(toks), i + 5)):
            if toks[j] in _BOUNDARY:
                break
            if toks[j] in parts and t in ("turned", "switched"):
                return parts[toks[j]], f"{t} {toks[j]}", i + 1
    if t in _VERBS:
        return _VERBS[t], t, i + 1
    return None


def _object(toks: list[str], after: int) -> list[str]:
    end = _clause_end(toks, after)
    obj = toks[after:end]
    # "turned read aloud on": drop the trailing particle.
    if obj and obj[-1] in ("on", "off"):
        obj = obj[:-1]
    return obj


def _elsewhere(phrase: list[str]) -> bool:
    """The words point at the user's own content ("in your spreadsheet",
    "of this essay"), never one of Iron Jarvis's records."""
    for i, t in enumerate(phrase):
        if t not in _ELSEWHERE_PREPS:
            continue
        nxt = phrase[i + 1] if i + 1 < len(phrase) else ""
        if nxt in _DETS and i + 2 < len(phrase):
            nxt = phrase[i + 2]
        if nxt in _CONTENT:
            return True
    return False


def _records_in(phrase: list[str], verb_word: str, sentence_toks: list[str]) -> list[str]:
    """The record kinds *phrase* names (a noun with its guards)."""
    out: list[str] = []
    if _elsewhere(phrase):
        return out

    def add(kind: str) -> None:
        if kind not in out:
            out.append(kind)

    for i, t in enumerate(phrase):
        prv = phrase[i - 1] if i > 0 else ""
        nxt = phrase[i + 1] if i + 1 < len(phrase) else ""
        if nxt in _NOT_THE_RECORD_AFTER:
            continue
        if t in ("schedule", "schedules"):
            if nxt not in _NOT_OUR_SCHEDULE and not (nxt in _DETS and i + 2 < len(phrase) and phrase[i + 2] in _NOT_OUR_SCHEDULE):
                add("schedule")
        elif t in ("workflow", "workflows"):
            add("workflow")
        elif t in ("webhook", "webhooks"):
            add("webhook")
        elif t in ("watcher", "watchers", "sentinel", "sentinels"):
            add("watcher")
        elif t in ("goal", "goals") and prv not in ("soccer", "football", "hockey", "field", "own"):
            add("goal")
        elif t in ("rule", "rules") and (
            prv in ("reflex", "automation", "trigger", "signal")
            or (nxt in ("that", "which") and i + 2 < len(phrase) and phrase[i + 2] in (
                "runs", "fires", "starts", "sends", "notifies", "triggers", "watches", "alerts", "will"
            ))
        ):
            add("rule")
        elif t == "always" and nxt == "allow":
            add("grant")
        elif t in ("project", "projects") and (
            _DETS.intersection(phrase[:i]) or nxt in ("called", "named")
        ):
            add("project")
        elif t in ("agent", "agents") and prv not in _NOT_OUR_AGENT_BEFORE and prv != "sub":
            add("agent")
        elif t in ("destination", "destinations") and _NOTIFY_WORDS.intersection(sentence_toks):
            add("destination")
        elif t in ("notification", "notifications", "alert", "alerts"):
            add("destination")
    if verb_word == "scheduled":
        # "I scheduled the triage workflow to run every morning" — a schedule
        # (the thing the claim is about, so it leads); "I scheduled a meeting
        # with Bob" is a calendar's.
        if not _NOT_OUR_SCHEDULE.intersection(phrase) and _RECURRING_HINTS.intersection(phrase):
            if "schedule" in out:
                out.remove("schedule")
            out.insert(0, "schedule")
    return out


def _app_ids(phrase_text: str, verb_word: str, phrase: list[str]) -> list[str]:
    """The catalog apps a connect-family claim names (``connectors.app_names``
    — the chat's own matcher), or ``[""]`` for an unnamed "app"."""
    if "to" in phrase[:1]:
        return []  # "connected to your Notion workspace": it read the app
    if _IN_APP_CONTENT.intersection(phrase):
        return []
    out: list[str] = []
    try:
        from ..connectors.app_names import GENERIC, apps_named

        # An app whose name is an everyday word ("memory", "fetch", "box") is
        # never read from a claim: "I connected the memory of both sessions".
        out = [a for a in apps_named(f"{verb_word} {phrase_text}") if a not in GENERIC]
    except Exception:  # noqa: BLE001 — a matcher fault names nothing
        out = []
    if not out and _APP_NOUNS.intersection(phrase):
        out = [""]
    return out


def _setting_keys(phrase_text: str) -> list[str]:
    """The schema rows *phrase_text* names — the settings trigger's own reading
    of the words (``settings.trigger``, the schema's labels, aliases and
    options), as if a change were asked for."""
    if not phrase_text.strip():
        return []
    # A claim is read stricter than a request: "the model" a reply updated is
    # a financial model, "the browser" a website's — the everyday word names
    # the app's setting only as "your / my / default <word>" (or inside a
    # longer phrase: "the default model", "the Daylight theme").
    text = _THE_EVERYDAY.sub(r"a \1", phrase_text)
    try:
        from ..settings.trigger import settings_ask

        keys = list(settings_ask("change " + text).keys)
    except Exception:  # noqa: BLE001 — names nothing
        return []
    toks = set(phrase_text.split())
    out = []
    for k in keys:
        if "{" in k:
            continue  # a pattern row ("permissions.{tool}") is no one setting
        if k in _MODEL_KEYS and not (toks & _MODEL_WORDS or any(_MODEL_NAME.match(t) for t in toks)):
            continue  # "your model" in a CPA's chat is a spreadsheet's
        out.append(k)
    return out


#: Everyday words a setting is also called: "the <word>" in a claim is
#: something else ("I updated the model to include depreciation").
_THE_EVERYDAY = re.compile(
    r"\bthe\s+(model|models|browser|editor|calendar|voice|brain|proxy|update|updates"
    r"|notification|notifications|approval|setting|settings|preference|preferences"
    r"|accessibility)\b"
)
_MODEL_KEYS = frozenset({"default_model", "default_provider", "routing_model", "ollama_model", "custom_model"})
_MODEL_WORDS = frozenset({"default", "ai", "llm", "local", "provider", "routing", "ollama", "custom"})
_MODEL_NAME = re.compile(
    r"(?:sonnet|opus|haiku|claude|gpt[\w.-]{0,20}|chatgpt|gemini[\w.-]{0,20}|codex|llama[\w.-]{0,20}"
    r"|qwen[\w.-]{0,20}|glm[\w.-]{0,20}|mistral|deepseek|grok|o\d)$"
)


def _head(phrase: list[str]) -> str:
    """The first word of *phrase* that is not a determiner."""
    for t in phrase:
        if t not in _DETS and t not in ("you", "to", "new", "own"):
            return t
    return ""


def _fill(claim: Claim, phrase: list[str], phrase_text: str, sentence_toks: list[str]) -> None:
    claim.phrase = phrase
    if _head(phrase) in _CONTENT:
        return  # "I changed the email to ask first": the user's own content
    if {"preference", "preferences", "memory", "memories", "lesson", "lessons"}.intersection(phrase):
        return  # a kept preference is memory (``remember_preference``), not a setting
    if "local" in phrase and "only" in phrase and phrase.index("local") + 1 < len(phrase) and (
        phrase[phrase.index("local") + 1] == "only"
    ):
        claim.local_only = True
    if claim.verb in _KIND_VERBS["record"]:
        claim.records = _records_in(phrase, claim.word, sentence_toks)
    if claim.verb in _KIND_VERBS["app"]:
        claim.apps = _app_ids(phrase_text, claim.word.split()[0], phrase)
    if claim.verb in _KIND_VERBS["setting"] and not claim.local_only:
        claim.settings = _setting_keys(phrase_text)
    if claim.local_only and claim.verb not in _KIND_VERBS["local_only"]:
        claim.local_only = False
    if claim.local_only:
        # "Local only for new chats" is the setting, not this chat's switch.
        if {"new", "default"}.intersection(phrase):
            claim.settings = [_LOCAL_ONLY_NEW_KEY]


def _state_family(pred: list[str]) -> str:
    first = pred[0] if pred else ""
    if first in _STATE_ON:
        return ON
    if first in _STATE_OFF:
        return OFF
    if first in _STATE_PAUSE:
        return PAUSE
    if first in _STATE_REMOVE:
        return REMOVE
    if first in _STATE_CREATE:
        return CREATE
    if first == "connected":
        return CONNECT
    if first == "disconnected":
        return DISCONNECT
    if first == "set" and (len(pred) > 1 and pred[1] == "up"):
        return CREATE
    return CHANGE


def _claims_in_sentence(sentence: str) -> list[Claim]:
    """Every change claim one sentence makes. Never raises."""
    if sentence.endswith("?"):
        return []
    toks = _tokens(sentence)[: MAX_SENTENCE]
    if not toks or _earlier(toks):
        return []
    out: list[Claim] = []
    used: set[int] = set()
    # "Undone." / "Reverted." on their own.
    core = [t for t in toks if t not in _INTERJECTIONS and t not in _BOUNDARY]
    if core in (["undone"], ["reverted"], ["rolled", "back"]):
        return [Claim(verb=UNDO, word=core[0], undo=True)]
    # Passive and state forms: "<subject> is now <state>", "<subject> has been
    # <verb>", "<subject> was <verb>".
    for k, t in enumerate(toks):
        if len(out) >= MAX_CLAIMS:
            break
        if t not in ("is", "are", "was", "were", "has", "have", "it's"):
            continue
        start = _clause_start(toks, k)
        subj = toks[start:k]
        if t == "it's":
            subj = ["it"]
        if not subj or len(subj) > MAX_SUBJECT_TOKENS:
            continue
        if any(w in ("you", "you've", "you're", "i", "we", "if", "when", "once", "whether",
                     "how", "what", "why", "unless", "after", "until") for w in subj):
            continue
        rest = toks[k + 1 : _clause_end(toks, k + 1)]
        if not rest or _negated(rest[:3]):
            continue
        claim: "Claim | None" = None
        if t in ("is", "are", "it's") and rest[0] == "now" and len(rest) > 1:
            pred = rest[1:]
            if pred[0] in _DESCRIPTIVE:
                continue
            claim = Claim(verb=_state_family(pred), word=pred[0])
            phrase = subj + ["to"] + pred
        elif t in ("is", "are") and len(rest) >= 2 and rest[1] == "now" and (
            rest[0] in _STATE_ON | _STATE_OFF | _STATE_PAUSE or rest[0] in ("connected", "disconnected")
        ):
            claim = Claim(verb=_state_family(rest[:1]), word=rest[0])
            phrase = list(subj)
        elif t in ("is", "are") and rest[:2] == ["all", "set"]:
            claim = Claim(verb=CREATE, word="set up")
            phrase = list(subj)
        elif t in ("has", "have") and rest[0] == "been" and len(rest) > 1:
            j = 1
            while j < len(rest) and rest[j] in ("successfully", "now", "also", "just"):
                j += 1
            v = _verb_at(rest, j) if j < len(rest) else None
            if v is None:
                continue
            claim = Claim(verb=v[0], word=v[1])
            phrase = subj + rest[v[2]:]
        elif t in ("was", "were"):
            j = 0
            while j < len(rest) and rest[j] in ("successfully", "now", "also", "just"):
                j += 1
            v = _verb_at(rest, j) if j < len(rest) else None
            if v is None:
                continue
            claim = Claim(verb=v[0], word=v[1])
            phrase = subj + rest[v[2]:]
        if claim is None:
            continue
        if "by" in phrase:
            # "… was created by your accountant": someone else's change.
            after_by = phrase[phrase.index("by") + 1 : phrase.index("by") + 2]
            if after_by and after_by[0] not in ("me", "jarvis", "iron"):
                continue
        if claim.verb == UNDO:
            if {"change", "changes", "that", "it", "this"}.intersection(subj) and not _UNDO_CODE.intersection(subj):
                claim.undo = True
                out.append(claim)
            continue
        if any(w in _NOT_A_CLAIM_BEFORE - {"your", "this", "that", "it"} for w in subj):
            continue
        _fill(claim, phrase, " ".join(phrase), toks)
        if claim.named():
            out.append(claim)
            used.update(range(start, k + 1))
    # Active claims: "I've turned on read aloud", "Turned on Auto tools".
    i = 0
    while i < len(toks) and len(out) < MAX_CLAIMS:
        if i in used:
            i += 1
            continue
        v = _verb_at(toks, i)
        if v is None:
            i += 1
            continue
        family, word, after = v
        start = _clause_start(toks, i)
        if not _assistant_subject(toks, start, i, toks[i]):
            i = after
            continue
        obj = _object(toks, after)
        claim = Claim(verb=family, word=word)
        if family == UNDO:
            if not _UNDO_CODE.intersection(obj):
                claim.undo = True
                out.append(claim)
            i = after
            continue
        _fill(claim, obj, " ".join(obj), toks)
        if claim.named():
            out.append(claim)
        i = after
    return out


def claims_in(text: str) -> list[Claim]:
    """Every setting or record change *text* claims (see the module
    docstring). Pure and bounded; never raises."""
    try:
        out: list[Claim] = []
        for s in _sentences(_clean(text)):
            out.extend(_claims_in_sentence(s))
            if len(out) >= MAX_CLAIMS:
                break
        return out[:MAX_CLAIMS]
    except Exception:  # noqa: BLE001 — a fault claims nothing
        return []


# --- "this reply was kept Local only"

_THIS_REPLY = r"(?:this|the\s+current)\s+(?:reply|answer|response)"
_LOCAL_ONLY = r"local\s+only"
_ON_THIS_PC = r"on\s+(?:your|this)\s+(?:own\s+)?(?:pc|computer|machine|device|network)"
_THIS_REPLY_LOCAL = (
    re.compile(
        rf"\b{_THIS_REPLY}\s+(?:is|was|will\s+be|stays|stayed|remains|remained|runs|ran|is\s+being)"
        rf"\s+(?:now\s+|also\s+)?(?:kept\s+|written\s+|answered\s+|running\s+)?"
        rf"(?:{_LOCAL_ONLY}|{_ON_THIS_PC})\b"
    ),
    re.compile(
        rf"\b{_LOCAL_ONLY}\b[^.!?\n]{{0,60}}?\b(?:applies|is\s+on|is\s+active|is\s+in\s+effect|covers"
        rf"|includes|takes\s+effect)\s+(?:to\s+|for\s+)?{_THIS_REPLY}\b"
    ),
    re.compile(rf"\b{_LOCAL_ONLY}\b[^.!?\n]{{0,60}}?\b(?:including|starting\s+with|from)\s+{_THIS_REPLY}\b"),
    re.compile(
        rf"\b{_LOCAL_ONLY}\b[^.!?\n]{{0,40}}?\b(?:immediately|right\s+away|starting\s+now"
        r"|takes\s+effect\s+now|effective\s+(?:immediately|now)|applies\s+now)\b"
    ),
)


def claims_this_reply_local(text: str) -> bool:
    """Does *text* say THIS reply was kept Local only (or stays on the PC
    because of it)? A question, a denial and "from your next message" never
    count. Never raises."""
    try:
        for s in _sentences(_clean(text)):
            if s.endswith("?"):
                continue
            low = s.lower()
            if "local" not in low:
                continue  # every form names Local only itself
            toks = _tokens(low)
            if _negated(toks) or "next" in toks or "will" in toks and "be" not in toks:
                continue
            if any(rx.search(low) for rx in _THIS_REPLY_LOCAL):
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------

#: Settings a reply may name for one another ("your persona" for the chat's
#: own persona, "your model" for the provider and the model).
_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"default_model", "default_provider"}),
    frozenset({"default_persona", "device.chat_persona"}),
    frozenset({"device.approval_mode", "mcp_auto_approve"}),
    frozenset({"iron_proxy_enabled", "account_groups", "accounts_share_claude_setup"}),
    frozenset({"sentinels_enabled", "sentinels_tick_seconds"}),
    frozenset({"chat_new_local_only"}),
)
#: Keys sharing these first words are one family too ("autonomy_level" and
#: "autonomy_dry_run" are both "autonomy").
_PREFIX_FAMILIES = frozenset(
    {"autonomy", "night", "curator", "calendar", "media", "fleet", "voice", "routing", "team",
     "profile", "ollama", "custom", "backup", "self", "skill", "opencode", "pi", "build", "ci",
     "local", "decompose", "busy", "max"}
)


def _prefix(key: str) -> str:
    return re.split(r"[._]", key, maxsplit=1)[0]


def _family(key: str) -> set[str]:
    out = {key}
    for fam in _FAMILIES:
        if key in fam:
            out |= fam
    pre = _prefix(key)
    if pre in _PREFIX_FAMILIES:
        out.add(f"prefix:{pre}")
    return out


@dataclass
class TurnRecord:
    """What THIS turn really changed, from the calls that succeeded and the
    cards they produced."""

    used: frozenset[str]
    setting_keys: set[str]
    settings_unknown: bool
    record_card_keys: list[str]
    app_waiting: set[str]  # catalog ids whose only card waits for a token
    app_good: set[str]
    secret_card: bool
    undo_ran: bool

    @classmethod
    def build(cls, tools_used: Iterable[str], config_cards: Iterable[Any]) -> "TurnRecord":
        used = frozenset(str(t) for t in tools_used or ())
        setting_keys: set[str] = set()
        record_keys: list[str] = []
        waiting: set[str] = set()
        good: set[str] = set()
        secret = False
        for c in config_cards or ():
            if not isinstance(c, dict):
                continue
            if c.get("kind") == "secret":
                secret = True
                continue
            if c.get("kind") != "change" or not c.get("key"):
                continue
            key = str(c["key"])
            if c.get("title"):
                record_keys.append(key)
                if key.startswith("app."):
                    app_id = key[4:]
                    if str(c.get("new") or "").startswith(_WAITING_STATE):
                        waiting.add(app_id)
                    else:
                        good.add(app_id)
            else:
                setting_keys.add(key)
        return cls(
            used=used,
            setting_keys=setting_keys,
            settings_unknown=bool(used & _SETTINGS_WRITERS) and not setting_keys,
            record_card_keys=record_keys,
            app_waiting=waiting - good,
            app_good=good,
            secret_card=secret,
            undo_ran=bool(used & _UNDO_TOOLS),
        )

    def _setting_families(self) -> set[str]:
        out: set[str] = set()
        for k in self.setting_keys:
            out |= _family(k)
        return out

    def answers(self, claim: Claim) -> bool:
        """Does a change this turn made answer *claim*? Any one of the kinds
        the claim may name is enough (a union — the conservative side)."""
        if self.undo_ran and not claim.undo:
            return True  # an undo this turn may have changed anything
        if claim.undo:
            return self.undo_ran
        fams = self._setting_families()
        for k in claim.settings:
            if self.settings_unknown or _family(k) & fams:
                return True
            if k == "active_project_id" and "project_focus" in self.used:
                return True
        for kind in claim.records:
            if self.used & _RECORD_TOOLS.get(kind, frozenset()):
                return True
            if any(key.startswith(_RECORD_CARD_PREFIX.get(kind, ("\x00",))) for key in self.record_card_keys):
                return True
        if claim.apps:
            if any(n.startswith("mcp__") for n in self.used):
                return True  # an app's own tool ran: it is connected
            removing = claim.verb in (DISCONNECT, REMOVE, OFF)
            if removing and self.used & _APP_REMOVERS:
                return True
            if not removing:
                if self.used & (_APP_CONNECTORS - {"app_connect"}):
                    return True
                for a in claim.apps:
                    if (a and a in self.app_good) or (not a and self.app_good):
                        return True
                if any(k.startswith("channel.") for k in self.record_card_keys):
                    return True
        if claim.local_only:
            if _LOCAL_ONLY_TOOL in self.used or _LOCAL_ONLY_NEW_KEY in self.setting_keys:
                return True
        return False

    def waiting_app(self, claim: Claim) -> str:
        """The app id a connect claim names whose connection is still WAITING
        for its key on a card this turn put up ("" when none)."""
        if claim.verb not in (CONNECT, ON, CHANGE) or not claim.apps:
            return ""
        for a in claim.apps:
            if a and a in self.app_waiting:
                return a
        if self.secret_card and "app_connect" in self.used:
            return claim.apps[0] or "\x00app"
        return ""


# --------------------------------------------------------------------------
# the note
# --------------------------------------------------------------------------


def _setting_label(claim: Claim) -> str:
    """The schema label of the setting *claim* most likely names: the one
    whose own words overlap the claim's words most (ties: the trigger's
    order, most specific first)."""
    try:
        from ..settings import schema

        best, best_score = "", -1
        words = set(claim.phrase)
        for k in claim.settings:
            d = schema.BY_KEY.get(k)
            if d is None:
                continue
            mine = set(re.findall(r"[a-z0-9]+", " ".join([d.label, *d.aliases]).lower()))
            score = len(words & mine)
            if score > best_score:
                best, best_score = d.label, score
        return best
    except Exception:  # noqa: BLE001
        return ""


def _app_name(app_id: str) -> str:
    if not app_id or app_id == "\x00app":
        return ""
    try:
        from ..connectors.catalog import CATALOG

        for c in CATALOG:
            if c.id == app_id:
                return c.name.split(" (")[0]
    except Exception:  # noqa: BLE001
        pass
    return ""


#: The participle each family says when the claim's own word is no verb (a
#: state: "your schedule is now set to …", "Notion is now live").
_FAMILY_WORD = {
    ON: "turned on", OFF: "turned off", CHANGE: "changed", CREATE: "created", REMOVE: "removed",
    PAUSE: "paused", CONNECT: "connected", DISCONNECT: "disconnected", UNDO: "undone",
}


def _participle(claim: Claim) -> str:
    word = claim.word
    if word == "scheduled":
        return "created"
    if claim.verb == CHANGE and word in ("switched", "made", "put", "moved", "configured", "set"):
        return "changed"
    if claim.verb == CREATE and word in ("built", "made", "saved", "added", "set up"):
        return "created" if word != "set up" else "set up"
    if word in _VERBS or word.split()[0] in _PARTICLE_VERBS:
        return word
    if claim.verb in (PAUSE, REMOVE) and word in _STATE_PAUSE | _STATE_REMOVE:
        return word
    return _FAMILY_WORD.get(claim.verb, "changed")


def sentence_for(claim: Claim, record: "TurnRecord | None" = None) -> str:
    """The note's one sentence for an unanswered *claim* — the record first
    (a record noun is the thing the clause is about), then an app, Local
    only, the setting."""
    if claim.undo:
        return f"Nothing was undone {IN_THIS_REPLY}."
    waiting = record.waiting_app(claim) if record is not None else ""
    if waiting:
        name = _app_name(waiting) or "The app"
        return (
            f"{name} is not connected yet: it waits for its key on the card {IN_THIS_REPLY}."
        )
    if claim.records:
        return f"No {_RECORD_NOUN.get(claim.records[0], 'record')} was {_participle(claim)} {IN_THIS_REPLY}."
    if claim.apps:
        name = _app_name(claim.apps[0])
        if name:
            return f"{name} was not {_participle(claim)} {IN_THIS_REPLY}."
        return f"No app was {_participle(claim)} {IN_THIS_REPLY}."
    if claim.local_only:
        return f"Local only was not changed {IN_THIS_REPLY}."
    label = _setting_label(claim)
    if label:
        return f"“{label}” was not changed {IN_THIS_REPLY}."
    return ""


THIS_REPLY_NOT_LOCAL = "This reply was not kept Local only"
NEXT_MESSAGE = "; Local only applies from your next message"


def _excused(history: Iterable[Any]) -> set[tuple[str, str]]:
    """The kinds an EARLIER assistant message in this conversation claimed —
    this reply may be summarising them. A claim that message's own note said
    was not made does not count."""
    out: set[tuple[str, str]] = set()
    msgs = [m for m in (history or ()) if str(getattr(m, "role", "") or "") == "assistant"]
    for m in msgs[-MAX_HISTORY_MESSAGES:]:
        content = getattr(m, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        text = content[-MAX_HISTORY_CHARS:]
        noted = " ".join(
            ln for ln in text.split("\n") if ln.lstrip().startswith("_Note:") and IN_THIS_REPLY in ln
        ).lower()
        for c in claims_in(text):
            if noted and sentence_for(c).lower().rstrip(".") in noted:
                continue
            out |= c.candidates()
    return out


def _earlier_turns(history: Iterable[Any]) -> list[Any]:
    """The messages BEFORE this turn's question (the last user message)."""
    msgs = list(history or ())
    for i in range(len(msgs) - 1, -1, -1):
        if str(getattr(msgs[i], "role", "") or "") == "user":
            return msgs[:i]
    return msgs


def claimed_change_note(
    reply: str,
    tools_used: Iterable[str],
    config_cards: Iterable[Any] = (),
    *,
    history: Iterable[Any] = (),
    custom_tools: Iterable[str] = (),
    reply_local_only: "bool | None" = None,
) -> str:
    """'' unless the REPLY claims a setting or a record changed that no
    successful call of this turn changed (see the module docstring). The
    note is one line: ``\\n\\n_Note: <sentence> <sentence>_``.

    *tools_used*: the calls that SUCCEEDED this turn; *config_cards*: the
    cards they produced (``chat_turn.config_cards_from_result``); *history*:
    the request's messages (only the assistant messages before this turn's
    question are read); *custom_tools*: the user-made tools that ran
    (``chat_turn.custom_tools_used``); *reply_local_only*: whether this reply
    ran Local only (None = read the turn's own scope,
    ``egress.current_scope()``). MIRROR NOTE (lock-step): both chat lanes.
    Never raises."""
    try:
        return _note(reply, tools_used, config_cards, history, custom_tools, reply_local_only)
    except Exception:  # noqa: BLE001 — a note never breaks a turn
        return ""


def _note(reply, tools_used, config_cards, history, custom_tools, reply_local_only) -> str:
    if not reply or not str(reply).strip():
        return ""
    record = TurnRecord.build(tools_used, config_cards)
    said: list[str] = []
    # THIS reply kept Local only? Nothing a tool does changes the scope a
    # reply started with, so this is judged after any tool.
    if reply_local_only is None:
        try:
            from ..providers import egress

            scope = egress.current_scope()
            reply_local_only = bool(scope.on and not scope.allowed_once)
        except Exception:  # noqa: BLE001 — unknown: no note
            reply_local_only = True
    if not reply_local_only and claims_this_reply_local(reply):
        said.append(
            THIS_REPLY_NOT_LOCAL + (NEXT_MESSAGE if _LOCAL_ONLY_TOOL in record.used else "") + "."
        )
    custom = {str(c) for c in custom_tools or ()}
    if record.used & _MAY_CHANGE_ANYTHING or record.used & custom or any(
        n.startswith("custom:") for n in record.used
    ):
        return _line(said)
    try:
        from .chat_turn import _FILE_CHANGING_TOOLS
    except Exception:  # noqa: BLE001
        _FILE_CHANGING_TOOLS = frozenset()  # type: ignore[assignment]
    coding = bool(record.used & _FILE_CHANGING_TOOLS)
    claims = claims_in(reply)
    if not claims:
        return _line(said)
    excused = _excused(_earlier_turns(history))
    for claim in claims:
        if coding:
            # A turn that changed files talks about code: its "I updated the
            # scheduler" is never one of Iron Jarvis's schedules.
            claim.records, claim.apps, claim.undo = [], [], False
            if not claim.named():
                continue
        if record.answers(claim):
            continue
        if claim.candidates() & excused:
            continue
        s = sentence_for(claim, record)
        if s and s not in said:
            said.append(s)
        if len(said) >= MAX_SAID:
            break
    return _line(said)


def _line(said: list[str]) -> str:
    if not said:
        return ""
    return "\n\n_Note: " + " ".join(said[:MAX_SAID]) + "_"


__all__ = [
    "Claim",
    "IN_THIS_REPLY",
    "TurnRecord",
    "claimed_change_note",
    "claims_in",
    "claims_this_reply_local",
    "sentence_for",
]
