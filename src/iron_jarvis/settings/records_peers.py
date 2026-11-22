"""What your other computers may do here, from chat (next leaps 41-a, D-41.2).

Each paired computer has one switch per thing it may do on this computer
(``peers/allows``), every one off for a new pairing. Chat reaches them two ways:

* ``peer_allows`` (allow, reads only): each paired computer, what it may do
  here, and what it last said it lets THIS computer do ("what may my desk
  do?").
* ``peer_allow_off`` (allow: it only narrows, like ``grant_revoke``): turns
  ONE switch, or every switch, OFF for one paired computer, through THE body
  the card's press runs (``routes/peers.change_allows``), so turning "Send me
  tasks" off stops that computer's tasks still running here, the way Remove
  does. Its Undo puts back only what it turned off — and only on a PERSON's
  Undo press (``perform_undo(pressed=True)``): a model's undo is refused in
  words, because turning a switch ON is never chat's (D-41.2). It refuses in
  words when the switch was changed again since.

Turning a switch ON is a press on the card (Settings › Connections › Your
other computers); the reader and the page door (``daemon/page_doors`` step
``peer_switches``) say so. Neither tool reaches a task another computer sent
(``core/trust.REMOTE_RUN_KEEP`` keeps neither) and the writer is kept away
from a low run (``LOW_TRUST_DENY``: it stops tasks).

Arming (:func:`peers_tools_for`, called by ``chat_turn.settings_ask_tools``,
which both chat lanes run off the loop, with the pairings' names): a message
naming a paired computer, or "my other computer", beside what one may do. A
question keeps only the reader. Linear, bounded patterns.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Iterable

from ..peers import allows as A
from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult
from . import records as R

READER = "peer_allows"
WRITER = "peer_allow_off"
#: The record kind this module's Undo serves (``records.restore``).
KIND = "peer_allows"
#: The tool that carries a card and an Undo, and its tier (``records.
#: RECORD_TOOL_TIERS`` and ``core.config``'s table say the same).
TIERS: dict[str, str] = {WRITER: "allow"}
ALL_TIERS: dict[str, str] = {READER: "allow", WRITER: "allow"}

#: The card (``daemon/page_doors.PAIRING_HREF``).
HREF = "/settings?section=connections-computers"
PLACE = "Settings › Connections › Your other computers"
ALL = "all"

NO_PAIRINGS = "No other computer is paired with this one."
NOT_HERE = "Your other computers can only be changed while Iron Jarvis's window is running."
ON_IS_A_PRESS = (
    f"Turning a switch ON is a press on the card in {PLACE}; chat can show the switches and turn them off."
)
#: A model's undo of a switch turned off (D-41.2): only a person's press.
PRESS_TO_UNDO = (
    "Turning a switch back on takes your own press: press Undo under the change, or turn it on in "
    f"{PLACE}. Nothing was changed."
)

#: How much of a message the arming reads.
SCAN_CHARS = 2000


def _d(platform: Any) -> Any:
    w = getattr(platform, "config_writer", None)
    return getattr(w, "d", None)


def _store(platform: Any) -> Any:
    from ..peers.store import store_for

    return store_for(platform)


def peer_names(platform: Any) -> tuple[str, ...]:
    """The pairings' names, for the arming. BLOCKING (one SQLite read).
    Never raises (``()``)."""
    try:
        return tuple(p.name for p in _store(platform).list())
    except Exception:  # noqa: BLE001 — arming never breaks a turn
        return ()


def _find(platform: Any, typed: str) -> tuple[Any, list[Any]]:
    """``(the pairing the words name, every pairing)``: by its name here, any
    case, an "@" in front allowed. BLOCKING."""
    peers = _store(platform).list()
    want = str(typed or "").strip().lstrip("@").casefold()
    for p in peers:
        if want and p.name.casefold() == want:
            return p, peers
    return None, peers


def _which_words(peers: list[Any]) -> str:
    if not peers:
        return NO_PAIRINGS
    return "Say which computer: " + ", ".join(p.name for p in peers) + "."


def snap(platform: Any, name: str) -> dict[str, Any] | None:
    """The switches of the pairing ``name`` (on or off each), or None when no
    pairing has that name. BLOCKING."""
    peer = _store(platform).get(name)
    if peer is None:
        return None
    allows = A.allows_of(peer)
    return {k: bool(allows[k]) for k in A.KEYS}


def restore_kind(platform: Any, name: str, prior: dict[str, Any] | None, desc: dict[str, Any]) -> str:
    """Undo of ``peer_allow_off``: put back ON only the switches that change
    turned off — and only on a person's Undo press (``desc["pressed"]``, set
    by ``perform_undo`` itself, never by the tool), since turning a switch on
    is never chat's (D-41.2). Refuses in words when the pairing is gone or a
    switch was changed again since. BLOCKING."""
    if desc.get("pressed") is not True:
        raise R.RecordError(PRESS_TO_UNDO)
    store = _store(platform)
    peer = store.get(name)
    if peer is None:
        raise R.RecordError(f"{name} is no longer paired with this computer, so nothing was put back.")
    try:
        turned = [k for k in json.loads(desc.get("now") or "[]") if k in A.BY_KEY]
    except (TypeError, ValueError):
        turned = []
    prior = prior if isinstance(prior, dict) else {}
    back = [k for k in turned if prior.get(k) is True]
    now = A.allows_of(peer)
    if any(now.get(k) for k in back):
        raise R.RecordError(
            f"What {name} may do was changed again since, so this change was not undone. Change it in {PLACE}."
        )
    if not back:
        return f"undo: nothing to put back for {name}"
    from ..peers.store import AllowsProblem

    try:
        store.set_allows(peer.id, {k: True for k in back})
    except AllowsProblem as exc:
        raise R.RecordError(str(exc)) from None
    return f"undo: {A.may_line(name, A.allows_of(store.get(name) or peer))}"


def _lines(peers: list[Any]) -> list[str]:
    out: list[str] = []
    for p in peers:
        allows = A.allows_of(p)
        off = [s for s in A.SWITCHES if s.ready and not allows.get(s.key)]
        later = [s for s in A.SWITCHES if not s.ready]
        line = A.may_line(p.name, allows)
        if off:
            line += " Off: " + ", ".join(s.label for s in off) + "."
        if later:
            line += " Coming in a later version: " + ", ".join(s.label for s in later) + "."
        they = A.they_allow_of(getattr(p, "they_allow_json", None))
        out.append(f"- {line} {A.they_line(p.name, they)}")
    return out


class PeerAllowsTool(Tool):
    name = READER
    description = (
        "List the computers paired with this one (Settings › Connections › Your other computers) and what "
        "each may do HERE, on this computer, switch by switch (\"Send me tasks\" and the rest), plus what "
        "each said it lets THIS computer do. Reads only. Turning a switch ON is only the user's press on "
        "that card; peer_allow_off turns one off."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            peers = await asyncio.to_thread(lambda: _store(self.platform).list())
        except Exception:  # noqa: BLE001 — said in words, never a traceback
            return ToolResult(ok=False, error="Your other computers could not be read just now.",
                              data={"door": HREF})
        if not peers:
            return ToolResult(ok=True, output=NO_PAIRINGS + f" Pairing is done in {PLACE}.",
                              data={"computers": [], "door": HREF})
        lines = ["What each paired computer may do on this computer:", *_lines(peers), ON_IS_A_PRESS]
        rows = [
            {"name": p.name, "may": A.on_keys(A.allows_of(p)),
             "lets_this_computer": A.they_allow_of(getattr(p, "they_allow_json", None))["allows"]}
            for p in peers
        ]
        return ToolResult(ok=True, output="\n".join(lines), data={"computers": rows, "door": HREF})


class PeerAllowOffTool(R._RecordTool):
    name = WRITER
    RECORD = KIND
    description = (
        "Turn OFF something a paired computer may do on this computer: `computer` is its name (as in "
        "\"@desk\"), `switch` is one of " + ", ".join(f"\"{k}\" ({A.BY_KEY[k].label})" for k in A.KEYS)
        + f", or \"{ALL}\" for every switch. Turning \"may_send_tasks\" off also stops the tasks that "
        "computer sent that are still running here. It only ever turns switches OFF: turning one on is the "
        "user's press on the card in Settings › Connections › Your other computers. The change has an Undo "
        "the user presses."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "computer": {"type": "string"},
            "switch": {"type": "string", "enum": [*A.KEYS, ALL]},
        },
        "required": ["computer", "switch"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("computer") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        peer, _peers = _find(self.platform, self._target(args))
        return peer.name if peer is not None else ""

    def _turning(self, args: dict[str, Any], current: dict[str, Any] | None) -> list[str]:
        switch = str(args.get("switch") or "").strip()
        if not current:
            return []
        keys = list(A.KEYS) if switch == ALL else [switch] if switch in A.BY_KEY else []
        return [k for k in keys if current.get(k)]

    def _turned(self, args: dict[str, Any]) -> str:
        """The switches this call turns off, as JSON (the Undo puts back only
        these, and only while they are still off). Read BEFORE the change.
        BLOCKING."""
        name = self._resolve(args)
        return json.dumps(self._turning(args, snap(self.platform, name) if name else None))

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        got = await super().capture_undo(args, ctx)
        if got is None:
            return None
        desc = json.loads(got["pre_inline"])
        desc["now"] = await asyncio.to_thread(self._turned, args)
        desc.pop("pressed", None)  # only perform_undo says a person pressed
        return {**got, "pre_inline": json.dumps(desc, default=str)}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        switch = str(args.get("switch") or "").strip()
        if switch != ALL and switch not in A.BY_KEY:
            return ToolResult(ok=False, error="Say which switch: " + ", ".join([*A.KEYS, ALL]) + ".")
        peer, peers = await asyncio.to_thread(_find, self.platform, self._target(args))
        if peer is None:
            return ToolResult(ok=False, error=_which_words(peers), data={"door": HREF})
        current = A.allows_of(peer)
        turning = self._turning(args, current)
        if not turning:
            said = (f"Nothing was changed: {A.may_line(peer.name, current)}"
                    if switch == ALL else f"“{A.BY_KEY[switch].label}” is already off for {peer.name}.")
            return ToolResult(ok=False, error=said, data={"door": HREF})
        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NOT_HERE, data={"door": HREF})
        from ..daemon.routes.peers import AllowsRefused, change_allows

        try:
            out = await change_allows(d, peer.name, {k: False for k in turning})
        except AllowsRefused as exc:
            return ToolResult(ok=False, error=exc.words, data={"door": HREF})
        old = A.may_line(peer.name, out["before"])
        new = A.may_line(peer.name, out["after"])
        label = (
            f"What {peer.name} may do here" if len(turning) > 1
            else f"“{A.BY_KEY[turning[0]].label}” for {peer.name}"
        )
        return ToolResult(
            ok=True,
            output=f"{out['said']} {ON_IS_A_PRESS}",
            data={
                "record_change": self._card(
                    "Switched off", label, "on" if len(turning) == 1 else old,
                    "off" if len(turning) == 1 else new, f"peer.{peer.name}",
                ),
                "stopped": out.get("stopped", 0),
                "door": HREF,
            },
        )


def peer_tools(platform: Any) -> list[Tool]:
    return [PeerAllowsTool(platform), PeerAllowOffTool(platform)]


# --------------------------------------------------------------------------- #
# the words that bring them to a chat reply
# --------------------------------------------------------------------------- #
#: A paired computer said without its name.
_PEER_NOUN = re.compile(
    r"\b(?:paired|other|another|second)\s+(?:computers?|pcs?|laptops?|desktops?|machines?)\b"
    r"|\byour\s+other\s+computers\b",
    re.IGNORECASE,
)
#: What one may do here ("send me tasks", "my work record", "my models"…).
#: "sending tasks TO the desk" is this computer's own sending, never a switch.
_SWITCH_WORDS = re.compile(
    r"\bsend(?:s|ing)?\s+(?:me|us|this\s+(?:computer|pc|one)|here)\b[^.!?\n]{0,24}?\b(?:tasks?|jobs?|work)\b"
    r"|\bsend(?:s|ing)?\s+(?:tasks?|jobs?|work)\s+(?:to\s+me|here|over\s+here)\b"
    r"|\b(?:tasks?|jobs?)\s+(?:from\s+(?!this\b|here\b|me\b|us\b)|it\s+sends\b)"
    r"|\bwork\s+record\b|\b(?:my|your|our)\s+(?:models?|record)\b"
    r"|\bask(?:s|ing)?\s+(?:me\s+)?questions?\b|\bcoding\s+work\b"
    r"|\bhand(?:s|ing)?\s+(?:me\s+)?(?:a\s+)?(?:whole\s+)?chats?\b",
    re.IGNORECASE,
)
_PERMIT_WORDS = re.compile(
    r"\b(?:may|allowed|allows?|let|lets|permissions?|switch(?:es)?|access|able\s+to)\b",
    re.IGNORECASE,
)
#: Without a computer's NAME ("my other computer"), only these say it is
#: about what one may do: "let other computers on my network reach this one"
#: is the network setting, never a switch.
_PERMIT_STRICT = re.compile(r"\b(?:may|allowed|permissions?|switch(?:es)?)\b", re.IGNORECASE)
_OFF_VERB = re.compile(
    r"\b(?:stop|turn(?:ed)?\s+off|switch(?:ed)?\s+off|shut\s+off|cut\s+off|disable|block|revoke|disallow"
    r"|forbid|prevent|(?:don['’]?t|do\s+not|no\s+longer|never)\s+(?:let|allow))\b",
    re.IGNORECASE,
)
_ON_VERB = re.compile(
    r"\b(?:let|allow|permit|enable|turn\s+on|switch\s+on)\b",
    re.IGNORECASE,
)
_NOT_OFF_BEFORE_ON = re.compile(r"\b(?:don['’]?t|do\s+not|no\s+longer|never)\s+$", re.IGNORECASE)
#: The page door's test has no pairing names (``daemon/page_doors`` is pure),
#: so a switch's everyday words ("turn on my models", "enable coding work in
#: this folder") count only beside another computer: a peer noun, a computer
#: word ("the desk", "my laptop") or an "@" name. The one exception is the
#: switch's own words "send me tasks", which nothing else in the app uses.
_COMPUTER_WORD = re.compile(
    r"\b(?:my|the|our|your|that)\s+(?:other\s+)?(?:desk|desktop|laptop|pc|computer|machine|workstation|tower"
    r"|mac|macbook|server|notebook)s?\b"
    r"|(?<![\w@])@\w",
    re.IGNORECASE,
)
_SEND_ME_TASKS = re.compile(r"\bsend\s+me\s+tasks\b", re.IGNORECASE)


def _names_rx(names: Iterable[str]) -> "re.Pattern[str] | None":
    clean = sorted({str(n) for n in names if str(n or "").strip()}, key=len, reverse=True)[:50]
    if not clean:
        return None
    return re.compile(
        r"(?<![\w-])@?(?:" + "|".join(re.escape(n) for n in clean) + r")(?![\w-])", re.IGNORECASE
    )


def _named(t: str, names: Iterable[str]) -> bool:
    rx = _names_rx(names)
    return rx is not None and rx.search(t) is not None


def might_ask(text: str) -> bool:
    """Could *text* be about what a paired computer may do (a switch's
    words, or one of the permission words)? The cheap test before the
    pairings' names are read. Pure."""
    t = str(text or "")[:SCAN_CHARS]
    return bool(t.strip()) and (_SWITCH_WORDS.search(t) is not None or _PERMIT_WORDS.search(t) is not None)


def peers_tools_for(text: str, names: Iterable[str] = ()) -> list[str]:
    """The paired-computer tools a chat message asks for: the reader when it
    names a paired computer (by its name here, or "my other computer") beside
    what one may do; the writer too when it asks to turn that off. A
    question keeps the reader only. Linear (each gap bounded)."""
    t = str(text or "")[:SCAN_CHARS]
    if not t.strip():
        return []
    switchy = _SWITCH_WORDS.search(t) is not None
    if _named(t, names):
        if not (switchy or _PERMIT_WORDS.search(t)):
            return []
    elif not (_PEER_NOUN.search(t) and (switchy or _PERMIT_STRICT.search(t))):
        return []
    out = [READER]
    if _OFF_VERB.search(t) and switchy and not R.is_question(t):
        out.append(WRITER)
    return out


def switch_on_asked(text: str) -> bool:
    """Does *text* ask to let a paired computer DO something here ("let desk
    send me tasks", "allow my other computer to use my models")? That is a
    press on the card (the page door ``peer_switches``), never chat's. Pure."""
    t = str(text or "")[:SCAN_CHARS]
    if not _SWITCH_WORDS.search(t):
        return False
    if not (_SEND_ME_TASKS.search(t) or _PEER_NOUN.search(t) or _COMPUTER_WORD.search(t)):
        return False
    for m in _ON_VERB.finditer(t):
        if not _NOT_OFF_BEFORE_ON.search(t[max(0, m.start() - 12):m.start()]):
            return True
    return False


ALL_PATTERNS = (_PEER_NOUN, _SWITCH_WORDS, _PERMIT_WORDS, _PERMIT_STRICT, _OFF_VERB, _ON_VERB, _NOT_OFF_BEFORE_ON,
                _COMPUTER_WORD, _SEND_ME_TASKS)
