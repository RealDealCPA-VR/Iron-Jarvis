"""The Spotlight quick task's final toast said the STATUS TABLE's word (v1.449.0).

The readiness audit (docs/PRODUCT-READINESS-RESULTS.md 0h.5 item 14): the
desktop's quick-task watcher read only ``status`` off the slim session row,
so a completed run whose ledger verdict was ``needs_you`` or
``completed_with_failures`` was announced "Task done", and a stopped task
"Task cancelled" — words no other surface uses.

RETIRED IN v1.506.0 (north-star fix plan 6.5, decision D11, moved on
purpose): the quick window no longer starts an agent run (``POST /sessions``
with agent_type "builder"); its words open in CHAT in the main window, where
the answer, any card and any hand-off to a run are on screen. So there is no
run for the desktop to watch and no verdict toast to word: the watcher
(``watchSpotlightTask``), its outcome reader, ``SPOTLIGHT_OUTCOME_WORDS`` and
the toast's folder offer were removed with the route, and the node pins that
lifted them went with them. The new behaviour is pinned by
``tests/test_north_star_quick_window_v1506.py`` (desktop) and
``dashboard/__tests__/north-star-quick-window-v1506.test.tsx`` (the chat
page). What stays true is pinned here.
"""

from __future__ import annotations

from tests.test_desktop_idle_v1333 import _src


def test_the_old_words_are_gone_from_the_desktop():
    src = _src()
    for old in ("Task done", "Task cancelled", "Task failed"):
        assert old not in src, f"main.js still says {old!r}"


def test_the_quick_window_announces_no_run_verdict():
    """No "Done: <words>" toast over a chat the user is looking at."""
    src = _src()
    for gone in ("watchSpotlightTask", "SPOTLIGHT_OUTCOME_WORDS", "spotlightToastPlan", "`Still running: "):
        assert gone not in src, gone
