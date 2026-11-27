"""19-b (build-out plan R4.37, idea 19): IT OFFERS TO RUN BY ITSELF, WITH A
REHEARSAL.

Through the real app factory, the real night review (``review_pass``), the
real Improvements route and the served offer routes; the draft's model and
the rehearsal's model are scripted; time is an injected clock. The plan's
calm rule: the schedule is made only when you press Accept, the rehearsal
runs only when you press Rehearse. Pinned, each with the way it would fail:

* DONE WHEN: three Monday chats leave, the next morning, the skill draft and
  BESIDE it "Run this by itself each Monday at 10am?" under "Tasks Iron
  Jarvis could run on a schedule", with no schedule and no rehearsal made;
* Accept takes two presses (``confirm``) and makes the schedule, switched
  on, on the newest chat's provider, model and account, with the owner's
  own words, reporting to that chat; it is made by nothing else;
* Rehearse runs 06-a's rehearsal of the schedule Accept would make, through
  the schedule's own door, changing nothing and making no schedule;
* Decline is final for that task;
* every add-time rule of ``POST /schedules`` holds at Accept and Rehearse
  (an account that is gone; a Local only chat that no longer is);
* chat lists the offer and accepts it through the same route.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlmodel import select

from iron_jarvis.core import config as _ij_config
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.improve import budget as B
from iron_jarvis.improve import night_review as NR
from iron_jarvis.learning import repeats as R
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.scheduling import offers as O
from iron_jarvis.scheduling.models import ScheduledTaskRecord
from iron_jarvis.skills.learning_models import SkillProposalRecord
from iron_jarvis.tools.rehearsal_store import RehearsalRecord
from tests.test_next_leaps_19a_chat_draft import IN_WINDOW, Router
from tests.test_next_leaps_19a_repeats import ALIKE, WORDS, _monday, seed_chat

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]

HEADING = "Tasks Iron Jarvis could run on a schedule"


def _scripted(steps: list[list[ToolCall]]):
    state = {"n": 0}

    async def fake_stream(*, system=None, messages=None, tools=None, **kw):
        i = state["n"]
        state["n"] += 1
        if i < len(steps):
            resp = LLMResponse(text="", tool_calls=list(steps[i]))
        else:
            resp = LLMResponse(text="Wrote the summary sheet.")
            yield {"type": "text", "text": "Wrote the summary sheet."}
        yield {"type": "final", "response": resp, "provider": "ollama", "model": "qwen3:8b"}

    return fake_stream


@pytest.fixture
def desk(tmp_path, monkeypatch):
    # The app's DEFAULT switches (the suite turns the feature lookup off).
    monkeypatch.setattr(_ij_config, "LOOKUP_ON_BY_DEFAULT", True)
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.providers.configure_local(ollama_base_url="http://127.0.0.1:11434")
    p.config.ollama_base_url = "http://127.0.0.1:11434"
    # The owner's one step: Yes to the night review's offer (D-17.1).
    p.config.night_review = True
    drafts = Router()
    p.router.complete = drafts.complete  # the draft's model; nothing else
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "bank-export.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    B.reset()
    NR.reset()
    yield SimpleNamespace(app=app, d=app.state.d, platform=p, drafts=drafts, engine=p.engine, folder=folder)
    B.reset()
    NR.reset()


def _three(desk, *, setups=(None, None, None), model=("ollama", "qwen3:8b")) -> list[str]:
    """Three chats on three Mondays at 10, newest first."""
    return [
        seed_chat(desk.engine, _monday(w), words=WORDS if w == 0 else ALIKE, provider=model[0], model=model[1],
                  setup=setups[w])
        for w in range(3)
    ]


def _night(desk, days: int = 0) -> dict:
    return asyncio.run(NR.review_pass(desk.d, now=IN_WINDOW + timedelta(days=days)))


async def _call(desk, method: str, path: str, body: Any = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=desk.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, json=body)


def call(desk, method: str, path: str, body: Any = None) -> httpx.Response:
    return asyncio.run(_call(desk, method, path, body))


def _items(desk, kind: str = "schedule_offer") -> list[dict]:
    res = call(desk, "GET", "/improvements")
    assert res.status_code == 200
    return [i for i in res.json()["items"] if i["kind"] == kind]


def _schedules(desk) -> list[ScheduledTaskRecord]:
    with session_scope(desk.engine) as db:
        return list(db.exec(select(ScheduledTaskRecord)))


def _rehearsals(desk) -> list[RehearsalRecord]:
    with session_scope(desk.engine) as db:
        return list(db.exec(select(RehearsalRecord)))


def _offers(desk) -> list[O.ScheduleOfferRecord]:
    with session_scope(desk.engine) as db:
        return list(db.exec(select(O.ScheduleOfferRecord)))


# --------------------------------------------------------------------------- #
# the offer
# --------------------------------------------------------------------------- #
def test_three_monday_chats_leave_an_offer_beside_the_draft_and_nothing_else(desk):
    tids = _three(desk)
    got = _night(desk)
    assert got["made"] == 2  # the draft and the offer
    assert len(desk.drafts.calls) == 1  # the draft's one request; the offer asks no model
    assert _schedules(desk) == [] and _rehearsals(desk) == []
    res = call(desk, "GET", "/improvements")
    groups = {g["kind"]: g["label"] for g in res.json()["groups"]}
    assert groups["schedule_offer"] == HEADING and "skill" in groups
    (item,) = _items(desk)
    assert item["title"] == "Run this by itself each Monday at 10am?"
    assert item["about"] == "bank-export-summary"
    assert item["why"] == "The 3 times you asked for this in chat were each on a Monday."
    days = R.days_words(sorted(R.day_of(_monday(w).timestamp()).isoformat() for w in range(3)))
    assert item["evidence"] == [
        f"You asked for this 3 times, on {days}.",
        "Each time Iron Jarvis used: excel read, excel edit.",
        "The skill draft “bank-export-summary” is waiting under Skill drafts. A schedule made before you "
        "approve it runs without it.",
        "Not rehearsed yet.",
    ]
    assert item["detail"]["after"] == WORDS
    assert item["extra"]["night"]["why"] == NR.OFFER_WHY
    assert item["accept"]["confirm"] is True  # two presses: it starts work by itself
    assert item["accept"]["path"].endswith("/accept") and item["decline"]["confirm"] is False
    assert "each Monday at 10am" in item["accept"]["note"]
    assert "your chat on " in item["accept"]["note"] and "reports there" in item["accept"]["note"]
    assert item["extra"]["rehearse"]["path"] == f"/schedules/offers/{item['ref']}/rehearse"
    (offer,) = _offers(desk)
    assert offer.chat_thread_id == tids[0] and offer.words == WORDS


def test_a_second_night_makes_no_second_offer(desk):
    _three(desk)
    _night(desk)
    _night(desk, days=1)
    assert len(_offers(desk)) == 1


def test_no_rhythm_no_offer(desk):
    for w, day in enumerate((_monday(0), _monday(1) + timedelta(days=2), _monday(2))):
        seed_chat(desk.engine, day, words=WORDS if w == 0 else ALIKE, provider="ollama", model="qwen3:8b")
    _night(desk)
    assert len(_items(desk, "skill")) == 1 and _items(desk) == []


# --------------------------------------------------------------------------- #
# Accept
# --------------------------------------------------------------------------- #
def test_accept_makes_the_schedule_switched_on_with_the_owner_s_words(desk):
    tids = _three(desk)
    _night(desk)
    (item,) = _items(desk)
    assert _schedules(desk) == []  # nothing until Accept
    res = call(desk, "POST", item["accept"]["path"])
    assert res.status_code == 200, res.text
    (sched,) = _schedules(desk)
    assert sched.enabled is True and sched.kind == "task" and sched.cron == "0 10 * * mon"
    assert sched.name == "bank-export-summary" and sched.chat_thread_id == tids[0]
    payload = json.loads(sched.payload_json)
    assert payload["task"] == WORDS  # the owner's line, byte for byte
    assert (payload["provider"], payload["model"]) == ("ollama", "qwen3:8b")
    assert "skills" not in payload  # the draft is not a skill yet
    nxt = sched.next_run
    # Next Monday at 10 on this PC's clock (the scheduler's zone): the day is
    # named in the cron, so the scheduler never reads it as Tuesday.
    assert nxt is not None and (nxt.weekday(), nxt.hour, nxt.minute) == (0, 10, 0), nxt
    assert res.json()["note"] == (
        "Made the schedule “bank-export-summary”: it runs by itself each Monday at 10am. "
        "Schedules can pause or remove it."
    )
    assert _items(desk) == []  # answered
    again = call(desk, "POST", item["accept"]["path"])
    assert again.status_code == 409 and len(_schedules(desk)) == 1


def test_an_approved_draft_rides_the_schedule_as_its_skill(desk):
    _three(desk)
    _night(desk)
    (prop,) = [i for i in _items(desk, "skill")]
    assert call(desk, "POST", prop["accept"]["path"], prop["accept"]["body"]).status_code == 200
    (item,) = _items(desk)
    assert "It uses the skill “bank-export-summary”." in item["evidence"]
    res = call(desk, "POST", item["accept"]["path"])
    assert res.status_code == 200, res.text
    (sched,) = _schedules(desk)
    assert json.loads(sched.payload_json)["skills"] == ["bank-export-summary"]
    assert json.loads(sched.payload_json)["task"] == WORDS  # the skill's name never joins the words


def test_accept_refuses_an_account_that_is_gone_and_makes_nothing(desk):
    _three(desk)
    _night(desk)
    (offer,) = _offers(desk)
    with session_scope(desk.engine) as db:
        row = db.get(O.ScheduleOfferRecord, offer.id)
        row.account = "acct_gone"
        db.add(row)
        db.commit()
    res = call(desk, "POST", f"/schedules/offers/{offer.id}/accept")
    assert res.status_code == 409 and res.json()["detail"]
    assert _schedules(desk) == []
    assert O.get(desk.engine, offer.id).status == O.WAITING  # it still waits
    reh = call(desk, "POST", f"/schedules/offers/{offer.id}/rehearse")
    assert reh.status_code == 409 and _rehearsals(desk) == []


def test_local_only_work_makes_a_schedule_that_runs_local_only_on_every_fire(desk):
    # A model on this PC answered the chats (ollama): their words stay home
    # (19-a's rule), so the schedule's payload says Local only, which the
    # fire reads whatever becomes of the chat.
    _three(desk)
    _night(desk)
    (offer,) = _offers(desk)
    assert offer.local_only is True
    res = call(desk, "POST", f"/schedules/offers/{offer.id}/accept")
    assert res.status_code == 200, res.text
    (sched,) = _schedules(desk)
    assert json.loads(sched.payload_json)["local_only"] is True


def test_cloud_chats_make_a_schedule_with_no_local_only_mark(desk):
    _three(desk, model=("claude-cli", "claude-sonnet-4-6"))
    _night(desk)
    (offer,) = _offers(desk)
    assert offer.local_only is False
    assert call(desk, "POST", f"/schedules/offers/{offer.id}/accept").status_code == 200
    (sched,) = _schedules(desk)
    payload = json.loads(sched.payload_json)
    assert "local_only" not in payload
    assert (payload["provider"], payload["model"]) == ("claude-cli", "claude-sonnet-4-6")


# --------------------------------------------------------------------------- #
# the record: words from the chats, a deleted chat, no model
# --------------------------------------------------------------------------- #
def test_the_offer_s_words_are_the_newest_chat_s_line_and_no_draft_holds_them(desk):
    _three(desk)
    _night(desk)
    (offer,) = _offers(desk)
    assert offer.words == WORDS  # the newest chat's own line, byte for byte
    assert "bank-export-summary" not in offer.words
    (prop,) = [i for i in _items(desk, "skill")]
    assert WORDS not in json.dumps(prop) and WORDS not in offer.evidence_json


def test_a_deleted_chat_takes_its_offer_and_its_words_with_it(desk):
    tids = _three(desk)
    _night(desk)
    assert len(_offers(desk)) == 1
    res = call(desk, "DELETE", f"/chat/threads/{tids[1]}")
    assert res.status_code in (200, 204), res.text
    assert _offers(desk) == [] and _items(desk) == []
    # and it is not made again from the two chats left
    _night(desk, days=1)
    assert _offers(desk) == []


def test_no_offer_without_the_model_the_chats_used(desk):
    for w in range(3):
        seed_chat(desk.engine, _monday(w), words=WORDS if w == 0 else ALIKE, provider="ollama", model="qwen3:8b")
    with session_scope(desk.engine) as db:
        from iron_jarvis.core.models import AgentRun

        for run in db.exec(select(AgentRun)):
            run.model = ""
            db.add(run)
        db.commit()
    prop = SimpleNamespace(id="p1", skill_name="x", evidence_json=json.dumps({"units": [
        {"thread": t.id, "start": _monday(w).timestamp()} for w, t in enumerate(_chats(desk))]}))
    got, why = O.offer_for(desk.platform, prop)
    assert got is None and why == "the model the chats used is not on record"


def _chats(desk) -> list[ChatThreadRecord]:
    with session_scope(desk.engine) as db:
        return sorted(db.exec(select(ChatThreadRecord)), key=lambda t: t.created_at, reverse=True)


# --------------------------------------------------------------------------- #
# Decline
# --------------------------------------------------------------------------- #
def test_decline_is_final_for_that_task(desk):
    _three(desk)
    _night(desk)
    (item,) = _items(desk)
    res = call(desk, "POST", item["decline"]["path"])
    assert res.status_code == 200 and res.json()["note"] == O.DECLINED_NOTE
    assert _items(desk) == [] and _schedules(desk) == []
    assert call(desk, "POST", item["accept"]["path"]).status_code == 409
    # The same task's next skill draft (say the first was rejected and the
    # owner kept asking) gets no offer either.
    with session_scope(desk.engine) as db:
        prop = db.exec(select(SkillProposalRecord)).one()
        db.add(SkillProposalRecord(skill_name="bank-summary-2", body_md=prop.body_md, source="chat",
                                   evidence_json=prop.evidence_json, status="pending"))
        db.commit()
    assert O.make_offers(desk.platform) == []


# --------------------------------------------------------------------------- #
# Rehearse (06-a's rehearsal, when the owner presses it)
# --------------------------------------------------------------------------- #
async def _settled(desk, rid: str, seconds: float = 60.0) -> dict:
    from iron_jarvis.tools import rehearsal_runs as RR

    deadline = time.monotonic() + seconds  # polls a condition, never asserts a duration
    while True:
        view = await asyncio.to_thread(RR.report, desk.platform, rid)
        if view["status"] != "running" or time.monotonic() > deadline:
            return view
        await asyncio.sleep(0.05)


def test_rehearse_runs_the_schedule_accept_would_make_and_changes_nothing(desk):
    _three(desk)
    _night(desk)
    (item,) = _items(desk)
    out = desk.folder / "summary.xlsx"
    desk.platform.router.stream = _scripted([[
        ToolCall(id="c1", name="write_file", arguments={"path": str(out), "content": "totals"}),
    ]])

    async def go() -> tuple[httpx.Response, dict, httpx.Response]:
        first = await _call(desk, "POST", item["extra"]["rehearse"]["path"])
        assert first.status_code == 200, first.text
        view = await _settled(desk, first.json()["id"])
        keep = await _call(desk, "POST", f"/rehearsals/{view['id']}/keep")
        return first, view, keep

    first, view, keep = asyncio.run(go())
    assert view["status"] == "ready", view
    assert view["subject"]["kind"] == "offer"
    from iron_jarvis.core.models import Session as SessionRow

    with session_scope(desk.engine) as db:
        runs = [r for r in db.exec(select(SessionRow)) if (r.origin or "").startswith("schedule:")]
    assert len(runs) == 1 and json.loads(runs[0].options_json or "{}").get("local_only") is True
    assert any(w["kind"] == "write" for w in view["would"]), view["would"]
    assert view["sentence"].endswith("Nothing was changed.")
    assert not out.exists()  # nothing was written
    assert _schedules(desk) == []  # Rehearse makes no schedule
    assert keep.status_code == 409  # only Accept makes it
    (again,) = _items(desk)
    assert again["extra"]["rehearsal_id"] == view["id"]
    assert "Not rehearsed yet." not in again["evidence"]


# --------------------------------------------------------------------------- #
# chat
# --------------------------------------------------------------------------- #
def test_chat_lists_the_offer_and_accepts_it_through_its_route(desk):
    from iron_jarvis.inbox.tools import SuggestionAnswerTool, SuggestionListTool

    _three(desk)
    _night(desk)
    ctx = SimpleNamespace()
    listed = asyncio.run(SuggestionListTool(desk.platform).execute({}, ctx))
    line = next(ln for ln in listed.output.splitlines() if "schedule_offer:" in ln)
    assert "Run this by itself each Monday at 10am?" in line
    assert "rehearse first = the rehearse tool with kind offer" in line
    (item,) = _items(desk)
    done = asyncio.run(SuggestionAnswerTool(desk.platform).execute({"id": item["id"], "answer": "accept"}, ctx))
    assert done.ok, done.error
    assert len(_schedules(desk)) == 1
