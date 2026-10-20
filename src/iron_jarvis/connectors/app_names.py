"""Chat knows the apps by name, and what is connected (v1.354.0, G39).

Before this, chat recognised an app only through a short word list
(``settings.records._APP_WORDS``): "check my Gmail", "open my inbox" and "is
github connected?" armed nothing, "search my Google Drive for the lease"
armed WEB search, and the model was never told what is connected.

ONE matcher, built from the catalog itself (:data:`connectors.catalog.CATALOG`
— its names, plus the words people use for them in :data:`ALIASES`), read by:

* ``settings.records.record_tools_for`` — a named app arms ``app_connect``;
* ``daemon.chat_turn._gather_grounding`` — a short "Apps named in this
  message" section saying, for each, connected (N tools) or not (both lanes);
* the stream lane (``routes/chat.py``) — a named app that IS connected with
  tools loaded has its tools ask-armed (visible, never granted);
* ``tools.autoselect`` — the web rule does not fire when the only web signal
  is a catalog app's name ("search my Google Drive" is not a web search);
* both chat lanes' doors (v1.359.0, G41) — a named app that is NOT connected,
  named for the user's own data in it rather than in research about it
  (:func:`connect_offers`), puts its Connect door under the reply whether or
  not the model called ``app_connect`` (``daemon.doors.with_connect_door``).

Rules that keep it honest:

* An app whose NAME is an everyday word (:data:`GENERIC` — Knowledge graph,
  Fetch web pages, Files & folders, Box, Step-by-step reasoning) counts only
  beside a connect / set-up verb ("connect Box"), never in "put it in a box".
  v1.357.0 (G82): those four took the Tools page's names (one catalog, one
  name); the names they had before stay in :data:`ALIASES`, so "set up
  sequential thinking" and "connect the filesystem" still name them.
* "my email" / "my inbox" are matched under Gmail only when no other mail
  service is named ("check my Outlook inbox" names Microsoft 365). v1.360.0
  (lane 10B): an everyday word names a KIND of app — :func:`resolve_kinds`
  turns it into the mail (or calendar) app the user CONNECTED, or one choice
  naming every app of that kind when none is; "my calendar" is never offered
  as Microsoft 365 on those words alone.
* v1.360.0 (lane 10B): the Connect door is for the user's OWN data only, and
  the default is no door (:func:`_for_own_data`): "stripe the table rows",
  "explain how postgres indexes work" and "a post about our GitHub launch"
  name no data of the user's.
* Claude Code and Codex are not catalog apps; :func:`clis_named` finds them so
  the section can say whether they are signed in, from the CACHED verdict
  (``ProviderManager.cli_login_status`` — never a probe).

Pure text work here; the status reads are split by where they may run:
:func:`live_tools` is in-memory and runs ON the loop (the registry is iterated
— a worker thread could race a pack registering, the v1.311.0 rule), and
:func:`app_states` does the blocking reads (the connections table, the memory
bases) and must be called OFF the loop. Neither ever raises.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

#: The longest message text the matcher reads (the autoselect bound).
_MAX_TEXT = 4000

#: Words people use for a catalog app beyond its catalog name. Regex
#: fragments, matched case-insensitively between word boundaries.
ALIASES: dict[str, tuple[str, ...]] = {
    "github": (r"git\s*hub",),
    "postgres": (r"postgres(?:ql)?",),
    "google_maps": (r"google\s*maps",),
    # v1.354.0 re-review: "my drive" in lower case is this PC's disk ("how
    # much space is left on my drive", "clean up my drive"); only Google's own
    # capitalised name for the Drive root, "My Drive", names the app.
    "google_drive": (r"google\s*drive", r"g-?drive", r"(?-i:My\s+Drive)"),
    "onedrive": (r"one\s*drive",),
    "dropbox": (r"drop\s*box",),
    "brave_search": (r"brave\s*search",),
    # v1.357.0 (G82): the names these cards had before they took the Tools
    # page's ("Filesystem", "Web Fetch", "Knowledge Graph Memory",
    # "Sequential Thinking") — still the words people use for them.
    "filesystem": (r"file\s*system",),
    "fetch": (r"web\s*fetch",),
    "memory": (r"knowledge\s+graph\s+memory",),
    "sequential_thinking": (r"sequential\s*thinking",),
    # v1.356.0 integrate (G60, lane 6D's note): "what's on my calendar
    # tomorrow?" and "check my Outlook inbox" name Microsoft 365 (Outlook, the
    # calendar, OneDrive). "Outlook" only capitalised ("an outlook on the
    # market" is a view), and "my calendar" never as "my calendar year".
    "microsoft_365": (
        r"microsoft\s*365",
        r"office\s*365",
        r"m365",
        r"(?-i:Outlook)",
        r"outlook\.com",
        r"my\s+(?:outlook\s+)?calendar(?!\s+years?\b)",
    ),
}

#: The everyday words for mail that mean Gmail (the one mail app in the
#: catalog) — only when no other mail service is named, and never when the
#: word only describes one of the user's own records ("pause my inbox goal",
#: "my email schedule") or an address ("my email address").
_NOT_MAIL_ITSELF = (
    r"(?!\s+(?:address(?:es)?|goals?|schedules?|workflows?|rules?|watchers?|webhooks?"
    r"|sentinels?|reflex(?:es)?|zero|templates?|signatures?)\b)"
)
#: v1.354.0 re-review: "the email" is a MESSAGE the user has in hand ("rewrite
#: the email below", "summarize the email", "make the email shorter") — the
#: everyday request of a new user, which must not be told Gmail is not
#: connected. Only "my email(s)" names the mailbox, and not when it points at
#: text in the message ("my email below:", "my email draft").
_NOT_A_PASTED_ONE = r"(?!\s*(?::|(?:below|above|here|drafts?)\b))"
_GMAIL_WORDS = re.compile(
    r"\bg-?mail\b"
    r"|\bmy\s+e-?mails?\b" + _NOT_MAIL_ITSELF + _NOT_A_PASTED_ONE
    + r"|\b(?:my|the)\s+(?:e-?mail\s+)?(?:inbox|mailbox)\b" + _NOT_MAIL_ITSELF,
    re.IGNORECASE,
)
_OTHER_MAIL = re.compile(
    r"\b(?:outlook|hotmail|yahoo|icloud|exchange|proton\s*mail|aol)\b", re.IGNORECASE
)

# --------------------------------------------------------------------------- #
# v1.360.0 (lane 10B): an EVERYDAY word names a KIND of app, not Gmail.
# --------------------------------------------------------------------------- #
#: The catalog apps of each kind an everyday word can mean. "check my inbox"
#: named Gmail for everyone, so an Outlook (Microsoft 365) user got "Connect
#: Gmail in Settings" and "Gmail: not connected" while Microsoft 365's tools
#: stayed unarmed (the final re-audit's regression). Google Calendar has no
#: catalog card, so "my calendar" can only ever be Microsoft 365 here — and
#: that is exactly why it is never OFFERED on those words alone.
KIND_APPS: dict[str, tuple[str, ...]] = {
    "mail": ("gmail", "microsoft_365"),
    "calendar": ("microsoft_365",),
}
#: The catalog id the everyday words of each kind are matched under (the
#: matcher's own reading, :data:`_GMAIL_WORDS` and ``ALIASES``).
_KIND_HIT_ID: dict[str, str] = {"mail": "gmail", "calendar": "microsoft_365"}
#: The everyday mail words — :data:`_GMAIL_WORDS` without Gmail's own name.
_GENERIC_MAIL_RX = re.compile(
    r"\bmy\s+e-?mails?\b" + _NOT_MAIL_ITSELF + _NOT_A_PASTED_ONE
    + r"|\b(?:my|the)\s+(?:e-?mail\s+)?(?:inbox|mailbox)\b" + _NOT_MAIL_ITSELF,
    re.IGNORECASE,
)
#: "my calendar" (never "my calendar year"), and not "my Outlook calendar".
_GENERIC_CALENDAR_RX = re.compile(r"\bmy\s+calendar(?!\s+years?\b)", re.IGNORECASE)
#: Each kind's apps BY NAME: a message that names one is not generic.
_MAIL_BRAND_RX = re.compile(
    r"\bg-?mail\b|\bmicrosoft\s*365\b|\boffice\s*365\b|\bm365\b|\boutlook\.com\b|(?-i:\bOutlook\b)",
    re.IGNORECASE,
)
_CALENDAR_BRAND_RX = re.compile(
    r"\bmicrosoft\s*365\b|\boffice\s*365\b|\bm365\b|\boutlook\.com\b|(?-i:\bOutlook\b)",
    re.IGNORECASE,
)

#: Catalog apps whose name is an everyday word: they count only beside a
#: connect / set-up verb, within a short reach before the name.
GENERIC: frozenset[str] = frozenset(
    {"memory", "fetch", "filesystem", "box", "sequential_thinking"}
)

#: The verbs that make a generic name an app ("connect Box", "set up the
#: filesystem"). "add" is not one ("add a box to the diagram").
CONNECT_VERB = (
    r"(?:connect|reconnect|set\s*up|setup|install|hook\s+up|link|turn\s+on|enable"
    r"|sign\s+in\s+to|log\s+in\s+to)"
)

#: Brand names that are also everyday words: these readings are not the app.
#: v1.359.0 integrate (lane 9C review): a named app that is not connected now
#: puts a Connect door under the reply, so a false reading shows. "I have no
#: notion what to write" / "the faintest notion" and "there is slack in the
#: schedule" / "slack in the plan" are the words, not the apps ("is Slack
#: connected?" and "any Notion pages" still name them).
_NOT_THE_APP: dict[str, str] = {
    "notion": (
        r"(?<![Aa] )(?<![Tt]he )(?<!no )(?<!faintest )(?<!slightest )(?<!vague )"
        r"\bnotion\b(?!\s+(?:that|of)\b)"
    ),
    "slack": (
        r"(?<![Ss]ome )(?<![Uu]p the )(?<!there is )(?<!there's )(?<!little )(?<!more )"
        r"\bslack\b(?!\s+off\b)(?!\s+in\s+(?:the|my|our|your|this)\s+"
        r"(?:schedule|plan|timeline|budget|rope|line|chain|system|week|day)\b)"
    ),
    "stripe": r"(?<![Aa] )\bstripe\b(?!\s+(?:on|of|across|down|along|pattern|through)\b)",
}

#: The subscription CLIs a message may ask about ("is Claude Code signed in?").
CLI_WORDS: dict[str, str] = {
    "claude-cli": r"\bclaude\s*code\b",
    "codex-cli": r"\bcodex\b",
}

#: Words that ask to connect, re-connect or fix an app's sign-in — a named app
#: that is already connected keeps ``app_connect`` armed only beside one.
CONNECT_INTENT = re.compile(
    r"\b(?:" + CONNECT_VERB + r"|tokens?|re-?auth\w*|renew\w*|expired|keys?)\b",
    re.IGNORECASE,
)


def _name_pattern(name: str) -> str:
    return r"\s+".join(re.escape(w) for w in name.lower().split())


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[str, "re.Pattern[str]"], ...]:
    """``(catalog id, pattern)`` for every catalog app, in catalog order."""
    from .catalog import CATALOG

    out: list[tuple[str, re.Pattern[str]]] = []
    for c in CATALOG:
        if c.id == "gmail":
            out.append((c.id, _GMAIL_WORDS))
            continue
        if c.id in _NOT_THE_APP:
            out.append((c.id, re.compile(_NOT_THE_APP[c.id], re.IGNORECASE)))
            continue
        names = [_name_pattern(c.name), *ALIASES.get(c.id, ())]
        body = "|".join(f"(?:{n})" for n in names)
        if c.id in GENERIC:
            rx = rf"\b{CONNECT_VERB}\b[^.?!\n]{{0,40}}?\b(?:{body})\b"
        else:
            rx = rf"\b(?:{body})\b"
        out.append((c.id, re.compile(rx, re.IGNORECASE)))
    return tuple(out)


def apps_named(text: str) -> list[str]:
    """The catalog app ids *text* names, in catalog order (deduped). Never
    raises; "" or no app names ``[]``."""
    t = str(text or "")[:_MAX_TEXT]
    if not t.strip():
        return []
    out: list[str] = []
    try:
        for cid, rx in _compiled():
            if cid == "gmail" and _OTHER_MAIL.search(t) and not re.search(
                r"\bg-?mail\b", t, re.IGNORECASE
            ):
                continue
            if rx.search(t):
                out.append(cid)
    except Exception:  # noqa: BLE001 — a matcher fault names nothing
        return []
    return out


def app_name_hits(text: str) -> list[tuple[str, int, int]]:
    """``(catalog id, start, end)`` for every place *text* names an app, in
    catalog order (v1.358.0: the web rule asks WHICH app a search verb is
    aimed at, because only a CONNECTED app takes a search over). Never
    raises."""
    t = str(text or "")[:_MAX_TEXT]
    hits: list[tuple[str, int, int]] = []
    try:
        for cid, rx in _compiled():
            if cid == "gmail" and _OTHER_MAIL.search(t) and not re.search(
                r"\bg-?mail\b", t, re.IGNORECASE
            ):
                continue
            hits += [(cid, m.start(), m.end()) for m in rx.finditer(t)]
    except Exception:  # noqa: BLE001
        return []
    return hits


def app_name_spans(text: str) -> list[tuple[int, int]]:
    """Where in *text* each named app's words sit (for the web rule, which
    must not count "Google" in "Google Drive" as a web signal). Never raises."""
    return [(start, end) for _cid, start, end in app_name_hits(text)]


def clis_named(text: str) -> list[str]:
    """The subscription CLI providers *text* names ("claude-cli", "codex-cli")."""
    t = str(text or "")[:_MAX_TEXT]
    return [p for p, rx in CLI_WORDS.items() if re.search(rx, t, re.IGNORECASE)]


def wants_connect(text: str) -> bool:
    """Does *text* ask to connect / fix an app's sign-in (not just use it)?"""
    return bool(CONNECT_INTENT.search(str(text or "")[:_MAX_TEXT]))


def generic_kinds(text: str) -> list[str]:
    """The KINDS of app *text* names only by an everyday word (v1.360.0,
    lane 10B): ``"mail"`` for "check my inbox" / "what's in my email today"
    and ``"calendar"`` for "what's on my calendar", each only when the message
    names no app of that kind by its own name ("check my Gmail", "my Outlook
    inbox", "my Outlook calendar") and, for mail, no other mail service
    ("my Yahoo inbox" names no catalog app at all). Order: mail, calendar.
    Pure; never raises."""
    t = str(text or "")[:_MAX_TEXT]
    out: list[str] = []
    try:
        if (
            _GENERIC_MAIL_RX.search(t)
            and not _MAIL_BRAND_RX.search(t)
            and not _OTHER_MAIL.search(t)
        ):
            out.append("mail")
        if _GENERIC_CALENDAR_RX.search(t) and not _CALENDAR_BRAND_RX.search(t):
            out.append("calendar")
    except Exception:  # noqa: BLE001 — a matcher fault names no kind
        return []
    return out


def kind_candidates(text: str, named: list[str]) -> list[str]:
    """*named* plus every catalog app of each kind *text* names only by an
    everyday word (:func:`generic_kinds`) — the apps whose loaded tools the
    grounding hop reads (``live_tools``, on the loop) so the kind can be
    resolved to the one that is connected. Order kept, deduped. Never
    raises."""
    out = [c for c in (named or []) if c]
    try:
        for kind in generic_kinds(text):
            for cid in KIND_APPS.get(kind, ()):
                if cid not in out:
                    out.append(cid)
    except Exception:  # noqa: BLE001
        return [c for c in (named or []) if c]
    return out


def resolve_kinds(
    text: str, named: list[str], live: dict[str, list[str]]
) -> tuple[list[str], dict[str, list[str]]]:
    """The apps the message really means, and the kinds left as a CHOICE
    (v1.360.0, lane 10B — the final re-audit's regression: "check my inbox"
    meant Gmail for everyone).

    *named* is :func:`apps_named`; *live* is :func:`live_tools` over
    :func:`kind_candidates` (an MCP app is in it once it is SAVED: connected,
    off, not working or not signed in yet — each says its own state). For
    each kind *text* names only by an everyday word:

    * an app of that kind is saved → the word means THAT app (Microsoft 365
      for an Outlook user, Gmail for a Gmail user, both when both are saved):
      its state is said, its tools are armed (stream lane), no door;
    * none is saved → mail stays a choice between every mail app of the
      catalog (one door names them all, once — never Gmail alone);
      the calendar is a choice with nothing to offer (the only calendar card
      is Microsoft 365, and "my calendar" does not say Outlook).

    Returns ``(named, choices)`` — *named* with the everyday word's app
    replaced by the saved ones (or every app of the kind, for a choice), and
    ``choices`` = ``{kind: [its apps]}`` for :func:`app_states` to fold into
    one row. Never raises (a fault answers *named* unchanged)."""
    out = [c for c in (named or []) if c]
    choices: dict[str, list[str]] = {}
    try:
        for kind in generic_kinds(text):
            hit_id = _KIND_HIT_ID[kind]
            apps = list(KIND_APPS.get(kind, ()))
            saved = [cid for cid in apps if cid in (live or {})]
            pick = saved or apps
            if not saved:
                choices[kind] = list(apps)
            # The everyday word's app takes the picked apps' place, in order.
            nxt: list[str] = []
            for c in out if hit_id in out else [*out, hit_id]:
                for cid in pick if c == hit_id else [c]:
                    if cid not in nxt:
                        nxt.append(cid)
            out = nxt
        return out, choices
    except Exception:  # noqa: BLE001 — knowing the apps never breaks a turn
        return [c for c in (named or []) if c], {}


# --------------------------------------------------------------------------- #
# v1.359.0 (G41): the Connect door, without the model.
# --------------------------------------------------------------------------- #
#: How far back from an app's name its clause is read (the words that say
#: whether the message is about the user's own data in it or about the app).
_CLAUSE_REACH = 80
#: A connect / set-up verb in the app's clause ("how do I connect Dropbox").
_CONNECT_VERB_RX = re.compile(rf"\b{CONNECT_VERB}\b", re.IGNORECASE)
#: A how-to question about the app ("how to share a folder in my Drive").
_HOW_TO_RX = re.compile(r"\bhow\s+(?:to|do|does|can|should|would)\b", re.IGNORECASE)
#: "What is <app>?" — the app itself, right after the question words (never
#: "what is in my Dropbox", which asks about the user's own files).
_WHAT_IS_RX = re.compile(
    r"\bwhat(?:\s+(?:is|are|does|do)|['’]s)\s+(?:the\s+|an?\s+)?\Z", re.IGNORECASE
)
#: Research words right before the app ("news about GitHub", "reviews of
#: Notion", "alternatives to Slack", "apps like Notion", "cheaper than Box",
#: and — v1.359.0 review — the company itself: "who founded Dropbox").
_RESEARCH_BEFORE_RX = re.compile(
    r"(?:\babout"
    r"|\b(?:reviews?|alternatives?|competitors?|history|pricing|prices?|costs?|docs"
    r"|documentation|features?|tutorials?|guides?|news)\s+(?:of|for|to|on|about)"
    r"|\b(?:vs\.?|versus|than|compared\s+(?:to|with)|instead\s+of|like|similar\s+to)"
    r"|\bwho\s+(?:founded|owns|owned|made|makes|created|runs|bought|acquired)"
    # v1.360.0 (lane 10B): the product's news, never the user's own data.
    r"|\bwhat(?:['’]s|\s+is)\s+new\s+(?:in|on|with))"
    r"\s+(?:the\s+)?\Z",
    re.IGNORECASE,
)
#: A comparison anywhere in the app's sentence ("is Dropbox better than
#: Google Drive?", "compare Notion and Evernote", and — v1.359.0 review —
#: "what's the difference between Dropbox and Google Drive", "which is
#: better, Notion or Obsidian?", "should I use Dropbox or Google Drive?") —
#: research about the apps. A possessive is read first, so "should I use my
#: Dropbox or my Drive for this?" stays the user's own.
_COMPARE_RX = re.compile(
    r"\b(?:better|worse|cheaper|pricier|faster|slower|safer|easier|harder)\s+than\b"
    r"|\bcompar(?:e|ed|es|ing|ison)\b|\bvs\.?(?!\w)|\bversus\b|\bpros\s+and\s+cons\b"
    r"|\bdifferences?\s+between\b"
    r"|\bwhich\s+(?:one\s+)?(?:is|are|was|would\s+be)\s+(?:the\s+)?"
    r"(?:better|best|cheaper|cheapest|faster|fastest|safer|safest|easier|easiest)\b"
    r"|\bshould\s+(?:i|we)\s+(?:use|pick|choose|get|go\s+with|switch\s+to)\b"
    r"[^.!?\n]{0,60}?\bor\b",
    re.IGNORECASE,
)
#: What the app COSTS, asked in its sentence ("how much does Dropbox cost?",
#: "how much would Slack charge us") — research about the app (v1.359.0
#: review: "Dropbox pricing" was already research, its question form was
#: not). Needs a cost word, so "how much space is left in Dropbox" stays own.
_PRICE_RX = re.compile(
    r"\bhow\s+much\s+(?:does|do|is|are|would|will)\b[^.!?\n]{0,60}?"
    r"\b(?:costs?|costing|charges?|charging)\b",
    re.IGNORECASE,
)
_SENTENCE_BREAK_RX = re.compile(r"[.!?\n]")
#: A web search said in so many words, read with the apps' own names blanked
#: (so "Google" in "Google Drive" never counts): "search online for …",
#: "google stripe fees", a message that opens with "research …".
_WEB_SEARCH_RX = re.compile(
    r"\bgoogle\b|\bonline\b|\binternet\b|\bweb\s+search\b"
    r"|\bthe\s+web\b(?!\s*(?:pages?|sites?|apps?|address(?:es)?|links?)\b)"
    r"|(?:\A|[.!?\n]\s*)research\b",
    re.IGNORECASE,
)


def _clause_before(t: str, start: int) -> str:
    """The words before ``t[start]`` back to the last clause break (the
    autoselect web rule's own break: punctuation, "and", "then", …)."""
    from ..tools.autoselect import _CLAUSE_BREAK_RX

    head = t[max(0, start - _CLAUSE_REACH):start]
    last = None
    for last in _CLAUSE_BREAK_RX.finditer(head):
        pass
    return head[last.end():] if last is not None else head


#: v1.360.0 (lane 10B, the final re-audit's G41 regression): the verbs that
#: ACT on what is in an app — search it, open it, list, read, save to, post
#: to, pull from, refund in. A sentence that opens with one and has the app
#: as its object ("search github for …", "show me the Slack messages") or as
#: its place ("save it to Notion", "pull the invoices from Stripe") is about
#: the user's own data in it. Writing verbs are left out on purpose: "write a
#: post about our GitHub launch" is a topic, not the user's GitHub.
_ACT_VERB = (
    r"(?:search(?:\s+through)?|find|check|look\s+(?:up|in|into|through|at|for)|go\s+through"
    r"|browse|open|list|show|read|get|fetch|pull(?:\s+up)?|grab|upload|save|send|post|share"
    r"|add|put|delete|remove|move|copy|download|sync|archive|update|edit|rename|star|label"
    r"|store|back\s+up|export|import|query|summari[sz]e|scan|sort|organi[sz]e|clean\s+up"
    r"|clear|empty|reply\s+to|forward|charge|refund|message|ping|notify|track|assign|close"
    r"|merge|review|comment\s+on|attach|create|make|file|raise|start|book|invite)"
)
#: The verb OPENS the app's clause (after a polite or "can you" lead-in).
_ACT_HEAD_RX = re.compile(
    r"\A\s*(?:(?:please|now|ok(?:ay)?|hey|jarvis|so)[\s,]+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need)\s+(?:you\s+)?to\s+"
    r"|go\s+ahead\s+and\s+|help\s+me\s+)?"
    + _ACT_VERB + r"\b",
    re.IGNORECASE,
)
#: Nothing but "me", "all (of) the", a determiner and a freshness word
#: between that verb and the app: the app is the verb's own object.
_OBJECT_GAP_RX = re.compile(
    r"(?:\s+(?:me|us))?\s+(?:all\s+(?:of\s+)?)?(?:(?:the|my|our|this|that)\s+)?"
    r"(?:(?:new|latest|recent|last|open|unread|old|today['’]?s)\s+)?",
    re.IGNORECASE,
)
#: A clear place IN the app ("the folder in Dropbox", "the refund in
#: Stripe", "how much space is left in Dropbox") — the user's own, by itself.
_IN_APP_RX = re.compile(r"\b(?:in|inside|within)\s+(?:the\s+|my\s+|our\s+)?\Z", re.IGNORECASE)
#: A place the data goes to or comes from ("to Notion", "from Stripe", "on
#: GitHub") — the user's own only beside an acting verb (:data:`_ACT_HEAD_RX`):
#: "alternatives to Slack" and "an article on Stripe" are not.
_TO_FROM_APP_RX = re.compile(
    r"\b(?:to|into|onto|from|on|through|across|via)\s+(?:the\s+|my\s+|our\s+)?\Z", re.IGNORECASE
)
#: A piece of writing ABOUT the app, right before it ("a LinkedIn post about
#: our GitHub launch", "a blog post on Notion") — a topic, never the user's
#: data in the app, a possessive included.
_TOPIC_RX = re.compile(
    r"\b(?:posts?|articles?|blogs?|essays?|papers?|pieces?|talks?|lectures?|courses?|books?"
    r"|reports?|presentations?|slides?|decks?|thoughts?|opinions?|takes?|tips|guides?"
    r"|tutorials?|videos?|podcasts?|tweets?|announcements?|newsletters?|press\s+releases?"
    r"|stor(?:y|ies)|poems?|songs?|jokes?|captions?|bios?|summary|overview)\s+"
    r"(?:about|on|regarding|announcing|celebrating|introducing)\s+(?:(?:my|our|the)\s+)?\Z",
    re.IGNORECASE,
)
#: A request to explain the app or how something in it works ("explain how
#: postgres indexes work", "teach me postgres joins", "describe Notion") —
#: research, unless the user's own data is said first ("explain my Stripe
#: payouts").
_EXPLAIN_RX = re.compile(
    r"\A\s*(?:(?:please|can\s+you|could\s+you)\s+)?(?:explain|describe|teach\s+me|define"
    r"|tell\s+me\s+(?:about|how|what|why|more))\b"
    r"|\bhow\s+(?:[\w'’-]+\s+){1,4}works?\b",
    re.IGNORECASE,
)
#: Something the user keeps in an app, right after its name ("Dropbox
#: files", "the Slack messages", "Sentry errors", "Notion templates"). An app
#: name followed by any OTHER word is a modifier of that word ("our GitHub
#: launch", "our Notion launch party"), never the app itself.
_DATA_NOUN_RX = re.compile(
    r"\A(?:['’]s)?\s+(?:accounts?|inbox(?:es)?|mailbox(?:es)?|mail|e-?mails?|messages?|threads?"
    r"|chats?|dms?|channels?|workspaces?|files?|folders?|docs?|documents?|pages?|notes?"
    r"|notebooks?|databases?|dbs?|tables?|rows?|records?|entries|repos?|repositories|projects?"
    r"|issues?|pull\s+requests?|prs?|branches|commits?|invoices?|payments?|payouts?|charges?"
    r"|customers?|subscriptions?|refunds?|transactions?|balance|events?|meetings?|appointments?"
    r"|calendar|contacts?|tasks?|boards?|cards?|errors?|alerts?|logs?|photos?|pictures?"
    r"|images?|scans?|uploads?|attachments?|storage|space|data|drive|templates?|wiki"
    # v1.360.0 re-review: what an app shows the user about their own account
    # ("check my Stripe dashboard", "my GitHub notifications").
    r"|bookmarks?|history|settings|dashboards?|notifications?|mentions?)\b",
    re.IGNORECASE,
)
#: What may follow "my <app>" when the app ITSELF is what the user owns ("my
#: Dropbox?", "my gmail for the invoice", "my Google Drive connected?",
#: "check my gmail online"): punctuation, the end, or one of these words.
_PHRASE_END_RX = re.compile(
    r"\A(?:['’]s)?\s*(?:\Z|[^\w\s]|(?:for|in|to|from|with|on|at|of|about|into|by|under|over"
    r"|since|after|before|and|or|but|so|then|nor|if|when|while|because|than|as|is|are|was"
    r"|were|has|have|had|does|do|did|can|could|will|would|should|might|must|isn['’]t"
    r"|aren['’]t|doesn['’]t|don['’]t|hasn['’]t|haven['’]t|won['’]t|wasn['’]t|can['’]t|keeps?"
    r"|says?|shows?|connected|linked|set|signed|hooked|working|online|now|today|tomorrow"
    r"|yesterday|again|too|please|right|first|later|here|there|still|yet|already|anymore"
    r"|properly|correctly|that|which|where|who|this|it|me|us)\b)",
    re.IGNORECASE,
)
#: The app itself is asked about being connected ("is github connected?",
#: "Slack isn't linked"). v1.360.0 review (lane 10B): a state word said
#: straight after the app counts only as a question or a claim about the
#: connection — "is" / "has been" before the state word, "is" (or "has",
#: "did", …) right before the app, or nothing after it but the end, a
#: question mark or a time word ("github connected?", "slack set up yet") —
#: never a news sentence ("Slack set up a new pricing tier", "GitHub
#: connected millions of developers"), which offered the door.
_STATE_WORDS = r"(?:connected|linked|hooked\s+up|signed\s+in|set\s+up)\b"
_STATE_AFTER_RX = re.compile(
    r"\A\s+(?:is|isn['’]t|was|wasn['’]t|still|not"
    r"|(?:(?:has|hasn['’]t|have|haven['’]t)\s+)?been)\s+(?:still\s+|not\s+)?" + _STATE_WORDS,
    re.IGNORECASE,
)
#: The state word straight after the app ("github connected …"), or after
#: "get"/"got" (v1.360.0 fix round: "did slack get connected?").
_STATE_GOT = r"(?:(?:get|got|gotten|getting)\s+)?"
_STATE_BARE_RX = re.compile(r"\A\s+" + _STATE_GOT + _STATE_WORDS, re.IGNORECASE)
#: ... and nothing after it but the end, punctuation or a time word.
_STATE_BARE_END_RX = re.compile(
    r"\A\s+" + _STATE_GOT + _STATE_WORDS + r"\s*(?:\Z|[?.!,;:]|(?:yet|now|already|properly"
    r"|correctly|right|anymore|again|still)\b)",
    re.IGNORECASE,
)
#: ... or the app is asked about ("is github connected", "has my Slack …").
_STATE_ASKED_RX = re.compile(
    r"\b(?:is|isn['’]t|was|wasn['’]t|are|aren['’]t|has|hasn['’]t|have|haven['’]t|did"
    r"|didn['’]t)\s+(?:my\s+|our\s+|the\s+)?\Z",
    re.IGNORECASE,
)
#: A question about what is in the app ("any new Sentry errors today?",
#: "which Notion pages mention Q3").
_ASKS_IN_RX = re.compile(
    r"\?|\b(?:any|which|what|how\s+many|did\s+i|do\s+i|have\s+i|are\s+there|is\s+there)\b",
    re.IGNORECASE,
)


def _acts_on_app(clause: str, after: str) -> bool:
    """Does *clause* (the words before the app) open with an acting verb
    whose object is the app itself ("search ", "show me the ", "summarize
    my ")? Pure.

    v1.360.0 re-review: the object is the app ITSELF only when nothing but
    the end, punctuation or a joining word follows it ("search github for
    issues", "open Notion") or a thing kept in it does ("show me the Slack
    messages") — the possessive rule's own test. An app name followed by any
    other word is a modifier of that word, so "remove the GitHub link from
    the footer", "update the Slack logo", "review the Postgres migration"
    and "share our GitHub launch post" are about a link, a logo, a migration
    and a post, never the user's data in the app (each offered its Connect
    door)."""
    m = _ACT_HEAD_RX.match(clause)
    if m is None or _OBJECT_GAP_RX.fullmatch(clause[m.end():]) is None:
        return False
    return _PHRASE_END_RX.match(after) is not None or _DATA_NOUN_RX.match(after) is not None


def _for_own_data(t: str, start: int, end: int, blanked: str) -> bool:
    """Is the app named at ``t[start:end]`` named for the user's OWN data in
    it (or to connect it), rather than in research ABOUT it — or not about
    the app at all ("stripe the table rows")?

    v1.360.0 (lane 10B, the final re-audit's G41 regression): the default is
    NOT OWN. v1.359.0 read anything that was not research as the user's own
    data, so "stripe the table rows" offered "Connect Stripe", "explain how
    postgres indexes work" offered Postgres, "the sentry stood guard at the
    gate in my story" offered Sentry and "Write a LinkedIn post about our
    GitHub launch" offered GitHub. Now the door needs a signal that the
    message is about the user's own data in that app:

    * a connect / set-up verb before it (or in a generic app's own match),
      or the app asked about as connected ("is github connected?") → own;
    * a how-to question, "what is <app>", or a piece of writing about it
      ("a post about our GitHub launch") → not own;
    * an app word that is the user's own ("my inbox", "my calendar") → own;
      "my <app>" / "our <app>" → own when the app ITSELF is what they own
      ("my Dropbox?", "my gmail for the invoice") or a thing they keep in it
      follows ("our Slack channel") — never when the app only qualifies
      another word ("our GitHub launch");
    * the app's public things after its name ("the Stripe API docs"),
      research words before it ("reviews of Notion"), a comparison or a
      price question, a request to explain ("explain how postgres indexes
      work"), or a web search said in so many words → not own;
    * a clear place in the app ("the folder in Dropbox", "the refund in
      Stripe") → own;
    * an acting verb ("search", "show me", "save", "pull") with the app as
      its object ("search github for issues") or as the place the data goes
      to or comes from ("save it to Notion", "pull the invoices from
      Stripe") → own;
    * a thing kept in the app right after its name, asked about or acted on
      ("any new Sentry errors today?") → own;
    * anything else → not own: no door.

    The web rule's own readings (``tools.autoselect._searches_in_the_app``,
    v1.358.0 — its possessive, own-word and public-things regexes) are
    imported, never copied. Pure."""
    from ..tools.autoselect import (
        _OWN_APP_WORDS_RX,
        _OWN_BEFORE_APP_RX,
        _RESEARCH_AFTER_APP_RX,
    )

    hit = t[start:end]
    after = t[end:end + 120]
    clause = _clause_before(t, start)
    if _CONNECT_VERB_RX.match(hit.lstrip()) or _CONNECT_VERB_RX.search(clause):
        return True
    if _STATE_AFTER_RX.match(after) or (
        _STATE_BARE_RX.match(after)
        and (_STATE_BARE_END_RX.match(after) or _STATE_ASKED_RX.search(clause))
    ):
        return True
    if _HOW_TO_RX.search(clause) or _WHAT_IS_RX.search(clause) or _TOPIC_RX.search(clause):
        return False
    if _OWN_APP_WORDS_RX.match(hit):
        return True
    if _OWN_BEFORE_APP_RX.search(clause) and (
        _PHRASE_END_RX.match(after) or _DATA_NOUN_RX.match(after)
    ):
        return True
    if _RESEARCH_AFTER_APP_RX.match(after) is not None:
        return False
    if _RESEARCH_BEFORE_RX.search(clause):
        return False
    sentence = _sentence_of(t, start, end)
    if _COMPARE_RX.search(sentence) or _PRICE_RX.search(sentence) or _EXPLAIN_RX.search(sentence):
        return False
    if _WEB_SEARCH_RX.search(blanked) is not None:
        return False
    if _IN_APP_RX.search(clause):
        return True
    acting = _ACT_HEAD_RX.match(clause) is not None
    if acting and _TO_FROM_APP_RX.search(clause):
        return True
    if _acts_on_app(clause, after):
        return True
    if _DATA_NOUN_RX.match(after) and (acting or _ASKS_IN_RX.search(sentence)):
        return True
    return False


def _sentence_of(t: str, start: int, end: int) -> str:
    """The sentence ``t[start:end]`` sits in (bounded to 200 characters each
    way)."""
    head = t[max(0, start - 200):start]
    last = None
    for last in _SENTENCE_BREAK_RX.finditer(head):
        pass
    tail = t[end:end + 200]
    stop = _SENTENCE_BREAK_RX.search(tail)
    return (head[last.end():] if last is not None else head) + t[start:end] + (
        tail[: stop.start()] if stop is not None else tail
    )


def own_data_apps(text: str) -> list[str]:
    """The catalog ids *text* names for the user's own data in them (or to
    connect them), in catalog order — research about an app is left out
    (:func:`_for_own_data`). The same matcher as :func:`apps_named`. Never
    raises; a fault names nothing."""
    t = str(text or "")[:_MAX_TEXT]
    try:
        hits = app_name_hits(t)
        if not hits:
            return []
        chars = list(t)
        for _cid, start, end in hits:
            for i in range(start, min(end, len(chars))):
                chars[i] = " "
        blanked = "".join(chars)
        out: list[str] = []
        for cid, start, end in hits:
            if cid not in out and _for_own_data(t, start, end, blanked):
                out.append(cid)
        return out
    except Exception:  # noqa: BLE001 — no door beats a broken turn
        return []


def connect_offers(text: str, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The apps whose Connect door the reply carries, whatever the model does
    (v1.359.0, G41): ``[{id, name}]`` for each row :func:`app_states` read as
    ``not_connected`` that *text* names for the user's own data in it (or to
    connect it) — never one named only in research about it
    (:func:`own_data_apps`). An app that is saved but off, not working or not
    signed in yet is not "not connected": its card says what to do. Rows
    order (catalog order). Never raises."""
    try:
        cands = [
            r for r in rows or []
            if isinstance(r, dict) and r.get("state") == "not_connected"
            and r.get("id") and r.get("id") not in CLI_WORDS
            # v1.360.0 (lane 10B): a kind said but never offered ("my
            # calendar" — its only card is not what the words named).
            and r.get("offer", True) is not False
        ]
        if not cands:
            return []
        own = set(own_data_apps(text))
        out: list[dict[str, Any]] = []
        for r in cands:
            if r["id"] not in own:
                continue
            offer: dict[str, Any] = {"id": str(r["id"]), "name": str(r.get("name") or r["id"])}
            if r.get("ids"):
                # A CHOICE (v1.360.0): every app the door names, so an
                # app_connect card for any one of them is the offer.
                offer["ids"] = [str(i) for i in r["ids"]]
            out.append(offer)
        return out
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# Status — ON the loop (in-memory) and OFF it (blocking), never raising.
# --------------------------------------------------------------------------- #
def live_tools(platform: Any, app_ids: list[str]) -> dict[str, list[str]]:
    """``{catalog id: its loaded tool names}`` for each named MCP app that is
    configured — in-memory reads only (the config list + the registry), so
    this runs ON the loop. An app with no tools loaded maps to ``[]``; an app
    that is not configured (or not an MCP app) is absent. Never raises."""
    out: dict[str, list[str]] = {}
    try:
        from .catalog import get_connector
        from .service import _configured_name
    except Exception:  # noqa: BLE001
        return out
    for cid in app_ids:
        try:
            conn = get_connector(cid)
            if conn is None or conn.connect_via != "mcp":
                continue
            server = _configured_name(platform, cid)
            if not server:
                continue
            out[cid] = list(platform.registry.mcp_names(server))
        except Exception:  # noqa: BLE001 — one app's fault names nothing
            continue
    return out


#: v1.360.0 (lane 10B): what a CHOICE row is called in the apps section.
_KIND_WORDS: dict[str, str] = {"mail": "Email", "calendar": "Calendar"}


def _fold_choices(rows: list[dict[str, Any]], choices: "dict[str, list[str]] | None") -> list[dict[str, Any]]:
    """Fold the rows of each kind left as a choice (:func:`resolve_kinds`)
    into ONE row (v1.360.0, lane 10B): ``{id, ids, kind, choice: True,
    offer, name, state: "not_connected", …}``. Mail names every mail app of
    the catalog once ("Gmail or Microsoft 365 (…)"), offered as one door;
    the calendar is said and never offered (its only card is Microsoft 365,
    which "my calendar" does not name). Never raises (rows come back as they
    were)."""
    if not choices:
        return rows
    try:
        from .catalog import get_connector

        out = list(rows)
        for kind, apps in choices.items():
            ids = [str(a) for a in apps or [] if a]
            if not ids:
                continue
            at = next((i for i, r in enumerate(out) if r.get("id") in ids), len(out))
            out = [r for r in out if r.get("id") not in ids]
            names = []
            for cid in ids:
                conn = get_connector(cid)
                names.append(str(conn.name) if conn is not None else cid)
            offer = kind != "calendar"
            row = {
                "id": ids[0], "ids": ids, "kind": kind, "choice": True, "offer": offer,
                "name": " or ".join(names) if offer else _KIND_WORDS.get(kind, kind),
                "state": "not_connected", "tools": 0, "needs": "", "note": "",
            }
            out.insert(min(at, len(out)), row)
        return out
    except Exception:  # noqa: BLE001 — rows as they were beat no rows
        return rows


def app_states(
    platform: Any,
    app_ids: list[str],
    live: dict[str, list[str]],
    clis: list[str] | None = None,
    choices: "dict[str, list[str]] | None" = None,
) -> list[dict[str, Any]]:
    """One row per named app (and named CLI): ``{id, name, state, tools,
    needs, note}`` where ``state`` is ``connected`` / ``not_working`` /
    ``off`` (v1.356.0: saved, but the user switched it off) / ``signin``
    (v1.358.0: saved and loaded, its sign-in on this PC unfinished) /
    ``not_connected`` (CLIs: ``signed_in`` / ``signed_out`` /
    ``not_installed`` / ``unknown``). BLOCKING (the connections table, the
    memory bases): call it OFF the loop. Never raises; a row it cannot read
    is left out rather than guessed.

    *choices* (v1.360.0, lane 10B): the kinds :func:`resolve_kinds` left as a
    choice — their apps' rows are folded into ONE row each
    (:func:`_fold_choices`), so "check my inbox" with no mail app says "no
    mail app is connected" once, never "Gmail: not connected"."""
    from .catalog import get_connector

    rows: list[dict[str, Any]] = []
    oauth_rows: "dict[str, dict] | None" = None

    def _oauth(provider: str) -> dict:
        nonlocal oauth_rows
        if oauth_rows is None:
            try:
                oauth_rows = {
                    str(r.get("provider")): r for r in (platform.connections.status() or [])
                }
            except Exception:  # noqa: BLE001 — unknown, not "none"
                oauth_rows = {}
        return oauth_rows.get(provider) or {}

    for cid in app_ids:
        try:
            conn = get_connector(cid)
            if conn is None:
                continue
            row: dict[str, Any] = {
                "id": cid, "name": conn.name, "state": "not_connected", "tools": 0,
                "needs": "", "note": "",
            }
            if conn.connect_via == "mcp":
                if cid in live and _switched_off(platform, cid):
                    # v1.356.0 integrate (G78): saved with its key, its tools
                    # not loaded on purpose — never "set up, but not working".
                    row["state"] = "off"
                elif cid in live:
                    n = len(live[cid])
                    row["tools"] = n
                    row["state"] = "connected" if n else "not_working"
                    if not n:
                        row["note"] = _load_reason(platform, cid)
                    else:
                        # v1.358.0 (integrate, lane 8D review): an app that
                        # signs in on this PC reads as its Directory card
                        # does — "Sign in to Microsoft to finish" — until its
                        # own check says signed in; never "connected" here
                        # while the card says otherwise.
                        unfinished = _signin_unfinished(platform, conn, cid)
                        if unfinished:
                            row["state"] = "signin"
                            row["note"] = unfinished
                else:
                    row["needs"] = conn.needs
            else:
                st = _oauth(conn.provider)
                if st.get("connected"):
                    row["state"] = "connected"
                    if conn.connect_via == "oauth" and _memory_base_missing(platform, conn.provider):
                        row["note"] = (
                            "its files reach chat through a memory base, and none points"
                            " at it yet (the Memory page adds one)"
                        )
                elif conn.connect_via == "oauth" and not st.get("oauth_client_configured"):
                    row["needs"] = conn.needs
            rows.append(row)
        except Exception:  # noqa: BLE001 — one app's fault drops only its row
            continue
    rows = _fold_choices(rows, choices)
    for cli in clis or []:
        try:
            st = platform.providers.cli_login_status(cli) or {}
            if not st.get("installed"):
                state = "not_installed"
            elif st.get("signed_in") is True:
                state = "signed_in"
            elif st.get("signed_in") is False:
                state = "signed_out"
            else:
                state = "unknown"
            from ..providers.failure_words import plain_name

            rows.append({"id": cli, "name": plain_name(cli), "state": state, "tools": 0,
                         "needs": "", "note": ""})
        except Exception:  # noqa: BLE001
            continue
    return rows


def _switched_off(platform: Any, cid: str) -> bool:
    """True when the app's saved pack row is switched OFF (v1.356.0, G78) —
    the loader's own reading (``connectors.service.pack_on``). Any fault reads
    as on (the older answer)."""
    try:
        from .service import _configured_name, _server_cfg, pack_on

        cfg = _server_cfg(platform, _configured_name(platform, cid) or cid)
        return cfg is not None and not pack_on(cfg)
    except Exception:  # noqa: BLE001
        return False


def _signin_unfinished(platform: Any, conn: Any, cid: str) -> str:
    """The card's own line ("Sign in to Microsoft to finish.") while an app
    that signs in on this PC has not; "" once signed in, or for any other
    app (v1.358.0, integrate). The Directory's reading
    (``connectors.service._signin_state``): ``signin_seen.seen`` is cached
    and never blocks; an unsure answer is "not signed in yet"."""
    if not getattr(conn, "signin_args", None):
        return ""
    try:
        from . import signin_seen
        from .service import _configured_name, _server_cfg, signin_identity, signin_needed_line
    except Exception:  # noqa: BLE001
        return ""
    try:
        server = _configured_name(platform, cid) or cid
        # v1.358.0 re-review: the FIRST read after a restart had no verdict
        # and told the model "not signed in yet" for a user who is signed
        # in; this reader is already off the loop, so it waits (bounded) for
        # the check that read starts. A still-unknown answer reads as before.
        verdict = signin_seen.seen_settled(
            platform, server, signin_identity(_server_cfg(platform, server))
        )
    except Exception:  # noqa: BLE001 — unsure is "not signed in yet"
        verdict = None
    if verdict is not None and getattr(verdict, "signed_in", False):
        return ""
    try:
        return signin_needed_line(conn)
    except Exception:  # noqa: BLE001
        return ""


def _load_reason(platform: Any, cid: str) -> str:
    """The pack's own load record, in a few words ("" when none)."""
    try:
        from ..mcp.tools import load_status
        from .service import _configured_name

        rec = dict(load_status(_configured_name(platform, cid) or cid) or {})
        state = str(rec.get("state") or "")
        if state == "starting":
            return "it is still starting"
        reason = " ".join(str(rec.get("reason") or "").split())
        return reason[:160]
    except Exception:  # noqa: BLE001
        return ""


def _memory_base_missing(platform: Any, provider: str) -> bool:
    """True only when the memory bases were READ and none is this drive."""
    try:
        from .service import _CLOUD_DRIVE_KINDS, _memory_kinds

        if provider not in _CLOUD_DRIVE_KINDS:
            return False
        kinds = _memory_kinds(platform)
        return kinds is not None and provider not in kinds
    except Exception:  # noqa: BLE001
        return False


#: Where an app is connected: the Directory (its nav name since v1.313.0),
#: which lives in Settings' Connections group.
APPS_PLACE = "the Directory in Settings › Connections"


def _needs_words(needs: str) -> str:
    """"Needs a one-time Google developer setup" → "It needs a one-time
    Google developer setup first."""
    n = " ".join(str(needs or "").split()).rstrip(". ")
    if not n:
        return ""
    if n.lower().startswith("needs "):
        n = "It needs " + n[6:]
    return f" {n} first."


def connected_ids(rows: list[dict[str, Any]]) -> list[str]:
    """The catalog ids of the rows :func:`app_states` read as ``connected``
    (v1.358.0): the apps a search may be taken over by (the web rule's
    ``apps_on``). CLI rows are never apps. Never raises."""
    try:
        return [
            str(r.get("id")) for r in rows or []
            if r.get("state") == "connected" and r.get("id") and r.get("id") not in CLI_WORDS
        ]
    except Exception:  # noqa: BLE001
        return []


def apps_section(rows: list[dict[str, Any]]) -> str:
    """The grounding section for the named apps ("" when none). Plain facts
    the model must not contradict, plus what to do for an app that is not
    connected — say so and offer to connect it, never search the web for
    what is inside it. v1.358.0 (the re-audit's regression): a question ABOUT
    the app itself (its docs, pricing, how to use it) is ordinary web
    research, so the line no longer forbids the web outright. Never raises."""
    if not rows:
        return ""
    lines: list[str] = []
    try:
        for r in rows:
            name = str(r.get("name") or r.get("id") or "")
            state = r.get("state")
            if r.get("choice") and r.get("kind") == "mail":
                # v1.360.0 (lane 10B): "check my inbox" with no mail app
                # saved — every mail app of the catalog, once, never Gmail
                # alone (an Outlook user was told "Gmail: not connected").
                line = (
                    f"- Email: no mail app is connected ({name} can be). If answering"
                    " needs what is in the user's own mail, tell the user plainly and"
                    " offer to connect the one they use (the app_connect tool does that"
                    f" when it is offered; otherwise {APPS_PLACE}). Never search the web"
                    " for what is in it. If answering needs neither, say nothing about it."
                )
                lines.append(line)
                continue
            if r.get("choice"):
                # v1.360.0 (lane 10B): "my calendar" with no calendar app
                # saved. Said, never offered: the only calendar card is
                # Microsoft 365, and "my calendar" does not say Outlook.
                line = (
                    f"- {_KIND_WORDS.get(str(r.get('kind') or ''), name)}: no"
                    f" {str(r.get('kind') or 'such')} app is connected. If answering needs"
                    f" the user's own {str(r.get('kind') or 'one')}, say so plainly and point"
                    f" to {APPS_PLACE}, which lists the apps that can be connected; never"
                    " guess which app they use and never search the web for it. If"
                    " answering does not need it, say nothing about it."
                )
                lines.append(line)
                continue
            if state == "connected":
                n = int(r.get("tools") or 0)
                line = f"- {name}: connected" + (
                    f" ({n} {'tool' if n == 1 else 'tools'})." if n else "."
                )
                if r.get("note"):
                    line = line.rstrip(".") + f"; {r['note']}."
            elif state == "not_working":
                why = f" ({r['note']})" if r.get("note") else ""
                # v1.354.0 re-review: said only when answering NEEDS the app
                # — a matched word is a hint, not the request ("rewrite my
                # email below" needs no Gmail), so the model must not open
                # every such reply with "it is not connected".
                line = (
                    f"- {name}: set up, but not working right now{why}. If answering"
                    f" needs {name}, say so and point to {APPS_PLACE} to fix it."
                )
            elif state == "signin":
                # v1.358.0 (integrate): saved and loaded, sign-in unfinished —
                # the Directory card's own words and press.
                why = str(r.get("note") or "").strip()
                line = (
                    f"- {name}: saved, but not signed in yet" + (f" ({why.rstrip('.')})" if why else "")
                    + f". If answering needs {name}, say so and point to its Sign in"
                    f" button on its card in {APPS_PLACE}."
                )
            elif state == "off":
                line = (
                    f"- {name}: switched off by the user (kept with its key; its"
                    f" tools are not loaded). If answering needs {name}, say so and"
                    " point to Settings › Tools or its card in"
                    f" {APPS_PLACE} to turn it on. Never ask for its key again."
                )
            elif state == "not_connected":
                # v1.358.0: "Never search the web in its place" read as "never
                # search the web" to a model asked "look up the Stripe API docs"
                # — it offered to connect Stripe instead of reading the docs.
                # What the web cannot do is reach what is INSIDE the user's
                # app; a question about the app itself is web research.
                line = (
                    f"- {name}: not connected.{_needs_words(r.get('needs', ''))} If"
                    f" answering needs what is in the user's own {name}, tell the"
                    " user plainly and offer to connect it (the app_connect tool"
                    f" does that when it is offered; otherwise {APPS_PLACE}). Never"
                    " search the web for what is in it. A question about"
                    f" {name} itself (its docs, pricing or how to use it) may be"
                    " answered from the web. If answering needs neither, say"
                    " nothing about it."
                )
            elif state == "signed_in":
                line = f"- {name}: signed in."
            elif state == "signed_out":
                line = (
                    f"- {name}: installed but not signed in (its Sign in button is in"
                    " Settings › Connections)."
                )
            elif state == "not_installed":
                line = f"- {name}: not installed on this PC."
            else:
                line = f"- {name}: sign-in not checked yet."
            lines.append(line)
    except Exception:  # noqa: BLE001 — no section beats a broken one
        return ""
    return (
        "\n\n# Apps named in this message\n"
        "What is connected right now (checked when this message arrived):\n"
        + "\n".join(lines)
    )
