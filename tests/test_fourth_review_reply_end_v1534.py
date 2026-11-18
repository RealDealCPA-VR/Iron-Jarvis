"""Fourth review, group 26 ("done means done for attempts and the night"):
ONE reading of a reply that was cut short, and attempts that end honestly.

* CS1: a night task whose reply was cut off at the model's output limit read
  "Done". It is now FAILED with the cut words (owner decision D12), and its
  work is still committed (the commit says it failed).
* CS2: an attempt cut off at the output limit read "Finished" and was offered
  as the one to keep. It is now "failed"; a cut and a limit are read in the
  same order as the night (R13: the cut first).
* CS3: an attempts turn was announced "Your coding session finished" from
  its done frame alone: now from its card ("could not finish" when none
  finished). A Stop pressed on an attempts turn sends no "finished" notice,
  and the turn is not recorded as finished (status "stopped", R14).
* CS5: a stop at what was left of the month's pay-per-use limit was worded as
  "this session's cost limit (list-price)". The met cap now says whose limit
  (``"of": "pay_left"``), beside the cleaned cap, never through ``clean``.
* CS4 (decision D4 A): a level or Plan the user sets while attempts run
  reaches them from their next step.
* The drift scan: no reader of a reply's ending in the coding-session files
  has its own copy of the cut rule.

Driven through the REAL night loop, the REAL attempts start and driver, the
REAL stream lane per attempt and the REAL reply watch (``recorded_turn``);
only the models are scripted stand-ins, and the clocks are injected. Nothing
reaches a real model or service, and no value is shaped like a credential.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from iron_jarvis.agents import model_team as MT
from iron_jarvis.comm.notifier import build_finished_title
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.routes import build_attempts as BAR
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import attempts as A
from iron_jarvis.harness import library as _library
from iron_jarvis.harness import night as N
from iron_jarvis.harness import reply_cap as RC
from iron_jarvis.harness import reply_end as RE
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_build_attempts_v1397 import (
    CODEX,
    LOCAL,
    ScriptedModel,
    World,
    make_calc_repo,
    write,
)
from tests.test_build_git_v1375 import git
from tests.test_build_sessions_v1367 import asgi, frames_of, messages_of, open_turn, turn_frames, turn_log, until
from tests.test_night_shift_review_v1398 import _ended
from tests.test_third_plan_g20_small_truths import Clock, _notices_after
from tests.test_third_plan_g21_night_attempts import _app, _isolated  # noqa: F401 — the autouse fixture

pytestmark = pytest.mark.usefixtures("no_network")

SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
MENU = [
    {"provider": CODEX[0], "model": CODEX[1], "available": True, "on_this_network": False},
    {"provider": LOCAL[0], "model": LOCAL[1], "available": True, "on_this_network": True},
]


def cut(text: str, *, cost: float = 0.0) -> LLMResponse:
    """A final answer the model's output limit cut off."""
    usage = {"input_tokens": 30, "output_tokens": 7, **({"cost_usd": cost} if cost else {})}
    return LLMResponse(text=text, finish_reason="max_tokens", usage=usage)


class CutModel(ScriptedModel):
    """The attempts suite's model stand-in, which can also answer with a
    whole ``LLMResponse`` (a cut-off answer) and can charge a price per call
    (``world.price``). It keeps the last message each call was sent."""

    async def stream(self, *, system, messages, tools, **kw):
        moves = self.world.moves.setdefault(self.provider, [])
        last = str(getattr(messages[-1], "content", "") if messages else "")
        price = getattr(self.world, "price", 0.0)
        if moves and tools and isinstance(moves[0], LLMResponse):
            resp = moves.pop(0)
            self.world.calls.append({"provider": self.provider, "model": self.model, "last": last,
                                     "tools": [t["name"] for t in tools or []]})
            if resp.text:
                yield {"type": "text", "text": resp.text}
            yield {"type": "final", "response": resp}
            return
        async for frame in super().stream(system=system, messages=messages, tools=tools, **kw):
            if frame.get("type") == "final":
                self.world.calls[-1]["last"] = last
                if price:
                    r = frame["response"]
                    frame = {**frame, "response": LLMResponse(
                        text=r.text, tool_calls=r.tool_calls, finish_reason=r.finish_reason,
                        usage={**(r.usage or {}), "cost_usd": price},
                    )}
            yield frame


def wire(app, monkeypatch, world: World) -> World:
    mgr = app.state.platform.providers
    for prov in ("claude-cli", "codex-cli", "ollama"):
        mgr.register(prov, lambda model=None, _p=prov: CutModel(_p, model, world))
    real = mgr.available
    monkeypatch.setattr(mgr, "available",
                        lambda name: True if name in ("claude-cli", "codex-cli", "ollama") else real(name))
    monkeypatch.setattr(BAR, "menu_rows", lambda d: [dict(r) for r in MENU])
    return world


def _line(app, task: dict) -> dict:
    line = N.last_reply_line(messages_of(app, task["thread_id"]))
    assert line is not None, task
    return line


# --------------------------------------------------------------------------- #
# CS1: a night task cut off at its length is FAILED, and still committed
# --------------------------------------------------------------------------- #


async def test_a_night_task_cut_at_its_length_fails_with_the_cut_words_and_its_commit_says_so(
    tmp_path, monkeypatch,
):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)
    wire(app, monkeypatch, World({"ollama": [
        write("w1", "notes.txt", "the first half\n"),
        cut("I wrote the first half of the notes and was about to"),
    ]}))
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes file")
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"], 120)
    assert _line(app, ended).get("truncated") is True, "the stand-in did not cut the answer"
    assert ended["state"] == N.FAILED, (ended["state"], ended["notes"])
    errors = [n["text"] for n in ended["notes"] if n["kind"] == "error"]
    assert errors == [RE.CUT_WORDS[RE.LENGTH]], ended["notes"]
    # The work is not lost: committed as for any other ending, and the commit
    # says how the task ended.
    assert ended["commit_sha"], ended["notes"]
    assert any(n["kind"] == "commit" and ended["commit_sha"][:12] in n["text"] for n in ended["notes"])
    message = git("log", "-1", "--format=%B", ended["commit_sha"], cwd=repo)
    assert message.rstrip().endswith("(failed)."), message


@pytest.mark.parametrize(
    ("line", "state", "note"),
    [
        ({"role": "assistant", "content": "Half", "route": {"cut_off": {"times": 3, "recovered": False}}},
         N.FAILED, ("error", RE.CUT_WORDS[RE.CUT_OFF])),
        ({"role": "assistant", "content": "All of it", "route": {"cut_off": {"times": 1, "recovered": True}}},
         N.DONE, None),
        # R13: a cut is read BEFORE a limit, at night and on the attempts card.
        ({"role": "assistant", "content": "Half", "truncated": True, "capMet": {"kind": "time", "limit": 30}},
         N.FAILED, ("error", RE.CUT_WORDS[RE.LENGTH])),
        ({"role": "assistant", "content": "Half", "truncated": True, "stepLimit": {"limit": 40}},
         N.FAILED, ("error", RE.CUT_WORDS[RE.LENGTH])),
    ],
    ids=["cut-off-not-recovered", "cut-off-recovered", "cut-and-time-limit", "cut-and-step-limit"],
)
def test_the_night_reads_a_cut_reply_through_the_one_reading(line, state, note):
    got, notes = N.outcome_of({"id": "nt1", "notes": []}, line)
    assert got == state, (got, notes)
    if note is not None:
        assert note in notes, notes


def test_the_one_reading():
    assert RE.cut_short({"truncated": True}) == RE.LENGTH
    assert RE.cut_short({"route": {"cut_off": {"recovered": False}}}) == RE.CUT_OFF
    assert RE.cut_short({"route": {"cut_off": {"recovered": True}}}) == ""
    assert RE.cut_short({"truncated": False, "route": {"provider": "x"}}) == ""
    for junk in (None, "truncated", [], {"route": "cut_off"}, {"route": {"cut_off": "yes"}}):
        assert RE.cut_short(junk) == ""


def test_the_next_turns_model_is_told_the_answer_was_cut_off():
    note = S.reply_end_note({"role": "assistant", "content": "Half the", "truncated": True})
    assert S.REPLY_LENGTH_CUT in note, note
    assert S.reply_end_note({"role": "assistant", "content": "All of it", "truncated": False}) == ""


# --------------------------------------------------------------------------- #
# CS2: an attempt cut off at its length is "failed"
# --------------------------------------------------------------------------- #


async def _day_attempts(app, repo: Path, sid_extra: dict | None = None):
    status, view = await asgi(app, "POST", "/build/sessions", {
        "folder": str(repo), "provider": LOCAL[0], "model": LOCAL[1], "approval_mode": "yolo",
        **(sid_extra or {}),
    })
    assert status == 201, view
    return view["id"]


TRY_BOTH = [{"provider": CODEX[0], "model": CODEX[1]}, {"provider": LOCAL[0], "model": LOCAL[1]}]


async def _attempts_card(app, sid: str, turn_id: str) -> dict:
    status, raw = await open_turn(app, sid, {"text": "write the notes file", "turn_id": turn_id,
                                             "try_on": TRY_BOTH}, timeout=120.0)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    done = frames[-1][1]
    return done


def _item(card: dict, provider: str) -> dict:
    return next(i for i in card["items"] if i["provider"] == provider)


async def test_an_attempt_cut_at_its_length_is_failed_and_the_card_says_so(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    wire(app, monkeypatch, World({
        "codex-cli": ["The notes file is written."],
        "ollama": [write("w1", "notes.txt", "the first half\n"),
                   cut("I wrote the first half of the notes and was about to")],
    }))
    sid = await _day_attempts(app, repo)
    done = await _attempts_card(app, sid, "cut-one")
    card = done[A.LINE_KEY]
    local = _item(card, LOCAL[0])
    assert local["state"] == "failed", local
    assert local["why"] == "failed" and "output limit" in local["words"], local
    assert local["words"] == "Failed: its answer was cut off at the model's output limit, so it did not finish"
    assert _item(card, CODEX[0])["state"] == "done"
    reading = A.card_reading(card)
    assert reading["finished"] == 1 and reading["state"] == A.READ_NEEDS_YOU, reading
    assert "Press Keep on the one you want" in done["reply"], done["reply"]
    # Its work is not lost: the files it changed can still be kept.
    assert local.get("files") == ["notes.txt"], local.get("files")
    await until(lambda: turn_log(app, "cut-one") is not None and turn_log(app, "cut-one").status != "running")
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "cut-one", "attempt": local["id"]})
    assert status == 200, kept


async def test_attempts_all_cut_off_finish_none_and_offer_no_keep(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    wire(app, monkeypatch, World({
        "codex-cli": [cut("I changed the first half")],
        "ollama": [cut("I wrote the first half")],
    }))
    sid = await _day_attempts(app, repo)
    done = await _attempts_card(app, sid, "cut-all")
    card = done[A.LINE_KEY]
    assert {i["state"] for i in card["items"]} == {"failed"}, card["items"]
    reading = A.card_reading(card)
    assert reading["finished"] == 0 and reading["state"] == A.READ_FAILED, reading
    assert "Press Keep" not in done["reply"], done["reply"]


def test_an_attempt_cut_and_at_its_limit_reads_the_cut_first():
    a = A.Attempt(id="try1", target=A.Target(label="M1", provider="p1", model="m"), pin=None, git={},
                  folder="", sid="s1", turn_id="u.try1")
    a.fold("done", json.dumps({"reply": "Half", "truncated": True, "cap": {"kind": "time", "limit": 30}}))
    a.settle()
    assert (a.state, a.why) == ("failed", "failed"), (a.state, a.words)
    assert "output limit" in a.words, a.words


# --------------------------------------------------------------------------- #
# CS3: an attempts turn is announced from its card; a Stop is never "finished"
# --------------------------------------------------------------------------- #


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
        (("failed", "failed"), "failed", "Your coding session could not finish"),
        # C-CS-3: one or more finished says what to do (was "needs_you").
        (("done", "failed"), "choose", "Your attempts are ready: choose the one to keep"),
    ],
    ids=["none-finished", "one-finished"],
)
async def test_an_attempts_turn_is_announced_from_its_card(coding, clock, states, outcome, title):
    app, sid = coding
    card = _card(*states)
    frame = {"reply": A.summary_words(card), A.LINE_KEY: card}
    [notice] = await _notices_after(app, sid, f"att-{outcome}", clock, ("done", frame))
    assert notice["outcome"] == outcome, notice
    assert build_finished_title(notice) == title


async def test_an_attempts_turn_the_user_stopped_sends_no_finished_notice_and_is_not_finished(
    tmp_path, monkeypatch, clock,
):
    repo = make_calc_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    began = asyncio.Event()

    async def hang(_world):
        began.set()
        await asyncio.Event().wait()

    wire(app, monkeypatch, World({"ollama": [hang]}))
    noted: list[str] = []
    monkeypatch.setattr(_library, "note_turn_finished", lambda platform, thread_id, turn_id: noted.append(turn_id))
    sid = await _day_attempts(app, repo)
    task = asyncio.create_task(open_turn(app, sid, {
        "text": "write the notes file", "turn_id": "att-stop", "try_on": [{"provider": LOCAL[0], "model": LOCAL[1]}],
    }, timeout=120.0))
    await asyncio.wait_for(began.wait(), 60)
    clock.offset += 120  # a long turn: the notice would be due
    status, _ = await asgi(app, "POST", "/chat/turns/att-stop/stop", {})
    assert status == 200
    status, raw = await task
    frames = frames_of(raw)
    assert frames[-1][0] == "done", frames[-3:]
    assert frames[-1][1].get("stopped") is True, frames[-1][1]
    await until(lambda: turn_log(app, "att-stop") is not None and turn_log(app, "att-stop").status != "running")
    platform = app.state.platform
    finished = [e for e in platform.event_bus.history if e.type == EventType.BUILD_TURN_FINISHED
                and (e.payload or {}).get("turn_id") == "att-stop"]
    assert finished == [], [e.payload for e in finished]
    assert "att-stop" not in noted, "a stopped attempts turn was noted as finished"


# --------------------------------------------------------------------------- #
# CS5: a stop at what was left of the month's pay-per-use limit says so
# --------------------------------------------------------------------------- #


def test_an_attempt_stopped_by_the_pay_left_says_whose_limit():
    def attempt(n: int, spent: float) -> A.Attempt:
        a = A.Attempt(id=f"try{n}", target=A.Target(label=f"M{n}", provider=f"p{n}", model="m"), pin=None,
                      git={}, folder="", sid=f"s{n}", turn_id=f"u.try{n}", cost="pay")
        a.usage["cost_usd"] = spent
        return a

    a1, a2 = attempt(1, 0.3), attempt(2, 0.0)
    plan = A.Plan(thread_id="t", turn_id="u", task="x", history=[], summary="", setup={}, project_id="",
                  start=None, attempts=[a1, a2], pay_room=0.5)
    met = A.attempt_cap(plan, a2).met(0.25)
    assert met == {"kind": "cost", "limit": 0.5, "of": RC.PAY_LEFT}, met
    sent = RC.receipt(met)
    assert sent == {"kind": "cost", "limit": 0.5, "of": RC.PAY_LEFT}, sent
    a2.fold("done", json.dumps({"reply": "Half done.", "cap": sent}))
    a2.settle()
    assert a2.state == "stopped", a2.words
    assert a2.words == "Stopped at what was left of this month's pay per use limit ($0.50)", a2.words
    assert "list-price" not in RC.instruction(sent) and "this session's" not in RC.instruction(sent)
    assert "what was left of this month's pay per use limit ($0.50)" in RC.instruction(sent)
    note = S.reply_end_note({"role": "assistant", "content": "Half done.", "capMet": sent})
    assert S.REPLY_PAY_LEFT in note and S.REPLY_COST_CAP not in note, note
    # The session's own cap keeps its words byte for byte.
    own = {"kind": "cost", "limit": 5.0}
    assert RC.receipt(own) == own
    assert RC.limit_words(own) == "$5.00 of list-price cost"
    # Nobody may SET whose limit it is: a PATCH carrying it is refused.
    assert RC.strict({"usd": 1.0, "of": RC.PAY_LEFT}) is None
    assert RC.clean({"usd": 1.0, "of": RC.PAY_LEFT}) == {"usd": 1.0}


async def test_a_night_task_stopped_by_the_pay_left_names_that_limit(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(MT, "cost_type", lambda platform, provider, model="": "pay")
    app.state.platform.config.team_paid_cap_usd = 0.4
    world = World({"ollama": [write(f"w{n}", f"part{n}.txt", f"{n}\n") for n in range(1, 8)]})
    world.price = 0.3  # each call costs $0.30: the $0.40 left is met after the second
    wire(app, monkeypatch, world)
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="write the notes in parts")
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"], 120)
    line = _line(app, ended)
    assert line.get("capMet") == {"kind": "cost", "limit": 0.4, "of": RC.PAY_LEFT}, line.get("capMet")
    last = world.calls[-1]["last"]
    assert "what was left of this month's pay per use limit ($0.40)" in last, last
    assert "this session's cost limit" not in last, last


# --------------------------------------------------------------------------- #
# CS4 (decision D4 A): a level or Plan set while attempts run reaches them
# --------------------------------------------------------------------------- #


def _rows(app, turn_id: str) -> list[dict]:
    return [d for e, d in turn_frames(app, turn_id) if e == A.EVENT]


async def test_a_stricter_level_set_while_an_attempt_runs_asks_from_its_next_step(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    seen: dict = {}
    sid_box: dict = {}

    async def tighten(_world):
        seen["patch"] = await asgi(app, "PATCH", f"/build/sessions/{sid_box['sid']}",
                                   {"approval_mode": "always_ask"})
        return write("w2", "two.txt", "2\n")

    wire(app, monkeypatch, World({"ollama": [write("w1", "one.txt", "1\n"), tighten, "Done."]}))
    sid = sid_box["sid"] = await _day_attempts(app, repo)
    task = asyncio.create_task(open_turn(app, sid, {
        "text": "write the notes file", "turn_id": "att-level", "try_on": [{"provider": LOCAL[0], "model": LOCAL[1]}],
    }, timeout=120.0))

    def asking():
        return next((d for d in _rows(app, "att-level") if isinstance(d.get("ask"), dict) and d["ask"].get("id")),
                    None) or (["ended"] if task.done() else None)

    row = await until(asking, 60.0)
    assert row is not None and row != ["ended"], "the stricter level never reached the running attempt"
    assert row["state"] == "waiting" and row["ask"]["tool"] == "write_file", row
    status, _ = await asgi(app, "POST", f"/chat/approvals/{row['ask']['id']}", {"decision": "deny"})
    assert status == 200
    status, raw = await task
    folder = Path(next(d["folder"] for d in _rows(app, "att-level") if d.get("folder")))
    assert (folder / "one.txt").exists(), "the step before the change ran at the old level"
    assert not (folder / "two.txt").exists(), "a write ran after the level was made stricter"
    status, view = seen["patch"]
    assert status == 200 and view["applies"] == "now", view


async def test_plan_switched_on_while_an_attempt_runs_stops_its_changes(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    seen: dict = {}
    sid_box: dict = {}

    async def plan_on(_world):
        seen["patch"] = await asgi(app, "PATCH", f"/build/sessions/{sid_box['sid']}", {"mode": "plan"})
        return write("w2", "two.txt", "2\n")

    wire(app, monkeypatch, World({"ollama": [write("w1", "one.txt", "1\n"), plan_on, "Here is my plan."]}))
    sid = sid_box["sid"] = await _day_attempts(app, repo)
    status, raw = await open_turn(app, sid, {
        "text": "write the notes file", "turn_id": "att-plan", "try_on": [{"provider": LOCAL[0], "model": LOCAL[1]}],
    }, timeout=120.0)
    assert status == 200, raw
    frames = frames_of(raw)
    card = frames[-1][1][A.LINE_KEY]
    [item] = card["items"]
    folder = Path(item["folder"])
    assert (folder / "one.txt").exists()
    assert not (folder / "two.txt").exists(), "a file was written after Plan was switched on"
    assert item["state"] == "stopped" and item["words"] == A.PLAN_NOW_WORDS, item
    assert A.card_reading(card)["finished"] == 0
    status, view = seen["patch"]
    assert status == 200 and view["applies"] == "now", view


# --------------------------------------------------------------------------- #
# the drift scan: one reading of a cut reply
# --------------------------------------------------------------------------- #

#: The files that read how a coding reply ended.
READERS = (
    "harness/night.py",
    "harness/attempts.py",
    "harness/sessions.py",
    "daemon/routes/build.py",
    "daemon/routes/build_attempts.py",
)
_READ_RX = re.compile(r"""(?:\.get\(\s*|\[\s*)["'](truncated|cut_off)["']""")
#: The one allowed copy: the reply watch copies the frame's mark into the line.
_ALLOWED = ('"truncated": bool(done.get("truncated")),',)


def test_no_reader_of_a_replys_ending_has_its_own_copy_of_the_cut_rule():
    found: list[str] = []
    allowed_seen = 0
    for rel in READERS:
        text = (SRC / rel).read_bytes().decode("utf-8").replace("\r\n", "\n")
        for n, line in enumerate(text.split("\n"), start=1):
            if not _READ_RX.search(line):
                continue
            if line.strip() in _ALLOWED:
                allowed_seen += 1
                continue
            found.append(f"{rel}:{n}: {line.strip()}")
    assert allowed_seen == 1, "the reply watch's one copy moved: update the scan"
    assert not found, "read a reply's ending through harness/reply_end.cut_short:\n" + "\n".join(found)
