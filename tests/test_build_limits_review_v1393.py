"""v1.393.0 REVIEW of Phase 4 (limits without a press). Each pin was red on
the merged doer branch before its fix (the mutation that brings it back is
named in the test's docstring).

Driven like ``tests/test_build_limits_v1393.py``: the REAL app factory, the
real ``/build/sessions/{id}/turn`` lane, the real router, the real wait-loop
pass and the real Iron-Proxy service/client against the fake Iron-Proxy; only
the two subscription adapters are doubles (``AccountDouble``).

DECISIONS (the brief's two judgment calls):

1(a) WHICH account takes over. An account the vendor has sent NO usage numbers
     for may be the one at its limit (numbers arrive only after a call), so it
     is not "0% used": every account with known numbers below 95% comes first
     (lowest first), then the ones with no numbers (Iron-Proxy's order). An
     unknown account IS still tried before the pinned account's own reset: it
     is in the group the user made, most such accounts are simply unused, a
     wrong guess costs one refused call, the line says what happened, and the
     move cap (4 in a row with nothing served) bounds the chain.

1(b) "Carry on by itself after an account's limit" OFF means NOTHING carries
     on by itself — no wait and no move within a group (a move IS a reply that
     carries on by itself, on another account). The card waits for the press;
     after the press, a group account may take over (the press is the consent)
     and the card says so (the session view's ``account_wait.group`` is the
     LIVE group, the loop's own rule).
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.settings import schema
from tests.test_build_account_turns_v1368 import _events, _finish, _session, _turn
from tests.test_build_accounts_v1368 import Behaviour, enable, iso, isolate, make_fake, wire_doubles
from tests.test_build_limits_v1393 import _groups, _later, _limit, _reset
from tests.test_build_sessions_v1367 import asgi, messages_of, setup_of


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    f.park_seconds = 600
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _windows(fake, pid: str, percent: float) -> None:
    fake.states[pid]["windows"] = [
        {"kind": "5h", "usedPercent": percent, "windowMins": 300, "resetsAt": iso(3600), "source": "headers"},
    ]


async def _refreshed(app) -> None:
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)


# --------------------------------------------------------------------------- #
# 1(a) a known-low account before one with no numbers
# --------------------------------------------------------------------------- #


async def test_a_known_low_account_takes_over_before_one_with_no_numbers(app, fake):
    """Mutation: score an unknown account as 0% again (the doer's rule) —
    c-spare (no numbers) wins over c-third (20%)."""
    await enable(app)
    fake.add_profile("anthropic", "Third", id="c-third")
    _windows(fake, "c-third", 20)
    await _refreshed(app)
    svc = app.state.platform.iron_proxy
    groups = {"Work": ["c-work", "c-spare", "c-third"]}
    assert BA.peak_percent(fake.states["c-spare"]) is None, "c-spare has no numbers"
    assert BA.takeover(svc, groups, "c-work")["id"] == "c-third"
    # Even a known HIGH account (below 95%) comes before an unknown one.
    _windows(fake, "c-third", 90)
    await _refreshed(app)
    target = BA.takeover(svc, groups, "c-work")
    assert (target["id"], target["percent"]) == ("c-third", 90)


async def test_an_account_with_no_numbers_still_takes_over_when_nothing_known_can(app, fake):
    await enable(app)
    fake.add_profile("anthropic", "Third", id="c-third")
    _windows(fake, "c-third", 97)
    await _refreshed(app)
    target = BA.takeover(app.state.platform.iron_proxy, {"Work": ["c-work", "c-spare", "c-third"]}, "c-work")
    assert target["id"] == "c-spare" and target["percent"] is None


# --------------------------------------------------------------------------- #
# 1(b) the setting OFF: nothing carries on by itself, not even a move
# --------------------------------------------------------------------------- #


async def test_with_the_setting_off_nothing_carries_on_by_itself_not_even_a_move(app, fake, work, monkeypatch):
    """Mutation: arm the `once` move-only wait again when the setting is off
    and a group is made — the reply carries on with c-spare by itself."""
    app.state.platform.config.build_auto_resume = False
    beh, sid = await _limit(app, fake, work, monkeypatch, moves=("LIMIT", "Carried on with Spare."),
                            groups={"Work": ["c-work", "c-spare"]})
    setup = setup_of(app, sid)
    assert "account_wait" not in setup and setup["account_limit"]["account"] == "c-work"
    # The limit is a question for the user (the card waits for the press).
    [said] = _events(app, "build.account_limit")
    assert said["waiting"] is False
    for _ in range(2):
        assert await build_routes.resume_due(app.state.d) == []
    assert len(beh.calls) == 1 and setup_of(app, sid)["account"] == "c-work"
    assert _events(app, "build.resumed") == []
    # The PRESS is the consent: the wait it arms may then move within the
    # group — and the card says so (the live group).
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/resume-when-ready")
    assert status == 200 and view["account_wait"]["group"] == "Work"
    assert await build_routes.resume_due(app.state.d) == [sid]
    event = _events(app, "build.resumed")[-1]
    assert (event["moved_from"], event["account"]) == ("c-work", "c-spare")
    await _finish(app, event["turn_id"])
    assert messages_of(app, sid)[-1]["content"] == "Carried on with Spare."
    # Said once: the press's limit was already on the bell.
    assert len(_events(app, "build.account_limit")) == 1


def test_the_settings_words_say_off_means_nothing_carries_on_by_itself():
    d = schema.get("build_auto_resume")
    assert "another account of that group" in d.help
    assert "Off: nothing carries on by itself" in d.help


# --------------------------------------------------------------------------- #
# the card's words follow the LIVE group (the loop's own rule)
# --------------------------------------------------------------------------- #


async def test_the_waiting_card_names_the_live_group(app, fake, work, monkeypatch):
    """Mutation: drop the view's live group — a wait armed before the group
    was made (or by the press) says nothing of the group while the loop moves
    the reply to it."""
    beh, sid = await _limit(app, fake, work, monkeypatch)
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["account_wait"]["auto"] is True and "group" not in view["account_wait"]
    # The group is made AFTER the wait was armed: the loop would move it.
    assert (await _groups(app, {"Work": ["c-work", "c-spare"]}))[0] == 200
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["account_wait"]["group"] == "Work"
    # Only the stored record is the loop's; the view adds the words.
    assert "group" not in setup_of(app, sid)["account_wait"]
    # Taken out of the group: the words go with it.
    assert (await _groups(app, {"Work": ["c-spare", "c-gone"]}))[0] == 200
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert "group" not in view["account_wait"]


# --------------------------------------------------------------------------- #
# the move cap counts moves with NOTHING served
# --------------------------------------------------------------------------- #


def _read(work) -> ToolCall:
    return ToolCall(id="r1", name="Read", arguments={"file_path": str(work / "app.py")})


async def test_an_account_that_served_a_call_before_its_limit_resets_the_move_count(app, fake, work, monkeypatch):
    """A long job through a group of accounts keeps moving: the cap is for a
    chain of accounts that fail at once (a stale snapshot), not for accounts
    that each did work. Mutation: keep `account_moves` when the limited turn
    had billed model calls — at the cap, no move."""
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour([[_read(work)], "LIMIT", "Spare carried on."]))
    assert (await _groups(app, {"Work": ["c-work", "c-spare"]}))[0] == 200
    sid = await _session(app, work)
    S.update(app.state.platform.engine, sid, setup={"account_moves": build_routes.MAX_GROUP_MOVES})
    frames = await _turn(app, sid, "read the app", "srv-1")
    assert [e for e, _ in frames][-2:] == ["account_limit", "error"]
    await _finish(app, "srv-1")
    assert len(beh.calls) == 2, "one billed call, then the limit"
    setup = setup_of(app, sid)
    assert "account_moves" not in setup, "c-work served a call: the chain starts again"
    assert setup["account_wait"]["group"] == "Work"
    assert await build_routes.resume_due(app.state.d) == [sid]
    assert _events(app, "build.resumed")[-1]["account"] == "c-spare"


async def test_a_turn_that_failed_at_once_keeps_the_move_count(app, fake, work, monkeypatch):
    """The other half: a limit on the FIRST call served nothing — the cap
    still ends the chain on a wait."""
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(["LIMIT"]))
    assert (await _groups(app, {"Work": ["c-work", "c-spare"]}))[0] == 200
    sid = await _session(app, work)
    S.update(app.state.platform.engine, sid, setup={"account_moves": build_routes.MAX_GROUP_MOVES})
    await _turn(app, sid, "go", "srv-2")
    await _finish(app, "srv-2")
    setup = setup_of(app, sid)
    assert setup["account_moves"] == build_routes.MAX_GROUP_MOVES
    assert "group" not in setup["account_wait"]
    assert await build_routes.resume_due(app.state.d) == []
    assert setup_of(app, sid)["account"] == "c-work"


async def test_choosing_an_account_starts_the_move_count_again(app, fake, work, monkeypatch):
    """The user's own pick is not an automatic move. Mutation: leave
    `account_moves` out of account_patch."""
    beh, sid = await _limit(app, fake, work, monkeypatch)
    S.update(app.state.platform.engine, sid, setup={"account_moves": 3})
    status, body = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "c-spare"})
    assert status == 200, body
    assert "account_moves" not in setup_of(app, sid)


# --------------------------------------------------------------------------- #
# privacy
# --------------------------------------------------------------------------- #


async def test_an_account_removed_and_added_again_is_in_no_group(app, fake, work, monkeypatch):
    """Groups are by Iron-Proxy's account id (random, never reused): the
    same title added again is a NEW account the user has not grouped."""
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Work": ["c-work", "c-spare"]})
    # Before the loop looks: c-spare is removed and added again.
    del fake.profiles["c-spare"]
    fake.states.pop("c-spare", None)
    fake.add_profile("anthropic", "Spare", id="c-spare-new")
    await _refreshed(app)
    for _ in range(2):
        assert await build_routes.resume_due(app.state.d) == []
    assert setup_of(app, sid)["account"] == "c-work"
    assert all(lease[1] == "c-work" for lease in beh.leases)


def test_account_groups_always_ask_and_no_grant_lifts_them():
    """Which accounts may take over each other's conversations is the user's
    privacy decision (work and personal never mix): from chat it is the
    PROTECTED tool, carded in every posture (yolo too) and never lifted by a
    standing grant. Mutation: tier "ask" — config_change runs it uncarded
    under yolo."""
    from iron_jarvis.settings.tools import ALWAYS_CARD_TOOLS, TOOL_FOR_TIER

    d = schema.get("account_groups")
    assert d.tier_for({"All": ["a", "b"]}) == "ask-floor"
    assert TOOL_FOR_TIER[d.tier] in ALWAYS_CARD_TOOLS


async def test_config_change_refuses_account_groups(app):
    from iron_jarvis.settings.tools import ConfigChangeTool
    from iron_jarvis.tools.base import ToolContext

    platform = app.state.platform
    tool = ConfigChangeTool(platform)
    ctx = ToolContext(workspace=platform.config.home, session_id="chat", agent_run_id="chat",
                      config=platform.config, event_bus=platform.event_bus, engine=platform.engine)
    res = await tool.execute({"key": "account_groups", "value": {"All": ["a", "b"]}}, ctx)
    assert res.ok is False and "config_change_protected" in res.error
    assert app.state.platform.config.account_groups == {}


# --------------------------------------------------------------------------- #
# races
# --------------------------------------------------------------------------- #


def test_amend_wait_never_writes_on_an_archived_session(tmp_path):
    """take_wait refuses an archived session; amend_wait did not, so a pass
    racing an archive marked (and announced) the limit of a session the user
    had just put away. Mutation: drop the archived check."""
    from iron_jarvis.core.ids import utcnow

    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    row = S.create(engine, str(tmp_path), "", {})
    wait = {"account": "c-work", "account_name": "Work Max", "armed_at": "2026-10-26T10:00:00",
            "next_check_at": "2026-10-26T10:00:00", "resets_at": ""}
    S.update(engine, row.id, setup={"account": "c-work", "account_wait": wait})
    with session_scope(engine) as db:
        rec = db.get(ChatThreadRecord, row.id)
        rec.archived_at = utcnow()
        db.add(rec)
        db.commit()
    assert S.amend_wait(engine, row.id, wait["armed_at"], "c-work", {"told": True}) is False
    assert "told" not in json.loads(S.get_row(engine, row.id).setup_json)["account_wait"]


async def test_a_wake_during_a_pass_is_not_lost(monkeypatch):
    """A turn that ends WHILE a pass runs wakes the loop; the pass used to
    clear the wake after it finished, so the wake was lost and the loop slept
    the whole tick (a grouped wait "looks at once"). Mutation: clear the
    event after the pass again."""
    passes: list[float] = []

    async def fake_pass(d, **kw):
        passes.append(time.monotonic())
        if len(passes) == 1:
            build_routes.wake_wait_loop()  # a turn ended meanwhile
            await asyncio.sleep(0.05)

    monkeypatch.setattr(build_routes, "resume_due", fake_pass)
    monkeypatch.setattr(build_routes, "WAIT_TICK_S", 30.0)
    task = asyncio.ensure_future(build_routes.account_wait_loop(None, lambda ok, exc: None))
    try:
        deadline = time.monotonic() + 5
        while len(passes) < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert len(passes) >= 2, "the wake that arrived during the pass was lost"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_moved_reply_that_meets_the_old_accounts_reset_does_not_move_back_by_itself(
    app, fake, work, monkeypatch,
):
    """A reply that moved to c-spare stays there when c-work's reset comes:
    nothing waits on c-work any more (the take ended that wait)."""
    beh, sid = await _limit(app, fake, work, monkeypatch, moves=("LIMIT", "On Spare."),
                            groups={"Work": ["c-work", "c-spare"]})
    assert await build_routes.resume_due(app.state.d) == [sid]
    await _finish(app, _events(app, "build.resumed")[-1]["turn_id"])
    _reset(fake)
    assert await build_routes.resume_due(app.state.d, now=_later()) == []
    assert setup_of(app, sid)["account"] == "c-spare"
