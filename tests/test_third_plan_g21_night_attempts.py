"""Third north-star plan, group 21 ("done means done, every cost counted"):
A NIGHT TASK TRIED ON SEVERAL MODELS FOLLOWS THE NIGHT'S OWN RULES.

* NA1 (decision G10): it ends from its attempts' card — "Needs you: choose
  one" when at least one attempt finished, "Failed" when none did; never
  "Done" (``attempts.card_reading``, read by ``night.outcome_of``).
* NA2: its Last night row counts each attempt's usage (the same reading).
* NA3 (decision G11): at night the pay-per-use limit applies to EVERY pick,
  the default model included; the attempts billed per use share what is left
  of the limit (``Plan.pay_room``, ``SharedCap``).
* NA4: an attempt never starts with no account chosen when the accounts list
  could not be read (the ordinary reply's own refusal).
* NA6: a night task that fails to start says so in plain words.
* NA7 (decision G12): "Night tasks at once" counts a task tried on three
  models as three.

Driven through the REAL night loop (``routes/night.night_pass``), the real
attempts start (``routes/build_attempts.maybe_start``), the real stream lane
per attempt and real git in ``tmp_path``; only the models are scripted
doubles (the attempts suite's own) and the night clock is injected.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from iron_jarvis.agents import model_team as MT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build_attempts as BAR
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import attempts as A
from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_morning as M
from iron_jarvis.harness import sessions as S
from tests.test_build_accounts_v1368 import isolate
from tests.test_build_attempts_v1397 import CLAUDE, CODEX, LOCAL, World, make_calc_repo, wire_models, write
from tests.test_build_git_v1375 import no_real_gh
from tests.test_build_sessions_v1367 import asgi, messages_of
from tests.test_night_shift_review_v1398 import _ended

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    no_real_gh(monkeypatch)
    yield from isolate(tmp_path, monkeypatch)


def _night_clock(monkeypatch) -> None:
    monkeypatch.setattr(N, "CLOCK", lambda: datetime(2026, 10, 26, 23, 30).astimezone())


async def _broke(world):  # noqa: ANN001
    raise RuntimeError("the stand-in model's server went away")


def _app(tmp_path, monkeypatch, *, default=LOCAL):
    _night_clock(monkeypatch)
    app = create_app(str(tmp_path / "home"))
    cfg = app.state.platform.config
    cfg.default_provider, cfg.default_model = default
    return app


def _card(app, task: dict) -> dict:
    line = N.last_reply_line(messages_of(app, task["thread_id"]))
    assert line is not None and isinstance(line.get(A.LINE_KEY), dict), line
    return line[A.LINE_KEY]


def _item(card: dict, provider: str) -> dict:
    return next(i for i in card["items"] if i["provider"] == provider)


# --------------------------------------------------------------------------- #
# NA1 + NA2: the night's ending and the row's usage, from the card
# --------------------------------------------------------------------------- #


async def test_na1_na2_a_night_task_whose_attempts_all_failed_is_failed_and_its_row_counts_their_usage(
    tmp_path, monkeypatch,
):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)
    wire_models(app, monkeypatch, World({
        "codex-cli": [write("c1", "notes_codex.txt", "codex\n"), _broke],
        "ollama": [write("o1", "notes_local.txt", "local\n"), _broke],
    }))
    engine = app.state.platform.engine
    task = N.add_task(engine, folder=str(repo), words="write the notes file",
                      try_on=[{"provider": CODEX[0], "model": CODEX[1]}, {"provider": LOCAL[0], "model": LOCAL[1]}])
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [task["id"]], out
    ended = await _ended(app, task["id"], 120)
    card = _card(app, ended)
    assert all(i["state"] == "failed" for i in card["items"]), card["items"]
    # NA1: never "done" over attempts that all failed.
    assert ended["state"] == N.FAILED, (ended["state"], ended["notes"])
    err = [n["text"] for n in ended["notes"] if n["kind"] == "error"]
    assert err and "none finished" in err[-1], ended["notes"]
    # The morning: the row and the ONE notice say "failed".
    await night_routes.morning_pass(app.state.d, [task["id"]])
    nights = M.list_summaries(engine)
    row = next(r for n in nights for r in n["rows"] if r["id"] == task["id"])
    assert row["state"] == "failed" and "none finished" in row.get("failure", ""), row
    assert nights[0]["words"]["title"] == "Last night: 1 failed", nights[0]["words"]
    assert M.TRY_ON_NONE_NOTE in row["commit_note"], row["commit_note"]
    # NA2: the row's usage is the sum of the attempts' own receipts.
    want = sum(int(i["usage"].get("input_tokens") or 0) + int(i["usage"].get("output_tokens") or 0)
               for i in card["items"])
    got = sum(int(u["input_tokens"]) + int(u["output_tokens"]) for u in row["usage"])
    assert want > 0 and got == want, (row["usage"], [i["usage"] for i in card["items"]])
    assert {u["provider"] for u in row["usage"]} == {CODEX[0], LOCAL[0]}, row["usage"]


def _line(*states: str) -> dict:
    items = []
    for n, st in enumerate(states, start=1):
        why = {"done": "finished", "failed": "failed", "stopped": "stopped", "limit": "limit"}[st]
        words = {"done": "Finished", "failed": "Failed: the model broke", "stopped": "Stopped at its step limit (40 steps)",
                 "limit": "Stopped at an account limit"}[st]
        items.append({"id": f"try{n}", "label": f"Model {n}", "provider": f"p{n}", "model": f"m{n}",
                      "state": st, "why": why, "words": words,
                      "usage": {"input_tokens": 100 * n, "output_tokens": 10 * n},
                      "account_name": "Work" if n == 1 else ""})
    return {"role": "assistant", "content": "Tried this on ...", "turnId": "bt_1",
            A.LINE_KEY: {"items": items, "task": "x", "base": "main", "base_commit": "a" * 40, "adopt": False}}


def test_na1_one_finished_is_needs_you_choose_one_and_all_finished_is_needs_you_never_done():
    task = {"id": "night_1", "notes": []}
    state, notes = N.outcome_of(task, _line("done", "failed", "stopped"))
    assert state == N.NEEDS_YOU, (state, notes)
    said = dict(notes).get("attempts", "")
    assert "1 finished and 2 did not" in said and "Choose one" in said, notes
    state, notes = N.outcome_of(task, _line("done", "done", "done"))
    assert state == N.NEEDS_YOU and "all 3 finished" in dict(notes).get("attempts", ""), (state, notes)
    state, notes = N.outcome_of(task, _line("failed", "limit", "stopped"))
    assert state == N.FAILED, (state, notes)
    failed = dict(notes)["error"]
    assert "none finished" in failed and "Model 2: Stopped at an account limit" in failed, failed
    # The row says what needs the user, and the notice counts it there.
    _s, notes = N.outcome_of(task, _line("done", "failed"))
    row = M.summary_row({"id": "night_1", "state": N.NEEDS_YOU, "words": "fix it",
                         "notes": [{"kind": k, "text": t} for k, t in notes]})
    assert any("Choose one" in n for n in row["needs"]), row["needs"]
    assert M.notice_words([row])["title"] == "Last night: 1 needs you"


def test_na2_usage_by_account_sums_each_attempts_receipt():
    rows = M.usage_by_account([_line("done", "failed", "stopped")])
    by = {(r["account"], r["provider"]): r for r in rows}
    assert by[("Work", "p1")]["input_tokens"] == 100 and by[("", "p3")]["output_tokens"] == 30, rows
    assert sum(r["input_tokens"] for r in rows) == 600, rows


# --------------------------------------------------------------------------- #
# NA3: the pay-per-use limit at night, every pick, one shared remainder
# --------------------------------------------------------------------------- #


def _codex_pays(monkeypatch) -> None:
    real = MT.cost_type

    def cost(platform, provider, model=""):  # noqa: ANN001
        if provider == CODEX[0]:
            return "pay"
        if provider == LOCAL[0]:
            return "free"
        return real(platform, provider, model)

    monkeypatch.setattr(MT, "cost_type", cost)


async def test_na3_at_zero_limit_a_default_model_pick_does_not_run_at_night_and_the_others_do(
    tmp_path, monkeypatch,
):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch, default=CODEX)  # the night session's model IS the pick
    _codex_pays(monkeypatch)
    app.state.platform.config.team_paid_cap_usd = 0.0
    world = wire_models(app, monkeypatch, World({"codex-cli": ["Codex finished."], "ollama": ["Local finished."]}))
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes file",
                      try_on=[{"provider": CODEX[0], "model": CODEX[1]}, {"provider": LOCAL[0], "model": LOCAL[1]}])
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [task["id"]], (out, N.get_task(app.state.platform.engine, task["id"]))
    ended = await _ended(app, task["id"], 120)
    assert not [c for c in world.calls if c["provider"] == CODEX[0]], "a pay-per-use pick ran at night at a $0 limit"
    card = _card(app, ended)
    codex = _item(card, CODEX[0])
    assert codex["state"] == "failed" and codex["words"].startswith(A.NOT_STARTED + ":"), codex
    assert "$0" in codex["words"] and "nothing started" not in codex["words"].lower(), codex["words"]
    assert _item(card, LOCAL[0])["state"] == "done"
    assert ended["state"] == N.NEEDS_YOU, ended


async def test_na3_when_no_pick_may_run_the_night_task_fails_with_the_reason(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch, default=CODEX)
    monkeypatch.setattr(MT, "cost_type", lambda platform, provider, model="": "pay")
    app.state.platform.config.team_paid_cap_usd = 0.0
    world = wire_models(app, monkeypatch, World({}))
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes file",
                      try_on=[{"provider": CODEX[0], "model": CODEX[1]}, {"provider": LOCAL[0], "model": LOCAL[1]}])
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"], 60)
    assert ended["state"] == N.FAILED and not world.calls, ended
    err = [n["text"] for n in ended["notes"] if n["kind"] == "error"][-1]
    assert err.startswith("None of the models to try it on could start") and "$0" in err, err


async def test_na3_the_attempts_billed_per_use_share_what_is_left_of_the_limit(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)
    _codex_pays(monkeypatch)
    app.state.platform.config.team_paid_cap_usd = 1.0
    caps: dict[str, object] = {}
    real_cap = A.attempt_cap

    def seen(plan, a):  # noqa: ANN001
        got = real_cap(plan, a)
        caps[a.target.provider] = got
        return got

    monkeypatch.setattr(A, "attempt_cap", seen)
    wire_models(app, monkeypatch, World({"codex-cli": ["Codex finished."], "ollama": ["Local finished."]}))
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes file",
                      try_on=[{"provider": CODEX[0], "model": CODEX[1]}, {"provider": LOCAL[0], "model": LOCAL[1]}])
    await night_routes.night_pass(app.state.d)
    await _ended(app, task["id"], 120)
    codex = caps.get(CODEX[0])
    assert codex is not None and getattr(codex, "pay_usd", None) == pytest.approx(1.0), caps
    # The free model keeps the night's time limit only (its own minutes).
    assert getattr(caps.get(LOCAL[0]), "pay_usd", None) is None, "a free model got the pay-per-use limit"


def test_na3_three_attempts_cannot_together_be_handed_more_than_the_remainder():
    def attempt(n: int, spent: float, cost: str = "pay") -> A.Attempt:
        a = A.Attempt(id=f"try{n}", target=A.Target(label=f"M{n}", provider=f"p{n}", model="m"), pin=None,
                      git={}, folder="", sid=f"s{n}", turn_id=f"u.try{n}", cost=cost)
        a.usage["cost_usd"] = spent
        return a

    a1, a2, a3, free = attempt(1, 0.5), attempt(2, 0.3), attempt(3, 0.0), attempt(4, 5.0, "free")
    plan = A.Plan(thread_id="t", turn_id="u", task="x", history=[], summary="", setup={}, project_id="",
                  start=None, attempts=[a1, a2, a3, free], pay_room=1.0)
    cap = A.attempt_cap(plan, a3)
    assert cap is not None
    assert cap.met(0.1) is None
    met = cap.met(0.25)  # 0.5 + 0.3 + 0.25 >= 1.0: the shared remainder is used up
    assert met == {"kind": "cost", "limit": 1.0}, met
    assert A.attempt_cap(plan, free) is None, "a free model's attempt got the pay-per-use limit"


# --------------------------------------------------------------------------- #
# NA4: no attempt starts unpinned when the accounts list could not be read
# --------------------------------------------------------------------------- #


class _UnreadAccounts:
    """Iron-Proxy turned on whose accounts list cannot be read."""

    enabled = True

    def cached_accounts(self):
        return None

    def check(self):
        return True

    def refresh_accounts(self):
        raise RuntimeError("the account manager did not answer")


async def test_na4_at_night_an_attempt_whose_accounts_are_unread_is_refused_and_the_others_run(
    tmp_path, monkeypatch,
):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)
    app.state.platform.iron_proxy = _UnreadAccounts()
    world = wire_models(app, monkeypatch, World({"codex-cli": ["Codex finished."], "ollama": ["Local finished."]}))
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes file",
                      try_on=[{"provider": CODEX[0], "model": CODEX[1]}, {"provider": LOCAL[0], "model": LOCAL[1]}])
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"], 120)
    unpinned = [c for c in world.calls if c["provider"] == CODEX[0]]
    assert not unpinned, f"an attempt started with no account chosen: {unpinned}"
    card = _card(app, ended)
    codex = _item(card, CODEX[0])
    assert codex["state"] == "failed" and BA.NOT_READ_NOTE in codex["words"], codex
    assert _item(card, LOCAL[0])["state"] == "done"


async def test_na4_by_day_the_request_is_refused_in_the_ordinary_replys_words(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)
    app.state.platform.iron_proxy = _UnreadAccounts()
    world = wire_models(app, monkeypatch, World({"codex-cli": ["Codex finished."], "ollama": ["Local finished."]}))
    status, view = await asgi(app, "POST", "/build/sessions",
                              {"folder": str(repo), "provider": LOCAL[0], "model": LOCAL[1]})
    assert status == 201, view
    from iron_jarvis.daemon.routes.build import TryOnPick

    body = SimpleNamespace(try_on=[TryOnPick(provider=CODEX[0], model=CODEX[1]),
                                   TryOnPick(provider=LOCAL[0], model=LOCAL[1])],
                           attachments=[], files=[], skill="", folder_skill="", command="")
    with pytest.raises(HTTPException) as caught:
        await BAR.maybe_start(app.state.d, view["id"], "write the notes file", "bt_day1", body)
    assert caught.value.status_code == 409 and BA.NOT_READ_NOTE in str(caught.value.detail), caught.value.detail
    assert not world.calls


# --------------------------------------------------------------------------- #
# NA6: a night task that fails to start says so in plain words
# --------------------------------------------------------------------------- #


async def test_na6_a_night_task_that_could_not_start_carries_no_exception_name(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)

    def boom(folder):  # noqa: ANN001
        raise KeyError("folder")

    monkeypatch.setattr(S, "folder_problem", boom)
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes file")
    await night_routes.night_pass(app.state.d)
    ended = N.get_task(app.state.platform.engine, task["id"])
    assert ended["state"] == N.FAILED, ended
    said = [n["text"] for n in ended["notes"] if n["kind"] == "error"]
    assert said == ["It could not start because of an internal error."], said
    assert all("KeyError" not in n["text"] for n in ended["notes"]), ended["notes"]


# --------------------------------------------------------------------------- #
# NA7: "Night tasks at once" counts replies; a try-on task runs that many at a time
# --------------------------------------------------------------------------- #


def _stub_start(monkeypatch) -> list[str]:
    started: list[str] = []

    async def start(d, task):  # noqa: ANN001
        started.append(task["id"])
        return True

    monkeypatch.setattr(night_routes, "start_task", start)
    return started


def _three() -> list[dict]:
    return [{"provider": "p1", "model": "m"}, {"provider": "p2", "model": "m"}, {"provider": "p3", "model": "m"}]


async def test_na7_a_try_on_task_counts_as_its_picks_and_waits_for_room(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    engine = app.state.platform.engine
    app.state.platform.config.night_max_at_once = 3
    started = _stub_start(monkeypatch)
    one = N.add_task(engine, folder=str(tmp_path / "a"), words="an ordinary task")
    assert N.mark_running(engine, one["id"], "2026-10-26")
    tried = N.add_task(engine, folder=str(tmp_path / "b"), words="tried on three", try_on=_three())
    await night_routes.night_pass(app.state.d)
    assert started == [], "a task tried on three models started beside one running reply (3 at once)"
    assert N.get_task(engine, tried["id"])["state"] == N.QUEUED
    # With nothing running it fits, and fills every place: the next one waits.
    N.finish_task(engine, one["id"], N.DONE)
    later = N.add_task(engine, folder=str(tmp_path / "c"), words="another ordinary task")
    await night_routes.night_pass(app.state.d)
    assert started == [tried["id"]], started
    assert later["id"] not in started


async def test_na7_a_try_on_task_with_more_picks_than_the_setting_still_starts_and_fills_the_room(
    tmp_path, monkeypatch,
):
    app = _app(tmp_path, monkeypatch)
    engine = app.state.platform.engine
    app.state.platform.config.night_max_at_once = 2
    started = _stub_start(monkeypatch)
    tried = N.add_task(engine, folder=str(tmp_path / "b"), words="tried on three", try_on=_three())
    small = N.add_task(engine, folder=str(tmp_path / "c"), words="an ordinary task")
    await night_routes.night_pass(app.state.d)
    # Never "Failed" for its size: it starts, two of its attempts at a time,
    # and that fills the night's two places, so the next task waits.
    assert started == [tried["id"]], started
    assert N.get_task(engine, small["id"])["state"] == N.QUEUED


def _concurrency(monkeypatch) -> dict:
    """Wrap the real attempt runner: who began, how many run at once, the
    most at once, and an event once two run (no clock involved)."""
    seen: dict = {"entered": [], "now": 0, "peak": 0, "two_in": asyncio.Event()}
    real = A._run_one

    async def run_one(platform, personas, plan, a, queue, stopped):  # noqa: ANN001
        seen["entered"].append(a.target.provider)
        seen["now"] += 1
        seen["peak"] = max(seen["peak"], seen["now"])
        if seen["now"] >= 2:
            seen["two_in"].set()
        try:
            await real(platform, personas, plan, a, queue, stopped)
        finally:
            seen["now"] -= 1

    monkeypatch.setattr(A, "_run_one", run_one)
    return seen


THREE = [{"provider": CLAUDE[0], "model": CLAUDE[1]}, {"provider": CODEX[0], "model": CODEX[1]},
         {"provider": LOCAL[0], "model": LOCAL[1]}]


async def test_na7_at_two_three_attempts_run_two_at_a_time_and_a_second_task_waits_for_room(
    tmp_path, monkeypatch,
):
    repo = make_calc_repo(tmp_path / "proj")
    other = make_calc_repo(tmp_path / "proj2")
    app = _app(tmp_path, monkeypatch)
    engine = app.state.platform.engine
    assert app.state.platform.config.night_max_at_once == 2  # the default
    seen = _concurrency(monkeypatch)
    release = asyncio.Event()

    async def hold(world):  # noqa: ANN001
        await release.wait()
        return "Finished."

    wire_models(app, monkeypatch, World({p["provider"]: [hold] for p in THREE}))
    task = N.add_task(engine, folder=str(repo), words="write the notes file", try_on=THREE)
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [task["id"]], (out, N.get_task(engine, task["id"]))
    await asyncio.wait_for(seen["two_in"].wait(), 60)
    assert len(seen["entered"]) == 2, f"more than two attempts began at once: {seen['entered']}"
    # A second night task waits for room while two of the attempts run.
    second = N.add_task(engine, folder=str(other), words="an ordinary task")
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [] and N.get_task(engine, second["id"])["state"] == N.QUEUED, out
    N.remove_task(engine, second["id"])
    release.set()
    ended = await _ended(app, task["id"], 120)
    card = _card(app, ended)
    assert [i["state"] for i in card["items"]] == ["done", "done", "done"], card["items"]
    assert sorted(seen["entered"]) == sorted(p["provider"] for p in THREE)
    assert seen["peak"] == 2, seen["peak"]
    assert ended["state"] == N.NEEDS_YOU, ended


async def test_na7_at_one_the_attempts_run_one_after_another(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)
    app.state.platform.config.night_max_at_once = 1
    seen = _concurrency(monkeypatch)
    wire_models(app, monkeypatch, World({CODEX[0]: ["Codex finished."], LOCAL[0]: ["Local finished."]}))
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes file",
                      try_on=[{"provider": CODEX[0], "model": CODEX[1]}, {"provider": LOCAL[0], "model": LOCAL[1]}])
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [task["id"]], out
    ended = await _ended(app, task["id"], 120)
    assert seen["peak"] == 1 and len(seen["entered"]) == 2, seen
    assert [i["state"] for i in _card(app, ended)["items"]] == ["done", "done"]
    assert ended["state"] == N.NEEDS_YOU, ended


async def test_na7_stop_reaches_an_attempt_waiting_its_turn(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)
    seen = _concurrency(monkeypatch)
    release = asyncio.Event()

    async def hold(world):  # noqa: ANN001
        await release.wait()
        return "Never said."

    world = wire_models(app, monkeypatch, World({p["provider"]: [hold] for p in THREE}))
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes file", try_on=THREE)
    await night_routes.night_pass(app.state.d)
    await asyncio.wait_for(seen["two_in"].wait(), 60)
    waiting = next(p["provider"] for p in THREE if p["provider"] not in seen["entered"])
    await asyncio.to_thread(night_routes.cancel_night_task, app.state.d, task["id"])
    ended = await _ended(app, task["id"], 120)
    assert ended["state"] == N.STOPPED, ended
    item = _item(_card(app, ended), waiting)
    assert item["state"] == "stopped" and item["words"] == A.STOPPED_BEFORE_START, item
    assert waiting not in seen["entered"] and not [c for c in world.calls if c["provider"] == waiting]
    release.set()


def test_na7_an_attempt_that_waited_its_turn_keeps_its_whole_time_limit(monkeypatch):
    from iron_jarvis.harness import reply_cap as RC

    monkeypatch.setattr(RC, "WALL", lambda: 100_000.0)
    a = A.Attempt(id="try3", target=A.Target(label="M3", provider="p3", model="m"), pin=None, git={},
                  folder="", sid="s3", turn_id="u.try3", cost="plan")
    plan = A.Plan(thread_id="t", turn_id="u", task="x", history=[], summary="",
                  setup={RC.SETUP_KEY: {"minutes": 45}}, project_id="", start=None, attempts=[a],
                  started_at=100_000.0 - 80 * 60)  # the attempts began 80 minutes ago
    a.wall_start = 100_000.0 - 30 * 60  # this one began 30 minutes ago, after waiting its turn
    cap = A.attempt_cap(plan, a)
    assert cap is not None and cap.met(0.0) is None, "the wait ate the attempt's minutes"
    a.wall_start = 100_000.0 - 46 * 60
    assert cap.met(0.0) == {"kind": "time", "limit": 45}


def test_na7_the_backstop_allows_one_limit_per_round_of_attempts():
    cfg = SimpleNamespace(night_task_minutes=90, night_max_at_once=2)
    grace = night_routes.OVERRUN_GRACE_S
    tried = {"try_on": _three(), "ran_s": 0.0}
    assert N.task_rounds(tried, cfg) == 2 and N.task_weight(tried, cfg) == 2
    assert N.overrun_after_s(tried, cfg, grace) == 2 * 90 * 60 + grace
    assert N.overrun_after_s({"ran_s": 0.0}, cfg, grace) == 90 * 60 + grace
    one_at_a_time = SimpleNamespace(night_task_minutes=90, night_max_at_once=1)
    assert N.task_rounds(tried, one_at_a_time) == 3 and N.task_weight(tried, one_at_a_time) == 1


def test_na_vocabulary_no_engine_words_in_the_new_sentences():
    words = [
        A.STOPPED_BEFORE_START,
        A.card_reading(_line("done", "failed")[A.LINE_KEY])["words"],
        A.card_reading(_line("failed", "stopped")[A.LINE_KEY])["words"],
        M.TRY_ON_NONE_NOTE,
        A.pick_refusal_words("Codex is pay per use, and your pay-per-use limit is $0 a month (X), so nothing is "
                             "tried on it. Raise the limit there, or name another model; nothing started."),
    ]
    for w in words:
        low = w.lower()
        for bad in ("card", "turn", "error", "exception", "keyerror", "nothing started"):
            assert bad not in low, (bad, w)
