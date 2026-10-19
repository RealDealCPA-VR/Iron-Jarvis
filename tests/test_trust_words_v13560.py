"""v1.356.0 (G77) — hovering "low trust" says what the run cannot do.

The dashboard's ``lib/trustWords.ts`` groups every tool a LOW-trust run is
kept away from into the families its sentence names (settings, schedules,
rules, connections, memory, ...). This pin reads that file AND the daemon's
real ``core.trust.LOW_TRUST_DENY``: a tool added to the deny set without a
family in the dashboard words (or a family naming a tool the set dropped)
fails here, so the hover can never quietly fall behind what is blocked.
"""

from __future__ import annotations

import re
from pathlib import Path

from iron_jarvis.core.trust import LOW_TRUST_DENY

WORDS = Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "trustWords.ts"


def _source() -> str:
    # A source pin normalises CRLF (a Windows runner checks out with it).
    return WORDS.read_text(encoding="utf-8").replace("\r\n", "\n")


def _families() -> dict[str, set[str]]:
    src = _source()
    block = src[src.index("export const LOW_TRUST_FAMILIES") : src.index("export const LOW_TRUST_CANNOT")]
    out: dict[str, set[str]] = {}
    for m in re.finditer(r'words:\s*"([^"]+)",\s*tools:\s*\[([^\]]*)\]', block):
        out[m.group(1)] = set(re.findall(r'"([a-z_]+)"', m.group(2)))
    assert out, "no families parsed out of lib/trustWords.ts"
    return out


def _sentence() -> str:
    src = _source()
    start = src.index("export const LOW_TRUST_CANNOT")
    end = src.index(";", start)
    return "".join(re.findall(r'"([^"]*)"', src[start:end]))


def test_every_kept_away_tool_has_a_family_in_the_dashboard_words():
    named = set().union(*_families().values())
    missing = sorted(set(LOW_TRUST_DENY) - named)
    assert missing == [], (
        f"core/trust.LOW_TRUST_DENY keeps {missing} away from a low-trust run, but "
        "dashboard/lib/trustWords.ts names no family for them — add them to a family "
        "(and the sentence, if it is a new kind of thing)"
    )


def test_no_family_names_a_tool_the_daemon_does_not_keep_away():
    named = set().union(*_families().values())
    assert sorted(named - set(LOW_TRUST_DENY)) == []


def test_each_tool_sits_in_exactly_one_family():
    seen: dict[str, str] = {}
    for words, tools in _families().items():
        for t in tools:
            assert t not in seen, f"{t} is in both {seen[t]!r} and {words!r}"
            seen[t] = words


def test_the_sentence_says_every_family():
    sentence = _sentence()
    for word in ("settings", "schedules", "rules", "connections", "memory", "agents or skills"):
        assert word in sentence, word
    # The two families that are not "change X" are said by what they stop.
    assert "terminals" in sentence
    assert "spend" in sentence
    for family in _families():
        if family in ("terminals", "spending"):
            continue
        assert family in sentence, family
