"""Fourth review, group 26, convenience C-CS-3: the attempts notice says
"choose one".

Before: a daytime "Try on several models" turn that ended with one or more
attempts finished was announced "Your coding session finished, but something
needs you" (outcome "needs_you", fourth review CS3), which does not say what.
Now its outcome is "choose", and the notifier (the phone), the desktop tray
and the bell all say "Your attempts are ready: choose the one to keep",
opening the session where the attempts card is. When no attempt finished it
stays "failed" ("Your coding session could not finish"). A night task's
attempts are unchanged: the night's one summary says how each ended.

Driven through the REAL reply watch (``recorded_turn``) with an injected
clock, the REAL notifier, and the REAL ``desktop/main.js`` tray functions
under node (the one-notice harness). Nothing reaches a real model.
"""

from __future__ import annotations

import pytest

from iron_jarvis.comm.notifier import build_finished_title, format_event
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import attempts as A
from tests.test_one_notice_v13600 import _NOTICES, _node, requires_node
from tests.test_third_plan_g20_small_truths import Clock, _notices_after

CHOOSE_TITLE = "Your attempts are ready: choose the one to keep"
FAILED_TITLE = "Your coding session could not finish"

#: Every outcome ``finished_notice_outcome`` can give, and its one headline.
TITLES = {
    "done": "Your coding session finished",
    "failed": FAILED_TITLE,
    "limit": "Your coding session stopped at its limit and needs you",
    "needs_you": "Your coding session finished, but something needs you",
    "choose": CHOOSE_TITLE,
}


def _card(*states: str) -> dict:
    return {"task": "write the notes file", "items": [
        {"id": f"try{n}", "label": f"Model {n}", "state": s, "why": "finished" if s == "done" else "failed",
         "words": "Finished" if s == "done" else "Failed: it stopped on an error"}
        for n, s in enumerate(states, start=1)
    ]}


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(build_routes, "time", c)
    return c


@pytest.fixture()
def coding(tmp_path):
    from tests.test_build_sessions_v1367 import new_session

    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    return app, new_session(app, work, title="Tidy the notes")["id"]


@pytest.mark.parametrize(
    ("states", "outcome", "title"),
    [
        (("done", "failed"), "choose", CHOOSE_TITLE),
        (("done",), "choose", CHOOSE_TITLE),
        (("done", "done"), "choose", CHOOSE_TITLE),
        (("failed", "failed"), "failed", FAILED_TITLE),
    ],
    ids=["one-of-two-finished", "the-only-one-finished", "both-finished", "none-finished"],
)
async def test_an_attempts_turn_with_a_finished_attempt_asks_the_user_to_choose(
    coding, clock, states, outcome, title,
):
    app, sid = coding
    card = _card(*states)
    frame = {"reply": A.summary_words(card), A.LINE_KEY: card}
    [notice] = await _notices_after(app, sid, f"att-{'-'.join(states)}", clock, ("done", frame))
    assert notice["outcome"] == outcome, notice
    assert build_finished_title(notice) == title
    # The phone's line (a destination that subscribed the type) says the same.
    line = format_event({"type": EventType.BUILD_TURN_FINISHED, "session_id": f"build:{sid}", "payload": notice})
    assert line.startswith(title), line


def test_every_outcome_has_its_own_headline_on_the_phone():
    for outcome, title in TITLES.items():
        assert build_finished_title({"outcome": outcome}) == title, outcome
    assert len(set(TITLES.values())) == len(TITLES), "two endings share one headline"


@requires_node
def test_the_tray_says_every_ending_in_the_bells_words_and_opens_the_session(tmp_path):
    cases = [
        {"event": {"id": f"evt_{o}", "type": "build.turn_finished", "session_id": "build:bs_try",
                   "ts": "2026-11-18T09:00:00",
                   "payload": {"thread_id": "bs_try", "turn_id": "bt_try", "title": "Write the notes",
                               "seconds": 150, "outcome": o}}}
        for o in TITLES
    ]
    out = _node(_NOTICES, tmp_path, {"cases": cases})["out"]
    for (outcome, title), notice in zip(TITLES.items(), out):
        assert notice is not None, outcome
        assert notice["title"] == title, (outcome, notice)
        assert notice["href"] == "/chat?view=code&session=bs_try", notice
        assert notice["body"] == "Write the notes · took 2 min 30 s", notice
