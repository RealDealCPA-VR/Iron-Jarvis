"""v1.393.0 (the long-jobs plan's Phase 4, decision 4): LIMITS WITHOUT A PRESS.

Driven through the REAL app factory, the real ``POST /build/sessions/{id}/turn``
lane, the REAL router, the real wait loop pass (``routes/build.resume_due``)
and the real Iron-Proxy service/client against the fake Iron-Proxy
(``tests/fixtures/fake_iron_proxy_v1301.py``). Only the two subscription
adapters are doubles (``AccountDouble`` — they make the SAME
``iron_proxy.accounts`` calls the real ones do).

1. A limit card ARMS ITS WAIT BY ITSELF (the same record, the same loop): it
   carries on at the reset with no press; a setting turns it off; Local only,
   an account that cannot come back, Cancel, Choose account, an archived
   session and the user's own later turn behave as before; a write after a
   slow check never re-arms a wait the user cancelled.
2. ACCOUNT GROUPS live in Iron Jarvis's config (the one writer), validated.
3. A limit MOVES the reply to another account of the SAME group and vendor
   that is usable now (lowest usage first), through the Choose account path;
   the line names both accounts; a work account never hands a conversation to
   a personal one; no move across vendors; nothing usable → the wait.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from iron_jarvis.core.config import Config
from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.harness import sessions as S
from iron_jarvis.settings import schema
from tests.test_build_account_turns_v1368 import _events, _finish, _session, _turn
from tests.test_build_accounts_v1368 import (
    Behaviour,
    enable,
    iso,
    isolate,
    make_fake,
    picks,
    wire_doubles,
)
from tests.test_build_sessions_v1367 import asgi, messages_of, setup_of, until


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


async def _groups(app, groups: dict) -> tuple[int, dict]:
    return await asgi(app, "PUT", "/settings/values", {"values": {"account_groups": groups}})


async def _limit(app, fake, work, monkeypatch, *, moves=("LIMIT", "Carried on."), groups=None,
                 turn_id="lim-1"):
    """A session on c-work (the Default) whose turn hits a 429: Iron-Proxy
    parks c-work for ``fake.park_seconds``. Answers ``(beh, sid)`` once the
    turn's task has ended (its record, its announce and its wake are done)."""
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(list(moves)))
    if groups is not None:
        status, body = await _groups(app, groups)
        assert status == 200, body
    sid = await _session(app, work)
    frames = await _turn(app, sid, "write the report", turn_id)
    assert [e for e, _ in frames][-2:] == ["account_limit", "error"], frames[-3:]
    await _finish(app, turn_id)
    return beh, sid


def _reset(fake, pid: str = "c-work") -> None:
    """The account's reset came: Iron-Proxy no longer parks it."""
    st = fake.states[pid]
    st["status"] = "ready"
    st.pop("parkedUntil", None)
    st.pop("parkedReason", None)


def _later(minutes: float = 30):
    return utcnow() + timedelta(minutes=minutes)


# --------------------------------------------------------------------------- #
# 1. the wait arms itself
# --------------------------------------------------------------------------- #


async def test_a_limit_arms_the_wait_by_itself_and_carries_on_at_the_reset(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch)
    wait = setup_of(app, sid)["account_wait"]
    card = setup_of(app, sid)["account_limit"]
    # The SAME record "Resume when ready" writes — armed with no press.
    assert wait["account"] == "c-work" and wait["auto"] is True
    assert build_routes._parse_iso(wait["resets_at"]) == build_routes._parse_iso(card["resets_at"])
    assert wait["next_check_at"] == wait["resets_at"], "the loop looks at the reset"
    assert "group" not in wait and "once" not in wait
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["account_wait"]["auto"] is True and view["account_limit"]["kind"] == "rate"
    # The bell hears the limit ONCE, and that the reply waits by itself.
    [said] = _events(app, "build.account_limit")
    assert said["waiting"] is True and said["account_name"] == "Work Max"
    # Before the reset: nothing runs.
    assert await build_routes.resume_due(app.state.d) == []
    assert len(beh.calls) == 1
    # The reset comes: it carries on by itself, on the same account.
    _reset(fake)
    fake.queries.clear()
    assert await build_routes.resume_due(app.state.d, now=_later()) == [sid]
    event = _events(app, "build.resumed")[-1]
    assert event["account"] == "c-work" and "moved_from" not in event
    frames = await _finish(app, event["turn_id"])
    assert frames[-1][0] == "done"
    assert [p.get("profileId") for p in picks(fake)] == ["c-work", "c-work"]
    msgs = messages_of(app, sid)
    assert msgs[-2]["continuation"] is True and msgs[-1]["content"] == "Carried on."
    assert "accountMove" not in msgs[-1]
    setup = setup_of(app, sid)
    assert "account_wait" not in setup and "account_limit" not in setup


async def test_the_turn_end_wakes_the_wait_loop(app, fake, work, monkeypatch):
    woke: list[int] = []
    monkeypatch.setattr(build_routes, "wake_wait_loop", lambda: woke.append(1))
    await _limit(app, fake, work, monkeypatch)
    assert await until(lambda: woke), "the loop is told the moment the limited turn ends"


async def test_the_setting_off_leaves_the_card_for_the_press(app, fake, work, monkeypatch):
    app.state.platform.config.build_auto_resume = False
    beh, sid = await _limit(app, fake, work, monkeypatch)
    setup = setup_of(app, sid)
    assert "account_limit" in setup and "account_wait" not in setup
    [said] = _events(app, "build.account_limit")
    assert said["waiting"] is False
    # The press still works exactly as before.
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/resume-when-ready")
    assert status == 200 and view["account_wait"]["account"] == "c-work"
    assert "auto" not in view["account_wait"]


def test_the_setting_is_one_schema_row_on_by_default():
    d = schema.get("build_auto_resume")
    assert (d.type, d.tier, d.group, d.store) == ("bool", "allow", "automation", "config")
    assert Config.model_fields["build_auto_resume"].default is True
    assert "build_auto_resume" in schema.daemon_keys()


async def test_the_setting_goes_through_the_one_writer(app):
    status, body = await asgi(app, "PUT", "/settings/values", {"values": {"build_auto_resume": False}})
    assert status == 200 and body["updated"] == ["build_auto_resume"] and body["action_id"]
    assert app.state.platform.config.build_auto_resume is False


async def test_an_account_that_cannot_come_back_arms_nothing(app, fake, work, monkeypatch):
    await enable(app)
    wire_doubles(app, monkeypatch, Behaviour(["never"]))
    sid = await _session(app, work, account="c-spare")
    del fake.profiles["c-spare"]
    fake.states.pop("c-spare", None)
    frames = await _turn(app, sid, "hi", "lim-gone")
    assert dict(frames)["account_limit"]["kind"] == "missing"
    await _finish(app, "lim-gone")
    assert "account_wait" not in setup_of(app, sid)


def test_local_only_and_a_card_for_another_account_arm_nothing():
    plat = type("P", (), {"config": Config(project_root=".", home=".")})()
    limit = {"account": "c-work", "kind": "usage", "resumable": True, "resets_at": iso(600)}
    assert build_routes.auto_wait(plat, {"account": "c-work"}, limit) is not None, "control"
    assert build_routes.auto_wait(plat, {"account": "c-work", "local_only": True}, limit) is None
    assert build_routes.auto_wait(plat, {"account": "c-spare"}, limit) is None
    assert build_routes.auto_wait(plat, {"account": "c-work"}, {**limit, "resumable": False}) is None


async def test_cancel_and_choose_account_end_a_wait_that_armed_itself(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch)
    status, view = await asgi(app, "DELETE", f"/build/sessions/{sid}/resume-when-ready")
    assert status == 200 and view["account_wait"] is None
    _reset(fake)
    assert await build_routes.resume_due(app.state.d, now=_later()) == []
    # Armed again by a second limit, then Choose account (no continue).
    _reset(fake)
    beh.moves = ["LIMIT"]
    await _turn(app, sid, "again", "lim-2")
    await _finish(app, "lim-2")
    assert setup_of(app, sid)["account_wait"]["auto"] is True
    status, body = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "c-spare"})
    assert status == 200 and body["account_wait"] is None
    assert await build_routes.resume_due(app.state.d, now=_later()) == []
    assert len(beh.calls) == 2


async def test_an_archived_session_never_carries_on(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch)
    status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/archive", {})
    assert status == 200
    _reset(fake)
    assert await build_routes.resume_due(app.state.d, now=_later()) == []
    assert len(beh.calls) == 1


async def test_a_turn_the_user_started_after_the_card_wins(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch)
    old = dict(setup_of(app, sid)["account_wait"])
    _reset(fake)
    beh.moves = ["I am back."]
    frames = await _turn(app, sid, "are you there?", "lim-own")
    assert frames[-1][0] == "done"
    await _finish(app, "lim-own")
    assert "account_wait" not in setup_of(app, sid)
    assert await build_routes.resume_due(app.state.d, now=_later()) == []
    # The old wait can never start anything now.
    with pytest.raises(build_routes.WaitEnded):
        await build_routes.start_build_turn(
            app.state.d, sid, build_routes.CONTINUE_TEXT, "bt_late", continuation=True, resume_of=old,
        )


async def test_a_wait_cancelled_during_a_slow_check_is_never_armed_again(app, fake, work, monkeypatch):
    """The loop's write after its check used to put back a wait the user
    cancelled while Iron-Proxy was asked (S.update of the old record)."""
    beh, sid = await _limit(app, fake, work, monkeypatch)

    def slow_check(platform, setup):
        S.update(app.state.platform.engine, sid, setup={"account_wait": None})  # the user's Cancel
        return {"kind": "rate", "resets_at": iso(900), "resumable": True}

    monkeypatch.setattr(build_routes, "check_account", slow_check)
    assert await build_routes.resume_due(app.state.d, now=_later(20)) == []
    assert "account_wait" not in setup_of(app, sid)


async def test_an_armed_wait_is_not_a_question_for_the_phone(app, fake, work, monkeypatch):
    """ONE notice: the bell's limit row. No approval.requested (the phone's
    answerable line) and no waiting row in the pending listing."""
    beh, sid = await _limit(app, fake, work, monkeypatch)
    types = [e.type for e in app.state.platform.event_bus.history]
    assert "approval.requested" not in types
    status, pending = await asgi(app, "GET", "/chat/approvals/pending")
    assert status == 200
    rows = pending if isinstance(pending, list) else pending.get("pending", pending.get("items", []))
    assert not any(isinstance(r, dict) and r.get("thread_id") == sid for r in rows)


# --------------------------------------------------------------------------- #
# 2. account groups, through the one writer
# --------------------------------------------------------------------------- #


async def test_groups_are_saved_through_the_writer_and_shown_on_the_card(app, fake):
    await enable(app)
    status, body = await _groups(app, {"Work": ["c-work", "c-spare", "x-plus"], " Home ": ["x-team"]})
    assert status == 200, body
    assert body["updated"] == ["account_groups"] and body["action_id"]
    assert app.state.platform.config.account_groups == {
        "Work": ["c-work", "c-spare", "x-plus"], "Home": ["x-team"],
    }
    status, view = await asgi(app, "GET", "/iron-proxy?discover=0")
    assert view["account_groups"] == app.state.platform.config.account_groups
    # Persisted in Iron Jarvis's config.toml (never in Iron-Proxy).
    text = (app.state.platform.config.home / "config.toml").read_text(encoding="utf-8")
    assert "account_groups" in text and "c-spare" in text
    assert not any("group" in str(c).lower() for c in fake.calls)
    # No groups is always a valid save.
    status, _ = await _groups(app, {})
    assert status == 200 and app.state.platform.config.account_groups == {}


@pytest.mark.parametrize("groups, words", [
    ({"Work": ["c-work"], "Home": ["c-work"]}, "in one group only"),
    ({"x" * 41: ["c-work"]}, "at most 40 characters"),
    ({"": ["c-work"]}, "give every group a name"),
    ({"Work": ["nobody"]}, "no Claude Code or Codex account “nobody”"),
    ({"Work": "c-work"}, "list the accounts"),
])
async def test_a_bad_group_is_refused_in_words(app, fake, groups, words):
    await enable(app)
    status, body = await _groups(app, groups)
    assert status == 400 and words in body["detail"], body
    assert body["detail"].startswith("Account groups")
    assert app.state.platform.config.account_groups == {}


async def test_a_group_needs_iron_proxy_to_know_its_accounts(app):
    status, body = await _groups(app, {"Work": ["c-work"]})
    assert status == 400 and "Iron-Proxy is off" in body["detail"]


async def test_an_account_removed_later_never_blocks_a_save(app, fake):
    await enable(app)
    assert (await _groups(app, {"Work": ["c-work", "c-spare"]}))[0] == 200
    del fake.profiles["c-spare"]
    fake.states.pop("c-spare", None)
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    status, body = await _groups(app, {"Work": ["c-work", "c-spare"], "Home": ["x-team"]})
    assert status == 200, body


def test_a_hand_edited_config_never_stops_a_boot():
    cfg = Config(project_root=".", home=".", account_groups={
        "  Work ": ["a", " a ", "b"], "Two": ["b", "c"], "": ["d"], "x" * 50: ["e"], "Empty": [],
    })
    assert cfg.account_groups == {"Work": ["a", "b"], "Two": ["c"]}


def test_the_groups_row_says_what_a_group_is():
    d = schema.get("account_groups")
    assert (d.type, d.tier, d.store) == ("dict", "ask", "config")
    assert d.help == (
        "Accounts in the same group can take over from each other when one reaches its limit. "
        "Work and personal accounts should be in different groups."
    )
    assert "account_groups" in schema.daemon_keys()


# --------------------------------------------------------------------------- #
# 3. the move within a group
# --------------------------------------------------------------------------- #


async def test_a_grouped_account_hands_the_reply_to_its_group(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch,
                            moves=("LIMIT", "Carried on with Spare."),
                            groups={"Work": ["c-work", "c-spare"]})
    wait = setup_of(app, sid)["account_wait"]
    assert wait["group"] == "Work" and wait["next_check_at"] == wait["armed_at"], "the loop looks at once"
    # ONE notice: the limit is not said before the group was looked at.
    assert _events(app, "build.account_limit") == []
    fake.queries.clear()
    assert await build_routes.resume_due(app.state.d) == [sid]
    event = _events(app, "build.resumed")[-1]
    assert (event["moved_from"], event["moved_from_name"]) == ("c-work", "Work Max")
    assert (event["account"], event["account_name"], event["group"]) == ("c-spare", "Spare", "Work")
    frames = await _finish(app, event["turn_id"])
    done = dict(frames)["done"]
    assert frames[-1][0] == "done" and done["route"]["account"]["id"] == "c-spare"
    # Every pick pinned: the check of c-work, then the continuation on c-spare.
    assert [(p["profileId"], p["pinned"]) for p in picks(fake)] == [("c-work", "1"), ("c-spare", "1")]
    assert beh.leases[-1] == ("claude-cli", "c-spare")
    move = {"from": "c-work", "fromName": "Work Max", "to": "c-spare", "toName": "Spare", "group": "Work"}
    msgs = messages_of(app, sid)
    assert msgs[-2]["continuation"] is True and msgs[-2]["accountMove"] == move
    assert msgs[-1]["content"] == "Carried on with Spare." and msgs[-1]["accountMove"] == move
    # The pin moved; the MODEL stayed the one the user picked.
    setup = setup_of(app, sid)
    assert (setup["account"], setup["account_name"], setup["provider"]) == ("c-spare", "Spare", "claude-cli")
    assert setup["model"] == "claude-sonnet-4-5"
    assert "account_wait" not in setup and "account_limit" not in setup and "account_moves" not in setup
    # The conversation went on as it was.
    sent = [m.content for m in beh.calls[-1]["messages"] if m.role == "user"]
    assert sent[0] == "write the report" and sent[-1] == build_routes.CONTINUE_TEXT
    assert _events(app, "build.account_limit") == [], "the move was the one notice"


async def test_a_work_account_never_hands_the_reply_to_a_personal_one(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch,
                            groups={"Work": ["c-work"], "Personal": ["c-spare", "c-gone"]})
    wait = setup_of(app, sid)["account_wait"]
    assert "group" not in wait, "an account alone in its group has nobody to hand over to"
    assert _events(app, "build.account_limit")[-1]["waiting"] is True
    fake.queries.clear()
    wait["next_check_at"] = iso(-5)  # due, while c-work is still parked
    S.update(app.state.platform.engine, sid, setup={"account_wait": wait})
    for _ in range(3):
        assert await build_routes.resume_due(app.state.d) == []
    assert {p["profileId"] for p in picks(fake)} == {"c-work"}
    assert all(lease[1] == "c-work" for lease in beh.leases)
    assert setup_of(app, sid)["account"] == "c-work"


async def test_an_account_in_no_group_never_moves(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Home": ["c-spare", "c-gone"]})
    assert "group" not in setup_of(app, sid)["account_wait"]
    assert await build_routes.resume_due(app.state.d) == []
    assert setup_of(app, sid)["account"] == "c-work"


async def test_no_move_ever_crosses_vendors(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Work": ["c-work", "x-plus", "x-team"]})
    assert "group" not in setup_of(app, sid)["account_wait"]
    assert BA.takeover(app.state.platform.iron_proxy, app.state.platform.config.account_groups, "c-work") is None
    assert await build_routes.resume_due(app.state.d) == []
    assert not any(p == "codex-cli" for p, _ in beh.leases)
    assert setup_of(app, sid)["provider"] == "claude-cli"


async def test_nothing_usable_in_the_group_falls_back_to_the_wait_then_moves_when_one_is(
    app, fake, work, monkeypatch,
):
    fake.states["c-spare"]["windows"] = [
        {"kind": "5h", "usedPercent": 96, "windowMins": 300, "resetsAt": iso(3600), "source": "headers"},
    ]
    beh, sid = await _limit(app, fake, work, monkeypatch,
                            moves=("LIMIT", "Spare took over."),
                            groups={"Work": ["c-work", "c-spare", "c-gone"]})
    # c-spare is at 96% of a window, c-gone must sign in again: nobody can.
    assert await build_routes.resume_due(app.state.d) == []
    wait = setup_of(app, sid)["account_wait"]
    assert wait["told"] is True and wait["account"] == "c-work"
    [said] = _events(app, "build.account_limit")
    assert said["waiting"] is True and said["account_name"] == "Work Max"
    # A later pass, before c-work's reset: c-spare's window moved on.
    fake.states["c-spare"]["windows"][0]["usedPercent"] = 40
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    assert await build_routes.resume_due(app.state.d) == [sid]
    event = _events(app, "build.resumed")[-1]
    assert event["account"] == "c-spare"
    await _finish(app, event["turn_id"])
    assert messages_of(app, sid)[-1]["content"] == "Spare took over."
    assert len(_events(app, "build.account_limit")) == 1, "said once"


async def test_the_lowest_usage_takes_over(app, fake, work, monkeypatch):
    fake.add_profile("anthropic", "Third", id="c-third")
    fake.states["c-spare"]["windows"] = [
        {"kind": "5h", "usedPercent": 70, "windowMins": 300, "resetsAt": iso(3600), "source": "headers"},
    ]
    fake.states["c-third"]["windows"] = [
        {"kind": "5h", "usedPercent": 20, "windowMins": 300, "resetsAt": iso(3600), "source": "headers"},
        {"kind": "7d", "usedPercent": 30, "windowMins": 10080, "resetsAt": iso(86400), "source": "headers"},
    ]
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Work": ["c-work", "c-spare", "c-third"]})
    assert await build_routes.resume_due(app.state.d) == [sid]
    assert _events(app, "build.resumed")[-1]["account"] == "c-third"


def test_a_window_at_95_percent_is_not_usable():
    row = {"status": BA.USAGE}
    at = {"windows": [{"usedPercent": 95, "resetsAt": iso(600)}]}
    below = {"windows": [{"usedPercent": 94.9, "resetsAt": iso(600)}, {"usedPercent": 10}]}
    over = {"windows": [{"usedPercent": 10}, {"usedPercent": 99, "resetsAt": iso(600)}]}
    reset = {"windows": [{"usedPercent": 99, "resetsAt": iso(-60)}]}
    assert BA.usable_now(row, at) is False
    assert BA.usable_now(row, over) is False, "every window counts"
    assert BA.usable_now(row, below) is True
    assert BA.usable_now(row, reset) is True, "a window whose reset passed is over"
    assert BA.usable_now(row, {}) is True, "no numbers is not a limit"
    for status in (BA.LIMIT, BA.NEEDS_SIGN_IN, BA.OFF):
        assert BA.usable_now({"status": status}, {}) is False


async def test_a_move_that_lost_a_race_moves_nothing(app, fake, work, monkeypatch):
    """The user's Cancel lands while the loop picks the account: the take
    fails, so the session stays on its own account and nothing starts."""
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Work": ["c-work", "c-spare"]})
    real = BA.takeover

    def racing(svc, groups, account_id):
        S.update(app.state.platform.engine, sid, setup={"account_wait": None})
        return real(svc, groups, account_id)

    monkeypatch.setattr(BA, "takeover", racing)
    assert await build_routes.resume_due(app.state.d) == []
    setup = setup_of(app, sid)
    assert setup["account"] == "c-work" and "account_wait" not in setup and "account_moves" not in setup
    assert len(beh.calls) == 1 and _events(app, "build.resumed") == []


async def test_moves_in_a_row_are_bounded(app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Work": ["c-work", "c-spare"]})
    S.update(app.state.platform.engine, sid, setup={"account_moves": build_routes.MAX_GROUP_MOVES})
    assert await build_routes.resume_due(app.state.d) == []
    assert setup_of(app, sid)["account"] == "c-work"


async def test_with_the_setting_off_a_group_still_moves_once_and_never_waits(app, fake, work, monkeypatch):
    app.state.platform.config.build_auto_resume = False
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Work": ["c-work", "c-gone"]})
    assert setup_of(app, sid)["account_wait"]["once"] is True
    # Nothing in the group can serve: the wait ENDS (the card waits for a
    # press), and the limit is said as a question that needs the user.
    assert await build_routes.resume_due(app.state.d) == []
    assert "account_wait" not in setup_of(app, sid)
    [said] = _events(app, "build.account_limit")
    assert said["waiting"] is False
    _reset(fake)
    assert await build_routes.resume_due(app.state.d, now=_later()) == []
    assert len(beh.calls) == 1, "never carried on by itself"


async def test_with_the_setting_off_a_usable_group_account_still_takes_over(app, fake, work, monkeypatch):
    app.state.platform.config.build_auto_resume = False
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Work": ["c-work", "c-spare"]})
    assert await build_routes.resume_due(app.state.d) == [sid]
    assert _events(app, "build.resumed")[-1]["account"] == "c-spare"


async def test_a_move_survives_a_restart(tmp_path, app, fake, work, monkeypatch):
    beh, sid = await _limit(app, fake, work, monkeypatch, groups={"Work": ["c-work", "c-spare"]})
    second = create_app(str(tmp_path / "home"))
    beh2 = wire_doubles(second, monkeypatch, Behaviour(["After the restart, on Spare."]))
    await enable(second)
    assert second.state.platform.config.account_groups == {"Work": ["c-work", "c-spare"]}
    assert await build_routes.resume_due(second.state.d) == [sid]
    event = _events(second, "build.resumed")[-1]
    await _finish(second, event["turn_id"])
    assert beh2.leases == [("claude-cli", "c-spare")]
    assert messages_of(second, sid)[-1]["accountMove"]["toName"] == "Spare"
