"""v1.329.0 (calm chat wave 8, J4): the built-in agents describe themselves
in plain words, with no dash aside.

The closing audit found the Guide's roster line, "the Iron Jarvis expert —
explains the app, finds your things in it", on the mission screen's Team list
AND in the chat @ menu (both read ``build_roster`` → ``RosterEntry.description``).
Every built-in description is a plain phrase now; this pins the Guide's words
and scans every built-in line (and the rendered roster block) for a spaced em
or en dash, so a new built-in cannot bring one back.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from iron_jarvis.agents import roster as roster_mod
from iron_jarvis.agents.roster import build_roster, roster_block

#: An em or en dash used as an aside: spaced, or at the edge of the text.
_ASIDE = re.compile("(^|\\s)[—–](\\s|$)")


class _Reg:
    def __init__(self, records=()):
        self._records = list(records)

    def list(self):
        return list(self._records)


class _Improvement:
    def stats(self):
        return {"lessons": [], "agents": [], "outcomes": {"count": 0}}


def _platform():
    return SimpleNamespace(agents_registry=_Reg(), remote_agents=_Reg(), improvement=_Improvement())


def test_the_guide_describes_itself_in_plain_words():
    entries = {e.name: e for e in build_roster(_platform())}
    assert entries["guide"].description == "the Iron Jarvis expert who explains the app and finds your things in it"


def test_no_builtin_description_carries_a_dash_aside():
    bad = {k: v for k, v in roster_mod._BUILTIN_STRENGTHS.items() if _ASIDE.search(v)}
    assert bad == {}
    # What the user actually reads (the mission Team list, the @ menu): every
    # built-in roster entry's description. The prompt block's own
    # "name — description" separator is the model's format, not user copy,
    # so only the description inside it is checked.
    builtins = [e for e in build_roster(_platform()) if e.kind == "builtin"]
    assert builtins
    for e in builtins:
        assert not _ASIDE.search(e.description or ""), (e.name, e.description)
    block = roster_block(_platform())
    assert "guide — the Iron Jarvis expert who explains" in block


def test_the_scan_sees_a_dash_aside_anti_vacuity():
    assert _ASIDE.search("the Iron Jarvis expert — explains the app")
    assert not _ASIDE.search("the Iron Jarvis expert who explains the app and finds your things in it")
