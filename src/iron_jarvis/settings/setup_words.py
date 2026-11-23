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


#: v1.455.0 (readiness Wave G, item 23): "When I get an email from the IRS,
#: forward it to my phone" — a rule on mail FROM someone, its action an order
#: of its own after a comma or "then" ("…, I panic" is the person's habit).
MAIL_FROM_RULE = re.compile(
    r"\b(?:when|whenever|every\s+time|each\s+time|any\s*time|if|as\s+soon\s+as)\s+(?:i|we)\s+"
    r"(?:get|receive|am\s+sent|are\s+sent|get\s+sent)\s+(?:an?\s+|any\s+)?(?:new\s+)?"
    # Review (v1.455.0): e-mail only. "When we get mail from the IRS, file
    # it in the cabinet" is a LETTER (the IRS writes by post), and a rule
    # cannot hear the post.
    r"e-?mails?\s+(?:from|by)\b[^.!?\n]{1,60}?"
    r"(?:,\s*|\s+then\s+|\s+(?:please|can\s+you|could\s+you)\s+)(?:then\s+)?(?:please\s+)?"
    r"(?:forward|send|text|tell|notify|alert|ping|message|let\s+me\s+know|reply|answer|respond|draft|file"
    r"|save|move|summari[sz]e|flag|label|tag|star|print|copy|start|run|add|put|remind)\b",
    re.IGNORECASE,
)


def mail_from_rule(text: str) -> bool:
    """A rule asked for on mail from someone (an order follows)."""
    return any(MAIL_FROM_RULE.search(s) is not None for s in sentences(text))


def every_new_email(text: str) -> bool:
    """A rule asked for on new EMAIL — heard only through an email destination
    with two-way listening on, from its Allowed senders."""
    return any(
        ((m := _EVERY_NEW.search(s)) is not None and _MAIL.search(m.group(0)) and _STANDING.search(s))
        or _MAIL_ARRIVES.search(s) is not None
        or MAIL_FROM_RULE.search(s) is not None
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


#: v1.455.0 (readiness Wave G, item 20): FILES arriving in a FOLDER on this
#: PC — "Whenever a new PDF shows up in my Downloads folder, rename it by
#: client name", "every new scan in C:\Scans". Leap 05-a: that is a RULE with
#: a file start (``reflex_create``, source ``file``: the rule makes its own
#: watcher of the folder); a plain watcher (``sentinel_add``) only suggests.
#: Never an app's own place ("…in my Google Drive", "…in QuickBooks"), never
#: a repository.
_FILE_THING = (
    r"(?:files?|documents?|docs?|pdfs?|invoices?|receipts?|scans?|images?|photos?|pictures?|screenshots?"
    r"|spreadsheets?|downloads?|attachments?|statements?|reports?|w-?2s?|1099s?|k-?1s?|csvs?)"
)
#: A folder on this PC: a drive path, or one to three words ending in
#: folder / directory / Downloads / Desktop / Documents. Never a cloud drive's
#: own name before it ("my Google Drive folder" is the app's).
_A_FOLDER = (
    r"(?:in|into|to|inside|under|on)\s+(?:(?:my|the|our|this|that)\s+)?"
    # Review (v1.455.0): possessive, so a guard after the path cannot be
    # dodged by shortening it ("C:\Scans I file them" read as "C:\Sca" +
    # "ns I…").
    r"(?:[A-Za-z]:[\\/][^\s.,;!?]{0,80}+"
    r"|(?:(?!(?:google|onedrive|dropbox|sharepoint|icloud|box|git\w*|code|repo\w*)\b)[\w'’-]{1,30}\s+){0,2}?"
    r"(?:folder|directory|downloads|desktop(?!\s+(?:apps?|programs?|applications?)\b)|documents)\b)"
)
_ARRIVES = (
    r"(?:arrives?|arriving|shows?\s+up|lands?|appears?|comes?\s+in|drops?\s+in"
    r"|gets?\s+(?:saved|added|dropped|downloaded|put|copied|moved|created)"
    r"|is\s+(?:saved|added|dropped|downloaded|put|copied|moved|created))"
)
#: Review (v1.455.0): the person's own HABIT right after the folder ("…shows
#: up in my Downloads folder I rename it by hand") is not a request. Read
#: with an optional trailing "folder" so a shorter folder match ("my
#: Downloads") cannot slip past it.
_NOT_A_HABIT_AFTER = (
    r"(?!(?:\s+(?:folder|directory))?\s*,?\s*(?:i|we)\s+(?!(?:want|need|would|will|['’]d|['’]ll)\b)[a-z])"
)
#: A first person doing something INSIDE the "every new …" span ("Any new
#: receipts I get I save to my Desktop") is the person's habit — unless a
#: request follows the folder ("…I save to my Desktop should be renamed").
_NO_FIRST_PERSON = r"(?:(?!\b(?:i|we)\s+(?!(?:want|need|would|will)\b)[a-z])[^.!?\n]){0,30}?"
_A_REQUEST_FOLLOWS = r"(?=[^.!?\n]{0,60}?\b(?:should|must|needs?\s+to|has\s+to|have\s+to|please)\b)"
FILES_ARRIVE_IN_FOLDER = re.compile(
    r"\b(?:when|whenever|every\s+time|each\s+time|any\s*time|as\s+soon\s+as)\b[^.!?\n]{0,40}?\b"
    # Review (v1.455.0): no comma between the arrival and the folder — a
    # folder after the comma is where the person PUTS it ("When the client's
    # documents arrive, put them in the client folder"), not where it lands.
    + _FILE_THING + r"\b[^.!?\n]{0,30}?\b" + _ARRIVES + r"\b[^.!?\n,;]{0,20}?\b" + _A_FOLDER + _NOT_A_HABIT_AFTER
    + r"|\b(?:every|each|any|all)\s+new\s+(?:[\w-]+\s+)?" + _FILE_THING
    + r"\b(?:" + _NO_FIRST_PERSON + r"\b" + _A_FOLDER + _NOT_A_HABIT_AFTER
    + r"|[^.!?\n]{0,30}?\b" + _A_FOLDER + _A_REQUEST_FOLLOWS + r")",
    re.IGNORECASE,
)


def files_arrive_in_folder(text: str) -> bool:
    """Files arriving in a folder on this PC, said as a standing request."""
    return any(FILES_ARRIVE_IN_FOLDER.search(s) is not None for s in sentences(text))


def without_folder_arrivals(text: str) -> str:
    """*text* with every "files arriving in a folder" span blanked (length
    kept), so the rule words are read only for what a rule can hear."""
    return FILES_ARRIVE_IN_FOLDER.sub(lambda m: " " * len(m.group(0)), str(text or ""))


def watch_and_act(text: str) -> bool:
    """A folder watched AND something done to what arrives in it — or
    (v1.455.0) files arriving in a folder, asked as a standing request."""
    return any(
        (WATCH_FOLDER.search(s) and _ACTS_ON_ARRIVALS.search(s)) or FILES_ARRIVE_IN_FOLDER.search(s)
        for s in sentences(text)
    )


#: Leap 05-a: a DOWNLOAD finishing, said as a standing request with an order
#: after it — "When a download finishes from irs.gov, file it in my Tax
#: folder", "Whenever I download a PDF from my bank, rename it". A download
#: rule's start (``reflex_create``, source ``download``). Never one download
#: now ("when THE download finishes, open it", "did my download finish?"),
#: never the person's habit ("whenever I download something it goes to my
#: Downloads folder"). Every repeat is bounded (linear).
#: One character of the same sentence: a dot only inside a word ("irs.gov").
_IN_SENTENCE = r"(?:[^.!?\n]|\.(?=\w))"
_IN_CLAUSE = r"(?:[^.!?\n,]|\.(?=\w))"
_A_REQUEST_AFTER = (
    r"(?=" + _IN_SENTENCE + r"{0,80}?(?:,\s*(?:(?:please|then|also|just)\s+)?"
    r"(?:(?:i|we)\s+(?:want|need|would\s+like|['’]d\s+like)\b"
    r"|(?!(?:i|we|it|they|he|she|that|this|there|you|my|our)\b)[a-z]{2,}\b)"
    r"|\b(?:should|must|please|needs?\s+to|has\s+to|have\s+to)\b))"
)
DOWNLOAD_FINISHES = re.compile(
    r"(?:\b(?:whenever|every\s+time|each\s+time|any\s*time)\b" + _IN_CLAUSE + r"{0,40}?\bdownload(?:s|ed|ing)?\b"
    r"|\bwhen\s+(?:(?:a|an|any|each|every)\s+(?:new\s+)?(?:[\w'’-]+\s+){0,3}?"
    r"(?:download\b" + _IN_CLAUSE + r"{0,30}?\b(?:finish(?:es)?|completes?|is\s+(?:done|finished|complete))\b"
    r"|(?:is|gets?)\s+downloaded\b)"
    r"|(?:i|we)\s+download\b))" + _A_REQUEST_AFTER,
    re.IGNORECASE,
)


#: Sentences whose dots inside a word are kept ("irs.gov" is a site, not the
#: end of a sentence). Linear: one pass, each piece bounded.
_SENTENCE_KEEPING_SITES = re.compile(_IN_SENTENCE + r"{1,600}")


def download_finishes(text: str) -> bool:
    """A finished download, said as a standing request (leap 05-a)."""
    return any(
        DOWNLOAD_FINISHES.search(s) is not None for s in _SENTENCE_KEEPING_SITES.findall(str(text or ""))
    )


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


# --------------------------------------------------------------------------- #
# A model VENDOR or PLAN asked to answer (v1.448.0, Wave A "chat configures
# everything", A1: "Use Claude for this chat instead of the default", "I have
# a ChatGPT Plus plan, can you use that to answer?", "Set up Gemini so I can
# try Google's model", "Use OpenRouter for the big questions" — each armed
# NOTHING at v1.447.0). ONE definition, read by ``settings.trigger`` (the
# default-provider / default-model rows are what change; a key still goes
# only on the secure card) and ``daemon.page_doors`` (the door to Settings ›
# Connections, where a plan signs in and a key is pasted).
# --------------------------------------------------------------------------- #
#: The cloud vendors and plans no chat tool can connect by itself. Never the
#: local programs (Ollama, LM Studio…): those have ``model_server_add``.
VENDOR = (
    r"(?:claude(?:[\s-]*(?:code|cli))?|anthropic|chatgpt|openai|gpt(?:-?\d[\w.-]*)?|gemini|google['’]s\s+(?:ai\s+)?models?"
    r"|openrouter|open\s+router|grok|xai|codex|mistral|deepseek|perplexity)"
)
_PLAN_WORD = r"(?:plus|pro|max|team|free|paid|enterprise|business)"
#: "my ChatGPT Plus plan", "the Claude Max subscription", "my OpenAI key" —
#: and a plan named by its tier alone ("Claude Max", "ChatGPT Plus").
PLAN = (
    r"(?:(?:my|the|our|your|a|an)\s+)?" + VENDOR + r"\s+(?:" + _PLAN_WORD
    + r"(?:\s+(?:plan|subscription|account|membership))?|plan|subscription|account|api\s+key|key|credits"
    r"|membership)"
)
#: Where a request may START (the trigger's own ``_ASKS_AT``, repeated here so
#: this module stays import-free): a clause of its own, never "I use Claude"
#: (a sentence about the user) and never "the slides use Claude".
_REQUEST_AT = (
    r"(?:^|(?<=[.!?;:,\n])\s*|\b(?:please|just|now|also|and|then|so|ok|okay|jarvis|let['’]?s)\s+"
    r"|\b(?:can|could|would|will)\s+you\s+(?:please\s+)?|\bi\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\s+"
    # v1.455.0 (readiness Wave G, item 19): "Can I use my ChatGPT Plus plan
    # with you?" — a question, so the settings writers stay out (the question
    # guard), but the Connections door is the answer.
    r"|\b(?:can|could|may)\s+i\s+(?:please\s+)?"
    r"|\bi['’]d\s+like\s+(?:you\s+)?to\s+)"
)
#: What makes the vendor the one who ANSWERS: "for this chat", "for the big
#: questions", "to answer", "instead (of the default)", "from now on", "by
#: default", "here", or the request ending right there. Never "to refactor my
#: repo" (work handed to Claude Code) and never "for this" alone.
ANSWERS_TAIL = (
    r"(?:\s+(?:instead(?:\s+of\s+[^.?!\n]{1,40})?|from\s+now\s+on|by\s+default|going\s+forward|for\s+now"
    r"|for\s+everything|for\s+(?:answers?|replies|questions|conversations?)(?=\s*(?:[.!?,;]|$))|for\s+all\s+(?:(?:my|our|the)\s+)?(?:chats|conversations|questions|answers|replies)"
    r"|for\s+(?:this|the|my|our|these|those)\s+(?:[\w-]+\s+){0,2}?(?:chat|chats|conversation|conversations"
    r"|questions?|answers?|replies|reply|thread|session|talk)"
    r"|to\s+(?:answer|reply|respond|chat|talk)(?:\s+(?:me|us|my\s+questions|these|this|here))?"
    r"|as\s+(?:my|the|your)\s+(?:model|assistant|default|ai|provider|brain)|here|in\s+(?:this\s+)?chat|for\s+chat"
    r"|for\s+me|please|instead)\b"
    # v1.455.0 (readiness Wave G, item 19): "I want to use my Claude Pro
    # subscription WITH JARVIS" — the plan used here, by this app. Only at the
    # end of the request (or before "too / from now on / instead"), so "use
    # ChatGPT with you open in another window" is not it.
    r"|\s+(?:with|through|in|inside|for)\s+(?:(?:iron\s+)?jarvis|you|this\s+app|the\s+app)"
    r"(?=\s*(?:[.!?,;]|$|(?:too|as\s+well|instead|from\s+now\s+on|please|now)\b))"
    r"|\s*(?:[.!?]|$))"
)
#: "use Claude for this chat", "switch to Gemini from now on", "try Google's
#: model", "answer with GPT-5 by default", "use OpenRouter for the big
#: questions".
USE_VENDOR = re.compile(
    _REQUEST_AT
    + r"(?:please\s+)?(?:just\s+)?(?:use|switch\s+(?:me\s+|over\s+)?to|try|answer\s+(?:me\s+)?(?:with|using|through)"
    r"|go\s+with|pick|choose|run\s+on|default\s+to|move\s+(?:me\s+)?(?:over\s+)?to)\s+"
    # A model's own name may follow the vendor ("Claude Sonnet 4.5"); a
    # function word never does ("use Claude for this" is not "…for this chat").
    r"(?:(?:my|the|your|our)\s+)?(?:" + PLAN + r"|" + VENDOR
    + r"(?:\s+(?!(?:for|to|in|on|with|as|by|from|instead|here|and|or|but|so|please|now|at|about|into|of|if"
    r"|when|the|a|an|my|this|that|it)\b)[\w.-]{1,20}){0,2}?)" + ANSWERS_TAIL,
    re.IGNORECASE,
)
#: "I have a ChatGPT Plus plan, can you use that to answer?", "we pay for
#: Claude Max, use it".
USE_PLAN = re.compile(
    r"\b(?:i|we)\s+(?:have|['’]ve\s+got|got|pay\s+for|am\s+on|['’]m\s+on|are\s+on|['’]re\s+on|subscribe\s+to)\s+"
    + PLAN + r"\b[^.?!\n]{0,60}?\b(?:use|switch\s+to|try|answer\s+(?:with|through|using)|run\s+on|go\s+with)\s+"
    r"(?:that|it|this|those|them|(?:my|the|that)\s+(?:plan|subscription|account|key))\b",
    re.IGNORECASE,
)
#: "set up Gemini so I can try Google's model", "connect my OpenAI account",
#: "add Claude" — never one put INTO the user's content ("add Gemini to the
#: comparison sheet"), never for someone else, never on another device.
SET_UP_VENDOR = re.compile(
    _REQUEST_AT
    + r"(?:please\s+)?(?:set\s*up|setup|connect|add|enable|turn\s+on|hook\s+up|configure|install|link)\s+"
    r"(?:(?:my|the|a|an|your|our)\s+)?(?:(?:api\s+)?keys?\s+for\s+|access\s+to\s+)?" + VENDOR + r"\b"
    # Review (A1): the vendor's OWN key, plan or account named right after it
    # — "connect my Anthropic API key so you can use Claude" (it reached no
    # door: the lookahead below refuses "Anthropic API", the product, and a
    # key is that product's). The lookaheads still read what follows.
    # Possessive (never given back): "add my OpenAI account to the vendor
    # list" must be judged on what follows "account", not on "account" itself.
    r"(?:\s+(?:api\s+)?keys?|\s+(?:plan|subscription|account))?+"
    r"(?!\s+(?:support|integration|plugins?|sdk|client|library|package|extension|example|tutorial|section"
    r"|chapter|paragraph|instructions|docs?|bindings?|comparison|column|row|logo|pricing|prices?|news|story"
    r"|stock|shares?|article|results?|answers?|output|response|style|voice|api)\b)"
    r"(?!\s+(?:to|into|in|on|with|inside)\s+(?!(?:jarvis|iron\s+jarvis|you|me|this\s+(?:pc|app|computer|chat)"
    r"|the\s+app|here|my\s+(?:chats?|default|pc|computer))\b)[\w-]+)"
    r"(?![^.!?\n]{0,30}?\bfor\s+(?:my|her|his|their|our|a|an)\s+(?:(?:new|little|older|younger)\s+)?"
    r"(?:brother|sister|mom|mother|dad|father|wife|husband|son|daughter|kids?|friends?|clients?|colleagues?"
    r"|coworkers?|co-workers?|boss|team|parents?|family|students?|class)\b)",
    re.IGNORECASE,
)


def names_a_model_provider(text: str) -> bool:
    """A cloud model vendor or plan asked to answer, or to be set up."""
    t = str(text or "")
    return any(p.search(t) is not None for p in (USE_VENDOR, USE_PLAN, SET_UP_VENDOR))


#: v1.455.0 (readiness Wave G, item 23): which providers a vendor word means.
#: A vendor counts as connected when ANY of them answers (its plan's CLI
#: signed in, or its API key saved), so the Connections door is not offered
#: beside a vendor that already works.
_VENDOR_WORD = re.compile(r"\b" + VENDOR + r"\b", re.IGNORECASE)
_VENDOR_PROVIDERS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (re.compile(p, re.IGNORECASE), ids)
    for p, ids in (
        (r"^(?:claude|anthropic)", ("claude-cli", "anthropic")),
        (r"^(?:chatgpt|openai|gpt|codex)", ("codex-cli", "openai")),
        (r"^(?:gemini|google)", ("google",)),
        (r"^open\s*router", ("openrouter",)),
        (r"^(?:grok|xai)", ("grok-cli", "xai")),
        (r"^mistral", ("mistral",)),
        (r"^deepseek", ("deepseek",)),
        (r"^perplexity", ("perplexity",)),
    )
)


def model_providers_named(text: str) -> list[tuple[str, ...]]:
    """For each vendor the model-provider request names (read inside the
    matched request only), the providers that vendor means — in order, once
    each; ``[]`` when there is no such request. Never raises."""
    t = str(text or "")
    out: list[tuple[str, ...]] = []
    try:
        for rx in (USE_VENDOR, USE_PLAN, SET_UP_VENDOR):
            for m in rx.finditer(t):
                for v in _VENDOR_WORD.finditer(m.group(0)):
                    for prx, ids in _VENDOR_PROVIDERS:
                        if prx.search(v.group(0)):
                            if ids not in out:
                                out.append(ids)
                            break
    except Exception:  # noqa: BLE001 — a matcher fault names nothing
        return []
    return out


# --------------------------------------------------------------------------- #
# Someone else's SCHEDULE (v1.448.0, Wave A, A1 — lifted out of
# ``daemon.change_claims``, which reads it from here now)
# --------------------------------------------------------------------------- #
#: Before "schedule": someone else's schedule — a CPA's (depreciation,
#: amortization, payment, lead…), a person's (work, workout, class…), never
#: one of Iron Jarvis's. "Schedule C" never reaches here: its head is "c".
#: ``settings.records`` reads it so "remind me how depreciation schedules
#: work" arms no schedule writer; ``daemon.change_claims`` so a reply about
#: one is never a claimed change.
NOT_OUR_SCHEDULE_BEFORE = frozenset(
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


# --------------------------------------------------------------------------- #
# v1.506.0 (north-star plan 6.3, 6.6): the places chat has no tool for, and
# a backup made now. Every gap bounded, read one sentence at a time.
# --------------------------------------------------------------------------- #
#: "back up Iron Jarvis now", "make a backup of my settings right away", "take
#: a backup now" — a backup made NOW (Iron Jarvis's own, :data:`JARVIS_BACKUP`,
#: or a bare "backup now" with no other thing named).
_NOW = re.compile(r"\b(?:now|right\s+(?:now|away)|immediately|today|before\s+(?:i|we|you))\b", re.IGNORECASE)
_BACKUP_NOW_BARE = re.compile(
    r"\b(?:make|take|run|create|do|start)\s+(?:a|an|another|one\s+more|a\s+fresh|a\s+new)\s+backup\s+"
    r"(?:now|right\s+(?:now|away)|immediately)\b|\bback\s*up\s+now\b",
    re.IGNORECASE,
)
_ASKING_ABOUT = re.compile(r"\b(?:how\s+often|when\s+(?:does|do|did|is)|where\s+(?:are|is|do)|how\s+many)\b", re.I)


def backup_now_asked(text: str) -> bool:
    """Does *text* ask Iron Jarvis to back ITSELF up now (``backup_now``)?
    Never "back up my client files" (the user's own things), never a question
    about the daily backup ("how often does it back up?")."""
    for s in sentences(text):
        if _ASKING_ABOUT.search(s):
            continue
        if _BACKUP_NOW_BARE.search(s) or (JARVIS_BACKUP.search(s) and _NOW.search(s)):
            return True
    return False


#: Restoring IRON JARVIS from one of its backups (never the user's files).
_USERS_THINGS = re.compile(
    r"\b(?:files?|folders?|documents?|photos?|pictures?|laptop|phone|iphone|computer|pc|drive|e-?mails?"
    r"|spreadsheets?|workbooks?|database\s+of\s+(?:my|the)\s+clients?|website|server|repo(?:sitory)?)\b",
    re.IGNORECASE,
)
_RESTORE = re.compile(
    r"\brestor(?:e|ing)\s+" + _OWNER + _JARVIS_THINGS + r"\b" + _NOT_JARVIS_AFTER
    + r"|\b(?:restor(?:e|ing)|roll(?:ing)?\s+back|go(?:ing)?\s+back|revert(?:ing)?)\b[^.!?\n]{0,40}?"
    r"\b(?:from|to|using)\s+(?:an?\s+|the\s+|my\s+|your\s+|yesterday['’]s\s+|last\s+(?:night|week)['’]s\s+)?"
    r"(?:(?:earlier|older|previous|last|latest|automatic)\s+)?backups?\b",
    re.IGNORECASE,
)


def restore_asked(text: str) -> bool:
    """Does *text* ask to put Iron Jarvis back from one of its backups?"""
    return any(_RESTORE.search(s) and not _USERS_THINGS.search(s) for s in sentences(text))


#: Installing, holding or checking an update of Iron Jarvis itself.
_UPDATE = re.compile(
    r"\b(?:install|apply|get|download|skip|hold|pause|postpone|delay|stop|block|turn\s+off|disable)\s+"
    r"(?:the\s+|this\s+|that\s+|an?\s+|any\s+|all\s+|new\s+|latest\s+){0,3}(?:iron\s+jarvis\s+|jarvis\s+|app\s+)?"
    r"updates?\b(?!\s+(?:to|of|on|in|for)\s+(?!(?:iron\s+)?jarvis\b|the\s+app\b|you\b))"
    r"|\b(?:update|upgrade)\s+(?:iron\s+jarvis|jarvis|yourself|the\s+app|this\s+app)\b"
    r"|\b(?:check|look)\s+(?:for|if\s+there\s+(?:is|are))\s+(?:an?\s+|any\s+|new\s+){0,2}updates?\b"
    r"|\b(?:don['’]?t|do\s+not|never)\s+(?:auto[\s-]?)?update\s+(?:yourself|iron\s+jarvis|jarvis|the\s+app)\b"
    r"|\b(?:is\s+there|are\s+there)\s+(?:an?\s+|any\s+)?(?:new\s+)?(?:updates?|version)\s+(?:of\s+)?"
    r"(?:iron\s+jarvis|jarvis|you|the\s+app|available)\b",
    re.IGNORECASE,
)


def update_asked(text: str) -> bool:
    """Does *text* ask to check for, install or hold an update of Iron Jarvis?"""
    return _UPDATE.search(str(text or "")) is not None


#: Pairing this computer with another one running Iron Jarvis.
_PC = r"(?:computers?|pcs?|laptops?|machines?|desktops?|workstations?|macs?|macbooks?)"
_PAIR = re.compile(
    r"\b(?:pair|re-?pair|link|connect)\w*\s+(?:this\s+" + _PC + r"|my\s+(?:two|2|other|office|work|home|second|spare|main)\s+"
    + _PC + r"|(?:my|our|the)\s+" + _PC + r"|(?:it|you|iron\s+jarvis|jarvis))\s+(?:with|to|and)\s+"
    r"(?:my|our|the|another|a\s+second|this)\s+(?:(?:other|office|work|home|second|spare|main)\s+)?"
    r"(?:" + _PC + r"|(?:iron\s+)?jarvis)\b"
    r"|\b(?:pair|link)\s+(?:my|our|the|two|both)\s+(?:two\s+)?" + _PC + r"\b"
    r"|\bpairing\s+code\b|\bpair(?:ed|ing)?\s+computers?\b|\byour\s+other\s+computers\b",
    re.IGNORECASE,
)


def pairing_asked(text: str) -> bool:
    """Does *text* ask to pair this computer with another one running Iron
    Jarvis (Settings › Connections › Your other computers)? Never a phone or
    a Bluetooth device ("pair my headphones")."""
    return _PAIR.search(str(text or "")) is not None


#: Removing a saved key, token or password (the Secrets page; never a tool).
_KEY = re.compile(
    r"\b(?:delete|remove|revoke|clear|erase|wipe|get\s+rid\s+of|take\s+out)\s+(?:my\s+|the\s+|that\s+|this\s+"
    r"|a\s+|an\s+|old\s+|saved\s+|stored\s+){0,3}(?:[\w-]+\s+){0,2}"
    r"(?:api[\s-]?keys?|access\s+keys?|secret\s+keys?|keys?|tokens?|passwords?|credentials?|secrets?)\b"
    r"(?!\s+(?:protection|protected|field|column|cell|rows?|from\s+(?:the|this|my)\s+(?:pdf|document|file|sheet)))",
    re.IGNORECASE,
)
_KEY_NOT = re.compile(
    r"\b(?:pdf|document|spreadsheet|workbook|sheet|word\s+file|excel|zip|keyboard|piano|code|config|dictionary"
    r"|dict|json|yaml|repo(?:sitory)?|script)\b|\.(?:py|js|ts|json|env|ya?ml|toml|ini)\b",
    re.I,
)


def key_removal_asked(text: str) -> bool:
    """Does *text* ask to remove a saved key or password?"""
    return any(_KEY.search(s) and not _KEY_NOT.search(s) for s in sentences(text))


#: Forgetting a folder's work in the work record (the Memory page).
_FORGET_FOLDER = re.compile(
    r"\b(?:forget|stop\s+(?:remembering|tracking|recording)|remove|drop|erase|wipe|clear)\s+"
    r"(?:(?:everything|all|the\s+work)\s+(?:about|from|in|for)\s+)?(?:the|this|that|my)\s+(?:[\w-]+\s+){0,2}folder\b"
    r"(?=[^.!?\n]{0,60}?\b(?:work\s+record|record|history|memory|remember(?:ed|s)?|you\s+know|what\s+you\s+did)\b)"
    r"|\bforget\s+(?:the\s+)?(?:work|everything)\s+(?:you\s+did\s+)?in\s+(?:the|this|that|my)\s+(?:[\w-]+\s+){0,2}folder\b",
    re.IGNORECASE,
)


def forget_folder_asked(text: str) -> bool:
    """Does *text* ask Iron Jarvis to forget a folder's work?"""
    return _FORGET_FOLDER.search(str(text or "")) is not None


#: Deleting a project (archiving one is a chat tool, project_archive).
_DELETE_PROJECT = re.compile(
    r"\b(?:delete|remove|erase|get\s+rid\s+of)\s+(?:the|this|that|my|our)\s+(?:[\w-]+\s+){0,3}project\b"
    r"(?!\s+(?:files?|folders?|notes?|brief|plan|budget|deadline|tasks?|members?)\b)",
    re.IGNORECASE,
)


def project_delete_asked(text: str) -> bool:
    return _DELETE_PROJECT.search(str(text or "")) is not None


#: Templates (saved prompts) and starter skill bundles (the Templates page).
_TEMPLATE = re.compile(
    r"\b(?:make|create|save|add|delete|remove|change|edit|rename|load|install|unload)\s+(?:a\s+|an\s+|the\s+|this\s+|"
    r"my\s+|that\s+)?(?:new\s+)?(?:prompt\s+)?templates?\b(?!\s+(?:for\s+(?:a|an|the|my)\s+)?(?:letter|email|invoice|"
    r"document|word|excel|spreadsheet|report|contract|engagement|memo|slide|deck)s?\b)"
    r"|\b(?:load|install|add|remove|unload)\s+(?:a\s+|the\s+|this\s+)?(?:starter\s+)?skill\s+bundles?\b"
    r"|\bskill\s+bundles?\b",
    re.IGNORECASE,
)


def template_asked(text: str) -> bool:
    return _TEMPLATE.search(str(text or "")) is not None


#: Deleting, archiving or renaming a chat or a coding session.
_CHAT_THING = re.compile(
    r"\b(?:delete|remove|erase|archive|unarchive|rename|clear)\s+(?:the|this|that|my|our|all\s+(?:my|the)|old)\s+"
    r"(?:(?:old|previous|last|other|current)\s+)?(?:chats?|conversations?|threads?|coding\s+sessions?)\b",
    re.IGNORECASE,
)


def chat_item_asked(text: str) -> bool:
    return _CHAT_THING.search(str(text or "")) is not None


#: Every compiled pattern over the user's words (the linear-time pin reads it).
ALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    JARVIS_BACKUP, _SENTENCE, TO_MY_PHONE, SET_UP_TO_MESSAGE_ME, _EDITS_A_WEBHOOK, _EVERY_NEW, _STANDING, _MAIL, _MAIL_ARRIVES, WATCH_FOLDER,
    _ACTS_ON_ARRIVALS, _BUILTIN_AGENT, _LIMIT, _LIMIT_VERB, VOICE, INTERNET_CALLS_IN, _OUTBOUND,
    USE_VENDOR, USE_PLAN, SET_UP_VENDOR, FILES_ARRIVE_IN_FOLDER, MAIL_FROM_RULE, _VENDOR_WORD,
    _NOW, _BACKUP_NOW_BARE, _ASKING_ABOUT, _USERS_THINGS, _RESTORE, _UPDATE, _PAIR, _KEY, _KEY_NOT,
    _FORGET_FOLDER, _DELETE_PROJECT, _TEMPLATE, _CHAT_THING,
)
