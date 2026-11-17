"""Owner decision 7 after the third fix plan: why a "Resume when ready" wait
ENDED, and the day a passing refusal is given, are kept WITH the stored card
and wait (``setup.account_limit.wait_ended``, ``setup.account_wait.refused_at``)
— never in the daemon's memory alone. After a restart the card still says why
the wait ended, and a refusal that has gone on for most of a day is not given
a new day.

Each test writes the state through the real wait loop (``resume_due``) and
the real start, then "restarts": the daemon's memory of group 23 is emptied
(as a new process has it) and a NEW app reads the stored row. The account
check and the account service are stand-ins (no Iron-Proxy). No sleeps: the
loop's clock is passed in.
"""

from __future__ import annotations

import shutil
from datetime import timedelta

from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import sessions as S
from tests.test_build_resume_v1391 import _reboot, home, work  # noqa: F401 — fixtures
from tests.test_build_sessions_v1367 import Script, asgi, asgi_get, new_session, setup_of, until, wire
from tests.test_third_plan_g23_coding_routes import ACCOUNT, _armed, accounts_on  # noqa: F401 — fixture


def _restart(home: str):  # noqa: F811
    """A new process: nothing of the old one's memory, a new app on the same
    home (its boot reconciles run)."""
    for name in ("_WAIT_ENDED", "_WAIT_REFUSED"):
        kept = getattr(build_routes, name, None)
        if isinstance(kept, dict):
            kept.clear()
    return _reboot(home)


async def _card_view(app, sid: str) -> dict:
    status, view = await asgi_get(app, f"/build/sessions/{sid}")
    assert status == 200, view
    return view.get("account_limit") or {}


async def test_why_a_wait_ended_is_still_said_after_a_restart(home, work, accounts_on):  # noqa: F811
    first = create_app(home)
    sid = new_session(first, work)["id"]
    now = utcnow()
    _armed(first, sid, due_at=now - timedelta(minutes=1))
    shutil.rmtree(work)  # a lasting refusal: the session's folder is gone

    assert await build_routes.resume_due(first.state.d, now=now) == []
    before = (await _card_view(first, sid)).get("wait_ended") or ""
    assert before.startswith("Iron Jarvis stopped waiting to carry this reply on, because it cannot start: ")

    second = _restart(home)
    stored = setup_of(second, sid)
    assert "account_wait" not in stored
    assert stored.get("account_limit", {}).get("wait_ended") == before, \
        "why the wait ended was not written with the stored card"
    assert (await _card_view(second, sid)).get("wait_ended") == before, \
        "after a restart the card no longer says why the wait ended"


async def test_a_passing_refusal_keeps_its_day_across_a_restart(home, work, accounts_on):  # noqa: F811
    first = create_app(home)
    sid = new_session(first, work)["id"]
    now = utcnow()
    _armed(first, sid, due_at=now - timedelta(minutes=1))
    build_routes._COMPACTING.add(sid)  # Compact holds the session: a passing refusal
    try:
        assert await build_routes.resume_due(first.state.d, now=now) == []
        assert "account_wait" in setup_of(first, sid)
        # Most of the day goes by; then the daemon restarts.
        later = now + timedelta(seconds=build_routes.WAIT_GIVE_UP_S - 60)
        assert await build_routes.resume_due(first.state.d, now=later) == []
        assert "account_wait" in setup_of(first, sid), "ended before its day"

        second = _restart(home)
        stored = setup_of(second, sid)["account_wait"]
        assert stored.get("refused_at") == now.isoformat(), stored
        # A day after the FIRST refusal (before the restart) the wait ends.
        after = now + timedelta(seconds=build_routes.WAIT_GIVE_UP_S + 1)
        assert await build_routes.resume_due(second.state.d, now=after) == []
    finally:
        build_routes._COMPACTING.discard(sid)
    assert "account_wait" not in setup_of(second, sid), "the restart gave the refused wait a new day"
    assert (await _card_view(second, sid)).get("wait_ended") == build_routes.WAIT_GAVE_UP_WORDS.format(
        why=build_routes.COMPACTING_SENTENCE,
    )


async def test_the_stored_words_go_when_the_user_waits_again_or_sends(home, work, accounts_on, monkeypatch):  # noqa: F811
    first = create_app(home)
    sid = new_session(first, work)["id"]
    now = utcnow()
    _armed(first, sid, due_at=now - timedelta(minutes=1))
    shutil.rmtree(work)
    assert await build_routes.resume_due(first.state.d, now=now) == []
    work.mkdir()  # the folder is back: the user fixed it

    second = _restart(home)
    assert (await _card_view(second, sid)).get("wait_ended")
    # Resume when ready again: the words go, and a Cancel does not bring them back.
    status, _ = await asgi(second, "POST", f"/build/sessions/{sid}/resume-when-ready", {})
    assert status == 200
    assert "wait_ended" not in await _card_view(second, sid)
    assert "wait_ended" not in setup_of(second, sid).get("account_limit", {})
    status, _ = await asgi(second, "DELETE", f"/build/sessions/{sid}/resume-when-ready", None)
    assert status == 200
    assert setup_of(second, sid).get("account_limit", {}).get("account") == ACCOUNT, "the card stays"
    assert "wait_ended" not in await _card_view(second, sid), "the old words came back after a Cancel"

    # Ended again, then the user's own message: the words go when it starts.
    _armed(second, sid, due_at=now - timedelta(minutes=1))
    S.update(second.state.platform.engine, sid, setup={"account_wait": None, "account_limit": {
        **setup_of(second, sid)["account_limit"], "wait_ended": "Iron Jarvis stopped waiting.",
    }})
    third = _restart(home)
    assert (await _card_view(third, sid)).get("wait_ended") == "Iron Jarvis stopped waiting."
    monkeypatch.setattr(build_routes, "turn_account", lambda *_a: (None, ""))
    wire(third, Script(["Carried on."]))
    lg = await build_routes.start_build_turn(third.state.d, sid, "go on", "bt-own")
    assert "wait_ended" not in setup_of(third, sid).get("account_limit", {}), \
        "a reply started and the card still said why an earlier wait ended"
    await until(lambda: lg.status != "running")


def test_the_stored_keys_survive_the_setup_cleaner():
    import json

    from iron_jarvis.daemon.routes.chat import _clean_setup

    words = build_routes.WAIT_ENDED_WORDS.format(why="x " * 150)
    raw = {"account": ACCOUNT, "account_limit": {"account": ACCOUNT, "wait_ended": words},
           "account_wait": {"account": ACCOUNT, "armed_at": "2026-11-17T00:00:00+00:00",
                            "refused_at": "2026-11-17T01:00:00+00:00"}}
    cleaned = json.loads(_clean_setup(raw))
    assert cleaned["account_limit"]["wait_ended"] == " ".join(words.split())[:600]
    assert len(cleaned["account_limit"]["wait_ended"]) > 200, "the words were cut at the other keys' 200"
    assert cleaned["account_wait"]["refused_at"] == "2026-11-17T01:00:00+00:00"
