"""The intelligence plan, release 11: A JOB YOU REPEAT CAN RUN WITH NO MODEL
(on top of next leaps 19-b), through the real app factory, the real night
review, the real Improvements route and the served offer, rehearsal,
workflow and schedule routes, with the app's DEFAULT switches. The draft's
model is scripted; nothing else may ask a model (:class:`NoModel` fails the
test if anything does); time is an injected clock, never a sleep.

Pinned, each with the way it would fail:

* THE DO-NOTHING CHECK: three Mondays of the same job (read this week's bank
  export, convert it to a spreadsheet) -> the next morning ONE offer, "You
  do this every Monday. Run it by itself?", "It runs the same way each
  time, uses no model, and works offline.", "Which file? Each time: the
  newest .csv file in “exports” in its folder." -> Rehearse lists what it
  would do and changes nothing -> ONE press of Accept -> next Monday's fire
  (the scheduler's own job, nobody in the app) converts the NEW export with
  ZERO model calls and passes its own step check;
* DONE WHEN: the three times give one workflow with one input; no value of
  any one time is in the saved steps or the schedule;
* Accept waits for the rehearsal (refused in words before it, ``blocked`` on
  the item), and a rehearsal that found a step that would ask makes nothing;
* steps that were not the same: 19-b's offer, as before (one offer per job);
* Decline is final and keeps none of the steps; Accept is final too: no
  second offer for that job;
* the schedule's inputs rule is checked at every door that saves one;
* chat reaches it: "Do this by itself from now on." arms the list, the
  rehearsal and the answer.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlmodel import select

from iron_jarvis.core import config as _ij_config
from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.improve import budget as B
from iron_jarvis.improve import night_review as NR
from iron_jarvis.scheduling import offers as O
from iron_jarvis.scheduling.models import ScheduledTaskRecord
from iron_jarvis.tools import rehearsal_runs as RR
from iron_jarvis.workflows.models import WorkflowRecord, WorkflowRunRecord, real_runs
from tests.test_intel_11_fixed_steps import bank_files, job_calls, seed_job, three_jobs
from tests.test_next_leaps_19a_chat_draft import IN_WINDOW, Router
from tests.test_next_leaps_19a_repeats import ALIKE, WORDS, _monday

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(180)]


class NoModel:
    """A model double that FAILS the test if anything asks it anything."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __getattr__(self, name: str) -> Any:
        self.asked.append(name)
        raise AssertionError(f"a model was asked ({name}) by work that uses no model")


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
    p.router.complete = drafts.complete  # the skill draft's model; nothing else
    folder = tmp_path / "work"
    folder.mkdir()
    B.reset()
    NR.reset()
    yield SimpleNamespace(app=app, d=app.state.d, platform=p, drafts=drafts, engine=p.engine, folder=folder)
    B.reset()
    NR.reset()


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


def _workflows(desk) -> list[WorkflowRecord]:
    with session_scope(desk.engine) as db:
        return list(db.exec(select(WorkflowRecord)))


def _offers(desk) -> list[O.ScheduleOfferRecord]:
    with session_scope(desk.engine) as db:
        return list(db.exec(select(O.ScheduleOfferRecord)))


def _real_runs(desk) -> list[WorkflowRunRecord]:
    with session_scope(desk.engine) as db:
        return list(db.exec(select(WorkflowRunRecord).where(real_runs())))


async def _settled(desk, rid: str) -> dict:
    driver = RR._DRIVERS.get(rid)
    if driver is not None:
        await asyncio.wait_for(asyncio.shield(driver), timeout=60)
    return await asyncio.to_thread(RR.report, desk.platform, rid)


def rehearse(desk, item: dict) -> dict:
    """Press Rehearse and read its report once it has finished (the same
    loop: the rehearsal runs in the background)."""
    async def go() -> dict:
        first = await _call(desk, "POST", item["extra"]["rehearse"]["path"])
        assert first.status_code == 200, first.text
        return await _settled(desk, first.json()["id"])

    return asyncio.run(go())


def _offer_made(desk) -> dict:
    bank_files(desk.folder)
    three_jobs(desk.engine, desk.folder)
    _night(desk)
    (item,) = _items(desk)
    return item


# --------------------------------------------------------------------------- #
# the do-nothing check
# --------------------------------------------------------------------------- #
def test_three_mondays_the_offer_the_rehearsal_one_yes_and_next_monday_it_is_done(desk):
    used = bank_files(desk.folder)
    three_jobs(desk.engine, desk.folder)
    got = _night(desk)
    assert got["made"] == 2  # the skill draft and the offer
    assert len(desk.drafts.calls) == 1  # the draft's one request; the offer asks no model
    assert _schedules(desk) == [] and _workflows(desk) == []  # nothing made unasked
    (item,) = _items(desk)
    assert item["title"] == "You do this every Monday. Run it by itself?"
    assert item["why"] == "It runs the same way each time, uses no model, and works offline."
    assert "Which file? Each time: the newest .csv file in “exports” in its folder." in item["evidence"]
    assert "Not rehearsed yet." in item["evidence"]
    assert item["detail"]["after"] == "1. read file: the newest file\n2. convert document: the newest file, summary.xlsx"
    assert item["accept"]["confirm"] is False  # ONE yes ...
    assert item["accept"]["blocked"] == O.NOT_REHEARSED_STEPS  # ... after the rehearsal
    assert item["extra"]["after_rehearsal"] is True
    (offer,) = _offers(desk)
    assert offer.kind == O.KIND_STEPS and offer.offline is True

    # Accept before the rehearsal: refused in words, nothing made.
    early = call(desk, "POST", item["accept"]["path"])
    assert early.status_code == 409 and early.json()["detail"] == O.NOT_REHEARSED_STEPS
    assert _schedules(desk) == [] and _workflows(desk) == []

    # The rehearsal: what it would do, and nothing changed (no model either).
    desk.platform.router = NoModel()
    before = sorted(os.listdir(desk.folder))
    view = rehearse(desk, item)
    assert view["status"] == "ready", view
    assert view["sentence"] == "This would write 1 file. Nothing was changed."
    assert view["would"][0]["target"] == "summary.xlsx" and view["reads"] == 1
    assert sorted(os.listdir(desk.folder)) == before and not (desk.folder / "summary.xlsx").exists()
    assert _real_runs(desk) == []  # a rehearsal is no run of anything
    (item,) = _items(desk)
    assert item["accept"]["blocked"] == ""  # now one press does it

    # One yes.
    res = call(desk, "POST", item["accept"]["path"])
    assert res.status_code == 200, res.text
    (wf,) = _workflows(desk)
    (sched,) = _schedules(desk)
    assert sched.enabled is True and sched.kind == "workflow" and sched.cron == "0 10 * * mon"
    payload = json.loads(sched.payload_json)
    assert payload == {"workflow": wf.name, "inputs": {"file": {"newest": {"in": "exports", "kind": "csv"}}}}
    assert wf.folder == str(desk.folder) and wf.description == O.STEPS_DESCRIPTION
    assert res.json()["note"] == O.steps_note(wf.name, sched.name, "each Monday at 10am")
    # Done when: one workflow, one input, no value of any one time kept.
    steps = json.loads(wf.steps_json)
    assert [s["tool"] for s in steps] == ["read_file", "convert_document"]
    assert all(s["kind"] == "tool" for s in steps)  # no agent step: no model
    kept = wf.steps_json + sched.payload_json
    for path in used:
        assert os.path.basename(path) not in kept
    assert _items(desk) == []  # answered

    # Next Monday: a new export arrives; the scheduler's own job fires it
    # (nobody opens the app), with no model anywhere.
    new = desk.folder / "exports" / "bank-week-next.csv"
    new.write_text("category,amount\nrent,140\nfood,33\n", encoding="utf-8")
    desk.platform.scheduler._fire(sched.name)
    (run,) = _real_runs(desk)
    assert run.status == "completed", run.outputs_json
    outputs = json.loads(run.outputs_json)
    assert outputs["file"]["summary"] == "exports/bank-week-next.csv"
    assert outputs["Step 2"]["status"] == "completed"  # its own check (the file it writes) held
    assert desk.platform.router.asked == []  # ZERO model calls
    from openpyxl import load_workbook

    cells = [[c.value for c in row] for row in load_workbook(desk.folder / "summary.xlsx").active.iter_rows()]
    assert ["rent", 140] in cells or ["rent", "140"] in cells  # the NEW week's export


def test_the_step_check_is_judged_on_the_real_run(desk):
    """Mutation guard for "passes its own step checks": a check that names a
    file the step does not write fails the run."""
    item = _offer_made(desk)
    desk.platform.router = NoModel()
    assert rehearse(desk, item)["status"] == "ready"
    assert call(desk, "POST", item["accept"]["path"]).status_code == 200
    (wf,) = _workflows(desk)
    steps = json.loads(wf.steps_json)
    steps[1]["expect"] = {"files": ["not-written.xlsx"]}
    with session_scope(desk.engine) as db:
        row = db.get(WorkflowRecord, wf.id)
        row.steps_json = json.dumps(steps)
        db.add(row)
        db.commit()
    (sched,) = _schedules(desk)
    desk.platform.scheduler._fire(sched.name)
    (run,) = _real_runs(desk)
    assert run.status == "failed"


# --------------------------------------------------------------------------- #
# the yes waits for a clean rehearsal
# --------------------------------------------------------------------------- #
def test_a_rehearsal_that_found_a_step_that_would_ask_makes_nothing(desk):
    item = _offer_made(desk)
    desk.platform.permissions._base["convert_document"] = "ask"  # the owner changed it since
    desk.platform.router = NoModel()
    view = rehearse(desk, item)
    assert view["refused"] or any(w["would"] != "run" for w in view["would"]) or view["status"] != "ready"
    res = call(desk, "POST", item["accept"]["path"])
    assert res.status_code == 409
    assert _schedules(desk) == [] and _workflows(desk) == []
    assert O.get(desk.engine, item["ref"]).status == O.WAITING


def test_no_file_to_rehearse_on_is_said_and_nothing_runs(desk):
    item = _offer_made(desk)
    for f in (desk.folder / "exports").iterdir():
        f.unlink()
    view = rehearse(desk, item)
    assert view["status"] == "failed" and view["error"] == "No .csv file is in “exports”, so it did not run."
    assert call(desk, "POST", item["accept"]["path"]).status_code == 409


# --------------------------------------------------------------------------- #
# one offer per job
# --------------------------------------------------------------------------- #
def test_steps_that_were_not_the_same_get_19b_s_offer_as_before(desk):
    bank_files(desk.folder)

    def other_order(folder, w):
        calls = job_calls(folder, w)
        return calls[::-1] if w == 1 else calls

    three_jobs(desk.engine, desk.folder, make=other_order)
    _night(desk)
    (item,) = _items(desk)  # one offer, 19-b's
    assert item["title"] == "Run this by itself each Monday at 10am?"
    (offer,) = _offers(desk)
    assert offer.kind == O.KIND_TASK and offer.steps_json == ""


def test_decline_is_final_and_keeps_none_of_the_steps(desk):
    item = _offer_made(desk)
    res = call(desk, "POST", item["decline"]["path"])
    assert res.status_code == 200 and res.json()["note"] == O.DECLINED_NOTE
    (offer,) = _offers(desk)
    assert offer.status == O.DECLINED and offer.steps_json == "" and offer.inputs_json == "" and offer.words == ""
    # Three more Mondays of the same job: never asked again.
    for w in range(3):
        seed_job(desk.engine, _monday(w) + timedelta(hours=1), desk.folder, job_calls(desk.folder, w),
                 words=ALIKE)
    _night(desk, days=1)
    assert _items(desk) == [] and len(_offers(desk)) == 1


def test_a_job_already_said_yes_to_is_not_offered_again(desk):
    item = _offer_made(desk)
    desk.platform.router = NoModel()
    assert rehearse(desk, item)["status"] == "ready"
    assert call(desk, "POST", item["accept"]["path"]).status_code == 200
    prop = SimpleNamespace(id="p-again", skill_name="again", evidence_json=_offers(desk)[0].evidence_json)
    got, why = O.offer_for(desk.platform, prop)
    assert got is None and why == "you already said yes to running this by itself"


# --------------------------------------------------------------------------- #
# every door that saves a schedule checks the inputs rule
# --------------------------------------------------------------------------- #
def test_the_schedules_page_refuses_an_inputs_rule_that_could_not_be_filled(desk):
    res = call(desk, "POST", "/schedules", {
        "name": "bad-inputs", "cron": "0 9 * * mon", "kind": "workflow",
        "payload": {"workflow": "x", "inputs": {"file": {"newest": {"in": "../up", "kind": "csv"}}}},
    })
    assert res.status_code == 422 and "outside" in res.json()["detail"]


# --------------------------------------------------------------------------- #
# chat
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("words", ["Do this by itself from now on.", "can you run it on your own from now on",
                                   "Handle that by yourself every week"])
def test_chat_reaches_it(words):
    from iron_jarvis.inbox.tools import suggestion_tools_for

    assert {"suggestion_list", "rehearse", "suggestion_answer"} <= set(suggestion_tools_for(words))


@pytest.mark.parametrize("words", ["I did this by hand", "run the report"])
def test_other_words_do_not(words):
    from iron_jarvis.inbox.tools import suggestion_tools_for

    assert "rehearse" not in suggestion_tools_for(words)


def test_a_folder_not_on_this_pc_never_says_works_offline(desk, monkeypatch):
    from iron_jarvis.scheduling import fixed_steps as F

    monkeypatch.setattr(F, "folder_is_local", lambda folder: False)  # a share or a mapped network drive
    item = _offer_made(desk)
    assert item["why"] == "It runs the same way each time, uses no model."
    assert _offers(desk)[0].offline is False
