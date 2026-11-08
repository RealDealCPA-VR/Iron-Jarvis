"""Page doors and plain "not offered" lines (v1.436.0, chat-first plan Wave 5,
part B3 of ``docs/CHAT-FIRST-PLAN-2026-11-04.md``).

Some steps cannot be done from chat at all: signing in an account (the
vendor's own sign-in runs in a terminal on this PC), the one-time Google or
Slack developer setup (made in the user's own account, in their browser),
installing and pairing the browser add-on, downloading a local model, and an
approval the user's Microsoft 365 admin gives in Microsoft's own admin
center. Before this, the model answered those from memory. Now:

* :func:`page_step` reads the user's message and names the ONE step it is
  about (or ``None``); :func:`with_page_door` puts that step's door under the
  reply — ``{"href", "label"}`` like every other door
  (``daemon/doors.py``), plus ``press`` for a sign-in the dashboard draws
  through its ONE sign-in press (``components/connections/
  CliSignInPress`` — never a second sign-in path). It ARMS NOTHING and ACTS
  ON NOTHING: a link, or a button the user presses. It never pushes out a
  door the turn already earned (a success door, a fix door, the Connect
  door, the coding offer): it takes a place only while there is room under
  :data:`doors.MAX_DOORS`, and never when a door to the same place is
  already there.
* :func:`prompt_block` gives the model, in that turn's system prompt, one
  plain sentence per step the message is about and per thing Iron Jarvis
  does NOT offer (controlling the Windows desktop, opening its screens from
  another computer, Full access as a folder default), so the reply says it
  plainly and never pretends. ``""`` when nothing matches — the system
  prompt is then byte-identical to before (pinned).

Every pattern runs on the user's own words, so every pattern is LINEAR: no
nested quantifier, every gap bounded (``_GAP``), and the text cut at
:data:`SCAN_CHARS` first (pinned with pathological input in a child process
with a deadline). Pure; never raises.

Every href is a real dashboard route or alias today (pinned by
``tests/test_page_doors_v14360.py`` against ``dashboard/app``,
``lib/relocated.ts`` and the Settings sections, and by
``dashboard/__tests__/page-doors-v1436.test.tsx`` against the Handbook's
headings through the Help page's own section rule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..connectors.app_names import ASKS_TO_MESSAGE_ME
from ..settings import setup_words as W
from .doors import _APPS_FIX_HREF, _BROWSER_FIX_HREF, MAX_DOORS, collect_doors

#: How much of the user's message is read. A request names its step near
#: the start; a long paste past this is the user's document, not the ask.
SCAN_CHARS = 2000

#: The longest gap two words of one request may have between them. Bounded
#: (never ``.*``), never across a sentence end — the linear-time rule.
_GAP = r"[^.!?\n]{0,48}?"

#: Where the accounts and sign-ins live: Settings › Connections › Accounts &
#: keys (the old address redirects there with its query; lib/relocated.ts).
ACCOUNTS_HREF = "/connections"
#: Build's terminals, opened on the Terminal view (lib/buildApp
#: buildViewFromSearch reads ``view=terminal``).
TERMINALS_HREF = "/terminals?view=terminal"
#: Settings › Connections › Notifications (the Slack form and its manifest).
NOTIFICATIONS_HREF = "/channels"
#: The Help page's guide viewer opened on one guide (and one of its sections:
#: the first heading whose slug — :func:`heading_slug` — is the section or
#: starts with it and a "-"; dashboard/lib/helpLinks.ts reads the same).
HELP_PATH = "/help"

#: The sign-in presses a door may carry: the /health provider ids the
#: dashboard's ``lib/cliSignIn.SIGN_IN_CLIS`` holds (pinned equal by the
#: fixture the dashboard test reads).
SIGN_IN_PRESSES: tuple[str, ...] = ("claude-cli", "codex-cli", "grok-cli")

#: v1.455.0 (readiness Wave G, item 23): the install presses a door may
#: carry — the Launch catalog ids ``terminals/ai_clis.INSTALL_JOBS`` knows and
#: ``dashboard/lib/cliInstall.INSTALL_JOBS`` holds (pinned equal).
INSTALL_PRESSES: tuple[str, ...] = ("claude", "codex")
#: Each install press's own program, looked for on this PC (a door is not
#: offered for a program that is already installed).
INSTALL_BINARIES: dict[str, str] = {"claude": "claude", "codex": "codex"}

#: The guides the Help page serves (``routes/helpdocs._DOCS``; pinned equal).
HELP_GUIDES: tuple[str, ...] = ("handbook", "recommended-settings", "local-models", "changelog")


def heading_slug(text: str) -> str:
    """A heading's slug the Help page's section rule reads: lower case, every
    run of anything but a-z and 0-9 one "-", none at either end. The twin of
    ``dashboard/lib/helpLinks.headingSlug``."""
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def help_href(guide: str, section: str = "") -> str:
    """The Help page opened on *guide* (and on its *section*)."""
    q = f"{HELP_PATH}?guide={guide}"
    return f"{q}&section={section}" if section else q


#: The Handbook section that walks the Microsoft 365 admin's steps
#: (docs/HANDBOOK.md "## Microsoft 365, locked down for client data").
M365_ADMIN_SECTION = "microsoft-365-locked-down-for-client-data"


@dataclass(frozen=True)
class PageStep:
    """One step only a page (or the user's own vendor account) can do."""

    id: str
    href: str
    label: str
    #: The sentence the model is given when the message is about this step.
    line: str
    #: A sign-in press the dashboard draws through its one press ("" = a link).
    press: str = ""
    #: v1.455.0 (readiness Wave G, item 23): an INSTALL press the dashboard
    #: draws through its one install press (``lib/cliInstall`` — "claude" or
    #: "codex", the Launch catalog's id; "" = none).
    install: str = ""

    def door(self) -> dict[str, str]:
        out = {"href": self.href, "label": self.label}
        if self.press:
            out["press"] = self.press
        if self.install:
            out["install"] = self.install
        return out


@dataclass(frozen=True)
class NotOffered:
    """Something Iron Jarvis does not offer at all: a line, never a door."""

    id: str
    line: str


# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #

#: "sign in", "log into", "login", "re-sign in", "sign back in" — a request to
#: sign in. Never the past tense ("I signed in to my bank this morning").
_ACT = (
    r"\b(?:sign(?:ing)?[\s-]*(?:in(?:to)?|on)|log(?:ging)?[\s-]*(?:in(?:to)?|on)|login"
    r"|re-?(?:sign|log)[\s-]*in|(?:sign|log)\s+back\s+in|re-?authenticat(?:e|ing)"
    # Review: "please sign me in to Claude Code".
    r"|(?:sign|log)\s+(?:me|us)\s+(?:back\s+)?in(?:to)?)\b"
)
#: "is not signed in", "logged out", "sign-in expired", "keeps asking me to
#: log in" — a sign-in that needs doing again.
_STATE = (
    r"(?:(?:not|n['’]t|never)\s+(?:been\s+)?(?:signed|logged)\s+in|(?:signed|logged)\s+out"
    r"|(?:sign[\s-]*in|log[\s-]*in|login)\s+(?:has\s+|keeps\s+)?(?:expired|failed|failing|broke"
    r"|stopped|isn['’]t\s+working|is\s+not\s+working|doesn['’]t\s+work|won['’]t\s+work)"
    r"|(?:needs?|wants?|ask(?:s|ing)?|keeps\s+asking)\s+(?:me\s+)?to\s+(?:sign|log)\s+in"
    # Review: Claude Code's and Grok's own words for it ("Please run /login"),
    # and "the Codex CLI wants me to log in" (the verb is the subject's).
    r"|(?:please\s+)?run\s+/login\b|\bme\s+to\s+(?:sign|log)\s+(?:back\s+)?in\b)"
)
#: A thing that is the subject of a sentence about its sign-in ("Codex says
#: …", "Claude Code keeps …", "Grok isn't …").
_SUBJECT_VERB = (
    r"\s+(?:says?|said|shows?|is|isn['’]t|was|wasn['’]t|keeps|wants?|asks?|needs?|won['’]t|can['’]t"
    r"|got|has|had|went|seems?)\b"
)
_PREP = r"(?:(?:to|into|on|with|in)\s+)?(?:(?:my|the|your|our)\s+)?"
_NOUN_SIGNIN = r"(?:['’]s)?\s+(?:sign[\s-]*in|log[\s-]*in|login)\b"


def _cli_rules(name: str, *, bare: str = "") -> tuple[re.Pattern[str], ...]:
    """The three ways a message asks to sign in to the program *name* (a
    regex): "sign in to <name>", "<name> sign-in / login …" and "<name>
    is / says / keeps … not signed in". *bare* (optional) is a short name
    accepted only after "to / into / on / with" ("sign in to Claude")."""
    rules = [
        _ACT + r"(?:\s+again)?\s+" + _PREP + name + r"\b",
        r"\b" + name + _NOUN_SIGNIN,
        # Review: one noun may sit between ("my Claude Code session says I'm
        # signed out").
        r"\b" + name + r"(?:\s+(?:session|terminal|window|app))?" + _SUBJECT_VERB + _GAP + _STATE,
    ]
    if bare:
        # Review: never a person's possessive ("log in with Claude's password
        # to the client portal" is a colleague named Claude).
        rules.append(
            _ACT + r"(?:\s+again)?\s+(?:to|into|on|with)\s+(?:(?:my|the|your)\s+)?" + bare + r"\b(?!['’]s\b)"
        )
    return tuple(re.compile(r, re.IGNORECASE) for r in rules)


_CLAUDE = r"claude[\s-]*(?:code|cli)"
_CODEX = r"codex(?:\s+cli)?"
_GROK = r"grok(?:\s+cli)?"
_OPENCODE = r"open[\s-]?code"
#: Pi, never a Raspberry Pi, never the number or a pie.
_PI = r"(?<!raspberry\s)pi(?![\s-]*(?:hole|zero|\d|chart|day|pi\b))"

_ACCOUNT_VENDOR = r"(?:claude|codex|chatgpt|openai|grok|anthropic)(?:[\s-]*code)?"


def _install_rules(name: str) -> tuple[re.Pattern[str], ...]:
    """"Install Codex for me", "can you install Claude Code?", "set up Codex
    on this PC", "download Claude Code" — the program installed HERE. Never a
    package of the user's code ("install the codex npm package"), never on
    another device ("install Codex on my Mac")."""
    tail = (
        r"(?!\s+(?:npm|pip|python|node|js|sdk|package|module|library|extension|plugin|vs\s*code|cli\s+package)\b)"
        r"(?![^.!?\n]{0,30}?\b(?:on|for)\s+(?:my|the|her|his|our|a|another)\s+(?:(?:new|old|work|other|second)\s+)?"
        r"(?:phone|iphone|android|ipad|tablet|mac|macbook|laptop|linux|server|vm|box|brother|sister|mom|dad"
        r"|wife|husband|son|daughter|kids?|friends?|clients?|team)\b)"
    )
    return tuple(
        re.compile(r, re.IGNORECASE)
        for r in (
            r"\b(?:install|re-?install|download|get)\s+(?:me\s+)?(?:the\s+)?" + name
            + r"(?:\s+(?:cli|app|program|tool))?\b" + tail,
        )
    )
_ORDINAL = r"(?:second|another|other|2nd|third|3rd|extra|new|different|work|personal|business|separate)"
_ACCOUNT_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\biron[\s-]*proxy\b" + _GAP + r"\b(?:accounts?|sign|log)",
        r"\b(?:accounts?|sign|log)\w*\b" + _GAP + r"\biron[\s-]*proxy\b",
        r"\b(?:" + _ACT + r"|add|set\s*up|connect)\s+" + _PREP
        + r"(?:(?:a|an)\s+)?" + _ORDINAL + r"\s+" + _ACCOUNT_VENDOR
        + r"\s+(?:accounts?|logins?|plans?|subscriptions?)\b",
        r"\b(?:add|set\s*up|connect)\s+(?:a|an|my|the)\s+" + _ACCOUNT_VENDOR
        + r"\s+accounts?\b",
        r"\b" + _ACCOUNT_VENDOR + r"\s+accounts?\b" + _GAP + r"(?:" + _STATE + r"|" + _ACT + r")",
        _ACT + r"(?:\s+again)?\s+" + _PREP + _ACCOUNT_VENDOR + r"\s+accounts?\b",
    )
)

_M365 = r"(?:(?:microsoft|office)[\s-]*365|m365)"
#: "sign in to Outlook" is Iron Jarvis's only when the sentence says it is
#: for Jarvis ("so you can read my mail") — "how do I log in to Outlook on my
#: phone" is the user's own Outlook.
_NOT_ON_A_DEVICE = (
    # Review: a laptop or a work computer is the user's own device too ("sign
    # in to my Microsoft account on the new laptop"); "on this PC" is not
    # excluded (Iron Jarvis runs there).
    r"(?![^.!?\n]{0,30}?\bon\s+(?:my|the|her|his|our|a|another)\s+(?:(?:new|old|work|home|personal|other"
    r"|second|office)\s+)?(?:phone|iphone|android|ipad|tablet|mac|macbook|laptop|computer|pc|desktop"
    r"|surface|chromebook)\b)"
)
_M365_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        _ACT + r"(?:\s+again)?\s+" + _PREP + r"(?:" + _M365 + r"|microsoft\s+account)\b" + _NOT_ON_A_DEVICE,
        r"\b" + _M365 + _NOUN_SIGNIN,
        r"\b" + _M365 + _SUBJECT_VERB + _GAP + _STATE,
        r"\b(?:you|jarvis)\b" + _GAP + _ACT + r"\s+" + _PREP + r"outlook\b" + _NOT_ON_A_DEVICE,
        _ACT + r"\s+" + _PREP + r"outlook\b(?=" + _GAP + r"\b(?:you|jarvis|iron\s+jarvis)\b)",
    )
)

#: A Microsoft 365 admin's own approval (sentence-level: the vendor AND the
#: approval words, so "the invoice needs admin approval" is nothing).
_MS_VENDOR = re.compile(
    r"\b(?:microsoft|m365|office[\s-]*365|entra|azure|tenant|outlook|onedrive|graph)\b", re.IGNORECASE
)
_MS_APPROVAL = re.compile(
    r"\b(?:admin(?:istrator)?\s+(?:consent|approval)|grant\s+(?:admin\s+)?consent"
    r"|app\s+registrations?|register(?:ing)?\s+(?:the|an|our|a|this)\s+app|conditional\s+access"
    r"|assignment\s+required|enterprise\s+applications?"
    r"|(?:approve|consent\s+to|allow)\s+(?:the\s+|this\s+|our\s+|my\s+)?(?:iron\s+jarvis(?:\s+app)?|app))\b",
    re.IGNORECASE,
)
_ENTRA = re.compile(r"\bentra\b" + _GAP + r"\b(?:apps?|registration|consent|permissions?|approv\w*)\b", re.IGNORECASE)

#: Google's one-time developer setup (sentence-level: Google AND a setup word).
_GOOGLE = re.compile(
    # "google it / google the …" is the verb, never Google's own setup.
    r"\b(?:google|gmail)\b(?!\s+(?:play|maps?\s+reviews?|it|this|that|what|how|for|search|the|a|an|me|my"
    r"|their|his|her|our)\b)",
    re.IGNORECASE,
)
_GOOGLE_SETUP = re.compile(
    # Review: a CPA's "client ID" is a customer's number ("add a client ID
    # column to the Google Sheet"), so "client/app ID" counts only as Google's
    # own (named right before it, or paired with its secret).
    r"\b(?:(?:developer|dev)\s+(?:setup|set[\s-]up|app|project|account|console)|cloud\s+console"
    r"|(?:google|gmail|drive|oauth|cloud)\s+(?:oauth\s+)?(?:client|app)\s+(?:id|secret)|client\s+secret"
    r"|client\s+id\s+and\s+(?:the\s+|a\s+|its\s+)?(?:client\s+)?secret"
    r"|oauth|consent\s+screen|credentials\s+(?:page|screen)"
    r"|api\s+(?:project|credentials)|one[\s-]time\s+(?:google\s+)?(?:developer\s+)?set[\s-]?up"
    r"|test\s+users?|(?:create|make|register|set\s*up)\s+(?:the|a|an|my|our)\s+google\s+"
    r"(?:(?:developer|cloud)\s+)?(?:app|project|client))\b",
    re.IGNORECASE,
)
_GOOGLE_LITERAL = re.compile(
    r"console\.cloud\.google\.com|\b(?:google_drive|gmail)_oauth_client_(?:id|secret)\b", re.IGNORECASE
)

#: A Slack app the user makes on Slack's site (sentence-level: Slack AND its
#: developer words — never "install the Slack app on my laptop").
_SLACK = re.compile(r"\bslack\b", re.IGNORECASE)
_SLACK_SETUP = re.compile(
    r"(?:\b(?:app|bot|xapp|xoxb)\s+tokens?\b|\bapp[\s-]level\s+tokens?\b|\bxox[bp]-|\bxapp-"
    r"|\b(?:app|slack|the|its)\s+manifest\b"
    # Review: "member ID" alone is also an insurance or a client's number
    # ("send the member ID to the team on Slack"): only Slack's own, or one
    # for Jarvis, or the user's own looked up.
    r"|\bslack\s+member\s+id\b"
    r"|\bmember\s+id\b(?=[^.!?\n]{0,40}?\bfor\s+(?:you|jarvis|iron\s+jarvis|notifications?)\b)"
    r"|\b(?:find|get|copy|where['’]s|where\s+is|where\s+do\s+i\s+find)\s+my\s+member\s+id\b"
    r"|\bsocket\s+mode\b|\bmessages\s+tab\b"
    r"|\b(?:create|make|set\s*up|register)\s+(?:a\s+|an\s+|the\s+|my\s+|our\s+)?(?:new\s+)?slack\s+(?:app|bot)\b"
    r"|\bslack\s+(?:app|bot)\b" + _GAP + r"\b(?:create|make|set\s*up|register)\b)",
    re.IGNORECASE,
)

#: The browser add-on: install / pair / set up / "won't connect".
_ADDON = r"(?:add[\s-]?on|extension)"
#: "Edge" alone is also the edge of a table: only "Microsoft Edge" or "Edge
#: browser" names the browser where the noun could be anything.
_BROWSER_NOUN = r"(?:(?:web\s+)?browser|chrome|microsoft\s+edge|edge\s+browser)"
_BROWSER_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\b(?:pair|re-?pair|unpair|link|connect)\w*\s+(?:my|the|your|this|a)\s+" + _BROWSER_NOUN + r"\b"
        r"(?!\s+(?:to|with)\s+(?:the\s+|my\s+)?(?:internet|wi-?fi|network|vpn|printer|tv|phone|bluetooth)\b)"
        # Review: the browser's own things are not the browser ("link my
        # Chrome bookmarks to the spreadsheet").
        r"(?!\s+(?:bookmarks?|history|passwords?|tabs?|profiles?|settings|cache|cookies|data|homepage"
        r"|downloads|windows?)\b)",
        # Review: "pair the add-on" (a phone extension is never paired).
        r"\b(?:pair|re-?pair|unpair)\w*\s+(?:the|my|your|this|a|an)\s+(?:(?:browser|chrome|edge)\s+)?"
        + _ADDON + r"\b"
        r"|\b(?:connect|link)\w*\s+(?:the|my|your|this)\s+(?:(?:browser|chrome|edge)\s+)?add[\s-]?on\b",
        r"\bbrowser\s+pairing\b|\bpairing\s+(?:code\s+for\s+)?(?:the|my|your)\s+browser\b",
        r"\b(?:install|set\s*up|setup|add|get|load|download|enable|turn\s+on|reinstall|update)\w*\s+"
        r"(?:your|iron\s+jarvis['’]?s?|jarvis['’]?s?|its)\s+(?:(?:browser|chrome|edge)\s+" + _ADDON
        + r"|add[\s-]?on)\b",
        r"\b(?:install|set\s*up|setup|add|get|load|download|enable|turn\s+on|reinstall|update)\w*\s+"
        r"(?:the|a|an)\s+(?:browser\s+add[\s-]?on|(?:browser|chrome|edge)\s+" + _ADDON
        + r"\b(?=" + _GAP + r"\b(?:you|jarvis)\b))",
        # Review: "add-on" is the app's own word; a bare "Chrome extension" is
        # any extension ("the Chrome extension for Grammarly isn't working")
        # unless the sentence names Jarvis.
        r"\bthe\s+(?:browser|chrome|edge)\s+(?:add[\s-]?on\b|extension\b(?=[^.!?\n]{0,60}?\b(?:you|jarvis)\b))"
        + _GAP
        + r"(?:(?:won['’]?t|doesn['’]?t|isn['’]?t|not|can['’]?t|never)\s+(?:pair|connect|work|show|load|install)"
        r"|(?:keeps\s+)?(?:disconnect|dropp)(?:s|ed|ing)\b)",
        r"\b(?:iron\s+)?jarvis\s+(?:browser\s+)?" + _ADDON + r"\b",
    )
)

#: Downloading a local model.
_MODEL_NAMES = r"(?:llama|qwen|gemma|mistral|deepseek|phi|glm|gpt-oss|nemotron|granite|smollm)"
_LOCAL_MODEL_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\b(?:download|pull|install|get|grab|set\s*up|add)\w*\s+(?:me\s+|us\s+)?"
        r"(?:(?:a|an|the|some|my|another|new|good|small|free|decent)\s+){0,2}"
        r"(?:local|offline|private|ollama|open[\s-]?source|open[\s-]?weights?)\s+"
        r"(?:ai\s+|llm\s+|language\s+)?(?:models?|llms?)\b",
        r"\bollama\s+(?:pull|run)\s+[a-z0-9]",
        # Review: a tag between the name and its number ("deepseek-r1").
        r"\b(?:download|pull|install|grab)\w*\s+(?:the\s+)?" + _MODEL_NAMES + r"(?:[\s.:-]?[a-z]{1,6})?[\s.:-]?\d",
        r"\b(?:download|pull|install|grab)\w*\s+(?:the\s+)?" + _MODEL_NAMES + r"\b[\w.: -]{0,20}?\bmodel\b",
        r"\b(?:which|what)\s+(?:local\s+|ollama\s+|offline\s+)?(?:ai\s+)?models?\s+(?:should|can|could|do)\s+"
        r"(?:i|we)\s+(?:download|pull|install)\b",
        r"\b(?:local|offline|ollama)\s+(?:ai\s+)?models?\b" + _GAP + r"\b(?:download|pull|install)\w*",
        # v1.448.0 (Wave A "chat configures everything", A1): which local
        # model THIS PC can run — "Which local models can run on this
        # laptop?", "what offline model fits my PC", "can my laptop run a
        # local model" (the guide "Local Models by RAM Tier" answers it).
        # Never "which financial models can run on this laptop".
        r"\b(?:which|what)\s+(?:(?:local|offline|ollama|open[\s-]?source|open[\s-]?weights?|small|free)\s+){1,2}"
        r"(?:ai\s+|llm\s+|language\s+)?(?:models?|llms?)\s+(?:can|could|would|will|should|might)\s+"
        r"(?:i\s+|we\s+)?(?:run|fit|work|use|load)\b[^.!?\n]{0,30}?\b(?:this|my|the|our)\s+"
        r"(?:laptop|pc|computer|machine|desktop|gpu|hardware|rig|macbook|box)\b",
        r"\b(?:which|what)\s+(?:(?:local|offline|ollama|open[\s-]?source|open[\s-]?weights?|small|free)\s+){1,2}"
        r"(?:ai\s+|llm\s+|language\s+)?(?:models?|llms?)\s+(?:fits?|runs?|works?)\s+(?:on\s+)?(?:this|my|the|our)\s+"
        r"(?:laptop|pc|computer|machine|desktop|gpu|hardware|rig|macbook|box)\b",
        r"\b(?:can|could|will|would)\s+(?:this|my|the|our)\s+(?:laptop|pc|computer|machine|desktop|gpu|macbook)\s+"
        r"run\s+(?:(?:a|an|any|some|which|what)\s+)?(?:local|offline|ollama|open[\s-]?source)\s+"
        r"(?:ai\s+|llm\s+|language\s+)?(?:models?|llms?)\b",
    )
)

#: Controlling the Windows desktop (not offered).
_DESKTOP_NOUN_GUARD = (
    r"(?!\s*(?:['’]s)?\s*(?:brightness|resolution|time|timeout|saver|size|settings?|wallpaper|background"
    r"|layout|folder|files?|shortcuts?|volume|temperature|name|password|login|account|camera"
    r"|microphone|mic|speakers?|drivers?|protector|recording|control\s+panel|icons?\s+size|reader"
    r"|readers|app|apps|version|client|notifications?|edition|pads?|wheel|batter(?:y|ies)|cables?"
    r"|cords?|dongle|sensitivity|speed|doors?)\b)"
)
_DESKTOP_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\b(?:control|operate|take\s+over|takeover|take\s+control\s+of|remote[\s-]+control)\s+(?:my|the|this)\s+"
        r"(?:mouse|cursor|keyboard|desktop|screen|computer|pc|laptop)\b" + _DESKTOP_NOUN_GUARD,
        r"\b(?:move|drive)\s+(?:my|the|this)\s+(?:mouse|cursor)\b",
        # Review: "use my keyboard" is asked OF Jarvis, or said to it as an
        # order at the start of a sentence — "How do I use my keyboard to
        # select a column in Excel?" is the user's own how-to question.
        r"\b(?:you|jarvis)\b" + _GAP + r"\b(?:use|click(?:\s+on)?|press|type\s+(?:on|into|with))\s+"
        r"(?:my|the|this)\s+(?:mouse|cursor|keyboard|desktop|screen)\b" + _DESKTOP_NOUN_GUARD,
        r"(?:^|[.!?\n])\s*(?:please\s+)?(?:use|click(?:\s+on)?|type\s+(?:on|into|with))\s+(?:my|the|this)\s+"
        r"(?:mouse|cursor|keyboard|desktop|screen)\b" + _DESKTOP_NOUN_GUARD,
        r"\b(?:you|jarvis)\b" + _GAP + r"\b(?:click|press|tap)\w*\b" + _GAP
        + r"\bon\s+(?:my|the|this)\s+(?:screen|desktop|monitor)\b" + _DESKTOP_NOUN_GUARD,
        r"\b(?:see|look\s+at|watch|view|read)\s+(?:my|the|this)\s+(?:screen|desktop|monitor)\b"
        + _DESKTOP_NOUN_GUARD,
        r"\bscreen\s*shot\s+of\s+(?:my|the|this)\s+(?:screen|desktop|monitor)\b",
        r"\b(?:open|launch|start)\s+(?:(?:the|my)\s+)?(?:excel|word|outlook|quickbooks|notepad|powerpoint|teams"
        r"|calculator|file\s+explorer|explorer|photoshop|lacerte|ultratax|drake|proseries)\b" + _GAP
        + r"\b(?:and|then)\s+(?:click|type|enter|press|fill|select|navigate|scroll)\b",
        r"\b(?:desktop|windows|pc|computer|gui)\s+(?:automation|control)\b(?!\s+panel)",
        r"\bcomputer\s+use\b(?=" + _GAP + r"\b(?:desktop|windows|outside|programs?)\b)",
        # v1.448.0 (Wave A "chat configures everything", A1): acting INSIDE a
        # desktop program named as such — "Click the Submit button in the
        # QuickBooks desktop app for me", "type the totals in the Excel app",
        # "press Save in Lacerte" — or on the desktop itself ("click the icon
        # on my desktop"). A web, mobile or phone app is not the desktop, and
        # "use the desktop app" asks for nothing (no acting verb). The lane
        # reads this reading first and arms no browser click for it
        # (tools.autoselect.select_ask_tools).
        r"\b(?:click|press|tap|double[\s-]?click|right[\s-]?click|type|enter|fill|select|choose|tick|check"
        r"|uncheck|toggle|drag)\w*\b" + _GAP
        + r"\b(?:in|on|inside|within|into)\s+(?:the|my|our|that|this)\s+"
        r"(?:(?!(?:web|browser|mobile|phone|ios|android|online)\b)[\w.&'’-]+\s+){0,3}?"
        r"(?:desktop\s+(?:app|application|program|version|client|software)|windows\s+(?:app|application|program)"
        r"|(?:excel|word|outlook|quickbooks|powerpoint|teams|notepad|lacerte|ultratax|drake|proseries|photoshop"
        r"|acrobat)\s+(?:app|application|window|program))\b",
        r"\b(?:click|press|tap|double[\s-]?click|right[\s-]?click|type|enter|fill|select|choose|drag)\w*\b"
        + _GAP + r"\b(?:in|inside|within)\s+(?:quickbooks\s+desktop|lacerte|ultratax|drake\s+(?:tax|software)"
        r"|proseries|file\s+explorer|notepad|the\s+calculator)\b",
        r"\b(?:click|press|tap|double[\s-]?click|right[\s-]?click|drag)\w*\b" + _GAP
        + r"\b(?:on|from)\s+(?:my|the|this)\s+desktop\b" + _DESKTOP_NOUN_GUARD,
    )
)


def asks_desktop_control(text: Any) -> bool:
    """Does the message ask Iron Jarvis to act on the Windows desktop (a thing
    it does not offer)? Read by ``tools.autoselect.select_ask_tools`` so a
    click asked for IN A DESKTOP PROGRAM never ask-arms a browser click (the
    model would try the page tools on a program that is no page). Pure."""
    try:
        return any(r.search(_scan(text)) for r in _DESKTOP_RULES)
    except Exception:  # noqa: BLE001 — a broken rule is no match
        return False

#: Opening Iron Jarvis's screens from another computer (not offered).
_JARVIS_OBJ = (
    r"(?:iron\s+jarvis|jarvis|you|this\s+app|the\s+app|the\s+dashboard|my\s+dashboard|this\s+dashboard"
    r"|the\s+iron\s+jarvis\s+app|your\s+(?:screens?|dashboard|pages?))"
)
_OPEN_VERB = (
    r"(?:open|use|access|reach|see|view|get\s+to|log\s+in(?:to)?|sign\s+in(?:to)?|connect\s+to"
    r"|bring\s+up|pull\s+up|talk\s+to|chat\s+with)"
)
_OTHER_COMPUTER = (
    r"(?:(?:from|on)\s+(?:another|a\s+different|my\s+other|other|a\s+second|my\s+second|my\s+work|my\s+home"
    r"|the\s+other|my\s+wife['’]?s|my\s+husband['’]?s|my\s+partner['’]?s|a\s+colleague['’]?s|an?)\s+"
    r"(?:computer|pc|laptop|machine|desktop|mac|macbook|ipad|tablet)"
    r"|from\s+my\s+(?:laptop|macbook|mac|ipad|tablet)|on\s+my\s+(?:ipad|tablet|mac|macbook))\b"
)
#: v1.448.0 (Wave A item 4): a SECOND Iron Jarvis on another of the user's
#: computers ("the Jarvis on my office PC", "my other Iron Jarvis"), and
#: handing jobs to that computer — the same line answers (a copy there runs
#: on its own; LAN access lets it hand this one tasks, never the reverse
#: from here, and never opens these screens there).
_OTHER_PC_WORD = r"(?:pc|computer|machine|laptop|desktop|mac|macbook|workstation|box)"
_OTHER_PC = (
    r"(?:my|the|our)\s+(?:other|office|work|home|second|spare|main|downstairs|upstairs|desk)\s+" + _OTHER_PC_WORD
    + r"|my\s+(?:laptop|macbook|mac|ipad|tablet)"
)
_ELSEWHERE_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\b" + _OPEN_VERB + r"\s+" + _JARVIS_OBJ + r"\b" + _GAP + r"\b" + _OTHER_COMPUTER,
        r"\b(?:open|use|access|reach|control|get\s+to)\s+" + _JARVIS_OBJ + r"\s+remotely\b",
        r"\bremote\s+access\s+(?:to\s+)?(?:iron\s+jarvis|jarvis|the\s+dashboard|this\s+app)\b",
        r"\b" + _OPEN_VERB + r"\s+" + _JARVIS_OBJ + r"\b" + _GAP
        + r"\b(?:over|across|from|through)\s+(?:my|the|our)\s+(?:network|lan|wi-?fi|internet|tailscale|vpn)\b",
        # "THE Jarvis on my office PC" — a copy that exists there; "run Iron
        # Jarvis on my laptop too" (installing one) stays an everyday sentence.
        r"\bthe\s+(?:iron\s+)?jarvis\s+(?:on|at|running\s+on|installed\s+on)\s+(?:" + _OTHER_PC + r")\b",
        r"\bmy\s+other\s+(?:iron\s+)?jarvis\b|\b(?:the\s+other|another|a\s+second|the\s+second|two|both)\s+"
        r"(?:copies\s+of\s+|copy\s+of\s+|instances?\s+of\s+)?(?:iron\s+)?jarvis(?:es)?\b",
        r"\b(?:hand|send|pass|push|give|offload|farm\s+out|delegate|route|move|assign)\b" + _GAP
        + r"\b(?:jobs?|tasks?|work|runs?|workloads?)\b" + _GAP
        + r"\b(?:to|onto|over\s+to)\s+(?:" + _OTHER_PC + r")\b",
    )
)

#: Full access (or "Don't ask") as a folder's default (not offered).
_FULL = r"(?:full\s+access|don['’]?t\s+ask|yolo)"
_FULL_DEFAULT_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\b(?:make|set|use|put)\w*\b" + _GAP + r"\b" + _FULL + r"\b" + _GAP + r"\bdefaults?\b",
        r"\bdefaults?\b" + _GAP + r"\b(?:to|as|on|in|with)\s+(?:the\s+)?" + _FULL + r"\b",
        r"\balways\s+(?:start|open|run|use)\b" + _GAP + r"\b(?:with|in|on)\s+" + _FULL + r"\b",
    )
)
_FOLDERISH = re.compile(r"\b(?:folders?|repos?|repository|repositories|director(?:y|ies)|projects?)\b", re.IGNORECASE)
_NOT_A_GRANT = re.compile(
    r"\baccess\s+to\s+(?:the|this|my|that|our)\s+(?:folder|repo|repository|directory|project)\b"
    r"|\b(?:give|grant|share)\w*\b" + _GAP + r"\bfull\s+access\b|\b\w+['’]s\s+access\b",
    re.IGNORECASE,
)

#: A sentence, for the rules that need two words in ONE sentence (linear:
#: one pass, each piece bounded).
_SENTENCE = re.compile(r"[^.!?\n]{1,600}")


def _any(rules: tuple[re.Pattern[str], ...]) -> Callable[[str], bool]:
    return lambda t: any(r.search(t) for r in rules)


def _sentences(t: str) -> list[str]:
    return _SENTENCE.findall(t)


def _vendor_approval(t: str) -> bool:
    if _ENTRA.search(t):
        return True
    return any(_MS_VENDOR.search(s) and _MS_APPROVAL.search(s) for s in _sentences(t))


def _google_setup(t: str) -> bool:
    if _GOOGLE_LITERAL.search(t):
        return True
    return any(_GOOGLE.search(s) and _GOOGLE_SETUP.search(s) for s in _sentences(t))


#: v1.442.0 (chat-first plan Wave 7, group A): Slack SET UP as the place Jarvis
#: messages the user ("Set up Slack so you can message me when a long job
#: finishes") — a notification destination, whose Slack app the user makes on
#: Slack's site. A setup verb on Slack AND the destination's own words; never
#: "connect my Slack" alone (the Directory's app, its own Connect door) and
#: never Slack already set up ("message me on Slack when it's done").
_SET_UP_SLACK = re.compile(
    r"\b(?:set\s*up|setup|connect|add|hook\s+up|link|configure|enable|turn\s+on)\s+(?:(?:my|the|a|an|our)\s+)?"
    r"slack\b",
    re.IGNORECASE,
)
_DESTINATION_WORDS = re.compile(r"\b(?:notifications?|alerts?|destinations?)\b", re.IGNORECASE)


def _slack_setup(t: str) -> bool:
    for s in _sentences(t):
        if not _SLACK.search(s):
            continue
        if _SLACK_SETUP.search(s):
            return True
        if _SET_UP_SLACK.search(s) and (ASKS_TO_MESSAGE_ME.search(s) or _DESTINATION_WORDS.search(s)):
            return True
    return False


#: v1.442.0 (Wave 7): connecting GMAIL ("Connect my Gmail and summarize my
#: latest emails…") — its card starts only after the one-time Google developer
#: setup, so the reply names that step and the card. An acting verb on Gmail;
#: never the user's own mail program ("connect my Gmail to Outlook on my
#: phone"), never "google it".
_GMAIL_CONNECT = re.compile(
    # Review (v1.442.0): only the USER's Gmail ("my", "our", "your" or none):
    # "set up a Gmail account for my new assistant" makes a Google account;
    # and never one of Gmail's own settings ("turn on Gmail's spam filter").
    r"\b(?:connect|set\s*up|setup|add|link|hook\s+up|turn\s+on|enable)\s+(?:(?:my|our|your)\s+)?"
    r"g-?mail\b(?!['’]s\b)(?!\s+(?:filters?|labels?|signatures?|forwarding|rules?|vacation|folders?"
    r"|spam|settings?|tabs?|themes?)\b)"
    r"(?![^.!?\n]{0,40}?\b(?:to|on|with)\s+(?:my|the|her|his|our|a)\s+(?:(?:new|old|work|home|other)\s+)?"
    r"(?:phone|iphone|android|ipad|tablet|mac|macbook|laptop|outlook|apple\s+mail|thunderbird)\b)",
    re.IGNORECASE,
)


def _gmail_setup(t: str) -> bool:
    return _GMAIL_CONNECT.search(t) is not None


#: v1.442.0 (Wave 7): a coding VIEW told to work in Full access FOR a folder
#: ("Make Build › Code use Codex on my work account in Full access for this
#: repo") is a folder default by another name.
_FULL_FOR_A_FOLDER = re.compile(
    r"\b(?:make|set|have|let|configure)\b" + _GAP + r"\b(?:in|with|on)\s+" + _FULL + r"\b" + _GAP
    + r"\b(?:for|in)\s+(?:this|the|my|that|our)\s+(?:repo|repository|folder|project|directory)\b",
    re.IGNORECASE,
)
_CODING_WORDS = re.compile(r"\b(?:build|code|coding|sessions?|codex|claude\s+code)\b", re.IGNORECASE)


def _full_default(t: str) -> bool:
    for s in _sentences(t):
        if _FOLDERISH.search(s) and not _NOT_A_GRANT.search(s) and any(r.search(s) for r in _FULL_DEFAULT_RULES):
            return True
        if _FULL_FOR_A_FOLDER.search(s) and _CODING_WORDS.search(s) and not _NOT_A_GRANT.search(s):
            return True
    return False


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #

_NOT_FOR_THEM = "Iron Jarvis cannot do it for the user, so say so plainly and point there"

#: Where the places are called ON SCREEN today (review): the Settings rows
#: are named like the pages they hold since v1.354.0 (G67), so "Accounts &
#: keys" and "Apps & tools" are retired names. Pinned equal to
#: ``providers/failure_words.ACCOUNTS_PLACE`` and ``connectors/app_names.
#: APPS_PLACE``, and no line may name a ``formerly:`` row of SettingsHome.
CONNECTIONS_PLACE = "Settings › Connections"
DIRECTORY_PLACE = "the Directory in Settings › Connections"
#: The Iron-Proxy card's own title (``components/connections/IronProxyCard
#: CARD_TITLE``; pinned equal).
ACCOUNTS_CARD = "Several accounts (Iron-Proxy)"
#: The one model Iron Jarvis downloads itself, and the words of the place
#: that offers it (``dashboard/lib/starterModel.STARTER_MODEL_LABEL`` and the
#: chat page's "Connect a model for real answers" line; pinned equal).
STARTER_MODEL_NAME = "Llama 3.2"
CONNECT_DOORS_WORDS = "Connect a model for real answers"


def _cli_line(name: str, inside: str) -> str:
    # Review: the Sign in button beside a CLI is drawn ONLY while it is not
    # signed in (ConnectionsPage: `available ? null : signedOut && cli`), so
    # the line says when it is there instead of promising it always is.
    return (
        f"Signing in to {name} is done on this PC, in {name} itself, in a terminal: while {name} is not "
        f"signed in, {CONNECTIONS_PLACE} shows a Sign in button beside it that opens that terminal, where "
        f"{inside}. {_NOT_FOR_THEM}."
    )


def _install_line(name: str, vendor: str) -> str:
    # The press opens POST /terminals/launch {cli: "<name>-install"}: a Build
    # terminal running the vendor's own documented installer, every step on
    # screen (terminals/ai_clis.SETUP_LAUNCHES).
    return (
        f"Iron Jarvis can install {name} on this PC: the \"Install {name} for me\" press under this reply opens "
        f"a terminal in Build that runs {vendor}'s own installer, and every step shows there (the same press is "
        f"beside {name} in {CONNECTIONS_PLACE}). Iron Jarvis does not install anything until the user presses "
        f"it. After it "
        f"finishes, {name} still has to be signed in, with its Sign in button there. Say so plainly and point "
        "there."
    )


#: The steps, in PRIORITY order: the first that matches is the message's
#: door (an account of the user's before this PC's own Claude Code login;
#: the admin's approval before the Microsoft sign-in).
STEPS: tuple[PageStep, ...] = (
    PageStep(
        "accounts", ACCOUNTS_HREF, "Open your accounts in Settings",
        # v1.438.0 (Wave 4 group B): ADDING one is a chat tool now
        # (settings/records_connections.AccountAddTool); signing in never is.
        # Review: this line reaches EVERY surface (the phone, a Build
        # terminal's chat, the sidebar), and account_add is offered only on
        # the chat page — so the line says "when it is offered".
        f"Adding a Claude, Codex or Grok account can be done with account_add when that tool is offered in "
        f"this reply (it asks first, and \"Several accounts\" must be switched on), or on the \"{ACCOUNTS_CARD}\" "
        f"card in {CONNECTIONS_PLACE}: "
        f"Add account. Each account then signs in with its own Sign in button (account_add opens it), in a "
        f"terminal on this PC, where the user finishes in their own browser. Signing in: {_NOT_FOR_THEM}.",
    ),
    PageStep(
        "vendor_approval", help_href("handbook", M365_ADMIN_SECTION), "Read the Microsoft 365 setup steps",
        "Approving Iron Jarvis in the firm's Microsoft 365 (registering its app, Grant admin consent, and who "
        "may use it) is done by the Microsoft 365 admin in the Microsoft Entra admin center, in their own "
        "browser; Iron Jarvis cannot do it for them. The Handbook section \"Microsoft 365, locked down for "
        "client data\" (in Help) walks it step by step; say so plainly and point there.",
    ),
    PageStep(
        "signin_microsoft", _APPS_FIX_HREF, "Sign in to Microsoft 365 in Settings",
        f"Signing in to Microsoft 365 is done on its card in {DIRECTORY_PLACE}: after "
        "Connect, its Sign in to Microsoft button opens a terminal with a web address and a code, and the "
        "user signs in there with their main account, in a private browser window. "
        f"{_NOT_FOR_THEM}.",
    ),
    PageStep(
        # v1.455.0 (readiness Wave G, item 23): "Install Codex for me" — the
        # one-press install job (terminals/ai_clis.INSTALL_JOBS, v1.450.0),
        # the same press as Settings › Connections and a new chat's doors.
        "install_claude_code", ACCOUNTS_HREF, "Install Claude Code for me",
        _install_line("Claude Code", "Anthropic"),
        install="claude",
    ),
    PageStep(
        "install_codex", ACCOUNTS_HREF, "Install Codex for me",
        _install_line("Codex", "OpenAI"),
        install="codex",
    ),
    PageStep(
        "signin_claude_code", ACCOUNTS_HREF, "Sign in to Claude Code",
        _cli_line("Claude Code", "the user types /login and finishes in their browser"),
        press="claude-cli",
    ),
    PageStep(
        "signin_codex", ACCOUNTS_HREF, "Sign in to Codex",
        _cli_line("Codex", "Codex shows its own sign-in screen (Sign in with ChatGPT) and the user finishes "
                  "in their browser"),
        press="codex-cli",
    ),
    PageStep(
        "signin_grok", ACCOUNTS_HREF, "Sign in to Grok CLI",
        _cli_line("Grok CLI", "it opens its own sign-in (or the user types /login) and the user finishes in "
                  "their browser"),
        press="grok-cli",
    ),
    PageStep(
        "signin_pi", TERMINALS_HREF, "Open Build to sign in to Pi",
        "Pi signs in by itself, for each model provider: the user opens a terminal in Build, starts Pi from "
        "its Launch menu and types /login there. Iron Jarvis's accounts do not apply to Pi. "
        f"{_NOT_FOR_THEM}.",
    ),
    PageStep(
        "signin_opencode", TERMINALS_HREF, "Open Build to sign in to OpenCode",
        "OpenCode signs in by itself, for each model provider: the user opens a terminal in Build and runs "
        "opencode auth login there. Iron Jarvis's accounts do not apply to OpenCode. "
        f"{_NOT_FOR_THEM}.",
    ),
    PageStep(
        "google_setup", ACCOUNTS_HREF, "Open Google's one-time setup in Settings",
        "Google Drive and Gmail need a one-time Google developer setup that the user makes in their own "
        "Google account, in their browser (the Google Cloud console); Iron Jarvis cannot make it for them. "
        f"Google Drive's steps are on its card in {CONNECTIONS_PLACE} (its One-time setup), and Gmail's "
        f"card in {DIRECTORY_PLACE} links its own steps; say so plainly and point there.",
    ),
    PageStep(
        # v1.442.0 (chat-first plan Wave 7, group A): CONNECTING Gmail. Its
        # card (connectors.catalog, connect_via "mcp", `needs` the Google
        # developer setup) starts only once that setup is done; app_connect
        # can save it before, and then says "did not start" (pinned).
        "gmail_setup", _APPS_FIX_HREF, "Open Gmail's card in Settings",
        f"Gmail is connected from its card in {DIRECTORY_PLACE}, and it starts only after a one-time Google "
        "developer setup the user makes in their own Google account, in their browser (the card's Docs link "
        "has the steps); Iron Jarvis cannot make that setup for them. app_connect, when it is offered in this "
        "reply, can save Gmail before that, but Gmail does not start and its tools are not in the chat until "
        "the setup is done. If Gmail is already connected, none of this is needed. Say so plainly and point "
        "there.",
    ),
    PageStep(
        "slack_setup", NOTIFICATIONS_HREF, "Set up Slack in Settings",
        "A Slack app (its bot and app tokens, the app manifest, the user's member ID) is made by the user on "
        "Slack's own website, signed in as themselves; Iron Jarvis cannot make it for them. Settings › "
        "Connections › Notifications has the Slack form, a manifest to copy and the steps (for Jarvis to "
        f"message the user there); the Slack card in {DIRECTORY_PLACE}, for reading and posting in their "
        "channels, takes the bot token of such an app too. Say so plainly and point there.",
    ),
    PageStep(
        # v1.442.0 (chat-first plan Wave 7, group A): a rule on EVERY NEW EMAIL.
        # comm.inbound refuses a sender the destination's allowlist does not
        # hold before any rule runs (fail-closed), comm.base.supports_inbound
        # needs the reading server, and channel_toggle refuses two-way with an
        # empty list — the list itself is set on the Notifications page only.
        "email_rule_inbox", NOTIFICATIONS_HREF, "Open Notifications in Settings",
        "A rule on new emails (reflex_create, when it is offered) hears only mail that reaches an email "
        "destination of Iron Jarvis's with two-way listening on, and only from senders on that destination's "
        "Allowed senders list (an empty list lets nobody in). So the inbox the clients write to must be that "
        "destination's, its mail server for reading must be set (channel_connect's imap_host for a new "
        "destination; for a destination that already exists, on the Notifications page, because "
        "channel_connect refuses a name that exists), and each "
        "client's address must be on its Allowed senders list, which is set on the destination in Settings › "
        "Connections › Notifications: Iron Jarvis cannot fill that list from chat. Say so plainly and point "
        "there.",
    ),
    PageStep(
        "browser_addon", _BROWSER_FIX_HREF, "Set up the browser add-on",
        "Iron Jarvis's browser add-on is installed and paired by the user in Chrome or Edge, from Settings › "
        "Connections › Browser, which gives the steps and the pairing button. "
        f"{_NOT_FOR_THEM}.",
    ),
    PageStep(
        # v1.448.0 (Wave A "chat configures everything", A1): a CLOUD model
        # vendor or plan asked to answer or to be set up ("Use Claude for this
        # chat", "I have a ChatGPT Plus plan, can you use that?", "Set up
        # Gemini", "Use OpenRouter for the big questions") — the one area a
        # beginner most needs a door, and no chat tool connects these by
        # itself: a plan signs in on its page, a key goes on the secure card.
        # The local programs (Ollama, LM Studio) have model_server_add and are
        # not read here (settings.setup_words.VENDOR). The default-model
        # settings tools ride beside the door when the words ask for a change.
        "model_provider", ACCOUNTS_HREF, "Open Connections to set up a model",
        f"Connecting a model provider is done in {CONNECTIONS_PLACE}: a plan (Claude Code, Codex, Grok) signs "
        "in there with its own Sign in button, in a terminal on this PC, in the user's own browser; an API key "
        "(OpenAI, Anthropic, Google Gemini, OpenRouter, xAI, Mistral, DeepSeek) is pasted there, or on the "
        "secure card that config_secret offers when it is in this reply (the key never reaches the chat's "
        "model); the default model is then chosen there, or with config_set when it is offered — that changes "
        "it for EVERY chat; for one chat only, the model menu above the message box picks the model without "
        "changing the default, so a request about 'this chat' is answered with that menu first. Iron Jarvis "
        "cannot sign in to a plan or get a key for the user; say so plainly and point there.",
    ),
    PageStep(
        "local_model", help_href("local-models"), "Which local model to download",
        # Review: the first-run wizard is gone (an orphan module); the starter
        # model is offered by a NEW chat's connect doors while no model is set
        # up (components/onboarding/ConnectDoors, the chat page's own words).
        f"Iron Jarvis does not download models by itself (only the small starter model, {STARTER_MODEL_NAME}, "
        f"that a new chat's \"{CONNECT_DOORS_WORDS}\" choices offer while no model is set up). A local model "
        "is downloaded with Ollama (ollama pull followed by the model's name, which can run in a terminal in "
        f"Build) and then chosen in {CONNECTIONS_PLACE}. The guide \"Local Models by RAM Tier\" (in Help) says "
        "which model fits this PC; say so plainly and point there.",
    ),
)

_STEP_RULES: dict[str, Callable[[str], bool]] = {
    "accounts": _any(_ACCOUNT_RULES),
    "vendor_approval": _vendor_approval,
    "signin_microsoft": _any(_M365_RULES),
    "install_claude_code": _any(_install_rules(_CLAUDE)),
    "install_codex": _any(_install_rules(_CODEX)),
    "signin_claude_code": _any(_cli_rules(_CLAUDE, bare="claude")),
    "signin_codex": _any(_cli_rules(_CODEX)),
    "signin_grok": _any(_cli_rules(_GROK)),
    "signin_pi": _any(
        tuple(
            re.compile(r, re.IGNORECASE)
            for r in (
                _ACT + r"(?:\s+again)?\s+(?:to|into|on|with)\s+(?:the\s+)?" + _PI + r"\b",
                r"\b" + _PI + _NOUN_SIGNIN,
            )
        )
    ),
    "signin_opencode": _any(_cli_rules(_OPENCODE)),
    "google_setup": _google_setup,
    "gmail_setup": _gmail_setup,
    "slack_setup": _slack_setup,
    "email_rule_inbox": W.every_new_email,
    "browser_addon": _any(_BROWSER_RULES),
    "model_provider": W.names_a_model_provider,
    "local_model": _any(_LOCAL_MODEL_RULES),
}

NOT_OFFERED: tuple[NotOffered, ...] = (
    NotOffered(
        "desktop_control",
        # Review: "of its own" — no built-in tool controls the desktop (the
        # real registry is pinned for it), but an app the user connected may
        # offer such tools, and a flat "cannot" would make the model refuse a
        # tool it holds.
        "Iron Jarvis has no way of its own to control the Windows desktop: it cannot move the mouse, type "
        "on the keyboard, watch the screen, or click and type in desktop programs (only an app the user "
        "connected could, through its own tools in your list). It works in a web browser (through its "
        "browser add-on or its own browser), with the user's files (it can read and write documents such as "
        "Word and Excel files), and through connected apps' own tools; the user can paste a screenshot into "
        "the chat for it to read. Say so plainly; never pretend to act on the desktop.",
    ),
    NotOffered(
        "another_computer",
        # Review: LAN access opens TWO token-checked doors (daemon/auth
        # _is_exempt: the task door AND a remote agent's message back), and
        # only a destination with two-way chat on takes messages (Telegram,
        # Slack, email; never Discord).
        "Iron Jarvis's screens open only on this PC; another computer cannot open them. Another computer can "
        "run its own copy of Iron Jarvis, with its own settings and data, and the setting \"Let other "
        "computers on my network reach this Iron Jarvis\" only lets such a copy hand this one tasks (and a "
        "remote agent the user connected send its messages back); it never opens these screens there. From "
        "a phone, the user can chat with Jarvis through a notification destination with two-way chat "
        "turned on (Telegram, Slack or email). Say so plainly.",
    ),
    NotOffered(
        "full_access_default",
        # Review: Auto IS a folder default (harness/folder_defaults
        # CODE_POSTURES and TOOL_MODES), and Auto runs routine actions without
        # asking, so "a new session never starts without asking" was false.
        "Full access and \"Don't ask\" cannot be a folder's default: the user turns either one on in each "
        "coding session, so a new session never starts in them. Any other approval level or mode, Auto "
        "included, can be a folder's default. Say so plainly.",
    ),
    # v1.442.0 (chat-first plan Wave 7, group A): what the plan's 30 setup
    # requests asked for in a way Iron Jarvis does not do it. Each line is
    # pinned to the code that makes it true (tests/test_wave7_gaps_v14420.py).
    NotOffered(
        # daemon/app.py's auto-backup loop: IRONJARVIS_AUTO_BACKUP_HOURS
        # (default 24) and _KEEP (default 7), an environment setting, never
        # one of Iron Jarvis's settings; settings/schema has the copy folder.
        "jarvis_backup",
        "Iron Jarvis backs itself up by itself once a day (every 24 hours) and keeps the newest 7 backups; "
        "how often is not a setting in Iron Jarvis, and a schedule does not change it (a schedule runs a task, "
        "it does not make a backup). What can be set, with config_set when it is offered in this reply: "
        "\"Backup copy folder\", a folder every backup is also copied to (another drive, or a synced folder on "
        "this PC such as the OneDrive folder), and \"Copy media with backups\". A backup can also be made at "
        "any time with Back up now in Settings › System › Maintenance. Say so plainly.",
    ),
    NotOffered(
        # sentinels/service._propose → motivation/engine.add_backlog (never
        # spawns a session); the Sentinels page: "Each suggestion waits in
        # Autonomy → Proposals for your approval".
        "watcher_suggests",
        "A folder watcher (sentinel_add, when it is offered in this reply) only notices new or changed files: "
        "each time, it leaves one suggestion, carrying the task it was given, in the Proposals list on the "
        "Autonomy page, and nothing runs until the user approves it there. Watchers are off until the setting "
        "\"Folder watchers (sentinels)\" is turned on. So Iron Jarvis cannot file new files by itself the "
        "moment they arrive: the filing runs when the user approves a suggestion, or right now in this chat. "
        "Say so plainly.",
    ),
    NotOffered(
        # agents/allowance reads DynamicAgentRecord (agents the user made);
        # agent_update refuses a built-in agent (records_projects.resolve_agent).
        "builtin_agent_limit",
        "Iron Jarvis's own agents (the builder, reviewer, researcher, planner and the others it comes with) "
        "have no monthly limit of their own; only an agent the user made can have one. Offer to make the "
        "user's own agent that works like it, with a monthly dollar or token limit (agent_create, when it is "
        "offered in this reply), and to use that agent for the work. Say so plainly.",
    ),
    NotOffered(
        # The chat page's own words: the composer's "Dictate your message"
        # button and the ⋯ menu's "Voice chat" row.
        "voice_press",
        "Talking to Jarvis is a press on the chat page, not a setting, and Iron Jarvis cannot press it for the "
        "user: the microphone button in the message box dictates one message into the box, and \"Voice chat\" "
        "in the ⋯ menu at the top of the chat listens hands-free, sends what the user says when they pause and "
        "reads each reply aloud. Reading replies aloud on its own is the setting \"Read replies aloud\". Say so "
        "plainly.",
    ),
    NotOffered(
        # daemon/auth: /webhooks/<slug> is not token-exempt; desktop/main.js
        # binds 127.0.0.1 unless lan_access (private addresses only).
        "internet_calls_in",
        "A web service on the internet (Zapier, IFTTT, Make and the like) cannot reach Iron Jarvis: it answers "
        "only on this PC (and, with \"Let other computers on my network reach this Iron Jarvis\" on, on the "
        "user's own network), and its incoming webhooks also need this PC's private access token, which must "
        "never be given to an outside service. So such a service cannot start a task here. The other way "
        "works: an outgoing webhook (webhook_add with direction outbound) sends Iron Jarvis's events to a web "
        "address, such as a Zapier hook, when the user asks for that. Say so plainly.",
    ),
)

_NOT_OFFERED_RULES: dict[str, Callable[[str], bool]] = {
    "desktop_control": _any(_DESKTOP_RULES),
    "another_computer": _any(_ELSEWHERE_RULES),
    "full_access_default": _full_default,
    "jarvis_backup": W.jarvis_backup,
    "watcher_suggests": W.watch_and_act,
    "builtin_agent_limit": W.builtin_agent_limit,
    "voice_press": W.asks_for_voice,
    "internet_calls_in": W.internet_calls_in,
}

#: Every compiled pattern over the user's words (the linear-time pin reads it).
ALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    *_ACCOUNT_RULES, *_M365_RULES, _MS_VENDOR, _MS_APPROVAL, _ENTRA, _GOOGLE, _GOOGLE_SETUP, _GOOGLE_LITERAL,
    _SLACK, _SLACK_SETUP, *_BROWSER_RULES, *_LOCAL_MODEL_RULES, *_DESKTOP_RULES, *_ELSEWHERE_RULES,
    *_FULL_DEFAULT_RULES, _FOLDERISH, _NOT_A_GRANT, _SENTENCE,
    _SET_UP_SLACK, _DESTINATION_WORDS, _GMAIL_CONNECT, _FULL_FOR_A_FOLDER, _CODING_WORDS, ASKS_TO_MESSAGE_ME,
    *W.ALL_PATTERNS,
    *_cli_rules(_CLAUDE, bare="claude"), *_cli_rules(_CODEX), *_cli_rules(_GROK), *_cli_rules(_OPENCODE),
    *_install_rules(_CLAUDE), *_install_rules(_CODEX),
)

#: The heading the model reads the lines under.
BLOCK_HEADING = "# What Iron Jarvis can and cannot do for this request"
#: Most lines one message adds (a long message naming everything stays short).
MAX_LINES = 4


def _scan(text: Any) -> str:
    return str(text or "")[:SCAN_CHARS]


def page_steps(text: Any) -> list[PageStep]:
    """Every step the message is about, in priority order (pure)."""
    t = _scan(text)
    if not t.strip():
        return []
    out: list[PageStep] = []
    for step in STEPS:
        try:
            if _STEP_RULES[step.id](t):
                out.append(step)
        except Exception:  # noqa: BLE001 — a broken rule is no match
            continue
    return out


def page_step(text: Any) -> "PageStep | None":
    """The ONE step whose door the reply carries, or ``None``."""
    steps = page_steps(text)
    return steps[0] if steps else None


def not_offered(text: Any) -> list[NotOffered]:
    """What the message asks for that Iron Jarvis does not offer (pure)."""
    t = _scan(text)
    if not t.strip():
        return []
    out: list[NotOffered] = []
    for item in NOT_OFFERED:
        try:
            if _NOT_OFFERED_RULES[item.id](t):
                out.append(item)
        except Exception:  # noqa: BLE001 — a broken rule is no match
            continue
    return out


def prompt_block(text: Any) -> str:
    """The section the model is given for this message: one line per step
    the message is about and per thing not offered, under
    :data:`BLOCK_HEADING`; ``""`` when nothing matches (the system prompt is
    then byte-identical to before). Never raises."""
    try:
        # Review: a "not offered" line is the one that stops the model
        # pretending, so it is never the line the cap cuts: the steps give up
        # their places first (the door's own step always keeps one).
        steps = page_steps(text)
        nots = not_offered(text)
        room = max(1 if steps else 0, MAX_LINES - len(nots))
        lines = [s.line for s in steps[:room]] + [n.line for n in nots]
        if not lines:
            return ""
        return "\n\n" + BLOCK_HEADING + "\n" + "\n".join("- " + ln for ln in lines[:MAX_LINES])
    except Exception:  # noqa: BLE001 — no line beats a broken turn
        return ""


def step_not_needed(step: PageStep, text: Any, *, ready: "Callable[[str], bool | None] | None" = None,
                    installed: "Callable[[str], bool | None] | None" = None) -> bool:
    """v1.455.0 (readiness Wave G, item 23): is *step*'s door one this PC no
    longer needs? The Connections door for a model vendor that ALREADY answers
    (``ready(provider)`` True for one of the providers each named vendor means
    — every named vendor), and an install press for a program that is already
    installed (``installed(binary)`` True). ``None`` from a callback is "not
    known": the door stays. Never raises."""
    try:
        if step.id == "model_provider" and ready is not None:
            groups = W.model_providers_named(_scan(text))
            return bool(groups) and all(any(ready(p) is True for p in ids) for ids in groups)
        if step.install and installed is not None:
            binary = INSTALL_BINARIES.get(step.install, "")
            return bool(binary) and installed(binary) is True
    except Exception:  # noqa: BLE001 — not known: the door stays
        return False
    return False


def with_page_door(entries: Any, text: Any, *, ready: "Callable[[str], bool | None] | None" = None,
                   installed: "Callable[[str], bool | None] | None" = None) -> list[Any]:
    """The turn's door entries with the message's page door — appended LAST,
    only while there is room under :data:`doors.MAX_DOORS` and only when no
    door already leads to the same place, so it never pushes out a door the
    turn earned. v1.455.0: a step this PC no longer needs
    (:func:`step_not_needed`) gives its place to the next step the message is
    about. Never raises (the entries come back unchanged)."""
    base = list(entries or [])
    try:
        step = next(
            (s for s in page_steps(text) if not step_not_needed(s, text, ready=ready, installed=installed)),
            None,
        )
        if step is None:
            return base
        door = step.door()
        kept = collect_doors(base)
        if len(kept) >= MAX_DOORS or any(e.get("href") == door["href"] for e in kept):
            return base
        return base + [door]
    except Exception:  # noqa: BLE001 — no door beats no reply
        return base
