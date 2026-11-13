"""THE CAPABILITY INDEX (capability search plan, Phase 1).

Which tools a chat reply gets is decided before the model answers, by
matching the user's words against hand-written patterns. Measured on wording
nobody wrote a pattern for (``tests/test_capability_reach_v1478.py``), those
patterns reach the right tool on 50 of 164 requests. This module is the other
way in: ONE index of everything Iron Jarvis can do, which the model searches
in its own words (``capability_search/tool.py``).

An entry is built ONLY from text the product already has:

* a built-in tool: its name, the approval card's words for it
  (``agents/mission.card_words``) and its own description;
* a setting: the schema row's label, other names, help and choices
  (``settings/schema``);
* a step that needs a page, and a thing Iron Jarvis does not offer: the
  sentence ``daemon/page_doors`` already holds for it.

No hand-written synonym list lives here, on purpose: the index must not
become a second set of word tables. A gap is closed by improving the text
the entry is built from (a tool's description, a setting's help).

EVERYDAY PHRASINGS (v1.482.0, ``phrasings.py``): people do not use the
product's own words ("a helper" for an agent, "stop asking me" for the
approvals setting). Each entry also carries eight short ways a non-technical
person might ask for it, written by a model from THAT ENTRY'S OWN TEXT and
nothing else (``scripts/gen_capability_phrasings.py``; never from a request
set, never edited by hand because a request misses: regenerate the entry).
They are search words only: no answer line ever shows them. Measured on a
development set written for it (612 requests): the right entry among the
first five 70.8% -> 86.3%, and 94.1% with the lookup's "two other ways to
name it" (``tool.py``).

NEVER in the index, because their words are someone else's:

* a connected app's tools (``mcp__…``);
* a user's own command tools. Those are registered under their PLAIN name
  (``custom:<name>`` is only their permission key), so they are left out by
  asking the registry which names are custom (``registry.custom_names()``),
  never by the shape of a name.

They join in a later phase, behind the check a skill's description gets.

Search is word-ranked (BM25), deterministic and needs no model. Everything
here is pure and bounded. (The package is ``capability_search``; the
unrelated ``capability`` package holds skill and tool proposals.)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

#: Tools a chat lookup never lists: they only mean something inside an agent
#: run or a coding session, or chat deliberately swaps them for another tool
#: (``secret_set``: a key never rides a tool argument in chat, so chat offers
#: ``config_secret``'s secure card in its place).
NOT_FOR_CHAT = frozenset({
    "find_capability", "ask_user", "exit_plan_mode", "plan_update", "notebook",
    "worklist_add", "worklist_done", "worklist_next", "worklist_status",
    "blackboard_post", "blackboard_read", "message_agent", "consult",
    "capability_propose", "memory_propose", "secret_set",
})

#: Tools that read and change nothing although they do not declare themselves
#: read-only (read from each one's own description).
READS_ONLY = frozenset({
    "app_search", "app_status", "browse", "computer_use_status", "file_search",
    "goal_list", "guide_read", "guide_search", "image_info", "integration_list",
    "list_agents", "memory_read", "memory_search", "recall", "recall_lessons",
    "secret_list", "tool_list", "view_image", "web_extract", "web_fetch",
    "web_search",
})

#: The settings tool that changes a row of each tier (pinned equal to
#: ``settings/tools.TOOL_FOR_TIER``).
SETTING_TOOL = {
    "allow": "config_set",
    "ask": "config_change",
    "ask-floor": "config_change_protected",
}

#: A query is the model's own short description of a request.
QUERY_CHARS = 300
MAX_RESULTS = 8

KINDS = ("tool", "setting", "secret", "page", "not_offered")

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by can do does for from has have how i if in into is it its "
    "me my of on or so that the their them then there this to up use used uses using "
    "was what when which who will with you your iron jarvis user users tool tools call "
    "calls called one only not no any all also each every more most must may never "
    "always than them these those own".split()
)


@dataclass(frozen=True)
class Capability:
    """One thing Iron Jarvis can do, or truly cannot."""

    kind: str
    id: str
    #: Plain words: "make an agent", "Text size".
    title: str
    #: The tool that does it ("" for a page step or a thing not offered).
    tool: str = ""
    #: Whether doing it changes something.
    changes: bool = False
    #: Where the user can do it by hand, when there is one place.
    href: str = ""
    #: The sentence to say, for a page step or a thing not offered.
    line: str = ""
    #: What the entry is found by.
    text: str = ""
    #: A setting some of whose values only the protected tool may set.
    protected: bool = False


def _stem(word: str) -> str:
    """One stem for a word and its everyday forms, so "setting" and
    "settings", "file" and "files", "change", "changes", "changed" and
    "changing" are the same search word. Every rule keeps at least three
    letters. Crude on purpose: it only has to be the SAME for both sides."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


_STOP_STEMS = frozenset(_stem(w) for w in _STOP)


def tokens(text: Any) -> list[str]:
    """Search words of *text*: lower-cased, lightly stemmed, stop words
    dropped in every form ("call", "calls", "calling"). Linear in the text."""
    out = []
    for raw in _WORD.findall(str(text or "").lower()):
        if raw in _STOP:
            continue
        word = _stem(raw)
        if word not in _STOP_STEMS:
            out.append(word)
    return out


def has_search_words(query: Any) -> bool:
    """Is there anything in *query* to search with?"""
    return bool(tokens(str(query or "")[:QUERY_CHARS]))


def _one_line(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def _tool_entries(platform: Any) -> list[Capability]:
    from ..agents.mission import card_words
    from ..tools.base import Reversibility

    registry = platform.registry
    names = sorted(registry.names())
    # A user's own command tools, by the registry's own list of them.
    custom = set(registry.custom_names())
    out: list[Capability] = []
    for name in names:
        if name in NOT_FOR_CHAT or name in custom or name.startswith("mcp__") or ":" in name:
            continue
        tool = registry.get(name)
        if tool is None:
            continue
        title = card_words(name)
        rev = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
        reads = name in READS_ONLY or rev in (Reversibility.READONLY, Reversibility.READONLY.value)
        description = _one_line(getattr(tool, "description", ""), 700)
        out.append(Capability(
            kind="tool", id=name, title=title, tool=name, changes=not reads,
            text=f"{name.replace('_', ' ')} {title} {description}",
        ))
    return out


def _setting_entries(_platform: Any = None) -> list[Capability]:
    from ..settings import schema

    out: list[Capability] = []
    for row in schema.SETTINGS:
        if getattr(row, "pattern", False):
            continue  # a family of keys ("permissions.{tool}"), not one setting
        choices = " ".join(str(label) for _value, label in (row.options or ()))
        others = " ".join(row.aliases or ())
        out.append(Capability(
            kind="setting", id=row.key, title=row.label,
            tool=SETTING_TOOL[row.tier], changes=True,
            href=f"/settings?focus={row.key}",
            protected=bool(row.floor_when) and row.tier != "ask-floor",
            text=f"{row.label} {others} {row.key.replace('_', ' ').replace('.', ' ')} "
                 f"{_one_line(row.help, 500)} {choices} setting",
        ))
    return out


def _secret_entries(_platform: Any = None) -> list[Capability]:
    """v1.484.0: the settings that hold a private value (the calendar link, the
    speech-to-text key). They are not in ``schema.SETTINGS`` (a value never
    rides a tool argument in chat), so before this nothing could find them: "connect
    my calendar on the internet" never reached the calendar link. Each is set
    only through ``config_secret``'s secure card. A family of secrets (one per
    provider, channel or app) is left out: each has its own tool."""
    from ..settings import schema

    out: list[Capability] = []
    for secret in schema.SECRETS:
        if secret.pattern:
            continue
        others = " ".join(secret.aliases or ())
        out.append(Capability(
            kind="secret", id=secret.name, title=secret.label, tool="config_secret",
            changes=True,
            text=f"{secret.label} {others} {secret.name.replace('_', ' ')} "
                 f"{_one_line(secret.help, 500)} setting",
        ))
    return out


def _page_entries(_platform: Any = None) -> list[Capability]:
    from ..daemon import page_doors

    out: list[Capability] = []
    for step in page_doors.STEPS:
        line = page_doors.line_without_reply_press(step)
        out.append(Capability(
            kind="page", id=step.id, title=step.label, href=step.href, line=line,
            text=f"{step.id.replace('_', ' ')} {step.label} {line}",
        ))
    for item in page_doors.NOT_OFFERED:
        out.append(Capability(
            kind="not_offered", id=item.id, title=item.id.replace("_", " "), line=item.line,
            text=f"{item.id.replace('_', ' ')} {item.line}",
        ))
    return out


#: The parts of the index, by the name a reader is told when one could not
#: be read.
PARTS = (
    ("tools", _tool_entries),
    ("settings", _setting_entries),
    ("secrets", _secret_entries),
    ("pages", _page_entries),
)


class CapabilityIndex:
    """The entries plus what a word search needs. Immutable once built."""

    def __init__(self, entries: list[Capability], unreadable: tuple[str, ...] = ()) -> None:
        self.entries = entries
        #: The parts that could not be read when this index was built.
        self.unreadable = tuple(unreadable)
        self._docs: list[dict[str, int]] = []
        self._lengths: list[int] = []
        df: dict[str, int] = {}
        for entry in entries:
            # The title and the id count three times: a request is usually
            # named by what the card calls it, and a description only
            # explains it.
            head = tokens(entry.title) + tokens(entry.id.replace("_", " "))
            words = head * 2 + tokens(entry.text)
            counts: dict[str, int] = {}
            for word in words:
                counts[word] = counts.get(word, 0) + 1
            self._docs.append(counts)
            self._lengths.append(len(words))
            for word in counts:
                df[word] = df.get(word, 0) + 1
        n = max(1, len(entries))
        self._avg = (sum(self._lengths) / n) or 1.0
        self._idf = {w: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def known_words(self, query: Any) -> tuple[int, int]:
        """``(search words of *query* that appear anywhere in the list, all its
        search words)``. The list is in English, so a query in another
        language knows few of its own words."""
        words = list(dict.fromkeys(tokens(str(query or "")[:QUERY_CHARS])))
        return sum(1 for w in words if w in self._idf), len(words)

    def search(self, query: Any, limit: int = 5) -> list[tuple[float, Capability]]:
        """The best matches for *query*, best first; ``[]`` for a query with
        no search words. Ties keep a fixed order (kind, then id), so a search
        is repeatable."""
        words = list(dict.fromkeys(tokens(str(query or "")[:QUERY_CHARS])))
        if not words:
            return []
        k1, b = 1.5, 0.75
        scored: list[tuple[float, int]] = []
        for at, counts in enumerate(self._docs):
            score = 0.0
            for word in words:
                tf = counts.get(word, 0)
                if not tf:
                    continue
                norm = tf + k1 * (1 - b + b * self._lengths[at] / self._avg)
                score += self._idf.get(word, 0.0) * tf * (k1 + 1) / norm
            if score > 0:
                scored.append((score, at))
        scored.sort(key=lambda s: (-s[0], KINDS.index(self.entries[s[1]].kind), self.entries[s[1]].id))
        return [(score, self.entries[at]) for score, at in scored[:clamp(limit)]]


def clamp(limit: Any) -> int:
    """A result count between 1 and :data:`MAX_RESULTS` for any input."""
    try:
        n = int(limit)
    except (TypeError, ValueError, OverflowError):
        n = 5
    return max(1, min(n or 5, MAX_RESULTS))


def with_phrasings(entries: list[Capability]) -> list[Capability]:
    """*entries* with each one's everyday phrasings (``phrasings.py``) added to
    the words it is found by. An entry with none keeps its own text."""
    from dataclasses import replace

    from .phrasings import PHRASINGS

    out = []
    for entry in entries:
        extra = PHRASINGS.get(entry.id)
        out.append(replace(entry, text=f"{entry.text} {' '.join(extra)}") if extra else entry)
    return out


def build(platform: Any, *, phrasings: bool = True) -> CapabilityIndex:
    """The index for this install. A part that cannot be read contributes no
    entries and is NAMED on the index (``unreadable``); the rest is built.
    ``phrasings=False`` builds from the product's own text alone (what the
    phrasings are written from)."""
    entries: list[Capability] = []
    unreadable: list[str] = []
    for name, part in PARTS:
        try:
            entries.extend(part(platform))
        except Exception:  # noqa: BLE001 — one unreadable part never empties the index
            unreadable.append(name)
    if phrasings:
        try:
            entries = with_phrasings(entries)
        except Exception:  # noqa: BLE001 — the product's own words still find everything they did
            unreadable.append("phrasings")
    return CapabilityIndex(entries, tuple(unreadable))


def _signature(platform: Any) -> tuple:
    registry = getattr(platform, "registry", None)
    try:
        return (tuple(sorted(registry.names())), tuple(sorted(registry.custom_names())))
    except Exception:  # noqa: BLE001
        return ()


def index_for(platform: Any) -> CapabilityIndex:
    """The index, rebuilt only when the set of registered tools changed (a
    tool pack loading, a new command tool). Kept on the platform object. An
    index with an unreadable part is never kept: the next lookup tries again.
    Two lookups building at once each build their own; the last one is kept."""
    signature = _signature(platform)
    kept = getattr(platform, "_capability_index", None)
    if kept is not None and signature and kept[0] == signature:
        return kept[1]
    built = build(platform)
    if signature and not built.unreadable:
        try:
            platform._capability_index = (signature, built)
        except Exception:  # noqa: BLE001 — a platform that takes no attribute is rebuilt each time
            pass
    return built


def search(platform: Any, query: Any, limit: int = 5) -> list[Capability]:
    """What matches *query*, best first. Never raises: a fault is ``[]``.
    (The tool does not use this: it must tell a fault from "nothing
    matches", so it reads the index itself.)"""
    try:
        return [entry for _score, entry in index_for(platform).search(query, limit)]
    except Exception:  # noqa: BLE001
        return []
