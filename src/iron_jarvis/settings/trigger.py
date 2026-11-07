"""WHICH SETTING A CHAT MESSAGE IS ABOUT, read off the schema's own words
(chat-first plan, Wave 2 group E, B1.1 — the owner's decision D2).

Before this, chat offered its settings tools only for words on ONE hand-kept
regex (``settings.tools._SETTINGS_WORDS``), while the schema already carried
a label, aliases and option names for every setting — used only by
``config_list``'s search. Measured at v1.432.0: 29 of 57 plain requests about a
setting armed nothing ("keep my PC awake while you work", "use Sonnet by
default", "let other computers on my network use Jarvis"), and "switch updates
to the stable channel" armed the NOTIFICATION channel tools instead.

Now the trigger is generated from every schema row (and every secret): its
label, its aliases and the words of its options, each turned into a PHRASE of
content words. A message is about a setting when it holds one of those phrases
— the words in order, at most two other words between each (``_GAP``) — or one
of the few request shapes a phrase cannot spell (``_EXTRA_REFERENCES``: a
model's name with "by default", "open files in Cursor", "turn off web
search"). Three guards keep a mere mention from arming a WRITER:

* A CHANGE CUE (``_CUE``): writers are armed only when the message also asks
  for something ("turn on", "use", "stop", "let", "set", "make"…). "The theme
  of my essay", "a status update", "the model of my car" name no change.
* A QUESTION arms only the reader ``config_list`` (``records.is_question``,
  the record tools' own guard): "what is my default model?" is answered by
  reading, never by a writer.
* A WORD THAT IS ALSO EVERYDAY (``_GENERIC``: theme, model, persona,
  browser…) counts only as "the / my / your <word>" right after a cue, and
  never when it is about something else ("…of my essay", "…in this
  spreadsheet", "…to look something up"). An everyday word that is too common
  to read at all (tone, formatting, power…) never counts alone; its row is
  reached by its longer phrases.

Pure and deterministic; never raises (a fault reads as "not about a setting").
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from functools import lru_cache

from . import schema
from . import setup_words as _W

#: Words dropped from a PHRASE (never from the message): a phrase is its
#: content words, so "Keep this PC awake" matches "keep my PC awake".
_STOP = frozenset(
    "a an the my your our their his her its this that these those of for to in on at by "
    "with and or me i it is are be from as per you we us can may will would should do does "
    "who what which when while if than then there here so".split()
)
#: How many other words may sit between two words of a phrase.
_GAP = 2
#: How far before (or after) a phrase its change cue may stand.
_CUE_BEFORE = 10
_CUE_AFTER = 3

#: Single words that name THIS app's settings and little else: one is enough
#: (a change cue is still needed for a writer).
_DISTINCT = frozenset(
    {
        "ollama", "sentinel", "watcher", "autonomy", "curator", "vscode", "pixio", "agents.md",
        "claude.md",
    }
)
#: Everyday single words a setting is also called: they count only as
#: "the / my / your <word>" right after a change cue (``_generic_ok``).
_GENERIC = frozenset(
    {
        "theme", "model", "persona", "browser", "editor", "approval", "update", "notification",
        "accessibility", "brain", "calendar", "proxy", "colors", "color", "settings", "setting",
        "preferences", "preference", "notifications", "voice",
    }
)
#: Words too common to read alone at all (their rows are reached by longer
#: phrases): "change the tone of this email", "turn the power off".
_NEVER_ALONE = frozenset(
    {
        "tone", "formatting", "power", "sleep", "chrome", "edge", "about", "use", "codex",
        "automatic", "auto", "off", "fresh", "stable", "low", "full", "daylight",
    }
)

#: "the max steps SETTING": the word itself says it is one of the app's
#: settings, whatever stands before it (a change cue is still needed).
_SETTING_WORDS = frozenset({"setting", "settings", "preference", "preferences"})
#: A determiner (or the cue itself) that makes "<word>" this app's setting.
_OWN_DETERMINERS = frozenset({"the", "my", "your", "our", "jarvis's", "app's", "chat's", "default"})
#: After a generic word: it is about something else.
_ELSEWHERE_PREPS = frozenset({"of", "in", "on", "for", "from", "with", "into", "inside"})
_ELSEWHERE_DETS = frozenset(
    {"this", "that", "the", "these", "those", "a", "an", "his", "her", "their", "its", "some"}
)
#: Right before a phrase's last word: someone else's or any one ("a model
#: like ARIMA", "his browser") — never this app's setting.
_NOT_OURS_BEFORE = frozenset({"a", "an", "his", "her", "their", "its", "some", "another", "any"})
#: Right after a phrase: the words name a different thing ("profile
#: picture", "theme park", "model car").
_EXTENDERS = frozenset(
    {
        "picture", "photo", "pic", "image", "page", "park", "parks", "song", "music", "tune", "car",
        "cars", "railway", "number", "card", "letter", "name", "train", "kit", "home",
        # v1.434.0 review: the code a coding chat writes ("add a watcher
        # function to my Vue component").
        "function", "functions", "handler", "handlers", "callback", "callbacks", "class", "method",
        "component", "variable",
        # Review (v1.442.0): "my voice MEMO app", "voice mail", "a voice
        # recorder" are other programs, never the app's voice.
        "memo", "memos", "mail", "recorder", "recording", "recordings",
    }
)
#: Review (v1.442.0): "add Ollama to the comparison SHEET" puts the words into
#: the user's own content (``_about_something_else``).
_CONTENT_NOUNS = frozenset(
    {
        "sheet", "sheets", "spreadsheet", "spreadsheets", "doc", "docs", "document", "documents", "deck",
        "slide", "slides", "report", "reports", "readme", "page", "email", "memo", "notes", "budget",
        "presentation", "proposal", "agenda", "checklist", "table", "chart", "letter", "newsletter", "wiki",
    }
)
#: v1.438.0 (Wave 4 group C): after "…windows", a word that names one of
#: WINDOWS' own things — "start Windows Update", "start Windows Defender" —
#: is never "start Iron Jarvis with Windows".
_WINDOWS_THINGS = frozenset(
    {
        "update", "updates", "defender", "explorer", "firewall", "terminal", "security", "store",
        "search", "installer", "install", "installation", "license", "key", "activation", "backup",
        "restore", "recovery", "repair", "troubleshooter", "sandbox", "subsystem", "shell", "server",
        "machine", "machines", "vm", "laptop", "pc", "computer", "computers", "box", "partition",
        "drive", "service", "services", "task", "tasks", "event", "events", "media", "photos",
        "mail", "calendar", "hello", "ink", "insider", "safe", "settings", "control", "registry",
    }
)
#: v1.438.0 review: a machine named right before "…start with Windows".
_DEVICES = frozenset(
    {
        "laptop", "laptops", "pc", "pcs", "computer", "computers", "machine", "machines",
        "workstation", "server", "servers", "printer", "scanner", "tablet", "surface", "vm",
        "monitor", "dell", "lenovo", "thinkpad",
    }
)
#: …unless the thing after it is this app ("the theme in the app").
_APP_WORDS = frozenset(
    {"app", "jarvis", "iron", "chat", "chats", "settings", "setting", "dashboard", "build", "code"}
)
#: "pause notifications for an hour" is still this app's notifications.
_TIME_UNITS = frozenset({"minute", "hour", "day", "week", "month", "while", "bit", "moment"})
#: "use my browser TO look something up" — an action, not a setting.
_ACTION_AFTER_TO = frozenset(
    {
        "look", "find", "search", "check", "get", "open", "read", "book", "buy", "order", "fill",
        "send", "download", "log", "visit", "browse", "watch", "write", "make", "draft", "build",
        "answer", "summarize", "summarise", "do", "pay", "sign",
    }
)

#: THE CHANGE CUE. A writer is armed only when the message asks for a change;
#: the words here are the verbs and request shapes people use for one.
_CUE = re.compile(
    r"\b(?:turn(?:ed|ing|s)?\s+(?:[\w'-]+\s+){0,3}?(?:on|off|up|down)"
    r"|switch(?:ed|ing|es)?|set(?:ting|s)?|change|make|use|using|enable|disable|stop|start"
    r"|keep|let|allow|don'?t|do\s+not|never|always|put|add|remove|pause|resume|unpause"
    r"|go\s+back|reset|restore|raise|lower|increase|decrease|bump|limit|cap|share|copy"
    r"|back\s+up|move|point|connect|disconnect|give|configure|choose|pick|select|show|hide"
    r"|mute|unmute|silence|forget|clear|block|require|prefer|from\s+now\s+on|by\s+default"
    r"|i\s+(?:want|need|would\s+like|'d\s+like)|please|can\s+you|could\s+you|read|talk"
    r"|speak|save|store|opt|approve|only\s+let|only\s+allow|run\s+on|go\s+to"
    r"|wake|tidy|archive|learn|hand|ask|quit|anymore|any\s+more)\b",
    re.IGNORECASE,
)

#: v1.438.0 (Wave 4 group C): the languages the profile can answer in
#: (``profile.language.LANGUAGES``' names, and Mandarin) — for "answer me in
#: Spanish from now on".
_LANG_NAMES = (
    r"(?:english|spanish|french|german|portuguese|italian|dutch|polish|swedish|turkish|russian"
    r"|ukrainian|greek|hebrew|arabic|hindi|thai|chinese|mandarin|japanese|korean)"
)
#: A clause that opens as a REQUEST (the start of a line or a sentence, after
#: "please", "can you", "I want you to"…) — never "I always answer in Spanish"
#: (a sentence about the user), never "my clients reply in French".
_ASKS_AT = (
    r"(?:^|(?<=[.!?;:,\n])\s*|\bplease\s+|\b(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"|\bi\s+(?:want|need|would\s+like)\s+you\s+to\s+|\bi'?d\s+like\s+you\s+to\s+|\band\s+)"
)
#: Words that make a request LASTING ("from now on", "by default", "always").
_LASTING_WORDS = (
    r"(?:from\s+now\s+on|by\s+default|going\s+forward|from\s+here\s+on|every\s+time|always"
    r"|in\s+(?:all|every)\s+(?:of\s+)?(?:your\s+)?(?:answers?|repl(?:y|ies)|responses?)"
    r"|even\s+(?:if|when)\b|no\s+matter\s+what|whatever\s+language)"
)
_ANSWER_VERB = r"(?:answer|reply|respond|talk|speak|write|chat)"
#: The answer formats the profile's Formatting row has words for.
_FORMATS = (
    r"(?:bullet(?:\s+points?|ed\s+lists?)?|bullets|headings|numbered\s+lists?|tables|short\s+paragraphs"
    r"|plain\s+prose|prose|paragraphs)"
)

#: Request shapes a phrase cannot spell. Each one names a setting AND asks for
#: a change, so it counts as both.
_MODEL_NAMES = (
    r"(?:sonnet|opus|haiku|claude(?:[\s-]+[\w.-]+)?|gpt[\w.-]*|chatgpt|gemini[\w.-]*|codex"
    r"|llama[\w.-]*|qwen[\w.-]*|glm[\w.-]*|mistral|deepseek|grok|ollama|o\d(?:-\w+)?"
    r"|(?:my\s+)?local\s+(?:\w+\s+)?model|(?:my\s+)?own\s+model)"
)
#: v1.442.0 (Wave 7 group C): the colour and feel words a theme of the user's
#: own is asked for in ("something green and calm", "a dark blue theme").
_THEME_COLOURS = (
    r"(?:green|blue|purple|violet|teal|red|orange|yellow|pink|gold|golden|cyan|navy|indigo|emerald"
    r"|mint|sage|lime|olive|forest|turquoise|aqua|magenta|lavender|coral|brown|amber|grey|gray"
    r"|silver|maroon|burgundy|crimson|plum|peach|beige|sand|slate|charcoal|black|white)"
)
_THEME_MOODS = (
    r"(?:calm|calming|calmer|soft|softer|warm|warmer|cool|cooler|muted|pastel|earthy|vibrant|bright"
    r"|brighter|dark|darker|light|lighter|deep|rich|relaxing|soothing|bold|subtle|neon|moody|gentle)"
)
#: After a theme word: not one for something ELSE ("…for my slides", "…in
#: the deck", "…of the website", "…with my logo"); "…for the app" and "…with
#: teal" are still ours.
_THEME_APP = r"(?:the\s+|this\s+)?(?:app|jarvis|iron\s+jarvis|dashboard|chat|interface)\b"
_NOT_FOR_SOMETHING_ELSE = (
    r"(?!\s+(?:for|in|on|of|to|inside|into)\s+(?!" + _THEME_APP + r")"
    r"|\s+with\s+(?!" + _THEME_APP + r"|(?:a\s+|an\s+|some\s+|more\s+)?(?:" + _THEME_COLOURS + r"|"
    + _THEME_MOODS + r")\b))"
)
#: v1.442.0 review: what may FOLLOW a theme word in a request. A list of
#: what may not ("…for my slides") let every other tail through: "…at the
#: baby shower", "…this Christmas", "…when you draw the chart", "…, then
#: draw a poster", "a theme song", "a theme park". So the request must END
#: there (or say please / instead / from now on / thanks), or name this app,
#: or add a colour or feel ("…with teal").
_THEME_TAIL = (
    r"(?=\s*(?:[.!?;\n]|$)"
    r"|\s*,\s*(?:please|thanks|thank\s+you)\b"
    r"|\s+(?:please|instead|now|again|for\s+me|thanks|thank\s+you|from\s+now\s+on|going\s+forward)\b"
    r"|\s+(?:for|in|on|inside)\s+" + _THEME_APP
    + r"|\s+with\s+(?:a\s+|an\s+|some\s+|more\s+)?(?:" + _THEME_COLOURS + r"|" + _THEME_MOODS + r")\b)"
)
#: …and where the request may START: a clause of its own ("use…", "Hey
#: Jarvis, use…", "please use…", "can you use…") — never "the slides should
#: use a dark blue theme", whose subject is something else.
_THEME_ASKS_AT = r"(?:" + _ASKS_AT + r"|\b(?:just|also|now|then|so|ok|okay|jarvis|let'?s)\s+)"
_EXTRA_REFERENCES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "use Sonnet by default", "make Gemini my default", "use my local
        # model by default".
        _MODEL_NAMES + r"\b[^.?!\n]{0,40}?\b(?:by\s+default|as\s+(?:my|the)\s+default|my\s+default)\b",
        r"\b(?:by\s+default|as\s+(?:my|the)\s+default|my\s+default)\b[^.?!\n]{0,24}?\b"
        r"(?:model|ai|provider)\b",
        # "talk to me like a pirate by default", "answer in French by default".
        r"\b(?:talk|speak|answer|reply|respond|write|sound)\b[^.?!\n]{0,60}?\bby\s+default\b",
        # v1.438.0 (Wave 4 group C): the profile's answer language — "answer
        # me in Spanish from now on", "always reply in French", "from now on,
        # write to me in German", "stick to Spanish even if I write English".
        _ASKS_AT + r"(?:always\s+)?" + _ANSWER_VERB + r"\s+(?:to\s+)?(?:me\s+)?(?:only\s+)?in\s+" + _LANG_NAMES
        + r"\b[^.?!\n]{0,40}?\b" + _LASTING_WORDS,
        _ASKS_AT + r"(?:from\s+now\s+on|going\s+forward|always)\s*,?\s+(?:please\s+)?" + _ANSWER_VERB
        + r"\s+(?:to\s+)?(?:me\s+)?(?:only\s+)?in\s+" + _LANG_NAMES + r"\b",
        _ASKS_AT + r"stick\s+(?:to|with)\s+" + _LANG_NAMES + r"\b\s*(?:[.!]|$|" + _LASTING_WORDS + r")",
        _ASKS_AT + r"(?:switch|change|set)\s+(?:my\s+|your\s+|the\s+)?(?:answer\s+|reply\s+|response\s+)?"
        r"language\s+to\s+" + _LANG_NAMES + r"\b",
        # …and its answer format: "use bullet points by default", "from now
        # on, answer with headings".
        _ASKS_AT + r"(?:always\s+)?(?:use|prefer|give\s+me|answer\s+(?:with|in)|reply\s+(?:with|in)"
        r"|write\s+(?:with|in)|format\s+(?:your\s+)?(?:answers?|repl(?:y|ies))\s+(?:with|as|in))\s+"
        + _FORMATS + r"\b[^.?!\n]{0,30}?\b" + _LASTING_WORDS,
        _ASKS_AT + r"(?:from\s+now\s+on|going\s+forward|always)\s*,?\s+(?:please\s+)?(?:use|answer\s+(?:with|in)"
        r"|reply\s+(?:with|in)|write\s+(?:with|in))\s+" + _FORMATS + r"\b",
        # v1.438.0 review: the app named, then it starting by itself at
        # sign-in — "make Jarvis open automatically when I log in".
        r"\b(?:iron\s+jarvis|jarvis|the\s+desktop\s+app)\s+(?:to\s+)?(?:open|start|launch|run)s?\b"
        r"[^.?!\n]{0,24}?\b(?:when\s+i\s+(?:log|sign)\s+(?:in|on)|at\s+(?:login|log\s*in|sign\s*in|startup)"
        r"|on\s+startup|with\s+windows)\b",
        # "open files in Cursor", "use VS Code for files".
        r"\b(?:open|edit|show)\b[^.?!\n]{0,30}?\b(?:in|with)\s+(?:cursor|vs\s*code|vscode)\b",
        r"\b(?:use|switch\s+to|set|pick|choose)\s+(?:cursor|vs\s*code|vscode)\b",
        # v1.434.0 review: "make the app dark", "turn Jarvis light" (the theme
        # with no theme word).
        r"\b(?:make|turn|switch|set)\s+(?:the\s+)?(?:app|jarvis|iron\s+jarvis|dashboard|screen"
        r"|interface)\s+(?:look\s+)?(?:dark|darker|light|lighter)\b",
        # v1.442.0 (Wave 7 group C): a theme of the user's own colours
        # (device.theme_colors). "Make the app purple", "turn Jarvis a calm
        # green" — the APP named, never "make the logo purple".
        r"\b(?:make|turn|switch|set|change|paint|colou?r)\s+(?:the\s+|my\s+)?(?:app|jarvis|iron\s+jarvis"
        r"|dashboard|interface)\s+(?:look\s+|to\s+|into\s+)?(?:(?:more|a\s+bit|a\s+little|really|very"
        r"|kind\s+of|sort\s+of|something|a|an)\s+)?(?:" + _THEME_MOODS + r"\s+(?:and\s+)?){0,2}"
        + _THEME_COLOURS + r"\b"
        # Review: never the dashboard of ANOTHER program ("…in Power BI").
        + r"(?!\s+(?:in|on|of|for|inside|into)\s+(?!" + _THEME_APP + r"))",
        # "use a dark blue theme", "switch to a green theme", "give me a calm
        # green look" — never one FOR something else ("a green theme for my
        # slides", "a dark palette for the website").
        # Review: ONE optional article followed by its own space — the old
        # ``\s+(?:a|…)?\s*`` could split a whitespace run two ways and was
        # quadratic ("use" + 4,000 spaces: about 5 s).
        _THEME_ASKS_AT
        + r"(?:use|switch\s+to|change\s+(?:it\s+|the\s+theme\s+)?to|set\s+(?:it\s+|the\s+theme\s+)?to"
        r"|go\s+(?:with|for)|give\s+me|try|pick|choose|make\s+me|create|apply|i\s+(?:want|need)"
        r"|i'?d\s+like|i\s+would\s+like)\s+(?:(?:a|an|some|the|my)\s+)?(?:(?:" + _THEME_COLOURS + r"|"
        + _THEME_MOODS + r"|and|or)(?:ish)?[\s,-]+){1,4}(?:theme|colou?r\s+scheme|palette|look)s?\b"
        + _NOT_FOR_SOMETHING_ELSE + _THEME_TAIL,
        # "make my own theme", "set a custom theme", "create a new colour theme".
        r"\b(?:make|create|build|design|set(?:\s+up)?|give\s+me|use|i\s+(?:want|need)|i'?d\s+like)\s+"
        r"(?:me\s+)?(?:my\s+own|my\s+custom|a\s+custom|a\s+new|an?\s+own)\s+(?:colou?r\s+)?theme\b"
        + _NOT_FOR_SOMETHING_ELSE + _THEME_TAIL,
        # "turn off web search", "disable the file search tool", "never run
        # the shell tool" — a tool's permission.
        r"\b(?:turn\s+off|turn\s+on|disable|enable|block|unblock|allow|never\s+(?:run|use)"
        r"|stop\s+using|don'?t\s+(?:run|use))\s+(?:the\s+)?(?:web\s+search|web\s+fetch"
        r"|file\s+search|shell|[\w-]+(?:\s+[\w-]+)?\s+tool)\b",
        # "read your replies out loud", "read replies aloud".
        r"\bread\b[^.?!\n]{0,30}?\b(?:repl(?:y|ies)|answers?|responses?)\b[^.?!\n]{0,12}?"
        r"\b(?:out\s+loud|aloud)\b",
        # "make your answers shorter", "keep your replies brief" — YOUR
        # answers, never "the answers in this FAQ".
        r"\b(?:make|keep)\s+(?:all\s+)?(?:your\s+)?(?:answers?|repl(?:y|ies)|responses?)\s+"
        r"(?:shorter|longer|brief(?:er)?|short|long|concise|detailed|simpler)\b"
        r"(?!\s+(?:in|of|on|for)\s+(?:this|that|the|my|these|those)\b)",
        # "don't let any background job run", "stop everything".
        r"\b(?:don'?t|do\s+not|never)\s+(?:let|allow)\s+(?:any\s+)?(?:background|scheduled"
        r"|automatic|unattended)\b",
        r"\bstop\s+(?:everything|all\s+(?:the\s+)?(?:background\s+)?(?:work|jobs|tasks|runs))\b",
        # "enable reading files in my Documents folder only", "only let
        # Jarvis read my Documents folder".
        r"\bread(?:ing)?\s+(?:files\s+)?(?:in|from)\s+(?:my\s+)?[\w\s]{0,20}?folders?\s+only\b",
        # "start night tasks at 22:00", "night shift at 10pm", "set the night
        # shift to start at 10pm". v1.448.0 (Wave A, A1): a request VERB or
        # the night words as the SUBJECT of starting — "the night shift at
        # the hospital starts at 7" is a person's shift, never a setting.
        r"\b(?:start|run|begin|schedule|set|move|shift|make|have|let|change|switch|put|kick\s+off)\b"
        r"[^.?!\n]{0,24}?\bnight\s+(?:tasks?|shift)\b[^.?!\n]{0,30}?\b(?:at|from|until|till|to)\s+\d",
        r"\bnight\s+(?:tasks?|shift)\b\s+(?:should\s+|to\s+|can\s+|could\s+|may\s+)?(?:start|begin|run|end|stop|kick\s+off)s?\b"
        r"[^.?!\n]{0,20}?\b(?:at|from|until|till)\s+\d",
        r"(?:^|[.!?;:,\n])\s*(?:please\s+)?night\s+(?:tasks?|shift)\s+(?:at|from)\s+\d",
        # "use Docker for running commands", "run commands in Docker".
        r"\b(?:use|run\b[^.?!\n]{0,20}?\bin|inside)\s+docker\b",
        # "back up to my D drive too", "copy my backups to E:\Backups".
        r"\b(?:back\s+(?:me\s+)?up|copy\s+(?:my\s+|all\s+(?:my\s+)?)backups|mirror\s+(?:my\s+)?backups?)\b"
        r"[^.?!\n]{0,40}?\b(?:to|on|onto|into)\b",
        # "let other computers on my network use Jarvis".
        r"\b(?:from|on)\s+(?:my\s+)?(?:laptop|other\s+pc|another\s+pc|other\s+computer|phone)\b"
        r"[^.?!\n]{0,40}?\b(?:same|my|home|local)\s+network\b",
    )
) + (
    # v1.442.0 (chat-first plan Wave 7, group A): Iron Jarvis's OWN backup
    # ("Schedule a weekly backup of my settings to my OneDrive folder") — its
    # "Backup copy folder" is the setting that sends it there; and a folder
    # WATCHED ("Watch my Downloads folder and file new PDFs…") needs "Folder
    # watchers (sentinels)" turned on. One definition each
    # (settings.setup_words); both linear.
    _W.JARVIS_BACKUP,
    _W.WATCH_FOLDER,
    # v1.448.0 (Wave A "chat configures everything", A1): a model VENDOR or
    # PLAN asked to answer — "Use Claude for this chat instead of the
    # default", "I have a ChatGPT Plus plan, can you use that to answer?",
    # "Set up Gemini so I can try Google's model", "Use OpenRouter for the
    # big questions" (each armed nothing before). The default-provider /
    # default-model rows are what change; a key still goes only on the secure
    # card. Never "use Claude Code to refactor my repo" (work handed over),
    # "Claude Monet painted…", "my ChatGPT history is long", "Gemini is my
    # star sign", "the Google model of management" (no request, or not the
    # vendor). One definition each (settings.setup_words); all linear.
    _W.USE_VENDOR,
    _W.USE_PLAN,
    _W.SET_UP_VENDOR,
)

#: Settings-shaped words that have NO row of their own (a per-chat switch, a
#: retired page, the settings themselves): the message is still about the
#: app's settings, so the tools are armed and the model can say where the
#: switch lives. Read like any phrase (the same guards); key "".
_NO_ROW_PHRASES = (
    "settings", "preferences", "notifications", "voice", "simple mode", "local only",
    "animated background", "new chats", "every chat", "all chats", "start with windows",
    "windows starts", "night shift", "api key", "kill switch", "emergency stop",
    "minimize to the tray", "close to the tray",
    # A notification service by name, never the bare word ("add slack time
    # to the plan"): its alerts, its channel, setting it up.
    *(
        f"{service} {thing}"
        for service in ("slack", "telegram", "discord")
        for thing in ("notifications", "alerts", "channel", "destination", "messages")
    ),
    *(f"{verb} {service}" for verb in ("set up", "connect") for service in ("slack", "telegram", "discord")),
)


@dataclass(frozen=True)
class _Phrase:
    key: str
    words: tuple[frozenset[str], ...]
    kind: str  # "multi" | "distinct" | "generic"
    #: v1.434.0 review: a TWO-WORD option label ("Don't ask", "Ask first",
    #: "Never run", "Stop and tell me", "Claude Code", "Gold & Red") is two
    #: everyday words. It counts only beside a word that names its ROW
    #: (these forms) or as the target of a switch ("switch to Arc Cyan") —
    #: "don't ask me what I want for dinner" armed the approval writers, and
    #: config_set can lower the approval posture with no card. None = no
    #: such rule (every other phrase).
    row: "frozenset[str] | None" = None


@dataclass(frozen=True)
class SettingsAsk:
    """What a chat message asks of the settings tools.

    ``reader`` — arm ``config_list`` (the message is about a setting);
    ``writers`` — arm the writers too (a change was asked for, not a
    question); ``keys`` — the schema rows it names, most specific first (for
    a hint; empty when only a no-row word or an extra shape matched)."""

    reader: bool = False
    writers: bool = False
    keys: tuple[str, ...] = ()


_NONE = SettingsAsk()
_TOKEN = re.compile(r"[a-z0-9]+(?:['.][a-z0-9]+)*")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower().replace("\u2019", "'"))


def _forms(word: str) -> frozenset[str]:
    """A word and its everyday inflections (plurals, -ing, -ed), so "replies"
    matches "reply" and "asking" matches "ask"."""
    w = word
    out = {w, w + "s", w + "es", w + "ed", w + "d", w + "ing", w + "er"}
    if w.endswith("y") and len(w) > 2:
        out |= {w[:-1] + "ies", w[:-1] + "ied"}
    if w.endswith("e"):
        out |= {w[:-1] + "ing", w[:-1] + "ed"}
    if len(w) > 2 and w[-1] not in "aeiouwy" and w[-2] in "aeiou" and w[-3] not in "aeiou":
        out |= {w + w[-1] + "ing", w + w[-1] + "ed"}
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        out.add(w[:-1])
    if w.endswith("ies"):
        out.add(w[:-3] + "y")
    return frozenset(out)


def _phrase_words(text: str) -> list[str]:
    return [t for t in _tokens(text.replace("-", " ").replace("_", " ")) if t not in _STOP]


def _strip_parens(label: str) -> str:
    return re.sub(r"\([^)]*\)", " ", label)


#: Words that never name a row by themselves (v1.434.0 review): the app's own
#: name, the chat, numbers, and the everyday words labels share.
_NOT_ROW_NAMES = frozenset(
    {"chat", "chats", "app", "jarvis", "iron", "default", "new", "mode", "one", "two", "my", "your",
     "setting", "settings"}
)
#: The verbs that make "<verb> … to <option>" a SWITCH ("switch to Arc Cyan",
#: "change it to Liquid Glass", "go back to Daylight").
_SWITCH_VERBS = frozenset(
    {"switch", "switched", "switching", "change", "changed", "changing", "set", "move", "moved",
     "put", "back", "revert", "reset", "turn", "turned"}
)


def _row_words(d: "schema.SettingDef") -> frozenset[str]:
    """The words that NAME a row (for its two-word options): its label's and
    its key's content words and its one-word aliases ("theme", "approvals",
    "editor"), in every form — never an everyday word (``_NOT_ROW_NAMES``,
    ``_NEVER_ALONE``) or a change cue ("stop", "use")."""
    words = list(_phrase_words(_strip_parens(d.label)))
    if not d.pattern:
        words += _phrase_words(d.key.split(".")[-1])
    for a in d.aliases:
        one = _phrase_words(a)
        if len(one) == 1:
            words += one
    out: set[str] = set()
    for w in words:
        if w in _NOT_ROW_NAMES or len(w) < 3 or _forms(w) & _NEVER_ALONE or _CUE.fullmatch(w):
            continue
        out |= _forms(w)
    return frozenset(out)


#: "…by default", "from now on": a LASTING change was asked for, so a two-word
#: option counts by itself ("use OpenAI Images by default").
_LASTING = re.compile(
    r"\b(?:by\s+default|from\s+now\s+on|as\s+(?:my|the)\s+default|my\s+default)\b", re.IGNORECASE
)


def _switch_target(toks: list[str], start: int) -> bool:
    """The phrase at *start* is what something is switched TO: "switch to
    Arc Cyan", "set it to don't ask", "go back to Liquid Glass"."""
    i = start - 1
    if i >= 0 and toks[i] == "the":
        i -= 1
    if i < 0 or toks[i] != "to":
        return False
    return any(t in _SWITCH_VERBS for t in toks[max(0, i - 4):i])


@lru_cache(maxsize=1)
def _index() -> tuple[_Phrase, ...]:
    """Every phrase of every schema row and secret, built once."""
    out: list[_Phrase] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    def add(key: str, raw: str, row: "frozenset[str] | None" = None) -> None:
        words = _phrase_words(_strip_parens(raw))
        if not words:
            return
        if len(words) == 1:
            w = words[0]
            if _forms(w) & _NEVER_ALONE or len(w) < 3:
                return
            kind = "distinct" if _forms(w) & _DISTINCT else ("generic" if _forms(w) & _GENERIC else "")
            if not kind:
                return
        else:
            kind = "multi"
        sig = (key, tuple(words))
        if sig in seen:
            return
        seen.add(sig)
        forms = tuple(_forms(w) for w in words)
        if row is not None and len(words) == 2:
            own = frozenset().union(*forms)
            out.append(_Phrase(key, forms, kind, row=frozenset(row - own)))
        else:
            out.append(_Phrase(key, forms, kind))

    for d in schema.SETTINGS:
        add(d.key, d.label)
        for a in d.aliases:
            add(d.key, a)
        # The key itself ("keep_awake", "update_channel") and, for a choice,
        # the words of each option ("Liquid Glass", "Ask before every action").
        if not d.pattern:
            add(d.key, d.key.split(".")[-1])
        row = _row_words(d)
        for _value, label in d.options:
            add(d.key, label, row=row)
        # "the Daylight theme", "stable updates": an option's words before
        # the row's own everyday word.
        generic = [a for a in d.aliases if _phrase_words(a) and len(_phrase_words(a)) == 1
                   and _forms(_phrase_words(a)[0]) & _GENERIC]
        for g in generic:
            for _value, label in d.options:
                add(d.key, f"{_strip_parens(label)} {g}")
    for s in schema.SECRETS:
        add(s.name, s.label)
        for a in s.aliases:
            add(s.name, a)
    for raw in _NO_ROW_PHRASES:
        add("", raw)
    return tuple(out)


def _match_at(toks: list[str], words: tuple[frozenset[str], ...]) -> "tuple[int, int] | None":
    """Where the phrase sits in *toks* — the indexes of its first and last
    words (its words in order, at most ``_GAP`` other words between each) —
    or None."""
    first = words[0]
    for i, t in enumerate(toks):
        if t not in first:
            continue
        pos = i
        ok = True
        for w in words[1:]:
            nxt = -1
            for j in range(pos + 1, min(len(toks), pos + 2 + _GAP)):
                if toks[j] in w:
                    nxt = j
                    break
            if nxt < 0:
                ok = False
                break
            pos = nxt
        if ok:
            return i, pos
    return None


def _about_something_else(toks: list[str], start: int, end: int, *, before: bool = True) -> bool:
    """Around the phrase: "a model like…", "…of my essay", "…in this
    spreadsheet", "…to look something up", "profile picture" — the words name
    something other than this app. ``before`` False reads only what FOLLOWS
    (a distinct word: "a watcher" is still ours, "a watcher in my React code"
    is not)."""
    nxt = toks[end + 1] if end + 1 < len(toks) else ""
    after = toks[end + 2] if end + 2 < len(toks) else ""
    third = toks[end + 3] if end + 3 < len(toks) else ""
    if before and start > 0 and toks[start - 1] in _NOT_OURS_BEFORE:
        return True
    if before and end > 0 and toks[end - 1] in _NOT_OURS_BEFORE:
        return True
    if nxt in _EXTENDERS:
        return True
    if (
        nxt in ("to", "into", "onto")
        and after in _ELSEWHERE_DETS | {"my", "our", "your"}
        and _CONTENT_NOUNS.intersection(toks[end + 3: end + 6])
    ):
        return True
    if toks[end] in ("windows", "window's") and nxt in _WINDOWS_THINGS:
        return True
    if toks[end] in ("windows", "window's", "login", "startup") and _DEVICES.intersection(
        toks[max(0, start - 4): start]
    ):
        # Review (v1.438.0): a MACHINE that starts with Windows — "my laptop
        # doesn't start with Windows anymore" — is a PC problem, never the
        # app's Start with Windows switch.
        return True
    if (
        nxt in ("of", "in", "inside", "into")
        and after
        and after not in _APP_WORDS
        and not after[0].isdigit()
    ):
        if after in _ELSEWHERE_DETS and third in _APP_WORDS:
            return False
        return True
    if nxt in _ELSEWHERE_PREPS and after in _ELSEWHERE_DETS and third not in _APP_WORDS:
        if nxt == "for" and after in ("a", "an") and third in _TIME_UNITS:
            return False
        return True
    if nxt == "to" and after in _ACTION_AFTER_TO:
        return True
    return False


def _generic_ok(toks: list[str], at: int, cue_at: set[int], question: bool) -> bool:
    """A generic word ("theme", "model") is this app's setting only as "the /
    my / your <word>" (or "<cue> <word>") within four words after a change
    cue — never "a model", "the financial model", "his browser"."""
    prev = toks[at - 1] if at > 0 else ""
    if prev not in _OWN_DETERMINERS and (at - 1) not in cue_at and toks[at] not in _SETTING_WORDS:
        if not (question and prev in ("what", "which")):
            return False
    if question:
        return True
    return any(at - 5 <= c < at for c in cue_at)


def _cue_positions(text: str, toks: list[str]) -> set[int]:
    """The token index where each change cue ENDS (so "turn on" counts at
    "on", right before the word it governs)."""
    out: set[int] = set()
    low = (text or "").lower().replace("\u2019", "'")
    # LINEAR (review): the tokens' start offsets once, then a bisect per cue.
    # Counting the tokens of ``low[: m.end()]`` per cue was quadratic in the
    # number of cues ("use use use …": 43 ms at 4,000 characters). Same
    # count: a token of the prefix is a token of the text that STARTS before
    # the cue's end (cutting a token never splits it in two).
    starts = [t.start() for t in _TOKEN.finditer(low)]
    for m in _CUE.finditer(low):
        before = bisect_left(starts, m.end())
        out.add(max(0, before - 1))
    return out


def settings_ask(text: str) -> SettingsAsk:
    """What *text* asks of chat's settings tools. Never raises."""
    try:
        return _settings_ask(str(text or "")[:4000])
    except Exception:  # noqa: BLE001 — a fault arms nothing new
        return _NONE


@lru_cache(maxsize=64)
def _settings_ask(text: str) -> SettingsAsk:
    """Cached: one turn asks several times (both lanes' arming, the stream
    lane's ask tier, a coding session's own set) about the same words."""
    if not text.strip():
        return _NONE
    from .records import is_question

    question = is_question(text)
    toks = _tokens(text.replace("-", " ").replace("_", " "))
    present = set(toks)
    cue_at = _cue_positions(text.replace("-", " ").replace("_", " "), toks)
    # v1.434.0 review: a word GLUED to the one before it by a hyphen is part
    # of a compound ("a goal-setting worksheet"), never a setting's own word.
    low = text.lower().replace("\u2019", "'")
    flat = low.replace("-", " ").replace("_", " ")
    glued = {
        i for i, m in enumerate(_TOKEN.finditer(flat)) if m.start() > 0 and low[m.start() - 1] in "-_"
    }
    keys: list[str] = []
    spans: list[tuple[int, int]] = []
    for ph in _index():
        if present.isdisjoint(ph.words[0]):
            continue
        if ph.kind == "generic":
            for i, t in enumerate(toks):
                if (
                    t in ph.words[0]
                    and i not in glued
                    and _generic_ok(toks, i, cue_at, question)
                    and not _about_something_else(toks, i, i)
                ):
                    keys.append(ph.key)
                    spans.append((i, i))
                    break
            continue
        at = _match_at(toks, ph.words)
        if at is None:
            continue
        if (
            ph.row is not None
            and present.isdisjoint(ph.row)
            and not _switch_target(toks, at[0])
            and not _LASTING.search(text)
        ):
            # A two-word option alone ("don't ask me…", "ask my boss first").
            continue
        # v1.434.0 review: a DISTINCT word still reads what follows it — "set
        # up a watcher in my React code" is not the app's watchers.
        if not _about_something_else(toks, *at, before=ph.kind != "distinct"):
            keys.append(ph.key)
            spans.append(at)
    found = bool(keys)
    keys = [k for k in dict.fromkeys(keys) if k]
    extra = any(p.search(text) for p in _EXTRA_REFERENCES)
    if not (found or extra):
        return _NONE
    if question:
        return SettingsAsk(reader=True, writers=False, keys=tuple(keys))
    # The cue must govern the phrase: within ten words before it, inside it,
    # or right after it ("turn keep awake ON") — a long pasted text with a
    # "use" in one paragraph and "local model" in another asks nothing.
    writers = extra or any(
        start - _CUE_BEFORE <= c <= end + _CUE_AFTER for start, end in spans for c in cue_at
    )
    return SettingsAsk(reader=True, writers=writers, keys=tuple(keys))


def settings_tools_for(text: str, *, auto_tools: bool) -> list[str]:
    """The settings tools a chat turn arms for *text* — its OWN place, outside
    the six-tool cap (B1.2). ``config_list`` reads; ``config_set`` (allow
    tier) and ``config_secret`` (the credential card) are the writers every
    lane may arm; with Auto tools off only the reader is armed, so the model
    can read the setting and say how to change it."""
    ask = settings_ask(text)
    if not ask.reader:
        return []
    if not (auto_tools and ask.writers):
        return ["config_list"]
    return ["config_list", "config_set", "config_secret"]
