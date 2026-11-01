"""v1.422.0 (Wave 2 "Never lose work", B5): Build › Code's account limits
behave like Chat › Code's (v1.393.0) — the reply WAITS for the reset by
itself and carries on, or MOVES within the user's account group, with no
press; Codex conversations move too (measured: ``hosted/codex_move``); Grok
waits when its limit says when and never moves.

Driven through the real app factory, the real service (its ``_emit`` /
``_end_turn``), the real routes and the real loop pass
(``carry_on.resume_due``). The coding tool is a fake driver (no process);
Iron-Proxy is an in-process stand-in whose cached snapshot the real
``harness/build_accounts`` rules read. Time is an injected clock
(``carry_on.CLOCK`` and ``resume_due(now=)``) — never a sleep.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import codex_move as CM
from iron_jarvis.hosted import grok as G
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import make
from iron_jarvis.terminals.limit_state import PaneLimit

from ._build_app_helpers_v1409 import client, stop_all, until
from .test_build_app_history_v1418 import fake_tool

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
SID = "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21"
TID = "01a198a2-eada-7373-96ad-91730b8053a4"


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "root"))


@pytest.fixture
def home(app) -> Path:
    return Path(app.state.platform.config.home)


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(CO, "CLOCK", lambda: NOW)
    return NOW


class _Client:
    def __init__(self, ip):
        self.ip = ip
        self.signals: list = []

    def profiles(self):
        return self.ip.profiles

    def states(self):
        return self.ip.states

    def signal(self, pid, text=""):
        self.signals.append((pid, text))
        return {"parked": True}


class _IP:
    """Iron-Proxy as Iron Jarvis sees it: a cached snapshot (what the group
    rules read) and a client (what Continue on another account asks)."""

    enabled = True

    def __init__(self, profiles, states):
        self.profiles, self.states = profiles, states
        self.client = _Client(self)

    def cached_accounts(self):
        return self.profiles, self.states

    def lease_client(self, timeout_s=None):
        return self.client

    def status(self):
        return {"running": True}


def _accounts(tmp_path: Path) -> tuple[_IP, dict[str, Path]]:
    homes = {k: tmp_path / f"acc-{k}" for k in ("p1", "p2", "p3", "o1", "o2", "g1", "g2")}
    for h in homes.values():
        h.mkdir()
    prov = {"p": "anthropic", "o": "openai", "g": "xai"}
    titles = {"p1": "Work", "p2": "Work 2", "p3": "Home", "o1": "Codex work", "o2": "Codex work 2",
              "g1": "Grok work", "g2": "Grok work 2"}
    profiles = [
        {"id": k, "provider": prov[k[0]], "lane": "cli", "title": titles[k], "order": i, "cli": {"home": str(h)}}
        for i, (k, h) in enumerate(homes.items())
    ]
    states = {k: {"status": "ready"} for k in homes}
    return _IP(profiles, states), homes


def _record(pid: str, title: str, home: Path) -> str:
    return json.dumps({"id": pid, "title": title, "source": "iron-proxy", "home": str(home), "unset": []})


def _session(app, work, *, harness="claude-code", account="", title="Work", acc_home=None, hid=SID):
    extra = {}
    if account:
        extra = {"account": account, "account_json": _record(account, title, acc_home)}
    return store.create(app.state.platform.engine, harness=harness, folder=str(work), harness_session_id=hid,
                        title="Refactor the parser", **extra)


async def _limit(svc, drivers, sid, *, reset=None, served=False, said="You've hit your usage limit.",
                 harness_event=None):
    """A turn that ends on the account's limit (``reset``: what the tool's own
    limit record says)."""
    await svc.start_turn(sid, "carry on with the parser", f"t-{len(drivers.get(sid).sent) if sid in drivers else 0}")
    await until(lambda: drivers.get(sid) and drivers[sid].turn_open)
    drv = drivers[sid]
    drv.turn_open = False
    drv.limit = PaneLimit(line=said, reset_at=reset)
    live = svc.live[sid]
    if served:
        svc._emit(live, make("text_done", item="a1", text="Half of it is done."))
    svc._emit(live, harness_event or make("error", title="Claude Code hit this account's limit", fix="",
                                          technical=said, fix_kind="account_limit"))
    svc._emit(live, make("done", stop="completed"))
    await until(lambda: live.turn is None)


def _published(app, monkeypatch) -> list:
    seen: list = []
    bus = app.state.platform.event_bus
    real = bus.publish

    async def spy(kind, payload, **kw):
        seen.append((kind, dict(payload)))
        return await real(kind, payload, **kw)

    monkeypatch.setattr(bus, "publish", spy)
    return seen


# --------------------------------------------------------------------------- #
# the wait for the reset
# --------------------------------------------------------------------------- #
async def test_a_limit_with_a_known_reset_waits_and_carries_on_at_the_reset(app, home, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    seen = _published(app, monkeypatch)
    row = _session(app, work)
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    st = CO.read_limits(home, row.id)
    assert st["wait"]["resets_at"] == reset.isoformat() and st["wait"]["next_check_at"] == reset.isoformat()
    assert "group" not in st["wait"]
    # The card says it waits, and until when.
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
    assert view["account_wait"]["resets_at"] == reset.isoformat()
    err = [e for e in view["events"] if e["type"] == "error"][-1]
    assert err["fix_kind"] == "account_limit"
    await until(lambda: any(k == "build.account_limit" for k, _ in seen))
    limit_news = [p for k, p in seen if k == "build.account_limit"]
    assert limit_news[0]["waiting"] is True and limit_news[0]["surface"] == "build_app"
    # Before the reset: nothing. At the reset: the reply carries on by itself.
    assert await CO.resume_due(svc, now=reset - timedelta(minutes=1)) == []
    assert len(drivers[row.id].sent) == 1
    assert await CO.resume_due(svc, now=reset + timedelta(seconds=1)) == [row.id]
    await until(lambda: len(drivers[row.id].sent) == 2)
    assert drivers[row.id].sent[1][0] == CO.CONTINUE_AFTER_RESET
    assert CO.read_limits(home, row.id).get("wait") is None
    resumed = [p for k, p in seen if k == "build.resumed"]
    assert resumed and resumed[0]["reason"] == "reset" and resumed[0]["surface"] == "build_app"
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
    carry = [e for e in view["events"] if e["type"] == "user" and e.get("carry")]
    assert carry[-1]["carry"]["why"] == "reset"
    assert "account_wait" not in view
    # Once is once: the next pass has nothing to do.
    assert await CO.resume_due(svc, now=reset + timedelta(hours=1)) == []
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_with_the_setting_off_nothing_waits_and_the_press_stays(app, home, work, monkeypatch):
    app.state.platform.config.build_auto_resume = False
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    st = CO.read_limits(home, row.id)
    assert st["card"]["resets_at"] == reset.isoformat() and st.get("wait") is None
    assert await CO.resume_due(svc, now=reset + timedelta(minutes=1)) == []
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


async def test_cancel_ends_the_wait_and_keeps_the_card(app, home, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/wait/cancel")
        assert r.status_code == 200, r.text
        again = await c.post(f"/build/app/sessions/{row.id}/wait/cancel")
        assert again.status_code == 409 and again.json()["detail"] == CO.NO_WAIT
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
    assert "account_wait" not in view and CO.read_limits(home, row.id)["card"]
    assert await CO.resume_due(svc, now=reset + timedelta(minutes=1)) == []
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


async def test_the_users_own_message_ends_the_wait(app, home, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    await svc.start_turn(row.id, "never mind, explain the plan instead", "t-user")
    await until(lambda: CO.read_limits(home, row.id).get("wait") is None)
    await until(lambda: len(drivers[row.id].sent) == 2)
    assert await CO.resume_due(svc, now=reset + timedelta(minutes=1)) == []
    assert [t for t, _ in drivers[row.id].sent] == ["carry on with the parser", "never mind, explain the plan instead"]
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


@pytest.mark.parametrize("reset", [None, NOW - timedelta(minutes=1)], ids=["unknown", "already_past"])
async def test_no_reset_ahead_and_no_group_waits_for_nothing(app, home, work, monkeypatch, reset):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    await _limit(svc, drivers, row.id, reset=reset)
    st = CO.read_limits(home, row.id)
    assert st["card"] and st.get("wait") is None
    await stop_all(app)


def test_a_wait_is_taken_only_by_its_own_id(tmp_path):
    """A wait the loop read and that was REPLACED meanwhile (a newer limit)
    is never taken for the older one."""
    sid = "app_x"
    (tmp_path / "hosted" / sid).mkdir(parents=True)
    (tmp_path / "hosted" / sid / CO.LIMITS_FILE).write_text(
        json.dumps({"card": {"account": "p1"}, "wait": {"id": "new", "account": "p1"}}), "utf-8")
    assert CO.take_wait(tmp_path, sid, "old") is None
    assert CO.read_limits(tmp_path, sid)["wait"]["id"] == "new"
    assert CO.take_wait(tmp_path, sid, "new")["id"] == "new"
    assert CO.read_limits(tmp_path, sid)["wait"] is None


async def test_a_chain_of_carry_ons_that_never_served_stops_and_waits_for_the_user(app, home, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    (home / "hosted" / row.id).mkdir(parents=True, exist_ok=True)
    (home / "hosted" / row.id / CO.LIMITS_FILE).write_text(json.dumps({"chain": CO.MAX_AUTO_IN_A_ROW}), "utf-8")
    await _limit(svc, drivers, row.id, reset=NOW + timedelta(minutes=30))
    st = CO.read_limits(home, row.id)
    assert st.get("wait") is None and st["card"]["note"] == CO.CHAIN_NOTE.format(n=CO.MAX_AUTO_IN_A_ROW)
    # An account that SERVED work starts the chain again.
    await _limit(svc, drivers, row.id, reset=NOW + timedelta(minutes=30), served=True)
    st = CO.read_limits(home, row.id)
    assert st["chain"] == 0 and st["wait"]["resets_at"]
    await stop_all(app)


async def test_a_still_parked_account_moves_the_next_look_to_its_park(app, home, work, tmp_path, monkeypatch):
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work, account="p3", title="Home", acc_home=homes["p3"])
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    ip.states["p3"] = {"status": "parked", "parkedUntil": later.isoformat()}
    assert await CO.resume_due(svc, now=reset + timedelta(minutes=1)) == []
    wait = CO.read_limits(home, row.id)["wait"]
    assert CO._parse(wait["next_check_at"]) == later
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


# --------------------------------------------------------------------------- #
# the move within the group
# --------------------------------------------------------------------------- #
def _conversation(acc_home: Path) -> Path:
    conv = acc_home / "projects" / "C--work-demo" / f"{SID}.jsonl"
    conv.parent.mkdir(parents=True, exist_ok=True)
    conv.write_text('{"type":"user","message":"build the parser"}\n', encoding="utf-8")
    return conv


async def test_a_limit_moves_within_the_group_to_the_same_vendors_free_account(app, home, work, tmp_path, monkeypatch):
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    monkeypatch.setattr(app.state.platform.config, "account_groups",
                        {"Work": ["p1", "p2", "o1"], "Home": ["p3"]})
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    seen = _published(app, monkeypatch)
    conv = _conversation(homes["p1"])
    row = _session(app, work, account="p1", title="Work", acc_home=homes["p1"])
    await _limit(svc, drivers, row.id, reset=None)
    wait = CO.read_limits(home, row.id)["wait"]
    assert wait["group"] == "Work" and wait["next_check_at"] == NOW.isoformat(), "the loop looks at once"
    assert await CO.resume_due(svc) == [row.id]
    await until(lambda: len(drivers[row.id].sent) == 2)
    after = store.get(app.state.platform.engine, row.id)
    assert after.account == "p2" and store.account_record(after)["home"] == str(homes["p2"])
    copied = homes["p2"] / "projects" / "C--work-demo" / f"{SID}.jsonl"
    assert copied.read_bytes() == conv.read_bytes() and conv.exists(), "COPIED, never moved"
    assert not any(homes["p3"].rglob("*.jsonl")), "never to another group's account"
    assert drivers[row.id].sent[1][0] == CO.CONTINUE_AFTER_MOVE
    assert ip.client.signals and ip.client.signals[0][0] == "p1"
    moved = [p for k, p in seen if k == "build.resumed"]
    assert moved and moved[0]["moved_from"] == "p1" and moved[0]["account"] == "p2" and moved[0]["group"] == "Work"
    assert not [p for k, p in seen if k == "build.account_limit"], "a move is said once, not beside a limit"
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
    carry = [e for e in view["events"] if e["type"] == "user" and e.get("carry")][-1]["carry"]
    assert carry == {"why": "move", "from_account": "Work", "to_account": "Work 2", "group": "Work"}
    notice = [e["text"] for e in view["events"] if e["type"] == "notice"]
    assert CO.MOVED_NOTE.format(frm="Work", to="Work 2", group="Work") in notice
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_no_move_across_groups_or_vendors_the_wait_stays_and_is_told_once(app, home, work, tmp_path, monkeypatch):
    ip, homes = _accounts(tmp_path)
    ip.states["p2"] = {"status": "parked", "parkedUntil": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()}
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    monkeypatch.setattr(app.state.platform.config, "account_groups",
                        {"Work": ["p1", "p2", "o1"], "Home": ["p3"]})
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    seen = _published(app, monkeypatch)
    _conversation(homes["p1"])
    row = _session(app, work, account="p1", title="Work", acc_home=homes["p1"])
    await _limit(svc, drivers, row.id, reset=NOW + timedelta(minutes=30))
    assert await CO.resume_due(svc) == []
    assert await CO.resume_due(svc) == []
    assert store.get(app.state.platform.engine, row.id).account == "p1"
    assert not any(any(h.rglob("*.jsonl")) for k, h in homes.items() if k != "p1")
    told = [p for k, p in seen if k == "build.account_limit"]
    assert len(told) == 1 and told[0]["waiting"] is True, "the limit is said ONCE, by the loop"
    assert CO.read_limits(home, row.id)["wait"]["group"] == "Work"
    # The reset still comes: it carries on with the same account.
    assert await CO.resume_due(svc, now=NOW + timedelta(minutes=31)) == [row.id]
    await until(lambda: len(drivers[row.id].sent) == 2)
    assert drivers[row.id].sent[1][0] == CO.CONTINUE_AFTER_RESET
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


def _rollout(acc_home: Path, body: str = '{"type":"session_meta"}\n') -> Path:
    path = acc_home / "sessions" / "2026" / "11" / "01" / f"rollout-2026-11-01T14-42-56-{TID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


async def test_a_codex_limit_moves_the_rollout_within_the_group(app, home, work, tmp_path, monkeypatch):
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    monkeypatch.setattr(app.state.platform.config, "account_groups", {"Codex": ["o1", "o2", "p1"]})
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    src = _rollout(homes["o1"])
    row = _session(app, work, harness="codex", account="o1", title="Codex work", acc_home=homes["o1"], hid=TID)
    await _limit(svc, drivers, row.id, reset=None)
    assert await CO.resume_due(svc) == [row.id]
    await until(lambda: len(drivers[row.id].sent) == 2)
    after = store.get(app.state.platform.engine, row.id)
    assert after.account == "o2" and store.account_record(after)["home"] == str(homes["o2"])
    dest = homes["o2"] / src.relative_to(homes["o1"])
    assert dest.read_bytes() == src.read_bytes() and src.exists()
    assert not any(homes["p1"].rglob("*.jsonl")), "never to another vendor's account"
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_continue_on_moves_a_codex_conversation_and_refuses_a_diverged_copy(app, home, work, tmp_path, monkeypatch):
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    svc = S.service_for(app.state.platform)
    fake_tool(svc, monkeypatch)
    src = _rollout(homes["o1"], '{"a":1}\n{"b":2}\n')
    dest = homes["o2"] / src.relative_to(homes["o1"])
    dest.parent.mkdir(parents=True)
    dest.write_text('{"a":1}\n{"c":3}\n', encoding="utf-8")  # a DIFFERENT copy
    row = _session(app, work, harness="codex", account="o1", title="Codex work", acc_home=homes["o1"], hid=TID)
    # The session's account is at its limit (what Iron-Proxy would be told).
    svc._live(store.get(app.state.platform.engine, row.id)).limit = PaneLimit(line="You've hit your usage limit.")
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={"account": "o2"})
        assert r.status_code == 409 and r.json()["detail"] == CM.DIVERGED.format(title="Codex work 2")
        assert dest.read_text(encoding="utf-8") == '{"a":1}\n{"c":3}\n' and ip.client.signals == []
        assert store.get(app.state.platform.engine, row.id).account == "o1"
        # An OLDER copy of the same conversation (a byte prefix) is replaced,
        # its old bytes kept in Iron Jarvis's own trash.
        dest.write_text('{"a":1}\n', encoding="utf-8")
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={"account": "o2"})
        assert r.status_code == 200, r.text
    assert dest.read_bytes() == src.read_bytes()
    assert [pid for pid, _ in ip.client.signals] == ["o1"], "told once, only when the move went ahead"
    kept = list((home / "trash").rglob(dest.name))
    assert kept and kept[0].read_text(encoding="utf-8") == '{"a":1}\n'
    assert store.get(app.state.platform.engine, row.id).account == "o2"
    await stop_all(app)


async def test_continue_on_refuses_a_codex_session_with_no_conversation_file(app, home, work, tmp_path, monkeypatch):
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    row = _session(app, work, harness="codex", account="o1", title="Codex work", acc_home=homes["o1"], hid=TID)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={})
    assert r.status_code == 409 and r.json()["detail"] == CM.NO_CODEX_FILE
    assert ip.client.signals == [] and not any(homes["o2"].rglob("*.jsonl"))


def test_find_rollout_never_globs_an_unchecked_id(tmp_path):
    _rollout(tmp_path)
    assert CM.find_rollout(str(tmp_path), TID) is not None
    assert CM.find_rollout(str(tmp_path), "*") is None
    assert CM.find_rollout(str(tmp_path), "../" + TID) is None


# --------------------------------------------------------------------------- #
# Codex and Grok reach the same card, and when they reset
# --------------------------------------------------------------------------- #
def test_the_reset_is_read_from_the_tools_own_record_its_windows_or_its_words():
    class D:
        limit = PaneLimit(line="x", reset_at=NOW + timedelta(hours=1))

    assert CO.reset_from(D(), {}) == NOW + timedelta(hours=1)

    class Codex:
        limit = PaneLimit(line="You've hit your usage limit.")
        seen_windows = [
            {"key": "primary", "percent": 100, "resets_at": "2026-11-01T15:00:00Z"},
            {"key": "secondary", "percent": 100, "resets_at": "2026-11-04T09:00:00Z"},
            {"key": "other", "percent": 40, "resets_at": "2026-11-09T09:00:00Z"},
        ]

    assert CO.reset_from(Codex(), {}) == datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    said = {"technical": "You've hit your usage limit. Upgrade to Plus to continue, or try again in 2 hours 5 minutes."}
    got = CO.reset_from(None, said)
    assert got is not None and abs((got - datetime.now(timezone.utc)) - timedelta(hours=2, minutes=5)) < timedelta(minutes=1)
    assert CO.reset_from(None, {"technical": "You've hit your usage limit."}) is None


def test_a_grok_limit_is_the_account_limit_card_with_groks_own_fix():
    ev = G.failure_event("HTTP 429 Too Many Requests: usage limit reached, try again in 3 hours")
    assert ev["fix_kind"] == "account_limit" and ev["fix"] == G.LIMIT_FIX
    assert "fix_kind" not in G.failure_event("boom")


async def test_grok_waits_for_a_reset_it_names_and_never_moves(app, home, work, tmp_path, monkeypatch):
    monkeypatch.setattr(CO, "CLOCK", lambda: datetime.now(timezone.utc))
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    monkeypatch.setattr(app.state.platform.config, "account_groups", {"Grok": ["g1", "g2"]})
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work, harness="grok", account="g1", title="Grok work", acc_home=homes["g1"], hid=TID)
    said = "usage limit reached, try again in 3 hours"
    await _limit(svc, drivers, row.id, reset=None, said=said, harness_event=G.failure_event(said))
    wait = CO.read_limits(home, row.id)["wait"]
    assert "group" not in wait, "Grok never moves to another account"
    assert CO._parse(wait["resets_at"]) > datetime.now(timezone.utc) + timedelta(hours=2, minutes=50)
    no_words = "usage limit reached"
    await _limit(svc, drivers, row.id, reset=None, said=no_words, harness_event=G.failure_event(no_words))
    assert CO.read_limits(home, row.id).get("wait") is None, "no reset named: nothing to wait for"
    await stop_all(app)


# --------------------------------------------------------------------------- #
# the drivers: each tool's limit reaches the card WITH its reset
# --------------------------------------------------------------------------- #
def _claude_limit_frames(text: str) -> list[dict]:
    from ._build_app_helpers_v1409 import FIX

    steps = json.loads((FIX / "claude_limit.json").read_text(encoding="utf-8"))["steps"]
    out = []
    for s in steps:
        o = s.get("out")
        if not isinstance(o, dict) or o.get("type") not in ("rate_limit_event", "assistant", "result"):
            continue
        o = json.loads(json.dumps(o))
        if o.get("type") == "rate_limit_event":
            o["rate_limit_info"]["resetsAt"] = int((NOW + timedelta(hours=2)).timestamp())
        if o.get("type") == "assistant":
            o["message"]["content"][0]["text"] = text
        if o.get("type") == "result":
            o["result"] = text
        out.append(o)
    return out


def test_claude_keeps_its_own_exact_reset_when_the_words_name_none():
    from iron_jarvis.hosted.claude import ClaudeDriver

    seen: list = []
    d = ClaudeDriver(write=None, emit=seen.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.turn_open = True
    for frame in _claude_limit_frames("You've hit your session limit"):
        d.on_message(frame)
    err = [e for e in seen if e.get("type") == "error"]
    assert err and err[0]["fix_kind"] == "account_limit"
    assert CO.reset_from(d, err[0]) == NOW + timedelta(hours=2), "the rate_limit_event's reset was dropped"


def test_a_codex_usage_limit_is_the_card_and_resets_when_its_full_window_does():
    from iron_jarvis.hosted.codex import CodexDriver

    seen: list = []
    d = CodexDriver(write=None, emit=seen.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.turn_open = True
    resets = int((NOW + timedelta(days=2)).timestamp())
    d.on_message({"jsonrpc": "2.0", "method": "account/rateLimits/updated", "params": {"rateLimits": {
        "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": int((NOW + timedelta(hours=1)).timestamp())},
        "secondary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": resets},
        "planType": "plus"}}})
    d.on_message({"jsonrpc": "2.0", "method": "error", "params": {"willRetry": False, "error": {
        "message": "You’ve hit your usage limit. Upgrade to Plus to continue using Codex, or try again at 3:45 PM.",
        "codexErrorInfo": "usageLimitExceeded"}}})
    err = [e for e in seen if e.get("type") == "error"]
    assert err and err[0]["fix_kind"] == "account_limit"
    assert CO.reset_from(d, err[0]) == datetime.fromtimestamp(resets, tz=timezone.utc)
