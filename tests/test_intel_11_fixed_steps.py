"""The intelligence plan, release 11: A JOB YOU REPEAT CAN RUN WITH NO MODEL.
The rules of ``scheduling/fixed_steps``: when are three times "the same
steps", what becomes the input, and what may never be saved.

Through the real app's registry, permissions and client list (``create_app``
on a temp home, the app's default switches). Pinned, each with the way it
would fail:

* DONE WHEN (part): three recorded times that differ in ONE value (the file
  read, named two ways: absolute and relative to the chat's folder) give
  ONE set of steps with ONE input, ``file``, and no file name of any time is
  in the steps;
* the same steps: the same tools, in the same order, with the same argument
  names; a step more, another order, an undone time: not the same;
* only tools that never ask a model and never reach the network; a file read
  where a scan could reach a model must be plain text;
* what changed must be ONE file each time, the newest of its kind in one
  folder when it was asked for (read from the disk);
* the leak rule: a client's job is never fixed steps; a secret-shaped value,
  a contact detail or a client's name in any case in what is kept refuses
  them; a path outside the chats' folder refuses them;
* a step that would ask first (its permission is Ask) refuses them;
* the input is filled by code: the newest file of its kind, and said in
  words when there is none; the schedule's inputs rule is checked when it is
  saved;
* "works offline" only for a folder on this PC.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType, ChatThreadRecord, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import fixed_steps as F
from tests.test_next_leaps_19a_repeats import ALIKE, WORDS, _monday, _utc

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]


# --------------------------------------------------------------------------- #
# the job: read this week's bank export, convert it to a spreadsheet
# --------------------------------------------------------------------------- #
def bank_files(folder, weeks: int = 3) -> list[str]:
    """``exports/bank-<w>.csv`` for each week, each written an hour before
    that Monday's request (so each was the newest when it was asked for)."""
    out = []
    (folder / "exports").mkdir(exist_ok=True)
    for w in range(weeks):
        f = folder / "exports" / f"bank-week-{w}.csv"
        f.write_text(f"category,amount\nrent,{100 + w}\nfood,{20 + w}\n", encoding="utf-8")
        at = (_monday(w) - timedelta(hours=1)).timestamp()
        os.utime(f, (at, at))
        out.append(str(f))
    return out


def job_calls(folder, w: int) -> list[tuple[str, dict]]:
    """One week's calls: the file read by its full path, then converted by
    its path inside the folder (one value, named two ways)."""
    return [
        ("read_file", {"path": str(folder / "exports" / f"bank-week-{w}.csv")}),
        ("convert_document", {"source": f"exports/bank-week-{w}.csv", "target": "summary.xlsx"}),
    ]


def rows(calls: list[tuple[str, dict]], **extra) -> list[SimpleNamespace]:
    return [SimpleNamespace(tool=t, args_json=json.dumps(a), ok=True, rehearsed=False, undo_of=None,
                            undone_at=None, **extra) for t, a in calls]


def times(folder, make=job_calls, weeks: int = 3) -> list[tuple[float, list]]:
    return [(_monday(w).timestamp(), rows(make(folder, w))) for w in range(weeks)]


def seed_job(engine, at: datetime, folder, calls: list[tuple[str, dict]], *, words: str = WORDS,
             provider: str = "ollama", model: str = "qwen3:8b", setup: "dict | None" = None) -> str:
    """One saved chat working in ``folder``, with one finished task at
    ``at``: the owner's request, the reply, the turn's run row and its tool
    rows in the ledger with their own arguments."""
    messages = [{"role": "user", "content": words, "at": at.isoformat()},
                {"role": "assistant", "content": "Done: the summary sheet is written.",
                 "at": (at + timedelta(seconds=60)).isoformat()}]
    with session_scope(engine) as db:
        t = ChatThreadRecord(title="bank", messages_json=json.dumps(messages), owner="user",
                             setup_json=json.dumps({"workspace_dir": str(folder), **(setup or {})}))
        t.created_at = _utc(at)
        t.updated_at = _utc(at + timedelta(seconds=61))
        db.add(t)
        run = AgentRun(session_id="chat", agent_type=AgentType.BUILDER, provider=provider, model=model,
                       thread_id=t.id)
        run.created_at = _utc(at + timedelta(seconds=1))
        run.finished_at = _utc(at + timedelta(seconds=55))
        db.add(run)
        for k, (tool, args) in enumerate(calls):
            row = ToolInvocation(session_id="chat", agent_run_id="chat", tool=tool, ok=True, output="ok",
                                 args_json=json.dumps(args))
            row.created_at = _utc(at + timedelta(seconds=10 + k))
            db.add(row)
        db.commit()
        return t.id


def three_jobs(engine, folder, make=job_calls, **kw) -> list[str]:
    """Three chats on three Mondays at 10, newest first."""
    return [seed_job(engine, _monday(w), folder, make(folder, w), words=WORDS if w == 0 else ALIKE, **kw)
            for w in range(3)]


@pytest.fixture
def app_home(tmp_path):
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "work"
    folder.mkdir()
    return SimpleNamespace(platform=app.state.platform, engine=app.state.platform.engine, folder=folder)


def _plan(desk, make=job_calls, owners=("", "", "")):
    return F.plan(desk.platform, times(desk.folder, make), str(desk.folder), owners=owners)


# --------------------------------------------------------------------------- #
# done when: one value differs -> one workflow with one input
# --------------------------------------------------------------------------- #
def test_three_times_that_differ_in_one_value_give_one_set_of_steps_with_one_input(app_home):
    used = bank_files(app_home.folder)
    plan = _plan(app_home)
    assert plan.inputs == {"file": {"newest": {"in": "exports", "kind": "csv"}}}
    assert plan.steps == [
        {"name": "Step 1", "kind": "tool", "tool": "read_file", "args": {"path": "{{file}}"}, "on_failure": "halt"},
        {"name": "Step 2", "kind": "tool", "tool": "convert_document",
         "args": {"source": "{{file}}", "target": "summary.xlsx"}, "on_failure": "halt",
         "expect": {"files": ["summary.xlsx"]}},
    ]
    assert plan.folder == str(app_home.folder)
    kept = json.dumps(plan.steps) + json.dumps(plan.inputs)
    for path in used:  # no value of any one time is kept
        assert os.path.basename(path) not in kept
    assert str(app_home.folder) not in kept  # paths are kept inside the folder only
    assert F.input_words(plan.inputs) == "Which file? Each time: the newest .csv file in “exports” in its folder."


def test_the_same_steps_with_nothing_changed_have_no_input(app_home):
    bank_files(app_home.folder)

    def same(folder, w):
        return job_calls(folder, 0)

    plan = F.plan(app_home.platform, [(_monday(w).timestamp(), rows(same(app_home.folder, w))) for w in range(3)],
                  str(app_home.folder))
    assert plan.inputs == {} and plan.steps[0]["args"] == {"path": "exports/bank-week-0.csv"}


# --------------------------------------------------------------------------- #
# the same steps
# --------------------------------------------------------------------------- #
def test_steps_in_another_order_are_not_the_same(app_home):
    bank_files(app_home.folder)

    def swapped(folder, w):
        calls = job_calls(folder, w)
        return calls[::-1] if w == 1 else calls

    with pytest.raises(F.NotFixed, match="not the same"):
        _plan(app_home, swapped)


def test_a_step_more_is_not_the_same(app_home):
    bank_files(app_home.folder)

    def more(folder, w):
        calls = job_calls(folder, w)
        return calls + [("calculate", {"expression": "1+1"})] if w == 2 else calls

    with pytest.raises(F.NotFixed, match="not the same"):
        _plan(app_home, more)


def test_a_call_that_failed_is_not_a_step(app_home):
    bank_files(app_home.folder)
    got = times(app_home.folder)
    got[1][1].insert(0, SimpleNamespace(tool="read_file", args_json='{"path": "nope.csv"}', ok=False, rehearsed=False,
                                        undo_of=None, undone_at=None))
    plan = F.plan(app_home.platform, got, str(app_home.folder))
    assert len(plan.steps) == 2


def test_a_time_that_was_undone_is_not_the_same(app_home):
    bank_files(app_home.folder)
    got = times(app_home.folder)
    got[0][1][1].undone_at = datetime.now()
    with pytest.raises(F.NotFixed, match="undone"):
        F.plan(app_home.platform, got, str(app_home.folder))


@pytest.mark.parametrize("tool", ["shell", "web_fetch", "read_document", "excel_edit", "send_email"])
def test_a_tool_that_may_ask_a_model_or_reach_out_is_never_a_fixed_step(app_home, tool):
    bank_files(app_home.folder)

    def other(folder, w):
        return [(tool, {"path": "a.txt"})] + job_calls(folder, w)

    with pytest.raises(F.NotFixed, match="not one Iron Jarvis can repeat"):
        _plan(app_home, other)


def test_a_file_a_model_may_be_asked_to_read_is_never_read_by_a_fixed_step(app_home):
    (app_home.folder / "scan.pdf").write_bytes(b"%PDF-1.4\n")

    def pdf(folder, w):
        return [("read_file", {"path": "scan.pdf"})]

    with pytest.raises(F.NotFixed, match="may need a model"):
        _plan(app_home, pdf)


# --------------------------------------------------------------------------- #
# what changed
# --------------------------------------------------------------------------- #
def test_two_things_that_changed_are_not_one_input(app_home):
    bank_files(app_home.folder)

    def two(folder, w):
        calls = job_calls(folder, w)
        calls[1][1]["target"] = f"summary-{w}.xlsx"
        return calls

    with pytest.raises(F.NotFixed, match="more than one thing changed"):
        _plan(app_home, two)


def test_what_changed_must_be_a_file(app_home):
    bank_files(app_home.folder)

    def words(folder, w):
        return job_calls(folder, w) + [("write_file", {"path": "note.txt", "content": f"week {w}"})]

    with pytest.raises(F.NotFixed, match="not a file"):
        _plan(app_home, words)


def test_a_file_that_was_not_the_newest_each_time_is_not_an_input(app_home):
    bank_files(app_home.folder)
    # Week 1's export was written AFTER week 2's request... but a newer one
    # (bank-week-0's twin) was there when week 1 was asked for.
    late = app_home.folder / "exports" / "other.csv"
    late.write_text("x\n", encoding="utf-8")
    at = (_monday(1) - timedelta(minutes=30)).timestamp()
    os.utime(late, (at, at))
    with pytest.raises(F.NotFixed, match="newest"):
        _plan(app_home)


def test_a_file_that_is_gone_cannot_show_it_was_the_newest(app_home):
    used = bank_files(app_home.folder)
    os.remove(used[1])
    with pytest.raises(F.NotFixed, match="newest"):
        _plan(app_home)


# --------------------------------------------------------------------------- #
# the leak rule
# --------------------------------------------------------------------------- #
def test_a_client_s_job_is_never_fixed_steps(app_home):
    bank_files(app_home.folder)
    with pytest.raises(F.NotFixed, match="client"):
        _plan(app_home, owners=("", "client:wc_hartleys", ""))


def _client(engine, name: str) -> None:
    from iron_jarvis.workmap.clients import names, store
    from iron_jarvis.workmap.models import CLIENT_CONFIRMED, WorkClient

    with session_scope(engine) as db:
        db.add(WorkClient(id="wc_" + name.lower(), name=name, signature=names.signature(name),
                          status=CLIENT_CONFIRMED))
        db.commit()
    store.bump_version()


@pytest.mark.parametrize("target", ["Marlow summary.xlsx", "marlow-totals.xlsx", "MARLOW.xlsx"])
def test_a_client_s_name_in_any_case_in_what_is_kept_refuses_the_steps(app_home, target):
    bank_files(app_home.folder)
    _client(app_home.engine, "Marlow")

    def named(folder, w):
        calls = job_calls(folder, w)
        calls[1][1]["target"] = target
        return calls

    with pytest.raises(F.NotFixed, match="client"):
        _plan(app_home, named)


@pytest.mark.parametrize("content", ["mail anna@example.org about it", "key sk-" + "a1" * 20])
def test_a_secret_or_a_contact_detail_in_what_is_kept_refuses_the_steps(app_home, content):
    bank_files(app_home.folder)

    def kept(folder, w):
        return job_calls(folder, w) + [("write_file", {"path": "note.txt", "content": content})]

    with pytest.raises(F.NotFixed, match="secret or a contact detail"):
        _plan(app_home, kept)


def test_a_path_outside_the_chats_folder_refuses_the_steps(app_home, tmp_path):
    bank_files(app_home.folder)

    def outside(folder, w):
        calls = job_calls(folder, w)
        calls[1][1]["target"] = str(tmp_path / "elsewhere.xlsx")
        return calls

    with pytest.raises(F.NotFixed, match="outside"):
        _plan(app_home, outside)


def test_a_step_that_asks_first_refuses_the_steps(app_home):
    bank_files(app_home.folder)
    app_home.platform.permissions._base["convert_document"] = "ask"  # the owner's "ask first"
    with pytest.raises(F.NotFixed, match="asks you"):
        _plan(app_home)


# --------------------------------------------------------------------------- #
# the input, filled by code
# --------------------------------------------------------------------------- #
def test_the_input_is_the_newest_file_of_its_kind(app_home):
    bank_files(app_home.folder)
    rule = {"file": {"newest": {"in": "exports", "kind": "csv"}}}
    assert F.resolve_inputs(rule, str(app_home.folder)) == {"file": "exports/bank-week-0.csv"}
    new = app_home.folder / "exports" / "bank-week-new.csv"
    new.write_text("category,amount\n", encoding="utf-8")
    (app_home.folder / "exports" / "later.txt").write_text("not a csv", encoding="utf-8")
    assert F.resolve_inputs(rule, str(app_home.folder)) == {"file": "exports/bank-week-new.csv"}


def test_no_file_to_use_is_said_in_words(app_home):
    rule = {"file": {"newest": {"in": "exports", "kind": "csv"}}}
    with pytest.raises(F.InputMissing, match=r"no \.csv file is in “exports”"):
        F.resolve_inputs(rule, str(app_home.folder))


@pytest.mark.parametrize("bad", [
    "exports", {"file": {"newest": {"in": "../up", "kind": "csv"}}},
    {"file": {"newest": {"in": "C:/Elsewhere" if os.name == "nt" else "/elsewhere", "kind": "csv"}}},
    {"file": {"newest": {"in": "x", "kind": "c.sv"}}}, {"file": {"oldest": {}}},
])
def test_an_inputs_rule_that_could_not_be_filled_is_refused(bad):
    assert F.check_inputs(bad)


def test_works_offline_only_for_a_folder_on_this_pc(app_home):
    assert F.folder_is_local(str(app_home.folder)) is True
    assert F.folder_is_local("\\\\fileserver\\clients") is False
    assert F.folder_is_local("") is False
