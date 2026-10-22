"""v1.368.0 (IJ harness Phase 5): a Build TURN on its session's ONE account.

Driven through the REAL app factory, the real ``POST /build/sessions/{id}/turn``
lane, the REAL router and the real Iron-Proxy service/client against the fake
Iron-Proxy. Only the two subscription adapters are doubles
(``AccountDouble``) — and they make the SAME ``iron_proxy.accounts`` calls the
real ones do (lease off the loop, ``retry_lease`` on a 429,
``report_success_async``). The real adapters' halves are pinned in
``test_build_account_usage_v1368.py``.

The plan's "Done when", proven here:

1. a session on a parked account shows the limit card with the REAL reset
   time (Iron-Proxy's own ``parkedUntil``);
2. "Resume when ready" continues after the reset — driven with a 1.5 s park —
   and the wait survives a restart (a second app on the same home);
3. "Choose account" continues on another Claude account AND on a Codex
   account, with the conversation intact;
5. nothing ever lands on an account the user did not pick: every pick a
   build turn makes is ``pinned=1&profileId=<the session's account>``, no
   other provider is called, no next account is leased.

(4 — the cards' plan and percent — is ``test_build_accounts_v1368.py`` and
the usage file.)
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import sessions as S
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import pin as P
from tests.test_build_accounts_v1368 import (
    Behaviour,
    enable,
    iso,
    isolate,
    make_fake,
    picks,
    wire_doubles,
    write_call,
)
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    frames_of,
    messages_of,
    open_turn,
    setup_of,
    turn_frames,
    turn_log,
    until,
    wire,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
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


async def _session(app, work, **extra) -> str:
    body = {"folder": str(work), "provider": "claude-cli", "model": "claude-sonnet-4-5", **extra}
    status, view = await asgi(app, "POST", "/build/sessions", body)
    assert status == 201, view
    return view["id"]


async def _turn(app, sid: str, text: str, turn_id: str) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] in ("done", "error"), frames[-3:]
    return frames


async def _finish(app, turn_id: str) -> list[tuple[str, dict]]:
    lg = await until(lambda: turn_log(app, turn_id))
    assert lg is not None
    await until(lambda: lg.status != "running")
    return turn_frames(app, turn_id)


def _only_pinned(fake, account: str) -> None:
    got = picks(fake)
    assert got, "no pick was made"
    for p in got:
        assert p == {"provider": p["provider"], "lane": "cli", "profileId": account, "pinned": "1"}, got


def _events(app, kind: str) -> list[dict]:
    return [e.payload for e in app.state.platform.event_bus.history if e.type == kind]


# --------------------------------------------------------------------------- #
# the turn runs on the session's account, and the receipt says so
# --------------------------------------------------------------------------- #


async def test_a_turn_leases_only_its_account_and_the_receipt_names_it(app, fake, work, monkeypatch):
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(["Hello from Work."]))
    sid = await _session(app, work)
    fake.queries.clear()
    frames = await _turn(app, sid, "hi", "acc-1")
    done = dict(frames)["done"]
    assert done["route"]["account"] == {"id": "c-work", "name": "Work Max"}
    assert done["route"]["harness"]["dialect"] == "claude"
    assert beh.leases == [("claude-cli", "c-work")]
    assert all(p is not None and p.profile_id == "c-work" for p in beh.pins), "the pin reached the adapter"
    _only_pinned(fake, "c-work")
    reply = messages_of(app, sid)[-1]
    assert reply["content"] == "Hello from Work." and reply["route"]["account"]["name"] == "Work Max"
    # Outside the turn, nothing is pinned (the ContextVar did not leak).
    assert P.current() is None


async def test_a_parked_account_shows_the_limit_card_with_the_real_reset(app, fake, work, monkeypatch):
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(["never sent"]))
    sid = await _session(app, work)
    until_iso = iso(5400)
    fake.states["c-work"].update({"status": "parked", "parkedUntil": until_iso,
                                  "parkedReason": {"kind": "quota-exhausted"}})
    fake.queries.clear()
    frames = await _turn(app, sid, "build the thing", "acc-park")
    kinds = [e for e, _ in frames]
    assert kinds[-2:] == ["account_limit", "error"], kinds[-4:]
    card = dict(frames)["account_limit"]
    assert card == {
        "account": "c-work", "account_name": "Work Max", "provider": "claude-cli", "vendor": "claude",
        "kind": "usage", "resets_at": accounts.iso_utc(until_iso), "resumable": True, "turn_id": "acc-park",
    }
    err = dict(frames)["error"]
    assert err["title"].startswith('The Claude account "Work Max" is at its usage limit until')
    assert err["title"].endswith("choose another account for this session.")
    assert err["fix"] == build_routes.LIMIT_KEPT
    for word in ("profile id", "daemon", "hint", "pick without"):
        assert word not in (err["title"] + err["fix"]).lower()
    # Nothing landed anywhere: no call ran, no other account or provider.
    assert beh.leases == [] and beh.calls == []
    _only_pinned(fake, "c-work")
    # The card is kept on the session (a reload shows it) and on the line.
    assert setup_of(app, sid)["account_limit"]["resets_at"] == card["resets_at"]
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["account_limit"]["kind"] == "usage" and view["account"]["status"] == "limit"
    line = messages_of(app, sid)[-1]
    assert line["role"] == "assistant" and line["accountLimit"]["resets_at"] == card["resets_at"]
    assert _events(app, "build.account_limit")[-1]["account_name"] == "Work Max"


async def test_a_limit_mid_turn_ends_after_the_last_complete_round_and_keeps_the_work(
    app, fake, work, monkeypatch,
):
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour([
        [write_call("w1", "hello.py", "print(1)\n")],
        "LIMIT",
    ]))
    sid = await _session(app, work)
    frames = await _turn(app, sid, "make hello.py then more", "acc-mid")
    kinds = [e for e, _ in frames]
    assert kinds[-2:] == ["account_limit", "error"]
    finished = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert [(d["name"], d["ok"]) for d in finished] == [("write_file", True)]
    # The work stays, with its Undo.
    assert (work / "hello.py").read_text(encoding="utf-8") == "print(1)\n"
    with session_scope(app.state.platform.engine) as db:
        assert db.exec(select(UndoJournal).where(UndoJournal.session_id == f"build:{sid}")).first()
    # The round that hit the limit was a COMPLETE round: its request carried
    # the call AND its result — never an orphaned tool call.
    second = beh.calls[1]["messages"]
    calls = [c.id for m in second if m.role == "assistant" for c in m.tool_calls]
    results = [m.tool_call_id for m in second if m.role == "tool"]
    assert calls == ["w1"] and results == ["w1"]
    # The session's record: the question and the limit line, role + content.
    history = S.history(S.get_row(app.state.platform.engine, sid))
    assert [h["role"] for h in history] == ["user"], history
    line = messages_of(app, sid)[-1]
    assert line["accountLimit"]["kind"] == "rate" and line["steps"][0]["name"] == "write_file"


# --------------------------------------------------------------------------- #
# "Resume when ready"
# --------------------------------------------------------------------------- #


async def _limit_then_arm(app, fake, work, monkeypatch, park_s: float = 1.5):
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(["LIMIT", "Carried on."]))
    fake.park_seconds = park_s
    sid = await _session(app, work)
    frames = await _turn(app, sid, "write the report", "acc-r1")
    card = dict(frames)["account_limit"]
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/resume-when-ready")
    assert status == 200, view
    return beh, sid, card, view


async def test_resume_when_ready_continues_after_the_reset(app, fake, work, monkeypatch):
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch)
    wait = view["account_wait"]
    assert wait["account"] == "c-work" and wait["resets_at"] and wait["next_check_at"]
    assert accounts.iso_utc(wait["resets_at"]) == card["resets_at"]
    # Before the reset nothing is checked and nothing runs.
    fake.queries.clear()
    assert await build_routes.resume_due(app.state.d) == []
    assert picks(fake) == [] and len(beh.calls) == 1
    # After it: a pinned pick of THAT account, then the same conversation goes on.
    await asyncio.sleep(1.7)
    resumed = await build_routes.resume_due(app.state.d)
    assert resumed == [sid]
    event = _events(app, "build.resumed")[-1]
    assert event["thread_id"] == sid and event["account_name"] == "Work Max"
    frames = await _finish(app, event["turn_id"])
    assert frames[-1][0] == "done" and dict(frames)["done"]["route"]["account"]["id"] == "c-work"
    _only_pinned(fake, "c-work")
    assert beh.leases[-1] == ("claude-cli", "c-work")
    msgs = messages_of(app, sid)
    assert msgs[-2]["continuation"] is True and msgs[-2]["content"] == build_routes.CONTINUE_TEXT
    assert msgs[-1]["content"] == "Carried on."
    # The conversation was intact: the earlier question rode the resumed call.
    sent = [m.content for m in beh.calls[-1]["messages"] if m.role == "user"]
    assert sent[0] == "write the report" and sent[-1] == build_routes.CONTINUE_TEXT
    setup = setup_of(app, sid)
    assert "account_wait" not in setup and "account_limit" not in setup


async def test_a_resumed_turn_that_fails_never_resumes_again_by_itself(app, fake, work, monkeypatch):
    """The wait ends when the daemon CONTINUES — not when the continued turn
    succeeds. A continued turn that fails for another reason (the account is
    fine) must not leave a due wait behind: the loop would start a new
    continuation every pass."""
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=1.0)
    beh.moves = ["FAIL"]
    await asyncio.sleep(1.2)
    [resumed] = await build_routes.resume_due(app.state.d)
    event = _events(app, "build.resumed")[-1]
    frames = await _finish(app, event["turn_id"])
    assert frames[-1][0] == "error"
    assert "account_wait" not in setup_of(app, sid)
    calls = len(beh.calls)
    assert await build_routes.resume_due(app.state.d) == []
    assert len(beh.calls) == calls, "no second continuation"


async def test_a_still_parked_account_moves_the_next_check_on(app, fake, work, monkeypatch):
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=600)
    wait = setup_of(app, sid)["account_wait"]
    # Make it due, while Iron-Proxy still parks the account.
    wait["next_check_at"] = iso(-5)
    S.update(app.state.platform.engine, sid, setup={"account_wait": wait})
    assert await build_routes.resume_due(app.state.d) == []
    after = setup_of(app, sid)["account_wait"]
    assert after["next_check_at"] > iso(500), "the next look is the account's own reset"
    assert len(beh.calls) == 1, "nothing ran"


async def test_the_wait_survives_a_restart(tmp_path, app, fake, work, monkeypatch):
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch)
    # A RESTART: a second app on the same home (its own platform, service,
    # router); the wait lives on the session.
    second = create_app(str(tmp_path / "home"))
    beh2 = wire_doubles(second, monkeypatch, Behaviour(["Back after the restart."]))
    await asyncio.sleep(1.7)
    assert await build_routes.resume_due(second.state.d) == [sid]
    event = _events(second, "build.resumed")[-1]
    frames = await _finish(second, event["turn_id"])
    assert frames[-1][0] == "done"
    assert beh2.leases == [("claude-cli", "c-work")]
    assert messages_of(second, sid)[-1]["content"] == "Back after the restart."


async def test_resume_needs_a_limit_and_cancel_clears_the_wait(app, fake, work, monkeypatch):
    await enable(app)
    wire_doubles(app, monkeypatch, Behaviour())
    sid = await _session(app, work)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/resume-when-ready")
    assert status == 409 and body["detail"] == build_routes.NOTHING_TO_WAIT_FOR
    fake.states["c-work"].update({"status": "parked", "parkedUntil": iso(900)})
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/resume-when-ready")
    assert status == 200 and view["account_wait"]["account"] == "c-work"
    status, view = await asgi(app, "DELETE", f"/build/sessions/{sid}/resume-when-ready")
    assert status == 200 and view["account_wait"] is None
    assert "account_wait" not in setup_of(app, sid)


async def test_the_wait_loop_reports_each_pass(tmp_path, fake, monkeypatch):
    from fastapi.testclient import TestClient

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    with TestClient(create_app(str(tmp_path / "home2"))) as client:
        end = time.monotonic() + 10
        while time.monotonic() < end and "build_accounts" not in client.app.state.d.loop_health:
            time.sleep(0.05)
        assert client.app.state.d.loop_health["build_accounts"]["ok"] is True


# --------------------------------------------------------------------------- #
# "Choose account"
# --------------------------------------------------------------------------- #


async def test_choose_another_claude_account_continues_with_the_conversation(app, fake, work, monkeypatch):
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=600)
    fake.queries.clear()
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/account",
                              {"account": "c-spare", "continue": True})
    assert status == 200, body
    assert body["session"]["account"]["id"] == "c-spare" and body["session"]["account_wait"] is None
    assert body["session"]["setup"]["model"] == "claude-sonnet-4-5", "another Claude account keeps the model"
    frames = await _finish(app, body["turn_id"])
    assert frames[-1][0] == "done" and dict(frames)["done"]["route"]["account"]["name"] == "Spare"
    _only_pinned(fake, "c-spare")
    last = beh.calls[-1]
    assert last["account"] == "c-spare" and last["provider"] == "claude-cli"
    sent = [(m.role, m.content) for m in last["messages"]]
    assert sent[0] == ("user", "write the report") and sent[-1] == ("user", build_routes.CONTINUE_TEXT)
    assert not any(m.raw_blocks for m in last["messages"]), "no native blocks cross accounts"
    assert "account_limit" not in setup_of(app, sid), "a finished turn clears the card"


async def test_choose_a_codex_account_switches_the_model_and_moves_the_conversation(
    app, fake, work, monkeypatch,
):
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=600)
    fake.queries.clear()
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/account",
                              {"account": "x-plus", "continue": True})
    assert status == 200, body
    sess = body["session"]
    assert sess["setup"]["provider"] == "codex-cli" and "model" not in sess["setup"]
    assert sess["account_vendor"] == "codex"
    frames = await _finish(app, body["turn_id"])
    done = dict(frames)["done"]
    assert frames[-1][0] == "done" and done["route"]["account"] == {"id": "x-plus", "name": "Codex Plus"}
    assert done["route"]["harness"]["dialect"] == "gpt", "the model met Codex's own tools"
    _only_pinned(fake, "x-plus")
    last = beh.calls[-1]
    assert (last["provider"], last["account"], last["model"]) == ("codex-cli", "x-plus", "subscription")
    sent = [(m.role, m.content) for m in last["messages"]]
    assert sent[0] == ("user", "write the report") and sent[-1] == ("user", build_routes.CONTINUE_TEXT)
    assert not any(m.raw_blocks for m in last["messages"]) and all(m.role != "tool" for m in last["messages"])


async def test_a_second_claude_account_that_refuses_thinking_is_said_on_the_receipt(
    app, fake, work, monkeypatch,
):
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(["ok"]))
    beh.drop_thinking = True
    sid = await _session(app, work)
    frames = await _turn(app, sid, "go on", "acc-th")
    account = dict(frames)["done"]["route"]["account"]
    assert account["note"] == build_routes.THINKING_DROPPED_NOTE
    assert messages_of(app, sid)[-1]["route"]["account"]["note"] == build_routes.THINKING_DROPPED_NOTE


# --------------------------------------------------------------------------- #
# Iron-Proxy off / no account: exactly Phase 4
# --------------------------------------------------------------------------- #


async def test_with_iron_proxy_off_a_turn_is_exactly_phase_4(app, work, monkeypatch):
    seen: list[dict] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append({"body": body, "pin": P.current(), **kw})
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    pins: list = []

    class Seeing(Script):
        async def stream(self, **kw):
            pins.append(P.current())
            async for f in super().stream(**kw):
                yield f

    wire(app, Seeing(["plain answer"], who=("anthropic", "claude-sonnet-4-5")))
    row = S.create(app.state.platform.engine, str(work), "", {"provider": "anthropic", "model": "claude-sonnet-4-5"})
    frames = await _turn(app, row.id, "hi", "acc-off")
    body = seen[0]["body"]
    assert body.provider == "anthropic" and body.model == "claude-sonnet-4-5"
    assert seen[0]["pin"] is None and pins == [None]
    assert "account" not in dict(frames)["done"]["route"]
    assert "account" not in setup_of(app, row.id)


async def test_iron_proxy_turned_off_under_an_account_refuses_in_words(app, fake, work, monkeypatch):
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour())
    sid = await _session(app, work)
    status, _ = await asgi(app, "POST", "/iron-proxy/disable")
    assert status == 200
    status, raw = await open_turn(app, sid, {"text": "hi", "turn_id": "acc-offd"})
    assert status == 409
    detail = json.loads(raw)["detail"]
    assert '"Work Max"' in detail and "Iron-Proxy is off" in detail
    assert messages_of(app, sid) == [] and beh.calls == []


async def test_a_session_without_an_account_takes_the_default_and_keeps_it(app, fake, work, monkeypatch):
    sid = await _session(app, work)  # Iron-Proxy off: no account
    assert "account" not in setup_of(app, sid)
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(["ok"]))
    fake.queries.clear()
    await _turn(app, sid, "hi", "acc-def")
    assert setup_of(app, sid)["account"] == "c-work" and beh.leases == [("claude-cli", "c-work")]
    _only_pinned(fake, "c-work")


async def test_iron_proxy_on_with_no_account_for_the_vendor_is_exactly_phase_4(app, fake, work, monkeypatch):
    """Only Codex accounts: a Claude session has none, its turn is unpinned
    and runs on this PC's own login (Iron-Proxy answers NO_PROFILE)."""
    for pid in ("c-work", "c-spare", "c-gone"):
        del fake.profiles[pid]
        fake.states.pop(pid, None)
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(["mine"]))
    sid = await _session(app, work)
    assert "account" not in setup_of(app, sid)
    frames = await _turn(app, sid, "hi", "acc-none")
    assert beh.leases == [("claude-cli", None)] and beh.pins == [None]
    assert "account" not in dict(frames)["done"]["route"]
    assert all("pinned" not in p for p in picks(fake))


async def test_a_turn_the_account_finished_clears_the_card_and_the_wait(app, fake, work, monkeypatch):
    """The user carried on themselves after arming "Resume when ready": the
    finished turn ends the wait, so no continuation answers a question nobody
    asked later."""
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=1.0)
    await asyncio.sleep(1.2)
    beh.moves = ["I am back."]
    frames = await _turn(app, sid, "are you there?", "acc-manual")
    assert frames[-1][0] == "done"
    setup = setup_of(app, sid)
    assert "account_wait" not in setup and "account_limit" not in setup
    assert await build_routes.resume_due(app.state.d) == []


async def test_a_pinned_turn_is_held_on_its_provider_through_a_transient_failure(
    app, fake, work, monkeypatch,
):
    """An OVERLOAD (529 — the provider's, not an account's) would fail an
    ordinary explicit pick over to the default provider. A pinned turn is
    HELD: the error is the turn's, and the default provider is never called
    (its account would be one nobody picked)."""
    from iron_jarvis.providers import router as router_mod

    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)  # no backoff waits
    await enable(app)
    app.state.platform.config.default_provider = "codex-cli"
    beh = wire_doubles(app, monkeypatch, Behaviour(["never"]))
    beh.overload["claude-cli"] = 10
    sid = await _session(app, work)
    frames = await _turn(app, sid, "hi", "acc-held")
    assert frames[-1][0] == "error"
    assert set(beh.attempts) == {"claude-cli"}, beh.attempts
    # The SAME failure without a pin (an ordinary chat-shaped call) moves on.
    beh.attempts.clear()
    beh.overload["claude-cli"] = 10
    from iron_jarvis.providers.adapters.base import LLMMessage

    async for _f in app.state.platform.router.stream(
        provider="claude-cli", model="claude-sonnet-4-5", system="",
        messages=[LLMMessage(role="user", content="x")], tools=[],
    ):
        pass
    assert "codex-cli" in beh.attempts, "unpinned, the default provider is tried (control)"


async def test_no_failover_ever_moves_a_pinned_turn(app, fake, work, monkeypatch):
    """The router HOLDS a pinned turn: an account refusal on claude-cli never
    reaches codex-cli (or the default provider), even though both are up."""
    await enable(app)
    app.state.platform.config.default_provider = "codex-cli"
    beh = wire_doubles(app, monkeypatch, Behaviour(["LIMIT"]))
    sid = await _session(app, work)
    frames = await _turn(app, sid, "hi", "acc-nf")
    assert frames[-1][0] == "error"
    assert [p for p, _ in beh.leases] == ["claude-cli"], beh.leases
    assert not any(c["provider"] == "codex-cli" for c in beh.calls)


async def test_an_iron_proxy_too_old_for_pinning_is_said_and_nothing_runs(tmp_path, app, work, monkeypatch):
    from fake_iron_proxy_v1301 import FEATURES_PICK_PROFILE

    stale = make_fake(tmp_path, features=FEATURES_PICK_PROFILE)
    try:
        await enable(app)
        beh = wire_doubles(app, monkeypatch, Behaviour(["never"]))
        sid = await _session(app, work)
        frames = await _turn(app, sid, "hi", "acc-old")
        err = dict(frames)["error"]
        assert accounts.PINNED_TOO_OLD in err["detail"] and "account_limit" not in dict(frames)
        assert beh.calls == [] and picks(stale) == []
    finally:
        stale.stop()
