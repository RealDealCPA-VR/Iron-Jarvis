"""v1.368.0 REVIEW (IJ harness Phase 5): the defects the review found, each
pinned with a mutation that turns it red.

1. "Resume when ready" started its continuation even when the user CANCELLED
   the wait, or CHOSE ANOTHER ACCOUNT, while the daemon was re-checking the
   account (the check is an Iron-Proxy call that can take seconds) — and it
   cleared the wait and the limit card only AFTER the continuation began, so
   the continuation's own limit card could be wiped. The wait is now TAKEN in
   one locked write right before the continuation's question
   (``sessions.take_wait``).
2. A turn the USER ran on the account after arming the wait — served, then
   failed for another reason or was stopped — left the wait due, and the
   loop then "carried on" with work the user had abandoned: a continuation
   answering a question nobody asked. An account that served ends the wait.
3. A one-shot made inside a Build turn (its automatic compaction) failed over
   to ANOTHER provider when the pinned account failed: the router holds the
   turn, but these utilities call the adapter directly.
4. The thinking-signature replay fired on ANY 400 that mentions "thinking"
   (``thinking.budget_tokens``, temperature-with-thinking): a wasted second
   call and a false note on the receipt.
5. Shutdown cut the turns off BEFORE stopping the wait loop, so a wait due at
   that moment could start a continuation nothing would save.
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

from iron_jarvis.daemon import app as app_module
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import sessions as S
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ProviderError
from tests.test_build_account_turns_v1368 import _events, _limit_then_arm, _turn
from tests.test_build_accounts_v1368 import isolate, make_fake, picks
from tests.test_build_sessions_v1367 import messages_of, open_turn, setup_of, turn_log, until
from tests.test_chat_turn_stop_v1241 import _asgi_post


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


def _continuations(app, sid: str) -> list[dict]:
    return [m for m in messages_of(app, sid) if m.get("continuation")]


# --------------------------------------------------------------------------- #
# 1. the wait is taken, atomically, right before the continuation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("meanwhile", ["cancel", "choose another account"])
async def test_a_wait_ended_while_the_account_is_rechecked_starts_nothing(
    app, fake, work, monkeypatch, meanwhile,
):
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=1.0)
    await asyncio.sleep(1.2)  # the reset passed: the account is usable again
    engine = app.state.platform.engine
    real_check = build_routes.check_account

    def user_acts_during_the_check(platform, setup):
        # The pinned pick is an Iron-Proxy call (seconds when it is starting);
        # the user presses Cancel / Choose account while it is in flight.
        if meanwhile == "cancel":
            S.update(engine, sid, setup={"account_wait": None})
        else:
            now = build_routes._setup_of(S.get_row(engine, sid))
            S.update(engine, sid, setup=build_routes.account_patch(platform, now, "c-spare"))
        return real_check(platform, setup)

    monkeypatch.setattr(build_routes, "check_account", user_acts_during_the_check)
    calls = len(beh.calls)
    fake.queries.clear()
    assert await build_routes.resume_due(app.state.d) == []
    assert len(beh.calls) == calls, "no continuation ran"
    assert _continuations(app, sid) == [], "no continuation question was written"
    assert _events(app, "build.resumed") == []
    if meanwhile != "cancel":
        assert setup_of(app, sid)["account"] == "c-spare"
        assert all(p.get("profileId") != "c-spare" for p in picks(fake)), "nothing ran on the new account"


async def test_the_wait_and_the_old_card_end_before_the_continuations_question(app, fake, work, monkeypatch):
    """No clear may run AFTER the continuation began: a continuation that hits
    the limit again writes its own card, which a late clear would wipe."""
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=1.0)
    await asyncio.sleep(1.2)
    seen: list[dict] = []
    real_append = S.append_lines

    def spy(eng, tid, lines, **kw):
        if any(isinstance(m, dict) and m.get("continuation") for m in lines):
            setup = build_routes._setup_of(S.get_row(eng, tid))
            seen.append({k: setup.get(k) for k in ("account_wait", "account_limit")})
        return real_append(eng, tid, lines, **kw)

    monkeypatch.setattr(S, "append_lines", spy)
    assert await build_routes.resume_due(app.state.d) == [sid]
    assert seen == [{"account_wait": None, "account_limit": None}], seen
    turn_id = _events(app, "build.resumed")[-1]["turn_id"]
    lg = await until(lambda: turn_log(app, turn_id))
    await until(lambda: lg.status != "running")
    assert "account_wait" not in setup_of(app, sid)


def test_take_wait_takes_only_the_wait_it_was_given(tmp_path, work):
    """The contract the loop leans on: the SAME wait (armed at that moment)
    for the SAME account the session is still on — and only once."""
    app = create_app(str(tmp_path / "home2"))
    engine = app.state.platform.engine
    row = S.create(engine, S.resolved_folder(str(work)), "t", {"provider": "claude-cli"})
    sid = row.id
    wait = {"account": "c-work", "account_name": "Work Max", "resets_at": "",
            "armed_at": "2026-10-22T00:00:00+00:00", "next_check_at": "2026-10-22T00:00:00+00:00"}
    card = {"account": "c-work", "kind": "usage"}
    S.update(engine, sid, setup={"account": "c-work", "account_wait": wait, "account_limit": card})
    assert S.take_wait(engine, sid, "2026-10-22T00:00:01+00:00", "c-work") is False, "another wait"
    # The session moved to another account while this wait stayed behind.
    S.update(engine, sid, setup={"account": "c-spare"})
    assert S.take_wait(engine, sid, wait["armed_at"], "c-work") is False, "not on that account now"
    S.update(engine, sid, setup={"account": "c-work"})
    assert S.take_wait(engine, sid, wait["armed_at"], "c-work") is True
    setup = build_routes._setup_of(S.get_row(engine, sid))
    assert "account_wait" not in setup and "account_limit" not in setup
    assert S.take_wait(engine, sid, wait["armed_at"], "c-work") is False, "taken once"


# --------------------------------------------------------------------------- #
# 2. an account that served the user's own turn ends the wait
# --------------------------------------------------------------------------- #


async def test_a_turn_the_user_ran_on_the_account_that_failed_ends_the_wait(app, fake, work, monkeypatch):
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=1.0)
    await asyncio.sleep(1.2)
    beh.moves = ["FAIL"]  # served (the pinned lease succeeded), then refused for another reason
    frames = await _turn(app, sid, "also add a test", "rv-own")
    assert frames[-1][0] == "error"
    assert "account_wait" not in setup_of(app, sid), "the account served: nothing is waiting any more"
    calls = len(beh.calls)
    assert await build_routes.resume_due(app.state.d) == []
    assert len(beh.calls) == calls and _continuations(app, sid) == []


class _HeldDouble(LLMAdapter):
    """Leases its (pinned) account, says a few words, then waits for the
    test's gate — a turn the user can Stop mid-way."""

    def __init__(self, gate: asyncio.Event, leases: list) -> None:
        self.provider = "claude-cli"
        self.model = "subscription"
        self.gate = gate
        self.leases = leases

    async def complete(self, **kw):  # pragma: no cover — the build lane streams
        raise AssertionError("not used")

    async def stream(self, *, system, messages, tools, **kw):
        lease = await asyncio.to_thread(accounts.lease, self.provider)
        self.leases.append(lease.profile_id if lease is not None else None)
        yield {"type": "text", "text": "half an answer "}
        await self.gate.wait()
        yield {"type": "final", "response": LLMResponse(text="half an answer done")}


async def test_a_turn_the_user_stopped_on_the_account_ends_the_wait(app, fake, work, monkeypatch):
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=1.0)
    await asyncio.sleep(1.2)
    gate = asyncio.Event()
    leases: list = []
    app.state.platform.providers.register("claude-cli", lambda model=None: _HeldDouble(gate, leases))
    drop = asyncio.Event()
    await open_turn(app, sid, {"text": "do the other thing", "turn_id": "rv-stop"},
                    drop=drop, stop_after="half an answer")
    assert (await _asgi_post(app, "/chat/turns/rv-stop/stop"))[0] == 200
    gate.set()
    lg = await until(lambda: turn_log(app, "rv-stop"))
    await until(lambda: lg.status != "running")
    assert lg.status == "stopped" and leases == ["c-work"]
    await until(lambda: "account_wait" not in setup_of(app, sid))
    assert "account_wait" not in setup_of(app, sid), "the user took over: the wait is over"
    calls = len(beh.calls)
    assert await build_routes.resume_due(app.state.d) == []
    assert len(beh.calls) == calls and _continuations(app, sid) == [], "the stopped work is not carried on"


async def test_a_turn_iron_proxy_could_not_lease_keeps_the_wait(app, fake, work, monkeypatch):
    """The account never served — Iron-Proxy did not answer the pinned pick —
    so a momentary outage during the user's own turn does not throw the
    wait away."""
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=600)

    def not_answering(spec, pin):
        raise accounts._unanswered_error(spec, RuntimeError("it did not answer in time"))

    monkeypatch.setattr(accounts, "_pinned_lease", not_answering)
    frames = await _turn(app, sid, "and the docs", "rv-down")
    assert frames[-1][0] == "error" and "account_limit" not in [e for e, _ in frames]
    assert setup_of(app, sid)["account_wait"]["account"] == "c-work"


async def test_a_turn_the_account_refused_again_keeps_the_wait(app, fake, work, monkeypatch):
    """The other side: the user's own turn that the PARKED account refused
    keeps the wait (the account never served)."""
    beh, sid, card, view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=600)
    frames = await _turn(app, sid, "and the tests too", "rv-again")
    assert [e for e, _ in frames][-2:] == ["account_limit", "error"]
    assert setup_of(app, sid)["account_wait"]["account"] == "c-work"


# --------------------------------------------------------------------------- #
# 3. a one-shot inside a Build turn is held on the account like the turn
# --------------------------------------------------------------------------- #


class _OneShot:
    def __init__(self, provider: str, fail: bool) -> None:
        self.provider = provider
        self.model = "m"
        self.fail = fail
        self.calls = 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.fail:
            raise ProviderError(f"{self.provider}: overloaded", status_code=529)
        return LLMResponse(text=f"from {self.provider}")


@pytest.fixture
def one_shot(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home1"))
    platform = app.state.platform
    primary = _OneShot("claude-cli", fail=True)
    stand_in = _OneShot("ollama", fail=False)

    async def once(adapter, *, system, messages, tools, attempts=3):
        return await adapter.complete(system=system, messages=messages, tools=tools)

    def get(name, *a, **k):
        if name == "ollama":
            return stand_in
        raise KeyError(name)

    monkeypatch.setattr(app_module, "_complete_with_retry", once)
    monkeypatch.setattr(platform.providers, "available", lambda name: name == "ollama")
    monkeypatch.setattr(platform.providers, "get", get)
    return types.SimpleNamespace(d=app.state.d, primary=primary, stand_in=stand_in)


async def test_a_one_shot_fails_over_without_a_pin(one_shot):
    resp, used, _m = await one_shot.d._one_shot_complete(
        "claude-cli", one_shot.primary, system="s", messages=[],
    )
    assert used == "ollama" and resp.text == "from ollama", "the control: a stand-in exists"


async def test_a_one_shot_under_a_build_pin_never_fails_over(one_shot):
    token = P.set_pin(P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max"))
    try:
        with pytest.raises(HTTPException):
            await one_shot.d._one_shot_complete("claude-cli", one_shot.primary, system="s", messages=[])
        # The compaction factory never swaps in another provider either.
        assert one_shot.d._compaction_complete("not-a-provider") is None
    finally:
        P.reset_pin(token)
    assert one_shot.primary.calls == 1 and one_shot.stand_in.calls == 0
    assert one_shot.d._compaction_complete("not-a-provider") is not None, "the control: unpinned it would"


# --------------------------------------------------------------------------- #
# 4. only a refused thinking BLOCK is sent again without the thinking
# --------------------------------------------------------------------------- #

_REFUSED = [
    "messages.1.content.0: Invalid `signature` in `thinking` block",
    "messages.3.content.1: `thinking` or `redacted_thinking` blocks in the latest assistant message "
    "cannot be modified. These blocks must remain as they were in the original response.",
    "messages.1.content.0.data: Invalid `data` in `redacted_thinking` block",
]
_ABOUT_SOMETHING_ELSE = [
    "`max_tokens` must be greater than `thinking.budget_tokens`. Please consult our documentation at "
    "https://docs.claude.com/en/docs/build-with-claude/extended-thinking#max-tokens-and-context-window-size",
    "thinking.type.enabled.budget_tokens: Input should be greater than or equal to 1024",
    "`temperature` may only be set to 1 when thinking is enabled. Please consult our documentation.",
    "messages.1.content.0.type: Expected `thinking` or `redacted_thinking`, but found `text`. When "
    "`thinking` is enabled, a final `assistant` message must start with a thinking block (preceeding the "
    "lastmost set of `tool_use` and `tool_result` blocks). We recommend you include thinking blocks from "
    "previous turns.",
    "max_tokens: too large",
]


def _attempt(text: str, status: int = 400):
    return types.SimpleNamespace(relay_used=True, status=status, error_text=text)


@pytest.mark.parametrize("text", _REFUSED)
def test_a_refused_thinking_block_is_recognised(text):
    assert sc.refused_thinking(_attempt(text)) is True


@pytest.mark.parametrize("text", _ABOUT_SOMETHING_ELSE)
def test_a_400_that_only_mentions_thinking_is_not_a_refused_block(text):
    assert sc.refused_thinking(_attempt(text)) is False


async def test_a_thinking_budget_400_is_not_sent_again(tmp_path, monkeypatch):
    from tests.test_build_account_usage_v1368 import Anthropic, Claude, _err_body, _thinking_turn

    up = Anthropic({}, answers=[(400, _err_body("invalid_request_error", _ABOUT_SOMETHING_ELSE[0]), {})])
    try:
        claude = Claude(tmp_path, monkeypatch, "relay_auto", native_text="API Error: 400")
        with pytest.raises(ProviderError):
            await claude.adapter(up.url).complete(system="", messages=_thinking_turn(), tools=[])
        assert up.hits == 1, "one call: dropping the thinking would not change this answer"
    finally:
        up.stop()


# --------------------------------------------------------------------------- #
# 5. shutdown stops "Resume when ready" before it cuts the turns off
# --------------------------------------------------------------------------- #


def test_shutdown_stops_the_wait_loop_before_the_turns_are_cut_off():
    src = Path(app_module.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    stop_waits = src.index('bg_tasks.pop("build_accounts", None)')
    cut_turns = src.index("_turn_logs.shutdown()")
    assert stop_waits < cut_turns
