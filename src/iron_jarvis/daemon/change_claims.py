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
on", "…because you changed it in Settings"); quoted words, code, a block
quote; how a setting works ("When read aloud is on, …"); a sentence that
points back to an EARLIER turn ("earlier", "already", "yesterday", "as I
said", "in my last reply"); and a claim an EARLIER assistant message in this
conversation already made — same kind, same direction, no other value (the
reply may be summarising it). A claim the earlier reply's own note marked as
not made, and another agent's line in the chat, are no excuse; "…to
Midnight" after "…to Daylight", or "turned off" after "turned on", is a NEW
claim.

REVIEW (Wave 5 group K) — what a record or a setting is, read narrowly: a
record is the HEAD of the object's first noun phrase ("the notification
email template" is a template, "a schedule of payments" a document), never a
CPA's or a person's schedule ("the depreciation schedule", "Schedule C",
"your schedule"), never another product's ("a GitHub Actions workflow", "a
project in Asana", "the alerts in Datadog"), never in a sentence about the
user's own content ("In the story, I created a rogue agent"). A setting
claim never points into content or a file, is never code ("dark mode
support", "…in config.py") and is never a thing MADE ("a Google Calendar
reminder"). An undo is only of "that change" / "it" / a record. After an
action in another app or on a web page (an app's own tool, a click, typing)
no record, setting or undo claim is judged. "Done!" / "You're all set —"
before a plain state ("the nightly schedule is gone", "Sonnet is your
default") makes it a claim.

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
    # v1.438.0 (Wave 4 group C, settings/records_self): a kept preference or
    # lesson (``remember_preference`` keeps one too), and a chat persona.
    "preference": frozenset(
        {"preference_edit", "preference_forget", "lesson_add", "remember_preference",
         # …or kept in another of the user's memories ("saved to memory").
         "memory_write", "memory_propose", "ltm_append", "brain_save", "notebook"}
    ),
    "persona": frozenset({"persona_create", "persona_update", "persona_delete"}),
    # v1.438.0 (Wave 4 group A, settings/records_build): the night shift and a
    # folder's defaults and coding team for new coding sessions.
    "night_task": frozenset({"night_task_add", "night_task_cancel", "night_task_move"}),
    "coding_defaults": frozenset({"build_defaults_set", "build_defaults_clear"}),
    "coding_team": frozenset({"build_team_set", "build_team_clear"}),
    # v1.438.0 (chat-first plan Wave 4 group B, settings/records_connections).
    "model_server": frozenset({"model_server_add", "model_server_remove"}),
    "account": frozenset({"account_add"}),
    "memory_base": frozenset({"memory_base_add"}),
    # v1.448.0 (Wave A item 4, settings/records_tools): a tool server (an MCP
    # server — ``app_import`` and ``app_connect`` add one too) and a web
    # service (a REST hookup).
    "tool_server": frozenset({"mcp_server_add", "app_import", "app_connect", "app_disconnect"}),
    "web_service": frozenset({"web_service_add"}),
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
    "preference": ("preference.",),
    "persona": ("persona.",),
    "night_task": ("night.",),
    "coding_defaults": ("build_defaults.",),
    "coding_team": ("build_team.",),
    "model_server": ("model_server.", "model_default."),
    "account": ("account.",),
    "memory_base": ("memory_base.", "memory_profile."),
    "tool_server": ("tool_server.", "app."),
    "web_service": ("web_service.",),
}
#: v1.438.0 (Wave 4 group B): a record card that ALSO changed a setting (a
#: model server made the default, a memory base made the memory profile's) —
#: its key's prefix and the settings it answers.
_CARD_SETTINGS: dict[str, tuple[str, ...]] = {
    "model_default.": ("default_model", "default_provider"),
    "memory_profile.": ("brain_profile",),
}
_APP_CONNECTORS = frozenset({"app_connect", "app_switch", "app_import", "channel_connect", "channel_toggle"})
#: v1.438.0 review: ``memory_base_add`` connects ONE app — Notion (a notes
#: folder is no app) — so it backs "I connected your Notion" and nothing
#: else; in :data:`_APP_CONNECTORS` it backed every app ("I connected your
#: Gmail" after a memory base was added got no note).
_MEMORY_BASE_APPS = frozenset({"notion"})
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
    "preference": "preference",
    "persona": "persona",
    "night_task": "night task",
    "coding_defaults": "folder default for new coding sessions",
    "coding_team": "coding team",
    "model_server": "model server",
    "account": "account",
    "memory_base": "memory base",
    "tool_server": "tool server",
    "web_service": "web service",
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
    "canceled": REMOVE, "revoked": REMOVE, "forgotten": REMOVE, "erased": REMOVE,
    "cleared": REMOVE,
    # v1.438.0 (Wave 4 group A): "I've queued it for tonight".
    "queued": CREATE,
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
_STATE_CREATE = frozenset({"created", "scheduled", "saved", "added", "queued"})  # v1.438.0: "is now queued"

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
     "sales", "secret", "free", "double", "enrollment", "transfer", "leasing", "ai", "an",
     # Review (Wave 5 group K): a story's, a firm's or a call centre's agents.
     "rogue", "undercover", "fbi", "cia", "special", "field", "enemy", "sleeper", "government",
     "federal", "talent", "literary", "ticket", "registered", "human", "live", "service", "call",
     "tax", "crewai", "langchain", "autogen"}
)
#: The user's own content: a record noun "in your spreadsheet" is something
#: else ("a schedule in your spreadsheet", "the goal of this essay").
_CONTENT_HEAD = frozenset(
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
#: …and, behind a preposition, more of it (Review, Wave 5 group K): a story,
#: a tax return, a CPA's work papers, someone else's system ("removed the
#: agent from the support queue"). Not read as a claim's FIRST word: "the
#: account groups setting" is one of the app's.
_CONTENT = _CONTENT_HEAD | frozenset(
    {
        "story", "stories", "novel", "plot", "scene", "chapter", "character", "characters",
        "game", "resume", "cv", "return", "returns", "proposal", "form", "forms", "budget",
        "forecast", "workpaper", "workpapers", "statement", "statements", "csv", "excel", "pdf",
        "template", "templates", "queue", "ticket", "tickets", "account", "accounts",
        "workspace", "org", "organization", "company", "portal", "crm", "config",
        "configuration", "server", "campaign", "board",
        # v1.438.0 review: "I added the GPU box to the asset inventory".
        "inventory", "inventories",
    }
)
#: Review (Wave 5 group K): "to", "with" and "using" too — "I've added three
#: goals TO your plan", "an agent USING LangChain". Never "for": "a schedule
#: FOR your weekly report" is one of Iron Jarvis's schedules.
_ELSEWHERE_PREPS = frozenset({"in", "of", "inside", "into", "from", "on", "within", "to", "with", "using"})
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

# --- Review (Wave 5 group K): the guards the first cut lacked. Each one was a
# FALSE note on a true reply, measured on replies outside the builder's table.

#: The words that END an object's first noun phrase. A record is what a claim
#: is about only as that phrase's HEAD (its last word): "the notification
#: email template" is a template, "a cron schedule" (to the workflow file) a
#: schedule, "the agent persona" a persona, "the project board" a board.
_NP_END = _BOUNDARY | frozenset(
    {"in", "of", "inside", "into", "from", "on", "within", "to", "for", "with", "at", "by",
     "about", "under", "over", "that", "which", "who", "whose", "so", "using", "via", "across",
     "between", "per", "after", "before", "when", "since", "called", "named", "titled",
     "each", "every", "if", "once", "until", "unless"}
)
#: Before "schedule": someone else's schedule — a CPA's (depreciation,
#: amortization, payment, lead…), a person's (work, workout, class…), never
#: one of Iron Jarvis's. "Schedule C" never reaches here: its head is "c".
_NOT_OUR_SCHEDULE_BEFORE = frozenset(
    {
        "depreciation", "amortization", "amortisation", "payment", "payments", "repayment",
        "loan", "payroll", "tax", "fee", "fees", "rate", "rates", "price", "pricing", "work",
        "workout", "training", "class", "exam", "shift", "school", "bus", "train", "flight",
        "delivery", "shipping", "production", "filing", "billing", "invoice", "invoicing",
        "vesting", "medication", "feeding", "cleaning", "study", "reading", "posting",
        "content", "editorial", "release", "publishing", "project", "engagement", "audit",
        "closing", "close", "asset", "assets", "lease", "debt", "interest", "cash", "inventory",
        "supporting", "lead", "fixed", "accrual", "accruals", "prepaid", "rent", "rental",
        "mortgage", "dividend", "distribution", "contribution", "commission", "sleep", "meal",
        "travel", "itinerary", "game", "tour", "court", "hearing",
    }
)
#: Another company's product: a record named with it is THAT product's ("a
#: GitHub Actions workflow", "the alerts in Datadog", "a project in Asana") —
#: and a claim after one of these words, behind a preposition, is work done
#: there. The catalog's own app ids are added below.
_EXTERNAL = frozenset(
    {
        "asana", "jira", "trello", "linear", "clickup", "monday", "basecamp", "airtable",
        "hubspot", "salesforce", "zapier", "ifttt", "n8n", "datadog", "pagerduty", "sentry",
        "stripe", "mailchimp", "shopify", "wordpress", "squarespace", "wix", "quickbooks",
        "qbo", "xero", "freshbooks", "netsuite", "outlook", "teams", "zoom", "calendly",
        "crewai", "langchain", "autogen", "windows", "macos", "linux", "android", "iphone",
        "ios", "excel", "word", "powerpoint", "sheets", "docs", "chrome", "firefox", "safari",
        "aws", "azure", "gcp", "kubernetes", "docker", "jenkins", "circleci", "gitlab",
        "bitbucket", "vercel", "netlify", "heroku", "cloudflare", "twilio", "sendgrid",
        "intercom", "zendesk", "freshdesk", "linkedin", "facebook", "instagram", "twitter",
        "youtube", "tiktok", "canva", "figma", "miro", "dropbox", "onedrive", "sharepoint",
        "icloud", "evernote", "todoist", "gusto", "adp", "paychex", "expensify", "ramp",
        "brex", "plaid", "paypal", "venmo", "docusign", "adobe", "acrobat", "lacerte",
        "ultratax", "drake", "proconnect", "cch", "axcess", "github", "gmail", "notion",
        "slack", "microsoft", "postgres", "supabase", "firebase", "mongodb",
        # …and the languages and frameworks a coding chat builds in.
        "python", "javascript", "typescript", "java", "react", "nextjs", "nodejs",
        "django", "flask", "fastapi", "langgraph", "llamaindex", "sdk", "framework",
        "package", "npm", "pip",
    }
)
#: Apps that ARE Iron Jarvis's notification destinations ("your Slack alerts"
#: is a destination; "a Slack workflow" is Slack's).
_NOTIFY_APPS = frozenset({"slack", "telegram", "discord", "email", "phone", "sms", "whatsapp", "signal"})
#: An action OUTSIDE Iron Jarvis (an app's own tool, a click or typing in a
#: web page or on the desktop): a claim after one may be about THAT change
#: ("I turned on dark mode", "I created the project") and is not judged —
#: the file twin's rule for a command, applied to another app.
#: The change history was READ this turn ("what did you change?"): the
#: reply recaps the ledger — earlier turns, other chats, the settings pages —
#: so its "I turned on read aloud at 3:12" is a report, never this reply's.
_RECAPS_THE_LEDGER = frozenset({"changes_list"})
_ACTS_ELSEWHERE = frozenset(
    {"browser_click", "browser_type", "browser_press_key", "browser_fill_form", "browser_navigate",
     "page_click", "page_type", "web_action", "browse", "desktop"}
)
#: Words in a SETTING claim's object that make it code ("dark mode support",
#: "the default model in the config"): never one of the app's settings.
_CODE_WORDS = frozenset(
    {
        "support", "component", "components", "css", "html", "stylesheet", "function",
        "functions", "class", "classes", "endpoint", "endpoints", "route", "routes",
        "flag", "flags", "template", "templates", "config", "env", "variable",
        "variables", "constant", "constants", "property", "properties", "prop", "props",
        "schema", "code", "script", "scripts", "module", "modules", "\x00file", "test", "tests",
        "reducer", "middleware", "migration", "migrations", "dockerfile", "yaml", "json",
        "readme", "repo", "repository", "branch", "commit", "column", "columns", "query",
        "handler", "handlers", "hook", "hooks", "jsx", "tsx", "frontend", "backend",
    }
)
#: An undo's object: what Iron Jarvis's own Undo reverses — "that", "it",
#: "the change", "your theme change". "I rolled back the release" and "I
#: reverted the migration" are someone else's.
_UNDO_OBJECT = frozenset(
    {"it", "that", "this", "change", "changes", "last", "latest", "previous", "recent", "your",
     "my", "the", "a", "setting", "settings", "for", "you", "me", "just", "now", "so", "again",
     "one", "update"}
)
#: The verb a user's own action is told with: "…because YOU CHANGED it in
#: Settings" — the sentence reports the user's change, never Jarvis's.
_USER_ACTION_VERBS = frozenset(
    {"turned", "switched", "set", "changed", "picked", "chose", "selected", "clicked", "pressed",
     "toggled", "flipped", "made", "updated", "enabled", "disabled", "created", "added",
     "deleted", "removed", "paused", "connected", "disconnected", "archived", "saved", "moved"}
)
#: A date in a claim's own words (before any "that runs…"): a report of an
#: EARLIER change ("…was created on October 3", "I paused it on Monday").
_MONTHS = frozenset(
    {"january", "february", "march", "april", "june", "july", "august", "september", "october",
     "november", "december", "jan", "feb", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
     "dec"}
)
_WEEKDAYS = frozenset({"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"})
_DATE_PREPS = frozenset({"on", "in", "during"})
_DATE_STOP = frozenset({"that", "which", "who", "to", "runs", "run", "will", "every", "each", "so"})
_YEAR = re.compile(r"(?:19|20)\d\d$")
_ORDINAL = re.compile(r"\d{1,2}(?:st|nd|rd|th)$")
#: An offer, a future or a condition in a "this reply is Local only" sentence.
_OFFER_WORDS = frozenset(
    {"can", "could", "i'll", "we'll", "i'd", "we'd", "would", "should", "shall", "may", "might",
     "want", "wants", "if", "once", "when", "whenever", "let", "let's", "lets", "try", "going",
     "gonna", "able", "ready", "new"}
)
#: A completion said on its own ("Done!", "You're all set —"): the state
#: after it ("the nightly schedule is gone", "Sonnet is your default") is a
#: claim even with no "now".
_DONE_LEADS: tuple[tuple[str, ...], ...] = (
    ("done",), ("all", "done"), ("all", "set"), ("you're", "all", "set"),
    ("you", "are", "all", "set"), ("that's", "done"), ("thats", "done"), ("finished",),
    ("there", "you", "go"), ("ok", "done"), ("okay", "done"), ("all", "finished"),
)
_DONE_STATES = (
    _STATE_ON | _STATE_OFF | _STATE_PAUSE | _STATE_REMOVE | frozenset({"connected", "disconnected"})
)

# --------------------------------------------------------------------------
# cleaning, sentences, tokens (all linear: every repeat is bounded)
# --------------------------------------------------------------------------

_INLINE_CODE = re.compile(r"`[^`\n]{0,300}`")
_STRAIGHT_QUOTE = re.compile(r'"[^"\n]{0,300}"')
_CURLY_QUOTE = re.compile(r"“[^”\n]{0,300}”")
#: v1.438.0 (Wave 4 group A): a full Windows path ("D:\\code\\shop") — one
#: placeholder word, so its parts ("code", "docs") never read as the user's
#: content.
_WIN_PATH = re.compile(r"\b[A-Za-z]:[\\/][^\s,;()\"'“”]{0,240}")
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
    out = _WIN_PATH.sub(" \x00folder ", out)
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
    #: v1.438.0: a LASTING PROMISE ("I'll answer in Spanish from now on") —
    #: its note names the setting, even when a kept preference answers it too.
    promise: bool = False

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


def _elsewhere(phrase: list[str], *, brands: bool = True) -> bool:
    """The words point at the user's own content or another product ("in
    your spreadsheet", "of this essay", "to your plan", "in Asana", "from
    the support queue"), never one of Iron Jarvis's records. The noun after
    the preposition is read up to three words in (its determiners skipped),
    stopping at the next preposition. *brands* False reads content only (a
    setting's value may be a brand: "your default model to Claude")."""
    return bool(_elsewhere_words(phrase, brands=brands))


def _elsewhere_words(phrase: list[str], *, brands: bool = True) -> set[str]:
    """The words that make :func:`_elsewhere` true (empty when it is not)."""
    out: set[str] = set()
    n = len(phrase)
    for i, t in enumerate(phrase):
        if t not in _ELSEWHERE_PREPS:
            continue
        j = i + 1
        while j < n and j <= i + 2 and phrase[j] in _DETS:
            j += 1
        for k in range(j, min(n, j + 3)):
            w = phrase[k]
            if t == "to" and w in _NOTIFY_APPS:
                break  # "…posts TO Slack": one of Iron Jarvis's destinations
            if w in _CONTENT or (brands and w in _EXTERNAL):
                out.add(w)
                break
            if w in _NP_END:
                break
    return out


#: Words that END a noun phrase but may also OPEN one ("that always-allow",
#: "every schedule").
_LEADS_NP = _DETS | frozenset({"every", "each"})


def _np(phrase: list[str]) -> list[str]:
    """The object's FIRST noun phrase: its words up to the first word that
    ends one (a preposition, a relative, "called"…)."""
    for i, t in enumerate(phrase):
        if t in _NP_END and not (i == 0 and t in _LEADS_NP):  # "that always-allow", "every schedule"
            return phrase[:i]
    return phrase


#: v1.438.0 (Wave 4 group C): before "preference" / "lesson" and "persona",
#: words that make it someone else's ("your seat preference", "a guitar
#: lesson", "the buyer persona").
_NOT_OUR_PREFERENCE_BEFORE = frozenset(
    {"seat", "meal", "dietary", "cookie", "cookies", "privacy", "email", "marketing", "notification",
     "notifications", "browser", "chrome", "edge", "windows", "outlook", "account", "customer", "travel",
     "flight", "hotel", "font", "display", "keyboard", "printer", "system", "sharing", "shipping",
     "guitar", "piano", "music", "driving", "tennis", "swimming", "online", "private", "first", "next",
     "spanish", "french", "math", "history", "science", "student", "students", "valuable", "important"}
)
#: v1.438.0 review: teaching material a lesson is added to.
_COURSE_WORDS = frozenset(
    {"course", "courses", "class", "classes", "curriculum", "syllabus", "module", "workshop", "webinar",
     "seminar", "training", "binder", "workbook", "cpe", "lms", "onboarding", "students", "slides", "deck",
     "outline", "intern", "interns", "staff", "trainees", "textbook", "lecture"}
)
_NOT_OUR_PERSONA_BEFORE = frozenset(
    {"buyer", "customer", "user", "marketing", "target", "audience", "brand", "ideal", "client",
     "shopper", "fictional", "character", "ux", "player", "public", "online", "social", "stage",
     "alter", "narrator's", "agent", "agents", "villain", "hero"}
)

#: A recurring "rule" is a reflex rule: "a rule that sends …".
_RULE_VERBS = frozenset({"runs", "fires", "starts", "sends", "notifies", "triggers", "watches", "alerts", "will"})


def _record_kind(np: list[str], rest: list[str], sentence_toks: list[str]) -> str:
    """The record kind the noun phrase *np* is ABOUT — read off its head (its
    last word) — or "" (Review, Wave 5 group K: the first cut read a record
    noun anywhere in fourteen words, so "the notification email template",
    "a Goals section with three goals" and "the depreciation schedule" were
    each one of Iron Jarvis's records)."""
    if not np:
        return ""
    head = np[-1]
    prv = np[-2] if len(np) > 1 else ""
    mods = np[:-1]
    nxt = rest[0] if rest else ""
    after = rest[1] if len(rest) > 1 else ""
    brand = _EXTERNAL.intersection(mods)
    if nxt in _NOT_THE_RECORD_AFTER:
        return ""  # "a schedule OF payments", "the goal OF this essay": a document's
    conn = _connection_kind(head, prv, mods, np)
    if conn is not None:
        return conn
    if head in ("schedule", "schedules"):
        if prv in _NOT_OUR_SCHEDULE_BEFORE or brand or nxt in _NOT_OUR_SCHEDULE:
            return ""
        if np in (["your", "schedule"], ["my", "schedule"]):
            return ""  # a person's calendar ("I updated your schedule")
        return "schedule"
    if brand and not (head in ("notification", "notifications", "alert", "alerts") and brand <= _NOTIFY_APPS):
        return ""  # "a GitHub Actions workflow", "your Mailchimp campaign agent"
    if head in ("workflow", "workflows"):
        return "workflow"
    if head in ("webhook", "webhooks"):
        return "webhook"
    if head in ("watcher", "watchers", "sentinel", "sentinels"):
        return "watcher"
    if head in ("goal", "goals"):
        return "" if prv in ("soccer", "football", "hockey", "field", "own") else "goal"
    if head in ("rule", "rules"):
        if prv in ("reflex", "automation", "trigger", "signal") or (
            nxt in ("that", "which") and after in _RULE_VERBS
        ):
            return "rule"
        return ""
    if head == "allow" and prv == "always":
        return "grant"
    if head in ("project", "projects"):
        return "project" if (_DETS.intersection(mods) or nxt in ("called", "named")) else ""
    if head in ("agent", "agents"):
        return "" if (prv in _NOT_OUR_AGENT_BEFORE or prv == "sub") else "agent"
    if head in ("preference", "preferences", "lesson", "lessons"):
        # v1.438.0 (Wave 4 group C): a kept preference or lesson — never a
        # seat's, a browser's, a cookie's, a guitar lesson.
        if prv in _NOT_OUR_PREFERENCE_BEFORE or nxt in ("plan", "plans", "learned") or prv.endswith(("'s", "’s")):
            return ""
        # Review (v1.438.0): a lesson added TO a course, a class or a deck
        # ("I added a lesson about depreciation to your CPE course") is a
        # teacher's, never a kept lesson.
        if head.startswith("lesson") and _COURSE_WORDS.intersection(rest[:14]):
            return ""
        return "preference"
    if head in ("persona", "personas"):
        if prv in _NOT_OUR_PERSONA_BEFORE:
            return ""
        # "…a new agent persona FOR THE STORY", "a persona for my campaign":
        # a story's or a plan's, never one of the chat's.
        if nxt == "for" and after not in ("you", "this", "chat", "chats", "jarvis", "iron", "me", "us"):
            return ""
        return "persona"
    if head in ("destination", "destinations"):
        return "destination" if _NOTIFY_WORDS.intersection(sentence_toks) else ""
    if head in ("notification", "notifications", "alert", "alerts"):
        return "destination"
    return ""


#: v1.438.0 (Wave 4 group A): a folder a coding default or team is kept for ("this repo", a full path).
_FOLDER_WORDS = frozenset(
    {"folder", "folder's", "repo", "repo's", "repository", "repository's", "codebase", "codebase's", "\x00folder"}
)
_CODING_KINDS = frozenset({"night_task", "coding_defaults", "coding_team"})
#: The user's own lists, never tonight's ("three tasks on your to-do list
#: for tonight").
_OTHER_LISTS = frozenset({"do", "todo", "todos", "checklist", "calendar", "reminders", "reminder", "planner", "agenda",
                          "notes", "board", "backlog", "sprint", "inbox", "spreadsheet", "sheet"})


def _coding_kinds(phrase: list[str], verb_word: str) -> list[str]:
    """The night shift's and a folder's coding records a claim's words name
    (v1.438.0, Wave 4 group A): "I've queued it for tonight", "I added it to
    tonight's list", "I set Codex as the default for this repo", "I stopped
    using the team for D:\\code\\shop". Never a calendar's evening ("a meeting
    for tonight") and never another company's team."""
    toks = set(phrase)
    out: list[str] = []
    night = bool(toks & {"tonight", "tonight's", "overnight"}) or (
        "night" in toks and bool(toks & {"shift", "list", "queue", "shift's"})
    )
    listed = bool(toks & {"queue", "queued", "task", "tasks", "shift", "list"})
    # v1.438.0 review: "I've queued the print job for tonight", "I queued your
    # email to send tonight" — another program's queue, never the night shift.
    other_work = not listed and bool(toks & _NOT_NIGHT_WORK)
    if night and not (_NOT_OUR_SCHEDULE & toks) and not (_OTHER_LISTS & toks) and not (_EXTERNAL & toks) and (
        verb_word == "queued" or listed
    ) and not other_work:
        out.append("night_task")
    folder = bool(toks & _FOLDER_WORDS)
    if folder and _names_the_setting(phrase, {"default", "defaults"}):
        out.append("coding_defaults")
    if folder and _names_the_setting(phrase, {"team", "teams"}) and not (_EXTERNAL & toks):
        out.append("coding_team")
    return out


#: v1.438.0 review: work the user queues in ANOTHER program for tonight.
_NOT_NIGHT_WORK = frozenset(
    {"job", "jobs", "print", "printer", "email", "emails", "mail", "message", "messages", "text", "texts", "song", "songs", "video",
     "videos", "post", "posts", "tweet", "tweets", "order", "orders", "delivery", "payment", "payments",
     "transfer", "transfers", "newsletter", "campaign", "broadcast", "upload", "uploads", "download",
     "downloads", "backup", "backups", "episode", "episodes", "playlist", "reminder", "reminders"}
)
#: v1.438.0 review: after "team" / "defaults", a word that makes it a part of
#: the user's own content ("the team section of the README", "the team
#: roster", "the defaults section").
_NOT_THE_SETTING_AFTER = _NOT_THE_RECORD_AFTER | frozenset(
    {"roster", "rosters", "member", "members", "photo", "photos", "bio", "bios", "meeting", "meetings",
     "channel", "chat", "lunch", "building", "heading", "paragraph", "block", "tab", "sheet", "slide"}
)


def _names_the_setting(phrase: list[str], words: "set[str]") -> bool:
    """A setting word in *phrase* that is the thing itself, never followed by
    a word that makes it the user's content ("team section", "defaults
    file")."""
    for i, t in enumerate(phrase):
        if t in words:
            nxt = phrase[i + 1] if i + 1 < len(phrase) else ""
            if nxt not in _NOT_THE_SETTING_AFTER:
                return True
    return False
#: v1.438.0 (Wave 4 group B): what a model server, an Iron-Proxy account and
#: a memory base are called. A server's words must say it serves MODELS (a
#: program that does, or "model / inference / AI server"): "the mail server",
#: "a Minecraft server" are someone else's.
_MODEL_SERVER_MODS = frozenset(
    {"model", "inference", "llm", "ai", "gpu", "studio", "lm", "ollama", "vllm", "llama", "cpp", "litellm",
     "sglang", "koboldcpp", "localai"}
)
_ACCOUNT_VENDORS = frozenset({"claude", "codex", "chatgpt", "openai", "grok", "xai"})
_MEMORY_PLACES = frozenset({"notion", "obsidian", "vault", "folder"})
#: v1.448.0 (Wave A item 4): a TOOL server ("the GitHub MCP server", "a tool
#: server") and a WEB service ("the pricing web service", "a REST hookup").
#: A bare "service" or "api" is someone's work, never a saved hookup.
_TOOL_SERVER_MODS = frozenset({"mcp", "tool", "tools"})
_WEB_SERVICE_MODS = frozenset({"web", "rest", "http", "https"})


def _connection_kind(head: str, prv: str, mods: list[str], np: list[str]) -> "str | None":
    """The connection record a noun phrase names (``model_server``,
    ``account``, ``memory_base``, ``tool_server``, ``web_service``), or None
    to let the other rules read it."""
    if head in ("server", "servers") and _TOOL_SERVER_MODS.intersection(mods):
        return "tool_server"
    if head == "mcp":
        return "tool_server"
    if head in ("service", "services") and _WEB_SERVICE_MODS.intersection(mods):
        return "web_service"
    if head in ("hookup", "hookups") and prv in ("rest", "web", "api"):
        return "web_service"
    if head in ("server", "servers", "endpoint", "gateway"):
        return "model_server" if _MODEL_SERVER_MODS.intersection(mods) else None
    if head == "box":
        return "model_server" if prv in _MODEL_SERVER_MODS else None
    if head in ("account", "accounts"):
        return "account" if _ACCOUNT_VENDORS.intersection(mods) else None
    if head in ("base", "bases") and prv == "memory":
        return "memory_base"
    if head in ("memory", "memories") and "as" in mods and _MEMORY_PLACES.intersection(np):
        return "memory_base"
    return None


def _records_in(phrase: list[str], verb_word: str, sentence_toks: list[str]) -> list[str]:
    """The record kinds *phrase* names (its head noun, with its guards)."""
    out: list[str] = []
    if _elsewhere(phrase):
        return out
    np = _np(phrase)
    kind = _record_kind(np, phrase[len(np):], sentence_toks)
    if kind:
        out.append(kind)
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


_INDEFINITE = frozenset({"a", "an", "another", "some", "new"})


def _setting_words_ok(claim: Claim, phrase: list[str]) -> bool:
    """May *phrase* name one of the app's settings at all? (Review, Wave 5
    group K.) Never a thing MADE ("I set up A Google Calendar reminder": a
    setting is changed, never created). The words that make it code or
    content are judged per setting (:func:`_vetoed`)."""
    return not (claim.verb == CREATE and phrase[:1] and phrase[0] in _INDEFINITE)


def _label_words(key: str) -> set[str]:
    try:
        from ..settings import schema

        d = schema.BY_KEY.get(key)
        if d is None:
            return set()
        return set(re.findall(r"[a-z0-9]+", " ".join([d.label, *d.aliases]).lower()))
    except Exception:  # noqa: BLE001
        return set()


def _vetoed(keys: list[str], phrase: list[str]) -> list[str]:
    """*keys* without the settings the words are not about: a claim that
    points into the user's own content or a file ("the default model IN
    CONFIG.PY") or is code ("dark mode SUPPORT") names no setting — unless
    that word is the setting's OWN ("the Custom ENDPOINT URL setting", "Share
    my profile with Claude CODE")."""
    veto = set(_CODE_WORDS.intersection(phrase)) | _elsewhere_words(phrase, brands=False)
    if not veto:
        return keys
    return [k for k in keys if veto <= _label_words(k)]


def _fill(claim: Claim, phrase: list[str], phrase_text: str, sentence_toks: list[str]) -> None:
    claim.phrase = phrase
    # v1.438.0 (Wave 4 group A): the night shift and a folder's coding records,
    # read first — "I cancelled the README task on tonight's list" names a
    # night task even though "README" reads as the user's content below.
    coding = _coding_kinds(phrase, claim.word) if claim.verb in _KIND_VERBS["record"] else []
    claim.records = list(coding)
    if _head(phrase) in _CONTENT_HEAD:
        return  # "I changed the email to ask first": the user's own content
    if {"preference", "preferences", "memory", "memories", "lesson", "lessons"}.intersection(phrase):
        # A kept preference is memory, never a setting. v1.438.0 (Wave 4
        # groups B and C): a memory BASE is a record ("I added Notion as your
        # memory base", settings/records_connections), and a preference or a
        # lesson is a RECORD of the user's own (settings/records_self) — "I've
        # forgotten that preference", "I added a lesson" are judged; "memory"
        # alone still names nothing here.
        if claim.verb in _KIND_VERBS["record"]:
            np = [] if _elsewhere(phrase) else _np(phrase)
            if np and _connection_kind(np[-1], np[-2] if len(np) > 1 else "", np[:-1], np) == "memory_base":
                claim.records = ["memory_base"]
            else:
                claim.records = [k for k in _records_in(phrase, claim.word, sentence_toks) if k == "preference"]
        return
    if "local" in phrase and "only" in phrase and phrase.index("local") + 1 < len(phrase) and (
        phrase[phrase.index("local") + 1] == "only"
    ):
        claim.local_only = True
    if claim.verb in _KIND_VERBS["record"]:
        claim.records = _records_in(phrase, claim.word, sentence_toks)
        claim.records += [k for k in coding if k not in claim.records]
    if claim.verb in _KIND_VERBS["app"] and phrase[:1] != ["to"]:
        # Review: the app is the claim's OBJECT ("I've connected GitHub"),
        # never a word further on ("enabled two-factor authentication on your
        # GitHub account" is work in GitHub).
        np = _np(phrase)
        claim.apps = _app_ids(" ".join(np), claim.word.split()[0], np)
    if claim.verb in _KIND_VERBS["setting"] and not claim.local_only and _setting_words_ok(claim, phrase):
        claim.settings = _vetoed(_setting_keys(phrase_text), phrase)
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


def _done_lead(toks: list[str]) -> int:
    """The length of a completion said first ("Done —", "You're all set:")
    when one opens *toks* and ends the sentence or its clause; else 0."""
    for lead in _DONE_LEADS:
        n = len(lead)
        if tuple(toks[:n]) == lead and (len(toks) == n or toks[n] in _BOUNDARY):
            return n
    return 0


def _dated(phrase: list[str]) -> bool:
    """The claim's own words name WHEN it happened ("on October 3", "in
    2025", "on Monday") — a report of an earlier change, never this reply's.
    Only before the words that describe the thing ("a schedule that runs on
    Monday" is no date)."""
    n = len(phrase)
    for i, t in enumerate(phrase):
        if t in _DATE_STOP:
            return False
        if t not in _DATE_PREPS or i + 1 >= n:
            continue
        nxt = phrase[i + 1]
        if nxt in _DETS and i + 2 < n:
            nxt = phrase[i + 2]
        if nxt in _MONTHS or nxt in _WEEKDAYS or _YEAR.match(nxt) or _ORDINAL.match(nxt):
            return True
    return False


def _user_acted(toks: list[str]) -> bool:
    """The sentence tells the USER's own change: "because you changed it in
    Settings", "you just switched it" (Review, Wave 5 group K)."""
    for j in range(len(toks) - 1):
        if toks[j] not in ("you", "you've"):
            continue
        nxt = toks[j + 1]
        if nxt in _ADVERBS and j + 2 < len(toks):
            nxt = toks[j + 2]
        if nxt in _USER_ACTION_VERBS:
            return True
    return False


def _undo_object_ok(obj: list[str]) -> bool:
    """An undo's object is one of Iron Jarvis's own undos: nothing, "it",
    "that change", "your theme change", a record ("the schedule change") —
    never a release, a migration or a document."""
    np = _np(obj)
    if _UNDO_CODE.intersection(obj):
        return False
    if not np or all(t in _UNDO_OBJECT for t in np):
        return True
    return np[-1] in ("change", "changes", "update", "setting", "settings") or bool(
        _record_kind(np, obj[len(np):], obj)
    )


#: v1.438.0 (Wave 4 group C): a LASTING PROMISE is a claim too — "I'll
#: answer in Spanish from now on", "From now on I'll use bullet points",
#: "Iron Jarvis will now start with Windows". Each says a lasting change
#: exists; it exists only when a setting (the profile's answer language or
#: formatting, the desktop app's switch) — or, for the answer, a kept
#: preference — changed THIS turn. Bounded, on the cleaned lower-case
#: sentence; a denial ("won't", "no longer") never matches the forward form,
#: and the desktop forms need "now" (or "no longer"): "Iron Jarvis will
#: start with Windows" with no "now" DESCRIBES how it works — never a claim.
_LANG_WORDS = (
    r"(?:english|spanish|french|german|portuguese|italian|dutch|polish|swedish|turkish|russian"
    r"|ukrainian|greek|hebrew|arabic|hindi|thai|chinese|mandarin|japanese|korean)"
)
_I_WILL = r"(?:i\s*'ll|i\s+will|i\s+am\s+going\s+to|i\s*'m\s+going\s+to)"
_LASTING_RX = re.compile(
    r"\b(?:from\s+now\s+on|from\s+here\s+on|going\s+forward|always|by\s+default|in\s+future"
    r"|every\s+(?:reply|answer|response|time)|in\s+(?:all|every)\s+(?:of\s+)?(?:my\s+)?"
    r"(?:answers?|repl(?:y|ies)|responses?))\b"
)
_PROMISE_LANGUAGE = re.compile(
    r"\b" + _I_WILL + r"\s+(?:now\s+|always\s+|only\s+){0,2}(?:answer|reply|respond|write|talk|speak)\b"
    r"[^.!?\n]{0,40}?\bin\s+" + _LANG_WORDS + r"\b"
)
_PROMISE_FORMAT = re.compile(
    r"\b" + _I_WILL + r"\s+(?:now\s+|always\s+){0,2}(?:use|answer\s+(?:with|in)|reply\s+(?:with|in)"
    r"|write\s+(?:with|in)|format\s+(?:my\s+)?(?:answers?|repl(?:y|ies))\s+(?:with|as|in))\s+"
    r"(?:short\s+)?(?:bullet(?:\s+points?|ed\s+lists?)?|bullets|headings|numbered\s+lists?|tables"
    r"|short\s+paragraphs|plain\s+prose|prose)\b"
)
_APP_NAME = r"(?:iron\s+jarvis|jarvis|the\s+(?:desktop\s+)?app|it)"
_PROMISE_START_ON = re.compile(
    r"\b" + _APP_NAME + r"\s+(?:will\s+now\s+|now\s+|will\s+from\s+now\s+on\s+)(?:start|open|launch|run)s?\s+(?:by\s+itself\s+|"
    r"automatically\s+|on\s+its\s+own\s+)?(?:with\s+windows|when\s+(?:you\s+(?:log|sign)\s+in|windows\s+starts)"
    r"|at\s+(?:login|startup|sign\s*-?\s*in))\b"
)
_PROMISE_START_OFF = re.compile(
    r"\b" + _APP_NAME + r"\s+(?:will\s+no\s+longer|no\s+longer)\s+(?:start|open|launch|run)s?"
    r"\s+(?:by\s+itself\s+|automatically\s+)?(?:with\s+windows|when\s+(?:you\s+(?:log|sign)\s+in|windows"
    r"\s+starts)|at\s+(?:login|startup))\b"
)
_PROMISE_TRAY_ON = re.compile(
    r"\b" + _APP_NAME + r"\s+(?:will\s+now\s+|now\s+|will\s+from\s+now\s+on\s+)(?:keep\s+running|stay\s+(?:open|running)|run)\s+"
    r"(?:in\s+the\s+(?:background|tray))\b[^.!?\n]{0,40}?\bclose\b"
)
_PROMISE_TRAY_OFF = re.compile(
    r"\b" + _APP_NAME + r"\s+(?:will\s+now\s+|now\s+|will\s+from\s+now\s+on\s+)(?:quit|close|stop|exit)s?\s+(?:completely\s+)?"
    r"when\s+you\s+close\s+(?:the|its)\s+window\b"
)


#: Review (v1.438.0): words that make a promise an offer or a condition.
_PROMISE_CONDITION = re.compile(
    r"\b(?:if|unless|once|confirm|confirmed|would\s+you|do\s+you\s+want|want\s+me\s+to|shall\s+i|should\s+i"
    r"|let\s+me\s+know|just\s+say|say\s+the\s+word)\b"
)


def _promise_claims(sentence: str) -> list[Claim]:
    """The lasting promises one sentence makes (see :data:`_LASTING_RX`)."""
    low = sentence.lower()
    out: list[Claim] = []
    if _PROMISE_CONDITION.search(low):
        # Review (v1.438.0): an OFFER or a condition ("If you want, I'll
        # always reply in German — confirm and I'll set it", "Once you
        # confirm, I will answer in Spanish from now on") promises nothing.
        return out
    if _LASTING_RX.search(low):
        if _PROMISE_LANGUAGE.search(low):
            out.append(Claim(verb=CHANGE, word="changed", phrase=["answer", "language"],
                             settings=["profile.language"], records=["preference"], promise=True))
        if _PROMISE_FORMAT.search(low):
            out.append(Claim(verb=CHANGE, word="changed", phrase=["formatting"],
                             settings=["profile.formatting"], records=["preference"], promise=True))
    for rx, verb, word in (
        (_PROMISE_START_ON, ON, "turned on"),
        (_PROMISE_START_OFF, OFF, "turned off"),
    ):
        if rx.search(low):
            out.append(Claim(verb=verb, word=word, phrase=["start", "windows"],
                             settings=["start_with_windows"], promise=True))
    for rx, verb, word in ((_PROMISE_TRAY_ON, ON, "turned on"), (_PROMISE_TRAY_OFF, OFF, "turned off")):
        if rx.search(low):
            out.append(Claim(verb=verb, word=word, phrase=["keep", "running", "background"],
                             settings=["keep_running_in_background"], promise=True))
    return out


def _claims_in_sentence(sentence: str, after_done: bool = False) -> list[Claim]:
    """Every change claim one sentence makes. *after_done*: the sentence
    before this one was only a completion ("Done!"). Never raises."""
    if sentence.endswith("?"):
        return []
    toks = _tokens(sentence)[: MAX_SENTENCE]
    if not toks or _earlier(toks):
        return []
    out: list[Claim] = []
    if not _user_acted(toks):
        out.extend(_promise_claims(sentence))
    used: set[int] = set()
    # "Undone." / "Reverted." on their own.
    core = [t for t in toks if t not in _INTERJECTIONS and t not in _BOUNDARY]
    if core in (["undone"], ["reverted"], ["rolled", "back"]):
        return [Claim(verb=UNDO, word=core[0], undo=True)]
    # Review (Wave 5 group K): a sentence about the user's own content or
    # another product ("In the story, I created a rogue agent", "…the
    # formatting changes in your doc") names none of Iron Jarvis's records.
    content_sentence = _elsewhere(toks)
    user_acted = _user_acted(toks)
    done = after_done or bool(_done_lead(toks))
    # Passive and state forms: "<subject> is now <state>", "<subject> has been
    # <verb>", "<subject> was <verb>".
    for k, t in enumerate(toks):
        if len(out) >= MAX_CLAIMS or user_acted:
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
        elif done and t in ("is", "are") and rest[0] in _DONE_STATES and "default" not in rest:
            # Review: after "Done!" / "You're all set —" a plain state is the
            # claim: "the nightly backup schedule is gone".
            claim = Claim(verb=_state_family(rest[:1]), word=rest[0])
            phrase = list(subj)
        elif done and t in ("is", "are") and rest[:1] == ["your"] and "default" in rest[1:3]:
            # …and "Sonnet is your default (model)".
            claim = Claim(verb=CHANGE, word="changed")
            named = rest[2:3] if rest[1] == "default" else rest[3:4]
            if not named and any(_MODEL_NAME.match(w) for w in subj):
                rest = rest[:3] + ["model"]  # "Sonnet is your default" names the model
            phrase = rest + ["to"] + subj
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
            if (
                {"change", "changes", "that", "it", "this"}.intersection(subj)
                and not _UNDO_CODE.intersection(subj)
                and not content_sentence
            ):
                claim.undo = True
                out.append(claim)
            continue
        if any(w in _NOT_A_CLAIM_BEFORE - {"your", "this", "that", "it"} for w in subj):
            continue
        if _dated(phrase):
            continue
        _fill(claim, phrase, " ".join(phrase), toks)
        if content_sentence:
            claim.records = [r for r in claim.records if r in _CODING_KINDS]
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
            # Review: an undo of a release, a migration or a document is not
            # one of Iron Jarvis's ("I rolled back the release").
            if _undo_object_ok(obj) and not content_sentence:
                claim.undo = True
                out.append(claim)
            i = after
            continue
        if word != "scheduled" and _dated(obj):
            i = after
            continue
        _fill(claim, obj, " ".join(obj), toks)
        if content_sentence:
            claim.records = [r for r in claim.records if r in _CODING_KINDS]
        if claim.named():
            out.append(claim)
        i = after
    return out


def claims_in(text: str) -> list[Claim]:
    """Every setting or record change *text* claims (see the module
    docstring). Pure and bounded; never raises."""
    try:
        out: list[Claim] = []
        after_done = False
        for s in _sentences(_clean(text)):
            out.extend(_claims_in_sentence(s, after_done))
            if len(out) >= MAX_CLAIMS:
                break
            # A sentence that is only "Done!" makes the next one's state a claim.
            toks = _tokens(s)[:8]
            after_done = bool(toks) and _done_lead(toks) == len(toks)
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
            if _OFFER_WORDS.intersection(toks):
                # Review: an offer, a condition or the new-chats setting ("I can
                # turn Local only on right away", "…right away for new chats").
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
    # v1.442.0 (Wave 7 group C): "I changed your theme to green" is the
    # theme of your own colours (setting one clears the other).
    frozenset({"device.theme", "device.theme_colors"}),
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
                for prefix, keys in _CARD_SETTINGS.items():
                    if key.startswith(prefix):
                        setting_keys.update(keys)
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
            if kind == "preference" and any(k.startswith("profile.") for k in self.setting_keys):
                return True  # v1.438.0: "your preference" kept in the profile (formatting, language)
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
                if "memory_base_add" in self.used and all(a in _MEMORY_BASE_APPS for a in claim.apps):
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
    if word == "gone":
        return "removed"  # Review: "the schedule is now gone" — never "was gone"
    if claim.verb == CHANGE and word in ("switched", "made", "put", "moved", "configured", "set"):
        return "changed"
    if claim.verb == CREATE and word in ("built", "made", "saved", "added", "set up"):
        return "created" if word != "set up" else "set up"
    if word in _VERBS or word.split()[0] in _PARTICLE_VERBS:
        return word
    if claim.verb in (PAUSE, REMOVE) and word in _STATE_PAUSE | _STATE_REMOVE:
        return word
    return _FAMILY_WORD.get(claim.verb, "changed")


def _coding_sentence(claim: Claim) -> str:
    """The note's sentence for a night task or a folder's coding record
    (v1.438.0, Wave 4 group A) — said by what the claim did, never "created"
    for "I set Codex as the default"."""
    kind = claim.records[0]
    if kind == "night_task":
        if claim.verb == CREATE:
            return f"No night task was queued {IN_THIS_REPLY}."
        if claim.verb in (REMOVE, PAUSE, OFF):
            return f"No night task was cancelled {IN_THIS_REPLY}."
        return f"Tonight's list was not changed {IN_THIS_REPLY}."
    if kind == "coding_defaults":
        return f"No folder's defaults for new coding sessions were changed {IN_THIS_REPLY}."
    return f"No folder's coding team was changed {IN_THIS_REPLY}."


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
    if claim.records and claim.records[0] in _CODING_KINDS:
        return _coding_sentence(claim)
    if claim.promise:
        label = _setting_label(claim)
        if label:
            return (
                f"“{label}” was not changed {IN_THIS_REPLY}, so this lasts only while this chat's "
                "history does."
                if claim.records
                else f"“{label}” was not changed {IN_THIS_REPLY}."
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


#: Another agent's words in the chat's history (an @-agent, a remote agent, a
#: run delivered to the chat): never Iron Jarvis's own earlier claim. The
#: SAME words ``comm.threads.agent_line_label`` and the page's
#: ``toRequestMessages`` put first on such a line.
_AGENT_LINE_PREFIX = "[Reply from the agent "

#: The words of a claim that are not its VALUE ("Sonnet", "Daylight", "9pm").
_NOT_A_VALUE = (
    _DETS
    | _STATE_ON | _STATE_OFF | _STATE_PAUSE | _STATE_REMOVE | _STATE_CREATE
    | frozenset(
        {"to", "for", "you", "me", "it", "on", "off", "now", "in", "of", "with", "at", "this",
         "chat", "chats", "reply", "replies", "again", "also", "all", "as", "requested", "asked",
         "so", "and", "is", "are", "set", "up", "mode", "setting", "settings", "option", "switch",
         "feature", "here", "there", "just", "both", "its", "it's", "default", "new", "every",
         "one", "please", "already", "still", "too", "that", "which", "will", "be", "by", "from",
         "back", "over", "away", "whole", "app", "jarvis", "iron", "conversation", "turned",
         "switched", "rolled", "hooked", "gone", "connected", "disconnected", "undone"}
    )
    | frozenset(_VERBS)
)
#: The verb DIRECTION a summary must share with its earlier claim: turning
#: something on, making it, connecting it or changing it are one direction;
#: each of the ways back is its own ("paused" is never "deleted").
_POSITIVE = frozenset({ON, CREATE, CONNECT, CHANGE})


def _direction(claim: Claim) -> str:
    if claim.verb in _POSITIVE:
        return "positive"
    if claim.verb == PAUSE:
        return "resume" if claim.word in ("resumed", "restarted") else "pause"
    if claim.verb == UNDO:
        return UNDO
    return claim.verb


def _values(claim: Claim) -> set[str]:
    """The words a claim says the thing was changed TO or about — its value —
    minus its setting's own label and aliases and the record nouns."""
    words = {t for t in claim.phrase if t not in _NOT_A_VALUE}
    words -= set(_RECORD_NOUN) | {f"{n}s" for n in _RECORD_NOUN}
    try:
        from ..settings import schema

        for k in claim.settings:
            d = schema.BY_KEY.get(k)
            if d is not None:
                words -= set(re.findall(r"[a-z0-9]+", " ".join([d.label, *d.aliases]).lower()))
    except Exception:  # noqa: BLE001 — every word stays a value
        pass
    return words


def _excused(history: Iterable[Any]) -> list[tuple[set[tuple[str, str]], str, set[str]]]:
    """The claims EARLIER assistant messages in this conversation made — this
    reply may be summarising them. A claim that message's own note said was
    not made does not count, and neither does another agent's line.

    Review (Wave 5 group K): the first cut kept only the KIND of each earlier
    claim, so one true "I set your theme to Daylight" excused every later
    false "I switched your theme to Midnight", and one true "I turned on read
    aloud" a false "I turned it off" — for six messages. A claim is now only
    excused by an earlier one of the same kind, the same direction and no
    other value (:func:`_is_excused`). Each earlier claim is kept as its
    kinds, direction and values, worked out once."""
    out: list[tuple[set[tuple[str, str]], str, set[str]]] = []
    msgs = [m for m in (history or ()) if str(getattr(m, "role", "") or "") == "assistant"]
    for m in msgs[-MAX_HISTORY_MESSAGES:]:
        content = getattr(m, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        if content.lstrip().startswith(_AGENT_LINE_PREFIX):
            continue  # another agent said it, never Iron Jarvis
        text = content[-MAX_HISTORY_CHARS:]
        noted = " ".join(
            ln for ln in text.split("\n") if ln.lstrip().startswith("_Note:") and IN_THIS_REPLY in ln
        ).lower()
        for c in claims_in(text):
            if noted and _noted_as_not_made(c, noted):
                continue
            out.append((c.candidates(), _direction(c), _values(c)))
    return out


def _noted_as_not_made(claim: Claim, noted: str) -> bool:
    """Did the earlier reply's own note say *claim* was not made? Its
    sentence as said with no card ("Notion was not connected…") or with a
    card still waiting for the key ("Notion is not connected yet…")."""
    if sentence_for(claim).lower().rstrip(".") in noted:
        return True
    for a in claim.apps:
        name = (_app_name(a) or "the app").lower()
        if f"{name} is not connected yet" in noted:
            return True
    return False


def _is_excused(claim: Claim, earlier: "list[tuple[set[tuple[str, str]], str, set[str]]]") -> bool:
    """An earlier claim of the same kind, in the same direction, with no
    value this one does not share (both naming a value and the values
    differing is a NEW change: "…to Midnight" after "…to Daylight")."""
    cands = claim.candidates()
    direction = _direction(claim)
    mine = _values(claim)
    for their_cands, their_direction, theirs in earlier:
        if not (their_cands & cands) or their_direction != direction:
            continue
        if mine and theirs and not mine <= theirs:
            continue
        return True
    return False


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


#: The reply Iron Jarvis writes itself when the model wrote none
#: (``chat_turn._no_text_reply``): it quotes a TOOL's own output, which is
#: never the model's claim.
_NO_TEXT_LEAD = "The model finished without writing an answer."


def _note(reply, tools_used, config_cards, history, custom_tools, reply_local_only) -> str:
    if not reply or not str(reply).strip():
        return ""
    if str(reply).lstrip().startswith(_NO_TEXT_LEAD):
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
    # Review (Wave 5 group K): an action in ANOTHER app or a web page (an app's
    # own tool, a click, typing) may be what the reply is about — "I created
    # the project", "I turned on dark mode" after clicks on a site.
    elsewhere = (
        bool(record.used & (_ACTS_ELSEWHERE | _RECAPS_THE_LEDGER))
        or any(n.startswith("mcp__") for n in record.used)
    )
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
        if elsewhere:
            claim.records, claim.settings, claim.undo = [], [], False
            if not claim.named():
                continue
        if record.answers(claim):
            continue
        if _is_excused(claim, excused):
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
