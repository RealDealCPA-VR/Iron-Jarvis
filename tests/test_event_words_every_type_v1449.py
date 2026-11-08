"""v1.449.0 (readiness plan 0h.5 item 14): EVERY event type the daemon
publishes has plain words on the Activity page and on the phone.

The Activity page printed the raw event id ("trust.lowered",
"webhook.rejected") for 67 of the 87 ``EventType`` members: the words table
(``comm/notifier.EVENT_WORDS``, twin ``dashboard/lib/eventWords.EVENT_WORDS``)
held 20 rows. The ``tests/test_background_names_v13550`` pattern, applied
here: a NEW event type with no words FAILS this file, and a row for a type
the daemon never publishes fails it too (a stale row is a lie waiting to
read wrong). The vitest half (``dashboard/__tests__/event-words-every-type-
v1449.test.ts``) reads ``core/events.py`` the same way.
"""

from __future__ import annotations

import re
from pathlib import Path

from iron_jarvis.comm.notifier import EVENT_WORDS, format_event
from iron_jarvis.core.events import EventType, OTHER_PUBLISHED_TYPES, known_event_types

ROOT = Path(__file__).resolve().parents[1]
EVENT_WORDS_TS = ROOT / "dashboard" / "lib" / "eventWords.ts"
RETIRED_TS = ROOT / "dashboard" / "__tests__" / "helpers" / "retiredWords.ts"


def _ts_words() -> dict[str, str]:
    src = EVENT_WORDS_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    block = src[src.index("export const EVENT_WORDS") : src.index("};", src.index("export const EVENT_WORDS"))]
    return dict(re.findall(r'"([a-z_.]+)":\s*"([^"]+)"', block))


def _event_type_members() -> set[str]:
    return {v for k, v in vars(EventType).items() if k.isupper() and isinstance(v, str) and "." in v}


def test_the_scan_finds_the_daemons_event_types_at_all() -> None:
    members = _event_type_members()
    assert len(members) >= 80, sorted(members)
    assert {"session.completed", "webhook.rejected", "trust.lowered"} <= members
    assert known_event_types() == members | OTHER_PUBLISHED_TYPES


def test_every_event_type_the_daemon_publishes_has_words() -> None:
    missing = sorted(t for t in known_event_types() if not EVENT_WORDS.get(t))
    assert not missing, (
        f"these event types have no row in comm/notifier.EVENT_WORDS (and so none in "
        f"dashboard/lib/eventWords.ts), so the Activity page prints their raw id: {missing}"
    )


def test_the_table_names_no_type_the_daemon_never_publishes() -> None:
    stale = sorted(set(EVENT_WORDS) - known_event_types())
    assert not stale, f"rows in EVENT_WORDS for event types the daemon never publishes: {stale}"


def test_the_dashboard_twin_holds_the_same_rows() -> None:
    ts = _ts_words()
    assert ts == EVENT_WORDS
    assert set(ts) == known_event_types()


def test_every_headline_is_words_never_an_id_a_path_or_a_retired_word() -> None:
    src = RETIRED_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    retired = [
        (word, re.compile(rx, re.I))
        for word, rx in re.findall(r"\{ word: \"([^\"]+)\", re: /(.+?)/i, use:", src)
    ]
    assert len(retired) >= 10, "read too little of retiredWords.ts"
    for etype, words in EVENT_WORDS.items():
        assert words[0].isupper(), (etype, words)
        assert not re.search(r"\b[a-z_]+\.[a-z_]+\b", words), (etype, words)  # never an event id
        assert "/" not in words and "\\" not in words and "_" not in words, (etype, words)
        hits = [w for w, rx in retired if rx.search(words)]
        assert not hits, f"{etype}: {words!r} uses a retired word {hits}"


def test_a_worded_type_is_a_sentence_on_the_phone_never_its_name() -> None:
    line = format_event({"type": "webhook.rejected", "payload": {"slug": "github-push", "reason": "bad_signature"}})
    assert line.startswith("Iron Jarvis: A webhook was refused"), line
    assert "webhook.rejected" not in line
    line = format_event({"type": "trust.lowered", "payload": {"tool": "web_fetch", "category": "injection"}})
    assert "trust.lowered" not in line and "Trust was lowered" in line
