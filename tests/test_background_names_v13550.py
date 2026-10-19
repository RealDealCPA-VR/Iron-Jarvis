"""v1.355.0 (product-readiness G32): background problems are named.

The bell, Everything > Status and the chat home name a failing background
loop through ONE table, ``dashboard/lib/loopWords.ts`` ("slack_socket" ->
"Slack messages aren't arriving", opening the channels page). Two promises
live on the daemon's side of that table, so they are pinned here, where a
Python change runs:

1. EVERY name the daemon can put in ``loop_health`` (a ``_tick("x", ...)``
   loop, a ``_rehydrate_step("x", ...)`` boot step, a direct
   ``loop_health["x"] = ...`` write) has a row in the table. A new loop added
   without words would otherwise reach the user as "A background task isn't
   working" with no door to its fix.
2. The home line's "Ask Jarvis" question (``HEALTH_QUESTION``) really arms
   the health readers (app_health, run_doctor, pack_status) through the chat
   lanes' own tool choice, so the answer reads the app's health instead of
   guessing.

The TS file is read as TEXT (CRLF normalised: a Windows runner checks it out
with CRLF endings).
"""

from __future__ import annotations

import re
from pathlib import Path

from iron_jarvis.tools.autoselect import select_auto_tools

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "iron_jarvis" / "daemon" / "app.py"
LOOP_WORDS = ROOT / "dashboard" / "lib" / "loopWords.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _daemon_loop_names() -> set[str]:
    src = _read(APP)
    names: set[str] = set()
    for pattern in (
        r"_tick\(\s*\"([a-z_]+)\"",
        r"_rehydrate_step\(\s*\"([a-z_]+)\"",
        r"loop_health\[\"([a-z_]+)\"\]",
    ):
        names.update(re.findall(pattern, src))
    return names


def _table_keys() -> set[str]:
    src = _read(LOOP_WORDS)
    start = src.index("const ROWS:")
    end = src.index("\n};", start)
    return set(re.findall(r"^\s{2}([a-z_]+): \{", src[start:end], flags=re.MULTILINE))


def _health_question() -> str:
    m = re.search(r'export const HEALTH_QUESTION = "([^"]+)";', _read(LOOP_WORDS))
    assert m, "HEALTH_QUESTION is not a plain string constant in lib/loopWords.ts"
    return m.group(1)


def test_the_scan_finds_the_daemons_loops_at_all() -> None:
    # Anti-vacuity: the regexes must see the loops this wave was about, or
    # the coverage check below passes on an empty set.
    names = _daemon_loop_names()
    for known in ("slack_socket", "inbound", "scheduler", "fleet", "iron_proxy", "dispatcher",
                  "reconcile_media_jobs", "auto_backup", "rehydrate_goals", "rehydrate_terminals"):
        assert known in names, known
    assert len(names) >= 25


def test_every_loop_the_daemon_reports_has_words_and_a_door() -> None:
    missing = sorted(_daemon_loop_names() - _table_keys())
    assert not missing, (
        "these loop_health names have no row in dashboard/lib/loopWords.ts, so the bell "
        f"and Status would call them 'A background task': {missing}"
    )


def test_the_table_names_no_loop_the_daemon_never_reports() -> None:
    # A row for a name nothing writes is a stale promise (a renamed loop).
    stale = sorted(_table_keys() - _daemon_loop_names())
    assert not stale, f"rows in lib/loopWords.ts for names the daemon never reports: {stale}"


def test_the_home_lines_question_arms_the_health_readers() -> None:
    q = _health_question()
    assert q == "Why is a background task failing?"
    armed = select_auto_tools(q)
    for reader in ("app_health", "run_doctor", "pack_status"):
        assert reader in armed, (reader, armed)
    # And nothing that changes anything: the question only READS.
    assert "pack_restart" not in armed
    assert "provider_rescan" not in armed
