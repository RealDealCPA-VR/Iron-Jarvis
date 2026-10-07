"""Is this message a CORRECTION of how the assistant answers? (v1.305.0)

Pure, deterministic, offline — no model call, no I/O. Idea from
agent-personalizer (MIT, Auny LLC): when the user corrects the SAME thing a
second time, offer to keep it as a standing preference instead of waiting for
them to say "from now on". The detector is CONSERVATIVE on purpose: a false
positive costs the user's attention (a question under a reply they did not
ask for), a false negative costs one more "shorter please".

Three functions:

* :func:`is_correction` — a SHORT user message (<= 280 chars) that both
  FRAMES a correction ("don't", "stop", "too long", "I said", "you keep",
  "instead", "shorter", "plain words" …) AND names a STYLE target (length,
  tables, bullets, emoji, headings, jargon, apologies, filler, asking first,
  tone, language …). A task request ("write a shorter email"), a question
  ("which route is shorter?"), a stop/cancel ("stop the job"), a correction
  of WHAT ("no, the other file") and anything code-shaped are not.
* :func:`signature` — a normalised key: lower-case, synonyms folded to one
  canonical token ("too long" / "shorten" / "be brief" -> ``short``),
  negation kept as ``not`` unless the canonical token already carries its
  direction, politeness and stop words dropped, light stemming, sorted.
* :func:`similar` — token Jaccard >= :data:`SIMILAR_AT` on two signatures,
  same polarity, at least one shared content token.

:func:`proposal_text` turns the user's own quotes into the sentence the card
offers (the user can edit it before keeping it).
"""

from __future__ import annotations

import re

#: Longest message the detector looks at.
MAX_CHARS = 280
#: Signatures this similar (token Jaccard) are the same correction.
SIMILAR_AT = 0.5

_NEG_WORDS = (
    r"(?:don'?t|do not|dont|stop|never|no more|quit|avoid|without|enough with|"
    r"cut out|cut the|lose the|drop the|ditch the|skip the|get rid of|less|fewer)"
)

# --------------------------------------------------------------------------- #
# what a correction is ABOUT — the style vocabulary
# --------------------------------------------------------------------------- #

#: Style targets: HOW an answer is written or how the assistant works.
_TARGET = re.compile(
    r"\b(?:"
    r"shorter|shorten\w*|brief(?:er|ly)?|concise\w*|terse\w*|succinct\w*|"
    r"short (?:answers|replies|responses|and sweet)|(?:keep (?:it|them|things)|be) short|"
    r"longer|too long|so long|long-winded|verbose|wordy|rambl\w*|waffl\w*|fluff\w*|filler|"
    r"more detail(?:s|ed)?|less detail|elaborate|to the point|to the chase|"
    r"tables?|bullets?|bullet(?:ed)? points?|bulleted|lists|numbered lists?|"
    r"emojis?|emojies|emoticons?|markdown|formatting|headers?|headings?|"
    r"bold(?:ing)?|italics?|code blocks?|all caps|capital letters|exclamation (?:marks?|points?)|"
    r"em ?dash(?:es)?|dashes|prose|paragraphs?|"
    r"jargon|technical terms|acronyms|big words|fancy words|"
    r"(?:plain|simple|normal|everyday) (?:english|words|language|terms)|"
    r"apolog\w*|saying sorry|caveats?|disclaimers?|preambles?|"
    r"small talk|pleasantries|sign-?offs?|recaps?|summar(?:y|ies) at the end|"
    r"repeat(?:ing)? (?:my|the) question|great question|flatter\w*|compliments?|praise|"
    r"sycophan\w*|formal|casual|chatty|enthusiastic|cheerful|robotic|tone|"
    r"follow-?up questions?|clarifying questions?|"
    r"ask(?:ing)? (?:me )?(?:first|before|questions|for (?:confirmation|permission)|permission|if)|"
    r"check(?:ing)? with me|confirm(?:ation)? (?:first|before)|"
    r"explain(?:ing)? (?:everything|yourself|each step|every step|the steps|what you did)|"
    r"explanations?|"
    r"in (?:english|spanish|french|german|portuguese|italian)"
    r")\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# how a correction is FRAMED
# --------------------------------------------------------------------------- #

_FRAMES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # a negation at the start: "don't use tables", "stop apologizing",
        # "no more emoji", "never use bullet points", "less jargon"
        # ("stop, the tables are wrong" is an interjection, not a correction)
        r"^" + _NEG_WORDS + r"\b(?!\s*[,.!:;-])",
        # "no tables", "no emoji please", "no bullet points"
        r"^no (?:more )?(?:\w+ )?(?=\S)",
        # "too long", "way too formal", "this answer is too long"
        r"^(?:(?:it'?s|it is|that'?s|that is|this is|they'?re|they are|still|"
        r"way|far|much|a bit|a little|kind of|(?:your|the|this|that) "
        r"(?:answer|reply|response|answers|replies|responses) (?:is|are|was|were))\s+)*"
        r"(?:way |far |much |a bit |a little )?too (?:long|verbose|wordy|formal|casual|"
        r"technical|complicated|complex|chatty|robotic|much|many)\b",
        # "you keep using tables", "I said no emoji", "like I said …"
        r"\b(?:you keep|you always|you still|i said|i told you|i asked (?:you )?(?:for|to|not)|"
        r"like i said|as i said|i already said|how many times|once again)\b",
        r"^again\b",
        r"\binstead\b",
        # length / plainness comparatives in their correction shapes
        r"^(?:a (?:bit|little|lot) |much |way |even )?(?:shorter|briefer|simpler|"
        r"more concise|more brief|less verbose|less wordy|longer|more detail(?:ed|s)?)\b",
        r"\b(?:make|keep) (?:it|that|this|them|things|everything|your "
        r"(?:answers|replies|responses|messages)) (?:a (?:bit|little|lot) |much |way |even )?"
        r"(?:shorter|short|brief|briefer|concise|tighter|simpler|plain|longer|casual|formal)\b",
        r"\b(?:shorter|briefer|simpler|more concise|longer|more detailed) "
        r"(?:answers|replies|responses|messages|please|pls)\b",
        r"\b(?:be|keep it|keep them|keep things) (?:more |a bit more )?(?:brief|concise|short|"
        r"succinct|terse|direct|to the point|plain|simple)\b",
        r"\bshorten (?:it|that|this|them|your (?:answers|replies|responses))\b",
        r"\b(?:get|cut|skip) (?:straight )?to the (?:point|chase)\b",
        r"\b(?:in|use|using|with|speak|talk|write|answer) (?:plain|simple|normal|everyday) "
        r"(?:english|words|language|terms)\b",
        r"^(?:plain|simple) (?:english|words|language)\b",
        r"\bskip the (?:preamble|intro|introduction|small talk|pleasantries|fluff|filler|"
        r"recap|summary|caveats|disclaimers|explanation)\b",
        r"\b(?:answer|reply|respond|write|talk|speak) in (?:english|spanish|french|german|"
        r"portuguese|italian)\b",
    )
)

#: A task request / question / stop shape that is not a correction of style.
_TASK_START = re.compile(
    r"^(?:write|draft|create|generate|build|make (?:a|an|me|the|some)|translate|summari[sz]e|"
    r"find|search|look up|open|run|send|email|schedule|book|show|list|give me (?:a|an|the|some)|"
    r"tell me (?:about|a|an|how|what|why|when|where|who)|add|remove|delete|rename|move|copy|"
    r"fix|debug|explain (?:how|what|why|the|this|that)|compare|calculate|convert|check (?:the|my|if|whether)|"
    r"read|review|edit|update|format (?:the|this|my)|turn (?:this|that|it) into|put (?:this|that|it) in)\b",
    re.IGNORECASE,
)

#: Nouns that make the message about a THING being produced, not about how
#: the assistant talks ("make the email shorter" edits an email).
_ARTIFACT = re.compile(
    r"\b(?:file|files|folder|document|documents|doc|docs|docx|pdf|report|reports|email|emails|"
    r"e-mail|letter|memo|spreadsheet|sheet|workbook|slide|slides|deck|presentation|page|website|"
    r"essay|article|post|tweet|caption|bio|story|poem|song|script|speech|proposal|contract|"
    r"invoice|resume|cv|chapter|section|title|headline|function|method|class|variable|query|"
    r"prompt|ticket|issue|commit|branch|job|task|agent|server|meeting|call|flight|route|trip|"
    r"recipe|video|image|picture|photo|logo|chart|graph|diagram|column|row|cell|formula|"
    r"database|code)\b",
    re.IGNORECASE,
)

#: A correction the user SCOPED to this one case is not a standing preference
#: ("answer in spanish for this client", "too long for the client", "keep it
#: brief, I'm on my phone", "no markdown, this goes into outlook", "less
#: detail on depreciation, more on the credit"). Reviewer, v1.305.0.
_ONE_OFF = re.compile(
    r"\b(?:for (?:this|that|these|those)\b|this (?:time|once)\b|for now\b|just once\b|"
    r"for the client\b|on my phone\b|(?:this|it) goes (?:in|into|to)\b|"
    r"(?:more|less) (?:detail )?on (?:the |my |this )?\w+)",
    re.IGNORECASE,
)

#: Code / path / link shapes.
_CODE = re.compile(r"```|`|\bdef \w+\(|\bfunction\s*\(|=>|[{};]\s*$|https?://|\w:\\|/\w+/\w+|\.\w{2,4}\b(?=\s|$)")

#: Leading politeness / address the detector looks past.
_LEAD = re.compile(
    r"^(?:(?:(?:hey|hi|ok|okay|ugh|come on|seriously|dude|jarvis|please|pls|plz|kindly|"
    r"also|and|but|so|honestly)\b[\s,.!:;-]*)|(?:(?:no|nope|nah)\s*[,.!:;-]+\s*))+",
    re.IGNORECASE,
)
_ASK_LEAD = re.compile(r"^(?:can|could|would|will) you (?:please |pls )?", re.IGNORECASE)
_TRAIL = re.compile(r"(?:[\s,.!]*(?:please|pls|plz|thanks|thank you|thx|ok|okay|again))+[\s.!?]*$", re.IGNORECASE)


def _strip_lead(text: str) -> str:
    """Drop leading politeness / interjections ("no, …", "please …", "again, …")."""
    out = text.strip()
    for _ in range(4):
        new = _LEAD.sub("", out, count=1).strip()
        if new == out:
            break
        out = new
    return out


def _core(text: str) -> tuple[str, bool]:
    """The message with politeness stripped, and whether it was a polite ask
    ("can you … ?")."""
    raw = " ".join((text or "").split())
    body = _strip_lead(raw)
    asked = False
    m = _ASK_LEAD.match(body)
    if m:
        asked = True
        body = body[m.end():]
    body = _TRAIL.sub("", body).strip()
    body = body.rstrip(" .!?").strip()
    return body, asked


def is_correction(text: str) -> bool:
    """True when ``text`` is a short message correcting HOW the assistant
    answers or works. Conservative; never raises."""
    try:
        raw = str(text or "")
        if not raw.strip() or len(raw) > MAX_CHARS:
            return False
        if raw.count("\n") > 2 or _CODE.search(raw):
            return False
        flat = " ".join(raw.split())
        body, asked = _core(flat)
        if not body or len(body) < 4:
            return False
        if flat.rstrip().endswith("?") and not asked:
            return False  # a question, not a correction ("which one is shorter?")
        if _TASK_START.match(body):
            return False
        if _ARTIFACT.search(body):
            return False
        if _ONE_OFF.search(body):
            return False
        if not _TARGET.search(body):
            return False
        return any(frame.search(body) for frame in _FRAMES)
    except Exception:  # noqa: BLE001 — a detector never breaks a turn
        return False



# --------------------------------------------------------------------------- #
# signatures
# --------------------------------------------------------------------------- #

#: Multi-word / synonym folds, applied in order on the lower-cased text.
#: Direction-bearing tokens (``short``, ``long``, ``plain``) absorb negation:
#: "stop rambling" and "be brief" both want ``short``.
_CANON: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), tok)
    for p, tok in (
        (r"\b(?:way |far |much |a bit |a little )?too (?:long|verbose|wordy|much text)\b", " short "),
        (r"\b(?:long-winded|verbose|wordy|rambl\w*|waffl\w*)\b", " short "),
        (r"\b(?:less (?:verbose|wordy|text)|fewer words|to the point|to the chase|tl;?dr)\b", " short "),
        (r"\b(?:short(?:er)?|shorten\w*|brief\w*|concise\w*|terse\w*|succinct\w*|tighter)\b", " short "),
        (r"\b(?:longer|more detail(?:s|ed)?|elaborate|in depth|more thorough)\b", " long "),
        (r"\b(?:plain|simple|normal|everyday) (?:english|words|language|terms)\b", " plain "),
        (r"\b(?:jargon|technical terms|acronyms|big words|fancy words|too technical)\b", " plain "),
        (r"\bbullet(?:ed)? points?\b|\bbullets?\b|\bbulleted\b", " bullet "),
        (r"\bnumbered lists?\b|\blists?\b", " list "),
        (r"\bemojis?\b|\bemojies\b|\bemoticons?\b", " emoji "),
        (r"\btables?\b", " table "),
        (r"\bhead(?:er|ing)s?\b", " heading "),
        (r"\bapolog\w*|\bsorry\b", " apology "),
        (r"\bpreambles?\b|\bintros?\b|\bintroductions?\b|\bsmall talk\b|\bpleasantries\b", " preamble "),
        (r"\bfluff\w*|\bfiller\b", " filler "),
        (r"\bcaveats?\b|\bdisclaimers?\b", " caveat "),
        (r"\bfollow-?up questions?\b|\bclarifying questions?\b|\bask(?:ing)? (?:me )?questions\b", " askq "),
        (r"\bwithout asking\b", " not askfirst "),
        (r"^(?:again|enough) with\b", " not "),
        (r"\bask(?:ing)? (?:me )?(?:first|before)|\bcheck(?:ing)? with me\b|\bconfirm(?:ation)? (?:first|before)\b", " askfirst "),
        # "you keep using tables" complains ABOUT tables: same as "no tables".
        (r"\byou (?:keep|always|still)(?: on)?(?: (?:using|adding|putting|doing|writing|saying|giving|making|being))?\b", " not "),
        (r"\bem ?dash(?:es)?\b|\bdashes\b", " dash "),
        (r"\bexclamation (?:marks?|points?)\b", " exclaim "),
        (r"\bgreat question\b|\bflatter\w*|\bcompliments?\b|\bpraise\b|\bsycophan\w*", " flattery "),
        (r"\bsummar(?:y|ies) at the end\b|\brecaps?\b", " recap "),
        (r"\b(?:don'?t|do not|dont|stop|never|no more|quit|avoid|without|enough with|"
         r"cut out|cut the|lose the|drop the|ditch the|skip the|get rid of|less|fewer)\b", " not "),
        (r"\bno\b", " not "),
    )
)

#: Tokens that carry their own direction (negation is folded into them).
_SELF_POLAR = frozenset({"short", "long", "plain"})

#: Words with no signal for "which correction is this".
_STOP = frozenset(
    """
    a an the and or but so to of in on at for with by from as is are was were be been being
    it its it's this that these those them they their there here i i'm im me my mine you your
    you're yours we us our he she his her please pls plz thanks thank thx again said told
    keep keeps kept make made making use used using just only really very much way bit little
    lot even more most also too still always ever all any some every each one ones thing things
    answer answers reply replies response responses message messages output text write writing
    written do does did doing done can could would will should shall may might must want need
    like as how what when where why who which if then than ok okay hey hi yeah yes no nope nah
    come seriously dude jarvis honestly now going get got stuff put putting add adding
    ugh hmm argh kindly
    give giving tell telling say saying talk talking speak speaking be being been instead
    """.split()
)


def _stem(word: str) -> str:
    """A light stem: trailing ``ies``/``es``/``s``/``ing``/``ed`` off longer words."""
    w = word
    for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)] + rep
    return w


def signature(text: str) -> str:
    """The normalised key of a correction (sorted space-joined tokens).

    ``"shorter please"``, ``"make it shorter"`` and ``"too long, shorten it"``
    all sign as ``"short"``; ``"no tables"`` and ``"stop using tables"`` as
    ``"not table"``. Never raises (``""`` on failure)."""
    try:
        low = " ".join(str(text or "").lower().split())
        low = re.sub(r"^(?:no|nope|nah)\s*[,.!:;-]+\s*", "", low)  # "no, …" is an interjection
        low = low.replace("\u2019", "'")
        for pat, tok in _CANON:
            low = pat.sub(tok, low)
        words = re.findall(r"[a-z][a-z']*", low)
        toks: set[str] = set()
        for w in words:
            w = w.strip("'")
            if not w or w in _STOP:
                continue
            if w in ("not",) or w in _SELF_POLAR or w in _CANON_TOKENS:
                toks.add(w)
                continue
            if len(w) < 3:
                continue
            toks.add(_stem(w))
        if toks & _SELF_POLAR:
            toks.discard("not")
        return " ".join(sorted(toks))
    except Exception:  # noqa: BLE001
        return ""


_CANON_TOKENS = frozenset(tok.strip() for _, tok in _CANON)


def similar(a: str, b: str) -> bool:
    """Do two signatures name the same correction? Jaccard >= SIMILAR_AT on
    their tokens, the same polarity, and at least one shared content token."""
    try:
        sa = set((a or "").split())
        sb = set((b or "").split())
        if not sa or not sb:
            return False
        if ("not" in sa) != ("not" in sb):
            return False
        shared = (sa & sb) - {"not"}
        if not shared:
            return False
        return len(sa & sb) / len(sa | sb) >= SIMILAR_AT
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# the sentence the card offers
# --------------------------------------------------------------------------- #

_CLEAN_LEAD = re.compile(
    r"^(?:(?:(?:ugh|ok|okay|hey|jarvis|come on|seriously|again|once again|"
    r"i said|i told you|like i said|as i said|i already said|please|pls|plz|kindly|and|but|so)"
    r"\b[\s,.!:;-]*)|(?:(?:no|nope|nah)\s*[,.!:;-]+\s*)|(?:(?:no|nope|nah)\s*$))+",
    re.IGNORECASE,
)
_CLEAN_INNER = re.compile(r"\s*,?\s*\b(?:please|pls|plz)\b\s*,?", re.IGNORECASE)
_CLEAN_TRAIL = re.compile(r"(?:[\s,.!]*(?:again|thanks|thank you|thx|ok|okay))+[\s.!?]*$", re.IGNORECASE)

#: Longest proposal sentence.
MAX_PROPOSAL = 280

#: Quotes shaped like an instruction.
_IMPERATIVE = re.compile(
    r"^(?:don'?t|do not|dont|never|no|stop|use|keep|make|be|avoid|skip|answer|reply|write|"
    r"talk|speak|plain|simple|shorter|briefer|less|fewer|more|quit|get|cut|always)\b",
    re.IGNORECASE,
)


def _clean_quote(text: str) -> str:
    out = " ".join(str(text or "").split())
    out = _ASK_LEAD.sub("", _CLEAN_LEAD.sub("", out)).strip()
    out = _CLEAN_TRAIL.sub("", out).strip()
    out = _CLEAN_INNER.sub(" ", out)
    out = re.sub(r"\s+([,.!?])", r"\1", " ".join(out.split()))
    out = out.strip(" ,;:-").rstrip(".!?").strip()
    return out


def proposal_text(quotes: list[str]) -> str:
    """A clean standing-preference sentence from the user's OWN words.

    Strips "no,", "please", "again", "I said" and the like, capitalises, ends
    with a period. Picks the most informative quote (most words after
    cleaning; the first given wins a tie — pass the newest first). ``""`` when
    nothing usable is left."""
    best = ""
    best_key: tuple[int, int] = (-1, -1)
    for q in quotes or []:
        c = _clean_quote(q)
        if not c:
            continue
        # An instruction ("no tables", "keep it short") reads as a standing
        # preference; a complaint ("too long", "you keep …") reads less well.
        key = (1 if _IMPERATIVE.match(c) else 0, len(c.split()))
        if key > best_key:
            best, best_key = c, key
    if not best:
        return ""
    best = best[0].upper() + best[1:]
    best = best[: MAX_PROPOSAL - 1].rstrip()
    return best + "."


__all__ = [
    "MAX_CHARS",
    "MAX_PROPOSAL",
    "SIMILAR_AT",
    "is_correction",
    "proposal_text",
    "signature",
    "similar",
]
