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

#: The guides the Help page serves (``routes/helpdocs._DOCS``; pinned equal).
HELP_GUIDES: tuple[str, ...] = ("handbook", "recommended-settings", "local-models")


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

    def door(self) -> dict[str, str]:
        out = {"href": self.href, "label": self.label}
        if self.press:
            out["press"] = self.press
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
    r"|re-?(?:sign|log)[\s-]*in|(?:sign|log)\s+back\s+in|re-?authenticat(?:e|ing))\b"
)
#: "is not signed in", "logged out", "sign-in expired", "keeps asking me to
#: log in" — a sign-in that needs doing again.
_STATE = (
    r"(?:(?:not|n['’]t|never)\s+(?:been\s+)?(?:signed|logged)\s+in|(?:signed|logged)\s+out"
    r"|(?:sign[\s-]*in|log[\s-]*in|login)\s+(?:has\s+|keeps\s+)?(?:expired|failed|failing|broke"
    r"|stopped|isn['’]t\s+working|is\s+not\s+working|doesn['’]t\s+work|won['’]t\s+work)"
    r"|(?:needs?|wants?|ask(?:s|ing)?|keeps\s+asking)\s+(?:me\s+)?to\s+(?:sign|log)\s+in)"
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
        r"\b" + name + _SUBJECT_VERB + _GAP + _STATE,
    ]
    if bare:
        rules.append(_ACT + r"(?:\s+again)?\s+(?:to|into|on|with)\s+(?:(?:my|the|your)\s+)?" + bare + r"\b")
    return tuple(re.compile(r, re.IGNORECASE) for r in rules)


_CLAUDE = r"claude[\s-]*(?:code|cli)"
_CODEX = r"codex(?:\s+cli)?"
_GROK = r"grok(?:\s+cli)?"
_OPENCODE = r"open[\s-]?code"
#: Pi, never a Raspberry Pi, never the number or a pie.
_PI = r"(?<!raspberry\s)pi(?![\s-]*(?:hole|zero|\d|chart|day|pi\b))"

_ACCOUNT_VENDOR = r"(?:claude|codex|chatgpt|openai|grok|anthropic)(?:[\s-]*code)?"
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
    r"(?![^.!?\n]{0,30}?\bon\s+(?:my|the|her|his|our)\s+(?:phone|iphone|android|ipad|tablet|mac|macbook)\b)"
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
    r"|(?:approve|consent\s+to|allow)\s+(?:the\s+|this\s+|our\s+|my\s+)?(?:iron\s+jarvis\s+)?app)\b",
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
    r"\b(?:(?:developer|dev)\s+(?:setup|set[\s-]up|app|project|account|console)|cloud\s+console"
    r"|client\s+(?:id|secret)|app\s+id|oauth|consent\s+screen|credentials\s+(?:page|screen)"
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
    r"(?:\b(?:app|bot)\s+tokens?\b|\bapp[\s-]level\s+tokens?\b|\bxox[bp]-|\bxapp-"
    r"|\b(?:app|slack|the|its)\s+manifest\b"
    r"|\bmember\s+id\b|\bsocket\s+mode\b|\bmessages\s+tab\b"
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
        r"(?!\s+(?:to|with)\s+(?:the\s+|my\s+)?(?:internet|wi-?fi|network|vpn|printer|tv|phone|bluetooth)\b)",
        r"\bbrowser\s+pairing\b|\bpairing\s+(?:code\s+for\s+)?(?:the|my|your)\s+browser\b",
        r"\b(?:install|set\s*up|setup|add|get|load|download|enable|turn\s+on|reinstall|update)\w*\s+"
        r"(?:your|iron\s+jarvis['’]?s?|jarvis['’]?s?|its)\s+(?:(?:browser|chrome|edge)\s+" + _ADDON
        + r"|add[\s-]?on)\b",
        r"\b(?:install|set\s*up|setup|add|get|load|download|enable|turn\s+on|reinstall|update)\w*\s+"
        r"(?:the|a|an)\s+(?:browser\s+add[\s-]?on|(?:browser|chrome|edge)\s+" + _ADDON
        + r"\b(?=" + _GAP + r"\b(?:you|jarvis)\b))",
        r"\bthe\s+(?:browser|chrome|edge)\s+" + _ADDON + r"\b" + _GAP
        + r"(?:won['’]?t|doesn['’]?t|isn['’]?t|not|can['’]?t|never)\s+(?:pair|connect|work|show|load|install)",
        r"\b(?:iron\s+)?jarvis\s+(?:browser\s+)?" + _ADDON + r"\b",
    )
)

#: Downloading a local model.
_MODEL_NAMES = r"(?:llama|qwen|gemma|mistral|deepseek|phi|glm|gpt-oss|nemotron|granite|smollm)"
_LOCAL_MODEL_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\b(?:download|pull|install|get|grab|set\s*up|add)\w*\s+"
        r"(?:(?:a|an|the|some|my|another|new|good|small|free|decent)\s+){0,2}"
        r"(?:local|offline|private|ollama|open[\s-]?source|open[\s-]?weights?)\s+"
        r"(?:ai\s+|llm\s+|language\s+)?(?:models?|llms?)\b",
        r"\bollama\s+(?:pull|run)\s+[a-z0-9]",
        r"\b(?:download|pull|install|grab)\w*\s+(?:the\s+)?" + _MODEL_NAMES + r"[\s.:-]?\d",
        r"\b(?:download|pull|install|grab)\w*\s+(?:the\s+)?" + _MODEL_NAMES + r"\b[\w.: -]{0,20}?\bmodel\b",
        r"\b(?:which|what)\s+(?:local\s+|ollama\s+|offline\s+)?(?:ai\s+)?models?\s+(?:should|can|could|do)\s+"
        r"(?:i|we)\s+(?:download|pull|install)\b",
        r"\b(?:local|offline|ollama)\s+(?:ai\s+)?models?\b" + _GAP + r"\b(?:download|pull|install)\w*",
    )
)

#: Controlling the Windows desktop (not offered).
_DESKTOP_NOUN_GUARD = (
    r"(?!\s*(?:['’]s)?\s*(?:brightness|resolution|time|timeout|saver|size|settings?|wallpaper|background"
    r"|layout|folder|files?|shortcuts?|volume|temperature|name|password|login|account|camera"
    r"|microphone|mic|speakers?|drivers?|protector|recording|control\s+panel|icons?\s+size|reader"
    r"|readers|app|apps|version|client|notifications?|edition)\b)"
)
_DESKTOP_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\b(?:control|operate|take\s+over|takeover|remote[\s-]+control)\s+(?:my|the|this)\s+"
        r"(?:mouse|cursor|keyboard|desktop|screen|computer|pc|laptop)\b" + _DESKTOP_NOUN_GUARD,
        r"\b(?:move|drive)\s+(?:my|the|this)\s+(?:mouse|cursor)\b",
        r"\b(?:use|click(?:\s+on)?|type\s+(?:on|into|with))\s+(?:my|the|this)\s+"
        r"(?:mouse|cursor|keyboard|desktop|screen)\b" + _DESKTOP_NOUN_GUARD,
        r"\b(?:see|look\s+at|watch|view|read)\s+(?:my|the|this)\s+(?:screen|desktop|monitor)\b"
        + _DESKTOP_NOUN_GUARD,
        r"\bscreen\s*shot\s+of\s+(?:my|the|this)\s+(?:screen|desktop|monitor)\b",
        r"\b(?:open|launch|start)\s+(?:(?:the|my)\s+)?(?:excel|word|outlook|quickbooks|notepad|powerpoint|teams"
        r"|calculator|file\s+explorer|explorer|photoshop|lacerte|ultratax|drake|proseries)\b" + _GAP
        + r"\b(?:and|then)\s+(?:click|type|enter|press|fill|select|navigate|scroll)\b",
        r"\b(?:desktop|windows|pc|computer|gui)\s+(?:automation|control)\b(?!\s+panel)",
        r"\bcomputer\s+use\b(?=" + _GAP + r"\b(?:desktop|windows|outside|programs?)\b)",
    )
)

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
_ELSEWHERE_RULES = tuple(
    re.compile(r, re.IGNORECASE)
    for r in (
        r"\b" + _OPEN_VERB + r"\s+" + _JARVIS_OBJ + r"\b" + _GAP + r"\b" + _OTHER_COMPUTER,
        r"\b(?:open|use|access|reach|control|get\s+to)\s+" + _JARVIS_OBJ + r"\s+remotely\b",
        r"\bremote\s+access\s+(?:to\s+)?(?:iron\s+jarvis|jarvis|the\s+dashboard|this\s+app)\b",
        r"\b" + _OPEN_VERB + r"\s+" + _JARVIS_OBJ + r"\b" + _GAP
        + r"\b(?:over|across|from|through)\s+(?:my|the|our)\s+(?:network|lan|wi-?fi|internet|tailscale|vpn)\b",
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


def _slack_setup(t: str) -> bool:
    return any(_SLACK.search(s) and _SLACK_SETUP.search(s) for s in _sentences(t))


def _full_default(t: str) -> bool:
    for s in _sentences(t):
        if _FOLDERISH.search(s) and not _NOT_A_GRANT.search(s) and any(r.search(s) for r in _FULL_DEFAULT_RULES):
            return True
    return False


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #

_NOT_FOR_THEM = "Iron Jarvis cannot do it for the user, so say so plainly and point there"


def _cli_line(name: str, inside: str) -> str:
    return (
        f"Signing in to {name} is done on this PC, in {name} itself: the Sign in button beside {name} in "
        f"Settings › Connections › Accounts & keys opens it in a terminal, where {inside}. "
        f"{_NOT_FOR_THEM}."
    )


#: The steps, in PRIORITY order: the first that matches is the message's
#: door (an account of the user's before this PC's own Claude Code login;
#: the admin's approval before the Microsoft sign-in).
STEPS: tuple[PageStep, ...] = (
    PageStep(
        "accounts", ACCOUNTS_HREF, "Open your accounts in Settings",
        "Adding a Claude, Codex or Grok account, or signing one in again, is done on the Iron-Proxy card "
        "(the user's accounts) in Settings › Connections › Accounts & keys: each account signs in with its "
        f"own Sign in button, in a terminal on this PC. {_NOT_FOR_THEM}.",
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
        "Signing in to Microsoft 365 is done on its card in Settings › Connections › Apps & tools: after "
        "Connect, its Sign in to Microsoft button opens a terminal with a web address and a code, and the "
        "user signs in there with their main account, in a private browser window. "
        f"{_NOT_FOR_THEM}.",
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
        "Google Drive's steps are in Settings › Connections › Accounts & keys (its card's One-time setup), "
        "and Gmail's card in Settings › Connections › Apps & tools links its own steps; say so plainly and "
        "point there.",
    ),
    PageStep(
        "slack_setup", NOTIFICATIONS_HREF, "Set up Slack in Settings",
        "A Slack app (its bot and app tokens, the app manifest, the user's member ID) is made by the user on "
        "Slack's own website, signed in as themselves; Iron Jarvis cannot make it for them. Settings › "
        "Connections › Notifications has the Slack form, a manifest to copy and the steps; say so plainly "
        "and point there.",
    ),
    PageStep(
        "browser_addon", _BROWSER_FIX_HREF, "Set up the browser add-on",
        "Iron Jarvis's browser add-on is installed and paired by the user in Chrome or Edge, from Settings › "
        "Connections › Browser, which gives the steps and the pairing button. "
        f"{_NOT_FOR_THEM}.",
    ),
    PageStep(
        "local_model", help_href("local-models"), "Which local model to download",
        "Iron Jarvis does not download models by itself (only the small starter model its first-run screen "
        "offers). A local model is downloaded with Ollama (ollama pull followed by the model's name, which "
        "can run in a terminal in Build) and then chosen in Settings › Connections. The guide \"Local Models "
        "by RAM Tier\" (in Help) says which model fits this PC; say so plainly and point there.",
    ),
)

_STEP_RULES: dict[str, Callable[[str], bool]] = {
    "accounts": _any(_ACCOUNT_RULES),
    "vendor_approval": _vendor_approval,
    "signin_microsoft": _any(_M365_RULES),
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
    "slack_setup": _slack_setup,
    "browser_addon": _any(_BROWSER_RULES),
    "local_model": _any(_LOCAL_MODEL_RULES),
}

NOT_OFFERED: tuple[NotOffered, ...] = (
    NotOffered(
        "desktop_control",
        "Iron Jarvis cannot control the Windows desktop: it cannot move the mouse, type on the keyboard, see "
        "the screen, or click and type in desktop programs. It works in a web browser (through its browser "
        "add-on or its own browser), with the user's files (it can read and write documents such as Word "
        "and Excel files), and through connected apps' own tools. Say so plainly; never pretend to act on "
        "the desktop.",
    ),
    NotOffered(
        "another_computer",
        "Iron Jarvis's screens open only on this PC; another computer cannot open them. Another computer can "
        "run its own copy of Iron Jarvis, with its own settings and data, and the setting \"Let other "
        "computers on my network reach this Iron Jarvis\" only lets such a copy hand this one tasks. From a phone, the user can chat with Jarvis through a notification "
        "destination such as Slack or Telegram. Say so plainly.",
    ),
    NotOffered(
        "full_access_default",
        "Full access and \"Don't ask\" cannot be a folder's default: each coding session chooses them itself, "
        "so a new session never starts without asking. Any other approval level or mode can be a folder's "
        "default. Say so plainly.",
    ),
)

_NOT_OFFERED_RULES: dict[str, Callable[[str], bool]] = {
    "desktop_control": _any(_DESKTOP_RULES),
    "another_computer": _any(_ELSEWHERE_RULES),
    "full_access_default": _full_default,
}

#: Every compiled pattern over the user's words (the linear-time pin reads it).
ALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    *_ACCOUNT_RULES, *_M365_RULES, _MS_VENDOR, _MS_APPROVAL, _ENTRA, _GOOGLE, _GOOGLE_SETUP, _GOOGLE_LITERAL,
    _SLACK, _SLACK_SETUP, *_BROWSER_RULES, *_LOCAL_MODEL_RULES, *_DESKTOP_RULES, *_ELSEWHERE_RULES,
    *_FULL_DEFAULT_RULES, _FOLDERISH, _NOT_A_GRANT, _SENTENCE,
    *_cli_rules(_CLAUDE, bare="claude"), *_cli_rules(_CODEX), *_cli_rules(_GROK), *_cli_rules(_OPENCODE),
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
        lines = [s.line for s in page_steps(text)] + [n.line for n in not_offered(text)]
        if not lines:
            return ""
        return "\n\n" + BLOCK_HEADING + "\n" + "\n".join("- " + ln for ln in lines[:MAX_LINES])
    except Exception:  # noqa: BLE001 — no line beats a broken turn
        return ""


def with_page_door(entries: Any, text: Any) -> list[Any]:
    """The turn's door entries with the message's page door — appended LAST,
    only while there is room under :data:`doors.MAX_DOORS` and only when no
    door already leads to the same place, so it never pushes out a door the
    turn earned. Never raises (the entries come back unchanged)."""
    base = list(entries or [])
    try:
        step = page_step(text)
        if step is None:
            return base
        door = step.door()
        kept = collect_doors(base)
        if len(kept) >= MAX_DOORS or any(e.get("href") == door["href"] for e in kept):
            return base
        return base + [door]
    except Exception:  # noqa: BLE001 — no door beats no reply
        return base
