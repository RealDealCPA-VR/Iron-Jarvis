"""WHAT A LOOKUP FOUND JOINS THE REPLY (capability search plan, Phase 2).

Phase 1 let the model look up what Iron Jarvis can do, and only answered.
This is the step after: when ``find_capability`` ran in a round, the tools it
found join the reply for the NEXT round, behind the gates every tool passes.

THE SAFETY RULES (each pinned in ``tests/test_capability_join_v1479.py``,
through the real chat lanes and the real ``registry.invoke``):

1. A LOOKUP NEVER GRANTS A CHANGE (the owner's decision 1: "a change found
   by lookup always shows a card first"). A tool the user's own words bring
   is allowed by those words. A tool the MODEL asked for has no such
   consent: a web page could tell the model to look up "delete" and use what
   it finds. So a found tool that changes anything joins as ASK FIRST (an
   "ask" override, the mechanism ``chat_turn.ask_first_overrides`` uses),
   whatever its own tier is, AND its name goes into the lane's ``found_asks``
   set, which the stream lane reads as a FORCED, ONCE-ONLY card: in every
   approval setting (Don't ask, Accept edits and Auto included), past every
   tab or standing grant, "Allow once" and "Deny" only (a wider answer is
   read as once), reason ``looked_up`` (a safety reason: Enter never allows
   it). One thing lifts it, the user's own earlier answer: a tool they
   already allowed for this chat (``body.granted_tools``), and never for a
   protected setting (``ALWAYS_CARD_TOOLS``). Where nobody can be asked (the
   POST lane: the phone, an API caller) such a tool does not join at all.
   A script cannot reach it either: ``tool_script`` refuses a tool the reply
   holds under an "ask" rule, and a lookup-brought reader never makes the
   script tool join (``chat_turn.offer_script_tool``).
2. A found tool that only reads AND stays on this PC joins and runs, as the
   readers chat already brings do. A tool that reaches outside this PC (the
   web tools, an app's resources: ``egress``; a memory base that may be on
   another machine: :data:`MEMORY_BASE_READERS`) could carry what the reply
   has read away, so it is treated as rule 1 treats a changer. A reader a
   lookup brought is never pointed at another computer
   (:func:`network_refusal`: a ``\\\\host\\share`` path is refused by its text).
3. ONLY A TOOL CHAT COULD ALREADY BRING (:func:`joinable`): the lookup widens
   the WORDS that reach a tool, never the set of tools. Command and script
   runners and the browser's acting tools are never brought by a lookup.
4. Every other gate still holds: Auto tools off (the owner's decision 2:
   the lookup answers, nothing joins), the user's Never run, a deny or ask
   already set for this reply, low trust (nothing joins), Local only (a tool that
   exists to reach the internet does not join; any other tool that reaches
   outside keeps its own Local only card), a weak model's tool budget, and a
   surface's own tool list.
5. BOUNDED: at most :data:`MAX_JOINED_PER_LOOKUP` tools per lookup and
   :data:`MAX_JOINING_LOOKUPS` lookups per reply.
6. NOTHING IS SILENT: the lookup's own result says which tools joined, which
   show the user an approval card when called, and which did not join and why.

The tool leaves what it found for its own turn (:data:`FOUND`, keyed by the
turn's id; a turn with no id gets answers only). Both lanes call
:func:`join_found` at the end of a tool round — MIRROR NOTE (lock-step).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from functools import lru_cache
from typing import Any

from . import index as _index

log = logging.getLogger(__name__)

NAME = "find_capability"

MAX_JOINED_PER_LOOKUP = 3
MAX_JOINING_LOOKUPS = 2

#: Never brought by a lookup, whatever else could bring them: they run a
#: command or a script, or act in the browser.
NEVER_JOINED = frozenset({
    "shell", "repl", "run_code", "code_run", "tool_script", "apply_changes",
    "process_start", "process_write",
})

#: The settings tools: together they take ONE of a lookup's places.
SETTINGS_FAMILY = frozenset({
    "config_list", "config_set", "config_change", "config_change_protected",
})

#: Tools chat arms "in their own place" (``chat_turn._resolve_armed_tools``
#: and the settings and record triggers) that are in none of the lists
#: :func:`joinable` reads.
OWN_PLACE = frozenset({
    "config_list", "config_set", "config_change", "config_change_protected",
    "config_secret", "calculate", "rename_file", "changes_list", "change_undo",
    "preference_list", "skill_create", "skill_update", "skill_load", "skill_search",
    "goal_list", "workflow_list",
    # v1.479.0: armed beside a tool the reply already has, or by a run verb
    # (`autoselect.wants_excel_trace` / `wants_excel_reconcile`, the Guide's
    # reader, `records._runs_a_named_workflow`).
    "guide_read", "excel_trace", "excel_reconcile", "workflow_run",
})


@lru_cache(maxsize=1)
def joinable() -> frozenset[str]:
    """Every tool some arming path of chat can already bring from the user's
    words, minus :data:`NEVER_JOINED` and the browser's tools."""
    from ..settings import records
    from ..tools.autoselect import ASK_TIER_TOOLS, AUTO_SAFE_TOOLS

    names = (
        set(AUTO_SAFE_TOOLS) | set(ASK_TIER_TOOLS) | set(records.RECORD_TOOL_TIERS)
        | set(records.ASK_FIRST_CREATE_TOOLS) | set(OWN_PLACE)
    )
    return frozenset(
        n for n in names if n not in NEVER_JOINED and not n.startswith("browser_")
    )


def comes_another_way(name: str) -> bool:
    """A tool no lookup brings but the user's own words or a switch do (a
    command runner, the browser's tools)."""
    return name in NEVER_JOINED or name.startswith("browser_")


def usable_first(entries: list) -> list:
    """*entries* (index matches, best first) with the tools NO lookup can
    bring and no words of the user bring either moved to the end, each group
    in its own order. An agent's or a coding session's tool often has the
    same words as the chat tool for the same thing (``create_agent`` beside
    ``agent_create``); listed first it sends the model to a tool it can never
    get. Settings, pages, "not offered" lines, joinable tools and the tools
    that come another way keep their places."""
    can = joinable()

    def last(entry: Any) -> bool:
        if getattr(entry, "kind", "") != "tool":
            return False
        name = str(getattr(entry, "tool", "") or "")
        return name not in can and not comes_another_way(name)

    return [e for e in entries if not last(e)] + [e for e in entries if last(e)]


# --------------------------------------------------------------------------- #
# what a lookup found, kept for its own turn
# --------------------------------------------------------------------------- #
MAX_KEYS = 256


def turn_key(ctx: Any) -> str:
    """The key of the turn *ctx* runs in, or "" when it has none (answers
    only then: what a lookup found must never reach another turn)."""
    turn = str(getattr(ctx, "turn_id", "") or "").strip()
    if not turn:
        return ""
    return f"{getattr(ctx, 'session_id', '') or ''}\x1f{turn}"


class _Found:
    """Per turn: the lookups not yet joined (``pending``), how many lookups
    have BROUGHT a tool (``joining``; a lookup that brought nothing uses up
    none of the reply's two), and every name a lookup brought (``names``).
    Bounded: past :data:`MAX_KEYS` turns the OLDEST one goes, never all of
    them (a live turn's pending lookups must not vanish)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    def _entry(self, key: str) -> dict[str, Any]:
        entry = self._by.get(key)
        if entry is None:
            while len(self._by) >= MAX_KEYS:
                self._by.popitem(last=False)
            entry = self._by[key] = {"pending": [], "joining": 0, "names": set()}
        return entry

    def note(self, key: str, rows: Any) -> None:
        if not key or not isinstance(rows, list):
            return
        with self._lock:
            self._entry(key)["pending"].append([r for r in rows if isinstance(r, dict)])

    def take(self, key: str) -> list[list[dict[str, Any]]]:
        """The lookups not yet joined, handed over once."""
        if not key:
            return []
        with self._lock:
            entry = self._by.get(key)
            if entry is None:
                return []
            pending, entry["pending"] = entry["pending"], []
            return pending

    def joining(self, key: str) -> int:
        with self._lock:
            entry = self._by.get(key)
            return int(entry["joining"]) if entry else 0

    def record(self, key: str, names: list) -> None:
        """One lookup brought *names* (non-empty)."""
        if not key or not names:
            return
        with self._lock:
            entry = self._entry(key)
            entry["joining"] += 1
            entry["names"].update(names)

    def brought(self, key: str) -> frozenset:
        with self._lock:
            entry = self._by.get(key)
            return frozenset(entry["names"]) if entry else frozenset()

    def forget_turn(self, turn_id: Any) -> None:
        """Drop whatever an EARLIER turn of this id left (a turn that was
        stopped mid-round never reached its join). Called when a turn's
        lookup is armed, before it can find anything."""
        turn = str(turn_id or "").strip()
        if not turn:
            return
        tail = "\x1f" + turn
        with self._lock:
            for key in [k for k in self._by if k.endswith(tail)]:
                del self._by[key]

    def clear(self) -> None:
        with self._lock:
            self._by.clear()


FOUND = _Found()


def brought_by_lookup(ctx: Any) -> frozenset:
    """The tools a lookup brought into the turn *ctx* runs in."""
    try:
        return FOUND.brought(turn_key(ctx))
    except Exception:  # noqa: BLE001
        return frozenset()


NETWORK_PATH_REFUSAL = (
    "NOT RUN: `{name}` came into this reply through a lookup, and a tool a lookup "
    "brought does not open a path on another computer ({path}). Tell the user; they "
    "can ask for that folder in their own words."
)


def network_refusal(ctx: Any, name: str, args: Any) -> str:
    """Why this call of a lookup-brought tool is not run, or "". A reader the
    MODEL brought is not pointed at another computer: opening a network path
    is a connection that offers the user's sign-in to a host the model chose.
    Read from the TEXT of the arguments only. A reader the user's own words
    brought, and a path inside the reply's own working folder, are untouched."""
    try:
        if str(name) not in brought_by_lookup(ctx):
            return ""
        from ..tools.tool_script import _network_path_in

        path = _network_path_in(args, getattr(ctx, "workspace", None))
        return NETWORK_PATH_REFUSAL.format(name=name, path=path) if path else ""
    except Exception:  # noqa: BLE001 — cannot tell: refuse (it is only a lookup's reader)
        return NETWORK_PATH_REFUSAL.format(name=name, path="it could not be checked")


# --------------------------------------------------------------------------- #
# the join
# --------------------------------------------------------------------------- #
LOW_TRUST_NOTE = (
    "(No tool joined this reply: it has read content that was flagged, so it takes "
    "no new tools. Tell the user what Iron Jarvis can do and where.)"
)
AUTO_OFF_NOTE = (
    "(No tool joined this reply: Auto tools is off for this chat, so it uses only "
    "the tools the user turned on. Tell the user what Iron Jarvis can do and where.)"
)
NO_MORE_NOTE = (
    f"(No more tools join this reply: {MAX_JOINING_LOOKUPS} lookups have already brought "
    "theirs. Use the tools you have, or tell the user what Iron Jarvis can do.)"
)

#: Said when a lookup brought nothing and named only tools it cannot bring.
LOOK_AGAIN = (
    "If the user's request needs a tool, look once more with other English words "
    "for it; otherwise tell the user what Iron Jarvis can do and where."
)

#: How a joined tool that asks is described to the model (v1.480.0). The old
#: words, "asks the user first", were read as an INSTRUCTION: in 15 replies
#: of the Phase 2 live run the right tool had joined and the model wrote
#: "Would you like me to…?" instead of calling it. The card IS the question.
CARD_WORDS = "shows the user an approval card when you call it"
CALL_IT_NOTE = (
    "If you have what the call needs, call it now: the card is how the user "
    "confirms, so do not ask again in words. Ask in words only for a detail the "
    "call needs and you do not have."
)

WHY_NEVER = "a lookup does not bring this tool"
WHY_DENIED = "it is set to Never run"
WHY_HELD = "a rule of this reply already speaks for it"
WHY_LOCAL = "this chat is Local only and the tool reaches the internet"
WHY_BUDGET = "this model's tool list is full"
WHY_CANNOT_ASK = (
    "it changes something or reaches outside this PC, and nobody can be asked here; "
    "the user can ask for it on the chat page"
)
WHY_ASK_RULE = "it is set to ask first, and nobody can be asked here"
WHY_SHARED = "its permission is shared with other tools, so a lookup does not bring it"

#: Readers that can send the model's words to a memory base that is not on
#: this PC (a Notion base, a memory server): treated as reaching outside.
MEMORY_BASE_READERS = frozenset({"ltm_search", "recall"})


def _reads(tool: Any, name: str) -> bool:
    from ..tools.base import Reversibility

    rev = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
    return name in _index.READS_ONLY or rev in (Reversibility.READONLY, Reversibility.READONLY.value)


def asks_when_found(registry: Any, name: str) -> bool:
    """Would a lookup bring *name* as ASK FIRST (it changes something, or
    reaches outside this PC)? What the lookup's own answer line says, so the
    list and the note under it agree. Fail-closed: cannot tell = it asks."""
    try:
        tool = registry.get(name)
        if tool is None:
            return True
        if not _reads(tool, name):
            return True
        from ..providers import egress

        return bool(
            name in egress.INTERNET_TOOLS or name in MEMORY_BASE_READERS
            or egress.reaches_outside(tool, registry)
        )
    except Exception:  # noqa: BLE001
        return True


def _wanted(rows: list[dict[str, Any]]) -> list[str]:
    """The tools one lookup asks for, in its own order: a tool match's tool;
    a setting match's change tool and the settings reader."""
    out: list[str] = []
    for row in rows:
        kind = row.get("kind")
        names = []
        if kind == "tool":
            names = [str(row.get("tool") or row.get("id") or "")]
        elif kind == "setting":
            names = [str(row.get("tool") or ""), "config_list"]
        elif kind == "secret":
            names = [str(row.get("tool") or "")]
        for name in names:
            if name and name not in out:
                out.append(name)
    return out


def _never_lifted() -> frozenset:
    """Tools whose card NO earlier answer lifts (a protected setting)."""
    try:
        from ..settings.tools import ALWAYS_CARD_TOOLS

        return frozenset(ALWAYS_CARD_TOOLS)
    except Exception:  # noqa: BLE001
        return frozenset({"config_change_protected"})


def join_found(
    d: Any,
    body: Any,
    *,
    tools_used: list,
    armed: list,
    auto_armed: list,
    ask_armed: Any,
    tool_specs: list,
    overrides: dict,
    armed_grant: set,
    msgs: list,
    ctx: Any,
    tool_ceiling: Any = None,
    ceiling: "int | None" = None,
    low_trust: bool = False,
    can_ask: bool = False,
    max_tools: int = 6,
    found_asks: "set | None" = None,
) -> list[str]:
    """Bring what this round's lookups found into the reply, in place (the
    round loop reads these same lists each round). Returns the names that
    joined. ``ask_armed`` must be the lane's own LIST when ``can_ask`` (the
    stream lane), with ``found_asks`` the set that lane reads as its forced
    cards; the POST lane passes ``()`` and ``can_ask=False``. With no
    ``found_asks`` set nothing that asks can join (there would be no forced
    card). Never raises, and a fault leaves every list as it was."""
    key = turn_key(ctx)
    try:
        lookups = FOUND.take(key)
    except Exception:  # noqa: BLE001
        return []
    if not lookups:
        return []
    asks_list = ask_armed if isinstance(ask_armed, list) else None
    before = (
        list(armed), list(auto_armed), list(asks_list) if asks_list is not None else None,
        list(tool_specs), dict(overrides), set(armed_grant),
        set(found_asks) if found_asks is not None else None,
    )
    try:
        # The setting is the switch: off, nothing joins, however the lookup
        # came to be among the reply's tools (a body that named it).
        if not getattr(getattr(d.platform, "config", None), "capability_search", False):
            return []
        if low_trust:
            _say(msgs, LOW_TRUST_NOTE)
            return []
        if not getattr(body, "auto_tools", False):
            _say(msgs, AUTO_OFF_NOTE)
            return []
        granted = {str(g or "").strip() for g in (getattr(body, "granted_tools", None) or ())}
        granted -= _never_lifted()
        registry = d.platform.registry
        perms = getattr(d.platform, "permissions", None)
        if perms is None:
            return []  # no permission table: nothing can be judged, nothing joins
        try:
            from ..providers import egress

            local_only = bool(egress.current_scope().on)
            internet = frozenset(egress.INTERNET_TOOLS)
        except Exception:  # noqa: BLE001 — Local only cannot be read: nothing joins
            return []
        room = max(0, MAX_JOINING_LOOKUPS - FOUND.joining(key))
        can = joinable()
        joined: list[str] = []
        asks: list[str] = []
        skipped: dict[str, str] = {}
        capped = False
        for rows in lookups:
            if room <= 0:
                capped = True
                continue
            taken = 0
            settings_counted = False
            mine: list[str] = []
            for name in _wanted(rows):
                held = {*armed, *(asks_list or ())}
                if name in held or name in joined:
                    continue
                # The settings tools count as ONE of a lookup's places
                # together (several setting matches all map to the same two
                # or three tools, and must not crowd out the tool matches).
                # Free only once the family HOLDS its place (v1.482.0: the
                # reader used to be free on its own, so with three places
                # taken it slipped in and then let a changer in after it).
                free = name in SETTINGS_FAMILY and settings_counted
                if taken >= MAX_JOINED_PER_LOOKUP and not free:
                    continue
                tool = registry.get(name)
                if tool is None or name not in can:
                    if tool is not None:
                        skipped[name] = WHY_NEVER
                    continue
                if tool_ceiling is not None and name not in tool_ceiling:
                    continue
                perm_key = getattr(tool, "perm_key", None)
                pkey = perm_key() if callable(perm_key) else name
                if any(getattr(perms.mode_for(k), "value", "deny") == "deny" for k in {name, pkey}):
                    skipped[name] = WHY_DENIED
                    continue
                turn_rule = str(overrides.get(name) or overrides.get(pkey) or "")
                if turn_rule == "deny":
                    skipped[name] = WHY_HELD
                    continue
                if pkey != name:
                    # A permission shared with other tools: granting or
                    # asking for the KEY would speak for its siblings too.
                    skipped[name] = WHY_SHARED
                    continue
                if local_only and name in internet:
                    skipped[name] = WHY_LOCAL
                    continue
                if ceiling is not None and ceiling < max_tools and len(held) >= ceiling:
                    skipped[name] = WHY_BUDGET
                    continue
                must_ask = asks_when_found(registry, name)
                mode = str(getattr(perms.mode_for(name, overrides), "value", "ask") or "ask")
                if not must_ask and mode == "allow" and turn_rule != "ask":
                    # Rule 2: a reader that stays on this PC joins and runs,
                    # like the readers chat already brings.
                    armed.append(name)
                    auto_armed.append(name)
                    overrides[name] = "allow"
                    armed_grant.add(name)
                else:
                    # Rule 1: everything else asks first, or does not join.
                    if not can_ask or asks_list is None or found_asks is None:
                        skipped[name] = WHY_CANNOT_ASK if must_ask else WHY_ASK_RULE
                        continue
                    overrides[name] = "ask"
                    asks_list.append(name)
                    if name in granted:
                        # The user already said "Allow for this chat" to THIS
                        # tool on a card: their answer stands (v1.312.0: a
                        # grant applies to a tool the turn armed some other
                        # way). Never for a protected setting.
                        armed_grant.add(name)
                    else:
                        armed_grant.discard(name)
                        found_asks.add(name)
                        asks.append(name)
                joined.append(name)
                mine.append(name)
                taken += 0 if free else 1
                settings_counted = settings_counted or name in SETTINGS_FAMILY
            if mine:
                room -= 1
                FOUND.record(key, mine)
        if joined:
            known = {*armed, *(asks_list or ())}
            extras = [
                s for s in tool_specs
                if isinstance(s, dict) and str(s.get("name") or "") not in known
            ]
            tool_specs[:] = registry.specs([*armed, *(asks_list or ())]) + extras
        _say(msgs, _note(joined, asks, skipped, capped))
        # Names only (never a query or an argument): which lookups brought
        # what. The plan's Phase 5 reads this to see what the word tables miss.
        log.info(
            "capability join: turn=%s joined=%s asks=%s not_joined=%s",
            str(getattr(ctx, "turn_id", "") or ""), joined, asks, sorted(skipped),
        )
        return joined
    except Exception:  # noqa: BLE001 — a join never fails a turn, and never half-happens
        log.debug("capability join failed", exc_info=True)
        armed[:] = before[0]
        auto_armed[:] = before[1]
        if asks_list is not None and before[2] is not None:
            asks_list[:] = before[2]
        tool_specs[:] = before[3]
        overrides.clear()
        overrides.update(before[4])
        armed_grant.clear()
        armed_grant.update(before[5])
        if found_asks is not None and before[6] is not None:
            found_asks.clear()
            found_asks.update(before[6])
        return []


def _note(joined: list[str], asks: list[str], skipped: dict[str, str], capped: bool) -> str:
    parts: list[str] = []
    if joined:
        shown = ", ".join(
            f"`{n}`" + (f" ({CARD_WORDS})" if n in asks else "") for n in joined
        )
        parts.append(
            f"Now among your tools in this reply: {shown}. Call the one that fits "
            "what the user asked for."
        )
        if asks:
            parts.append(CALL_IT_NOTE)
    for name, why in skipped.items():
        parts.append(f"`{name}` did not join: {why}.")
    if capped:
        parts.append(NO_MORE_NOTE.strip("()"))
    elif skipped and not joined:
        parts.append(LOOK_AGAIN)
    if not parts:
        return ""
    return "(" + " ".join(parts) + ")"


def _say(msgs: list, note: str) -> None:
    """Put *note* on this round's lookup result (the newest one)."""
    if not note:
        return
    for message in reversed(msgs):
        if getattr(message, "role", "") != "tool":
            break
        if str(getattr(message, "name", "") or "") == NAME:
            message.content = str(message.content or "") + "\n\n" + note
            return
    for message in reversed(msgs):
        if getattr(message, "role", "") == "tool":
            message.content = str(message.content or "") + "\n\n" + note
            return
