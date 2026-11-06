"""The setup requests Wave 7 of the chat-first plan measured (v1.442.0, group A).

``docs/CHAT-FIRST-PLAN-2026-11-04.md`` Wave 7 proves "if the app offers
something, the user can do all the setup for it from chat" with the review's
30 setup requests. Run through the REAL arming, ten of them still armed the
wrong tool or said nothing true about what is not possible. These are the
shapes those sentences have, ONE definition each, read by:

* ``settings.records.record_tools_for`` — which tools the request is offered;
* ``settings.trigger`` — which settings it reaches;
* ``daemon.page_doors`` — the plain line the model is given when the request
  asks for something Iron Jarvis does not do the way it was asked.

Every pattern runs on the user's own words, so every pattern is LINEAR: no
nested quantifier, every gap bounded, never across a sentence end. Pinned with
pathological input in a child process with a deadline
(``tests/test_wave7_gaps_v14420.py``). Pure; nothing here raises.
"""

from __future__ import annotations

import re

#: The longest gap two words of one request may have between them.
_GAP = r"[^.!?\n]{0,48}?"

# --------------------------------------------------------------------------- #
# Iron Jarvis's OWN backup ("Schedule a weekly backup of my settings to my
# OneDrive folder", "Back up everything to my external drive automatically")
# --------------------------------------------------------------------------- #
#: What a backup of IRON JARVIS holds — never the user's own files ("back up
#: my client files", "back up my laptop").
_JARVIS_THINGS = (
    r"(?:settings|data|chats?|history|memor(?:y|ies)|preferences|configuration|config|setup"
    r"|iron\s+jarvis|jarvis|the\s+app|app\s+data"
    r"|everything(?!\s+(?:on|in|from)\s+(?:my|the|this|our)\s+\w+))"
)
_OWNER = r"(?:(?:all\s+)?(?:my|your|the|our|iron\s+jarvis['’]?s?|jarvis['’]?s?)\s+)?"
#: Review (v1.442.0): a word after them that makes them the user's own THING
#: ("the backup of my settings FILE is in git", "back up the config folder").
_NOT_JARVIS_AFTER = (
    r"(?!\s+(?:files?|folders?|director(?:y|ies)|page|screens?|tab|modules?|code|json|yaml|toml|tables?"
    r"|sheets?|schemas?|panels?|menus?|dialogs?|class|objects?|repo|repository|branch)\b)"
)
JARVIS_BACKUP = re.compile(
    r"\bbackups?\s+of\s+" + _OWNER + _JARVIS_THINGS + r"\b" + _NOT_JARVIS_AFTER
    + r"|\bback(?:ing)?\s+up\s+" + _OWNER + _JARVIS_THINGS + r"\b" + _NOT_JARVIS_AFTER
    + r"|\bback(?:ing)?\s+" + _OWNER + _JARVIS_THINGS + r"\s+up\b"
    r"|\b(?:your|iron\s+jarvis['’]?s?|jarvis['’]?s?|the\s+app['’]?s?|(?:my|the|your)\s+automatic|automatic)\s+backups?\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]{1,600}")


def sentences(text: str) -> list[str]:
    """*text* cut into sentences (linear: one pass, each piece bounded)."""
    return _SENTENCE.findall(str(text or ""))


def jarvis_backup(text: str) -> bool:
    """Does *text* ask about Iron Jarvis's OWN backup?"""
    return JARVIS_BACKUP.search(str(text or "")) is not None


def without_backup_sentences(text: str) -> str:
    """*text* with every sentence about Iron Jarvis's own backup left out — so
    "schedule a weekly backup of my settings" is not read as one of the
    app's schedules (a schedule runs a task; it does not back the app up)."""
    return " . ".join(s for s in sentences(text) if JARVIS_BACKUP.search(s) is None)


# --------------------------------------------------------------------------- #
# "…and send it to my phone"
# --------------------------------------------------------------------------- #
_PHONE = (
    r"(?:phone|cell(?:\s*phone)?|mobile(?:\s+phone)?|iphone|android)"
    # Review (v1.442.0): "to my phone NUMBER on the form", "my phone CARRIER".
    r"(?!\s+(?:numbers?|carriers?|plans?|bills?|company|provider|cases?|screens?|contacts?|book|line)\b)"
    r"(?!['’]s\b)"
)
TO_MY_PHONE = re.compile(
    r"\b(?:send|text|message|ping|notify|alert|push|forward|deliver)\s+(?:me\s+)?"
    r"(?:it|this|that|them|those|me|alerts?|reminders?|updates?|notifications?"
    r"|the\s+(?:summary|digest|results?|report|briefing|alerts?|reminders?|updates?|notifications?|answer)"
    r"|a\s+(?:summary|digest|text|message|reminder|notification|note|briefing))\b"
    r"[^.!?\n]{0,30}?\b(?:to|on)\s+my\s+" + _PHONE + r"\b"
    r"|\b(?:get|receive|have|want)\s+(?:the\s+)?(?:alerts?|notifications?|updates?|texts?|a\s+text)\s+"
    r"(?:on|to)\s+my\s+" + _PHONE + r"\b",
    re.IGNORECASE,
)

#: Review (v1.442.0): a destination TYPE set up so Jarvis can message the
#: user ("Please set up email so you can message me at work").
SET_UP_TO_MESSAGE_ME = re.compile(
    r"\b(?:set\s*up|setup|connect|add|hook\s+up|configure|enable|turn\s+on|use)\s+(?:(?:my|an?|the|our)\s+)?"
    r"(?:e-?mail|telegram|discord|texts?|text\s+messages?)\b[^.!?\n]{0,40}?\b(?:so\s+(?:that\s+)?|to\s+let\s+|for\s+)?"
    r"(?:you|jarvis|iron\s+jarvis)\s+(?:can\s+|could\s+|will\s+|to\s+)?(?:message|notify|ping|alert|text|reach|contact)\s+me\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# "Create a workflow that drafts a reply to every new email from a client."
# --------------------------------------------------------------------------- #
_ARRIVING = (
    r"(?:e-?mails?|mail|messages?|texts?|files?|documents?|pdfs?|invoices?|receipts?|uploads?"
    r"|attachments?|payments?|orders?|tickets?|leads?|submissions?|forms?)"
)
_EVERY_NEW = re.compile(
    r"\b(?:every|each|any|all)\s+(?:new|incoming|inbound|fresh)\s+(?:[\w-]+\s+)?" + _ARRIVING + r"\b",
    re.IGNORECASE,
)
#: …said as a standing request (never "reply to every new email in my inbox
#: now", a one-off chore).
_STANDING = re.compile(
    r"\b(?:workflows?|rules?|automat\w*|from\s+now\s+on|going\s+forward"
    # Review (v1.442.0): "I always reply to every new email" is a HABIT.
    r"|(?<!\bi\s)(?<!\bwe\s)(?<!\bhe\s)(?<!\bshe\s)(?<!\bthey\s)always|whenever|on\s+its\s+own)\b",
    re.IGNORECASE,
)
_MAIL = re.compile(r"\b(?:e-?mails?|mail|inbox)\b", re.IGNORECASE)


def every_new_arrival(text: str) -> bool:
    """A standing request about "every new <thing that arrives>" — a rule's
    trigger (reflex_create)."""
    return any(_EVERY_NEW.search(s) and _STANDING.search(s) for s in sentences(text))


#: "when a client email arrives", "whenever a new email comes in" — the rule
#: shape settings.records reads (``_REFLEX_TRIGGER``), for mail.
_MAIL_ARRIVES = re.compile(
    r"\b(?:when|whenever|every\s+time|each\s+time|any\s*time|as\s+soon\s+as)\b[^.!?\n]{0,60}?"
    r"\b(?:e-?mails?|mail)\b[^.!?\n]{0,40}?\b(?:arrives?|arriving|comes?\s+in|lands?|shows?\s+up"
    r"|is\s+received|gets?\s+received|hits?\s+my\s+inbox)\b",
    re.IGNORECASE,
)


def every_new_email(text: str) -> bool:
    """A rule asked for on new EMAIL — heard only through an email destination
    with two-way listening on, from its Allowed senders."""
    return any(
        ((m := _EVERY_NEW.search(s)) is not None and _MAIL.search(m.group(0)) and _STANDING.search(s))
        or _MAIL_ARRIVES.search(s) is not None
        for s in sentences(text)
    )


# --------------------------------------------------------------------------- #
# "Watch my Downloads folder and file new PDFs into the right client folder."
# --------------------------------------------------------------------------- #
#: A folder as what is watched — never "watch the Downloads movie".
_NOT_A_FOLDER_AFTER = (
    r"(?!\s+(?:movies?|films?|shows?|videos?|series|documentar(?:y|ies)|episodes?|pages?|buttons?|icons?"
    r"|counts?|speeds?|bars?|wallpapers?|backgrounds?|usage|activity|screens?|temperatures?|performance"
    # Review (v1.442.0): "the desktop SUPPORT queue", "my desktop APP".
    r"|support|queues?|teams?|apps?|applications?|computers?|pcs?|machines?|versions?|clients?|tickets?"
    r"|notifications?|sharing|clock|space)\b)"
)
WATCH_FOLDER = re.compile(
    r"\b(?:watch|keep\s+an\s+eye\s+on|monitor)\s+(?:my|the|this|that|for)\b[^.?!\n]{0,30}?"
    r"\b(?:folder|directory|drive|downloads|desktop|documents\s+folder|path)\b" + _NOT_A_FOLDER_AFTER,
    re.IGNORECASE,
)
#: What a person wants DONE to what arrives.
_ACTS_ON_ARRIVALS = re.compile(
    r"\b(?:and|then|to)\s+(?:then\s+|automatically\s+)?(?:file|move|sort|rename|organi[sz]e|process|put|copy"
    r"|delete|send|upload|convert|summari[sz]e|tag|label|forward|print|archive|extract|categori[sz]e)\b",
    re.IGNORECASE,
)


def watch_and_act(text: str) -> bool:
    """A folder watched AND something done to what arrives in it."""
    return any(WATCH_FOLDER.search(s) and _ACTS_ON_ARRIVALS.search(s) for s in sentences(text))


# --------------------------------------------------------------------------- #
# "Set a monthly spending limit for my builder agent."
# --------------------------------------------------------------------------- #
#: Iron Jarvis's own agents by the words a person uses (core.models.AgentType).
BUILTIN_AGENT_NAMES: tuple[str, ...] = (
    "supervisor", "planner", "builder", "reviewer", "researcher", "memory", "automation", "maintainer",
    "guide",
)
_BUILTIN_AGENT = re.compile(
    r"\b(?:the|my|your|our)\s+(?:" + "|".join(BUILTIN_AGENT_NAMES) + r")\s+agent(?:['’]s)?\b", re.IGNORECASE
)
_LIMIT = re.compile(
    r"\b(?:monthly|per\s+month|a\s+month|spending|budget|allowance|cap|limit|spend\s+limit)\b", re.IGNORECASE
)
_LIMIT_VERB = re.compile(
    r"\b(?:set|give|put|add|make|cap|limit|raise|lower|change|create|apply|impose)\b", re.IGNORECASE
)


def builtin_agent_limit(text: str) -> bool:
    """A monthly limit asked for on one of Iron Jarvis's own agents."""
    return any(
        _BUILTIN_AGENT.search(s) and _LIMIT.search(s) and _LIMIT_VERB.search(s) for s in sentences(text)
    )


# --------------------------------------------------------------------------- #
# "Turn on voice so I can talk to you."
# --------------------------------------------------------------------------- #
VOICE = re.compile(
    r"\b(?:turn\s+on|switch\s+on|enable|start|use|set\s+up|open|activate)\s+(?:the\s+|my\s+|your\s+)?"
    r"(?:voice(?:\s+(?:chat|mode|input|control|conversation))?|dictation|microphone|mic"
    r"|hands[\s-]?free(?:\s+mode)?)\b"
    r"(?!\s+(?:of|memos?|mail|notes?|over|actors?|recordings?|messages?|recorder|checks?|tests?)\b)"
    # Review (v1.442.0): "use the microphone in Zoom" is another program's.
    r"(?!\s+(?:in|on|for|with)\s+(?!(?:the\s+|this\s+|my\s+)?(?:chat|jarvis|iron\s+jarvis|app)\b)[\w.-]+)"
    r"|\b(?:talk|speak)\s+(?:to|with)\s+(?:you|jarvis|iron\s+jarvis)\b[^.!?\n]{0,30}?"
    r"\b(?:out\s+loud|by\s+voice|with\s+my\s+voice|instead\s+of\s+typing|hands[\s-]?free|using\s+my\s+voice"
    r"|through\s+(?:the|my)\s+mic(?:rophone)?)\b"
    r"|\b(?:jarvis['’]?s?|your|the\s+app['’]?s?)\s+voice\s+(?:chat|mode)\b",
    re.IGNORECASE,
)


def asks_for_voice(text: str) -> bool:
    return VOICE.search(str(text or "")) is not None


# --------------------------------------------------------------------------- #
# "Set up a webhook so Zapier can start a task."
# --------------------------------------------------------------------------- #
_SERVICE = (
    r"(?:zapier|ifttt|make\.com|integromat|pipedream|power\s+automate|n8n\s+cloud|github\s+actions?"
    r"|a\s+web\s+service|web\s+services|an\s+online\s+service|a\s+cloud\s+service)"
)
#: What a service would do INTO Iron Jarvis.
_INTO_JARVIS = (
    r"(?:(?:start|trigger|run|kick\s+off|create|launch)\s+(?:(?:a|an|the|my|new|some)\s+)?"
    r"(?:tasks?|runs?|sessions?|jobs?|workflows?|work|agents?|jarvis|you|iron\s+jarvis)"
    r"|(?:send|post|push)\s+(?:[\w'-]+\s+){0,3}?(?:to|into)\s+(?:you|jarvis|iron\s+jarvis|this\s+pc)"
    r"|(?:reach|call|talk\s+to|tell|ask|message|ping|wake\s+up)\s+(?:you|jarvis|iron\s+jarvis))\b"
)
INTERNET_CALLS_IN = re.compile(
    r"\bso\s+(?:that\s+)?" + _SERVICE + r"\s+can\s+" + _INTO_JARVIS
    + r"|\b(?:let|have|allow|get)\s+" + _SERVICE + r"\s+(?:to\s+)?" + _INTO_JARVIS
    + r"|\b(?:webhooks?|requests?|calls?|triggers?)\s+from\s+" + _SERVICE + r"\b"
    + r"|\b" + _SERVICE + r"\s+(?:should|will|would|could|can|to)\s+" + _INTO_JARVIS,
    re.IGNORECASE,
)
#: …but sending Iron Jarvis's events OUT to such a service is a webhook it has.
_OUTBOUND = re.compile(
    r"\b(?:outgoing|outbound)\b|\b(?:send|post|forward|push)\s+(?:my\s+|the\s+|iron\s+jarvis['’]?s?\s+|your\s+)?"
    r"(?:events?|results?|alerts?|notifications?|updates?)\s+(?:to|into)\b",
    re.IGNORECASE,
)


#: Review (v1.442.0): "Delete the webhook from Zapier" changes a webhook the
#: user has; it is not Zapier asked to call in.
_EDITS_A_WEBHOOK = re.compile(
    r"\b(?:delete|remove|turn\s+off|disable|pause|stop|rename|change|edit|update|fix|test|check)\s+"
    r"(?:(?:the|my|that|this|our|a)\s+)?(?:[\w-]+\s+)?webhooks?\b",
    re.IGNORECASE,
)


def internet_calls_in(text: str) -> bool:
    """A web service on the internet asked to reach INTO Iron Jarvis."""
    return any(
        INTERNET_CALLS_IN.search(s) is not None and _EDITS_A_WEBHOOK.search(s) is None
        for s in sentences(text)
    )


def asks_outbound(text: str) -> bool:
    return _OUTBOUND.search(str(text or "")) is not None


#: Every compiled pattern over the user's words (the linear-time pin reads it).
ALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    JARVIS_BACKUP, _SENTENCE, TO_MY_PHONE, SET_UP_TO_MESSAGE_ME, _EDITS_A_WEBHOOK, _EVERY_NEW, _STANDING, _MAIL, _MAIL_ARRIVES, WATCH_FOLDER,
    _ACTS_ON_ARRIVALS, _BUILTIN_AGENT, _LIMIT, _LIMIT_VERB, VOICE, INTERNET_CALLS_IN, _OUTBOUND,
)
